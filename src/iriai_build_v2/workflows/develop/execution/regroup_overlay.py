"""Typed regroup overlay schema + models (Slice 09a).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup
(``dag_regroup.py``) into a reusable typed *overlay* that can be validated,
activated, resolved, observed, and rolled back through one store API while
projecting the legacy ``dag-regroup:*`` artifacts synchronously for existing
readers. The root ``dag`` is never overwritten.

This module (``09a``) is the **foundation**: the typed overlay/feedback models
and the deterministic ``overlay_id`` / ``overlay_slug`` derivations only. The
``validate_overlay`` algorithm (09b), activation / rollback / resolver (09c),
and scheduler feedback / adaptive-sizing logic (09d) land in later sub-slices.
``dag_regroup.py`` is intentionally left untouched by 09a.

Field lists, types, and the identifier-derivation formulas are transcribed
from ``docs/execution-control-plane/09-regroup-overlay-and-scheduler-
feedback.md`` ("Overlay Schema", "Scheduler Feedback Schema", and the
deterministic-identifier rules near the top of "Proposed Interfaces/Types").
A :class:`~iriai_build_v2.models.outputs.DerivedDAGArtifact` (models/outputs.py)
remains the compatibility payload for artifact consumers; the typed
:class:`RegroupOverlay` is the canonical record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ....execution_control.models import stable_digest
from ....models.outputs import (
    DerivedDAGArtifact,
    ImplementationDAG,
    ImplementationTask,
)
from .repair import _is_derived_dag_artifact_key

__all__ = [
    "OverlayStatus",
    "RegroupActiveMarkerStatus",
    "OverlayBarrierSource",
    "SchedulerGroupMetricState",
    "SchedulerFeedbackDataQuality",
    "SchedulerFeedbackConfidence",
    "OverlayCompatibilityKeys",
    "OverlayBarrier",
    "OverlayTaskSpeedMetadata",
    "RegroupActivationContract",
    "RegroupRollbackPlan",
    "RegroupActiveMarker",
    "RegroupOverlay",
    "SchedulerGroupMetric",
    "SchedulerFeedback",
    "derive_overlay_id",
    "derive_overlay_slug",
    "derive_metric_id",
    "_validate_derived_dag_artifact_update",
    "_validate_regroup_against_base_dag",
    "_regroup_task_definition_for_compare",
    "_regroup_hard_barrier_by_task",
    "_derived_dag_write_set_conflicts",
    "_derived_dag_task_write_sets",
    "_regroup_task_declared_write_paths",
]


# ── Status / enum literals ──────────────────────────────────────────────────
#
# doc 09 spells these inline on each model; hoisting them to named aliases
# mirrors the Slice 08 ``merge_queue_store`` convention (``MergeQueueStatus``,
# ``RepoTargetStatus``) so callers and the store layer share one vocabulary.

OverlayStatus = Literal["staged", "active", "rolled_back", "superseded", "rejected"]
RegroupActiveMarkerStatus = Literal["active", "rolled_back"]
OverlayBarrierSource = Literal["task_contract", "speed_index", "operator", "legacy"]
SchedulerGroupMetricState = Literal[
    "pending",
    "active",
    "completed",
    "failed",
    "rolled_back",
]
SchedulerFeedbackDataQuality = Literal["sufficient", "insufficient", "mixed", "stale"]
SchedulerFeedbackConfidence = Literal["low", "medium", "high"]


# ── Overlay schema (doc 09 § "Overlay Schema") ──────────────────────────────


class OverlayCompatibilityKeys(BaseModel):
    """Legacy artifact keys an overlay projects to (doc 09 § "Overlay Schema").

    The projection path is one-way from typed overlay rows to legacy artifacts;
    these keys identify the compatibility views, not a second authority.
    """

    canonical_artifact_key: str
    active_marker_key: str
    rollback_artifact_key: str
    observation_artifact_key: str
    sizing_review_key_prefix: str
    projection_idempotency_keys: dict[str, str] = Field(default_factory=dict)
    legacy_alias_keys: list[str] = Field(default_factory=list)


class OverlayBarrier(BaseModel):
    """A hard/soft barrier over a set of overlay task ids."""

    barrier_id: str
    task_ids: list[str]
    hard: bool = True
    source: OverlayBarrierSource


class OverlayTaskSpeedMetadata(BaseModel):
    """Per-task scheduling metadata carried by the overlay's ``speed_index``."""

    semantic_lane: str = "unknown"
    barrier: str = "unknown"
    critical_path_depth: int = 0
    commit_risk: int = 0
    verification_cost: int = 0
    unknown_write: bool = False
    scheduler_feedback_ids: list[int] = Field(default_factory=list)


