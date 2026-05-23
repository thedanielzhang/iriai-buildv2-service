"""Slice 09d-1 tests — the typed ``SchedulerGroupMetric`` builder.

These exercise
:func:`iriai_build_v2.workflows.develop.execution.scheduler_metrics.build_scheduler_group_metrics`
— the deterministic typed-evidence joiner that produces one
:class:`~...regroup_overlay.SchedulerGroupMetric` per group from typed task
attempts (``execution_journal_rows``), typed gate / failure / repair evidence
(``evidence_nodes``), merge-queue timings (``merge_queue_items``), and
``dag-group:{n}`` checkpoints.

They run against a real Postgres database (the directory ``conftest`` fixtures
``mq_conn`` / ``mq_dsn``) because the builder runs real bounded ``SELECT``s
joining five tables; the in-memory ``_FakeConnection`` cannot serve them. They
skip cleanly when no Postgres is reachable.

Coverage (per the Slice 09d-1 brief — doc 09 § "Scheduler Feedback Schema",
§ "Scheduler Metrics And Cap Rules", § "Adaptive Sizing Data Flow" steps 1-3):

- the evidence-link joins (typed attempts / gate evidence / failures / merge
  queue / checkpoints all attach to the right group by ``group_idx``);
- the completed-group identity (a group is ``completed`` only with a
  ``dag-group:{n}`` checkpoint AND a fully proven merge-queue lane);
- the active / incomplete exclusion (a running group is ``completed=False``,
  ``active=True``, and carries no ``checkpoint_duration_h`` / ``hours_per_task``
  — STATUS-only);
- ``data_quality_flags`` set on every missing evidence-link category;
- the typed failure / repair classification into the doc-09 counters;
- determinism — the same DB state yields byte-identical ``metric_id``s;
- the 09d-1 SAFETY property — the builder NEVER writes a row / artifact /
  active marker (it is a pure read path).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from iriai_build_v2.models.outputs import ImplementationDAG, ImplementationTask
from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupOverlay,
    RegroupRollbackPlan,
    SchedulerGroupMetric,
)
from iriai_build_v2.workflows.develop.execution.scheduler_metrics import (
    SchedulerMetricsError,
    build_scheduler_group_metrics,
    effective_execution_order_for_overlay,
)

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


# ── base DAG fixture ─────────────────────────────────────────────────────────


def _task(task_id: str, *, deps: list[str], files: list[str]) -> ImplementationTask:
    return ImplementationTask(
        id=task_id,
        name=f"task {task_id}",
        description=f"do {task_id}",
        files=files,
        dependencies=deps,
        team=0,
    )


def _base_dag() -> ImplementationDAG:
    """A 4-group DAG: [T00] [T10] [T20,T21] [T30]."""

    return ImplementationDAG(
        tasks=[
            _task("T00", deps=[], files=["a0.py"]),
            _task("T10", deps=["T00"], files=["a1.py"]),
            _task("T20", deps=["T10"], files=["pkg/a20.py"]),
            _task("T21", deps=["T10"], files=["pkg/a21.py"]),
            _task("T30", deps=["T20"], files=["a30.py"]),
        ],
        num_teams=1,
        execution_order=[["T00"], ["T10"], ["T20", "T21"], ["T30"]],
        complete=True,
    )


_BASE_DAG = _base_dag()


# ── DB insert helpers ────────────────────────────────────────────────────────


async def _insert_feature(conn: asyncpg.Connection, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id,
        feature_id,
        feature_id,
        "develop",
        "ws-1",
    )


async def _insert_attempt(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    key: str,
    group_idx: int | None,
    entry_type: str = "task_attempt",
    status: str = "succeeded",
    created_at: datetime | None = None,
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO execution_journal_rows "
            "(feature_id, idempotency_key, entry_type, status, group_idx, "
            " request_digest, created_at) "
            "VALUES ($1,$2,$3,$4,$5,'rd',$6) RETURNING id",
            feature_id,
            key,
            entry_type,
            status,
            group_idx,
            created_at or _NOW,
        )
    )


async def _insert_evidence(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    key: str,
    kind: str,
    group_idx: int | None,
    status: str = "approved",
    failure_id: int | None = None,
    payload: str = "{}",
    metadata: str = "{}",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO evidence_nodes "
            "(feature_id, idempotency_key, kind, status, group_idx, "
            " failure_id, content_hash, payload, metadata, started_at, "
            " finished_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,'ch',$7::jsonb,$8::jsonb,$9,$10) "
            "RETURNING id",
            feature_id,
            key,
            kind,
            status,
            group_idx,
            failure_id,
            payload,
            metadata,
            started_at or _NOW,
            finished_at,
        )
    )


async def _insert_merge_queue_item(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    key: str,
    group_idx: int,
    status: str = "queued",
    checkpoint_projection_id: int | None = None,
    merge_proof_evidence_id: int | None = None,
    commit_proof_evidence_id: int | None = None,
    checkpoint_evidence_id: int | None = None,
    checkpoint_gate_evidence_id: int | None = None,
    post_apply_gate_evidence_id: int | None = None,
    pre_queue_gate_evidence_id: int | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> int:
    # A `checkpointing`/`done` lane must carry non-empty checkpoint digests
    # (the merge_queue_items_checkpoint_digests_check CHECK constraint).
    ckpt_digest = "cov" if status in {"checkpointing", "done"} else ""
    ckpt_body_sha = "bsha" if status in {"checkpointing", "done"} else ""
    result_commit = "rc" if status == "done" else ""
    return int(
        await conn.fetchval(
            "INSERT INTO merge_queue_items "
            "(feature_id, dag_sha256, group_idx, base_commit, request_digest, "
            " idempotency_key, status, checkpoint_projection_id, "
            " merge_proof_evidence_id, commit_proof_evidence_id, "
            " checkpoint_evidence_id, checkpoint_gate_evidence_id, "
            " post_apply_gate_evidence_id, pre_queue_gate_evidence_id, "
            " checkpoint_coverage_digest, checkpoint_body_sha256, "
            " result_commit, created_at, updated_at) "
            "VALUES ($1,'dsha',$2,'bc','rd',$3,$4,$5,$6,$7,$8,$9,$10,$11,"
            " $12,$13,$14,$15,$16) RETURNING id",
            feature_id,
            group_idx,
            key,
            status,
            checkpoint_projection_id,
            merge_proof_evidence_id,
            commit_proof_evidence_id,
            checkpoint_evidence_id,
            checkpoint_gate_evidence_id,
            post_apply_gate_evidence_id,
            pre_queue_gate_evidence_id,
            ckpt_digest,
            ckpt_body_sha,
            result_commit,
            created_at or _NOW,
            updated_at or _NOW,
        )
    )


async def _insert_checkpoint_artifact(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int,
    created_at: datetime | None = None,
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO artifacts (feature_id, key, value, created_at) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            feature_id,
            f"dag-group:{group_idx}",
            '{"ok":true}',
            created_at or _NOW,
        )
    )


async def _seed_completed_group(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int,
    task_ids: list[str],
    checkpointed_at: datetime,
    first_attempt_at: datetime,
) -> None:
    """Seed a FULLY completed group: typed attempts + gate + a merge-queue lane
    with merge/commit/checkpoint proof + a ``dag-group`` checkpoint artifact."""

    for task_id in task_ids:
        await _insert_attempt(
            conn,
            feature_id,
            key=f"att-{group_idx}-{task_id}",
            group_idx=group_idx,
            created_at=first_attempt_at,
        )
    gate_id = await _insert_evidence(
        conn,
        feature_id,
        key=f"gate-{group_idx}",
        kind="deterministic_gate",
        group_idx=group_idx,
        started_at=first_attempt_at + timedelta(hours=1),
        finished_at=first_attempt_at + timedelta(hours=2),
    )
    merge_proof = await _insert_evidence(
        conn, feature_id, key=f"mp-{group_idx}", kind="merge_proof",
        group_idx=group_idx,
    )
    commit_proof = await _insert_evidence(
        conn, feature_id, key=f"cp-{group_idx}", kind="commit_proof",
        group_idx=group_idx,
    )
    ckpt_ev = await _insert_evidence(
        conn, feature_id, key=f"cke-{group_idx}", kind="checkpoint_gate",
        group_idx=group_idx,
    )
    post_apply = await _insert_evidence(
        conn, feature_id, key=f"pa-{group_idx}", kind="merge_gate",
        group_idx=group_idx,
    )
    await _insert_merge_queue_item(
        conn,
        feature_id,
        key=f"mq-{group_idx}",
        group_idx=group_idx,
        status="done",
        checkpoint_projection_id=9000 + group_idx,
        merge_proof_evidence_id=merge_proof,
        commit_proof_evidence_id=commit_proof,
        checkpoint_evidence_id=ckpt_ev,
        checkpoint_gate_evidence_id=ckpt_ev,
        post_apply_gate_evidence_id=post_apply,
        pre_queue_gate_evidence_id=gate_id,
        created_at=first_attempt_at,
        updated_at=checkpointed_at,
    )
    await _insert_checkpoint_artifact(
        conn, feature_id, group_idx=group_idx, created_at=checkpointed_at
    )


# ── overlay fixture ──────────────────────────────────────────────────────────


def _overlay(feature_id: str, *, overlay_id: str = "ov-09d1") -> RegroupOverlay:
    """A typed overlay over the base DAG suffix [2, 3] with a typed speed
    index, used to exercise the overlay-aware effective-order path."""

    derived = [["T20", "T21"], ["T30"]]
    return RegroupOverlay(
        overlay_id=overlay_id,
        overlay_slug="g2-g3",
        feature_id=feature_id,
        status="active",
        artifact_key="dag-regroup:g2-g3",
        source_dag_key="dag",
        base_dag_artifact_id=1,
        base_dag_sha256="basesha",
        checkpointed_group=1,
        group_idx_offset=2,
        last_original_group=3,
        original_execution_order=[["T20", "T21"], ["T30"]],
        derived_execution_order=derived,
        original_to_new_group_mapping={2: [2], 3: [3]},
        task_definition_fingerprints={"T20": "f", "T21": "f", "T30": "f"},
        remaining_dependency_edges={"T20": [], "T21": [], "T30": ["T20"]},
        barriers=[],
        write_sets={"T20": ["pkg/a20.py"], "T21": ["pkg/a21.py"], "T30": ["a30.py"]},
        speed_index={
            "T20": OverlayTaskSpeedMetadata(
                semantic_lane="backend", barrier="b-backend",
                commit_risk=3, verification_cost=2, critical_path_depth=4,
            ),
            "T21": OverlayTaskSpeedMetadata(
                semantic_lane="backend", barrier="b-backend",
                commit_risk=2, verification_cost=1,
            ),
            "T30": OverlayTaskSpeedMetadata(
                semantic_lane="backend", barrier="b-backend",
                unknown_write=True, verification_cost=5,
            ),
        },
        activation_contract=RegroupActivationContract(
            required_checkpoint_key="dag-group:1",
            forbidden_checkpoint_key="dag-group:2",
            forbidden_first_wave_task_keys=["dag-task:T20", "dag-task:T21"],
            forbidden_group_artifact_prefixes=["dag-verify:g2"],
            forbidden_group_event_idx=2,
            required_base_dag_artifact_id=1,
            required_base_dag_sha256="basesha",
            required_overlay_sha256="osha",
        ),
        rollback_plan=RegroupRollbackPlan(
            restore_source_dag_key="dag",
            restore_from_checkpoint_group=1,
            rollback_marker_key="dag-regroup-rollback:g2-g3",
            allowed_until_group_idx=2,
            forbidden_started_keys=["dag-task:T20"],
            forbidden_started_event_group_idx=2,
            forbidden_typed_attempt_group_idx=2,
            forbidden_merge_queue_group_idx=2,
        ),
        compatibility_keys=OverlayCompatibilityKeys(
            canonical_artifact_key="dag-regroup:g2-g3",
            active_marker_key="dag-regroup-active:g2-g3",
            rollback_artifact_key="dag-regroup-rollback:g2-g3",
            observation_artifact_key="dag-regroup-observation:g2-g3",
            sizing_review_key_prefix="review:dag-sizing:" + feature_id,
        ),
        created_at=_NOW,
        overlay_sha256="osha",
        validation_digest="vd",
    )


# ════════════════════════════════════════════════════════════════════════════
# Pure-function tests (no DB) — effective execution order + input validation
# ════════════════════════════════════════════════════════════════════════════


def test_effective_order_no_overlay_is_base_dag() -> None:
    order = effective_execution_order_for_overlay(_BASE_DAG, None)
    assert order == [["T00"], ["T10"], ["T20", "T21"], ["T30"]]


def test_effective_order_with_overlay_is_prefix_plus_derived() -> None:
    overlay = _overlay("feat-x")
    # Overlay offset is 2: base prefix waves [0,2) + the overlay derived suffix.
    order = effective_execution_order_for_overlay(_BASE_DAG, overlay)
    assert order == [["T00"], ["T10"], ["T20", "T21"], ["T30"]]


def test_effective_order_rejects_out_of_range_offset() -> None:
    overlay = _overlay("feat-x")
    bad = overlay.model_copy(update={"group_idx_offset": 99})
    with pytest.raises(SchedulerMetricsError):
        effective_execution_order_for_overlay(_BASE_DAG, bad)


@pytest.mark.asyncio
async def test_builder_rejects_empty_feature_id(mq_conn) -> None:
    with pytest.raises(SchedulerMetricsError):
        await build_scheduler_group_metrics(
            mq_conn, feature_id="", base_dag=_BASE_DAG
        )


# ════════════════════════════════════════════════════════════════════════════
# Evidence-link joins
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_builder_joins_typed_evidence_by_group_idx(mq_conn) -> None:
    """Typed attempts / gate evidence / failures / merge queue / checkpoints
    all attach to the correct group via ``group_idx`` (doc 09 § "Adaptive
    Sizing Data Flow" step 1)."""

    await _insert_feature(mq_conn, "feat-join")
    # Group 0: a fully completed group.
    await _seed_completed_group(
        mq_conn, "feat-join", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    # Group 1: a fully completed group.
    await _seed_completed_group(
        mq_conn, "feat-join", group_idx=1, task_ids=["T10"],
        first_attempt_at=_NOW + timedelta(hours=3),
        checkpointed_at=_NOW + timedelta(hours=8),
    )
    # An attempt that belongs to a DIFFERENT group must not bleed into group 0.
    await _insert_attempt(
        mq_conn, "feat-join", key="stray", group_idx=1,
        entry_type="task_attempt",
    )

    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-join", base_dag=_BASE_DAG
    )
    by_group = {m.group_idx: m for m in metrics}
    # Group 0 has exactly one typed attempt (T00); the stray group-1 attempt
    # did not leak in.
    assert by_group[0].task_attempt_ids and len(by_group[0].task_attempt_ids) == 1
    # Group 1 has two typed attempts (T10 + the stray).
    assert len(by_group[1].task_attempt_ids) == 2
    # Both groups carry gate evidence and a merge-queue item.
    for group_idx in (0, 1):
        assert by_group[group_idx].gate_evidence_ids
        assert by_group[group_idx].merge_queue_item_id is not None
        assert by_group[group_idx].checkpoint_projection_id is not None


@pytest.mark.asyncio
async def test_builder_uses_overlay_speed_index_for_lane_barrier(mq_conn) -> None:
    """With a typed overlay the metric carries the overlay's ``overlay_id`` and
    its typed ``speed_index`` (lane / barrier / risk) drives the aggregates."""

    await _insert_feature(mq_conn, "feat-ov")
    overlay = _overlay("feat-ov")
    # Group 2 (the overlay's first post-regroup group) — incomplete.
    await _insert_attempt(mq_conn, "feat-ov", key="a2-T20", group_idx=2)
    await _insert_checkpoint_artifact(mq_conn, "feat-ov", group_idx=2)

    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-ov", base_dag=_BASE_DAG, overlay=overlay
    )
    by_group = {m.group_idx: m for m in metrics}
    # The first post-regroup group is the overlay's offset (2).
    assert min(by_group) == 2
    g2 = by_group[2]
    assert g2.overlay_id == "ov-09d1"
    # The overlay speed index put T20 + T21 in the "backend" lane / "b-backend"
    # barrier.
    assert g2.lane_counts == {"backend": 2}
    assert g2.barrier_counts == {"b-backend": 2}
    # max risk / verification cost come from the overlay metadata.
    assert g2.max_commit_risk == 3
    assert g2.max_verification_cost == 2
    assert g2.max_dependency_depth == 4
    # write-set count comes from the overlay write_sets (pkg/a20.py, pkg/a21.py).
    assert g2.write_set_count == 2
    assert g2.repo_count == 1  # both under "pkg/"


