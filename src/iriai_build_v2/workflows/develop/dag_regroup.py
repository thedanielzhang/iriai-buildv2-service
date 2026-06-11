"""DAG regroup CLI / review facade.

This module is the **CLI / review facade** for the regroup feature, per
``docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md``
§ "Refactoring Steps" item 1 — *"Keep ``dag_regroup.py`` as a CLI/review facade
that delegates to the new module."* The typed control-plane substance — the
``RegroupOverlay`` schema + the deterministic derivations, the 13-step
``validate_overlay``, ``activate_overlay`` / ``rollback_overlay``, the typed
``RegroupOverlayResolver``, and the typed scheduler-feedback layer — was
extracted across Slices 09a–09d into the sibling package
``workflows/develop/execution/`` (``regroup_overlay*.py`` + ``scheduler_*.py``).

**Slice 09e-1 — facade conversion scope (this change).** doc 09 § "Refactoring
Steps" item 1's facade conversion is multi-part: a fully delegating facade also
requires items 7/8 — rewiring ``command_activate`` / ``command_rollback`` onto
the *async, typed* ``activate_overlay`` / ``rollback_overlay`` over the
``execution_regroup_overlays`` tables, which in turn needs a
``DerivedDAGArtifact`` → typed ``RegroupOverlay`` conversion layer. That rewire
necessarily changes CLI behavior (it writes typed overlay rows, not only
``artifacts``) and is therefore split out as **Slice 09e-2**. Slice 09e-1 does
the surgical, behavior-preserving first chunk:

- it converts the legacy ``g45-g73`` artifact-key constants below from opaque
  string literals into a delegation to the typed
  :func:`regroup_overlay.derive_overlay_slug` — the facade now *derives* its
  compatibility keys through the typed module instead of hard-coding them, and
  the formula yields the byte-identical ``g45-g73`` suffix for the
  ``group_idx_offset == 45`` / ``last_original_group == 73`` case;
- the CLI commands (``command_draft`` / ``command_validate`` /
  ``command_activate`` / ``command_rollback`` / ``command_status`` /
  ``command_analyze_sizing`` / ``command_recommend_sizing``), the legacy
  builders (``build_staged_regroup`` / ``build_speed_index`` /
  ``validate_candidate``), and the legacy review collectors
  (``collect_sizing_metrics`` / ``identify_process_improvements`` /
  ``recommend_adaptive_sizing``) keep their EXACT existing behavior — the
  27-test ``tests/workflows/test_dag_regroup.py`` compatibility suite is the
  behavior-preservation safety net and stays green.

The legacy ``dag-regroup:*`` / ``review:dag-sizing:*`` artifacts this facade
writes remain synchronous compatibility projections of typed overlay state; the
root ``dag`` is never overwritten.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import subprocess
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import asyncpg

from ...config import DATABASE_URL
from ...db import create_pool
from ...models.outputs import DerivedDAGArtifact, ImplementationDAG, ImplementationTask
from .execution.regroup_overlay import (
    OverlayBarrier,
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupOverlay,
    RegroupRollbackPlan,
    derive_overlay_id,
    derive_overlay_slug,
)

DEFAULT_FROM_GROUP = 45
DEFAULT_TO_GROUP = 73

# Slice 09e-1 facade conversion (doc 09 § "Refactoring Steps" item 1): the
# legacy ``g45-g73`` compatibility suffix is now *derived* through the typed
# overlay module rather than hard-coded. ``derive_overlay_slug`` is the single
# source of truth for the slug spelling; for the ``group_idx_offset == 45`` /
# ``last_original_group == 73`` suffix it deterministically yields ``g45-g73``,
# so every legacy key string below is byte-identical to its prior literal.
_LEGACY_OVERLAY_SLUG = derive_overlay_slug(
    group_idx_offset=DEFAULT_FROM_GROUP,
    last_original_group=DEFAULT_TO_GROUP,
)

DRAFT_KEY = f"review:dag-regroup-draft:{_LEGACY_OVERLAY_SLUG}"
CANONICAL_KEY = f"dag-regroup:{_LEGACY_OVERLAY_SLUG}"
ACTIVE_KEY = f"dag-regroup-active:{_LEGACY_OVERLAY_SLUG}"
ROLLBACK_KEY = f"dag-regroup-rollback:{_LEGACY_OVERLAY_SLUG}"
OBSERVATION_KEY = f"dag-regroup-observation:{_LEGACY_OVERLAY_SLUG}"
SOURCE_DAG_KEY = "dag"
OUTPUT_ARTIFACT_BUDGET_BYTES = 8 * 1024 * 1024
DEFAULT_METRICS_EVENT_LIMIT = 50_000
DEFAULT_METRICS_ARTIFACT_LIMIT = 50_000
DEFAULT_CHECKPOINT_P75_BUDGET_HOURS = 12.0

LANE_RANKS = {
    "backend.artifacts": 0,
    "backend.bridge/phases": 1,
    "backend.live-edit": 2,
    "backend.checkpoint-resume": 3,
    "planning-ui.document": 4,
    "implementation-ui": 5,
    "review-ui": 6,
    "chat-ui": 7,
    "perf/ci": 8,
    "misc": 9,
}

HARD_BARRIER_KEYWORDS = {
    "backend-foundation",
    "bridge-api-adapter",
    "generated-output",
    "ci-perf",
    "package-mirror",
    "cross-repo",
    "ui-integration-e2e",
}


@dataclass(slots=True)
class DagRecord:
    artifact_id: int
    value: str
    sha256: str


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    reason: str = ""
    details: list[dict[str, Any]] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _require_output_budget(value: str, *, label: str) -> None:
    size = len(value.encode("utf-8"))
    if size > OUTPUT_ARTIFACT_BUDGET_BYTES:
        raise RuntimeError(
            f"{label} is {size} bytes; exceeds {OUTPUT_ARTIFACT_BUDGET_BYTES} byte budget"
        )


def _task_text(task: ImplementationTask) -> str:
    parts = [
        task.id,
        task.name,
        task.description,
        task.repo_path,
        " ".join(task.files),
        " ".join(scope.path for scope in task.file_scope),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _effective_repo_path(task: ImplementationTask) -> str:
    """Return the task's repo_path, treating absolute paths as unset (N-17 tolerance).

    Absolute ``repo_path`` values (e.g. ``/Users/.../repos``) arise when the agent
    writes the full workspace-local path instead of a bare relative name.  They are
    invalid for path-prefixing — a ``Users/...`` top-level directory would poison the
    cross-repo barrier and commit-risk heuristics.  Per the N-17 tolerance pattern:
    treat absolute as unset with a loud warning so the rest of the regroup pipeline
    degrades gracefully rather than producing corrupted write-sets.
    """
    raw = str(task.repo_path or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        warnings.warn(
            f"task {task.id!r}: repo_path {raw!r} is absolute — treating as unset "
            "(N-17: absolute repo_path is invalid for write-set path-prefixing; "
            "fix the DAG task definition to use a bare relative repo name)",
            stacklevel=4,
        )
        return ""
    return raw


def _task_paths(task: ImplementationTask) -> list[str]:
    paths: list[str] = []
    for path in task.files:
        if str(path).strip():
            paths.append(str(path).strip())
    for scope in task.file_scope:
        action = str(scope.action or "").lower()
        if action and action != "read_only" and str(scope.path).strip():
            paths.append(str(scope.path).strip())
    repo_path = _effective_repo_path(task)
    if repo_path and paths:
        return sorted({path if "/" in path else f"{repo_path}/{path}" for path in paths})
    return sorted(set(paths))


def _task_write_paths_for_overlay(task: ImplementationTask) -> set[str]:
    """Write-paths for the overlay ``write_sets``, mirroring ``_task_declared_write_paths``.

    Unlike ``_task_paths`` (which is used for barrier/commit-risk heuristics and
    must NOT add a ``Users/`` top-level variant), this helper mirrors the validator's
    ``_task_declared_write_paths`` dual-add semantics exactly, so the builder's
    ``write_sets`` is always a superset of what the validator expects.

    Per the N-17 tolerance pattern, an absolute ``repo_path`` is treated as unset
    (same as ``_effective_repo_path``), which means for DAGs where repo_path was
    blanked or is absolute, the builder and validator both see zero prefix — no
    mismatch, no step-10 reject.
    """
    paths: set[str] = set()
    repo_path = _effective_repo_path(task)  # "" for absolute (N-17)

    def _add(raw: str) -> None:
        p = str(raw or "").strip()
        if not p:
            return
        paths.add(p)
        if repo_path and not p.startswith(f"{repo_path}/"):
            paths.add(f"{repo_path}/{p.lstrip('/')}")

    for path in task.files:
        _add(path)
    for scope in task.file_scope:
        action = str(scope.action or "").strip().lower()
        if action and action != "read_only":
            _add(scope.path)
    return paths


def semantic_lane_for_task(task: ImplementationTask) -> str:
    text = _task_text(task)
    if "artifact" in text or "artifacts" in text:
        return "backend.artifacts"
    if "bridge" in text or "phase" in text or "adapter" in text:
        return "backend.bridge/phases"
    if "live edit" in text or "live-edit" in text or "stream" in text:
        return "backend.live-edit"
    if "checkpoint" in text or "resume" in text or "quiesce" in text:
        return "backend.checkpoint-resume"
    if "planning" in text or "document" in text or "doc" in text:
        return "planning-ui.document"
    if "implementation" in text and ("ui" in text or "frontend" in text):
        return "implementation-ui"
    if "review" in text and ("ui" in text or "frontend" in text):
        return "review-ui"
    if "chat" in text or "conversation" in text:
        return "chat-ui"
    if any(token in text for token in ("perf", "performance", "ci", "benchmark", "workflow test")):
        return "perf/ci"
    return "misc"


def _barrier_for_task(task: ImplementationTask, lane: str) -> str:
    text = _task_text(task)
    paths = " ".join(_task_paths(task)).lower()
    combined = f"{text} {paths}"
    if any(token in combined for token in ("generated", "codegen", "schema")):
        return "generated-output"
    if any(token in combined for token in ("package.json", "pyproject", "lockfile", "package mirror", "backend mirror")):
        return "package-mirror"
    if any(token in combined for token in ("e2e", "playwright", "integration")) and "ui" in lane:
        return "ui-integration-e2e"
    if any(token in combined for token in ("ci", ".github", "benchmark", "perf", "performance")):
        return "ci-perf"
    if len({path.split("/", 1)[0] for path in _task_paths(task) if "/" in path}) > 1:
        return "cross-repo"
    if lane == "backend.bridge/phases":
        return "bridge-api-adapter"
    if lane.startswith("backend."):
        return "backend-foundation"
    return lane


def _verification_cost(task: ImplementationTask) -> int:
    text = _task_text(task)
    cost = 1
    if any(token in text for token in ("e2e", "playwright", "browser", "integration")):
        cost += 4
    if any(token in text for token in ("perf", "ci", "benchmark", "migration")):
        cost += 3
    if task.verification_gates:
        cost += min(4, len(task.verification_gates))
    return cost


def _commit_risk(task: ImplementationTask) -> int:
    text = _task_text(task)
    paths = _task_paths(task)
    risk = 0
    if len(paths) > 5:
        risk += 2
    if any(token in text for token in ("generated", "package", "lockfile", "migration", "rename", "move")):
        risk += 3
    if any(path.endswith((".json", ".lock", ".toml", ".yaml", ".yml")) for path in paths):
        risk += 1
    if len({path.split("/", 1)[0] for path in paths if "/" in path}) > 1:
        risk += 3
    return risk


def _implementation_cost(task: ImplementationTask) -> int:
    text_size = len(task.description or "") + len(task.name or "")
    return max(1, min(10, (text_size // 280) + len(_task_paths(task)) + len(task.acceptance_criteria)))


def _remaining_task_lookup(
    dag: ImplementationDAG,
    from_group: int,
    to_group: int,
) -> tuple[list[list[str]], dict[str, ImplementationTask], set[str]]:
    original_order = [list(group) for group in dag.execution_order[from_group : to_group + 1]]
    remaining_ids = {task_id for group in original_order for task_id in group}
    tasks_by_id = {task.id: task for task in dag.tasks if task.id in remaining_ids}
    missing = sorted(remaining_ids - set(tasks_by_id))
    if missing:
        raise ValueError(f"remaining DAG tasks missing definitions: {missing[:10]}")
    return original_order, tasks_by_id, remaining_ids


def _critical_path_depths(
    tasks_by_id: dict[str, ImplementationTask],
    remaining_ids: set[str],
) -> dict[str, int]:
    reverse_edges: dict[str, list[str]] = {task_id: [] for task_id in remaining_ids}
    for task in tasks_by_id.values():
        for dep in task.dependencies:
            if dep in remaining_ids:
                reverse_edges.setdefault(dep, []).append(task.id)

    memo: dict[str, int] = {}

    def depth(task_id: str) -> int:
        if task_id in memo:
            return memo[task_id]
        children = reverse_edges.get(task_id, [])
        memo[task_id] = 1 + max((depth(child) for child in children), default=0)
        return memo[task_id]

    return {task_id: depth(task_id) for task_id in remaining_ids}


def build_speed_index(
    dag: ImplementationDAG,
    *,
    from_group: int = DEFAULT_FROM_GROUP,
    to_group: int = DEFAULT_TO_GROUP,
) -> dict[str, Any]:
    original_order, tasks_by_id, remaining_ids = _remaining_task_lookup(dag, from_group, to_group)
    depths = _critical_path_depths(tasks_by_id, remaining_ids)
    original_group_by_task = {
        task_id: from_group + group_idx
        for group_idx, group in enumerate(original_order)
        for task_id in group
    }
    task_metadata: dict[str, dict[str, Any]] = {}
    lane_counts: dict[str, int] = {}
    for task_id in sorted(remaining_ids):
        task = tasks_by_id[task_id]
        lane = semantic_lane_for_task(task)
        barrier = _barrier_for_task(task, lane)
        write_set = _task_paths(task)
        unknown_write = not bool(write_set)
        metadata = {
            "semantic_lane": lane,
            "barrier": barrier,
            "critical_path_depth": depths[task_id],
            "lane_rank": LANE_RANKS.get(lane, 99),
            "estimated_implementation_cost": _implementation_cost(task),
            "verification_cost": _verification_cost(task),
            "commit_risk": _commit_risk(task),
            "unknown_write_penalty": 100 if unknown_write else 0,
            "original_group": original_group_by_task[task_id],
            "sort_key": [
                -depths[task_id],
                LANE_RANKS.get(lane, 99),
                _verification_cost(task),
                _commit_risk(task),
                _implementation_cost(task),
                100 if unknown_write else 0,
                task_id,
            ],
        }
        task_metadata[task_id] = metadata
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    return {
        "from_group": from_group,
        "to_group": to_group,
        "task_count": len(remaining_ids),
        "lane_counts": dict(sorted(lane_counts.items())),
        "tasks": task_metadata,
    }


def _wave_cap_for_barrier(barrier: str, task_ids: Iterable[str], speed_index: dict[str, Any]) -> int:
    if barrier in {"backend-foundation", "bridge-api-adapter", "generated-output", "ci-perf", "package-mirror", "cross-repo"}:
        return 4
    tasks = speed_index.get("tasks", {})
    if all(
        isinstance(tasks.get(task_id), dict)
        and tasks[task_id].get("verification_cost", 0) <= 2
        and tasks[task_id].get("commit_risk", 0) <= 1
        for task_id in task_ids
    ):
        return 14
    return 10


def _dependencies_within_remaining(task: ImplementationTask, remaining_ids: set[str]) -> list[str]:
    return [dep for dep in task.dependencies if dep in remaining_ids]


def _write_sets_overlap(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    return bool(left & right)


def _can_add_to_wave(
    task_id: str,
    wave: list[str],
    *,
    speed_index: dict[str, Any],
    write_sets: dict[str, list[str]],
    original_group_by_task: dict[str, int],
) -> bool:
    if not wave:
        return True
    tasks_meta = speed_index.get("tasks", {})
    barrier = tasks_meta.get(task_id, {}).get("barrier")
    existing_barriers = {
        tasks_meta.get(existing_id, {}).get("barrier")
        for existing_id in wave
    }
    if barrier not in existing_barriers:
        return False
    candidate_write_set = set(write_sets.get(task_id, []))
    for existing_id in wave:
        if _write_sets_overlap(candidate_write_set, set(write_sets.get(existing_id, []))):
            return False
    source_groups = {original_group_by_task[existing_id] for existing_id in wave}
    source_groups.add(original_group_by_task[task_id])
    if len(source_groups) > 1:
        proposed = [*wave, task_id]
        if any(not write_sets.get(proposed_task_id) for proposed_task_id in proposed):
            return False
    return True


def build_staged_regroup(
    base_dag: ImplementationDAG,
    *,
    base_dag_artifact_id: int | None,
    base_dag_sha256: str,
    from_group: int = DEFAULT_FROM_GROUP,
    to_group: int = DEFAULT_TO_GROUP,
    artifact_key: str = CANONICAL_KEY,
) -> DerivedDAGArtifact:
    if from_group < 0 or to_group < from_group or to_group >= len(base_dag.execution_order):
        raise ValueError("invalid regroup group range")
    if to_group != len(base_dag.execution_order) - 1:
        raise ValueError(
            "staged regroup must cover the full remaining DAG suffix; "
            f"to_group={to_group} but latest group is {len(base_dag.execution_order) - 1}"
        )
    original_order, tasks_by_id, remaining_ids = _remaining_task_lookup(base_dag, from_group, to_group)
    speed_index = build_speed_index(base_dag, from_group=from_group, to_group=to_group)
    original_group_by_task = {
        task_id: from_group + group_idx
        for group_idx, group in enumerate(original_order)
        for task_id in group
    }
    # WB-1: use dual-add semantics (mirrors validator's _task_declared_write_paths)
    # so write_sets is always a superset of what validate_overlay step 10 expects.
    # Absolute repo_path is treated as unset per the N-17 tolerance pattern —
    # _task_paths is NOT changed (it drives barrier/commit-risk heuristics).
    write_sets = {
        task_id: sorted(_task_write_paths_for_overlay(task))
        for task_id, task in tasks_by_id.items()
        if _task_write_paths_for_overlay(task)
    }
    remaining_dependencies = {
        task_id: set(_dependencies_within_remaining(task, remaining_ids))
        for task_id, task in tasks_by_id.items()
    }
    scheduled: set[str] = set()
    unscheduled = set(remaining_ids)
    waves: list[list[str]] = []
    while unscheduled:
        eligible = [
            task_id
            for task_id in unscheduled
            if remaining_dependencies[task_id].issubset(scheduled)
        ]
        if not eligible:
            raise ValueError("remaining DAG contains a cycle or unsatisfied dependency")
        eligible.sort(key=lambda task_id: speed_index["tasks"][task_id]["sort_key"])
        seed = eligible[0]
        seed_barrier = speed_index["tasks"][seed]["barrier"]
        cap = _wave_cap_for_barrier(seed_barrier, [seed], speed_index)
        wave = [seed]
        for task_id in eligible[1:]:
            if len(wave) >= cap:
                break
            if _can_add_to_wave(
                task_id,
                wave,
                speed_index=speed_index,
                write_sets=write_sets,
                original_group_by_task=original_group_by_task,
            ):
                wave.append(task_id)
                cap = min(
                    cap,
                    _wave_cap_for_barrier(seed_barrier, wave, speed_index),
                )
        wave.sort(key=lambda task_id: speed_index["tasks"][task_id]["sort_key"])
        waves.append(wave)
        scheduled.update(wave)
        unscheduled.difference_update(wave)

    derived_tasks = [
        tasks_by_id[task_id].model_copy(
            deep=True,
            update={"dependencies": _dependencies_within_remaining(tasks_by_id[task_id], remaining_ids)},
        )
        for task_id in sorted(remaining_ids)
    ]
    original_to_new: dict[str, list[int]] = {str(idx): [] for idx in range(from_group, to_group + 1)}
    for derived_idx, wave in enumerate(waves):
        absolute_group = from_group + derived_idx
        for task_id in wave:
            original_to_new[str(original_group_by_task[task_id])].append(absolute_group)
    original_to_new = {
        original_group: sorted(set(new_groups))
        for original_group, new_groups in original_to_new.items()
    }
    barrier_tasks: dict[str, list[str]] = {}
    for task_id, metadata in speed_index["tasks"].items():
        barrier_tasks.setdefault(str(metadata["barrier"]), []).append(task_id)
    barriers = [
        {
            "id": barrier_id,
            "hard": True,
            "task_ids": sorted(task_ids),
            "reason": "speed-indexed semantic regroup barrier",
        }
        for barrier_id, task_ids in sorted(barrier_tasks.items())
    ]
    dag = ImplementationDAG(
        tasks=derived_tasks,
        num_teams=base_dag.num_teams,
        execution_order=waves,
        requirement_coverage=base_dag.requirement_coverage,
        complete=base_dag.complete,
    )
    return DerivedDAGArtifact(
        artifact_key=artifact_key,
        source_dag_key=SOURCE_DAG_KEY,
        dag=dag,
        base_dag_artifact_id=base_dag_artifact_id,
        base_dag_sha256=base_dag_sha256,
        checkpointed_group=from_group - 1,
        group_idx_offset=from_group,
        original_execution_order=original_order,
        original_to_new_group_mapping=original_to_new,
        barriers=barriers,
        write_sets=write_sets,
        verification_matrix={
            "wave_count": len(waves),
            "original_group_range": [from_group, to_group],
            "semantic_lanes": speed_index["lane_counts"],
        },
        speed_index=speed_index,
        activation_contract=[
            f"dag-group:{from_group - 1} exists before activation",
            f"dag-group:{from_group} does not exist before activation",
            "root dag artifact is not overwritten",
            "all original remaining task IDs appear exactly once",
            "all remaining dependencies are preserved exactly",
            "no same-wave dependencies or write-set conflicts exist",
        ],
        rollback_plan=[
            f"rollback may run only before any group {from_group} task/event/artifact exists",
            "restore original_execution_order from dag-regroup-rollback:g45-g73",
            "clear active marker by writing a rolled_back dag-regroup-active:g45-g73 marker",
            "after group 45 begins, rollback is blocked and a reconcile group is required",
        ],
        derivation_reason="Persistent staged speed-indexed semantic regroup for G45-G73.",
        activation_plan=[
            "acquire feature dag-regroup advisory lock",
            "reload latest root dag and reject stale base id/hash",
            f"require dag-group:{from_group - 1} present and dag-group:{from_group} absent",
            "check DB/worktree safety budgets",
            "write canonical, rollback, and active marker artifacts",
        ],
        validation_notes=[
            "speed order is advisory only; dependencies, barriers, write sets, and activation safety gates dominate",
        ],
        complete=True,
    )


def validate_candidate(
    candidate: DerivedDAGArtifact,
    *,
    base_dag: ImplementationDAG,
    base_dag_artifact_id: int | None,
    base_dag_sha256: str,
    boundary_checkpoint_exists: bool,
) -> ValidationResult:
    from .phases.implementation import _validate_derived_dag_artifact_update

    parsed, reason, details = _validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base_dag,
        base_dag_artifact_id=base_dag_artifact_id,
        base_dag_sha256=base_dag_sha256,
        boundary_checkpoint_exists=boundary_checkpoint_exists,
        require_regroup_context=True,
    )
    return ValidationResult(
        ok=parsed is not None,
        reason=reason,
        details=details,
    )


def _review_key(kind: str, feature_id: str, range_label: str) -> str:
    return f"review:dag-{kind}:{feature_id}:{range_label}"


def _percentile(values: list[float], percentile: float) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(clean) - 1)
    fraction = rank - lower
    return clean[lower] + (clean[upper] - clean[lower]) * fraction


def _mean(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return statistics.mean(clean)


def _round_float(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _group_from_artifact_key(key: str) -> int | None:
    for pattern in (
        r"^dag-group:(\d+)$",
        r":g(\d+)(?::|-|$)",
        r"worktree-registry:g(\d+)$",
        r"dag-path-canonicalization:g(\d+)$",
    ):
        match = re.search(pattern, key)
        if match:
            return int(match.group(1))
    return None


def _group_from_event(row: dict[str, Any]) -> int | None:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if isinstance(metadata, dict):
        group_idx = metadata.get("group_idx")
        if isinstance(group_idx, int):
            return group_idx
        if isinstance(group_idx, str) and group_idx.isdigit():
            return int(group_idx)
    text = f"{row.get('content') or ''} {row.get('source') or ''}"
    match = re.search(r"\bg(?:roup\s*)?(\d+)\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _artifact_category(key: str) -> str:
    prefix_map = {
        "dag-verify:": "verify",
        "dag-repair-expanded-verify:": "expanded_verify",
        "dag-repair-lens:": "repair_lens",
        "dag-verify-rca:": "rca",
        "dag-repair-rca:": "rca",
        "dag-fix:": "fix",
        "dag-commit-failure:": "commit_failure",
        "dag-workspace-acl-normalization:": "acl_norm",
        "dag-workspace-permission-repair:": "permission_repair",
        "dag-task-reconcile:": "task_reconcile",
        "dag-task-spec-reconcile:": "task_spec_reconcile",
        "dag-path-canonicalization:": "path_canon",
        "worktree-registry:": "worktree_registry",
        "dag-worktree-alias": "worktree_alias",
        "dag-authority-gate:": "authority_gate",
        "dag-direct-repair-route:": "direct_repair_route",
        "dag-repair-preflight:": "repair_preflight",
        "dag-artifact-repair:": "artifact_repair",
        "dag-task:": "task_result",
        "dag-group:": "checkpoint",
    }
    for prefix, category in prefix_map.items():
        if key.startswith(prefix):
            return category
    return key.split(":", 1)[0]


def _effective_execution_order(
    base_dag: ImplementationDAG,
    candidate: DerivedDAGArtifact | None,
) -> tuple[list[list[str]], dict[str, ImplementationTask], dict[str, dict[str, Any]], int]:
    tasks_by_id = {task.id: task for task in base_dag.tasks}
    speed_tasks: dict[str, dict[str, Any]] = {}
    offset = len(base_dag.execution_order)
    if candidate is None:
        return [list(group) for group in base_dag.execution_order], tasks_by_id, speed_tasks, offset
    offset = int(candidate.group_idx_offset or DEFAULT_FROM_GROUP)
    for task in candidate.dag.tasks:
        tasks_by_id[task.id] = task
    speed_tasks = {
        str(task_id): dict(metadata)
        for task_id, metadata in (candidate.speed_index or {}).get("tasks", {}).items()
        if isinstance(metadata, dict)
    }
    return (
        [list(group) for group in base_dag.execution_order[:offset]]
        + [list(group) for group in candidate.dag.execution_order],
        tasks_by_id,
        speed_tasks,
        offset,
    )


# ── DerivedDAGArtifact → typed RegroupOverlay conversion layer (Slice 09e-1b) ─
#
# doc 09 § "Refactoring Steps" item 1 mandates ``dag_regroup.py`` be a CLI/review
# facade that DELEGATES the typed control-plane substance to the new module.
# items 7/8 require ``command_activate`` / ``command_rollback`` to drive the
# *typed* ``activate_overlay`` / ``rollback_overlay`` (09c-1) over the
# ``execution_regroup_overlays`` tables. The typed path's input is a typed
# :class:`RegroupOverlay` (09a) — ``validate_overlay``'s ``_coerce_typed_overlay``
# (``regroup_overlay_validation.py``) structurally REJECTS a bare
# ``DerivedDAGArtifact`` with ``dag_regroup_overlay_not_typed``. The CLI's draft
# input is still a ``DerivedDAGArtifact`` (``build_staged_regroup`` / the
# operator-authored draft), so the facade needs the *inverse* of doc 09's
# "A DerivedDAGArtifact ... generated from the typed overlay" projection: a
# deterministic adapter that lifts a ``DerivedDAGArtifact`` regroup candidate
# plus the base DAG into a typed ``RegroupOverlay``. This is that adapter.
#
# The adapter is a pure, deterministic transcription — it computes NO new
# scheduling. Every typed field is derived from the candidate / base DAG, and
# the resulting overlay is then handed to the 13-step ``validate_overlay`` (the
# sole gate); a structurally-wrong overlay fails validation and never activates.


class RegroupOverlayConversionError(RuntimeError):
    """A ``DerivedDAGArtifact`` regroup candidate cannot be lifted to a typed
    :class:`RegroupOverlay` — fail fast (no silent degradation).

    Raised by :func:`derived_artifact_to_regroup_overlay` when the candidate is
    structurally unfit for conversion (missing offset / suffix mismatch /
    unknown task ids). It is a *conversion* failure, distinct from a
    *validation* rejection — a converted overlay can still fail the 13-step
    ``validate_overlay``; conversion only guarantees a well-formed typed shape.
    """


def derived_artifact_to_regroup_overlay(
    candidate: DerivedDAGArtifact,
    base_dag: ImplementationDAG,
    *,
    feature_id: str,
    reason: str = "",
) -> RegroupOverlay:
    """Lift a ``DerivedDAGArtifact`` regroup candidate to a typed ``RegroupOverlay``.

    The deterministic CLI-side inverse of ``build_canonical_projection`` (09c-1).
    Given a staged/canonical ``DerivedDAGArtifact`` regroup candidate and the
    base DAG it was derived from, produce the full 09a typed overlay:
    ``overlay_id`` / ``overlay_slug`` / the execution orders /
    ``original_to_new_group_mapping`` / ``barriers`` / ``write_sets`` /
    ``speed_index`` / ``activation_contract`` / ``rollback_plan`` /
    ``compatibility_keys`` / ``overlay_sha256`` / ``validation_digest``.

    The conversion computes NO scheduling — it transcribes the candidate's
    already-decided derived waves, mapping, and barriers into the typed shape
    and derives the deterministic identifiers/contracts. The result is the
    typed input ``validate_overlay`` requires; the validator (not this adapter)
    is the safety gate, so this adapter never relaxes a check.

    ``overlay_sha256`` and ``validation_digest`` are filled with deterministic
    placeholders here — :func:`validate_overlay`'s ``_normalize_overlay``
    recomputes the canonical ``overlay_sha256`` from the typed substance, and
    ``record_validation`` owns the ``validation_digest``. The
    ``activation_contract.required_overlay_sha256`` self-reference is set to the
    placeholder for the same reason (the canonical-sha computation excludes it).

    Raises :class:`RegroupOverlayConversionError` when the candidate is
    structurally unfit (no ``group_idx_offset``; ``original_execution_order``
    does not match the base suffix; a derived task id is not in the base
    suffix).
    """

    # Lazy import: keep ``regroup_overlay_validation`` (which imports the 09b
    # store) off this module's import-time graph. ``_task_definition_
    # fingerprint`` is the SAME deterministic formula ``validate_overlay`` step
    # 5 compares against, so a converted overlay's fingerprints match by
    # construction (step 5 then re-derives them from the base DAG and confirms).
    # ``_canonical_overlay_sha`` is reused to stamp ``overlay_sha256`` (and the
    # ``activation_contract.required_overlay_sha256`` self-reference) so the
    # converted overlay is DIRECTLY validatable — ``_canonical_overlay_sha``
    # excludes both fields from the hashed body, so this is a single-pass fixed
    # point, exactly as the 09c-1 activation test's ``_valid_overlay`` does it.
    from .execution.regroup_overlay_validation import (
        _canonical_overlay_sha,
        _normalize_overlay,
        _task_definition_fingerprint,
    )

    offset = candidate.group_idx_offset
    if offset is None:
        raise RegroupOverlayConversionError(
            "regroup candidate has no group_idx_offset; cannot derive a typed "
            "overlay (the offset is part of the typed overlay identity)"
        )
    offset = int(offset)
    checkpointed_group = (
        int(candidate.checkpointed_group)
        if candidate.checkpointed_group is not None
        else offset - 1
    )
    if offset < 0 or offset > len(base_dag.execution_order):
        raise RegroupOverlayConversionError(
            f"regroup candidate group_idx_offset={offset} is out of range for a "
            f"base DAG with {len(base_dag.execution_order)} groups"
        )
    base_suffix = [list(group) for group in base_dag.execution_order[offset:]]
    base_suffix_ids = {tid for group in base_suffix for tid in group}
    base_by_id = {task.id: task for task in base_dag.tasks}

    derived_order = [list(group) for group in candidate.dag.execution_order]
    derived_ids = {tid for wave in derived_order for tid in wave}
    unknown = sorted(derived_ids - base_suffix_ids)
    if unknown:
        raise RegroupOverlayConversionError(
            "regroup candidate derived execution order references task ids that "
            f"are not in the base DAG suffix at offset {offset}: {unknown[:10]}"
        )
    missing_base = sorted(tid for tid in derived_ids if tid not in base_by_id)
    if missing_base:
        raise RegroupOverlayConversionError(
            "regroup candidate derived tasks have no base DAG definition: "
            f"{missing_base[:10]}"
        )

    # original_execution_order — the candidate carries it; the converter trusts
    # the candidate's recorded value but the validator's step 3 re-checks it
    # equals base_dag.execution_order[offset:] exactly, so a lying candidate is
    # rejected downstream. Default to the freshly-sliced base suffix when the
    # candidate omitted it.
    original_order = (
        [list(group) for group in candidate.original_execution_order]
        if candidate.original_execution_order
        else base_suffix
    )

    # original_to_new_group_mapping — DerivedDAGArtifact keys it by *str*; the
    # typed RegroupOverlay keys it by *int*. Transcribe verbatim.
    original_to_new = {
        int(orig): [int(g) for g in targets]
        for orig, targets in candidate.original_to_new_group_mapping.items()
    }

    # task_definition_fingerprints — the SAME formula validate_overlay step 5
    # uses, computed over the BASE task definitions (the derived tasks are the
    # base tasks re-waved; step 5 compares against the base DAG).
    fingerprints = {
        tid: _task_definition_fingerprint(base_by_id[tid])
        for tid in sorted(derived_ids)
    }

    # remaining_dependency_edges — base task dependencies restricted to the
    # remaining suffix (validate_overlay step 6 compares to exactly this).
    remaining_edges = {
        tid: sorted(
            str(dep)
            for dep in base_by_id[tid].dependencies
            if str(dep) in base_suffix_ids
        )
        for tid in sorted(derived_ids)
    }

    # barriers — DerivedDAGArtifact barriers are list[dict] with id/hard/
    # task_ids; the typed OverlayBarrier needs barrier_id/task_ids/hard/source.
    typed_barriers: list[OverlayBarrier] = []
    for raw in candidate.barriers:
        if not isinstance(raw, dict):
            continue
        barrier_id = str(raw.get("id") or raw.get("barrier_id") or "").strip()
        if not barrier_id:
            continue
        typed_barriers.append(
            OverlayBarrier(
                barrier_id=barrier_id,
                task_ids=sorted(
                    str(tid)
                    for tid in (raw.get("task_ids") or [])
                    if str(tid) in derived_ids
                ),
                hard=bool(raw.get("hard", True)),
                source="speed_index",
            )
        )

    # write_sets — already dict[str, list[str]] on DerivedDAGArtifact.
    write_sets = {
        str(tid): [str(path) for path in paths]
        for tid, paths in candidate.write_sets.items()
    }

    # speed_index — DerivedDAGArtifact carries a {"tasks": {...}} legacy block
    # (build_speed_index); the typed overlay maps task_id ->
    # OverlayTaskSpeedMetadata directly.
    speed_tasks = (candidate.speed_index or {}).get("tasks", {})
    typed_speed_index: dict[str, OverlayTaskSpeedMetadata] = {}
    for tid in sorted(derived_ids):
        meta = speed_tasks.get(tid) if isinstance(speed_tasks, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        typed_speed_index[tid] = OverlayTaskSpeedMetadata(
            semantic_lane=str(meta.get("semantic_lane") or "unknown"),
            barrier=str(meta.get("barrier") or "unknown"),
            critical_path_depth=int(meta.get("critical_path_depth") or 0),
            commit_risk=int(meta.get("commit_risk") or 0),
            verification_cost=int(meta.get("verification_cost") or 0),
            unknown_write=not bool(write_sets.get(tid)),
        )

    last_original_group = (
        len(base_dag.execution_order) - 1
        if base_dag.execution_order
        else None
    )
    overlay_slug = derive_overlay_slug(
        group_idx_offset=offset,
        last_original_group=last_original_group,
    )
    overlay_id = derive_overlay_id(
        feature_id=feature_id,
        source_dag_key=candidate.source_dag_key or SOURCE_DAG_KEY,
        base_dag_artifact_id=int(candidate.base_dag_artifact_id or 0),
        base_dag_sha256=candidate.base_dag_sha256 or "",
        group_idx_offset=offset,
        derived_execution_order=derived_order,
    )
    canonical_artifact_key = f"dag-regroup:{overlay_slug}"
    active_marker_key = f"dag-regroup-active:{overlay_slug}"
    rollback_artifact_key = f"dag-regroup-rollback:{overlay_slug}"
    observation_artifact_key = f"dag-regroup-observation:{overlay_slug}"
    compatibility_keys = OverlayCompatibilityKeys(
        canonical_artifact_key=canonical_artifact_key,
        active_marker_key=active_marker_key,
        rollback_artifact_key=rollback_artifact_key,
        observation_artifact_key=observation_artifact_key,
        sizing_review_key_prefix=f"review:dag-sizing:{feature_id}",
    )

    first_wave = derived_order[0] if derived_order else []
    first_wave_task_keys = sorted(f"dag-task:{tid}" for tid in first_wave)
    # ``_canonical_overlay_sha`` excludes both ``overlay_sha256`` and
    # ``activation_contract.required_overlay_sha256`` from the hashed body, so
    # the sha is a single-pass fixed point: build the overlay with a
    # placeholder, compute the canonical sha, then stamp both fields. The
    # ``validation_digest`` stays a placeholder — ``record_validation`` owns it,
    # and the 09b store's ``_validate_overlay_for_insert`` only requires it be
    # non-empty.
    _SHA_PLACEHOLDER = "0" * 64

    activation_contract = RegroupActivationContract(
        required_checkpoint_key=f"dag-group:{checkpointed_group}",
        forbidden_checkpoint_key=f"dag-group:{offset}",
        forbidden_first_wave_task_keys=first_wave_task_keys,
        forbidden_group_artifact_prefixes=[
            f"dag-verify:g{offset}:",
            f"dag-commit-failure:g{offset}:",
            f"dag-writeability-preflight:g{offset}:",
        ],
        forbidden_group_event_idx=offset,
        required_base_dag_artifact_id=int(candidate.base_dag_artifact_id or 0),
        required_base_dag_sha256=candidate.base_dag_sha256 or "",
        required_overlay_sha256=_SHA_PLACEHOLDER,
    )
    rollback_plan = RegroupRollbackPlan(
        restore_source_dag_key=candidate.source_dag_key or SOURCE_DAG_KEY,
        restore_from_checkpoint_group=checkpointed_group,
        rollback_marker_key=rollback_artifact_key,
        allowed_until_group_idx=offset,
        forbidden_started_keys=first_wave_task_keys,
        forbidden_started_event_group_idx=offset,
        forbidden_typed_attempt_group_idx=offset,
        forbidden_merge_queue_group_idx=offset,
    )

    draft_overlay = RegroupOverlay(
        overlay_id=overlay_id,
        overlay_slug=overlay_slug,
        feature_id=feature_id,
        status="staged",
        artifact_key=canonical_artifact_key,
        source_dag_key=candidate.source_dag_key or SOURCE_DAG_KEY,
        base_dag_artifact_id=int(candidate.base_dag_artifact_id or 0),
        base_dag_sha256=candidate.base_dag_sha256 or "",
        checkpointed_group=checkpointed_group,
        group_idx_offset=offset,
        last_original_group=last_original_group,
        original_execution_order=original_order,
        derived_execution_order=derived_order,
        original_to_new_group_mapping=original_to_new,
        task_definition_fingerprints=fingerprints,
        remaining_dependency_edges=remaining_edges,
        barriers=typed_barriers,
        write_sets=write_sets,
        speed_index=typed_speed_index,
        activation_contract=activation_contract,
        rollback_plan=rollback_plan,
        compatibility_keys=compatibility_keys,
        created_at=datetime.now(timezone.utc),
        reason=reason or candidate.derivation_reason or "",
        overlay_sha256=_SHA_PLACEHOLDER,
        validation_digest=_SHA_PLACEHOLDER,
    )
    # WB-2: normalize BEFORE stamping the canonical sha so that the sha matches
    # what validate_overlay step 1 will compute.  The builder orders waves by
    # sort_key; _normalize_overlay re-sorts within-wave task ids lexicographically
    # and is idempotent — running it here makes the sha a normalization fixed-point
    # so step 11 (required_overlay_sha256 == overlay_sha256 after re-normalizing)
    # passes.  The placeholder overlay_sha256 / required_overlay_sha256 are
    # excluded from the hashed body so this is still a single-pass fixed point.
    normalized_overlay = _normalize_overlay(draft_overlay)
    # _normalize_overlay already recomputes and stamps overlay_sha256; extract it
    # and set the required_overlay_sha256 self-reference to the same value.
    canonical_sha = normalized_overlay.overlay_sha256
    return normalized_overlay.model_copy(
        update={
            "activation_contract": normalized_overlay.activation_contract.model_copy(
                update={"required_overlay_sha256": canonical_sha}
            ),
        }
    )


def _task_metadata_for_metrics(
    task: ImplementationTask,
    speed_tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metadata = dict(speed_tasks.get(task.id) or {})
    lane = str(metadata.get("semantic_lane") or semantic_lane_for_task(task))
    barrier = str(metadata.get("barrier") or _barrier_for_task(task, lane))
    write_set = metadata.get("write_set")
    paths = _task_paths(task)
    if not isinstance(write_set, list):
        write_set = paths
    repo_candidates = set()
    if task.repo_path:
        repo_candidates.add(str(task.repo_path))
    for path in paths:
        if "/" in path:
            repo_candidates.add(path.split("/", 1)[0])
    return {
        "semantic_lane": lane,
        "barrier": barrier,
        "critical_path_depth": int(metadata.get("critical_path_depth") or 0),
        "commit_risk": int(metadata.get("commit_risk") or _commit_risk(task)),
        "verification_cost": int(metadata.get("verification_cost") or _verification_cost(task)),
        "implementation_cost": int(metadata.get("estimated_implementation_cost") or _implementation_cost(task)),
        "unknown_write": not bool(write_set),
        "write_set": sorted({str(path) for path in write_set if str(path).strip()}),
        "repos": sorted(repo_candidates),
        "sort_key": metadata.get("sort_key") or [
            0,
            LANE_RANKS.get(lane, 99),
            _verification_cost(task),
            _commit_risk(task),
            _implementation_cost(task),
            100 if not write_set else 0,
            task.id,
        ],
    }


def _new_group_bucket() -> dict[str, Any]:
    return {
        "events": Counter(),
        "artifacts": Counter(),
        "event_ids_by_type": defaultdict(list),
        "artifact_ids_by_category": defaultdict(list),
        "event_times_by_type": defaultdict(list),
        "artifact_keys": [],
        "first_seen_at": None,
        "last_seen_at": None,
    }


def _touch_time(bucket: dict[str, Any], timestamp: Any) -> None:
    if timestamp is None:
        return
    if bucket["first_seen_at"] is None or timestamp < bucket["first_seen_at"]:
        bucket["first_seen_at"] = timestamp
    if bucket["last_seen_at"] is None or timestamp > bucket["last_seen_at"]:
        bucket["last_seen_at"] = timestamp


def _summarize_group_metrics(
    *,
    group_idx: int,
    group: list[str],
    bucket: dict[str, Any],
    checkpoint_at: Any,
    previous_checkpoint_at: Any,
    tasks_by_id: dict[str, ImplementationTask],
    speed_tasks: dict[str, dict[str, Any]],
    active_group: int | None,
) -> dict[str, Any]:
    task_metadata = [
        _task_metadata_for_metrics(tasks_by_id[task_id], speed_tasks)
        for task_id in group
        if task_id in tasks_by_id
    ]
    task_count = len(group)
    lane_counts = Counter(str(item["semantic_lane"]) for item in task_metadata)
    barrier_counts = Counter(str(item["barrier"]) for item in task_metadata)
    repos = sorted({repo for item in task_metadata for repo in item["repos"]})
    write_paths = sorted({path for item in task_metadata for path in item["write_set"]})
    unknown_write_count = sum(1 for item in task_metadata if item["unknown_write"])
    start_at = bucket["first_seen_at"] or previous_checkpoint_at
    end_at = checkpoint_at or bucket["last_seen_at"]
    checkpoint_duration_h = None
    if checkpoint_at is not None and previous_checkpoint_at is not None:
        checkpoint_duration_h = (checkpoint_at - previous_checkpoint_at).total_seconds() / 3600
    observed_duration_h = None
    if start_at is not None and end_at is not None:
        observed_duration_h = (end_at - start_at).total_seconds() / 3600
    implementation_times = (
        bucket["event_times_by_type"].get("dag_task_start", [])
        + bucket["event_times_by_type"].get("dag_task_finish", [])
        + bucket["event_times_by_type"].get("dag_task_dispatch", [])
    )
    verify_times = (
        bucket["event_times_by_type"].get("dag_verify_start", [])
        + bucket["event_times_by_type"].get("dag_verify_finish", [])
        + bucket["event_times_by_type"].get("dag_expanded_verify_start", [])
        + bucket["event_times_by_type"].get("dag_expanded_verify_finish", [])
    )
    repair_times = (
        bucket["event_times_by_type"].get("dag_repair_cycle_start", [])
        + bucket["event_times_by_type"].get("dag_repair_round_start", [])
        + bucket["event_times_by_type"].get("dag_repair_round_finish", [])
    )
    commit_times = bucket["event_times_by_type"].get("dag_commit_failed", [])

    def span_hours(values: list[Any]) -> float | None:
        if len(values) < 2:
            return None
        return (max(values) - min(values)).total_seconds() / 3600

    verify_count = int(bucket["events"].get("dag_verify_finish", 0) or bucket["artifacts"].get("verify", 0))
    expanded_verify_count = int(bucket["events"].get("dag_expanded_verify_finish", 0) or bucket["artifacts"].get("expanded_verify", 0))
    repair_cycles = int(bucket["events"].get("dag_repair_cycle_start", 0))
    commit_failures = int(bucket["events"].get("dag_commit_failed", 0) or bucket["artifacts"].get("commit_failure", 0))
    rca_count = int(bucket["artifacts"].get("rca", 0))
    fix_count = int(bucket["artifacts"].get("fix", 0))
    verify_cost_units = verify_count + (expanded_verify_count * 6)
    checkpointed = checkpoint_at is not None
    normalized_basis_h = checkpoint_duration_h if checkpointed else observed_duration_h
    tail_risks: list[str] = []
    if checkpoint_duration_h is not None and checkpoint_duration_h > DEFAULT_CHECKPOINT_P75_BUDGET_HOURS:
        tail_risks.append("checkpoint_over_12h")
    if repair_cycles >= max(4, task_count):
        tail_risks.append("repeated_repair_cycles")
    if commit_failures >= 2:
        tail_risks.append("repeated_commit_failures")
    if bucket["artifacts"].get("worktree_alias", 0):
        tail_risks.append("worktree_alias_drag")
    if bucket["artifacts"].get("acl_norm", 0) >= 2:
        tail_risks.append("acl_normalization_drag")
    if bucket["artifacts"].get("task_reconcile", 0) >= 2 or bucket["artifacts"].get("task_spec_reconcile", 0) >= 2:
        tail_risks.append("stale_projection_drag")
    if fix_count >= 2 and verify_count >= 3:
        tail_risks.append("retry_oscillation_suspected")
    return {
        "group_idx": group_idx,
        "task_ids": list(group),
        "task_count": task_count,
        "checkpointed": checkpointed,
        "active": active_group == group_idx and not checkpointed,
        "start_at": start_at.isoformat() if hasattr(start_at, "isoformat") else start_at,
        "checkpoint_at": checkpoint_at.isoformat() if hasattr(checkpoint_at, "isoformat") else checkpoint_at,
        "last_seen_at": bucket["last_seen_at"].isoformat() if hasattr(bucket["last_seen_at"], "isoformat") else bucket["last_seen_at"],
        "checkpoint_duration_h": _round_float(checkpoint_duration_h),
        "observed_duration_h": _round_float(observed_duration_h),
        "implementation_duration_h": _round_float(span_hours(implementation_times)),
        "verify_duration_h": _round_float(span_hours(verify_times)),
        "repair_duration_h": _round_float(span_hours(repair_times)),
        "commit_duration_h": _round_float(span_hours(commit_times)),
        "lane_counts": dict(sorted(lane_counts.items())),
        "barrier_counts": dict(sorted(barrier_counts.items())),
        "dominant_lane": lane_counts.most_common(1)[0][0] if lane_counts else "unknown",
        "dominant_barrier": barrier_counts.most_common(1)[0][0] if barrier_counts else "unknown",
        "repo_mix": repos,
        "write_set_count": len(write_paths),
        "unknown_write_count": unknown_write_count,
        "max_dependency_depth": max((int(item["critical_path_depth"]) for item in task_metadata), default=0),
        "max_commit_risk": max((int(item["commit_risk"]) for item in task_metadata), default=0),
        "max_verification_cost": max((int(item["verification_cost"]) for item in task_metadata), default=0),
        "verify_count": verify_count,
        "expanded_verify_count": expanded_verify_count,
        "rca_count": rca_count,
        "repair_cycles": repair_cycles,
        "fix_count": fix_count,
        "commit_failures": commit_failures,
        "acl_normalizations": int(bucket["artifacts"].get("acl_norm", 0)),
        "worktree_alias_events": int(bucket["artifacts"].get("worktree_alias", 0)),
        "stale_projection_repairs": int(bucket["artifacts"].get("task_reconcile", 0) + bucket["artifacts"].get("task_spec_reconcile", 0)),
        "agent_errors": int(bucket["events"].get("agent_error", 0)),
        "agent_stalls": int(bucket["events"].get("agent_stalled", 0)),
        "verify_cost_units": verify_cost_units,
        "tasks_per_hour": _round_float(task_count / normalized_basis_h if task_count and normalized_basis_h and normalized_basis_h > 0 else None),
        "hours_per_task": _round_float(normalized_basis_h / task_count if task_count and normalized_basis_h is not None else None),
        "repair_cycles_per_task": _round_float(repair_cycles / task_count if task_count else None),
        "commit_failures_per_task": _round_float(commit_failures / task_count if task_count else None),
        "verify_cost_per_task": _round_float(verify_cost_units / task_count if task_count else None),
        "tail_risks": tail_risks,
        "evidence": {
            "event_ids": [event_id for ids in bucket["event_ids_by_type"].values() for event_id in ids][:40],
            "artifact_ids": [artifact_id for ids in bucket["artifact_ids_by_category"].values() for artifact_id in ids][:40],
            "artifact_ids_by_category": {
                category: ids[:20]
                for category, ids in sorted(bucket["artifact_ids_by_category"].items())
            },
        },
    }


def _active_group_from_checkpoints(
    checkpoints: dict[int, Any],
    effective_order: list[list[str]],
) -> int | None:
    if checkpoints:
        candidate = max(checkpoints) + 1
        if candidate < len(effective_order):
            return candidate
        return None
    if effective_order:
        return 0
    return None


def collect_sizing_metrics(
    *,
    feature_id: str,
    base_dag: ImplementationDAG,
    regroup_candidate: DerivedDAGArtifact | None,
    events: list[dict[str, Any]],
    artifact_summaries: list[dict[str, Any]],
    from_group: int = DEFAULT_FROM_GROUP,
) -> dict[str, Any]:
    effective_order, tasks_by_id, speed_tasks, regroup_offset = _effective_execution_order(
        base_dag,
        regroup_candidate,
    )
    checkpoints: dict[int, Any] = {}
    buckets: dict[int, dict[str, Any]] = defaultdict(_new_group_bucket)
    for artifact in artifact_summaries:
        key = str(artifact.get("key") or "")
        group_idx = _group_from_artifact_key(key)
        if group_idx is None:
            continue
        category = _artifact_category(key)
        bucket = buckets[group_idx]
        bucket["artifacts"][category] += 1
        bucket["artifact_ids_by_category"][category].append(int(artifact.get("id") or 0))
        bucket["artifact_keys"].append(key)
        _touch_time(bucket, artifact.get("created_at"))
        if category == "checkpoint":
            checkpoints[group_idx] = artifact.get("created_at")
    for event in events:
        group_idx = _group_from_event(event)
        if group_idx is None:
            continue
        bucket = buckets[group_idx]
        event_type = str(event.get("event_type") or "")
        bucket["events"][event_type] += 1
        bucket["event_ids_by_type"][event_type].append(int(event.get("id") or 0))
        timestamp = event.get("created_at")
        bucket["event_times_by_type"][event_type].append(timestamp)
        _touch_time(bucket, timestamp)
    active_group = _active_group_from_checkpoints(checkpoints, effective_order)
    group_metrics = [
        _summarize_group_metrics(
            group_idx=group_idx,
            group=group,
            bucket=buckets[group_idx],
            checkpoint_at=checkpoints.get(group_idx),
            previous_checkpoint_at=checkpoints.get(group_idx - 1),
            tasks_by_id=tasks_by_id,
            speed_tasks=speed_tasks,
            active_group=active_group,
        )
        for group_idx, group in enumerate(effective_order)
        if group_idx in checkpoints or group_idx in buckets or group_idx >= from_group
    ]

    def aggregate(label: str, groups: Iterable[int]) -> dict[str, Any]:
        selected = [
            metric for metric in group_metrics
            if metric["group_idx"] in set(groups) and metric["checkpointed"]
        ]
        durations = [
            float(metric["checkpoint_duration_h"])
            for metric in selected
            if metric["checkpoint_duration_h"] is not None
        ]
        task_count = sum(int(metric["task_count"]) for metric in selected)
        repair_cycles = sum(int(metric["repair_cycles"]) for metric in selected)
        commit_failures = sum(int(metric["commit_failures"]) for metric in selected)
        verify_cost = sum(int(metric["verify_cost_units"]) for metric in selected)
        return {
            "label": label,
            "completed_group_count": len(selected),
            "task_count": task_count,
            "hours_total": _round_float(sum(durations)),
            "checkpoint_duration_p50_h": _round_float(statistics.median(durations) if durations else None),
            "checkpoint_duration_p75_h": _round_float(_percentile(durations, 0.75)),
            "tasks_per_hour": _round_float(task_count / sum(durations) if task_count and durations and sum(durations) > 0 else None),
            "hours_per_task": _round_float(sum(durations) / task_count if task_count and durations else None),
            "repair_cycles_per_task": _round_float(repair_cycles / task_count if task_count else None),
            "commit_failures_per_task": _round_float(commit_failures / task_count if task_count else None),
            "verify_cost_per_task": _round_float(verify_cost / task_count if task_count else None),
        }

    latest_checkpoint = max(checkpoints) if checkpoints else None
    baseline_pre = aggregate("pre_change_g38_g44", range(38, min(45, len(effective_order))))
    completed_post_groups = [
        metric["group_idx"]
        for metric in group_metrics
        if metric["group_idx"] >= from_group and metric["checkpointed"]
    ]
    post_completed = aggregate(
        "post_change_completed",
        completed_post_groups,
    )
    current_feature_window = aggregate("post_change_g45_g54", range(45, 55))
    lane_stats = _lane_stats_from_metrics(group_metrics, min_group=from_group)
    return {
        "feature_id": feature_id,
        "generated_at": _now_iso(),
        "source": "postgres",
        "regroup_active": regroup_candidate is not None,
        "regroup_offset": regroup_offset,
        "effective_group_count": len(effective_order),
        "latest_checkpoint_group": latest_checkpoint,
        "active_group": active_group,
        "completed_post_groups": completed_post_groups,
        "baselines": {
            "pre_change_g38_g44": baseline_pre,
            "post_change_completed": post_completed,
            "post_change_g45_g54": current_feature_window,
        },
        "lane_stats": lane_stats,
        "groups": group_metrics,
    }


def _lane_stats_from_metrics(
    group_metrics: list[dict[str, Any]],
    *,
    min_group: int,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for metric in group_metrics:
        if metric["group_idx"] < min_group or not metric["checkpointed"]:
            continue
        lane = str(metric.get("dominant_lane") or "unknown")
        barrier = str(metric.get("dominant_barrier") or "unknown")
        buckets[f"lane:{lane}"].append(metric)
        buckets[f"barrier:{barrier}"].append(metric)
    stats: dict[str, dict[str, Any]] = {}
    for key, metrics in sorted(buckets.items()):
        task_count = sum(int(metric["task_count"]) for metric in metrics)
        durations = [
            float(metric["checkpoint_duration_h"])
            for metric in metrics
            if metric["checkpoint_duration_h"] is not None
        ]
        stats[key] = {
            "sample_count": len(metrics),
            "task_count": task_count,
            "checkpoint_duration_p50_h": _round_float(statistics.median(durations) if durations else None),
            "checkpoint_duration_p75_h": _round_float(_percentile(durations, 0.75)),
            "hours_per_task": _round_float(sum(durations) / task_count if task_count and durations else None),
            "repair_cycles_per_task": _round_float(sum(int(metric["repair_cycles"]) for metric in metrics) / task_count if task_count else None),
            "commit_failures_per_task": _round_float(sum(int(metric["commit_failures"]) for metric in metrics) / task_count if task_count else None),
            "verify_cost_per_task": _round_float(sum(int(metric["verify_cost_units"]) for metric in metrics) / task_count if task_count else None),
        }
    return stats


def _process_improvement(
    *,
    finding_class: str,
    title: str,
    groups: list[int],
    evidence: list[int],
    estimated_lost_hours: float,
    estimated_retry_impact: float,
    proposed_fix: str,
    safe_during_current_feature: bool,
) -> dict[str, Any]:
    evidence_ids = sorted({int(item) for item in evidence if item})
    return {
        "class": finding_class,
        "title": title,
        "affected_groups": sorted(set(groups)),
        "evidence_artifact_or_event_ids": evidence_ids[:80],
        "evidence_id_count": len(evidence_ids),
        "estimated_lost_hours": _round_float(estimated_lost_hours, 2),
        "estimated_retry_impact": _round_float(estimated_retry_impact, 2),
        "proposed_workflow_fix": proposed_fix,
        "safe_during_current_feature": safe_during_current_feature,
    }


def identify_process_improvements(metrics: dict[str, Any]) -> dict[str, Any]:
    min_group = int(metrics.get("regroup_offset") or DEFAULT_FROM_GROUP)
    groups = [
        metric for metric in metrics.get("groups", [])
        if int(metric.get("group_idx") or -1) >= min_group
    ]
    findings: list[dict[str, Any]] = []

    def artifact_ids(metric: dict[str, Any], *categories: str) -> list[int]:
        by_category = (metric.get("evidence") or {}).get("artifact_ids_by_category") or {}
        ids: list[int] = []
        for category in categories:
            ids.extend(int(item) for item in by_category.get(category, []) if item)
        return ids

    commit_groups = [m for m in groups if int(m.get("commit_failures") or 0) > 0]
    if commit_groups:
        findings.append(_process_improvement(
            finding_class="commit_hygiene_loops",
            title="Commit-only failures are still consuming retry cycles.",
            groups=[int(m["group_idx"]) for m in commit_groups],
            evidence=[item for m in commit_groups for item in artifact_ids(m, "commit_failure", "direct_repair_route")],
            estimated_lost_hours=sum(float(m.get("checkpoint_duration_h") or 0) * 0.2 for m in commit_groups),
            estimated_retry_impact=sum(float(m.get("commit_failures") or 0) for m in commit_groups),
            proposed_fix="Run commit hygiene preflight before expanded verification and route hook-only failures directly to a commit-hygiene lane.",
            safe_during_current_feature=True,
        ))
    acl_groups = [m for m in groups if int(m.get("acl_normalizations") or 0) > 0]
    if acl_groups:
        findings.append(_process_improvement(
            finding_class="acl_workability_normalization",
            title="ACL normalization is recurring and should remain pre-dispatch.",
            groups=[int(m["group_idx"]) for m in acl_groups],
            evidence=[item for m in acl_groups for item in artifact_ids(m, "acl_norm", "permission_repair")],
            estimated_lost_hours=sum(min(float(m.get("checkpoint_duration_h") or 0), 2.0) for m in acl_groups),
            estimated_retry_impact=sum(float(m.get("acl_normalizations") or 0) for m in acl_groups),
            proposed_fix="Keep normalization automatic and add a no-op fast path so repeated already-ok ACL checks do not dominate small waves.",
            safe_during_current_feature=True,
        ))
    alias_groups = [m for m in groups if int(m.get("worktree_alias_events") or 0) > 0]
    if alias_groups:
        findings.append(_process_improvement(
            finding_class="worktree_alias_canonical_path_drift",
            title="Worktree alias handling is active and should feed future sizing risk.",
            groups=[int(m["group_idx"]) for m in alias_groups],
            evidence=[item for m in alias_groups for item in artifact_ids(m, "worktree_alias")],
            estimated_lost_hours=sum(min(float(m.get("checkpoint_duration_h") or 0), 3.0) for m in alias_groups),
            estimated_retry_impact=sum(float(m.get("worktree_alias_events") or 0) for m in alias_groups),
            proposed_fix="Treat alias evidence as a sizing risk input and keep alias preflight before verifier/RCA context generation.",
            safe_during_current_feature=True,
        ))
    stale_groups = [m for m in groups if int(m.get("stale_projection_repairs") or 0) >= 2]
    if stale_groups:
        findings.append(_process_improvement(
            finding_class="stale_dag_task_projection",
            title="Stale task/result projection repair is frequent.",
            groups=[int(m["group_idx"]) for m in stale_groups],
            evidence=[item for m in stale_groups for item in artifact_ids(m, "task_reconcile", "task_spec_reconcile", "artifact_repair")],
            estimated_lost_hours=sum(float(m.get("checkpoint_duration_h") or 0) * 0.15 for m in stale_groups),
            estimated_retry_impact=sum(float(m.get("stale_projection_repairs") or 0) for m in stale_groups),
            proposed_fix="Refresh generated verify/RCA context after any task-result reconciliation and before expanded verify.",
            safe_during_current_feature=True,
        ))
    contract_groups = [
        m for m in groups
        if int(m.get("rca_count") or 0) >= 2
        and int(m.get("expanded_verify_count") or 0) >= 2
        and str(m.get("dominant_barrier") or "").startswith(("backend", "bridge", "generated", "package", "cross-repo"))
    ]
    if contract_groups:
        findings.append(_process_improvement(
            finding_class="product_contract_catalog_drift",
            title="Backend/catalog contract drift is the dominant semantic retry cost.",
            groups=[int(m["group_idx"]) for m in contract_groups],
            evidence=[item for m in contract_groups for item in artifact_ids(m, "rca", "expanded_verify", "repair_lens")],
            estimated_lost_hours=sum(float(m.get("checkpoint_duration_h") or 0) * 0.35 for m in contract_groups),
            estimated_retry_impact=sum(float(m.get("repair_cycles") or 0) for m in contract_groups),
            proposed_fix="Add a deterministic catalog/schema contract preflight before model expanded verify for backend bridge/catalog waves.",
            safe_during_current_feature=True,
        ))
    oscillation_groups = [m for m in groups if "retry_oscillation_suspected" in (m.get("tail_risks") or [])]
    if oscillation_groups:
        findings.append(_process_improvement(
            finding_class="claimed_file_retry_oscillation",
            title="Repeated fix/verify loops suggest claimed-file or patch oscillation.",
            groups=[int(m["group_idx"]) for m in oscillation_groups],
            evidence=[item for m in oscillation_groups for item in artifact_ids(m, "fix", "verify", "rca")],
            estimated_lost_hours=sum(float(m.get("checkpoint_duration_h") or 0) * 0.25 for m in oscillation_groups),
            estimated_retry_impact=sum(float(m.get("fix_count") or 0) for m in oscillation_groups),
            proposed_fix="Persist claimed-file presence checks across retries and block fixes that delete a previously claimed deliverable without explicit RCA evidence.",
            safe_during_current_feature=True,
        ))
    over_verify_groups = [
        m for m in groups
        if int(m.get("task_count") or 0) <= 4
        and int(m.get("expanded_verify_count") or 0) >= 3
        and str(m.get("dominant_lane") or "").endswith("ui")
    ]
    if over_verify_groups:
        findings.append(_process_improvement(
            finding_class="over_verification_low_risk_waves",
            title="Low-risk UI waves may be overpaying expanded verification overhead.",
            groups=[int(m["group_idx"]) for m in over_verify_groups],
            evidence=[item for m in over_verify_groups for item in artifact_ids(m, "expanded_verify", "repair_lens")],
            estimated_lost_hours=sum(float(m.get("checkpoint_duration_h") or 0) * 0.2 for m in over_verify_groups),
            estimated_retry_impact=sum(float(m.get("expanded_verify_count") or 0) for m in over_verify_groups),
            proposed_fix="Use a lighter verification matrix for low-risk UI/test-only waves after deterministic compile/test preflight passes.",
            safe_during_current_feature=False,
        ))
    agent_groups = [m for m in groups if int(m.get("agent_errors") or 0) or int(m.get("agent_stalls") or 0)]
    if agent_groups:
        findings.append(_process_improvement(
            finding_class="agent_runtime_stalls_or_failures",
            title="Agent/runtime failures should be separated from product repair metrics.",
            groups=[int(m["group_idx"]) for m in agent_groups],
            evidence=[item for m in agent_groups for item in (m.get("evidence") or {}).get("event_ids", [])],
            estimated_lost_hours=sum(min(float(m.get("checkpoint_duration_h") or 0), 2.0) for m in agent_groups),
            estimated_retry_impact=sum(float(m.get("agent_errors") or 0) + float(m.get("agent_stalls") or 0) for m in agent_groups),
            proposed_fix="Add runtime/provider failure as a first-class retry route that does not trigger product RCA.",
            safe_during_current_feature=True,
        ))
    findings.sort(
        key=lambda item: (
            float(item.get("estimated_lost_hours") or 0),
            float(item.get("estimated_retry_impact") or 0),
        ),
        reverse=True,
    )
    return {
        "feature_id": metrics.get("feature_id"),
        "generated_at": _now_iso(),
        "findings": findings,
    }


def _global_post_baseline(metrics: dict[str, Any]) -> dict[str, float]:
    baseline = (metrics.get("baselines") or {}).get("post_change_completed") or {}
    return {
        "repair_cycles_per_task": float(baseline.get("repair_cycles_per_task") or 0),
        "commit_failures_per_task": float(baseline.get("commit_failures_per_task") or 0),
        "hours_per_task": float(baseline.get("hours_per_task") or 0),
    }


def _metadata_is_test_only(metadata: dict[str, Any]) -> bool:
    paths = [str(path).lower() for path in metadata.get("write_set") or []]
    if not paths:
        return False
    test_tokens = (
        "test/",
        "tests/",
        "__tests__/",
        ".test.",
        ".spec.",
        "fixture",
        "fixtures/",
    )
    return all(any(token in path for token in test_tokens) for path in paths)


def _candidate_cap_for_metadata(
    metadata: dict[str, Any],
    *,
    metrics: dict[str, Any],
) -> tuple[int, list[str]]:
    lane = str(metadata.get("semantic_lane") or "unknown")
    barrier = str(metadata.get("barrier") or "unknown")
    reasons: list[str] = []
    high_risk_barriers = {
        "backend-foundation",
        "bridge-api-adapter",
        "generated-output",
        "ci-perf",
        "package-mirror",
        "cross-repo",
    }
    if metadata.get("unknown_write"):
        return 4, ["unknown write set keeps cap conservative"]
    if _metadata_is_test_only(metadata) and int(metadata.get("commit_risk") or 0) <= 1:
        policy_cap = 14
        reasons.append("test-only lane can use wider low-risk waves")
    elif barrier in high_risk_barriers or int(metadata.get("commit_risk") or 0) >= 4:
        return 4, [f"{barrier} is high-risk"]
    elif "test" in lane or barrier in {"perf/ci"}:
        policy_cap = 14
        reasons.append("test/perf lane can use wider low-risk waves")
    elif lane.endswith("ui") or lane in {"implementation-ui", "review-ui", "chat-ui", "planning-ui.document"}:
        policy_cap = 10
        reasons.append("isolated UI/document lane can use wider waves")
    elif lane.startswith("backend.") or len(metadata.get("repos") or []) > 1:
        policy_cap = 6
        reasons.append("mixed backend or multi-repo work uses moderate waves")
    else:
        policy_cap = 10
        reasons.append("misc low-coupling work can use wider waves")

    lane_stats = (metrics.get("lane_stats") or {}).get(f"lane:{lane}") or {}
    barrier_stats = (metrics.get("lane_stats") or {}).get(f"barrier:{barrier}") or {}
    stats = barrier_stats if int(barrier_stats.get("sample_count") or 0) >= 2 else lane_stats
    if int(stats.get("sample_count") or 0) < 2:
        return min(4, policy_cap), ["insufficient completed samples for lane/barrier; keep current cap"]
    baseline = _global_post_baseline(metrics)
    stats_repair = float(stats.get("repair_cycles_per_task") or 0)
    stats_commit = float(stats.get("commit_failures_per_task") or 0)
    if baseline["repair_cycles_per_task"] and stats_repair > baseline["repair_cycles_per_task"] * 1.1:
        return min(4, policy_cap), ["recent repair rate is worse than post-change baseline"]
    if baseline["commit_failures_per_task"] and stats_commit > baseline["commit_failures_per_task"] * 1.1:
        return min(4, policy_cap), ["recent commit-failure rate is worse than post-change baseline"]
    hours_per_task = float(stats.get("hours_per_task") or baseline["hours_per_task"] or 0)
    if hours_per_task > 0:
        cap = policy_cap
        while cap > 4 and hours_per_task * cap > DEFAULT_CHECKPOINT_P75_BUDGET_HOURS:
            cap -= 1
        if cap < policy_cap:
            reasons.append("cap reduced to keep predicted p75 checkpoint under 12h")
        return max(4, cap), reasons
    return min(4, policy_cap), ["missing hours/task evidence; keep current cap"]


def _can_add_to_adaptive_wave(
    task_id: str,
    wave: list[str],
    *,
    task_metadata: dict[str, dict[str, Any]],
    write_sets: dict[str, list[str]],
    original_group_by_task: dict[str, int],
) -> bool:
    if not wave:
        return True
    barrier = task_metadata.get(task_id, {}).get("barrier")
    if any(task_metadata.get(existing, {}).get("barrier") != barrier for existing in wave):
        return False
    candidate_write_set = set(write_sets.get(task_id, []))
    for existing_id in wave:
        if _write_sets_overlap(candidate_write_set, set(write_sets.get(existing_id, []))):
            return False
    source_groups = {original_group_by_task.get(existing_id, -1) for existing_id in wave}
    source_groups.add(original_group_by_task.get(task_id, -1))
    if len(source_groups) > 1:
        proposed = [*wave, task_id]
        if any(not write_sets.get(proposed_task_id) for proposed_task_id in proposed):
            return False
    return True


def recommend_adaptive_sizing(
    *,
    base_dag: ImplementationDAG,
    regroup_candidate: DerivedDAGArtifact | None,
    metrics: dict[str, Any],
    from_group: int = DEFAULT_FROM_GROUP,
) -> dict[str, Any]:
    effective_order, tasks_by_id, speed_tasks, regroup_offset = _effective_execution_order(
        base_dag,
        regroup_candidate,
    )
    active_group = metrics.get("active_group")
    latest_checkpoint = metrics.get("latest_checkpoint_group")
    recommend_from_group = from_group
    if isinstance(active_group, int) and active_group >= from_group:
        recommend_from_group = active_group + 1
    elif isinstance(latest_checkpoint, int) and latest_checkpoint >= from_group:
        recommend_from_group = latest_checkpoint + 1
    recommend_from_group = max(recommend_from_group, from_group)
    remaining_original_order = [
        list(group)
        for group in effective_order[recommend_from_group:]
    ]
    remaining_ids = {task_id for group in remaining_original_order for task_id in group}
    task_metadata = {
        task_id: _task_metadata_for_metrics(tasks_by_id[task_id], speed_tasks)
        for task_id in remaining_ids
        if task_id in tasks_by_id
    }
    write_sets = {
        task_id: list(metadata["write_set"])
        for task_id, metadata in task_metadata.items()
        if metadata["write_set"]
    }
    original_group_by_task = {
        task_id: recommend_from_group + idx
        for idx, group in enumerate(remaining_original_order)
        for task_id in group
    }
    dependencies = {
        task_id: {
            dep for dep in tasks_by_id[task_id].dependencies
            if dep in remaining_ids
        }
        for task_id in remaining_ids
        if task_id in tasks_by_id
    }
    cap_by_task: dict[str, int] = {}
    cap_reasons_by_task: dict[str, list[str]] = {}
    for task_id, metadata in task_metadata.items():
        cap, reasons = _candidate_cap_for_metadata(metadata, metrics=metrics)
        cap_by_task[task_id] = cap
        cap_reasons_by_task[task_id] = reasons
    scheduled: set[str] = set()
    unscheduled = set(remaining_ids)
    waves: list[list[str]] = []
    while unscheduled:
        eligible = [
            task_id for task_id in unscheduled
            if dependencies.get(task_id, set()).issubset(scheduled)
        ]
        if not eligible:
            break
        eligible.sort(key=lambda task_id: task_metadata[task_id]["sort_key"])
        seed = eligible[0]
        wave = [seed]
        wave_cap = cap_by_task.get(seed, 4)
        for task_id in eligible[1:]:
            if len(wave) >= wave_cap:
                break
            if _can_add_to_adaptive_wave(
                task_id,
                wave,
                task_metadata=task_metadata,
                write_sets=write_sets,
                original_group_by_task=original_group_by_task,
            ):
                wave.append(task_id)
                wave_cap = min(wave_cap, cap_by_task.get(task_id, 4))
        wave.sort(key=lambda task_id: task_metadata[task_id]["sort_key"])
        waves.append(wave)
        scheduled.update(wave)
        unscheduled.difference_update(wave)
    original_sizes = [len(group) for group in remaining_original_order]
    candidate_sizes = [len(wave) for wave in waves]
    barrier_caps: dict[str, list[int]] = defaultdict(list)
    cap_reasons: dict[str, Counter] = defaultdict(Counter)
    for task_id, metadata in task_metadata.items():
        barrier = str(metadata.get("barrier") or "unknown")
        barrier_caps[barrier].append(cap_by_task.get(task_id, 4))
        for reason in cap_reasons_by_task.get(task_id, []):
            cap_reasons[barrier][reason] += 1
    return {
        "feature_id": metrics.get("feature_id"),
        "generated_at": _now_iso(),
        "mode": "recommend_then_approve",
        "throughput_first": True,
        "regroup_offset": regroup_offset,
        "active_group": active_group,
        "latest_checkpoint_group": latest_checkpoint,
        "recommend_from_group": recommend_from_group,
        "active_group_policy": "current active wave is untouched; recommendations start at the next unstarted wave",
        "remaining_task_count": len(remaining_ids),
        "current_remaining_wave_count": len(remaining_original_order),
        "recommended_wave_count": len(waves),
        "current_wave_size_summary": {
            "min": min(original_sizes) if original_sizes else 0,
            "max": max(original_sizes) if original_sizes else 0,
            "mean": _round_float(statistics.mean(original_sizes) if original_sizes else None),
        },
        "recommended_wave_size_summary": {
            "min": min(candidate_sizes) if candidate_sizes else 0,
            "max": max(candidate_sizes) if candidate_sizes else 0,
            "mean": _round_float(statistics.mean(candidate_sizes) if candidate_sizes else None),
        },
        "recommended_caps_by_barrier": {
            barrier: {
                "cap": int(statistics.median(caps)),
                "sample_task_count": len(caps),
                "reasons": dict(reasons.most_common(3)),
            }
            for barrier, caps in sorted(barrier_caps.items())
            for reasons in [cap_reasons[barrier]]
        },
        "recommended_waves": [
            {
                "proposed_group_idx": recommend_from_group + idx,
                "task_ids": wave,
                "task_count": len(wave),
                "barriers": sorted({str(task_metadata[task_id].get("barrier")) for task_id in wave}),
                "lanes": sorted({str(task_metadata[task_id].get("semantic_lane")) for task_id in wave}),
            }
            for idx, wave in enumerate(waves)
        ],
        "activation_policy": "review artifact only; no canonical regroup or active marker is written",
    }


async def _latest_artifact(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    key: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, key, value, created_at
        FROM artifacts
        WHERE feature_id = $1 AND key = $2
        ORDER BY id DESC
        LIMIT 1
        """,
        feature_id,
        key,
    )


