"""The typed ``SchedulerGroupMetric`` builder (Slice 09d-1).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup into a
reusable typed overlay and feeds typed execution metrics back into future wave
sizing. 09a delivered the typed models (``regroup_overlay``), 09b the store,
09b-2 the validator, 09c activation / rollback / the fail-closed resolver.

Slice 09d (scheduler feedback + adaptive sizing) is **SPLIT** per the
loop-discipline rule (STATUS.md "Loop discipline"; STATUS.md "Next safe action"
explicitly anticipates "09d may need splitting (the metric builder vs. the cap
computation + feedback emission)"):

- **09d-1 — this module** — :func:`build_scheduler_group_metrics`, the
  deterministic typed-evidence joiner. One
  :class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay.SchedulerGroupMetric`
  per group from the first post-regroup group through the high-water
  checkpoint, joined from typed task attempts (Slice 1
  ``execution_journal_rows``), typed failures (Slice 7 — ``evidence_nodes``
  failure / repair kinds), gate / verification durations (Slice 6 —
  ``evidence_nodes`` gate kinds), merge-queue timings (Slice 08
  ``merge_queue_items``), and checkpoints.
- **09d-2** (next iteration) — the conservative cap computation + the typed
  ``SchedulerFeedback`` + the ``review:dag-sizing:*`` projection.

**Scope boundary (09d-1).** This module is a *pure read path*. It runs only
bounded ``SELECT``s; it NEVER writes a row, NEVER persists a
``SchedulerFeedback``, NEVER recommends a cap, and NEVER writes any artifact —
least of all an active marker. It produces typed ``SchedulerGroupMetric``
objects in memory and returns them. The cap computation and the
``SchedulerFeedback`` / ``review:dag-sizing:*`` emission are 09d-2's concern.

**Determinism.** The builder uses only sorted keys, fixed iteration order, and
the row data it reads; it has no clock / random source. ``derive_metric_id``
(09a) sorts ``task_ids`` and ``evidence_ids`` before hashing, so the
``metric_id`` is invariant to row-fetch ordering.

**doc-09 spec.** The fields, the join sources, the completed-group definition,
the duration formulas, and the ``data_quality_flags`` rule are transcribed from
``docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md``
§ "Scheduler Feedback Schema", § "Scheduler Metrics And Cap Rules", and the
§ "Adaptive Sizing Data Flow" steps 1-3. Specifically:

- "Build ``SchedulerGroupMetric`` for each group from the first post-regroup
  group through the high-water checkpoint. Active and incomplete groups are
  included for status but excluded from completed-throughput averages."
- "Only completed groups contribute to throughput and sizing baselines. A
  group is completed only after checkpoint projection exists and is linked to
  merge, commit, no-dirty, and gate evidence."
- "A metric is usable for sizing only when it has typed evidence links for
  task attempts, gate/verification, merge or checkpoint, and compatibility
  projection lineage while legacy readers remain. Missing links do not block
  status reporting, but they set ``data_quality_flags`` and force
  ``SchedulerFeedback.data_quality`` away from ``sufficient``."

**Why a sibling leaf module (not ``regroup_overlay.py`` / not the store).**
``regroup_overlay.py`` is the 09a model-only module; the 09a/09b/09c
no-refactor discipline (STATUS.md "Loop discipline") forbids editing it. The
builder is given an asyncpg connection and runs plain bounded ``SELECT``s
directly — mirroring how ``regroup_overlay_activation.py``'s forbidden-set
checks query ``execution_journal_rows`` / ``evidence_nodes`` /
``merge_queue_items`` by ``(feature_id, group_idx)``. It does NOT import the
09b ``RegroupOverlayStore`` (that store is the typed-overlay persistence layer;
it does not own metric reads). ``dag_regroup.py`` is left UNTOUCHED — the
legacy ``collect_sizing_metrics`` artifact/event collector stays a
compatibility projection source until 09e's facade conversion; this builder is
the typed-metrics input doc 09 § "Refactoring Steps" item 5 calls for.

Interfaces verified at file:line while writing this module:

- ``SchedulerGroupMetric`` + ``derive_metric_id`` —
  ``regroup_overlay.py`` (09a).
- ``RegroupOverlay`` (the typed overlay; ``derived_execution_order`` /
  ``group_idx_offset`` / ``speed_index`` of typed
  :class:`~...regroup_overlay.OverlayTaskSpeedMetadata`) — ``regroup_overlay.py``.
- ``execution_journal_rows`` columns (``entry_type`` / ``status`` /
  ``group_idx`` / ``task_id`` / ``created_at`` / ``updated_at``) —
  ``schema.sql:33-64``; the ``(feature_id, group_idx)`` query is the same one
  ``regroup_overlay_activation.py:537`` runs.
- ``evidence_nodes`` columns (``kind`` / ``status`` / ``group_idx`` /
  ``failure_id`` / ``attempt_id`` / ``payload`` / ``metadata`` /
  ``artifact_id`` / ``started_at`` / ``finished_at``) — ``schema.sql:403-449``;
  ``failure_class`` lives at ``payload->>'failure_class'`` for
  ``runtime_failure_context`` / ``failure_route_decision`` nodes
  (``supervisor/evidence.py:493``); ``repair_kind`` lives in the
  ``repair_request`` / ``repair_outcome`` node payload
  (``execution/repair.py:95`` ``RepairKind``).
- ``merge_queue_items`` columns (``group_idx`` / ``status`` /
  ``checkpoint_projection_id`` / ``checkpoint_evidence_id`` /
  ``merge_proof_evidence_id`` / ``commit_proof_evidence_id`` /
  ``gate_evidence_ids`` / ``created_at`` / ``updated_at``) —
  ``schema.sql:567-637``.
- ``artifacts`` ``dag-group:{n}`` checkpoint key —
  ``dag_regroup.py:542`` ``_group_from_artifact_key``.
- ``FailureClass`` taxonomy — ``execution/failure_router.py:14-42``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from ....models.outputs import DerivedDAGArtifact, ImplementationDAG
from .regroup_overlay import (
    RegroupOverlay,
    SchedulerGroupMetric,
    derive_metric_id,
)

__all__ = [
    "SchedulerMetricsError",
    "build_scheduler_group_metrics",
]


# ── error ───────────────────────────────────────────────────────────────────


class SchedulerMetricsError(RuntimeError):
    """Raised when the metric builder is given inconsistent inputs.

    Fail-fast (a clear error, not a silent empty result) — doc 09 / the
    feedback-no-silent-degradation rule. The builder is a read path, so the
    only failure modes are a missing base DAG or an overlay whose
    ``derived_execution_order`` shape contradicts its ``group_idx_offset``.
    """


# ── failure-class taxonomy buckets (doc 09 § "Scheduler Metrics") ────────────
#
# doc 09 § "Scheduler Metrics And Cap Rules":
#   product_repair_cycles_per_task  — "typed product repair attempts"
#   workflow_repair_cycles_per_task — "typed workflow/control-plane repair
#       attempts ... Alias, ACL, stale projection, runtime, queue, and commit
#       hygiene classes."
#   commit_failures_per_task        — "typed commit failure records ... commit
#       hook and no-dirty failures"
#   merge_conflicts_per_task        — "merge queue conflict records"
#
# The FailureClass taxonomy (failure_router.py:14-42) maps onto these buckets.
# `product_defect` is the only product-repair class; `merge_conflict` and
# `commit_hygiene` are split out (doc 09 keeps merge conflicts "separate from
# product repair" and commit failures in their own line); every remaining
# workflow / control-plane class counts as a workflow repair cycle.

_PRODUCT_FAILURE_CLASSES: frozenset[str] = frozenset({"product_defect"})
_COMMIT_FAILURE_CLASSES: frozenset[str] = frozenset({"commit_hygiene"})
_MERGE_CONFLICT_CLASSES: frozenset[str] = frozenset({"merge_conflict"})
_RUNTIME_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "runtime_provider",
        "runtime_timeout",
        "runtime_cancelled",
        "runtime_context",
        "runtime_structured_output",
        "dispatcher_internal",
        "verifier_provider",
        "verifier_context",
    }
)
_WORKSPACE_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "worktree_alias",
        "acl_workability",
        "sandbox_allocation",
        "sandbox_binding",
        "sandbox_isolation",
        "sandbox_capture",
        "sandbox_cleanup",
    }
)
_STALE_PROJECTION_FAILURE_CLASSES: frozenset[str] = frozenset({"stale_projection"})
# Everything that is a workflow/control-plane repair class for the
# `workflow_repair_cycles` count (doc 09: "Alias, ACL, stale projection,
# runtime, queue, and commit hygiene classes"). This is every FailureClass
# that is NOT a product defect.
_PRODUCT_DEFECT_CLASS = "product_defect"

# `repair_kind` -> product vs workflow (execution/repair.py:22 RepairKind).
# A `product` repair is the only product-repair kind; contract /
# canonicalization / workspace / commit_hygiene / sandbox_cleanup repairs are
# all workflow/control-plane repairs.
_PRODUCT_REPAIR_KINDS: frozenset[str] = frozenset({"product"})

# evidence_nodes.kind families (schema.sql:437-448).
_VERIFY_KINDS: frozenset[str] = frozenset({"raw_verifier", "deterministic_gate"})
_EXPANDED_VERIFY_KINDS: frozenset[str] = frozenset({"expanded_lens"})
_GATE_KINDS: frozenset[str] = frozenset(
    {
        "gate_request",
        "candidate_manifest",
        "deterministic_gate",
        "raw_verifier",
        "expanded_lens",
        "aggregate_verdict",
        "merge_gate",
        "checkpoint_gate",
    }
)
_REPAIR_OUTCOME_KINDS: frozenset[str] = frozenset(
    {"repair_request", "repair_outcome"}
)
_RETRY_KINDS: frozenset[str] = frozenset({"retry_request", "retry_outcome"})
_FAILURE_KINDS: frozenset[str] = frozenset(
    {"runtime_failure_context", "failure_route_decision"}
)


# ── small helpers ────────────────────────────────────────────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a JSONB column value to a dict (asyncpg may hand back str)."""

    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        import json

        try:
            parsed = json.loads(value)
        except Exception:  # pragma: no cover - corrupt JSONB
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_int_list(value: Any) -> list[int]:
    """Coerce a JSONB column value to a list of ints.

    asyncpg hands a ``JSONB`` column back as a raw JSON *string* (``'[]'``) on
    a plain connection — iterating that string yields characters, not list
    elements. This parses the string first, then keeps only int-coercible
    members (a malformed entry is dropped, never crashes the read path).
    """

    if isinstance(value, (str, bytes)):
        import json

        try:
            value = json.loads(value)
        except Exception:  # pragma: no cover - corrupt JSONB
            return []
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):  # pragma: no cover - corrupt member
            continue
    return out