class RegroupActivationContract(BaseModel):
    """The bounded checks activation must satisfy before flipping a row active.

    doc 09 § "Activation And Rollback Constraints" enumerates the matching
    runtime checks; this is their typed contract form.
    """

    required_checkpoint_key: str
    forbidden_checkpoint_key: str
    forbidden_first_wave_task_keys: list[str]
    forbidden_group_artifact_prefixes: list[str]
    forbidden_group_event_idx: int
    required_base_dag_artifact_id: int
    required_base_dag_sha256: str
    required_overlay_sha256: str
    requires_feature_advisory_lock: bool = True


class RegroupRollbackPlan(BaseModel):
    """Bounded rollback eligibility plan for an overlay.

    Rollback is allowed only before the first derived wave starts; after that
    boundary a forward-only overlay is the only regroup mutation path.
    """

    restore_source_dag_key: str
    restore_from_checkpoint_group: int
    rollback_marker_key: str
    allowed_until_group_idx: int
    forbidden_started_keys: list[str]
    forbidden_started_event_group_idx: int
    forbidden_typed_attempt_group_idx: int
    forbidden_merge_queue_group_idx: int
    forward_only_after_start: bool = True


class RegroupActiveMarker(BaseModel):
    """The ``dag-regroup-active:{slug}`` marker body (doc 09 § "Overlay Schema").

    The marker must reference the exact typed row id, overlay id/sha,
    validation digest, canonical / rollback artifact ids+keys, base DAG
    id/hash, and group offset committed in the typed overlay row.
    """

    schema_version: Literal[1] = 1
    status: RegroupActiveMarkerStatus
    feature_id: str
    overlay_id: str
    overlay_slug: str
    overlay_row_id: int
    canonical_artifact_key: str
    canonical_artifact_id: int
    canonical_sha256: str
    active_marker_key: str
    rollback_artifact_key: str
    rollback_artifact_id: int
    source_dag_key: str
    base_dag_artifact_id: int
    base_dag_sha256: str
    checkpointed_group: int
    group_idx_offset: int
    validation_digest: str
    activated_at: datetime | None = None
    rolled_back_at: datetime | None = None
    reason: str = ""


class RegroupOverlay(BaseModel):
    """The canonical typed regroup record (doc 09 § "Overlay Schema").

    A :class:`~iriai_build_v2.models.outputs.DerivedDAGArtifact` remains the
    compatibility payload for artifact consumers and is generated from this
    typed overlay plus the unchanged base task definitions.
    """

    schema_version: Literal[1] = 1
    overlay_id: str
    overlay_slug: str
    feature_id: str
    status: OverlayStatus
    artifact_key: str
    source_dag_key: str
    base_dag_artifact_id: int
    base_dag_sha256: str
    checkpointed_group: int
    group_idx_offset: int
    last_original_group: int | None = None
    original_execution_order: list[list[str]]
    derived_execution_order: list[list[str]]
    original_to_new_group_mapping: dict[int, list[int]]
    task_definition_fingerprints: dict[str, str]
    remaining_dependency_edges: dict[str, list[str]]
    barriers: list[OverlayBarrier]
    write_sets: dict[str, list[str]]
    speed_index: dict[str, OverlayTaskSpeedMetadata]
    activation_contract: RegroupActivationContract
    rollback_plan: RegroupRollbackPlan
    compatibility_keys: OverlayCompatibilityKeys
    validation_evidence_ids: list[int] = Field(default_factory=list)
    scheduler_feedback_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    activated_at: datetime | None = None
    rolled_back_at: datetime | None = None
    reason: str = ""
    overlay_sha256: str
    validation_digest: str


# ── Scheduler feedback schema (doc 09 § "Scheduler Feedback Schema") ─────────