async def _latest_dag_record(conn: asyncpg.Connection, feature_id: str) -> DagRecord:
    row = await _latest_artifact(conn, feature_id=feature_id, key=SOURCE_DAG_KEY)
    if row is None:
        raise RuntimeError(f"feature {feature_id} has no root dag artifact")
    value = str(row["value"] or "")
    return DagRecord(
        artifact_id=int(row["id"]),
        value=value,
        sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
    )


def _parse_dag(record: DagRecord) -> ImplementationDAG:
    return ImplementationDAG.model_validate_json(record.value)


async def _artifact_exists(conn: asyncpg.Connection, *, feature_id: str, key: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM artifacts WHERE feature_id = $1 AND key = $2 LIMIT 1",
            feature_id,
            key,
        )
    )


async def _insert_artifact(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    key: str,
    value: str,
) -> int:
    return int(
        await conn.fetchval(
            """
            INSERT INTO artifacts (feature_id, key, value)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            feature_id,
            key,
            value,
        )
    )


async def _log_event(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    event_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO events (feature_id, event_type, source, content, metadata)
        VALUES ($1, $2, 'dag-regroup', $3, $4::jsonb)
        """,
        feature_id,
        event_type,
        content,
        json.dumps(metadata or {}),
    )


async def _load_candidate(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    prefer_canonical: bool = False,
) -> tuple[DerivedDAGArtifact, str]:
    keys = [CANONICAL_KEY, DRAFT_KEY] if prefer_canonical else [DRAFT_KEY, CANONICAL_KEY]
    for key in keys:
        row = await _latest_artifact(conn, feature_id=feature_id, key=key)
        if row is None:
            continue
        candidate = DerivedDAGArtifact.model_validate_json(str(row["value"] or ""))
        if candidate.artifact_key != key:
            candidate = candidate.model_copy(update={"artifact_key": key})
        return candidate, key
    raise RuntimeError(f"no regroup candidate found for {feature_id}; run draft first")


async def _load_metrics_regroup_candidate(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
) -> DerivedDAGArtifact | None:
    for key in (CANONICAL_KEY, DRAFT_KEY):
        row = await _latest_artifact(conn, feature_id=feature_id, key=key)
        if row is None:
            continue
        candidate = DerivedDAGArtifact.model_validate_json(str(row["value"] or ""))
        if candidate.artifact_key != key:
            candidate = candidate.model_copy(update={"artifact_key": key})
        return candidate
    return None


async def _fetch_metric_events(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, created_at, event_type, source, left(coalesce(content, ''), 256) AS content, metadata
        FROM events
        WHERE feature_id = $1
        ORDER BY id DESC
        LIMIT $2
        """,
        feature_id,
        max(1, int(limit)),
    )
    return [dict(row) for row in rows]


async def _fetch_artifact_summaries(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, key, created_at, pg_column_size(value) AS bytes
        FROM artifacts
        WHERE feature_id = $1
        ORDER BY id DESC
        LIMIT $2
        """,
        feature_id,
        max(1, int(limit)),
    )
    return [dict(row) for row in rows]


async def _build_sizing_outputs(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    from_group: int,
    event_limit: int,
    artifact_limit: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, DagRecord]:
    record = await _latest_dag_record(conn, feature_id)
    base_dag = _parse_dag(record)
    candidate = await _load_metrics_regroup_candidate(conn, feature_id=feature_id)
    events = await _fetch_metric_events(conn, feature_id=feature_id, limit=event_limit)
    artifact_summaries = await _fetch_artifact_summaries(
        conn,
        feature_id=feature_id,
        limit=artifact_limit,
    )
    metrics = collect_sizing_metrics(
        feature_id=feature_id,
        base_dag=base_dag,
        regroup_candidate=candidate,
        events=events,
        artifact_summaries=artifact_summaries,
        from_group=from_group,
    )
    process = identify_process_improvements(metrics)
    recommendation = recommend_adaptive_sizing(
        base_dag=base_dag,
        regroup_candidate=candidate,
        metrics=metrics,
        from_group=from_group,
    )
    range_label = f"g{from_group}-current"
    return metrics, process, recommendation, range_label, record


async def command_analyze_sizing(args: argparse.Namespace) -> dict[str, Any]:
    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            metrics, process, _recommendation, range_label, record = await _build_sizing_outputs(
                conn,
                feature_id=args.feature_id,
                from_group=args.from_group,
                event_limit=args.event_limit,
                artifact_limit=args.artifact_limit,
            )
            metrics_key = _review_key("sizing-metrics", args.feature_id, range_label)
            process_key = _review_key("process-improvements", args.feature_id, range_label)
            metrics_value = _json_dumps(metrics)
            process_value = _json_dumps(process)
            _require_output_budget(metrics_value, label=metrics_key)
            _require_output_budget(process_value, label=process_key)
            metrics_id = await _insert_artifact(
                conn,
                feature_id=args.feature_id,
                key=metrics_key,
                value=metrics_value,
            )
            process_id = await _insert_artifact(
                conn,
                feature_id=args.feature_id,
                key=process_key,
                value=process_value,
            )
            await _log_event(
                conn,
                feature_id=args.feature_id,
                event_type="dag_sizing_analysis_created",
                content=metrics_key,
                metadata={
                    "metrics_artifact_id": metrics_id,
                    "process_artifact_id": process_id,
                    "base_dag_artifact_id": record.artifact_id,
                    "range_label": range_label,
                    "latest_checkpoint_group": metrics.get("latest_checkpoint_group"),
                    "active_group": metrics.get("active_group"),
                },
            )
            return {
                "ok": True,
                "mode": "analysis",
                "metrics_artifact_key": metrics_key,
                "metrics_artifact_id": metrics_id,
                "process_artifact_key": process_key,
                "process_artifact_id": process_id,
                "latest_checkpoint_group": metrics.get("latest_checkpoint_group"),
                "active_group": metrics.get("active_group"),
                "baselines": metrics.get("baselines"),
                "top_process_findings": process.get("findings", [])[:5],
            }
    finally:
        await pool.close()


async def command_recommend_sizing(args: argparse.Namespace) -> dict[str, Any]:
    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            metrics, process, recommendation, range_label, record = await _build_sizing_outputs(
                conn,
                feature_id=args.feature_id,
                from_group=args.from_group,
                event_limit=args.event_limit,
                artifact_limit=args.artifact_limit,
            )
            metrics_key = _review_key("sizing-metrics", args.feature_id, range_label)
            process_key = _review_key("process-improvements", args.feature_id, range_label)
            recommendation_key = _review_key("sizing-recommendation", args.feature_id, range_label)
            metrics_value = _json_dumps(metrics)
            process_value = _json_dumps(process)
            recommendation_value = _json_dumps(recommendation)
            _require_output_budget(metrics_value, label=metrics_key)
            _require_output_budget(process_value, label=process_key)
            _require_output_budget(recommendation_value, label=recommendation_key)
            metrics_id = await _insert_artifact(
                conn,
                feature_id=args.feature_id,
                key=metrics_key,
                value=metrics_value,
            )
            process_id = await _insert_artifact(
                conn,
                feature_id=args.feature_id,
                key=process_key,
                value=process_value,
            )
            recommendation_id = await _insert_artifact(
                conn,
                feature_id=args.feature_id,
                key=recommendation_key,
                value=recommendation_value,
            )
            await _log_event(
                conn,
                feature_id=args.feature_id,
                event_type="dag_sizing_recommendation_created",
                content=recommendation_key,
                metadata={
                    "metrics_artifact_id": metrics_id,
                    "process_artifact_id": process_id,
                    "recommendation_artifact_id": recommendation_id,
                    "base_dag_artifact_id": record.artifact_id,
                    "range_label": range_label,
                    "latest_checkpoint_group": metrics.get("latest_checkpoint_group"),
                    "active_group": metrics.get("active_group"),
                    "recommend_from_group": recommendation.get("recommend_from_group"),
                    "recommended_wave_count": recommendation.get("recommended_wave_count"),
                },
            )
            return {
                "ok": True,
                "mode": "recommend_then_approve",
                "metrics_artifact_key": metrics_key,
                "metrics_artifact_id": metrics_id,
                "process_artifact_key": process_key,
                "process_artifact_id": process_id,
                "recommendation_artifact_key": recommendation_key,
                "recommendation_artifact_id": recommendation_id,
                "latest_checkpoint_group": metrics.get("latest_checkpoint_group"),
                "active_group": metrics.get("active_group"),
                "recommend_from_group": recommendation.get("recommend_from_group"),
                "remaining_task_count": recommendation.get("remaining_task_count"),
                "current_remaining_wave_count": recommendation.get("current_remaining_wave_count"),
                "recommended_wave_count": recommendation.get("recommended_wave_count"),
                "current_wave_size_summary": recommendation.get("current_wave_size_summary"),
                "recommended_wave_size_summary": recommendation.get("recommended_wave_size_summary"),
                "recommended_caps_by_barrier": recommendation.get("recommended_caps_by_barrier"),
                "top_process_findings": process.get("findings", [])[:5],
            }
    finally:
        await pool.close()


def _first_activation_wave_task_ids(candidate: DerivedDAGArtifact) -> list[str]:
    """Return task ids whose artifacts prove the effective G45 wave started."""

    task_ids: list[str] = []
    if candidate.dag.execution_order:
        task_ids.extend(str(task_id) for task_id in candidate.dag.execution_order[0])
    # Include the original first group as a conservative compatibility check for
    # older draft artifacts and hand-authored candidates.
    if candidate.original_execution_order:
        task_ids.extend(
            str(task_id) for task_id in candidate.original_execution_order[0]
        )
    return sorted({task_id for task_id in task_ids if task_id})


async def _group45_started(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    candidate: DerivedDAGArtifact,
) -> bool:
    from_group = int(candidate.group_idx_offset or DEFAULT_FROM_GROUP)
    first_wave = _first_activation_wave_task_ids(candidate)
    if await _artifact_exists(conn, feature_id=feature_id, key=f"dag-group:{from_group}"):
        return True
    if first_wave:
        task_artifact_count = await conn.fetchval(
            """
            SELECT count(*)
            FROM artifacts
            WHERE feature_id = $1
              AND key = ANY($2::text[])
            """,
            feature_id,
            [f"dag-task:{task_id}" for task_id in first_wave],
        )
        if int(task_artifact_count or 0) > 0:
            return True
    patterned_count = await conn.fetchval(
        """
        SELECT count(*)
        FROM artifacts
        WHERE feature_id = $1
          AND (
            key LIKE $2
            OR key LIKE $3
            OR key LIKE $4
          )
        """,
        feature_id,
        f"dag-verify:g{from_group}:%",
        f"dag-commit-failure:g{from_group}:%",
        f"dag-writeability-preflight:g{from_group}:%",
    )
    if int(patterned_count or 0) > 0:
        return True
    event_count = await conn.fetchval(
        """
        SELECT count(*)
        FROM events
        WHERE feature_id = $1
          AND source <> 'dag-regroup'
          AND metadata->>'group_idx' = $2
        """,
        feature_id,
        str(from_group),
    )
    return int(event_count or 0) > 0


def _rss_mb_by_command(tokens: tuple[str, ...]) -> int:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "rss=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return 0
    total_kb = 0
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rss, _, command = stripped.partition(" ")
        try:
            rss_kb = int(rss)
        except ValueError:
            continue
        lowered = command.lower()
        if any(token in lowered for token in tokens):
            total_kb += rss_kb
    return total_kb // 1024


async def _safety_snapshot(conn: asyncpg.Connection, feature_id: str) -> dict[str, Any]:
    active_connections = int(
        await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE state <> 'idle'",
        )
        or 0
    )
    db_size_bytes = int(
        await conn.fetchval("SELECT pg_database_size(current_database())") or 0
    )
    artifact_bytes = int(
        await conn.fetchval(
            """
            SELECT COALESCE(sum(pg_column_size(value)), 0)
            FROM artifacts
            WHERE feature_id = $1
            """,
            feature_id,
        )
        or 0
    )
    outbox_table = await conn.fetchval(
        "SELECT to_regclass('public.public_dashboard_outbox')",
    )
    if outbox_table:
        outbox_pending_bytes = int(
            await conn.fetchval(
                """
                SELECT COALESCE(sum(pg_column_size(payload)), 0)
                FROM public_dashboard_outbox
                WHERE status = 'pending'
                """,
            )
            or 0
        )
    else:
        outbox_pending_bytes = 0
    return {
        "active_db_connections": active_connections,
        "database_bytes": db_size_bytes,
        "feature_artifact_value_bytes": artifact_bytes,
        "pending_outbox_payload_bytes": outbox_pending_bytes,
        "postgres_rss_mb": _rss_mb_by_command(("postgres",)),
        "dashboard_rss_mb": _rss_mb_by_command(("dashboard",)),
        "supervisor_rss_mb": _rss_mb_by_command(("supervisor",)),
    }