# ════════════════════════════════════════════════════════════════════════════
# Completed-group identity + active/incomplete exclusion
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fully_proven_group_is_completed_with_durations(mq_conn) -> None:
    """A group with a ``dag-group`` checkpoint AND a fully proven merge-queue
    lane is ``completed`` and carries ``checkpoint_duration_h`` /
    ``hours_per_task`` (doc 09 § "Scheduler Metrics And Cap Rules")."""

    await _insert_feature(mq_conn, "feat-done")
    await _seed_completed_group(
        mq_conn, "feat-done", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=4),
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-done", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert g0.completed is True
    assert g0.active is False
    assert g0.state == "completed"
    # checkpoint_duration_h = checkpointed_at - first_attempt_at = 4h.
    assert g0.checkpoint_duration_h == pytest.approx(4.0)
    assert g0.hours_per_task == pytest.approx(4.0)  # 4h / 1 task
    assert g0.tasks_per_hour == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_active_group_excluded_from_throughput(mq_conn) -> None:
    """A group still in flight (no checkpoint) is ``completed=False``,
    ``active=True``, and carries NO ``checkpoint_duration_h`` /
    ``hours_per_task`` — STATUS-only, excluded from completed throughput
    (doc 09 § "Adaptive Sizing Data Flow" step 2)."""

    await _insert_feature(mq_conn, "feat-active")
    # Group 0 completed.
    await _seed_completed_group(
        mq_conn, "feat-active", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    # Group 1 is in flight: an attempt + a gate, but NO checkpoint.
    await _insert_attempt(
        mq_conn, "feat-active", key="a1-T10", group_idx=1,
        created_at=_NOW + timedelta(hours=3),
    )
    await _insert_evidence(
        mq_conn, "feat-active", key="gate-1", kind="deterministic_gate",
        group_idx=1,
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-active", base_dag=_BASE_DAG
    )
    by_group = {m.group_idx: m for m in metrics}
    assert by_group[1].completed is False
    assert by_group[1].active is True
    assert by_group[1].state == "active"
    # The active group carries NO completed-throughput numbers.
    assert by_group[1].checkpoint_duration_h is None
    assert by_group[1].hours_per_task is None
    assert by_group[1].tasks_per_hour is None
    assert by_group[1].checkpointed_at is None


@pytest.mark.asyncio
async def test_checkpoint_artifact_without_merge_proof_is_not_completed(
    mq_conn,
) -> None:
    """A ``dag-group`` checkpoint artifact while the merge-queue lane has only
    reached ``integrated`` (committed but not checkpointed — no
    ``checkpoint_projection_id``, no checkpoint evidence) does NOT make a group
    ``completed`` — doc 09 requires the checkpoint be "linked to merge, commit,
    no-dirty, and gate evidence"."""

    await _insert_feature(mq_conn, "feat-half")
    await _insert_attempt(mq_conn, "feat-half", key="a0", group_idx=0)
    await _insert_checkpoint_artifact(mq_conn, "feat-half", group_idx=0)
    # A merge-queue lane that reached `integrated` (merge + commit + post-apply
    # proof) but NOT `done` — no checkpoint projection / checkpoint evidence.
    pre_queue = await _insert_evidence(
        mq_conn, "feat-half", key="pq0", kind="gate_request", group_idx=0,
    )
    merge_proof = await _insert_evidence(
        mq_conn, "feat-half", key="mp0", kind="merge_proof", group_idx=0,
    )
    commit_proof = await _insert_evidence(
        mq_conn, "feat-half", key="cp0", kind="commit_proof", group_idx=0,
    )
    post_apply = await _insert_evidence(
        mq_conn, "feat-half", key="pa0", kind="merge_gate", group_idx=0,
    )
    await _insert_merge_queue_item(
        mq_conn, "feat-half", key="mq0", group_idx=0, status="integrated",
        merge_proof_evidence_id=merge_proof,
        commit_proof_evidence_id=commit_proof,
        post_apply_gate_evidence_id=post_apply,
        pre_queue_gate_evidence_id=pre_queue,
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-half", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    # No checkpoint_projection_id and no checkpoint evidence -> not completed.
    assert g0.completed is False
    assert g0.checkpoint_projection_id is None


# ════════════════════════════════════════════════════════════════════════════
# data_quality_flags on missing evidence-link categories
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_missing_evidence_links_set_data_quality_flags(mq_conn) -> None:
    """A group with NO typed attempt / gate / merge-or-checkpoint / projection
    evidence sets every ``data_quality_flag`` (doc 09 § "Scheduler Feedback
    Schema": missing links "force ``SchedulerFeedback.data_quality`` away from
    ``sufficient``")."""

    await _insert_feature(mq_conn, "feat-flags")
    # An overlay so group 2 is in the window even with zero evidence.
    overlay = _overlay("feat-flags")
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-flags", base_dag=_BASE_DAG, overlay=overlay
    )
    g2 = next(m for m in metrics if m.group_idx == 2)
    assert "missing_typed_attempt_evidence" in g2.data_quality_flags
    assert "missing_gate_evidence" in g2.data_quality_flags
    assert "missing_merge_or_checkpoint_evidence" in g2.data_quality_flags
    assert "missing_projection_lineage" in g2.data_quality_flags


@pytest.mark.asyncio
async def test_partial_evidence_sets_only_missing_flags(mq_conn) -> None:
    """A group with typed attempts + a gate but no merge-queue item / no
    projection lineage sets ONLY the two missing-category flags."""

    await _insert_feature(mq_conn, "feat-partial")
    await _insert_attempt(mq_conn, "feat-partial", key="a0", group_idx=0)
    await _insert_evidence(
        mq_conn, "feat-partial", key="gate0", kind="deterministic_gate",
        group_idx=0,
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-partial", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert "missing_typed_attempt_evidence" not in g0.data_quality_flags
    assert "missing_gate_evidence" not in g0.data_quality_flags
    assert "missing_merge_or_checkpoint_evidence" in g0.data_quality_flags
    assert "missing_projection_lineage" in g0.data_quality_flags


@pytest.mark.asyncio
async def test_full_evidence_links_yield_no_data_quality_flags(mq_conn) -> None:
    """A group with typed attempt + gate + merge-queue + projection-lineage
    evidence carries an EMPTY ``data_quality_flags`` (it is usable for sizing)."""

    await _insert_feature(mq_conn, "feat-clean")
    await _seed_completed_group(
        mq_conn, "feat-clean", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    # A compatibility-projection row encoding group 0 (dag-group:0).
    await mq_conn.execute(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, request_digest) "
        "VALUES ($1, 'jr-proj', 'group_checkpoint', 'succeeded', 'rd')",
        "feat-clean",
    )
    jr_id = await mq_conn.fetchval(
        "SELECT id FROM execution_journal_rows WHERE idempotency_key='jr-proj'"
    )
    art_id = await mq_conn.fetchval(
        "SELECT id FROM artifacts WHERE feature_id=$1 AND key='dag-group:0'",
        "feat-clean",
    )
    await mq_conn.execute(
        "INSERT INTO execution_artifact_projections "
        "(feature_id, typed_row_id, artifact_id, projection_key, "
        " projection_kind, projection_sha256, idempotency_key) "
        "VALUES ($1,$2,$3,'dag-group:0','group_checkpoint','psha',"
        " 'idem-proj-0')",
        "feat-clean",
        jr_id,
        art_id,
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-clean", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert g0.data_quality_flags == []
    assert g0.compatibility_projection_ids  # the projection id was joined


# ════════════════════════════════════════════════════════════════════════════
# Typed failure / repair classification
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_failure_classes_bucket_into_doc09_counters(mq_conn) -> None:
    """Typed failures classify into the doc-09 counters by ``failure_class``:
    ``commit_hygiene`` -> commit_failures, ``merge_conflict`` -> merge_conflicts,
    a runtime class -> runtime_failures, a workspace class -> workspace_failures,
    ``stale_projection`` -> stale_projection_repairs."""

    await _insert_feature(mq_conn, "feat-fc")
    await _insert_attempt(mq_conn, "feat-fc", key="a0", group_idx=0)
    failure_cases = [
        ("commit_hygiene", "commit_hook_failed"),
        ("merge_conflict", "rebase_conflict"),
        ("runtime_provider", "provider_internal_error"),
        ("acl_workability", "unwritable_runtime_path"),
        ("stale_projection", "stale_dag_task"),
    ]
    for idx, (failure_class, failure_type) in enumerate(failure_cases):
        await _insert_evidence(
            mq_conn, "feat-fc", key=f"fail-{idx}",
            kind="runtime_failure_context", group_idx=0,
            failure_id=5000 + idx,
            payload='{"failure_class":"%s","failure_type":"%s"}'
            % (failure_class, failure_type),
        )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-fc", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert g0.commit_failures == 1
    assert g0.merge_conflicts == 1
    assert g0.runtime_failures == 1
    assert g0.workspace_failures == 1
    assert g0.stale_projection_repairs == 1
    # All five typed failure ids were collected.
    assert len(g0.failure_ids) == 5


@pytest.mark.asyncio
async def test_repair_kinds_split_product_vs_workflow(mq_conn) -> None:
    """A ``product`` repair counts toward ``product_repair_cycles``; a
    non-product repair (contract / workspace / commit_hygiene / …) counts
    toward ``workflow_repair_cycles`` (doc 09 § "Scheduler Metrics And Cap
    Rules")."""

    await _insert_feature(mq_conn, "feat-rk")
    await _insert_attempt(mq_conn, "feat-rk", key="a0", group_idx=0)
    await _insert_evidence(
        mq_conn, "feat-rk", key="rep-prod", kind="repair_outcome", group_idx=0,
        payload='{"repair_kind":"product"}',
    )
    await _insert_evidence(
        mq_conn, "feat-rk", key="rep-contract", kind="repair_request",
        group_idx=0, payload='{"repair_kind":"contract"}',
    )
    await _insert_evidence(
        mq_conn, "feat-rk", key="rep-ws", kind="repair_outcome", group_idx=0,
        payload='{"repair_kind":"workspace"}',
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-rk", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert g0.product_repair_cycles == 1
    assert g0.workflow_repair_cycles == 2  # contract + workspace
    assert g0.product_repair_cycles_per_task == pytest.approx(1.0)
    assert g0.workflow_repair_cycles_per_task == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_verify_and_expanded_verify_counts(mq_conn) -> None:
    """``raw_verifier`` / ``deterministic_gate`` nodes count as verifications;
    ``expanded_lens`` nodes count as expanded verifications."""

    await _insert_feature(mq_conn, "feat-vc")
    await _insert_attempt(mq_conn, "feat-vc", key="a0", group_idx=0)
    await _insert_evidence(
        mq_conn, "feat-vc", key="rv", kind="raw_verifier", group_idx=0,
    )
    await _insert_evidence(
        mq_conn, "feat-vc", key="dg", kind="deterministic_gate", group_idx=0,
    )
    await _insert_evidence(
        mq_conn, "feat-vc", key="el1", kind="expanded_lens", group_idx=0,
    )
    await _insert_evidence(
        mq_conn, "feat-vc", key="el2", kind="expanded_lens", group_idx=0,
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-vc", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert g0.verify_count == 2  # raw_verifier + deterministic_gate
    assert g0.expanded_verify_count == 2


@pytest.mark.asyncio
async def test_queue_retries_and_merge_conflict_lanes(mq_conn) -> None:
    """``failed`` / ``poisoned`` merge-queue lanes count as queue retries; a
    ``poisoned`` lane is also a merge conflict; ``tail_risks`` reflects it."""

    await _insert_feature(mq_conn, "feat-qr")
    await _insert_attempt(mq_conn, "feat-qr", key="a0", group_idx=0)
    await _insert_merge_queue_item(
        mq_conn, "feat-qr", key="mq-fail", group_idx=0, status="failed",
    )
    await _insert_merge_queue_item(
        mq_conn, "feat-qr", key="mq-poison", group_idx=0, status="poisoned",
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-qr", base_dag=_BASE_DAG
    )
    g0 = next(m for m in metrics if m.group_idx == 0)
    assert g0.queue_retries == 2
    assert g0.merge_conflicts >= 1  # the poisoned lane
    assert "merge_queue_retry_loop" in g0.tail_risks
    assert "merge_conflict" in g0.tail_risks


# ════════════════════════════════════════════════════════════════════════════
# metric_id determinism
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_metric_id_is_deterministic_across_builds(mq_conn) -> None:
    """The same DB state yields byte-identical ``metric_id``s on a re-build —
    ``derive_metric_id`` sorts ``task_ids`` / ``evidence_ids`` before hashing
    so row-fetch ordering cannot perturb it."""

    await _insert_feature(mq_conn, "feat-det")
    await _seed_completed_group(
        mq_conn, "feat-det", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    first = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-det", base_dag=_BASE_DAG
    )
    second = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-det", base_dag=_BASE_DAG
    )
    assert [m.metric_id for m in first] == [m.metric_id for m in second]
    assert [m.model_dump(mode="json") for m in first] == [
        m.model_dump(mode="json") for m in second
    ]
    # The metric_id is a 24-hex digest.
    assert all(len(m.metric_id) == 24 for m in first)


@pytest.mark.asyncio
async def test_metric_id_differs_for_different_groups(mq_conn) -> None:
    """Two groups produce distinct ``metric_id``s (group_idx is in the hash)."""

    await _insert_feature(mq_conn, "feat-det2")
    await _seed_completed_group(
        mq_conn, "feat-det2", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    await _seed_completed_group(
        mq_conn, "feat-det2", group_idx=1, task_ids=["T10"],
        first_attempt_at=_NOW + timedelta(hours=3),
        checkpointed_at=_NOW + timedelta(hours=7),
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-det2", base_dag=_BASE_DAG
    )
    ids = [m.metric_id for m in metrics]
    assert len(ids) == len(set(ids))


# ════════════════════════════════════════════════════════════════════════════
# Window: first post-regroup group through the high-water checkpoint
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_window_spans_first_group_through_high_water(mq_conn) -> None:
    """The metric set runs from the first group (0 with no overlay) through the
    high-water checkpoint + the active group (doc 09 § "Adaptive Sizing Data
    Flow" step 2)."""

    await _insert_feature(mq_conn, "feat-win")
    # Groups 0 + 1 completed; group 2 active.
    await _seed_completed_group(
        mq_conn, "feat-win", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    await _seed_completed_group(
        mq_conn, "feat-win", group_idx=1, task_ids=["T10"],
        first_attempt_at=_NOW + timedelta(hours=3),
        checkpointed_at=_NOW + timedelta(hours=8),
    )
    await _insert_attempt(
        mq_conn, "feat-win", key="a2", group_idx=2,
        created_at=_NOW + timedelta(hours=8),
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-win", base_dag=_BASE_DAG
    )
    groups = sorted(m.group_idx for m in metrics)
    # high-water checkpoint is group 1; the window includes the active group 2.
    assert groups == [0, 1, 2]
    by_group = {m.group_idx: m for m in metrics}
    assert by_group[0].completed and by_group[1].completed
    assert by_group[2].active and not by_group[2].completed


@pytest.mark.asyncio
async def test_checkpoint_duration_uses_previous_checkpoint_basis(
    mq_conn,
) -> None:
    """``checkpoint_duration_h`` of a later group measures from the PREVIOUS
    group's checkpoint (doc 09 formula: ``checkpointed_at - max(previous_
    checkpointed_at, first_group_attempt_at)``)."""

    await _insert_feature(mq_conn, "feat-prev")
    await _seed_completed_group(
        mq_conn, "feat-prev", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    # Group 1: its first attempt is BEFORE group 0's checkpoint, so the basis
    # is group 0's checkpoint (the later of the two).
    await _seed_completed_group(
        mq_conn, "feat-prev", group_idx=1, task_ids=["T10"],
        first_attempt_at=_NOW + timedelta(hours=1),
        checkpointed_at=_NOW + timedelta(hours=9),
    )
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-prev", base_dag=_BASE_DAG
    )
    by_group = {m.group_idx: m for m in metrics}
    # group 1 checkpoint_duration_h = 9h - max(3h, 1h) = 6h.
    assert by_group[1].checkpoint_duration_h == pytest.approx(6.0)


# ════════════════════════════════════════════════════════════════════════════
# SAFETY — 09d-1 NEVER writes a row / artifact / active marker
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_builder_never_writes_any_row_or_artifact(mq_conn) -> None:
    """09d-1 SAFETY PROPERTY: the metric builder is a pure read path. It writes
    NO ``execution_scheduler_feedback`` row, NO ``artifacts`` row (least of all
    a ``dag-regroup-active:*`` marker), NO ``execution_regroup_overlays`` row,
    NO ``events`` row, NO ``evidence_nodes`` row. The recommendation + the
    typed ``SchedulerFeedback`` / ``review:dag-sizing:*`` projection are 09d-2's
    concern; an active marker is NEVER written by the scheduler path at all."""

    await _insert_feature(mq_conn, "feat-safe")
    await _seed_completed_group(
        mq_conn, "feat-safe", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )

    async def _counts() -> dict[str, int]:
        out: dict[str, int] = {}
        for table in (
            "execution_scheduler_feedback",
            "execution_regroup_overlays",
            "execution_regroup_validations",
            "artifacts",
            "events",
            "evidence_nodes",
            "execution_journal_rows",
            "merge_queue_items",
        ):
            out[table] = int(
                await mq_conn.fetchval(f"SELECT count(*) FROM {table}")
            )
        return out

    before = await _counts()
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-safe", base_dag=_BASE_DAG
    )
    after = await _counts()
    # NOT ONE row changed in ANY table — the builder only ran SELECTs.
    assert before == after
    # And specifically: zero active-marker artifacts exist (the builder cannot
    # create one; only activation can).
    assert (
        int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE "
                "'dag-regroup-active:%'"
            )
        )
        == 0
    )
    # The builder did its job — it returned typed metrics.
    assert metrics and all(isinstance(m, SchedulerGroupMetric) for m in metrics)


@pytest.mark.asyncio
async def test_builder_returns_metrics_for_freshly_staged_overlay(mq_conn) -> None:
    """With a staged overlay but zero typed evidence the builder still returns a
    metric set (every group STATUS-only) — it never silently returns empty."""

    await _insert_feature(mq_conn, "feat-fresh")
    overlay = _overlay("feat-fresh")
    metrics = await build_scheduler_group_metrics(
        mq_conn, feature_id="feat-fresh", base_dag=_BASE_DAG, overlay=overlay
    )
    assert metrics  # not empty
    for m in metrics:
        assert m.completed is False
        assert m.state in {"pending", "active"}
        assert m.overlay_id == "ov-09d1"