def _failure_class_of(payload: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Extract the typed ``failure_class`` from an evidence-node row.

    ``runtime_failure_context`` / ``failure_route_decision`` nodes carry the
    class at ``payload->>'failure_class'`` (supervisor/evidence.py:493). Some
    routing evidence nests it under ``route_decision`` / ``observation`` or
    carries it in ``metadata``. We probe the documented locations in order and
    fall back to ``"unknown"`` — a missing class is itself a data-quality
    signal, never a silent drop.
    """

    for source in (payload, metadata):
        value = source.get("failure_class")
        if isinstance(value, str) and value:
            return value
    for nest_key in ("observation", "route_decision"):
        nested = payload.get(nest_key)
        if isinstance(nested, dict):
            value = nested.get("failure_class")
            if isinstance(value, str) and value:
                return value
    return "unknown"


def _repair_kind_of(payload: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Extract the typed ``repair_kind`` from a repair-request/outcome node.

    ``RepairRequest`` (execution/repair.py:95) carries ``repair_kind``; a
    ``repair_outcome`` references its request and may echo the kind. We probe
    the row payload then metadata, then fall back to ``"unknown"``.
    """

    for source in (payload, metadata):
        value = source.get("repair_kind")
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _hours_between(start: datetime | None, end: datetime | None) -> float | None:
    """Whole-hours float between two timestamps, or ``None`` if either is unset.

    Negative spans (clock skew / out-of-order rows) clamp to ``0.0`` — a
    duration is never negative.
    """

    if start is None or end is None:
        return None
    delta = (end - start).total_seconds() / 3600.0
    return round(delta, 4) if delta > 0 else 0.0


def _ratio(numerator: float, task_count: int) -> float | None:
    """``numerator / task_count`` rounded, or ``None`` when ``task_count`` is 0."""

    if task_count <= 0:
        return None
    return round(numerator / task_count, 4)


def _min_ts(values: list[datetime | None]) -> datetime | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _max_ts(values: list[datetime | None]) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


# ── effective execution order (mirrors dag_regroup._effective_execution_order) ─


def _effective_order_and_speed(
    base_dag: ImplementationDAG,
    overlay: RegroupOverlay | None,
) -> tuple[list[list[str]], int, dict[str, dict[str, Any]]]:
    """The effective execution order + the regroup offset + per-task speed meta.

    With no overlay: the base DAG's own ``execution_order`` (offset = its
    length, i.e. no post-regroup tail). With a typed overlay: the base prefix
    waves ``[0, group_idx_offset)`` followed by the overlay's
    ``derived_execution_order`` — exactly the shape
    :meth:`RegroupOverlayResolver.resolve` produces and the same composition
    ``dag_regroup._effective_execution_order`` builds for the legacy
    ``DerivedDAGArtifact``. The root DAG is never overwritten.

    ``speed_index`` is the overlay's typed ``OverlayTaskSpeedMetadata`` map
    (per-task lane / barrier / commit risk / verification cost / unknown-write)
    flattened to plain dicts; empty when there is no overlay.
    """

    base_order = [list(group) for group in base_dag.execution_order]
    if overlay is None:
        return base_order, len(base_order), {}

    offset = int(overlay.group_idx_offset)
    if offset < 0 or offset > len(base_order):
        raise SchedulerMetricsError(
            f"overlay group_idx_offset={offset} is outside the base DAG "
            f"execution order (len={len(base_order)})"
        )
    derived = [list(group) for group in overlay.derived_execution_order]
    effective = base_order[:offset] + derived
    speed = {
        str(task_id): meta.model_dump(mode="json")
        for task_id, meta in overlay.speed_index.items()
    }
    return effective, offset, speed


# ── typed-evidence joins (one bounded SELECT per source per build) ───────────


async def _load_typed_attempts(
    conn: asyncpg.Connection, feature_id: str
) -> dict[int, list[dict[str, Any]]]:
    """``execution_journal_rows`` task attempts bucketed by ``group_idx``.

    Slice 1's typed attempt journal. doc 09 § "Adaptive Sizing Data Flow" step
    1: "Collect typed attempts from Slice 1." Only rows with a non-null
    ``group_idx`` can be attributed to a group. ``task_attempt`` /
    ``dispatch_attempt`` rows are the per-task attempts; ``group_checkpoint`` /
    ``commit_failure`` / ``verify_result`` rows for the group are kept too (the
    builder filters by ``entry_type`` where it needs a specific kind).
    """

    rows = await conn.fetch(
        "SELECT id, entry_type, status, group_idx, task_id, created_at, "
        "updated_at FROM execution_journal_rows "
        "WHERE feature_id = $1 AND group_idx IS NOT NULL "
        "ORDER BY group_idx, id",
        feature_id,
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["group_idx"]), []).append(dict(row))
    return grouped