def _safety_violations(snapshot: dict[str, Any]) -> list[str]:
    checks = [
        ("active_db_connections", 30),
        ("postgres_rss_mb", 3_072),
        ("dashboard_rss_mb", 1_024),
        ("supervisor_rss_mb", 1_024),
        ("pending_outbox_payload_bytes", 512 * 1024 * 1024),
    ]
    violations: list[str] = []
    for key, limit in checks:
        value = int(snapshot.get(key) or 0)
        if value > limit:
            violations.append(f"{key}={value} exceeds {limit}")
    return violations


async def command_draft(args: argparse.Namespace) -> dict[str, Any]:
    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            record = await _latest_dag_record(conn, args.feature_id)
            dag = _parse_dag(record)
            to_group = args.to_group if args.to_group is not None else min(DEFAULT_TO_GROUP, len(dag.execution_order) - 1)
            candidate = build_staged_regroup(
                dag,
                base_dag_artifact_id=record.artifact_id,
                base_dag_sha256=record.sha256,
                from_group=args.from_group,
                to_group=to_group,
                artifact_key=DRAFT_KEY,
            )
            canonical_candidate = candidate.model_copy(update={"artifact_key": CANONICAL_KEY})
            checkpointed_group_exists = await _artifact_exists(
                conn,
                feature_id=args.feature_id,
                key=f"dag-group:{args.from_group - 1}",
            )
            forbidden_checkpoint_exists = await _artifact_exists(
                conn,
                feature_id=args.feature_id,
                key=f"dag-group:{args.from_group}",
            )
            validation = validate_candidate(
                canonical_candidate,
                base_dag=dag,
                base_dag_artifact_id=record.artifact_id,
                base_dag_sha256=record.sha256,
                boundary_checkpoint_exists=forbidden_checkpoint_exists,
            )
            if not validation.ok:
                raise RuntimeError(f"generated draft failed validation: {validation.reason} {validation.details}")
            draft_value = candidate.model_dump_json()
            _require_output_budget(draft_value, label=DRAFT_KEY)
            draft_id = await _insert_artifact(
                conn,
                feature_id=args.feature_id,
                key=DRAFT_KEY,
                value=draft_value,
            )
            await _log_event(
                conn,
                feature_id=args.feature_id,
                event_type="dag_regroup_draft_created",
                content=DRAFT_KEY,
                metadata={
                    "draft_artifact_id": draft_id,
                    "base_dag_artifact_id": record.artifact_id,
                    "base_dag_sha256": record.sha256,
                    "from_group": args.from_group,
                    "to_group": to_group,
                    "checkpointed_group_exists": checkpointed_group_exists,
                    "forbidden_checkpoint_exists": forbidden_checkpoint_exists,
                    "wave_count": len(candidate.dag.execution_order),
                    "task_count": len(candidate.dag.tasks),
                },
            )
            return {
                "ok": True,
                "artifact_key": DRAFT_KEY,
                "artifact_id": draft_id,
                "base_dag_artifact_id": record.artifact_id,
                "base_dag_sha256": record.sha256,
                "checkpointed_group_exists": checkpointed_group_exists,
                "forbidden_checkpoint_exists": forbidden_checkpoint_exists,
                "wave_count": len(candidate.dag.execution_order),
                "task_count": len(candidate.dag.tasks),
                "lane_counts": candidate.speed_index.get("lane_counts", {}),
            }
    finally:
        await pool.close()