class SchedulerGroupMetric(BaseModel):
    """Per-group typed execution metric joined from typed evidence.

    Only completed groups (checkpoint projection linked to merge, commit,
    no-dirty, and gate evidence) contribute to throughput / sizing baselines;
    active and incomplete groups appear for status only.
    """

    metric_id: str
    feature_id: str
    group_idx: int
    overlay_id: str | None = None
    state: SchedulerGroupMetricState
    completed: bool
    active: bool = False
    task_ids: list[str]
    task_count: int
    checkpoint_projection_id: int | None = None
    merge_queue_item_id: int | None = None
    task_attempt_ids: list[int] = Field(default_factory=list)
    failure_ids: list[int] = Field(default_factory=list)
    gate_evidence_ids: list[int] = Field(default_factory=list)
    compatibility_projection_ids: list[int] = Field(default_factory=list)
    started_at: datetime | None = None
    checkpointed_at: datetime | None = None
    checkpoint_duration_h: float | None = None
    implementation_duration_h: float | None = None
    verification_duration_h: float | None = None
    repair_duration_h: float | None = None
    merge_queue_wait_h: float | None = None
    merge_apply_duration_h: float | None = None
    commit_duration_h: float | None = None
    lane_counts: dict[str, int]
    barrier_counts: dict[str, int]
    repo_count: int
    write_set_count: int
    unknown_write_count: int
    max_dependency_depth: int
    max_commit_risk: int
    max_verification_cost: int
    verify_count: int
    expanded_verify_count: int
    product_repair_cycles: int
    workflow_repair_cycles: int
    commit_failures: int
    merge_conflicts: int
    queue_retries: int
    runtime_failures: int
    workspace_failures: int
    stale_projection_repairs: int
    verify_cost_units: int
    tasks_per_hour: float | None
    hours_per_task: float | None
    product_repair_cycles_per_task: float | None
    workflow_repair_cycles_per_task: float | None
    commit_failures_per_task: float | None
    merge_conflicts_per_task: float | None
    verify_cost_per_task: float | None
    tail_risks: list[str]
    data_quality_flags: list[str] = Field(default_factory=list)
    evidence_ids: list[int]


class SchedulerFeedback(BaseModel):
    """A lane/barrier sizing window + recommendation (doc 09 § "Scheduler...").

    Advisory evidence only: it may affect future *recommendation* artifacts
    but the overlay validator remains the sole gate for executable regroups.
    """

    schema_version: Literal[1] = 1
    feedback_id: str
    feature_id: str
    generated_at: datetime
    window_start_group: int
    window_end_group: int
    overlay_id: str | None = None
    lane: str
    barrier: str
    completed_groups: list[int]
    sample_count: int
    tasks_per_hour: float | None
    hours_per_task_p50: float | None
    hours_per_task_p75: float | None
    product_repair_cycles_per_task: float | None
    workflow_repair_cycles_per_task: float | None
    commit_failures_per_task: float | None
    merge_conflicts_per_task: float | None
    verify_cost_per_task: float | None
    queue_wait_p75_h: float | None
    data_quality: SchedulerFeedbackDataQuality
    recommended_cap: int
    current_cap: int
    confidence: SchedulerFeedbackConfidence
    reasons: list[str]
    metric_ids: list[str]
    evidence_ids: list[int]


# ── Deterministic identifier derivations (doc 09 § "Proposed Interfaces") ────
#
# doc 09:
#   overlay_id  = sha256(feature_id, source_dag_key, base_dag_artifact_id,
#                        base_dag_sha256, group_idx_offset,
#                        canonical derived order)[:24]
#   overlay_slug = g{group_idx_offset}-g{last_original_group} when the original
#                  suffix has a bounded end, otherwise g{group_idx_offset}-tail
#   metric_id   = sha256(feature_id, group_idx, overlay_id or "root",
#                        checkpoint_projection_id, task_ids, evidence_ids)[:24]

_OVERLAY_ID_LEN = 24
_METRIC_ID_LEN = 24


def derive_overlay_id(
    *,
    feature_id: str,
    source_dag_key: str,
    base_dag_artifact_id: int,
    base_dag_sha256: str,
    group_idx_offset: int,
    derived_execution_order: list[list[str]],
) -> str:
    """Deterministic 24-hex ``overlay_id`` (doc 09 § "Proposed Interfaces").

    Hashed over the identity tuple ``(feature_id, source_dag_key,
    base_dag_artifact_id, base_dag_sha256, group_idx_offset, canonical derived
    order)``. The "canonical derived order" is the ``derived_execution_order``
    waves with each wave's task ids sorted, so two overlays that schedule the
    same task multiset into the same waves derive the same id regardless of
    the incidental within-wave list ordering.
    """

    canonical_order = [sorted(wave) for wave in derived_execution_order]
    return stable_digest(
        {
            "feature_id": feature_id,
            "source_dag_key": source_dag_key,
            "base_dag_artifact_id": base_dag_artifact_id,
            "base_dag_sha256": base_dag_sha256,
            "group_idx_offset": group_idx_offset,
            "derived_execution_order": canonical_order,
        }
    )[:_OVERLAY_ID_LEN]


def derive_overlay_slug(
    *,
    group_idx_offset: int,
    last_original_group: int | None,
) -> str:
    """Deterministic ``overlay_slug`` (doc 09 § "Proposed Interfaces").

    ``g{group_idx_offset}-g{last_original_group}`` when the original suffix
    has a bounded end; ``g{group_idx_offset}-tail`` otherwise. The legacy
    ``g45-g73`` spelling is the natural output of this formula for the
    ``group_idx_offset == 45`` / ``last_original_group == 73`` suffix, so it
    stays a compatibility projection for that exact suffix without a special
    case here.
    """

    if last_original_group is None:
        return f"g{group_idx_offset}-tail"
    return f"g{group_idx_offset}-g{last_original_group}"