async def _load_evidence_nodes(
    conn: asyncpg.Connection, feature_id: str
) -> dict[int, list[dict[str, Any]]]:
    """``evidence_nodes`` rows bucketed by ``group_idx``.

    The Slice 6 gate/verification graph + the Slice 7 typed failure / repair
    evidence all live in ``evidence_nodes`` (distinguished by ``kind``). doc 09
    § "Adaptive Sizing Data Flow" step 1 collects "gate results from Slice 6,
    failure classes from Slice 7." Only rows with a non-null ``group_idx`` can
    be attributed to a group.
    """

    rows = await conn.fetch(
        "SELECT id, kind, status, group_idx, attempt_id, failure_id, "
        "artifact_id, payload, metadata, started_at, finished_at, created_at "
        "FROM evidence_nodes "
        "WHERE feature_id = $1 AND group_idx IS NOT NULL "
        "ORDER BY group_idx, id",
        feature_id,
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["group_idx"]), []).append(dict(row))
    return grouped


async def _load_merge_queue_items(
    conn: asyncpg.Connection, feature_id: str
) -> dict[int, list[dict[str, Any]]]:
    """``merge_queue_items`` rows bucketed by ``group_idx``.

    Slice 08's durable merge queue. doc 09 § "Adaptive Sizing Data Flow" step
    1 collects "merge queue timings from Slice 8." A group may have several
    repo lanes; all are kept and the builder aggregates them.
    """

    rows = await conn.fetch(
        "SELECT id, group_idx, status, checkpoint_projection_id, "
        "checkpoint_evidence_id, checkpoint_gate_evidence_id, "
        "merge_proof_evidence_id, commit_proof_evidence_id, "
        "post_apply_gate_evidence_id, pre_queue_gate_evidence_id, "
        "gate_evidence_ids, failure_id, created_at, updated_at "
        "FROM merge_queue_items "
        "WHERE feature_id = $1 "
        "ORDER BY group_idx, id",
        feature_id,
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["group_idx"]), []).append(dict(row))
    return grouped


async def _load_checkpoint_artifacts(
    conn: asyncpg.Connection, feature_id: str
) -> dict[int, dict[str, Any]]:
    """The ``dag-group:{n}`` checkpoint ``artifacts`` rows keyed by group idx.

    The legacy ``dag-group:{n}`` artifact is the synchronous compatibility
    projection of a completed group checkpoint
    (``ExecutionControlStore.project_group_checkpoint``). Its presence is the
    legacy "this group checkpointed" signal and its ``created_at`` is the
    ``checkpointed_at`` time; the typed ``merge_queue_items.checkpoint_*``
    columns are the typed proof links. The latest row for the key wins.
    """

    rows = await conn.fetch(
        "SELECT id, key, created_at FROM artifacts "
        "WHERE feature_id = $1 AND key LIKE 'dag-group:%' "
        "ORDER BY id",
        feature_id,
    )
    checkpoints: dict[int, dict[str, Any]] = {}
    for row in rows:
        key = str(row["key"] or "")
        suffix = key.split(":", 1)[1] if ":" in key else ""
        if not suffix.isdigit():
            continue
        # Latest row for the group wins (ORDER BY id ascending -> overwrite).
        checkpoints[int(suffix)] = dict(row)
    return checkpoints