async def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            record = await _latest_dag_record(conn, args.feature_id)
            dag = _parse_dag(record)
            candidate, candidate_key = await _load_candidate(
                conn,
                feature_id=args.feature_id,
                prefer_canonical=args.canonical,
            )
            canonical_candidate = candidate.model_copy(update={"artifact_key": CANONICAL_KEY})
            from_group = int(candidate.group_idx_offset or DEFAULT_FROM_GROUP)
            checkpointed_group_exists = await _artifact_exists(
                conn,
                feature_id=args.feature_id,
                key=f"dag-group:{from_group - 1}",
            )
            forbidden_checkpoint_exists = await _artifact_exists(
                conn,
                feature_id=args.feature_id,
                key=f"dag-group:{from_group}",
            )
            validation = validate_candidate(
                canonical_candidate,
                base_dag=dag,
                base_dag_artifact_id=record.artifact_id,
                base_dag_sha256=record.sha256,
                boundary_checkpoint_exists=forbidden_checkpoint_exists,
            )
            return {
                "ok": validation.ok,
                "candidate_key": candidate_key,
                "reason": validation.reason,
                "details": validation.details or [],
                "base_dag_artifact_id": record.artifact_id,
                "base_dag_sha256": record.sha256,
                "checkpointed_group_exists": checkpointed_group_exists,
                "forbidden_checkpoint_exists": forbidden_checkpoint_exists,
            }
    finally:
        await pool.close()