def derive_metric_id(
    *,
    feature_id: str,
    group_idx: int,
    overlay_id: str | None,
    checkpoint_projection_id: int | None,
    task_ids: list[str],
    evidence_ids: list[int],
) -> str:
    """Deterministic 24-hex ``metric_id`` (doc 09 § "Scheduler Feedback Schema").

    Hashed over ``(feature_id, group_idx, overlay_id or "root",
    checkpoint_projection_id, task_ids, evidence_ids)``. ``task_ids`` and
    ``evidence_ids`` are sorted so the id is invariant to collection ordering.
    """

    return stable_digest(
        {
            "feature_id": feature_id,
            "group_idx": group_idx,
            "overlay_id": overlay_id or "root",
            "checkpoint_projection_id": checkpoint_projection_id,
            "task_ids": sorted(task_ids),
            "evidence_ids": sorted(evidence_ids),
        }
    )[:_METRIC_ID_LEN]


# ── Slice 11k: pure DerivedDAGArtifact-validator cluster ────────────────────
#
# Per docs/execution-control-plane/11-refactor-map.md § "Boundary-level API
# contracts" row for `execution/regroup_overlay.py`, the pure
# ``DerivedDAGArtifact``-validator cluster moves from
# ``workflows/develop/phases/implementation.py:23920-24461`` to this module
# byte-for-byte. Each helper depends only on stdlib + the ``models.outputs``
# types (``DerivedDAGArtifact``, ``ImplementationDAG``, ``ImplementationTask``)
# + the pre-existing Slice-11h ``execution/repair.py``
# ``_is_derived_dag_artifact_key`` predicate (already imported above) +
# a function-body lazy ``from ..dag_regroup import _barrier_for_task,
# semantic_lane_for_task`` (already module-level at
# ``workflows/develop/dag_regroup.py:181, :204``; the relative import points
# "across" within the same ``workflows/develop/`` package and survives the
# move byte-for-byte).


