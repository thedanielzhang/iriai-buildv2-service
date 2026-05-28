"""Read-only helpers for one-off in-flight execution-control adoption.

This module is an operator aid, not a runtime fallback. It inspects an
in-flight feature at a declared safe boundary and returns the typed inputs that
would be written as an :class:`InFlightAdoptionRecord`. It never writes
artifacts, mutates DAGs, repairs checkpoints, or infers adoption.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .atomic_landing import InFlightAdoptionRecord, RollbackDisposition

__all__ = [
    "AdoptionMigrationArtifactReader",
    "AdoptionMigrationBlocker",
    "AdoptionPreflightSnapshot",
    "AdoptionMigrationPreflight",
    "build_in_flight_adoption_preflight",
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
        if await _artifact_record(artifact_store, f"dag-group:{group_idx}", feature=feature) is None:
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
        ids = [_id for _id in (_int_or_none(value) for value in (supplied_ids or [])) if _id is not None]
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