async def command_activate(args: argparse.Namespace) -> dict[str, Any]:
    """Activate a staged regroup candidate onto the typed control plane.

    **Slice 09e-1b CLI rewire (doc 09 § "Refactoring Steps" items 1, 3, 7).**
    The facade no longer runs the legacy artifact-table activation. It:

    1. loads the ``DerivedDAGArtifact`` regroup candidate + the live root DAG;
    2. lifts the candidate to a typed :class:`RegroupOverlay` via
       :func:`derived_artifact_to_regroup_overlay` (the conversion layer);
    3. persists the staged overlay row through the 09b
       :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore`
       (``insert_overlay`` is idempotent on overlay identity);
    4. routes the CLI activation safety checks through the 13-step
       :func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_validation.validate_overlay`
       (doc 09 item 3's "CLI activation" clause) under the feature advisory
       lock — a candidate that fails ``validate_overlay`` raises and NOTHING is
       activated (fail-closed);
    5. calls the typed
       :func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_activation.activate_overlay`
       (09c-1) — which holds the feature advisory lock + one
       ``conn.transaction()``, re-runs ``validate_overlay``, runs the FULL
       doc-09 forbidden-set check (boundary checkpoint exists; the next-group
       checkpoint / first-wave ``dag-task:*`` / group-scoped verify-failure-
       preflight-merge-repair artifacts / non-regroup group events / typed
       attempts / merge-queue items / workspace snapshots / gate evidence all
       ABSENT), rejects if a different overlay is already active, and writes the
       canonical / rollback / active-marker compatibility projections + the
       typed status flip + the typed activation event ATOMICALLY.

    The DB-row-presence safety checks the legacy facade ran by hand
    (``dag-group:{checkpointed_group}`` exists, ``dag-group:{offset}`` absent,
    "group work has already started", "an active marker already exists") are
    SUBSUMED by ``activate_overlay``'s in-transaction forbidden-set check and
    its ``get_active_overlay`` check — they are not re-implemented here.

    The CLI-only ``--skip-safety`` DB/worktree budget gate is preserved: it is a
    facade concern (not part of the typed control plane), so it runs as a
    fail-fast guard before the conversion + typed activation.
    """

    # Lazy imports — keep the async typed activation/validation/store modules
    # off this facade's import-time graph (regroup_overlay_validation imports
    # the 09b store). Mirrors the existing lazy import in validate_candidate.
    from ...execution_control.regroup_overlay_store import RegroupOverlayStore
    from .execution.regroup_overlay_activation import (
        OverlayConflictNeedsFreshOverlay,
        RegroupActivationRejected,
        activate_overlay,
    )
    from .execution.regroup_overlay_validation import (
        OverlayValidationContext,
        validate_overlay,
    )

    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            record = await _latest_dag_record(conn, args.feature_id)
            dag = _parse_dag(record)
            candidate, candidate_key = await _load_candidate(
                conn,
                feature_id=args.feature_id,
                prefer_canonical=False,
            )
            # Fail-fast staleness guard (a clear operator-facing message before
            # any conversion). validate_overlay step 2 ALSO rejects a stale
            # base id/hash structurally — this is the human-legible early gate.
            if candidate.base_dag_artifact_id != record.artifact_id:
                raise RuntimeError(
                    "candidate base DAG artifact id is stale: "
                    f"{candidate.base_dag_artifact_id} != {record.artifact_id}; "
                    "regenerate the draft from the latest root dag"
                )
            if candidate.base_dag_sha256 != record.sha256:
                raise RuntimeError(
                    "candidate base DAG hash is stale; regenerate the draft "
                    "from the latest root dag"
                )

            # CLI-only DB/worktree safety budget gate (the --skip-safety flag).
            safety = await _safety_snapshot(conn, args.feature_id)
            violations = [] if args.skip_safety else _safety_violations(safety)
            if violations:
                raise RuntimeError(
                    "safety budget exceeded: " + "; ".join(violations)
                )

            # (2) lift the DerivedDAGArtifact candidate to a typed RegroupOverlay.
            overlay = derived_artifact_to_regroup_overlay(
                candidate,
                dag,
                feature_id=args.feature_id,
                reason="operator CLI activation",
            )

            # (3) persist the staged overlay row (idempotent on overlay identity).
            store = RegroupOverlayStore(conn)
            overlay_row_id = await store.insert_overlay(overlay)

            # (4) doc 09 item 3 — route the CLI activation safety checks through
            # the 13-step validate_overlay, under the feature advisory lock. A
            # rejection raises (fail-closed); NOTHING is activated. activate_
            # overlay re-runs validate_overlay internally — this explicit pass
            # is the literal "called from ... CLI activation" routing and gives
            # the operator a precise rejection reason before any state change.
            await store.acquire_feature_lock(args.feature_id)
            try:
                validation = await validate_overlay(
                    overlay,
                    OverlayValidationContext(
                        feature_id=args.feature_id,
                        boundary_checkpoint_exists=await _artifact_exists(
                            conn,
                            feature_id=args.feature_id,
                            key=f"dag-group:{overlay.group_idx_offset}",
                        ),
                        checkpointed_group_exists=await _artifact_exists(
                            conn,
                            feature_id=args.feature_id,
                            key=f"dag-group:{overlay.checkpointed_group}",
                        ),
                        overlay_row_id=overlay_row_id,
                    ),
                    store,
                    activation_check=False,
                    persist=True,
                )
            finally:
                await store.release_feature_lock(args.feature_id)
            if not validation.valid:
                raise RuntimeError(
                    "candidate validation failed: "
                    f"{validation.reason} {validation.details}"
                )

            # (5) typed activation — activate_overlay re-acquires the feature
            # advisory lock and runs the whole flow in one conn.transaction();
            # a bad overlay can never activate (validate_overlay re-runs +
            # the full forbidden-set check). A validation-digest conflict from
            # a corrected overlay surfaces as OverlayConflictNeedsFreshOverlay;
            # a doc-09 constraint rejection surfaces as RegroupActivationRejected
            # — both are re-raised as RuntimeError so the CLI surface stays a
            # uniform fail-closed RuntimeError carrying the deterministic reason.
            try:
                activation = await activate_overlay(
                    store,
                    feature_id=args.feature_id,
                    overlay_id=overlay.overlay_id,
                    reason="operator CLI activation",
                )
            except OverlayConflictNeedsFreshOverlay as conflict:
                raise RuntimeError(
                    "regroup overlay validation digest conflict: a fresh "
                    f"overlay {conflict.fresh_overlay_id} was re-staged; "
                    "re-run activation against the fresh overlay"
                ) from conflict
            except RegroupActivationRejected as rejected:
                raise RuntimeError(
                    f"regroup overlay activation rejected: {rejected.reason} "
                    f"{rejected.details}"
                ) from rejected
            return {
                "ok": True,
                "candidate_key": candidate_key,
                "overlay_id": activation.overlay_id,
                "overlay_slug": activation.overlay_slug,
                "overlay_row_id": activation.overlay_row_id,
                "canonical_artifact_id": activation.canonical_artifact_id,
                "canonical_artifact_key": activation.canonical_artifact_key,
                "canonical_sha256": activation.canonical_sha256,
                "active_artifact_id": activation.active_marker_artifact_id,
                "active_marker_key": activation.active_marker_key,
                "rollback_artifact_id": activation.rollback_artifact_id,
                "rollback_artifact_key": activation.rollback_artifact_key,
                "activation_event_id": activation.activation_event_id,
                "validation_digest": activation.validation_digest,
                "safety_snapshot": safety,
            }
    finally:
        await pool.close()