def _validate_derived_dag_artifact_update(
    artifact_key: str,
    content: str,
    *,
    base_dag: ImplementationDAG | None = None,
    base_dag_artifact_id: int | None = None,
    base_dag_sha256: str | None = None,
    boundary_checkpoint_exists: bool = False,
    require_regroup_context: bool = False,
) -> tuple[DerivedDAGArtifact | None, str, list[dict[str, Any]]]:
    if not _is_derived_dag_artifact_key(artifact_key):
        return None, "not_derived_dag_artifact", []
    try:
        parsed = DerivedDAGArtifact.model_validate_json(content)
    except Exception as exc:
        return None, "invalid_derived_dag_artifact_json", [{
            "error": str(exc),
        }]
    if parsed.artifact_key != artifact_key:
        return None, "derived_dag_artifact_key_mismatch", [{
            "expected_artifact_key": artifact_key,
            "actual_artifact_key": parsed.artifact_key,
        }]
    if parsed.source_dag_key != "dag" and not parsed.source_dag_key.startswith("dag:"):
        return None, "derived_dag_missing_active_source", [{
            "source_dag_key": parsed.source_dag_key,
        }]
    task_ids = [task.id for task in parsed.dag.tasks]
    duplicate_task_ids = sorted({
        task_id for task_id in task_ids if task_ids.count(task_id) > 1
    })
    if duplicate_task_ids:
        return None, "derived_dag_duplicate_task_ids", [{
            "task_ids": duplicate_task_ids,
        }]
    known_task_ids = set(task_ids)
    missing_from_execution = sorted(
        known_task_ids
        - {
            task_id
            for group in parsed.dag.execution_order
            for task_id in group
        }
    )
    execution_task_ids = [
        task_id
        for group in parsed.dag.execution_order
        for task_id in group
    ]
    duplicate_execution_task_ids = sorted({
        task_id for task_id in execution_task_ids if execution_task_ids.count(task_id) > 1
    })
    unknown_in_execution = sorted({
        task_id
        for group in parsed.dag.execution_order
        for task_id in group
        if task_id not in known_task_ids
    })
    if missing_from_execution or unknown_in_execution or duplicate_execution_task_ids:
        return None, "derived_dag_execution_order_mismatch", [{
            "missing_from_execution_order": missing_from_execution,
            "unknown_in_execution_order": unknown_in_execution,
            "duplicate_in_execution_order": duplicate_execution_task_ids,
        }]
    task_group_idx = {
        task_id: group_idx
        for group_idx, group in enumerate(parsed.dag.execution_order)
        for task_id in group
    }
    dependency_errors: list[dict[str, Any]] = []
    for task in parsed.dag.tasks:
        task_group = task_group_idx.get(task.id)
        for dependency_id in task.dependencies:
            dependency_group = task_group_idx.get(dependency_id)
            if dependency_group is None:
                dependency_errors.append({
                    "task_id": task.id,
                    "dependency_id": dependency_id,
                    "reason": "unknown_dependency",
                })
            elif task_group is not None and dependency_group >= task_group:
                dependency_errors.append({
                    "task_id": task.id,
                    "dependency_id": dependency_id,
                    "task_group": task_group,
                    "dependency_group": dependency_group,
                    "reason": (
                        "dependency_in_same_execution_group"
                        if dependency_group == task_group
                        else "dependency_after_task"
                    ),
                })
    if dependency_errors:
        return None, "derived_dag_dependency_order_invalid", dependency_errors[:20]
    write_conflicts = _derived_dag_write_set_conflicts(parsed)
    if write_conflicts:
        return None, "derived_dag_write_set_conflict", write_conflicts[:20]
    if parsed.artifact_key.startswith("dag-regroup:"):
        if parsed.checkpointed_group is None:
            return None, "dag_regroup_missing_checkpointed_group", []
        if parsed.group_idx_offset is None:
            return None, "dag_regroup_missing_group_idx_offset", []
        if parsed.group_idx_offset != parsed.checkpointed_group + 1:
            return None, "dag_regroup_offset_mismatch", [{
                "checkpointed_group": parsed.checkpointed_group,
                "group_idx_offset": parsed.group_idx_offset,
            }]
        if not parsed.original_to_new_group_mapping:
            return None, "dag_regroup_missing_original_mapping", []
        if not parsed.rollback_plan:
            return None, "dag_regroup_missing_rollback_plan", []
        if not parsed.activation_contract:
            return None, "dag_regroup_missing_activation_contract", []
        activation_text = "\n".join(str(item) for item in parsed.activation_contract).lower()
        forbidden_checkpoint = f"dag-group:{parsed.group_idx_offset}"
        if forbidden_checkpoint not in activation_text:
            return None, "dag_regroup_activation_missing_boundary_checkpoint", [{
                "required_boundary": f"no {forbidden_checkpoint} exists",
            }]
        if require_regroup_context and base_dag is None:
            return None, "dag_regroup_context_required", []
        if base_dag is not None:
            context_reason, context_details = _validate_regroup_against_base_dag(
                parsed,
                base_dag=base_dag,
                base_dag_artifact_id=base_dag_artifact_id,
                base_dag_sha256=base_dag_sha256,
                boundary_checkpoint_exists=boundary_checkpoint_exists,
            )
            if context_reason:
                return None, context_reason, context_details
    return parsed, "", [{
        "task_count": len(parsed.dag.tasks),
        "group_count": len(parsed.dag.execution_order),
        "source_dag_key": parsed.source_dag_key,
        "checkpointed_group": parsed.checkpointed_group,
        "group_idx_offset": parsed.group_idx_offset,
        "activation_plan_count": len(parsed.activation_plan),
    }]


