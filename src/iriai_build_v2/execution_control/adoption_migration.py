"""Helpers for one-off in-flight execution-control adoption.

This module is an operator aid, not a runtime fallback. It inspects an
in-flight feature at a declared safe boundary and returns the typed inputs that
would be written as an :class:`InFlightAdoptionRecord`. It never writes
artifacts, mutates DAGs, repairs checkpoints, or infers adoption.

Post-adoption repair helpers remain read-only too: they build append-only
artifact bodies for an operator to insert transactionally.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol

from .atomic_landing import InFlightAdoptionRecord, RollbackDisposition

__all__ = [
    "AdoptionMigrationArtifactReader",
    "AdoptionMigrationBlocker",
    "AdoptionPreflightSnapshot",
    "AdoptionMigrationPreflight",
    "PostAdoptionRepoIdentityRepairPlan",
    "PostAdoptionRepoIdentityBulkRepairPlan",
    "build_in_flight_adoption_preflight",
    "build_post_adoption_repo_identity_repair_plan",
    "build_post_adoption_repo_identity_bulk_repair_plan",
]


class AdoptionMigrationArtifactReader(Protocol):
    async def get(self, key: str, *, feature: Any) -> Any | None:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class AdoptionMigrationBlocker:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdoptionPreflightSnapshot:
    feature_id: str
    boundary_group_idx: int
    adoption_marker_key: str
    root_dag_artifact_id: int | None
    root_dag_sha256: str
    boundary_group_artifact_id: int | None
    boundary_group_sha256: str
    boundary_task_ids: tuple[str, ...]
    boundary_result_statuses: dict[str, str]
    completed_checkpoint_range: tuple[int, int]
    next_effective_group_idx: int
    active_regroup_artifact_ids: tuple[int, ...]
    active_regroup_metadata: dict[str, Any]
    pre_adoption_baseline: dict[str, Any]


@dataclass(frozen=True)
class AdoptionMigrationPreflight:
    snapshot: AdoptionPreflightSnapshot
    adoption_record_fields: dict[str, Any]
    blockers: tuple[AdoptionMigrationBlocker, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers


@dataclass
class PostAdoptionRepoIdentityRepairPlan:
    feature_id: str
    group_idx: int
    boundary_group_idx: int
    regroup_active_key: str
    regroup_canonical_key: str
    regroup_rollback_key: str
    registry_key: str
    old_root_dag_artifact_id: int | None
    old_root_dag_sha256: str
    old_regroup_artifact_id: int | None
    old_regroup_sha256: str
    old_rollback_artifact_id: int | None
    old_active_marker_artifact_id: int | None
    changed_task_ids: tuple[str, ...]
    resolved_repo_paths_by_task: dict[str, str]
    current_group_task_ids: tuple[str, ...]
    post_boundary_missing_repo_identity_before: tuple[dict[str, Any], ...]
    post_boundary_missing_repo_identity_after: tuple[dict[str, Any], ...]
    root_dag: dict[str, Any]
    regroup_projection: dict[str, Any]
    rollback_projection: dict[str, Any]
    active_marker: dict[str, Any]
    registry_evidence: dict[str, Any]
    blockers: tuple[AdoptionMigrationBlocker, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers

    def root_dag_value(self) -> str:
        return _stable_json(self.root_dag)

    def root_dag_sha256(self) -> str:
        return _sha256(self.root_dag_value())

    def regroup_value(self, *, new_root_dag_artifact_id: int, new_root_dag_sha256: str) -> str:
        body = json.loads(_stable_json(self.regroup_projection))
        body["base_dag_artifact_id"] = int(new_root_dag_artifact_id)
        body["base_dag_sha256"] = str(new_root_dag_sha256)
        return _stable_json(body)

    def rollback_value(
        self,
        *,
        new_root_dag_artifact_id: int,
        new_root_dag_sha256: str,
        created_at: str,
    ) -> str:
        body = json.loads(_stable_json(self.rollback_projection))
        body["base_dag_artifact_id"] = int(new_root_dag_artifact_id)
        body["base_dag_sha256"] = str(new_root_dag_sha256)
        body["created_at"] = created_at
        return _stable_json(body)

    def active_marker_value(
        self,
        *,
        new_root_dag_artifact_id: int,
        new_root_dag_sha256: str,
        new_regroup_artifact_id: int,
        new_regroup_sha256: str,
        new_rollback_artifact_id: int,
        created_at: str,
    ) -> str:
        body = json.loads(_stable_json(self.active_marker))
        body["base_dag_artifact_id"] = int(new_root_dag_artifact_id)
        body["base_dag_sha256"] = str(new_root_dag_sha256)
        body["canonical_artifact_id"] = int(new_regroup_artifact_id)
        body["canonical_artifact_key"] = self.regroup_canonical_key
        body["canonical_sha256"] = str(new_regroup_sha256)
        body["regroup_sha256"] = str(new_regroup_sha256)
        body["rollback_artifact_id"] = int(new_rollback_artifact_id)
        body["rollback_artifact_key"] = self.regroup_rollback_key
        body["created_at"] = created_at
        return _stable_json(body)

    def audit_value(
        self,
        *,
        new_root_dag_artifact_id: int,
        new_regroup_artifact_id: int,
        new_rollback_artifact_id: int,
        new_active_marker_artifact_id: int,
        created_at: str,
    ) -> str:
        return _stable_json({
            "artifact_schema": "execution-control-post-adoption-metadata-repair-v1",
            "feature_id": self.feature_id,
            "group_idx": self.group_idx,
            "boundary_group_idx": self.boundary_group_idx,
            "created_at": created_at,
            "repair_kind": "post_adoption_repo_identity",
            "changed_task_ids": list(self.changed_task_ids),
            "resolved_repo_paths_by_task": dict(sorted(self.resolved_repo_paths_by_task.items())),
            "current_group_task_ids": list(self.current_group_task_ids),
            "old_artifacts": {
                "dag": self.old_root_dag_artifact_id,
                "dag_sha256": self.old_root_dag_sha256,
                "regroup": self.old_regroup_artifact_id,
                "regroup_sha256": self.old_regroup_sha256,
                "rollback": self.old_rollback_artifact_id,
                "active_marker": self.old_active_marker_artifact_id,
            },
            "new_artifacts": {
                "dag": int(new_root_dag_artifact_id),
                "regroup": int(new_regroup_artifact_id),
                "rollback": int(new_rollback_artifact_id),
                "active_marker": int(new_active_marker_artifact_id),
            },
            "keys": {
                "active_marker": self.regroup_active_key,
                "canonical_regroup": self.regroup_canonical_key,
                "rollback": self.regroup_rollback_key,
                "registry": self.registry_key,
            },
            "registry_evidence": self.registry_evidence,
            "post_boundary_missing_repo_identity_before": list(
                self.post_boundary_missing_repo_identity_before
            ),
            "post_boundary_missing_repo_identity_after": list(
                self.post_boundary_missing_repo_identity_after
            ),
            "policy": {
                "append_only": True,
                "updated_rows_in_place": False,
                "runtime_registry_inference_restored": False,
                "product_repos_mutated": False,
            },
        })


@dataclass
class PostAdoptionRepoIdentityBulkRepairPlan:
    feature_id: str
    boundary_group_idx: int
    first_group_idx: int
    last_group_idx: int
    regroup_active_key: str
    regroup_canonical_key: str
    regroup_rollback_key: str
    seed_registry_key: str
    review_key: str
    old_root_dag_artifact_id: int | None
    old_root_dag_sha256: str
    old_regroup_artifact_id: int | None
    old_regroup_sha256: str
    old_rollback_artifact_id: int | None
    old_active_marker_artifact_id: int | None
    changed_task_ids: tuple[str, ...]
    deterministic_task_ids: tuple[str, ...]
    reviewed_task_ids: tuple[str, ...]
    split_original_task_ids: tuple[str, ...]
    created_split_task_ids: tuple[str, ...]
    moved_task_ids: tuple[str, ...]
    normalized_legacy_files_task_ids: tuple[str, ...]
    regroup_validation: dict[str, Any]
    resolved_repo_paths_by_task: dict[str, str]
    affected_group_indices: tuple[int, ...]
    group_registry_projections: dict[int, dict[str, Any]]
    post_boundary_missing_repo_identity_before: tuple[dict[str, Any], ...]
    post_boundary_missing_repo_identity_after: tuple[dict[str, Any], ...]
    review_artifact: dict[str, Any]
    root_dag: dict[str, Any]
    regroup_projection: dict[str, Any]
    rollback_projection: dict[str, Any]
    active_marker: dict[str, Any]
    registry_evidence: dict[str, Any]
    blockers: tuple[AdoptionMigrationBlocker, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers

    def root_dag_value(self) -> str:
        return _stable_json(self.root_dag)

    def root_dag_sha256(self) -> str:
        return _sha256(self.root_dag_value())

    def regroup_value(self, *, new_root_dag_artifact_id: int, new_root_dag_sha256: str) -> str:
        body = json.loads(_stable_json(self.regroup_projection))
        body["base_dag_artifact_id"] = int(new_root_dag_artifact_id)
        body["base_dag_sha256"] = str(new_root_dag_sha256)
        return _stable_json(body)

    def rollback_value(
        self,
        *,
        new_root_dag_artifact_id: int,
        new_root_dag_sha256: str,
        created_at: str,
    ) -> str:
        body = json.loads(_stable_json(self.rollback_projection))
        body["base_dag_artifact_id"] = int(new_root_dag_artifact_id)
        body["base_dag_sha256"] = str(new_root_dag_sha256)
        body["created_at"] = created_at
        return _stable_json(body)

    def active_marker_value(
        self,
        *,
        new_root_dag_artifact_id: int,
        new_root_dag_sha256: str,
        new_regroup_artifact_id: int,
        new_regroup_sha256: str,
        new_rollback_artifact_id: int,
        created_at: str,
    ) -> str:
        body = json.loads(_stable_json(self.active_marker))
        body["base_dag_artifact_id"] = int(new_root_dag_artifact_id)
        body["base_dag_sha256"] = str(new_root_dag_sha256)
        body["canonical_artifact_id"] = int(new_regroup_artifact_id)
        body["canonical_artifact_key"] = self.regroup_canonical_key
        body["canonical_sha256"] = str(new_regroup_sha256)
        body["regroup_sha256"] = str(new_regroup_sha256)
        body["rollback_artifact_id"] = int(new_rollback_artifact_id)
        body["rollback_artifact_key"] = self.regroup_rollback_key
        body["created_at"] = created_at
        return _stable_json(body)

    def review_value(self, *, created_at: str) -> str:
        body = json.loads(_stable_json(self.review_artifact))
        body["created_at"] = created_at
        return _stable_json(body)

    def registry_value(self, group_idx: int) -> str:
        return _stable_json(self.group_registry_projections[int(group_idx)])

    def audit_value(
        self,
        *,
        new_root_dag_artifact_id: int,
        new_regroup_artifact_id: int,
        new_rollback_artifact_id: int,
        new_active_marker_artifact_id: int,
        review_artifact_id: int,
        registry_artifact_ids_by_group: Mapping[int, int],
        created_at: str,
    ) -> str:
        return _stable_json({
            "artifact_schema": "execution-control-post-adoption-metadata-repair-v1",
            "feature_id": self.feature_id,
            "group_range": [self.first_group_idx, self.last_group_idx],
            "boundary_group_idx": self.boundary_group_idx,
            "created_at": created_at,
            "repair_kind": "post_adoption_repo_identity_bulk",
            "changed_task_ids": list(self.changed_task_ids),
            "deterministic_task_ids": list(self.deterministic_task_ids),
            "reviewed_task_ids": list(self.reviewed_task_ids),
            "split_original_task_ids": list(self.split_original_task_ids),
            "created_split_task_ids": list(self.created_split_task_ids),
            "moved_task_ids": list(self.moved_task_ids),
            "normalized_legacy_files_task_ids": list(self.normalized_legacy_files_task_ids),
            "regroup_validation": self.regroup_validation,
            "resolved_repo_paths_by_task": dict(sorted(self.resolved_repo_paths_by_task.items())),
            "affected_group_indices": list(self.affected_group_indices),
            "old_artifacts": {
                "dag": self.old_root_dag_artifact_id,
                "dag_sha256": self.old_root_dag_sha256,
                "regroup": self.old_regroup_artifact_id,
                "regroup_sha256": self.old_regroup_sha256,
                "rollback": self.old_rollback_artifact_id,
                "active_marker": self.old_active_marker_artifact_id,
            },
            "new_artifacts": {
                "dag": int(new_root_dag_artifact_id),
                "regroup": int(new_regroup_artifact_id),
                "rollback": int(new_rollback_artifact_id),
                "active_marker": int(new_active_marker_artifact_id),
                "review": int(review_artifact_id),
                "registries_by_group": {
                    str(group_idx): int(artifact_id)
                    for group_idx, artifact_id in sorted(registry_artifact_ids_by_group.items())
                },
            },
            "keys": {
                "active_marker": self.regroup_active_key,
                "canonical_regroup": self.regroup_canonical_key,
                "rollback": self.regroup_rollback_key,
                "seed_registry": self.seed_registry_key,
                "review": self.review_key,
            },
            "registry_evidence": self.registry_evidence,
            "post_boundary_missing_repo_identity_before": list(
                self.post_boundary_missing_repo_identity_before
            ),
            "post_boundary_missing_repo_identity_after": list(
                self.post_boundary_missing_repo_identity_after
            ),
            "policy": {
                "append_only": True,
                "updated_rows_in_place": False,
                "runtime_registry_inference_restored": False,
                "product_repos_mutated": False,
                "cross_repo_tasks_split": True,
            },
        })


async def build_in_flight_adoption_preflight(
    *,
    feature: Any,
    artifact_store: AdoptionMigrationArtifactReader,
    boundary_group_idx: int,
    candidate_commit: str | None = None,
    deploy_artifact_id: str | None = None,
    projection_digest: str | None = None,
    active_regroup_artifact_ids: list[int] | tuple[int, ...] | None = None,
    active_regroup_metadata: Mapping[str, Any] | None = None,
    workspace_snapshot_ids: list[int] | tuple[int, ...] | None = None,
    rollback_disposition: RollbackDisposition = "legacy_resume_before_next_group",
    feature_state_at_adoption: str = "",
    adopted_by: str = "",
    landing_gate_result_id: str = "",
    notes: str = "",
    now: datetime | None = None,
) -> AdoptionMigrationPreflight:
    """Inspect a feature and return adoption-marker inputs or typed blockers.

    Required runtime facts are read from the artifact store. Required operator
    facts such as ``candidate_commit`` and ``deploy_artifact_id`` must be passed
    explicitly; this helper reports blockers instead of inventing them.
    """

    feature_id = _feature_id(feature)
    blockers: list[AdoptionMigrationBlocker] = []
    if not feature_id:
        blockers.append(_blocker("missing_feature_id", "feature.id is required"))
    if boundary_group_idx < 0:
        blockers.append(_blocker(
            "invalid_boundary_group",
            "boundary_group_idx must be non-negative",
            boundary_group_idx=boundary_group_idx,
        ))

    root_record = await _artifact_record(artifact_store, "dag", feature=feature)
    if root_record is None:
        blockers.append(_blocker("missing_root_dag", "root DAG artifact 'dag' is missing"))
        root_id = None
        root_sha = ""
    else:
        root_id = _int_or_none(root_record.get("id"))
        root_value = str(root_record.get("value") or "")
        root_sha = str(root_record.get("sha256") or _sha256(root_value))
        if root_id is None or not root_sha:
            blockers.append(_blocker(
                "missing_root_dag_identity",
                "root DAG artifact must expose both id and sha256",
                root_dag_artifact_id=root_record.get("id"),
                root_dag_sha256=root_sha,
            ))

    boundary_key = f"dag-group:{boundary_group_idx}"
    boundary_record = await _artifact_record(
        artifact_store,
        boundary_key,
        feature=feature,
    )
    boundary_payload: dict[str, Any] = {}
    boundary_group_artifact_id: int | None = None
    boundary_group_sha256 = ""
    boundary_task_ids: tuple[str, ...] = ()
    boundary_result_statuses: dict[str, str] = {}
    if boundary_record is None:
        blockers.append(_blocker(
            "missing_boundary_group",
            f"boundary checkpoint {boundary_key!r} is missing",
            boundary_group_idx=boundary_group_idx,
        ))
    else:
        boundary_group_artifact_id = _int_or_none(boundary_record.get("id"))
        boundary_value = str(boundary_record.get("value") or "")
        boundary_group_sha256 = str(
            boundary_record.get("sha256") or _sha256(boundary_value)
        )
        boundary_payload = _json_object(boundary_value)
        if not boundary_payload:
            blockers.append(_blocker(
                "invalid_boundary_group_body",
                f"boundary checkpoint {boundary_key!r} is not a JSON object",
            ))
        else:
            boundary_task_ids = tuple(str(item) for item in boundary_payload.get("task_ids") or [])
            boundary_result_statuses = _result_statuses(boundary_payload)
            _validate_boundary_checkpoint(
                blockers,
                boundary_payload,
                boundary_group_idx=boundary_group_idx,
                boundary_task_ids=boundary_task_ids,
                boundary_result_statuses=boundary_result_statuses,
            )

    missing_range_groups = await _missing_checkpoint_groups(
        artifact_store,
        feature=feature,
        boundary_group_idx=boundary_group_idx,
    )
    if missing_range_groups:
        blockers.append(_blocker(
            "missing_completed_checkpoint_in_range",
            "completed_checkpoint_range is not contiguous through the boundary",
            missing_groups=missing_range_groups[:20],
            missing_group_count=len(missing_range_groups),
        ))

    regroup_ids, regroup_metadata, regroup_blockers = await _resolve_regroup_snapshot(
        artifact_store,
        feature=feature,
        supplied_ids=active_regroup_artifact_ids,
        supplied_metadata=active_regroup_metadata,
    )
    blockers.extend(regroup_blockers)

    if not str(candidate_commit or "").strip():
        blockers.append(_blocker(
            "missing_candidate_commit",
            "candidate_commit must be supplied by the operator",
        ))
    if not str(deploy_artifact_id or "").strip():
        blockers.append(_blocker(
            "missing_deploy_artifact_id",
            "deploy_artifact_id must be supplied by the operator",
        ))

    pre_adoption_baseline = {
        "sealed_completed_checkpoint_range": [0, boundary_group_idx],
        "root_dag_artifact_id": root_id,
        "root_dag_sha256": root_sha,
        "boundary_group_idx": boundary_group_idx,
        "boundary_group_artifact_id": boundary_group_artifact_id,
        "boundary_group_sha256": boundary_group_sha256,
        "boundary_verdict": boundary_payload.get("verdict", ""),
        "boundary_commit_hash": boundary_payload.get("commit_hash", ""),
        "boundary_task_ids": list(boundary_task_ids),
        "boundary_result_statuses": dict(sorted(boundary_result_statuses.items())),
        "active_regroup": regroup_metadata,
        "pre_adoption_debt": {
            "sealed_without_recompile": True,
            "legacy_groups_require_marker_to_skip": True,
            "manual_dag_repair_allowed": False,
        },
    }
    resolved_projection_digest = str(projection_digest or "").strip()
    if not resolved_projection_digest:
        resolved_projection_digest = _sha256(
            json.dumps(pre_adoption_baseline, sort_keys=True, separators=(",", ":"))
        )

    snapshot = AdoptionPreflightSnapshot(
        feature_id=feature_id,
        boundary_group_idx=boundary_group_idx,
        adoption_marker_key=f"execution-control-adoption:{feature_id}",
        root_dag_artifact_id=root_id,
        root_dag_sha256=root_sha,
        boundary_group_artifact_id=boundary_group_artifact_id,
        boundary_group_sha256=boundary_group_sha256,
        boundary_task_ids=boundary_task_ids,
        boundary_result_statuses=boundary_result_statuses,
        completed_checkpoint_range=(0, boundary_group_idx),
        next_effective_group_idx=boundary_group_idx + 1,
        active_regroup_artifact_ids=tuple(regroup_ids),
        active_regroup_metadata=regroup_metadata,
        pre_adoption_baseline=pre_adoption_baseline,
    )

    record_fields: dict[str, Any] = {}
    if not blockers:
        record_fields = {
            "status": "adopted",
            "feature_id": feature_id,
            "candidate_commit": str(candidate_commit).strip(),
            "deploy_artifact_id": str(deploy_artifact_id).strip(),
            "legacy_root_dag_artifact_id": root_id,
            "legacy_root_dag_sha256": root_sha,
            "completed_checkpoint_range": (0, boundary_group_idx),
            "next_effective_group_idx": boundary_group_idx + 1,
            "active_regroup_artifact_ids": list(regroup_ids),
            "workspace_snapshot_ids": list(workspace_snapshot_ids or []),
            "projection_digest": resolved_projection_digest,
            "adoption_marker_artifact_id": None,
            "adopted_at": now or datetime.now(timezone.utc),
            "rollback_disposition": rollback_disposition,
            "blockers": [],
            "feature_state_at_adoption": feature_state_at_adoption,
            "adopted_by": adopted_by,
            "landing_gate_result_id": landing_gate_result_id,
            "pre_adoption_baseline": pre_adoption_baseline,
            "notes": notes,
        }
        InFlightAdoptionRecord(**record_fields)

    return AdoptionMigrationPreflight(
        snapshot=snapshot,
        adoption_record_fields=record_fields,
        blockers=tuple(blockers),
    )


async def build_post_adoption_repo_identity_repair_plan(
    *,
    feature: Any,
    artifact_store: AdoptionMigrationArtifactReader,
    group_idx: int,
    boundary_group_idx: int,
    regroup_active_key: str | None = None,
    registry_key: str | None = None,
) -> PostAdoptionRepoIdentityRepairPlan:
    """Build append-only artifact bodies for strict post-adoption repo repair.

    This function performs no writes. It resolves missing repo identity only
    when the current dispatch group's workspace-authority registry has exactly
    one repo claiming the task.
    """

    feature_id = _feature_id(feature)
    blockers: list[AdoptionMigrationBlocker] = []
    if not feature_id:
        blockers.append(_blocker("missing_feature_id", "feature.id is required"))
    if group_idx < 0:
        blockers.append(_blocker(
            "invalid_group_idx",
            "group_idx must be non-negative",
            group_idx=group_idx,
        ))
    if boundary_group_idx < 0:
        blockers.append(_blocker(
            "invalid_boundary_group",
            "boundary_group_idx must be non-negative",
            boundary_group_idx=boundary_group_idx,
        ))

    adoption_key = f"execution-control-adoption:{feature_id}"
    adoption_record = await _artifact_record(artifact_store, adoption_key, feature=feature)
    adoption_payload = _json_object(adoption_record.get("value") if adoption_record else "")
    _validate_post_adoption_marker(
        blockers,
        adoption_payload,
        feature_id=feature_id,
        boundary_group_idx=boundary_group_idx,
        group_idx=group_idx,
    )

    active_key = regroup_active_key or await _single_active_regroup_key(
        artifact_store,
        feature=feature,
        blockers=blockers,
    )
    active_record = (
        await _artifact_record(artifact_store, active_key, feature=feature)
        if active_key else None
    )
    active_marker = _json_object(active_record.get("value") if active_record else "")
    if not active_marker:
        blockers.append(_blocker(
            "missing_active_regroup_marker",
            "active regroup marker is required for post-adoption repair",
            key=active_key,
        ))
    elif str(active_marker.get("status") or "").lower() != "active":
        blockers.append(_blocker(
            "inactive_active_regroup_marker",
            "active regroup marker status must be active",
            status=active_marker.get("status"),
        ))

    canonical_key = str(active_marker.get("canonical_artifact_key") or "").strip()
    rollback_key = str(active_marker.get("rollback_artifact_key") or "").strip()
    if not canonical_key:
        blockers.append(_blocker(
            "missing_canonical_regroup_key",
            "active marker is missing canonical_artifact_key",
        ))
    if not rollback_key:
        blockers.append(_blocker(
            "missing_rollback_key",
            "active marker is missing rollback_artifact_key",
        ))

    root_record = await _artifact_record(artifact_store, "dag", feature=feature)
    root_payload = _json_object(root_record.get("value") if root_record else "")
    if not root_payload:
        blockers.append(_blocker(
            "missing_root_dag",
            "root DAG artifact 'dag' is missing or invalid",
        ))

    regroup_record = (
        await _artifact_record(artifact_store, canonical_key, feature=feature)
        if canonical_key else None
    )
    regroup_payload = _json_object(regroup_record.get("value") if regroup_record else "")
    if not regroup_payload:
        blockers.append(_blocker(
            "missing_canonical_regroup",
            "canonical regroup artifact is missing or invalid",
            key=canonical_key,
        ))

    rollback_record = (
        await _artifact_record(artifact_store, rollback_key, feature=feature)
        if rollback_key else None
    )
    rollback_payload = _json_object(rollback_record.get("value") if rollback_record else "")
    if not rollback_payload:
        blockers.append(_blocker(
            "missing_rollback_projection",
            "regroup rollback projection is missing or invalid",
            key=rollback_key,
        ))

    registry_artifact_key = registry_key or f"workspace-authority-registry:g{group_idx}"
    registry_record = await _artifact_record(
        artifact_store,
        registry_artifact_key,
        feature=feature,
    )
    registry_payload = _json_object(registry_record.get("value") if registry_record else "")
    registry = _registry_payload(registry_payload)
    registry_repos = list(registry.get("repos") or []) if isinstance(registry, Mapping) else []
    if not registry_repos:
        blockers.append(_blocker(
            "missing_workspace_authority_registry",
            "workspace-authority registry with repos is required",
            key=registry_artifact_key,
        ))

    if blockers:
        return _blocked_post_adoption_plan(
            feature_id=feature_id,
            group_idx=group_idx,
            boundary_group_idx=boundary_group_idx,
            regroup_active_key=active_key,
            regroup_canonical_key=canonical_key,
            regroup_rollback_key=rollback_key,
            registry_key=registry_artifact_key,
            blockers=blockers,
        )

    root_tasks = _tasks_by_id(root_payload)
    regroup_dag = _json_object(regroup_payload.get("dag"))
    regroup_tasks = _tasks_by_id(regroup_dag)
    group_offset = _int_or_none(
        active_marker.get("group_idx_offset")
        or regroup_payload.get("group_idx_offset")
    )
    if group_offset is None:
        blockers.append(_blocker("missing_regroup_offset", "active regroup offset is required"))
        group_offset = 0
    local_group_idx = group_idx - group_offset
    regroup_order = list(regroup_dag.get("execution_order") or [])
    if local_group_idx < 0 or local_group_idx >= len(regroup_order):
        blockers.append(_blocker(
            "group_not_in_regroup_projection",
            "group_idx is outside active regroup projection",
            group_idx=group_idx,
            group_idx_offset=group_offset,
            derived_group_count=len(regroup_order),
        ))
        current_group_task_ids: tuple[str, ...] = ()
    else:
        current_group_task_ids = tuple(str(item) for item in regroup_order[local_group_idx])

    changed_task_ids: list[str] = []
    resolved_repo_paths_by_task: dict[str, str] = {}
    updated_root = json.loads(_stable_json(root_payload))
    updated_regroup = json.loads(_stable_json(regroup_payload))
    updated_root_tasks = _tasks_by_id(updated_root)
    updated_regroup_tasks = _tasks_by_id(_json_object(updated_regroup.get("dag")))

    for task_id in current_group_task_ids:
        task = updated_root_tasks.get(task_id) or root_tasks.get(task_id)
        if not task:
            blockers.append(_blocker(
                "missing_current_group_task",
                "current group task is missing from root DAG tasks",
                task_id=task_id,
            ))
            continue
        if _task_has_repo_identity(task):
            continue
        repo_candidates = _registry_repos_claiming_task(registry_repos, task_id)
        if len(repo_candidates) != 1:
            blockers.append(_blocker(
                "ambiguous_repo_identity" if len(repo_candidates) > 1 else "unmapped_repo_identity",
                "missing task repo identity must resolve to exactly one registry repo",
                task_id=task_id,
                candidate_repo_ids=[
                    str(repo.get("repo_id") or "") for repo in repo_candidates
                ],
            ))
            continue
        repo_path = _repo_path_from_registry_repo(repo_candidates[0])
        if not repo_path:
            blockers.append(_blocker(
                "missing_registry_repo_path",
                "registry repo claim does not expose a stable repo path",
                task_id=task_id,
                repo_id=repo_candidates[0].get("repo_id"),
            ))
            continue
        _set_task_repo_path(updated_root_tasks, task_id, repo_path)
        _set_task_repo_path(updated_regroup_tasks, task_id, repo_path)
        changed_task_ids.append(task_id)
        resolved_repo_paths_by_task[task_id] = repo_path

    if not changed_task_ids and not blockers:
        blockers.append(_blocker(
            "no_repo_identity_repairs_needed",
            "current group has no missing repo identity to repair",
            group_idx=group_idx,
        ))

    before_scan = _scan_post_boundary_missing_repo_identity(
        root_payload,
        regroup_payload,
        group_idx_offset=group_offset,
        first_group_idx=boundary_group_idx + 1,
    )
    after_scan = _scan_post_boundary_missing_repo_identity(
        updated_root,
        updated_regroup,
        group_idx_offset=group_offset,
        first_group_idx=boundary_group_idx + 1,
    )

    if blockers:
        return _blocked_post_adoption_plan(
            feature_id=feature_id,
            group_idx=group_idx,
            boundary_group_idx=boundary_group_idx,
            regroup_active_key=active_key,
            regroup_canonical_key=canonical_key,
            regroup_rollback_key=rollback_key,
            registry_key=registry_artifact_key,
            blockers=blockers,
        )

    registry_evidence = {
        "artifact_id": _int_or_none(registry_record.get("id")),
        "artifact_key": registry_artifact_key,
        "registry_digest": registry.get("registry_digest", ""),
        "repo_count": len(registry_repos),
        "resolved_repo_ids_by_task": {
            task_id: str(
                _registry_repos_claiming_task(registry_repos, task_id)[0].get("repo_id")
                or ""
            )
            for task_id in changed_task_ids
        },
    }

    return PostAdoptionRepoIdentityRepairPlan(
        feature_id=feature_id,
        group_idx=group_idx,
        boundary_group_idx=boundary_group_idx,
        regroup_active_key=active_key,
        regroup_canonical_key=canonical_key,
        regroup_rollback_key=rollback_key,
        registry_key=registry_artifact_key,
        old_root_dag_artifact_id=_int_or_none(root_record.get("id")),
        old_root_dag_sha256=str(
            root_record.get("sha256") or _sha256(str(root_record.get("value") or ""))
        ),
        old_regroup_artifact_id=_int_or_none(regroup_record.get("id")),
        old_regroup_sha256=str(
            regroup_record.get("sha256") or _sha256(str(regroup_record.get("value") or ""))
        ),
        old_rollback_artifact_id=_int_or_none(rollback_record.get("id")),
        old_active_marker_artifact_id=_int_or_none(active_record.get("id")),
        changed_task_ids=tuple(changed_task_ids),
        resolved_repo_paths_by_task=dict(resolved_repo_paths_by_task),
        current_group_task_ids=current_group_task_ids,
        post_boundary_missing_repo_identity_before=tuple(before_scan),
        post_boundary_missing_repo_identity_after=tuple(after_scan),
        root_dag=updated_root,
        regroup_projection=updated_regroup,
        rollback_projection=json.loads(_stable_json(rollback_payload)),
        active_marker=json.loads(_stable_json(active_marker)),
        registry_evidence=registry_evidence,
        blockers=(),
    )


async def build_post_adoption_repo_identity_bulk_repair_plan(
    *,
    feature: Any,
    artifact_store: AdoptionMigrationArtifactReader,
    boundary_group_idx: int,
    first_group_idx: int | None = None,
    last_group_idx: int | None = None,
    regroup_active_key: str | None = None,
    seed_registry_key: str | None = None,
    review_key: str | None = None,
    reviewed_records: Mapping[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    registry_builder: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    feature_root: str = "",
    workspace_root: str = "",
    normalize_legacy_files: bool = True,
) -> PostAdoptionRepoIdentityBulkRepairPlan:
    """Build append-only artifact bodies for all remaining repo-identity debt.

    This helper is still read-only. It rejects ambiguous or cross-repo tasks
    unless reviewed evidence supplies either a repo override or split task
    definitions.
    """

    feature_id = _feature_id(feature)
    blockers: list[AdoptionMigrationBlocker] = []
    if not feature_id:
        blockers.append(_blocker("missing_feature_id", "feature.id is required"))
    if boundary_group_idx < 0:
        blockers.append(_blocker(
            "invalid_boundary_group",
            "boundary_group_idx must be non-negative",
            boundary_group_idx=boundary_group_idx,
        ))

    resolved_first_group_idx = (
        int(first_group_idx) if first_group_idx is not None else boundary_group_idx + 1
    )
    _validate_post_adoption_marker(
        blockers,
        _json_object((await _artifact_record(
            artifact_store,
            f"execution-control-adoption:{feature_id}",
            feature=feature,
        ) or {}).get("value", "")),
        feature_id=feature_id,
        boundary_group_idx=boundary_group_idx,
        group_idx=resolved_first_group_idx,
    )

    active_key = regroup_active_key or await _single_active_regroup_key(
        artifact_store,
        feature=feature,
        blockers=blockers,
    )
    active_record = (
        await _artifact_record(artifact_store, active_key, feature=feature)
        if active_key else None
    )
    active_marker = _json_object(active_record.get("value") if active_record else "")
    if not active_marker:
        blockers.append(_blocker(
            "missing_active_regroup_marker",
            "active regroup marker is required for bulk repair",
            key=active_key,
        ))
    elif str(active_marker.get("status") or "").lower() != "active":
        blockers.append(_blocker(
            "inactive_active_regroup_marker",
            "active regroup marker status must be active",
            status=active_marker.get("status"),
        ))

    canonical_key = str(active_marker.get("canonical_artifact_key") or "").strip()
    rollback_key = str(active_marker.get("rollback_artifact_key") or "").strip()
    if not canonical_key:
        blockers.append(_blocker(
            "missing_canonical_regroup_key",
            "active marker is missing canonical_artifact_key",
        ))
    if not rollback_key:
        blockers.append(_blocker(
            "missing_rollback_key",
            "active marker is missing rollback_artifact_key",
        ))

    root_record = await _artifact_record(artifact_store, "dag", feature=feature)
    root_payload = _json_object(root_record.get("value") if root_record else "")
    if not root_payload:
        blockers.append(_blocker("missing_root_dag", "root DAG artifact is missing or invalid"))

    regroup_record = (
        await _artifact_record(artifact_store, canonical_key, feature=feature)
        if canonical_key else None
    )
    regroup_payload = _json_object(regroup_record.get("value") if regroup_record else "")
    regroup_dag = _json_object(regroup_payload.get("dag"))
    if not regroup_payload or not regroup_dag:
        blockers.append(_blocker(
            "missing_canonical_regroup",
            "canonical regroup artifact is missing or invalid",
            key=canonical_key,
        ))

    rollback_record = (
        await _artifact_record(artifact_store, rollback_key, feature=feature)
        if rollback_key else None
    )
    rollback_payload = _json_object(rollback_record.get("value") if rollback_record else "")
    if not rollback_payload:
        blockers.append(_blocker(
            "missing_rollback_projection",
            "regroup rollback projection is missing or invalid",
            key=rollback_key,
        ))

    group_offset = _int_or_none(
        active_marker.get("group_idx_offset")
        or regroup_payload.get("group_idx_offset")
    )
    if group_offset is None:
        blockers.append(_blocker("missing_regroup_offset", "active regroup offset is required"))
        group_offset = 0
    regroup_order = list(regroup_dag.get("execution_order") or [])
    derived_last_group_idx = group_offset + len(regroup_order) - 1
    resolved_last_group_idx = (
        int(last_group_idx) if last_group_idx is not None else derived_last_group_idx
    )
    if resolved_last_group_idx < resolved_first_group_idx:
        blockers.append(_blocker(
            "invalid_group_range",
            "last_group_idx must be at or after first_group_idx",
            first_group_idx=resolved_first_group_idx,
            last_group_idx=resolved_last_group_idx,
        ))

    registry_artifact_key = seed_registry_key or (
        f"workspace-authority-registry:g{resolved_first_group_idx}"
    )
    seed_record = await _artifact_record(artifact_store, registry_artifact_key, feature=feature)
    seed_payload = _json_object(seed_record.get("value") if seed_record else "")
    seed_registry = _registry_payload(seed_payload)
    seed_repos = list(seed_registry.get("repos") or []) if seed_registry else []
    if not seed_repos:
        blockers.append(_blocker(
            "missing_seed_workspace_authority_registry",
            "bulk repair requires a seed workspace-authority registry",
            key=registry_artifact_key,
        ))
    resolved_feature_root = feature_root or str(seed_registry.get("feature_root") or "")
    resolved_workspace_root = workspace_root or _workspace_root_from_feature_root(
        resolved_feature_root
    )

    review_artifact_key = review_key or (
        "execution-control-post-adoption-repo-identity-review:"
        f"{feature_id}:g{resolved_first_group_idx}-g{resolved_last_group_idx}"
    )
    review_records, review_blockers = _normalize_review_records(reviewed_records)
    blockers.extend(review_blockers)

    if blockers:
        return _blocked_bulk_post_adoption_plan(
            feature_id=feature_id,
            boundary_group_idx=boundary_group_idx,
            first_group_idx=resolved_first_group_idx,
            last_group_idx=resolved_last_group_idx,
            regroup_active_key=active_key,
            regroup_canonical_key=canonical_key,
            regroup_rollback_key=rollback_key,
            seed_registry_key=registry_artifact_key,
            review_key=review_artifact_key,
            blockers=blockers,
        )

    before_scan = [
        item for item in _scan_post_boundary_missing_repo_identity(
            root_payload,
            regroup_payload,
            group_idx_offset=group_offset,
            first_group_idx=resolved_first_group_idx,
        )
        if int(item.get("group_idx") or -1) <= resolved_last_group_idx
    ]
    missing_by_group: dict[int, list[dict[str, Any]]] = {}
    for item in before_scan:
        missing_by_group.setdefault(int(item["group_idx"]), []).append(item)

    root_tasks = _tasks_by_id(root_payload)
    updated_root = json.loads(_stable_json(root_payload))
    updated_regroup = json.loads(_stable_json(regroup_payload))
    updated_regroup_dag = (
        updated_regroup.get("dag") if isinstance(updated_regroup.get("dag"), dict) else {}
    )
    updated_root_tasks = _tasks_by_id(updated_root)
    updated_regroup_tasks = _tasks_by_id(updated_regroup_dag)

    registry_by_group: dict[int, dict[str, Any]] = {}
    for group_idx in sorted(missing_by_group):
        tasks = _tasks_for_group(root_tasks, regroup_order, group_idx, group_offset)
        registry_by_group[group_idx] = await _build_bulk_group_registry(
            registry_builder,
            feature_id=feature_id,
            group_idx=group_idx,
            tasks=tasks,
            seed_registry=seed_registry,
            feature_root=resolved_feature_root,
            workspace_root=resolved_workspace_root,
        )

    changed_task_ids: list[str] = []
    deterministic_task_ids: list[str] = []
    reviewed_task_ids: list[str] = []
    split_original_task_ids: list[str] = []
    created_split_task_ids: list[str] = []
    moved_task_ids: list[str] = []
    resolved_repo_paths_by_task: dict[str, str] = {}
    affected_groups: set[int] = set()
    required_review_task_ids: list[str] = []
    review_decisions: list[dict[str, Any]] = []

    for group_idx in sorted(missing_by_group):
        for missing in missing_by_group[group_idx]:
            task_id = str(missing.get("task_id") or "")
            task = updated_root_tasks.get(task_id) or root_tasks.get(task_id)
            if not task:
                blockers.append(_blocker(
                    "missing_current_group_task",
                    "missing-identity task is absent from the root DAG",
                    task_id=task_id,
                    group_idx=group_idx,
                ))
                continue
            review = review_records.get(task_id)
            prefix_candidates = _path_matching_registry_repos(
                _task_path_examples(task),
                seed_repos,
            )
            prefix_repo_paths = sorted({
                _repo_path_from_registry_repo(repo)
                for repo in prefix_candidates
                if _repo_path_from_registry_repo(repo)
            })
            if len(prefix_repo_paths) > 1:
                if not review or not review.get("split_tasks"):
                    required_review_task_ids.append(task_id)
                    blockers.append(_blocker(
                        "cross_repo_split_required",
                        "task file_scope spans multiple repos and must be split",
                        task_id=task_id,
                        group_idx=group_idx,
                        repo_paths=prefix_repo_paths,
                    ))
                    continue
                split_result = _apply_reviewed_task_split(
                    updated_root,
                    updated_regroup_dag,
                    updated_root_tasks,
                    updated_regroup_tasks,
                    task,
                    review,
                    group_idx=group_idx,
                    known_repo_paths=_known_repo_paths(seed_repos),
                )
                if split_result["blockers"]:
                    blockers.extend(split_result["blockers"])
                    continue
                split_original_task_ids.append(task_id)
                created_split_task_ids.extend(split_result["created_task_ids"])
                changed_task_ids.extend(split_result["created_task_ids"])
                reviewed_task_ids.append(task_id)
                resolved_repo_paths_by_task.update(split_result["repo_paths_by_task"])
                review_decisions.append(_review_decision(task_id, group_idx, review))
                affected_groups.add(group_idx)
                continue

            registry = registry_by_group.get(group_idx) or {}
            claims = _registry_repos_claiming_task(list(registry.get("repos") or []), task_id)
            repo_path = ""
            source = ""
            if review and review.get("repo_path") and bool(review.get("override")):
                repo_path = _normalize_repo_path(review.get("repo_path"))
                source = "review"
            elif len(claims) == 1:
                repo_path = _repo_path_from_registry_repo(claims[0])
                source = "workspace_authority"
            elif review and review.get("repo_path"):
                repo_path = _normalize_repo_path(review.get("repo_path"))
                source = "review"
            else:
                required_review_task_ids.append(task_id)
                blockers.append(_blocker(
                    "repo_identity_review_required"
                    if not claims else "ambiguous_repo_identity",
                    "task requires reviewed repo identity evidence",
                    task_id=task_id,
                    group_idx=group_idx,
                    candidate_repo_ids=[
                        str(repo.get("repo_id") or "") for repo in claims
                    ],
                ))
                continue

            if not _review_or_claim_is_usable(
                repo_path,
                review=review,
                source=source,
                known_repo_paths=_known_repo_paths(seed_repos),
            ):
                required_review_task_ids.append(task_id)
                blockers.append(_blocker(
                    "invalid_reviewed_repo_identity",
                    "repo identity decision lacks required evidence",
                    task_id=task_id,
                    group_idx=group_idx,
                    repo_path=repo_path,
                    source=source,
                ))
                continue

            _set_task_repo_path(updated_root_tasks, task_id, repo_path)
            _set_task_repo_path(updated_regroup_tasks, task_id, repo_path)
            changed_task_ids.append(task_id)
            resolved_repo_paths_by_task[task_id] = repo_path
            affected_groups.add(group_idx)
            if source == "workspace_authority":
                deterministic_task_ids.append(task_id)
            else:
                reviewed_task_ids.append(task_id)
                review_decisions.append(_review_decision(task_id, group_idx, review))

    move_result = _apply_reviewed_group_moves(
        updated_root,
        updated_regroup_dag,
        review_records,
        group_offset=group_offset,
        first_group_idx=resolved_first_group_idx,
        last_group_idx=resolved_last_group_idx,
    )
    if move_result["blockers"]:
        blockers.extend(move_result["blockers"])
    moved_task_ids.extend(move_result["moved_task_ids"])
    changed_task_ids.extend(move_result["moved_task_ids"])
    affected_groups.update(move_result["affected_group_indices"])
    review_decisions.extend(move_result["review_decisions"])

    normalized_legacy_files_task_ids: list[str] = []
    if normalize_legacy_files:
        normalized_legacy_files_task_ids = _normalize_legacy_files_for_group_range(
            updated_root,
            updated_regroup_dag,
            group_offset=group_offset,
            first_group_idx=resolved_first_group_idx,
            last_group_idx=resolved_last_group_idx,
        )
        changed_task_ids.extend(normalized_legacy_files_task_ids)
        for group_idx in _groups_for_task_ids(
            updated_regroup_dag,
            normalized_legacy_files_task_ids,
            group_offset=group_offset,
        ):
            affected_groups.add(group_idx)

    _sync_regroup_original_order(updated_regroup, updated_root, group_offset)
    _recompute_regroup_original_to_new_mapping(updated_regroup, group_offset)
    regroup_validation = _validate_regroup_hard_barriers(
        updated_regroup,
        group_offset=group_offset,
    )
    if not regroup_validation["approved"]:
        blockers.append(_blocker(
            "dag_regroup_barrier_violation",
            "reviewed group moves create derived waves with mixed hard barriers",
            violations=regroup_validation["violations"],
        ))

    if blockers:
        return _blocked_bulk_post_adoption_plan(
            feature_id=feature_id,
            boundary_group_idx=boundary_group_idx,
            first_group_idx=resolved_first_group_idx,
            last_group_idx=resolved_last_group_idx,
            regroup_active_key=active_key,
            regroup_canonical_key=canonical_key,
            regroup_rollback_key=rollback_key,
            seed_registry_key=registry_artifact_key,
            review_key=review_artifact_key,
            blockers=blockers,
        )

    refreshed_root_tasks = _tasks_by_id(updated_root)
    refreshed_order = list(updated_regroup_dag.get("execution_order") or [])
    group_registry_projections: dict[int, dict[str, Any]] = {}
    for group_idx in sorted(affected_groups):
        tasks = _tasks_for_group(refreshed_root_tasks, refreshed_order, group_idx, group_offset)
        registry = await _build_bulk_group_registry(
            registry_builder,
            feature_id=feature_id,
            group_idx=group_idx,
            tasks=tasks,
            seed_registry=seed_registry,
            feature_root=resolved_feature_root,
            workspace_root=resolved_workspace_root,
        )
        group_registry_projections[group_idx] = {
            "artifact_schema": "workspace-authority-compatibility-projection-v1",
            "authoritative_mode": "compatibility_projection",
            "registry": registry,
        }

    after_scan = [
        item for item in _scan_post_boundary_missing_repo_identity(
            updated_root,
            updated_regroup,
            group_idx_offset=group_offset,
            first_group_idx=resolved_first_group_idx,
        )
        if int(item.get("group_idx") or -1) <= resolved_last_group_idx
    ]
    if after_scan:
        blockers.append(_blocker(
            "post_repair_missing_repo_identity",
            "bulk repair did not eliminate all missing repo identities",
            missing_count=len(after_scan),
            task_ids=[str(item.get("task_id") or "") for item in after_scan[:20]],
        ))

    if blockers:
        return _blocked_bulk_post_adoption_plan(
            feature_id=feature_id,
            boundary_group_idx=boundary_group_idx,
            first_group_idx=resolved_first_group_idx,
            last_group_idx=resolved_last_group_idx,
            regroup_active_key=active_key,
            regroup_canonical_key=canonical_key,
            regroup_rollback_key=rollback_key,
            seed_registry_key=registry_artifact_key,
            review_key=review_artifact_key,
            blockers=blockers,
        )

    review_artifact = {
        "artifact_schema": "execution-control-post-adoption-repo-identity-review-v1",
        "feature_id": feature_id,
        "group_range": [resolved_first_group_idx, resolved_last_group_idx],
        "reviewed_task_ids": sorted(set(reviewed_task_ids)),
        "required_review_task_ids": sorted(set(required_review_task_ids)),
        "decisions": review_decisions,
        "records": list(review_records.values()),
    }
    registry_evidence = {
        "seed_artifact_id": _int_or_none(seed_record.get("id")),
        "seed_artifact_key": registry_artifact_key,
        "seed_registry_digest": seed_registry.get("registry_digest", ""),
        "seed_repo_count": len(seed_repos),
        "workspace_authority_projection_groups": sorted(group_registry_projections),
    }

    return PostAdoptionRepoIdentityBulkRepairPlan(
        feature_id=feature_id,
        boundary_group_idx=boundary_group_idx,
        first_group_idx=resolved_first_group_idx,
        last_group_idx=resolved_last_group_idx,
        regroup_active_key=active_key,
        regroup_canonical_key=canonical_key,
        regroup_rollback_key=rollback_key,
        seed_registry_key=registry_artifact_key,
        review_key=review_artifact_key,
        old_root_dag_artifact_id=_int_or_none(root_record.get("id")),
        old_root_dag_sha256=str(
            root_record.get("sha256") or _sha256(str(root_record.get("value") or ""))
        ),
        old_regroup_artifact_id=_int_or_none(regroup_record.get("id")),
        old_regroup_sha256=str(
            regroup_record.get("sha256") or _sha256(str(regroup_record.get("value") or ""))
        ),
        old_rollback_artifact_id=_int_or_none(rollback_record.get("id")),
        old_active_marker_artifact_id=_int_or_none(active_record.get("id")),
        changed_task_ids=tuple(dict.fromkeys(changed_task_ids)),
        deterministic_task_ids=tuple(sorted(set(deterministic_task_ids))),
        reviewed_task_ids=tuple(sorted(set(reviewed_task_ids))),
        split_original_task_ids=tuple(sorted(set(split_original_task_ids))),
        created_split_task_ids=tuple(dict.fromkeys(created_split_task_ids)),
        moved_task_ids=tuple(dict.fromkeys(moved_task_ids)),
        normalized_legacy_files_task_ids=tuple(dict.fromkeys(normalized_legacy_files_task_ids)),
        regroup_validation=regroup_validation,
        resolved_repo_paths_by_task=dict(sorted(resolved_repo_paths_by_task.items())),
        affected_group_indices=tuple(sorted(affected_groups)),
        group_registry_projections=group_registry_projections,
        post_boundary_missing_repo_identity_before=tuple(before_scan),
        post_boundary_missing_repo_identity_after=tuple(after_scan),
        review_artifact=review_artifact,
        root_dag=updated_root,
        regroup_projection=updated_regroup,
        rollback_projection=json.loads(_stable_json(rollback_payload)),
        active_marker=json.loads(_stable_json(active_marker)),
        registry_evidence=registry_evidence,
        blockers=(),
    )


def _feature_id(feature: Any) -> str:
    if isinstance(feature, str):
        return feature.strip()
    return str(getattr(feature, "id", "") or "").strip()


def _blocker(code: str, message: str, **details: Any) -> AdoptionMigrationBlocker:
    return AdoptionMigrationBlocker(code=code, message=message, details=details)


async def _artifact_record(
    artifact_store: AdoptionMigrationArtifactReader,
    key: str,
    *,
    feature: Any,
) -> dict[str, Any] | None:
    get_record = getattr(artifact_store, "get_record", None)
    if callable(get_record):
        record = await get_record(key, feature=feature)
        if record is None:
            return None
        value = _artifact_value_to_text(record.get("value"))
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at", ""),
            "key": record.get("key", key),
            "value": value,
            "sha256": record.get("sha256") or _sha256(value),
        }
    value = await artifact_store.get(key, feature=feature)
    if value is None:
        return None
    return {
        "id": None,
        "created_at": "",
        "key": key,
        "value": _artifact_value_to_text(value),
        "sha256": "",
    }


def _artifact_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "surrogateescape")
    return json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=False, separators=(",", ":"))


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _result_statuses(checkpoint: Mapping[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for raw in list(checkpoint.get("results") or []):
        if not isinstance(raw, Mapping):
            continue
        task_id = str(raw.get("task_id") or "")
        if task_id:
            statuses[task_id] = str(raw.get("status") or "")
    return statuses


def _validate_boundary_checkpoint(
    blockers: list[AdoptionMigrationBlocker],
    checkpoint: Mapping[str, Any],
    *,
    boundary_group_idx: int,
    boundary_task_ids: tuple[str, ...],
    boundary_result_statuses: dict[str, str],
) -> None:
    if checkpoint.get("group_idx") != boundary_group_idx:
        blockers.append(_blocker(
            "boundary_group_mismatch",
            "boundary checkpoint group_idx does not match requested boundary",
            expected=boundary_group_idx,
            actual=checkpoint.get("group_idx"),
        ))
    if checkpoint.get("verdict") != "approved":
        blockers.append(_blocker(
            "non_approved_boundary",
            "boundary checkpoint verdict must be approved",
            verdict=checkpoint.get("verdict"),
        ))
    result_ids = set(boundary_result_statuses)
    task_ids = set(boundary_task_ids)
    if result_ids != task_ids:
        blockers.append(_blocker(
            "incomplete_task_results",
            "boundary checkpoint results must cover every boundary task exactly",
            missing_task_ids=sorted(task_ids - result_ids),
            unexpected_task_ids=sorted(result_ids - task_ids),
        ))
    non_completed = {
        task_id: status
        for task_id, status in boundary_result_statuses.items()
        if status != "completed"
    }
    if non_completed:
        blockers.append(_blocker(
            "incomplete_task_results",
            "boundary checkpoint task results must all be completed",
            non_completed_statuses=non_completed,
        ))


async def _missing_checkpoint_groups(
    artifact_store: AdoptionMigrationArtifactReader,
    *,
    feature: Any,
    boundary_group_idx: int,
) -> list[int]:
    missing: list[int] = []
    if boundary_group_idx < 0:
        return missing
    for group_idx in range(boundary_group_idx + 1):
        record = await _artifact_record(
            artifact_store,
            f"dag-group:{group_idx}",
            feature=feature,
        )
        if record is None:
            missing.append(group_idx)
    return missing


async def _resolve_regroup_snapshot(
    artifact_store: AdoptionMigrationArtifactReader,
    *,
    feature: Any,
    supplied_ids: list[int] | tuple[int, ...] | None,
    supplied_metadata: Mapping[str, Any] | None,
) -> tuple[list[int], dict[str, Any], list[AdoptionMigrationBlocker]]:
    blockers: list[AdoptionMigrationBlocker] = []
    if supplied_ids is not None or supplied_metadata is not None:
        ids = [
            _id
            for _id in (_int_or_none(value) for value in (supplied_ids or []))
            if _id is not None
        ]
        return ids, dict(supplied_metadata or {}), blockers

    summaries = await _list_regroup_active_summaries(artifact_store, feature=feature)
    if not summaries:
        return [], {"status": "none_detected"}, blockers

    keys = sorted({str(item.get("key") or "") for item in summaries if item.get("key")})
    if len(keys) > 1:
        blockers.append(_blocker(
            "ambiguous_regroup_state",
            "multiple active regroup marker keys were observed",
            active_marker_keys=keys,
        ))
        return [], {"status": "ambiguous", "active_marker_keys": keys}, blockers

    latest = max(summaries, key=lambda item: int(item.get("id") or 0))
    key = str(latest.get("key") or "")
    marker_payload = _json_object(await artifact_store.get(key, feature=feature))
    artifact_id = _int_or_none(latest.get("id"))
    ids = [artifact_id] if artifact_id is not None else []
    metadata = {
        "status": "detected",
        "active_marker_key": key,
        "active_marker_artifact_id": artifact_id,
        "active_marker_payload": marker_payload,
    }
    return ids, metadata, blockers


async def _list_regroup_active_summaries(
    artifact_store: AdoptionMigrationArtifactReader,
    *,
    feature: Any,
) -> list[dict[str, Any]]:
    feature_id = _feature_id(feature)
    list_record_summaries = getattr(artifact_store, "list_record_summaries", None)
    if callable(list_record_summaries):
        rows = await list_record_summaries(
            feature_id=feature_id,
            prefixes=("dag-regroup-active:",),
            limit=50,
            order="desc",
        )
        return [dict(row) for row in rows]
    list_records = getattr(artifact_store, "list_records", None)
    if callable(list_records):
        rows = await list_records(
            feature_id=feature_id,
            prefixes=("dag-regroup-active:",),
            limit=50,
            order="desc",
        )
        return [dict(row) for row in rows]
    return []


def _validate_post_adoption_marker(
    blockers: list[AdoptionMigrationBlocker],
    marker: Mapping[str, Any],
    *,
    feature_id: str,
    boundary_group_idx: int,
    group_idx: int,
) -> None:
    if not marker:
        blockers.append(_blocker(
            "missing_adoption_marker",
            "post-adoption repair requires an execution-control adoption marker",
        ))
        return

    if str(marker.get("status") or "").lower() != "adopted":
        blockers.append(_blocker(
            "invalid_adoption_marker_status",
            "adoption marker status must be adopted",
            status=marker.get("status"),
        ))
    marker_feature_id = str(marker.get("feature_id") or "").strip()
    if marker_feature_id and marker_feature_id != feature_id:
        blockers.append(_blocker(
            "adoption_marker_feature_mismatch",
            "adoption marker feature_id does not match requested feature",
            expected=feature_id,
            actual=marker_feature_id,
        ))

    raw_range = marker.get("completed_checkpoint_range")
    checkpoint_range: tuple[int, int] | None = None
    if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
        start = _int_or_none(raw_range[0])
        end = _int_or_none(raw_range[1])
        if start is not None and end is not None:
            checkpoint_range = (start, end)
    if checkpoint_range != (0, boundary_group_idx):
        blockers.append(_blocker(
            "adoption_marker_range_mismatch",
            "adoption marker completed_checkpoint_range must match the declared boundary",
            expected=[0, boundary_group_idx],
            actual=list(checkpoint_range) if checkpoint_range is not None else raw_range,
        ))

    next_group_idx = _int_or_none(marker.get("next_effective_group_idx"))
    expected_next = boundary_group_idx + 1
    if next_group_idx != expected_next or group_idx != expected_next:
        blockers.append(_blocker(
            "adoption_marker_next_group_mismatch",
            "post-adoption repair is scoped to the first strict group after the boundary",
            expected_next_effective_group_idx=expected_next,
            marker_next_effective_group_idx=next_group_idx,
            requested_group_idx=group_idx,
        ))


async def _single_active_regroup_key(
    artifact_store: AdoptionMigrationArtifactReader,
    *,
    feature: Any,
    blockers: list[AdoptionMigrationBlocker],
) -> str:
    summaries = await _list_regroup_active_summaries(artifact_store, feature=feature)
    if not summaries:
        blockers.append(_blocker(
            "missing_active_regroup_marker",
            "no active regroup marker was observed",
        ))
        return ""
    keys = sorted({str(item.get("key") or "") for item in summaries if item.get("key")})
    if len(keys) != 1:
        blockers.append(_blocker(
            "ambiguous_regroup_state",
            "exactly one active regroup marker key is required",
            active_marker_keys=keys,
        ))
        return ""
    return keys[0]


def _registry_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    embedded = payload.get("registry")
    if isinstance(embedded, Mapping):
        return dict(embedded)
    if isinstance(embedded, str):
        return _json_object(embedded)
    if isinstance(payload.get("repos"), list):
        return dict(payload)
    return {}


def _blocked_post_adoption_plan(
    *,
    feature_id: str,
    group_idx: int,
    boundary_group_idx: int,
    regroup_active_key: str,
    regroup_canonical_key: str,
    regroup_rollback_key: str,
    registry_key: str,
    blockers: list[AdoptionMigrationBlocker],
) -> PostAdoptionRepoIdentityRepairPlan:
    return PostAdoptionRepoIdentityRepairPlan(
        feature_id=feature_id,
        group_idx=group_idx,
        boundary_group_idx=boundary_group_idx,
        regroup_active_key=regroup_active_key,
        regroup_canonical_key=regroup_canonical_key,
        regroup_rollback_key=regroup_rollback_key,
        registry_key=registry_key,
        old_root_dag_artifact_id=None,
        old_root_dag_sha256="",
        old_regroup_artifact_id=None,
        old_regroup_sha256="",
        old_rollback_artifact_id=None,
        old_active_marker_artifact_id=None,
        changed_task_ids=(),
        resolved_repo_paths_by_task={},
        current_group_task_ids=(),
        post_boundary_missing_repo_identity_before=(),
        post_boundary_missing_repo_identity_after=(),
        root_dag={},
        regroup_projection={},
        rollback_projection={},
        active_marker={},
        registry_evidence={},
        blockers=tuple(blockers),
    )


def _blocked_bulk_post_adoption_plan(
    *,
    feature_id: str,
    boundary_group_idx: int,
    first_group_idx: int,
    last_group_idx: int,
    regroup_active_key: str,
    regroup_canonical_key: str,
    regroup_rollback_key: str,
    seed_registry_key: str,
    review_key: str,
    blockers: list[AdoptionMigrationBlocker],
) -> PostAdoptionRepoIdentityBulkRepairPlan:
    return PostAdoptionRepoIdentityBulkRepairPlan(
        feature_id=feature_id,
        boundary_group_idx=boundary_group_idx,
        first_group_idx=first_group_idx,
        last_group_idx=last_group_idx,
        regroup_active_key=regroup_active_key,
        regroup_canonical_key=regroup_canonical_key,
        regroup_rollback_key=regroup_rollback_key,
        seed_registry_key=seed_registry_key,
        review_key=review_key,
        old_root_dag_artifact_id=None,
        old_root_dag_sha256="",
        old_regroup_artifact_id=None,
        old_regroup_sha256="",
        old_rollback_artifact_id=None,
        old_active_marker_artifact_id=None,
        changed_task_ids=(),
        deterministic_task_ids=(),
        reviewed_task_ids=(),
        split_original_task_ids=(),
        created_split_task_ids=(),
        moved_task_ids=(),
        normalized_legacy_files_task_ids=(),
        regroup_validation={},
        resolved_repo_paths_by_task={},
        affected_group_indices=(),
        group_registry_projections={},
        post_boundary_missing_repo_identity_before=(),
        post_boundary_missing_repo_identity_after=(),
        review_artifact={},
        root_dag={},
        regroup_projection={},
        rollback_projection={},
        active_marker={},
        registry_evidence={},
        blockers=tuple(blockers),
    )


def _normalize_review_records(
    reviewed_records: Mapping[str, Any] | list[Any] | tuple[Any, ...] | None,
) -> tuple[dict[str, dict[str, Any]], list[AdoptionMigrationBlocker]]:
    if reviewed_records is None:
        raw_records: list[Any] = []
    elif isinstance(reviewed_records, Mapping):
        if isinstance(reviewed_records.get("records"), list):
            raw_records = list(reviewed_records.get("records") or [])
        elif isinstance(reviewed_records.get("decisions"), list):
            raw_records = list(reviewed_records.get("decisions") or [])
        else:
            raw_records = list(reviewed_records.values())
    else:
        raw_records = list(reviewed_records)

    records: dict[str, dict[str, Any]] = {}
    blockers: list[AdoptionMigrationBlocker] = []
    for raw in raw_records:
        record = _mapping_for(raw)
        task_id = str(record.get("task_id") or "").strip()
        if not task_id:
            blockers.append(_blocker(
                "invalid_review_record",
                "review record is missing task_id",
                record=record,
            ))
            continue
        if task_id in records:
            blockers.append(_blocker(
                "duplicate_review_record",
                "review records must be unique by task_id",
                task_id=task_id,
            ))
            continue
        if record.get("blocker"):
            blockers.append(_blocker(
                "blocked_review_record",
                "review record contains a blocker",
                task_id=task_id,
                blocker=record.get("blocker"),
            ))
        records[task_id] = record
    return records, blockers


def _workspace_root_from_feature_root(feature_root: str) -> str:
    if not feature_root:
        return ""
    parts = feature_root.replace("\\", "/").split("/")
    try:
        idx = parts.index(".iriai")
    except ValueError:
        return ""
    if idx <= 0:
        return ""
    return "/".join(parts[:idx])


async def _build_bulk_group_registry(
    registry_builder: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None,
    *,
    feature_id: str,
    group_idx: int,
    tasks: list[Any],
    seed_registry: Mapping[str, Any],
    feature_root: str,
    workspace_root: str,
) -> dict[str, Any]:
    builder = registry_builder or _default_bulk_workspace_registry
    value = builder(
        feature_id=feature_id,
        group_idx=group_idx,
        tasks=tasks,
        seed_registry=seed_registry,
        feature_root=feature_root,
        workspace_root=workspace_root,
    )
    if hasattr(value, "__await__"):
        value = await value  # type: ignore[assignment]
    registry = _mapping_for(value)
    if "registry" in registry and isinstance(registry.get("registry"), Mapping):
        registry = dict(registry["registry"])
    return registry


async def _default_bulk_workspace_registry(
    *,
    feature_id: str,
    group_idx: int,
    tasks: list[Any],
    seed_registry: Mapping[str, Any],
    feature_root: str,
    workspace_root: str,
) -> Mapping[str, Any]:
    del group_idx
    from iriai_build_v2.workflows.develop.execution.workspace_authority import (
        WorkspaceAuthority,
    )

    authority = WorkspaceAuthority(
        feature_root=feature_root or None,
        workspace_root=workspace_root or None,
        feature_slug=str(seed_registry.get("feature_slug") or ""),
    )
    registry = await authority.build_registry(
        feature_id,
        tasks,
        feature_root=feature_root or None,
        feature_slug=str(seed_registry.get("feature_slug") or ""),
        workspace_root=workspace_root or None,
    )
    return _mapping_for(registry)


def _tasks_for_group(
    tasks_by_id: Mapping[str, Any],
    execution_order: list[Any],
    group_idx: int,
    group_offset: int,
) -> list[Any]:
    local_group_idx = group_idx - group_offset
    if local_group_idx < 0 or local_group_idx >= len(execution_order):
        return []
    raw_group = execution_order[local_group_idx]
    if not isinstance(raw_group, list):
        return []
    return [
        tasks_by_id[str(task_id)]
        for task_id in raw_group
        if str(task_id) in tasks_by_id
    ]


def _path_matching_registry_repos(paths: list[str], repos: list[Any]) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for raw_repo in repos:
        repo = _mapping_for(raw_repo)
        repo_path = _repo_path_from_registry_repo(repo)
        if not repo_path:
            continue
        for path in paths:
            text = str(path or "").replace("\\", "/").strip().strip("/")
            if text == repo_path or text.startswith(f"{repo_path}/"):
                matches[repo_path] = repo
                break
    return [matches[key] for key in sorted(matches)]


def _known_repo_paths(repos: list[Any]) -> set[str]:
    return {
        repo_path
        for repo_path in (
            _repo_path_from_registry_repo(_mapping_for(repo))
            for repo in repos
        )
        if repo_path
    }


def _review_or_claim_is_usable(
    repo_path: str,
    *,
    review: Mapping[str, Any] | None,
    source: str,
    known_repo_paths: set[str],
) -> bool:
    if not repo_path or repo_path not in known_repo_paths:
        return False
    if source == "workspace_authority":
        return True
    if not review:
        return False
    if review.get("blocker"):
        return False
    if not str(review.get("reviewer_id") or "").strip():
        return False
    if not list(review.get("evidence_paths") or []):
        return False
    try:
        confidence = float(review.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence >= 0.8


def _review_decision(
    task_id: str,
    group_idx: int,
    review: Mapping[str, Any] | None,
) -> dict[str, Any]:
    body = dict(review or {})
    body["task_id"] = task_id
    body["group_idx"] = group_idx
    return body


def _apply_reviewed_group_moves(
    root_dag: dict[str, Any],
    regroup_dag: dict[str, Any],
    review_records: Mapping[str, Mapping[str, Any]],
    *,
    group_offset: int,
    first_group_idx: int,
    last_group_idx: int,
) -> dict[str, Any]:
    blockers: list[AdoptionMigrationBlocker] = []
    moved_task_ids: list[str] = []
    affected_group_indices: set[int] = set()
    review_decisions: list[dict[str, Any]] = []
    root_order = list(root_dag.get("execution_order") or [])
    regroup_order = list(regroup_dag.get("execution_order") or [])

    for task_id, review in review_records.items():
        target_group_idx = _int_or_none(review.get("target_group_idx"))
        if target_group_idx is None:
            continue
        if not _review_evidence_is_usable(review):
            blockers.append(_blocker(
                "invalid_group_move_review",
                "group move review lacks required evidence",
                task_id=task_id,
                target_group_idx=target_group_idx,
            ))
            continue
        if target_group_idx < first_group_idx or target_group_idx > last_group_idx:
            blockers.append(_blocker(
                "group_move_target_out_of_range",
                "group move target must stay inside the repaired group range",
                task_id=task_id,
                target_group_idx=target_group_idx,
                first_group_idx=first_group_idx,
                last_group_idx=last_group_idx,
            ))
            continue
        local_source = _find_task_group(regroup_order, task_id)
        if local_source is None:
            blockers.append(_blocker(
                "group_move_task_not_found",
                "group move source task is not in active regroup execution_order",
                task_id=task_id,
            ))
            continue
        source_group_idx = group_offset + local_source
        if source_group_idx < first_group_idx or source_group_idx > last_group_idx:
            blockers.append(_blocker(
                "group_move_source_out_of_range",
                "group move source must stay inside the repaired group range",
                task_id=task_id,
                source_group_idx=source_group_idx,
                first_group_idx=first_group_idx,
                last_group_idx=last_group_idx,
            ))
            continue
        if source_group_idx == target_group_idx:
            continue
        local_target = target_group_idx - group_offset
        if not (0 <= local_target < len(regroup_order)):
            blockers.append(_blocker(
                "group_move_target_not_in_regroup",
                "group move must be represented in active regroup order",
                task_id=task_id,
                source_group_idx=source_group_idx,
                target_group_idx=target_group_idx,
            ))
            continue
        _move_task_in_order(regroup_order, task_id, local_source, local_target)
        root_source_group_idx = _find_task_group(root_order, task_id)
        if root_source_group_idx is not None and target_group_idx < len(root_order):
            _move_task_in_order(root_order, task_id, root_source_group_idx, target_group_idx)
        moved_task_ids.append(task_id)
        affected_group_indices.update({source_group_idx, target_group_idx})
        review_decisions.append(_review_decision(task_id, target_group_idx, review))

    root_dag["execution_order"] = root_order
    regroup_dag["execution_order"] = regroup_order
    return {
        "blockers": blockers,
        "moved_task_ids": moved_task_ids,
        "affected_group_indices": sorted(affected_group_indices),
        "review_decisions": review_decisions,
    }


def _review_evidence_is_usable(review: Mapping[str, Any]) -> bool:
    if review.get("blocker"):
        return False
    if not str(review.get("reviewer_id") or "").strip():
        return False
    if not list(review.get("evidence_paths") or []):
        return False
    try:
        confidence = float(review.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence >= 0.8


def _find_task_group(order: list[Any], task_id: str) -> int | None:
    for group_idx, raw_group in enumerate(order):
        if isinstance(raw_group, list) and task_id in {str(item) for item in raw_group}:
            return group_idx
    return None


def _move_task_in_order(
    order: list[Any],
    task_id: str,
    source_group_idx: int,
    target_group_idx: int,
) -> None:
    source = list(order[source_group_idx])
    target = list(order[target_group_idx])
    order[source_group_idx] = [str(item) for item in source if str(item) != task_id]
    if task_id not in {str(item) for item in target}:
        target.append(task_id)
    order[target_group_idx] = target


def _recompute_regroup_original_to_new_mapping(
    regroup_payload: dict[str, Any],
    group_offset: int,
) -> None:
    original_order = list(regroup_payload.get("original_execution_order") or [])
    regroup_dag = (
        regroup_payload.get("dag")
        if isinstance(regroup_payload.get("dag"), dict)
        else {}
    )
    derived_order = list(regroup_dag.get("execution_order") or [])
    original_group_by_task: dict[str, int] = {}
    for local_original_idx, raw_group in enumerate(original_order):
        if not isinstance(raw_group, list):
            continue
        original_group_idx = group_offset + local_original_idx
        for raw_task_id in raw_group:
            task_id = str(raw_task_id)
            if task_id:
                original_group_by_task[task_id] = original_group_idx

    mapping: dict[str, list[int]] = {
        str(group_offset + local_original_idx): []
        for local_original_idx, raw_group in enumerate(original_order)
        if isinstance(raw_group, list)
    }
    for local_derived_idx, raw_group in enumerate(derived_order):
        if not isinstance(raw_group, list):
            continue
        derived_group_idx = group_offset + local_derived_idx
        for raw_task_id in raw_group:
            original_group_idx = original_group_by_task.get(str(raw_task_id))
            if original_group_idx is None:
                continue
            bucket = mapping.setdefault(str(original_group_idx), [])
            if derived_group_idx not in bucket:
                bucket.append(derived_group_idx)

    regroup_payload["original_to_new_group_mapping"] = {
        original_group: sorted(new_groups)
        for original_group, new_groups in sorted(
            mapping.items(),
            key=lambda item: int(item[0]) if str(item[0]).lstrip("-").isdigit() else item[0],
        )
    }


def _validate_regroup_hard_barriers(
    regroup_payload: Mapping[str, Any],
    *,
    group_offset: int,
) -> dict[str, Any]:
    regroup_dag = _mapping_for(regroup_payload.get("dag"))
    execution_order = list(regroup_dag.get("execution_order") or [])
    barrier_by_task = _regroup_hard_barrier_by_task_from_projection(regroup_payload)
    violations: list[dict[str, Any]] = []
    for local_group_idx, raw_group in enumerate(execution_order):
        if not isinstance(raw_group, list):
            continue
        hard_barriers = sorted({
            barrier_by_task[str(task_id)]
            for task_id in raw_group
            if str(task_id) in barrier_by_task
        })
        if len(hard_barriers) > 1:
            violations.append({
                "new_group": group_offset + local_group_idx,
                "barriers": hard_barriers,
                "task_ids": [str(task_id) for task_id in raw_group],
            })
    return {
        "approved": not violations,
        "reason": "" if not violations else "dag_regroup_barrier_violation",
        "violations": violations[:20],
    }


def _regroup_hard_barrier_by_task_from_projection(
    regroup_payload: Mapping[str, Any],
) -> dict[str, str]:
    barrier_by_task: dict[str, str] = {}
    for idx, raw_barrier in enumerate(list(regroup_payload.get("barriers") or [])):
        barrier = _mapping_for(raw_barrier)
        hard = barrier.get("hard", True)
        if hard is False or str(hard).lower() in {"false", "0", "no", "off"}:
            continue
        barrier_id = str(
            barrier.get("id")
            or barrier.get("barrier_id")
            or barrier.get("name")
            or barrier.get("kind")
            or f"barrier-{idx}"
        ).strip()
        if not barrier_id:
            continue
        for raw_task_id in list(barrier.get("task_ids") or []):
            task_id = str(raw_task_id).strip()
            if task_id:
                barrier_by_task.setdefault(task_id, barrier_id)

    speed_tasks = _mapping_for(_mapping_for(regroup_payload.get("speed_index")).get("tasks"))
    for task_id, raw_metadata in speed_tasks.items():
        metadata = _mapping_for(raw_metadata)
        barrier_id = str(metadata.get("barrier") or "").strip()
        if barrier_id:
            barrier_by_task.setdefault(str(task_id), barrier_id)
    return barrier_by_task


def _normalize_legacy_files_for_group_range(
    root_dag: dict[str, Any],
    regroup_dag: dict[str, Any],
    *,
    group_offset: int,
    first_group_idx: int,
    last_group_idx: int,
) -> list[str]:
    root_tasks = _tasks_by_id(root_dag)
    regroup_tasks = _tasks_by_id(regroup_dag)
    task_ids: set[str] = set()
    order = list(regroup_dag.get("execution_order") or [])
    for local_idx, raw_group in enumerate(order):
        group_idx = group_offset + local_idx
        if group_idx < first_group_idx or group_idx > last_group_idx:
            continue
        if isinstance(raw_group, list):
            task_ids.update(str(task_id) for task_id in raw_group)

    changed: list[str] = []
    for task_id in sorted(task_ids):
        touched = False
        for tasks_by_id in (root_tasks, regroup_tasks):
            task = tasks_by_id.get(task_id)
            if not isinstance(task, dict):
                continue
            if list(task.get("file_scope") or []) and list(task.get("files") or []):
                task["files"] = []
                touched = True
        if touched:
            changed.append(task_id)
    return changed


def _groups_for_task_ids(
    regroup_dag: Mapping[str, Any],
    task_ids: list[str],
    *,
    group_offset: int,
) -> list[int]:
    wanted = set(task_ids)
    groups: list[int] = []
    for local_idx, raw_group in enumerate(list(regroup_dag.get("execution_order") or [])):
        if isinstance(raw_group, list) and wanted.intersection(str(item) for item in raw_group):
            groups.append(group_offset + local_idx)
    return groups


def _apply_reviewed_task_split(
    root_dag: dict[str, Any],
    regroup_dag: dict[str, Any],
    root_tasks_by_id: dict[str, Any],
    regroup_tasks_by_id: dict[str, Any],
    task: Any,
    review: Mapping[str, Any],
    *,
    group_idx: int,
    known_repo_paths: set[str],
) -> dict[str, Any]:
    task_id = str(_value_for(task, "id") or "")
    blockers: list[AdoptionMigrationBlocker] = []
    split_specs = list(review.get("split_tasks") or [])
    if not split_specs:
        return {
            "blockers": [_blocker(
                "missing_split_tasks",
                "cross-repo review must provide split_tasks",
                task_id=task_id,
                group_idx=group_idx,
            )],
            "created_task_ids": [],
            "repo_paths_by_task": {},
        }

    downstream = _downstream_dependency_task_ids(root_dag, task_id)
    if downstream:
        blockers.append(_blocker(
            "cross_repo_split_has_downstream_dependencies",
            "cross-repo task split requires dependency rewrites",
            task_id=task_id,
            downstream_task_ids=downstream,
        ))

    original_paths = set(_task_path_examples(task))
    selected_paths: set[str] = set()
    split_tasks: list[dict[str, Any]] = []
    repo_paths_by_task: dict[str, str] = {}
    created_task_ids: list[str] = []
    existing_task_ids = set(root_tasks_by_id) | set(regroup_tasks_by_id)

    for raw_spec in split_specs:
        spec = _mapping_for(raw_spec)
        split_id = str(spec.get("task_id") or spec.get("id") or "").strip()
        repo_path = _normalize_repo_path(spec.get("repo_path"))
        paths = [str(path) for path in list(spec.get("file_scope_paths") or spec.get("paths") or [])]
        if not split_id:
            blockers.append(_blocker(
                "invalid_split_task",
                "split task is missing task_id",
                parent_task_id=task_id,
            ))
            continue
        if split_id in existing_task_ids or split_id in created_task_ids:
            blockers.append(_blocker(
                "duplicate_split_task_id",
                "split task id already exists",
                task_id=split_id,
                parent_task_id=task_id,
            ))
            continue
        if repo_path not in known_repo_paths:
            blockers.append(_blocker(
                "invalid_split_repo_path",
                "split task repo_path is not a known repo",
                task_id=split_id,
                repo_path=repo_path,
            ))
            continue
        if not paths:
            blockers.append(_blocker(
                "missing_split_paths",
                "split task must list file_scope_paths",
                task_id=split_id,
            ))
            continue
        unknown = sorted(set(paths) - original_paths)
        if unknown:
            blockers.append(_blocker(
                "unknown_split_paths",
                "split task references paths outside original task scope",
                task_id=split_id,
                paths=unknown,
            ))
            continue
        selected_paths.update(paths)
        split_task = _copy_task_for_split(task, split_id, repo_path, set(paths), spec)
        split_tasks.append(split_task)
        created_task_ids.append(split_id)
        repo_paths_by_task[split_id] = repo_path

    if original_paths and selected_paths != original_paths:
        blockers.append(_blocker(
            "incomplete_split_path_coverage",
            "split tasks must cover original file scope exactly",
            task_id=task_id,
            missing_paths=sorted(original_paths - selected_paths),
            unexpected_paths=sorted(selected_paths - original_paths),
        ))

    if blockers:
        return {
            "blockers": blockers,
            "created_task_ids": [],
            "repo_paths_by_task": {},
        }

    if not _replace_task_with_splits(root_dag, task_id, split_tasks):
        blockers.append(_blocker(
            "missing_split_source_task",
            "source task was not found in root DAG",
            task_id=task_id,
        ))
    _replace_task_with_splits(regroup_dag, task_id, split_tasks)
    root_tasks_by_id.pop(task_id, None)
    regroup_tasks_by_id.pop(task_id, None)
    for split_task in split_tasks:
        root_tasks_by_id[split_task["id"]] = split_task
        regroup_tasks_by_id[split_task["id"]] = split_task

    return {
        "blockers": blockers,
        "created_task_ids": created_task_ids,
        "repo_paths_by_task": repo_paths_by_task,
    }


def _downstream_dependency_task_ids(dag_payload: Mapping[str, Any], task_id: str) -> list[str]:
    downstream: list[str] = []
    for task in list(dag_payload.get("tasks") or []):
        if not isinstance(task, Mapping):
            continue
        deps = [str(item) for item in list(task.get("dependencies") or [])]
        if task_id in deps:
            downstream.append(str(task.get("id") or ""))
    return sorted(task_id for task_id in downstream if task_id)


def _copy_task_for_split(
    task: Any,
    split_id: str,
    repo_path: str,
    selected_paths: set[str],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    body = json.loads(_stable_json(_mapping_for(task) or task))
    body["id"] = split_id
    body["repo_path"] = repo_path
    body["name"] = str(spec.get("name") or f"{body.get('name', split_id)} ({repo_path})")
    body["file_scope"] = [
        scope for scope in list(body.get("file_scope") or [])
        if str(_value_for(scope, "path") or "") in selected_paths
    ]
    body["files"] = [
        path for path in list(body.get("files") or [])
        if str(path) in selected_paths
    ]
    return body


def _replace_task_with_splits(
    dag_payload: dict[str, Any],
    task_id: str,
    split_tasks: list[dict[str, Any]],
) -> bool:
    tasks = list(dag_payload.get("tasks") or [])
    found = False
    next_tasks: list[Any] = []
    for task in tasks:
        current_id = str(_value_for(task, "id") or "")
        if current_id == task_id:
            next_tasks.extend(split_tasks)
            found = True
        else:
            next_tasks.append(task)
    if found:
        dag_payload["tasks"] = next_tasks
    order = list(dag_payload.get("execution_order") or [])
    split_ids = [task["id"] for task in split_tasks]
    dag_payload["execution_order"] = _replace_task_id_in_order(order, task_id, split_ids)
    return found


def _replace_task_id_in_order(
    order: list[Any],
    task_id: str,
    replacement_ids: list[str],
) -> list[Any]:
    updated: list[Any] = []
    for raw_group in order:
        if not isinstance(raw_group, list):
            updated.append(raw_group)
            continue
        group: list[str] = []
        for raw_task_id in raw_group:
            current = str(raw_task_id)
            if current == task_id:
                group.extend(replacement_ids)
            else:
                group.append(current)
        updated.append(group)
    return updated


def _sync_regroup_original_order(
    regroup_payload: dict[str, Any],
    root_dag: Mapping[str, Any],
    group_offset: int,
) -> None:
    root_order = list(root_dag.get("execution_order") or [])
    if 0 <= group_offset <= len(root_order):
        regroup_payload["original_execution_order"] = [
            list(group) for group in root_order[group_offset:]
        ]


def _tasks_by_id(dag_payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_tasks = dag_payload.get("tasks") or dag_payload.get("task_definitions") or []
    if isinstance(raw_tasks, Mapping):
        raw_iterable = raw_tasks.values()
    elif isinstance(raw_tasks, list):
        raw_iterable = raw_tasks
    else:
        raw_iterable = []

    tasks: dict[str, Any] = {}
    for task in raw_iterable:
        task_id = str(_value_for(task, "id") or _value_for(task, "task_id") or "").strip()
        if task_id:
            tasks[task_id] = task
    return tasks


def _task_has_repo_identity(task: Any) -> bool:
    return bool(str(_value_for(task, "repo_id") or "").strip()) or bool(
        str(_value_for(task, "repo_path") or "").strip()
    )


def _registry_repos_claiming_task(registry_repos: list[Any], task_id: str) -> list[dict[str, Any]]:
    claiming: list[dict[str, Any]] = []
    for raw_repo in registry_repos:
        repo = _mapping_for(raw_repo)
        claimed_ids: set[str] = set()
        for field in ("writable_task_ids", "read_only_task_ids", "task_ids"):
            values = repo.get(field) or []
            if isinstance(values, str):
                claimed_ids.add(values)
            elif isinstance(values, list):
                claimed_ids.update(str(item) for item in values)
            elif isinstance(values, tuple):
                claimed_ids.update(str(item) for item in values)
        if task_id in claimed_ids:
            claiming.append(repo)
    return claiming


def _repo_path_from_registry_repo(repo: Mapping[str, Any]) -> str:
    for field in ("workspace_relative_path", "repo_path"):
        normalized = _normalize_repo_path(repo.get(field))
        if normalized:
            return normalized
    return ""


def _normalize_repo_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if not text or text == "." or text.startswith("~"):
        return ""
    if ":" in text.split("/", 1)[0]:
        return ""
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _set_task_repo_path(tasks_by_id: Mapping[str, Any], task_id: str, repo_path: str) -> None:
    task = tasks_by_id.get(task_id)
    if task is None:
        return
    if isinstance(task, dict):
        task["repo_path"] = repo_path
        return
    setattr(task, "repo_path", repo_path)


def _scan_post_boundary_missing_repo_identity(
    root_payload: Mapping[str, Any],
    regroup_payload: Mapping[str, Any],
    *,
    group_idx_offset: int,
    first_group_idx: int,
) -> list[dict[str, Any]]:
    root_tasks = _tasks_by_id(root_payload)
    regroup_dag = _json_object(regroup_payload.get("dag"))
    regroup_tasks = _tasks_by_id(regroup_dag)
    order = regroup_dag.get("execution_order") or []
    if not isinstance(order, list):
        return []

    missing: list[dict[str, Any]] = []
    for local_group_idx, raw_group in enumerate(order):
        group_idx = group_idx_offset + local_group_idx
        if group_idx < first_group_idx:
            continue
        if not isinstance(raw_group, list):
            continue
        for raw_task_id in raw_group:
            task_id = str(raw_task_id)
            task = root_tasks.get(task_id) or regroup_tasks.get(task_id)
            if task is None or _task_has_repo_identity(task):
                continue
            file_scope_paths = _task_path_examples(task)
            missing.append({
                "group_idx": group_idx,
                "local_group_idx": local_group_idx,
                "task_id": task_id,
                "task_name": str(_value_for(task, "name") or ""),
                "file_scope_paths": file_scope_paths[:5],
                "file_scope_path_count": len(file_scope_paths),
            })
    return missing


def _task_path_examples(task: Any) -> list[str]:
    paths: list[str] = []
    for scope in _value_for(task, "file_scope") or []:
        path = str(_value_for(scope, "path") or "").strip()
        if path:
            paths.append(path)
    for path in _value_for(task, "files") or []:
        text = str(path).strip()
        if text:
            paths.append(text)
    return list(dict.fromkeys(paths))


def _mapping_for(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="json"))
    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        return dict(as_dict())
    return {}


def _value_for(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)