async def command_status(args: argparse.Namespace) -> dict[str, Any]:
    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            record = await _latest_dag_record(conn, args.feature_id)
            rows = await conn.fetch(
                """
                SELECT id, key, created_at, pg_column_size(value) AS bytes
                FROM artifacts
                WHERE feature_id = $1
                  AND key = ANY($2::text[])
                ORDER BY id DESC
                """,
                args.feature_id,
                [DRAFT_KEY, CANONICAL_KEY, ACTIVE_KEY, ROLLBACK_KEY, OBSERVATION_KEY],
            )
            g44 = await _artifact_exists(conn, feature_id=args.feature_id, key="dag-group:44")
            g45 = await _artifact_exists(conn, feature_id=args.feature_id, key="dag-group:45")
            active_row = await _latest_artifact(conn, feature_id=args.feature_id, key=ACTIVE_KEY)
            active_status = ""
            if active_row is not None:
                try:
                    active_status = str(json.loads(str(active_row["value"] or "{}")).get("status") or "")
                except Exception:
                    active_status = "invalid"
            rollback_blocked = g45
            rollback_block_reason = "dag-group:45 exists" if g45 else ""
            try:
                candidate, _candidate_key = await _load_candidate(
                    conn,
                    feature_id=args.feature_id,
                    prefer_canonical=True,
                )
                rollback_blocked = await _group45_started(
                    conn,
                    feature_id=args.feature_id,
                    candidate=candidate,
                )
                if rollback_blocked and not rollback_block_reason:
                    rollback_block_reason = "group 45 task/event/artifact exists"
            except Exception as exc:
                rollback_blocked = True
                rollback_block_reason = f"rollback eligibility unknown: {exc}"
            return {
                "ok": True,
                "feature_id": args.feature_id,
                "base_dag_artifact_id": record.artifact_id,
                "base_dag_sha256": record.sha256,
                "dag_group_44_exists": g44,
                "dag_group_45_exists": g45,
                "active_status": active_status,
                "rollback_blocked": rollback_blocked,
                "rollback_block_reason": rollback_block_reason,
                "artifacts": [dict(row) for row in rows],
            }
    finally:
        await pool.close()