def _validate_regroup_against_base_dag(
    parsed: DerivedDAGArtifact,
    *,
    base_dag: ImplementationDAG,
    base_dag_artifact_id: int | None,
    base_dag_sha256: str | None,
    boundary_checkpoint_exists: bool,
) -> tuple[str, list[dict[str, Any]]]:
    group_idx_offset = parsed.group_idx_offset
    if group_idx_offset is None:
        return "dag_regroup_missing_group_idx_offset", []
    if group_idx_offset < 0 or group_idx_offset > len(base_dag.execution_order):
        return "dag_regroup_offset_out_of_range", [{
            "group_idx_offset": group_idx_offset,
            "base_group_count": len(base_dag.execution_order),
        }]
    if boundary_checkpoint_exists:
        return "dag_regroup_boundary_checkpoint_exists", [{
            "checkpoint_key": f"dag-group:{group_idx_offset}",
        }]
    if (
        parsed.base_dag_artifact_id is not None
        and base_dag_artifact_id is not None
        and parsed.base_dag_artifact_id != base_dag_artifact_id
    ):
        return "dag_regroup_base_dag_artifact_mismatch", [{
            "expected_base_dag_artifact_id": base_dag_artifact_id,
            "actual_base_dag_artifact_id": parsed.base_dag_artifact_id,
        }]
    if (
        parsed.base_dag_sha256
        and base_dag_sha256
        and parsed.base_dag_sha256 != base_dag_sha256
    ):
        return "dag_regroup_base_dag_hash_mismatch", [{
            "expected_base_dag_sha256": base_dag_sha256,
            "actual_base_dag_sha256": parsed.base_dag_sha256,
        }]

    original_order = [list(group) for group in base_dag.execution_order[group_idx_offset:]]
    if parsed.original_execution_order != original_order:
        return "dag_regroup_original_execution_order_mismatch", [{
            "expected_group_count": len(original_order),
            "actual_group_count": len(parsed.original_execution_order),
        }]

    original_task_ids = [task_id for group in original_order for task_id in group]
    original_task_set = set(original_task_ids)
    derived_task_ids = [task.id for task in parsed.dag.tasks]
    derived_task_set = set(derived_task_ids)
    missing = sorted(original_task_set - derived_task_set)
    extra = sorted(derived_task_set - original_task_set)
    duplicate_original = sorted({
        task_id for task_id in original_task_ids if original_task_ids.count(task_id) > 1
    })
    duplicate_derived = sorted({
        task_id for task_id in derived_task_ids if derived_task_ids.count(task_id) > 1
    })
    if missing or extra or duplicate_original or duplicate_derived:
        return "dag_regroup_task_preservation_mismatch", [{
            "missing_task_ids": missing[:20],
            "extra_task_ids": extra[:20],
            "duplicate_original_task_ids": duplicate_original[:20],
            "duplicate_derived_task_ids": duplicate_derived[:20],
        }]

    base_tasks_by_id = {task.id: task for task in base_dag.tasks}
    derived_tasks_by_id = {task.id: task for task in parsed.dag.tasks}
    missing_base_tasks = sorted(
        task_id for task_id in original_task_set if task_id not in base_tasks_by_id
    )
    if missing_base_tasks:
        return "dag_regroup_base_task_missing", [{
            "missing_task_ids": missing_base_tasks[:20],
        }]
    task_definition_mismatches: list[dict[str, Any]] = []
    dependency_mismatches: list[dict[str, Any]] = []
    for task_id in sorted(original_task_set):
        base_task = base_tasks_by_id[task_id]
        derived_task = derived_tasks_by_id[task_id]
        base_definition = _regroup_task_definition_for_compare(
            base_task,
            remaining_task_ids=original_task_set,
        )
        derived_definition = _regroup_task_definition_for_compare(
            derived_task,
            remaining_task_ids=original_task_set,
        )
        if base_definition != derived_definition:
            changed_fields = sorted(
                field_name
                for field_name in set(base_definition) | set(derived_definition)
                if base_definition.get(field_name) != derived_definition.get(field_name)
            )
            task_definition_mismatches.append({
                "task_id": task_id,
                "changed_fields": changed_fields[:20],
            })
        base_remaining_dependencies = {
            str(dependency_id)
            for dependency_id in base_task.dependencies
            if str(dependency_id) in original_task_set
        }
        derived_dependencies = {str(dependency_id) for dependency_id in derived_task.dependencies}
        missing_dependencies = sorted(base_remaining_dependencies - derived_dependencies)
        extra_dependencies = sorted(derived_dependencies - base_remaining_dependencies)
        if missing_dependencies or extra_dependencies:
            dependency_mismatches.append({
                "task_id": task_id,
                "missing_dependencies": missing_dependencies,
                "extra_dependencies": extra_dependencies,
            })
    if task_definition_mismatches:
        return "dag_regroup_task_definition_mismatch", task_definition_mismatches[:20]
    if dependency_mismatches:
        return "dag_regroup_dependency_preservation_mismatch", dependency_mismatches[:20]

    expected_mapping_indices = set(range(group_idx_offset, len(base_dag.execution_order)))
    actual_mapping_indices: set[int] = set()
    mapping_key_errors: list[dict[str, Any]] = []
    for original_group in parsed.original_to_new_group_mapping:
        try:
            original_group_idx = int(original_group)
        except (TypeError, ValueError):
            mapping_key_errors.append({
                "original_group": original_group,
                "reason": "non_integer_original_group",
            })
            continue
        actual_mapping_indices.add(original_group_idx)
    if mapping_key_errors:
        return "dag_regroup_original_mapping_invalid", mapping_key_errors[:20]
    missing_mapping = sorted(
        str(group_idx) for group_idx in expected_mapping_indices - actual_mapping_indices
    )
    extra_mapping = sorted(
        str(group_idx) for group_idx in actual_mapping_indices - expected_mapping_indices
    )
    if missing_mapping or extra_mapping:
        return "dag_regroup_original_mapping_mismatch", [{
            "missing_original_groups": missing_mapping,
            "extra_original_groups": extra_mapping,
        }]

    derived_group_min = group_idx_offset
    derived_group_max = group_idx_offset + max(0, len(parsed.dag.execution_order) - 1)
    invalid_targets: list[dict[str, Any]] = []
    for original_group, new_groups in parsed.original_to_new_group_mapping.items():
        if not new_groups:
            invalid_targets.append({
                "original_group": original_group,
                "reason": "empty_new_group_mapping",
            })
            continue
        for new_group in new_groups:
            try:
                new_group_idx = int(new_group)
            except (TypeError, ValueError):
                invalid_targets.append({
                    "original_group": original_group,
                    "new_group": new_group,
                    "reason": "non_integer_new_group",
                })
                continue
            if new_group_idx < derived_group_min or new_group_idx > derived_group_max:
                invalid_targets.append({
                    "original_group": original_group,
                    "new_group": new_group_idx,
                    "valid_min": derived_group_min,
                    "valid_max": derived_group_max,
                    "reason": "new_group_out_of_range",
                })
    if invalid_targets:
        return "dag_regroup_original_mapping_invalid", invalid_targets[:20]

    reverse_mapping: dict[int, set[int]] = {}
    for original_group, new_groups in parsed.original_to_new_group_mapping.items():
        original_group_idx = int(original_group)
        for new_group in new_groups:
            reverse_mapping.setdefault(int(new_group), set()).add(original_group_idx)

    original_group_by_task = {
        task_id: group_idx
        for group_idx, group in enumerate(base_dag.execution_order)
        for task_id in group
    }
    task_write_sets = _derived_dag_task_write_sets(
        parsed,
        task_definitions_by_id={
            task_id: base_tasks_by_id[task_id]
            for task_id in original_task_set
        },
    )
    authoritative_write_conflicts = _derived_dag_write_set_conflicts(
        parsed,
        task_write_sets=task_write_sets,
    )
    if authoritative_write_conflicts:
        return "derived_dag_write_set_conflict", authoritative_write_conflicts[:20]
    missing_write_set_coverage: list[dict[str, Any]] = []
    for derived_group_idx, group in enumerate(parsed.dag.execution_order):
        source_group_indices = {
            original_group_by_task.get(task_id)
            for task_id in group
            if original_group_by_task.get(task_id) is not None
        }
        if len(source_group_indices) <= 1:
            continue
        missing_task_ids = sorted(
            task_id for task_id in group if not task_write_sets.get(task_id)
        )
        if missing_task_ids:
            missing_write_set_coverage.append({
                "new_group": group_idx_offset + derived_group_idx,
                "source_original_groups": sorted(source_group_indices),
                "missing_task_ids": missing_task_ids[:20],
            })
    if missing_write_set_coverage:
        return "dag_regroup_missing_write_set_coverage", missing_write_set_coverage[:20]

    barrier_by_task = _regroup_hard_barrier_by_task(
        parsed,
        task_definitions_by_id={
            task_id: base_tasks_by_id[task_id]
            for task_id in original_task_set
        },
    )
    barrier_violations: list[dict[str, Any]] = []
    for derived_group_idx, group in enumerate(parsed.dag.execution_order):
        hard_barriers = sorted({
            barrier_by_task[task_id]
            for task_id in group
            if task_id in barrier_by_task
        })
        if len(hard_barriers) > 1:
            barrier_violations.append({
                "new_group": group_idx_offset + derived_group_idx,
                "barriers": hard_barriers,
                "task_ids": list(group),
            })
    if barrier_violations:
        return "dag_regroup_barrier_violation", barrier_violations[:20]

    mapping_mismatches: list[dict[str, Any]] = []
    for original_group, new_groups in parsed.original_to_new_group_mapping.items():
        original_group_idx = int(original_group)
        original_tasks = set(base_dag.execution_order[original_group_idx])
        mapped_tasks: set[str] = set()
        for new_group in new_groups:
            new_group_idx = int(new_group)
            derived_group_idx = new_group_idx - group_idx_offset
            if 0 <= derived_group_idx < len(parsed.dag.execution_order):
                mapped_tasks.update(parsed.dag.execution_order[derived_group_idx])
        missing_mapped_tasks = sorted(original_tasks - mapped_tasks)
        if missing_mapped_tasks:
            mapping_mismatches.append({
                "original_group": original_group_idx,
                "new_groups": [int(group_idx) for group_idx in new_groups],
                "missing_task_ids": missing_mapped_tasks[:20],
                "extra_task_ids": [],
            })
    for derived_group_idx, group in enumerate(parsed.dag.execution_order):
        absolute_group_idx = group_idx_offset + derived_group_idx
        source_group_indices = reverse_mapping.get(absolute_group_idx, set())
        allowed_tasks = {
            task_id
            for original_group_idx in source_group_indices
            for task_id in base_dag.execution_order[original_group_idx]
        }
        extra_mapped_tasks = sorted(set(group) - allowed_tasks)
        if extra_mapped_tasks:
            mapping_mismatches.append({
                "new_group": absolute_group_idx,
                "mapped_original_groups": sorted(source_group_indices),
                "missing_task_ids": [],
                "extra_task_ids": extra_mapped_tasks[:20],
            })
    if mapping_mismatches:
        return "dag_regroup_original_mapping_task_mismatch", mapping_mismatches[:20]

    return "", []