async def _load_compatibility_projection_ids(
    conn: asyncpg.Connection, feature_id: str
) -> dict[int, list[int]]:
    """Per-group compatibility-projection ``execution_artifact_projections`` ids.

    doc 09 § "Scheduler Feedback Schema": a metric "is usable for sizing only
    when it has typed evidence links for ... compatibility projection lineage
    while legacy readers remain." ``execution_artifact_projections``
    (``schema.sql:159``) is the typed-row -> legacy-artifact projection-link
    table (Slice 1); a row carries the projected ``projection_key``. We bucket
    the projection ids by the ``g{n}`` group encoded in the projected key
    (``dag-task:g{n}:*``, ``dag-verify:g{n}:*``, ``dag-group:{n}`` …) so a
    metric can cite its projection-lineage evidence.

    This table is OPTIONAL — when the install predates Slice 1's projection
    table the query degrades to an empty map (the missing-projection-link
    ``data_quality_flags`` signal then fires, which is the correct fail-soft
    behavior: status reporting continues, sizing is disqualified).
    """

    import re

    try:
        rows = await conn.fetch(
            "SELECT id, projection_key FROM execution_artifact_projections "
            "WHERE feature_id = $1 ORDER BY id",
            feature_id,
        )
    except asyncpg.UndefinedTableError:  # pragma: no cover - old install
        return {}
    grouped: dict[int, list[int]] = {}
    group_re = re.compile(r"(?:^dag-group:(\d+)$)|(?::g(\d+)(?::|-|$))")
    for row in rows:
        key = str(row["projection_key"] or "")
        match = group_re.search(key)
        if match is None:
            continue
        group_idx = int(match.group(1) or match.group(2))
        grouped.setdefault(group_idx, []).append(int(row["id"]))
    return grouped


# ── per-group metric assembly ────────────────────────────────────────────────