async def command_rollback(args: argparse.Namespace) -> dict[str, Any]:
    """Roll back the active regroup overlay through the typed control plane.

    **Slice 09e-1b CLI rewire (doc 09 § "Refactoring Steps" item 8).** The
    facade no longer writes a legacy ``rolled_back`` artifact marker by hand. It
    looks up the single ``active`` typed overlay row via
    :meth:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore.get_active_overlay`
    and routes to the typed
    :func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_activation.rollback_overlay`
    (09c-1).

    ``rollback_overlay`` holds the feature advisory lock + one
    ``conn.transaction()``, cross-checks the active marker against the typed
    row, re-runs the doc-09 "not started" forbidden-set check (the first derived
    wave / ``group_idx_offset`` must be untouched), and — only when those pass —
    writes a ``rolled_back`` typed status + a new ``status="rolled_back"``
    active marker + the typed ``dag_regroup_overlay_rolled_back`` event. After
    the first derived wave starts it rejects fail-closed
    (:class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_activation.RegroupRollbackRejected`),
    leaving the active marker untouched — the operator's only path is then a
    forward-only overlay from the latest checkpoint. It NEVER deletes the
    canonical overlay, rollback artifact, validation rows, scheduler feedback,
    events, checkpoints, or root DAG.
    """

    # Lazy imports — mirror command_activate (keep the async typed modules off
    # this facade's import-time graph).
    from ...execution_control.regroup_overlay_store import RegroupOverlayStore
    from .execution.regroup_overlay_activation import (
        RegroupRollbackRejected,
        rollback_overlay,
    )

    pool = await create_pool(args.database_url)
    try:
        async with pool.acquire() as conn:
            store = RegroupOverlayStore(conn)
            active = await store.get_active_overlay(args.feature_id)
            if active is None:
                raise RuntimeError(
                    "no active typed regroup overlay exists for feature "
                    f"{args.feature_id}; nothing to roll back"
                )
            # A fail-closed rollback rejection (the first derived wave started,
            # a marker mismatch, etc.) surfaces as RegroupRollbackRejected —
            # re-raise it as RuntimeError so the CLI surface is a uniform
            # fail-closed RuntimeError carrying the deterministic reason. The
            # typed overlay row is left untouched (rollback_overlay's
            # transaction rolled back).
            try:
                result = await rollback_overlay(
                    store,
                    feature_id=args.feature_id,
                    overlay_id=active.overlay_id,
                    reason=args.reason,
                )
            except RegroupRollbackRejected as rejected:
                raise RuntimeError(
                    f"regroup overlay rollback rejected: {rejected.reason} "
                    f"{rejected.details}"
                ) from rejected
            return {
                "ok": True,
                "overlay_id": result.overlay_id,
                "overlay_slug": result.overlay_slug,
                "overlay_row_id": result.overlay_row_id,
                "rolled_back_marker_id": result.rolled_back_marker_artifact_id,
                "active_marker_key": result.active_marker_key,
                "rollback_event_id": result.rollback_event_id,
                "reason": result.reason,
            }
    finally:
        await pool.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent staged DAG regroup operator")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DATABASE_URL))
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--feature-id", required=True)

    draft = subparsers.add_parser("draft")
    add_common(draft)
    draft.add_argument("--from-group", type=int, default=DEFAULT_FROM_GROUP)
    draft.add_argument("--to-group", type=int, default=DEFAULT_TO_GROUP)

    validate = subparsers.add_parser("validate")
    add_common(validate)
    validate.add_argument("--canonical", action="store_true")

    analyze_sizing = subparsers.add_parser("analyze-sizing")
    add_common(analyze_sizing)
    analyze_sizing.add_argument("--from-group", type=int, default=DEFAULT_FROM_GROUP)
    analyze_sizing.add_argument("--event-limit", type=int, default=DEFAULT_METRICS_EVENT_LIMIT)
    analyze_sizing.add_argument("--artifact-limit", type=int, default=DEFAULT_METRICS_ARTIFACT_LIMIT)

    recommend_sizing = subparsers.add_parser("recommend-sizing")
    add_common(recommend_sizing)
    recommend_sizing.add_argument("--from-group", type=int, default=DEFAULT_FROM_GROUP)
    recommend_sizing.add_argument("--event-limit", type=int, default=DEFAULT_METRICS_EVENT_LIMIT)
    recommend_sizing.add_argument("--artifact-limit", type=int, default=DEFAULT_METRICS_ARTIFACT_LIMIT)

    activate = subparsers.add_parser("activate")
    add_common(activate)
    activate.add_argument("--from-group", type=int, default=DEFAULT_FROM_GROUP)
    activate.add_argument("--skip-safety", action="store_true")

    status = subparsers.add_parser("status")
    add_common(status)

    rollback = subparsers.add_parser("rollback")
    add_common(rollback)
    rollback.add_argument("--reason", default="operator rollback before group 45 start")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "draft":
        return await command_draft(args)
    if args.command == "validate":
        return await command_validate(args)
    if args.command == "analyze-sizing":
        return await command_analyze_sizing(args)
    if args.command == "recommend-sizing":
        return await command_recommend_sizing(args)
    if args.command == "activate":
        return await command_activate(args)
    if args.command == "status":
        return await command_status(args)
    if args.command == "rollback":
        return await command_rollback(args)
    raise ValueError(f"unknown command {args.command}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1) from exc
    print(_json_dumps(result))


if __name__ == "__main__":
    main()