def _regroup_task_definition_for_compare(
    task: ImplementationTask,
    *,
    remaining_task_ids: set[str],
) -> dict[str, Any]:
    del remaining_task_ids
    payload = task.model_dump(mode="json")
    payload.pop("dependencies", None)
    return payload


def _regroup_hard_barrier_by_task(
    parsed: DerivedDAGArtifact,
    *,
    task_definitions_by_id: dict[str, ImplementationTask] | None = None,
) -> dict[str, str]:
    barrier_by_task: dict[str, str] = {}
    if task_definitions_by_id:
        from ..dag_regroup import _barrier_for_task, semantic_lane_for_task

        return {
            task_id: _barrier_for_task(task, semantic_lane_for_task(task))
            for task_id, task in task_definitions_by_id.items()
        }
    for idx, barrier in enumerate(parsed.barriers or []):
        if not isinstance(barrier, dict):
            continue
        hard = barrier.get("hard", True)
        if hard is False or str(hard).lower() in {"false", "0", "no", "off"}:
            continue
        barrier_id = str(
            barrier.get("id")
            or barrier.get("barrier_id")
            or barrier.get("name")
            or barrier.get("kind")
            or f"barrier-{idx}"
        )
        for task_id in barrier.get("task_ids") or []:
            task_key = str(task_id)
            if task_key:
                barrier_by_task.setdefault(task_key, barrier_id)
    speed_tasks = (parsed.speed_index or {}).get("tasks")
    if isinstance(speed_tasks, dict):
        for task_id, metadata in speed_tasks.items():
            if not isinstance(metadata, dict):
                continue
            barrier_id = str(metadata.get("barrier") or "").strip()
            if barrier_id:
                barrier_by_task.setdefault(str(task_id), barrier_id)
    return barrier_by_task