def _classify_evidence(
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bucket a group's ``evidence_nodes`` rows into the doc-09 metric counters.

    Returns the per-group typed counters + the evidence-id lists. Every count
    is derived from the typed ``kind`` + the typed ``failure_class`` /
    ``repair_kind`` — no artifact-key string sniffing (that is the legacy
    ``collect_sizing_metrics`` path; doc 09 § "Refactoring Steps" item 5 makes
    typed metrics the recommender input).
    """

    gate_evidence_ids: list[int] = []
    failure_ids: set[int] = set()
    verify_count = 0
    expanded_verify_count = 0
    product_repair_cycles = 0
    workflow_repair_cycles = 0
    commit_failures = 0
    merge_conflicts = 0
    runtime_failures = 0
    workspace_failures = 0
    stale_projection_repairs = 0
    started: list[datetime | None] = []
    finished: list[datetime | None] = []
    verify_started: list[datetime | None] = []
    verify_finished: list[datetime | None] = []
    repair_started: list[datetime | None] = []
    repair_finished: list[datetime | None] = []

    for node in nodes:
        kind = str(node.get("kind") or "")
        node_id = int(node["id"])
        started_at = node.get("started_at")
        finished_at = node.get("finished_at")
        payload = _as_dict(node.get("payload"))
        metadata = _as_dict(node.get("metadata"))

        if kind in _GATE_KINDS:
            gate_evidence_ids.append(node_id)
        if kind in _VERIFY_KINDS:
            verify_count += 1
            verify_started.append(started_at)
            verify_finished.append(finished_at)
        if kind in _EXPANDED_VERIFY_KINDS:
            expanded_verify_count += 1
            verify_started.append(started_at)
            verify_finished.append(finished_at)
        if kind in _REPAIR_OUTCOME_KINDS:
            repair_kind = _repair_kind_of(payload, metadata)
            if repair_kind in _PRODUCT_REPAIR_KINDS:
                product_repair_cycles += 1
            else:
                # contract / canonicalization / workspace / commit_hygiene /
                # sandbox_cleanup — and an unknown repair_kind — all count as
                # workflow/control-plane repair cycles (doc 09 keeps every
                # non-product repair in the workflow bucket).
                workflow_repair_cycles += 1
            repair_started.append(started_at)
            repair_finished.append(finished_at)
        if kind in _FAILURE_KINDS:
            failure_id = node.get("failure_id")
            if failure_id is not None:
                failure_ids.add(int(failure_id))
            else:
                # No FK failure_id on the row — still count the failure node
                # itself so the counters are not silently under-reported.
                failure_ids.add(node_id)
            failure_class = _failure_class_of(payload, metadata)
            if failure_class in _COMMIT_FAILURE_CLASSES:
                commit_failures += 1
            if failure_class in _MERGE_CONFLICT_CLASSES:
                merge_conflicts += 1
            if failure_class in _RUNTIME_FAILURE_CLASSES:
                runtime_failures += 1
            if failure_class in _WORKSPACE_FAILURE_CLASSES:
                workspace_failures += 1
            if failure_class in _STALE_PROJECTION_FAILURE_CLASSES:
                stale_projection_repairs += 1

        started.append(started_at)
        finished.append(finished_at)

    return {
        "gate_evidence_ids": sorted(set(gate_evidence_ids)),
        "failure_ids": sorted(failure_ids),
        "verify_count": verify_count,
        "expanded_verify_count": expanded_verify_count,
        "product_repair_cycles": product_repair_cycles,
        "workflow_repair_cycles": workflow_repair_cycles,
        "commit_failures": commit_failures,
        "merge_conflicts": merge_conflicts,
        "runtime_failures": runtime_failures,
        "workspace_failures": workspace_failures,
        "stale_projection_repairs": stale_projection_repairs,
        "evidence_started_at": _min_ts(started),
        "evidence_finished_at": _max_ts(finished),
        "verification_duration_h": _hours_between(
            _min_ts(verify_started), _max_ts(verify_finished)
        ),
        "repair_duration_h": _hours_between(
            _min_ts(repair_started), _max_ts(repair_finished)
        ),
    }


def _merge_queue_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a group's ``merge_queue_items`` lanes into the doc-09 fields.

    A group may have multiple repo lanes. The completed-checkpoint identity is:
    at least one lane reached ``done`` AND carries a non-null
    ``checkpoint_projection_id`` linked to merge/commit/checkpoint-gate
    evidence (doc 09 § "Scheduler Metrics And Cap Rules": "A group is completed
    only after checkpoint projection exists and is linked to merge, commit,
    no-dirty, and gate evidence").
    """

    if not items:
        return {
            "merge_queue_item_id": None,
            "checkpoint_projection_id": None,
            "has_merge_proof": False,
            "has_commit_proof": False,
            "has_checkpoint_evidence": False,
            "has_post_apply_gate": False,
            "queue_retries": 0,
            "merge_conflict_lanes": 0,
            "gate_evidence_ids": [],
            "merge_queue_wait_h": None,
            "merge_apply_duration_h": None,
            "commit_duration_h": None,
            "queue_created_at": None,
            "checkpointed_at": None,
        }

    # Prefer the `done` lane that actually carries the checkpoint projection as
    # the representative item; otherwise the lowest-id lane.
    done_with_ckpt = [
        item
        for item in items
        if str(item.get("status")) == "done"
        and item.get("checkpoint_projection_id") is not None
    ]
    representative = (
        sorted(done_with_ckpt, key=lambda it: int(it["id"]))[0]
        if done_with_ckpt
        else sorted(items, key=lambda it: int(it["id"]))[0]
    )

    has_merge_proof = any(
        item.get("merge_proof_evidence_id") is not None for item in items
    )
    has_commit_proof = any(
        item.get("commit_proof_evidence_id") is not None for item in items
    )
    has_checkpoint_evidence = any(
        item.get("checkpoint_evidence_id") is not None for item in items
    )
    has_post_apply_gate = any(
        item.get("post_apply_gate_evidence_id") is not None for item in items
    )
    queue_retries = sum(
        1
        for item in items
        if str(item.get("status")) in {"failed", "poisoned"}
    )
    merge_conflict_lanes = sum(
        1 for item in items if str(item.get("status")) == "poisoned"
    )
    gate_ids: set[int] = set()
    for item in items:
        for ev in _as_int_list(item.get("gate_evidence_ids")):
            gate_ids.add(ev)
        for column in (
            "pre_queue_gate_evidence_id",
            "post_apply_gate_evidence_id",
            "checkpoint_gate_evidence_id",
        ):
            value = item.get(column)
            if value is not None:
                gate_ids.add(int(value))

    queue_created_at = _min_ts([item.get("created_at") for item in items])
    checkpointed_at = (
        representative.get("updated_at")
        if str(representative.get("status")) == "done"
        else None
    )

    return {
        "merge_queue_item_id": int(representative["id"]),
        "checkpoint_projection_id": (
            int(representative["checkpoint_projection_id"])
            if representative.get("checkpoint_projection_id") is not None
            else None
        ),
        "has_merge_proof": has_merge_proof,
        "has_commit_proof": has_commit_proof,
        "has_checkpoint_evidence": has_checkpoint_evidence,
        "has_post_apply_gate": has_post_apply_gate,
        "queue_retries": queue_retries,
        "merge_conflict_lanes": merge_conflict_lanes,
        "gate_evidence_ids": sorted(gate_ids),
        "merge_queue_wait_h": _hours_between(
            queue_created_at, representative.get("updated_at")
        ),
        "merge_apply_duration_h": None,
        "commit_duration_h": None,
        "queue_created_at": queue_created_at,
        "checkpointed_at": checkpointed_at,
    }


def _speed_aggregates(
    task_ids: list[str], speed: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Aggregate the overlay's per-task ``OverlayTaskSpeedMetadata`` for a group.

    Empty / all-default when there is no overlay (the root-DAG case carries no
    typed speed index). ``lane_counts`` / ``barrier_counts`` count tasks per
    lane / barrier; ``max_*`` take the per-group maxima; ``unknown_write_count``
    counts tasks the overlay flags ``unknown_write``.
    """

    lane_counts: dict[str, int] = {}
    barrier_counts: dict[str, int] = {}
    unknown_write_count = 0
    max_dependency_depth = 0
    max_commit_risk = 0
    max_verification_cost = 0
    verify_cost_units = 0
    for task_id in task_ids:
        meta = speed.get(task_id) or {}
        lane = str(meta.get("semantic_lane") or "unknown")
        barrier = str(meta.get("barrier") or "unknown")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        barrier_counts[barrier] = barrier_counts.get(barrier, 0) + 1
        if bool(meta.get("unknown_write")):
            unknown_write_count += 1
        max_dependency_depth = max(
            max_dependency_depth, int(meta.get("critical_path_depth") or 0)
        )
        max_commit_risk = max(max_commit_risk, int(meta.get("commit_risk") or 0))
        verification_cost = int(meta.get("verification_cost") or 0)
        max_verification_cost = max(max_verification_cost, verification_cost)
        verify_cost_units += verification_cost
    return {
        "lane_counts": dict(sorted(lane_counts.items())),
        "barrier_counts": dict(sorted(barrier_counts.items())),
        "unknown_write_count": unknown_write_count,
        "max_dependency_depth": max_dependency_depth,
        "max_commit_risk": max_commit_risk,
        "max_verification_cost": max_verification_cost,
        "verify_cost_units": verify_cost_units,
    }


def _build_one_metric(
    *,
    feature_id: str,
    group_idx: int,
    task_ids: list[str],
    overlay_id: str | None,
    write_sets: dict[str, list[str]],
    speed: dict[str, dict[str, Any]],
    typed_attempts: list[dict[str, Any]],
    evidence_nodes: list[dict[str, Any]],
    merge_queue_items: list[dict[str, Any]],
    checkpoint_artifact: dict[str, Any] | None,
    previous_checkpointed_at: datetime | None,
    compatibility_projection_ids: list[int],
    high_water_checkpoint: int | None,
) -> SchedulerGroupMetric:
    """Assemble one :class:`SchedulerGroupMetric` for a single group.

    Deterministic: all the inputs are row data; the only ordering-sensitive
    operation is :func:`derive_metric_id`, which sorts before hashing.

    The completed-group identity (doc 09 § "Scheduler Metrics And Cap Rules"):
    a group is ``completed`` only when its ``dag-group:{n}`` checkpoint
    projection exists AND a merge-queue lane carries a non-null
    ``checkpoint_projection_id`` linked to merge proof + commit proof +
    checkpoint evidence + a post-apply (no-dirty) gate. Anything short of that
    is ``active`` (the high-water group) or ``pending`` — STATUS-only, excluded
    from the completed-throughput / p75 averages 09d-2 computes.
    """

    sorted_task_ids = sorted(task_ids)
    task_count = len(sorted_task_ids)

    evidence = _classify_evidence(evidence_nodes)
    mq = _merge_queue_summary(merge_queue_items)
    speed_agg = _speed_aggregates(sorted_task_ids, speed)

    task_attempt_ids = sorted(
        int(row["id"])
        for row in typed_attempts
        if str(row.get("entry_type") or "")
        in {"task_attempt", "dispatch_attempt"}
    )
    commit_failure_attempt_ids = [
        int(row["id"])
        for row in typed_attempts
        if str(row.get("entry_type") or "") == "commit_failure"
    ]

    # Checkpoint identity. The legacy `dag-group:{n}` artifact AND a typed
    # merge-queue checkpoint projection linked to full proof.
    checkpoint_projection_id = mq["checkpoint_projection_id"]
    checkpointed_at = (
        checkpoint_artifact.get("created_at")
        if checkpoint_artifact is not None
        else mq["checkpointed_at"]
    )
    fully_proven_checkpoint = bool(
        checkpoint_artifact is not None
        and checkpoint_projection_id is not None
        and mq["has_merge_proof"]
        and mq["has_commit_proof"]
        and mq["has_checkpoint_evidence"]
        and mq["has_post_apply_gate"]
    )
    completed = fully_proven_checkpoint
    is_active = (
        not completed
        and high_water_checkpoint is not None
        and group_idx == high_water_checkpoint + 1
    ) or (
        not completed
        and high_water_checkpoint is None
        and group_idx == 0
        and (bool(typed_attempts) or bool(evidence_nodes) or bool(merge_queue_items))
    )

    # state (doc 09 § "Scheduler Feedback Schema": pending|active|completed|
    # failed|rolled_back). 09d-1 derives pending/active/completed/failed from
    # typed evidence; `rolled_back` is set by the overlay status (09d-2 owns
    # the overlay-status join — 09d-1 conservatively reports completed/active/
    # failed/pending only).
    state: str
    if completed:
        state = "completed"
    elif mq["merge_conflict_lanes"] > 0 or any(
        str(item.get("status")) in {"failed", "poisoned"}
        for item in merge_queue_items
    ):
        # A poisoned/failed lane with no completed checkpoint is a failed group
        # for status purposes; an active group still in flight has neither.
        state = "failed" if not is_active else "active"
    elif is_active:
        state = "active"
    else:
        state = "pending"

    # Timing. doc 09 § "Scheduler Metrics And Cap Rules":
    #   checkpoint_duration_h =
    #     checkpointed_at - max(previous_checkpointed_at, first_group_attempt_at)
    attempt_started: list[datetime | None] = [
        row.get("created_at") for row in typed_attempts
    ]
    first_group_attempt_at = _min_ts(
        attempt_started
        + [evidence["evidence_started_at"], mq["queue_created_at"]]
    )
    started_at = first_group_attempt_at
    duration_basis_start = _max_ts([previous_checkpointed_at, first_group_attempt_at])
    checkpoint_duration_h = (
        _hours_between(duration_basis_start, checkpointed_at)
        if completed
        else None
    )

    # implementation_duration_h: first attempt -> first verify/gate evidence
    # (the implementation window precedes verification). Best-effort from typed
    # timestamps; None when the bracketing evidence is absent.
    implementation_duration_h = _hours_between(
        first_group_attempt_at, evidence["evidence_started_at"]
    )
    verification_duration_h = evidence["verification_duration_h"]
    repair_duration_h = evidence["repair_duration_h"]

    # Counts that combine merge-queue + evidence-node sources.
    commit_failures = evidence["commit_failures"] + len(commit_failure_attempt_ids)
    merge_conflicts = evidence["merge_conflicts"] + mq["merge_conflict_lanes"]
    queue_retries = mq["queue_retries"]

    # Repo / write-set shape.
    repos: set[str] = set()
    write_set_count = 0
    for task_id in sorted_task_ids:
        paths = write_sets.get(task_id) or []
        write_set_count += len(paths)
        for path in paths:
            if "/" in str(path):
                repos.add(str(path).split("/", 1)[0])
    repo_count = len(repos)

    # Per-task ratios (completed groups only carry meaningful throughput; the
    # ratios are still computed for every group so 09d-2 can read them, but
    # tasks_per_hour / hours_per_task are None unless the group completed).
    tasks_per_hour: float | None = None
    hours_per_task: float | None = None
    if completed and checkpoint_duration_h and checkpoint_duration_h > 0:
        tasks_per_hour = round(task_count / checkpoint_duration_h, 4)
        hours_per_task = round(checkpoint_duration_h / task_count, 4)

    verify_cost_units = speed_agg["verify_cost_units"]

    # Evidence-link categories (doc 09 § "Scheduler Feedback Schema"): a metric
    # is usable for sizing ONLY with typed attempt + gate + merge/checkpoint +
    # projection-lineage evidence links. A missing category sets a flag.
    gate_evidence_ids = sorted(
        set(evidence["gate_evidence_ids"]) | set(mq["gate_evidence_ids"])
    )
    data_quality_flags: list[str] = []
    if not task_attempt_ids:
        data_quality_flags.append("missing_typed_attempt_evidence")
    if not gate_evidence_ids:
        data_quality_flags.append("missing_gate_evidence")
    if mq["merge_queue_item_id"] is None and checkpoint_projection_id is None:
        data_quality_flags.append("missing_merge_or_checkpoint_evidence")
    if not compatibility_projection_ids:
        data_quality_flags.append("missing_projection_lineage")
    if completed and not fully_proven_checkpoint:  # pragma: no cover - guarded
        data_quality_flags.append("checkpoint_proof_incomplete")
    data_quality_flags = sorted(set(data_quality_flags))

    # tail_risks: deterministic, evidence-derived risk tags for the group.
    tail_risks: list[str] = []
    if speed_agg["unknown_write_count"] > 0:
        tail_risks.append("unknown_write_set")
    if merge_conflicts > 0:
        tail_risks.append("merge_conflict")
    if queue_retries >= 2:
        tail_risks.append("merge_queue_retry_loop")
    if evidence["product_repair_cycles"] >= 2:
        tail_risks.append("repeated_product_repair")
    if evidence["workflow_repair_cycles"] >= 3:
        tail_risks.append("repeated_workflow_repair")
    if commit_failures >= 2:
        tail_risks.append("commit_hygiene_loop")
    tail_risks = sorted(set(tail_risks))

    # The full sorted evidence-id set the metric cites (doc 09: `evidence_ids`).
    evidence_ids = sorted(
        set(task_attempt_ids)
        | set(commit_failure_attempt_ids)
        | set(gate_evidence_ids)
        | set(evidence["failure_ids"])
        | set(compatibility_projection_ids)
        | ({int(checkpoint_artifact["id"])} if checkpoint_artifact else set())
        | ({mq["merge_queue_item_id"]} if mq["merge_queue_item_id"] else set())
    )

    metric_id = derive_metric_id(
        feature_id=feature_id,
        group_idx=group_idx,
        overlay_id=overlay_id,
        checkpoint_projection_id=checkpoint_projection_id,
        task_ids=sorted_task_ids,
        evidence_ids=evidence_ids,
    )

    return SchedulerGroupMetric(
        metric_id=metric_id,
        feature_id=feature_id,
        group_idx=group_idx,
        overlay_id=overlay_id,
        state=state,  # type: ignore[arg-type]
        completed=completed,
        active=is_active,
        task_ids=sorted_task_ids,
        task_count=task_count,
        checkpoint_projection_id=checkpoint_projection_id,
        merge_queue_item_id=mq["merge_queue_item_id"],
        task_attempt_ids=task_attempt_ids,
        failure_ids=evidence["failure_ids"],
        gate_evidence_ids=gate_evidence_ids,
        compatibility_projection_ids=sorted(compatibility_projection_ids),
        started_at=started_at,
        checkpointed_at=checkpointed_at if completed else None,
        checkpoint_duration_h=checkpoint_duration_h,
        implementation_duration_h=implementation_duration_h,
        verification_duration_h=verification_duration_h,
        repair_duration_h=repair_duration_h,
        merge_queue_wait_h=mq["merge_queue_wait_h"],
        merge_apply_duration_h=mq["merge_apply_duration_h"],
        commit_duration_h=mq["commit_duration_h"],
        lane_counts=speed_agg["lane_counts"],
        barrier_counts=speed_agg["barrier_counts"],
        repo_count=repo_count,
        write_set_count=write_set_count,
        unknown_write_count=speed_agg["unknown_write_count"],
        max_dependency_depth=speed_agg["max_dependency_depth"],
        max_commit_risk=speed_agg["max_commit_risk"],
        max_verification_cost=speed_agg["max_verification_cost"],
        verify_count=evidence["verify_count"],
        expanded_verify_count=evidence["expanded_verify_count"],
        product_repair_cycles=evidence["product_repair_cycles"],
        workflow_repair_cycles=evidence["workflow_repair_cycles"],
        commit_failures=commit_failures,
        merge_conflicts=merge_conflicts,
        queue_retries=queue_retries,
        runtime_failures=evidence["runtime_failures"],
        workspace_failures=evidence["workspace_failures"],
        stale_projection_repairs=evidence["stale_projection_repairs"],
        verify_cost_units=verify_cost_units,
        tasks_per_hour=tasks_per_hour,
        hours_per_task=hours_per_task,
        product_repair_cycles_per_task=_ratio(
            evidence["product_repair_cycles"], task_count
        ),
        workflow_repair_cycles_per_task=_ratio(
            evidence["workflow_repair_cycles"], task_count
        ),
        commit_failures_per_task=_ratio(commit_failures, task_count),
        merge_conflicts_per_task=_ratio(merge_conflicts, task_count),
        verify_cost_per_task=_ratio(verify_cost_units, task_count),
        tail_risks=tail_risks,
        data_quality_flags=data_quality_flags,
        evidence_ids=evidence_ids,
    )


# ── public API ───────────────────────────────────────────────────────────────


async def build_scheduler_group_metrics(
    conn: asyncpg.Connection,
    *,
    feature_id: str,
    base_dag: ImplementationDAG,
    overlay: RegroupOverlay | None = None,
    write_sets: dict[str, list[str]] | None = None,
) -> list[SchedulerGroupMetric]:
    """Build one :class:`SchedulerGroupMetric` per group (Slice 09d-1).

    doc 09 § "Adaptive Sizing Data Flow" steps 1-3: collect typed attempts
    (Slice 1) / gate results (Slice 6) / failure classes (Slice 7) / merge
    queue timings (Slice 8) / checkpoints; build a ``SchedulerGroupMetric`` for
    "each group from the first post-regroup group through the high-water
    checkpoint"; attach evidence ids by category and set ``data_quality_flags``
    on missing categories.

    Parameters
    ----------
    conn
        An asyncpg connection. The builder runs read-only bounded ``SELECT``s;
        it acquires NO advisory lock and writes nothing.
    feature_id
        The feature whose typed execution evidence is joined.
    base_dag
        The root :class:`ImplementationDAG`. Its ``execution_order`` is the
        base wave layout; the root DAG is never overwritten.
    overlay
        The active typed :class:`RegroupOverlay`, or ``None``. When supplied,
        the effective execution order is the base prefix waves ``[0,
        group_idx_offset)`` followed by ``overlay.derived_execution_order``,
        and the overlay's typed ``speed_index`` supplies per-task lane /
        barrier / risk metadata; metrics carry ``overlay_id =
        overlay.overlay_id``. When ``None`` the base DAG's own execution order
        is used and metrics carry ``overlay_id = None`` (``derive_metric_id``
        substitutes ``"root"``).
    write_sets
        Optional per-task authoritative write-set map (Slice 3 contract
        scopes). When supplied it feeds ``repo_count`` / ``write_set_count``.
        When ``None`` and an overlay is present, ``overlay.write_sets`` is
        used; otherwise write-set counts are 0.

    Returns
    -------
    list[SchedulerGroupMetric]
        One metric per group from the first post-regroup group (the overlay's
        ``group_idx_offset``, or ``0`` when there is no overlay) through the
        high-water checkpoint (the highest ``dag-group:{n}`` index, or the
        active group when no checkpoint exists yet). Ordered by ``group_idx``.
        Active and incomplete groups ARE included (``completed=False``) — doc
        09 says they "appear in status output" but 09d-2 excludes them from
        completed-throughput / p75 averages via the ``completed`` flag.

    Raises
    ------
    SchedulerMetricsError
        When ``feature_id`` is empty or the overlay's ``group_idx_offset``
        contradicts the base DAG's execution-order shape. Fail-fast — never a
        silent empty result.
    """

    if not feature_id:
        raise SchedulerMetricsError(
            "build_scheduler_group_metrics requires a feature_id"
        )

    effective_order, offset, speed = _effective_order_and_speed(base_dag, overlay)
    overlay_id = overlay.overlay_id if overlay is not None else None
    effective_write_sets: dict[str, list[str]] = dict(
        write_sets
        if write_sets is not None
        else (overlay.write_sets if overlay is not None else {})
    )

    # Join sources — one bounded SELECT each.
    typed_attempts = await _load_typed_attempts(conn, feature_id)
    evidence_nodes = await _load_evidence_nodes(conn, feature_id)
    merge_queue_items = await _load_merge_queue_items(conn, feature_id)
    checkpoint_artifacts = await _load_checkpoint_artifacts(conn, feature_id)
    projection_ids = await _load_compatibility_projection_ids(conn, feature_id)

    # The first post-regroup group is the overlay offset (or 0 with no
    # overlay); the high-water checkpoint is the highest checkpointed group.
    first_group = offset if overlay is not None else 0
    high_water_checkpoint = (
        max(checkpoint_artifacts) if checkpoint_artifacts else None
    )

    # The window end: the high-water checkpoint, or — if none has checkpointed
    # — the highest group index that carries any typed evidence (the active
    # group is included for status). When there is no evidence at all, the
    # window is just `first_group` itself so a metric set is still returned for
    # a freshly-staged overlay (every metric STATUS-only).
    evidenced_groups = (
        set(typed_attempts)
        | set(evidence_nodes)
        | set(merge_queue_items)
        | set(checkpoint_artifacts)
    )
    if high_water_checkpoint is not None:
        window_end = max(high_water_checkpoint, first_group)
        # The active group is one past the high-water checkpoint; include it
        # when it carries evidence or is a real group in the effective order.
        if (
            high_water_checkpoint + 1 < len(effective_order)
            and high_water_checkpoint + 1 >= first_group
        ):
            window_end = max(window_end, high_water_checkpoint + 1)
    elif evidenced_groups:
        window_end = max(max(evidenced_groups), first_group)
    else:
        window_end = first_group

    # Never run off the end of the effective execution order.
    window_end = min(window_end, max(len(effective_order) - 1, first_group))

    metrics: list[SchedulerGroupMetric] = []
    previous_checkpointed_at: datetime | None = None
    # Seed `previous_checkpointed_at` from the checkpoint immediately before
    # `first_group` so the first window group's `checkpoint_duration_h` basis
    # is correct (doc 09: `max(previous_checkpointed_at, first_group_attempt_at)`).
    if first_group - 1 in checkpoint_artifacts:
        previous_checkpointed_at = checkpoint_artifacts[first_group - 1].get(
            "created_at"
        )

    for group_idx in range(first_group, window_end + 1):
        task_ids = (
            list(effective_order[group_idx])
            if group_idx < len(effective_order)
            else []
        )
        metric = _build_one_metric(
            feature_id=feature_id,
            group_idx=group_idx,
            task_ids=task_ids,
            overlay_id=overlay_id,
            write_sets=effective_write_sets,
            speed=speed,
            typed_attempts=typed_attempts.get(group_idx, []),
            evidence_nodes=evidence_nodes.get(group_idx, []),
            merge_queue_items=merge_queue_items.get(group_idx, []),
            checkpoint_artifact=checkpoint_artifacts.get(group_idx),
            previous_checkpointed_at=previous_checkpointed_at,
            compatibility_projection_ids=projection_ids.get(group_idx, []),
            high_water_checkpoint=high_water_checkpoint,
        )
        metrics.append(metric)
        if metric.completed and metric.checkpointed_at is not None:
            previous_checkpointed_at = metric.checkpointed_at

    return metrics


def effective_execution_order_for_overlay(
    base_dag: ImplementationDAG,
    overlay: RegroupOverlay | DerivedDAGArtifact | None,
) -> list[list[str]]:
    """Public helper: the effective execution order for a base DAG + overlay.

    Exposed for 09d-2's adaptive-sizing wave construction (which needs the same
    base-prefix-plus-derived-suffix composition). Accepts either a typed
    :class:`RegroupOverlay` or a legacy :class:`DerivedDAGArtifact` (the legacy
    compatibility payload), so 09d-2 / 09e can reuse it without re-deriving the
    composition. The root DAG is never overwritten.
    """

    if overlay is None:
        return [list(group) for group in base_dag.execution_order]
    base_order = [list(group) for group in base_dag.execution_order]
    if isinstance(overlay, RegroupOverlay):
        offset = int(overlay.group_idx_offset)
        derived = [list(group) for group in overlay.derived_execution_order]
    else:
        offset = int(
            overlay.group_idx_offset
            if overlay.group_idx_offset is not None
            else len(base_order)
        )
        derived = [list(group) for group in overlay.dag.execution_order]
    if offset < 0 or offset > len(base_order):
        raise SchedulerMetricsError(
            f"overlay group_idx_offset={offset} is outside the base DAG "
            f"execution order (len={len(base_order)})"
        )
    return base_order[:offset] + derived