def _derived_dag_write_set_conflicts(
    parsed: DerivedDAGArtifact,
    *,
    task_write_sets: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    write_sets = task_write_sets if task_write_sets is not None else _derived_dag_task_write_sets(parsed)
    if not write_sets:
        return conflicts
    for group_idx, group in enumerate(parsed.dag.execution_order):
        owners = [task_id for task_id in group if task_id in write_sets]
        for idx, left in enumerate(owners):
            for right in owners[idx + 1:]:
                overlap = sorted(write_sets[left] & write_sets[right])
                if overlap:
                    conflicts.append({
                        "group_idx": group_idx,
                        "left": left,
                        "right": right,
                        "overlap": overlap[:20],
                    })
    return conflicts


def _derived_dag_task_write_sets(
    parsed: DerivedDAGArtifact,
    *,
    task_definitions_by_id: dict[str, ImplementationTask] | None = None,
) -> dict[str, set[str]]:
    task_definitions = (
        list(task_definitions_by_id.values())
        if task_definitions_by_id is not None
        else list(parsed.dag.tasks)
    )
    write_sets: dict[str, set[str]] = {}
    for task in task_definitions:
        paths = _regroup_task_declared_write_paths(task)
        if paths:
            write_sets[str(task.id)] = paths
    for owner, paths in (parsed.write_sets or {}).items():
        owner_key = str(owner)
        write_sets.setdefault(owner_key, set()).update(
            str(path) for path in paths if str(path)
        )
    return write_sets


def _regroup_task_declared_write_paths(task: ImplementationTask) -> set[str]:
    paths: set[str] = set()

    def _add_path(raw_path: str) -> None:
        path = str(raw_path or "").strip()
        if not path:
            return
        paths.add(path)
        repo_path = str(task.repo_path or "").strip().strip("/")
        if repo_path and not path.startswith(f"{repo_path}/"):
            paths.add(f"{repo_path}/{path.lstrip('/')}")

    for path in task.files:
        _add_path(path)
    for scope in task.file_scope:
        action = str(scope.action or "").strip().lower()
        if action and action != "read_only":
            _add_path(scope.path)
    return paths
