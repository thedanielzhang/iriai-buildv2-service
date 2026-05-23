"""Slice 09c-1 tests — typed regroup-overlay activation + rollback.

These exercise
:func:`iriai_build_v2.workflows.develop.execution.regroup_overlay_activation.activate_overlay`
and :func:`...regroup_overlay_activation.rollback_overlay`. They run against a
real Postgres database (the ``mq_conn`` / ``mq_dsn`` fixtures in this
directory's conftest) because activation / rollback are a single store
transaction over the typed overlay row + the ``artifacts`` / ``events`` tables
under the feature advisory lock. They skip cleanly when no Postgres is
reachable.

Coverage (per the Slice 09c brief):

- activation happy path (canonical + rollback + active-marker projections +
  the typed status flip + the typed event, all atomic);
- each forbidden-artifact / event / typed-row rejection from doc 09
  § "Activation And Rollback Constraints";
- the 09b-2 reviewer-watch item 1 conflict path (a corrected overlay
  re-validating to a different digest -> a fresh overlay is re-staged);
- activation idempotency over the same overlay / rejection of a different
  overlay over the same active suffix;
- rollback before the first derived wave (allowed) and after it (rejected),
  for every forbidden first-wave / offset class;
- rollback marker-mismatch fail-closed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import asyncpg
import pytest

from iriai_build_v2.execution_control.regroup_overlay_store import (
    RegroupOverlayStore,
)
from iriai_build_v2.models.outputs import ImplementationDAG, ImplementationTask
from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
    OverlayBarrier,
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupOverlay,
    RegroupRollbackPlan,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay_activation import (
    OverlayConflictNeedsFreshOverlay,
    RegroupActivationRejected,
    RegroupRollbackRejected,
    activate_overlay,
    build_canonical_projection,
    rollback_overlay,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
    OverlayValidationContext,
    validate_overlay,
)

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


# ── base DAG fixture (same 4-group shape as the 09b-2 validation suite) ─────


def _base_task(task_id: str, *, deps: list[str], files: list[str]) -> ImplementationTask:
    return ImplementationTask(
        id=task_id,
        name=f"task {task_id}",
        description=f"do {task_id}",
        files=files,
        dependencies=deps,
        team=0,
    )


def _base_dag() -> ImplementationDAG:
    return ImplementationDAG(
        tasks=[
            _base_task("T00", deps=[], files=["a0.py"]),
            _base_task("T10", deps=["T00"], files=["a1.py"]),
            _base_task("T20", deps=["T10"], files=["a20.py"]),
            _base_task("T21", deps=["T10"], files=["a21.py"]),
            _base_task("T30", deps=["T20"], files=["a30.py"]),
        ],
        num_teams=1,
        execution_order=[["T00"], ["T10"], ["T20", "T21"], ["T30"]],
        complete=True,
    )


_BASE_DAG = _base_dag()
_BASE_DAG_JSON = _BASE_DAG.model_dump_json()


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


async def _insert_base_dag(
    conn: asyncpg.Connection, feature_id: str, *, key: str = "dag"
) -> tuple[int, str]:
    artifact_id = await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value) VALUES ($1,$2,$3) "
        "RETURNING id",
        feature_id,
        key,
        _BASE_DAG_JSON,
    )
    return int(artifact_id), hashlib.sha256(_BASE_DAG_JSON.encode("utf-8")).hexdigest()


async def _insert_artifact(
    conn: asyncpg.Connection, feature_id: str, key: str, value: str = "{}"
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


# ── overlay builder ─────────────────────────────────────────────────────────


def _fingerprints() -> dict[str, str]:
    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _task_definition_fingerprint,
    )

    by_id = {t.id: t for t in _BASE_DAG.tasks}
    return {tid: _task_definition_fingerprint(by_id[tid]) for tid in ("T20", "T21", "T30")}


def _valid_overlay(
    *,
    feature_id: str,
    base_dag_artifact_id: int,
    base_dag_sha256: str,
    overlay_id: str = "overlay-09c1",
) -> RegroupOverlay:
    """A fully valid overlay over the base DAG suffix [2, 3].

    ``_canonical_overlay_sha`` excludes ``activation_contract.required_overlay_
    sha256`` (a self-reference) so the sha is a single-pass fixed point.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _canonical_overlay_sha,
    )

    derived = [["T20", "T21"], ["T30"]]
    first_wave = derived[0]
    base = RegroupOverlay(
        overlay_id=overlay_id,
        overlay_slug="g2-g3",
        feature_id=feature_id,
        status="staged",
        artifact_key="dag-regroup:g2-g3",
        source_dag_key="dag",
        base_dag_artifact_id=base_dag_artifact_id,
        base_dag_sha256=base_dag_sha256,
        checkpointed_group=1,
        group_idx_offset=2,
        last_original_group=3,
        original_execution_order=[["T20", "T21"], ["T30"]],
        derived_execution_order=derived,
        original_to_new_group_mapping={2: [2], 3: [3]},
        task_definition_fingerprints=_fingerprints(),
        remaining_dependency_edges={"T20": [], "T21": [], "T30": ["T20"]},
        barriers=[
            OverlayBarrier(
                barrier_id="b-backend",
                task_ids=["T20", "T21", "T30"],
                hard=True,
                source="task_contract",
            )
        ],
        write_sets={"T20": ["a20.py"], "T21": ["a21.py"], "T30": ["a30.py"]},
        speed_index={
            "T20": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-backend"),
            "T21": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-backend"),
            "T30": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-backend"),
        },
        activation_contract=RegroupActivationContract(
            required_checkpoint_key="dag-group:1",
            forbidden_checkpoint_key="dag-group:2",
            forbidden_first_wave_task_keys=sorted(
                f"dag-task:{tid}" for tid in first_wave
            ),
            forbidden_group_artifact_prefixes=["dag-verify:g2"],
            forbidden_group_event_idx=2,
            required_base_dag_artifact_id=base_dag_artifact_id,
            required_base_dag_sha256=base_dag_sha256,
            required_overlay_sha256="PLACEHOLDER",
        ),
        rollback_plan=RegroupRollbackPlan(
            restore_source_dag_key="dag",
            restore_from_checkpoint_group=1,
            rollback_marker_key="dag-regroup-rollback:g2-g3",
            allowed_until_group_idx=2,
            forbidden_started_keys=sorted(f"dag-task:{tid}" for tid in first_wave),
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
        overlay_sha256="PLACEHOLDER",
        validation_digest="valdig",
    )
    canonical = _canonical_overlay_sha(base)
    contract = base.activation_contract.model_copy(
        update={"required_overlay_sha256": canonical}
    )
    return base.model_copy(
        update={"overlay_sha256": canonical, "activation_contract": contract}
    )


async def _stage_validated_overlay(
    conn: asyncpg.Connection,
    store: RegroupOverlayStore,
    *,
    feature_id: str,
    overlay_id: str = "overlay-09c1",
) -> tuple[RegroupOverlay, int, int, str]:
    """Insert + validate a staged overlay; return (overlay, row_id, dag_id, sha).

    After this the overlay row carries ``latest_successful_validation_id`` and
    ``validation_digest`` (``record_validation`` advanced them) so activation
    can read them.
    """

    dag_id, sha = await _insert_base_dag(conn, feature_id)
    overlay = _valid_overlay(
        feature_id=feature_id,
        base_dag_artifact_id=dag_id,
        base_dag_sha256=sha,
        overlay_id=overlay_id,
    )
    row_id = await store.insert_overlay(overlay)
    result = await validate_overlay(
        overlay,
        OverlayValidationContext(
            feature_id=feature_id,
            boundary_checkpoint_exists=False,
            checkpointed_group_exists=True,
            overlay_row_id=row_id,
        ),
        store,
        activation_check=False,
        persist=True,
    )
    assert result.valid, result.reason
    return overlay, row_id, dag_id, sha


# ════════════════════════════════════════════════════════════════════════════
# Activation — happy path
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_activation_happy_path_writes_all_projections_atomically(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-act")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-act"
    )
    # The boundary checkpoint dag-group:1 must exist.
    await _insert_artifact(mq_conn, "feat-act", "dag-group:1", '{"ok":true}')

    result = await activate_overlay(
        store, feature_id="feat-act", overlay_id=overlay.overlay_id, reason="go"
    )

    # The typed overlay row flipped staged -> active.
    row = await mq_conn.fetchrow(
        "SELECT status, activated_at, active_marker_projection_id, "
        "compatibility_artifact_ids FROM execution_regroup_overlays WHERE id = $1",
        row_id,
    )
    assert row["status"] == "active"
    assert row["activated_at"] is not None
    assert row["active_marker_projection_id"] == result.active_marker_artifact_id

    # All three compatibility projections were written.
    assert await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE id = $1", result.canonical_artifact_id
    )
    assert await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE id = $1", result.rollback_artifact_id
    )
    marker_value = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE id = $1",
        result.active_marker_artifact_id,
    )
    marker = json.loads(marker_value)
    assert marker["status"] == "active"
    # P3-9: the marker references the just-written canonical artifact body sha.
    canonical_value = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE id = $1", result.canonical_artifact_id
    )
    assert marker["canonical_sha256"] == hashlib.sha256(
        canonical_value.encode("utf-8")
    ).hexdigest()
    assert marker["canonical_artifact_id"] == result.canonical_artifact_id
    assert marker["rollback_artifact_id"] == result.rollback_artifact_id

    # The typed activation event was written.
    event = await mq_conn.fetchrow(
        "SELECT event_type, metadata FROM events WHERE id = $1",
        result.activation_event_id,
    )
    assert event["event_type"] == "dag_regroup_overlay_activated"

    # The canonical projection round-trips through DerivedDAGArtifact.
    from iriai_build_v2.models.outputs import DerivedDAGArtifact

    DerivedDAGArtifact.model_validate_json(canonical_value)


@pytest.mark.asyncio
async def test_activation_reads_latest_successful_validation_from_row(mq_conn) -> None:
    """Reviewer-watch item 3: activation READS the overlay row's recorded
    latest_successful_validation_id / validation_digest, not re-derives."""

    await _insert_feature(mq_conn, "feat-rd")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-rd"
    )
    await _insert_artifact(mq_conn, "feat-rd", "dag-group:1")
    row_before = await mq_conn.fetchrow(
        "SELECT latest_successful_validation_id, validation_digest "
        "FROM execution_regroup_overlays WHERE id = $1",
        row_id,
    )
    assert row_before["latest_successful_validation_id"] is not None

    result = await activate_overlay(
        store, feature_id="feat-rd", overlay_id=overlay.overlay_id
    )
    # The marker's validation_digest equals the row's recorded digest.
    assert result.validation_digest == row_before["validation_digest"]
    assert result.active_marker.validation_digest == row_before["validation_digest"]


@pytest.mark.asyncio
async def test_activation_idempotent_for_same_overlay(mq_conn) -> None:
    """Re-activating the same already-active overlay is accepted (no 2nd flip)."""

    await _insert_feature(mq_conn, "feat-idem")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-idem"
    )
    await _insert_artifact(mq_conn, "feat-idem", "dag-group:1")
    await activate_overlay(
        store, feature_id="feat-idem", overlay_id=overlay.overlay_id
    )
    # A second activate: the overlay is now `active`, not `staged` -> rejected
    # fail-closed (the doc-09 contract: activation flips a STAGED row).
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-idem", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_overlay_not_staged"


@pytest.mark.asyncio
async def test_activation_rejects_different_overlay_over_active_suffix(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-2ov")
    store = RegroupOverlayStore(mq_conn)
    overlay_a, _, dag_id, sha = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-2ov", overlay_id="overlay-A"
    )
    await _insert_artifact(mq_conn, "feat-2ov", "dag-group:1")
    await activate_overlay(store, feature_id="feat-2ov", overlay_id="overlay-A")

    # A different staged overlay for the same feature.
    overlay_b = _valid_overlay(
        feature_id="feat-2ov",
        base_dag_artifact_id=dag_id,
        base_dag_sha256=sha,
        overlay_id="overlay-B",
    )
    row_b = await store.insert_overlay(overlay_b)
    await validate_overlay(
        overlay_b,
        OverlayValidationContext(
            feature_id="feat-2ov", overlay_row_id=row_b,
        ),
        store,
        persist=True,
    )
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(store, feature_id="feat-2ov", overlay_id="overlay-B")
    assert exc.value.reason == "dag_regroup_other_overlay_active"


# ════════════════════════════════════════════════════════════════════════════
# Activation — each forbidden-artifact / event / typed-row rejection
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_activation_rejects_missing_boundary_checkpoint(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-nocp")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-nocp"
    )
    # dag-group:1 (the boundary checkpoint) intentionally NOT inserted.
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-nocp", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_boundary_checkpoint_missing"


@pytest.mark.asyncio
async def test_activation_rejects_offset_checkpoint_present(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-offcp")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-offcp"
    )
    await _insert_artifact(mq_conn, "feat-offcp", "dag-group:1")
    # dag-group:2 (the offset group) already checkpointed -> stale overlay.
    await _insert_artifact(mq_conn, "feat-offcp", "dag-group:2")
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-offcp", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_boundary_checkpoint_exists"


@pytest.mark.asyncio
async def test_activation_rejects_first_wave_task_artifact(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-task")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-task"
    )
    await _insert_artifact(mq_conn, "feat-task", "dag-group:1")
    # A first-derived-wave task (T20) already has a dag-task:* artifact.
    await _insert_artifact(mq_conn, "feat-task", "dag-task:T20")
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-task", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_first_wave_task_started"


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "dag-verify:g2:initial",
        "dag-commit-failure:g2:implementation",
        "dag-writeability-preflight:g2:initial",
        "dag-merge:g2:apply",
        "dag-repair:g2:request",
    ],
)
@pytest.mark.asyncio
async def test_activation_rejects_group_scoped_artifact(
    mq_conn, forbidden_key: str
) -> None:
    feature_id = "feat-grp-" + forbidden_key.split(":")[0]
    await _insert_feature(mq_conn, feature_id)
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id=feature_id
    )
    await _insert_artifact(mq_conn, feature_id, "dag-group:1")
    await _insert_artifact(mq_conn, feature_id, forbidden_key)
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id=feature_id, overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_group_artifact_exists"


@pytest.mark.asyncio
async def test_activation_rejects_non_regroup_group_event(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-ev")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-ev"
    )
    await _insert_artifact(mq_conn, "feat-ev", "dag-group:1")
    # A non-regroup event with metadata.group_idx == 2 (the offset).
    await mq_conn.execute(
        "INSERT INTO events (feature_id, event_type, source, content, metadata) "
        "VALUES ($1, 'dag_group_started', 'implementation', '', $2::jsonb)",
        "feat-ev",
        json.dumps({"group_idx": "2"}),
    )
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-ev", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_non_regroup_group_event_exists"


@pytest.mark.asyncio
async def test_activation_allows_regroup_group_event(mq_conn) -> None:
    """A `dag_regroup*` event for the offset group is the activation's own
    lineage and must NOT block activation."""

    await _insert_feature(mq_conn, "feat-rev")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-rev"
    )
    await _insert_artifact(mq_conn, "feat-rev", "dag-group:1")
    await mq_conn.execute(
        "INSERT INTO events (feature_id, event_type, source, content, metadata) "
        "VALUES ($1, 'dag_regroup_overlay_staged', 'implementation', '', "
        "$2::jsonb)",
        "feat-rev",
        json.dumps({"group_idx": "2"}),
    )
    # Should succeed — the dag_regroup* event is excluded.
    result = await activate_overlay(
        store, feature_id="feat-rev", overlay_id=overlay.overlay_id
    )
    assert result.overlay_id == overlay.overlay_id


@pytest.mark.asyncio
async def test_activation_rejects_typed_attempt_for_offset(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-att")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-att"
    )
    await _insert_artifact(mq_conn, "feat-att", "dag-group:1")
    # A typed execution_journal_rows attempt for group_idx == 2.
    await mq_conn.execute(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, group_idx, "
        " request_digest) "
        "VALUES ($1, 'k-att-2', 'task_attempt', 'started', 2, 'rd')",
        "feat-att",
    )
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-att", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_typed_attempt_exists"


@pytest.mark.asyncio
async def test_activation_rejects_merge_queue_item_for_offset(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-mq")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-mq"
    )
    await _insert_artifact(mq_conn, "feat-mq", "dag-group:1")
    await mq_conn.execute(
        "INSERT INTO merge_queue_items "
        "(feature_id, dag_sha256, group_idx, base_commit, request_digest, "
        " idempotency_key, status) "
        "VALUES ($1, 'd', 2, 'bc', 'rd', 'k-mq-2', 'failed')",
        "feat-mq",
    )
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-mq", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_merge_queue_item_exists"


@pytest.mark.asyncio
async def test_activation_rejects_workspace_snapshot_for_offset(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-ws")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-ws"
    )
    await _insert_artifact(mq_conn, "feat-ws", "dag-group:1")
    journal_id = await mq_conn.fetchval(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, request_digest) "
        "VALUES ($1, 'k-j-ws', 'task_attempt', 'started', 'rd') RETURNING id",
        "feat-ws",
    )
    await mq_conn.execute(
        "INSERT INTO workspace_snapshots "
        "(feature_id, idempotency_key, execution_journal_row_id, group_idx, "
        " snapshot_digest) "
        "VALUES ($1, 'k-ws-2', $2, 2, 'sd')",
        "feat-ws",
        journal_id,
    )
    # The execution_journal_rows row above carries no group_idx, so the
    # workspace_snapshots check (group_idx == 2) is the one that fires.
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-ws", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_workspace_snapshot_exists"


@pytest.mark.asyncio
async def test_activation_rejects_gate_evidence_for_offset(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-ge")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-ge"
    )
    await _insert_artifact(mq_conn, "feat-ge", "dag-group:1")
    await mq_conn.execute(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, group_idx, kind, content_hash) "
        "VALUES ($1, 'k-ge-2', 2, 'deterministic_gate', 'ch')",
        "feat-ge",
    )
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-ge", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_gate_evidence_exists"


@pytest.mark.asyncio
async def test_activation_rollback_leaves_no_partial_row_on_forbidden(mq_conn) -> None:
    """A forbidden-set rejection rolls back the WHOLE transaction — the typed
    overlay row stays `staged` and no projection artifacts are written."""

    await _insert_feature(mq_conn, "feat-atomic")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-atomic"
    )
    await _insert_artifact(mq_conn, "feat-atomic", "dag-group:1")
    await _insert_artifact(mq_conn, "feat-atomic", "dag-task:T20")
    with pytest.raises(RegroupActivationRejected):
        await activate_overlay(
            store, feature_id="feat-atomic", overlay_id=overlay.overlay_id
        )
    # Overlay row still staged.
    status = await mq_conn.fetchval(
        "SELECT status FROM execution_regroup_overlays WHERE id = $1", row_id
    )
    assert status == "staged"
    # No canonical / active-marker projection artifacts.
    for key in ("dag-regroup:g2-g3", "dag-regroup-active:g2-g3"):
        count = await mq_conn.fetchval(
            "SELECT count(*) FROM artifacts WHERE feature_id = $1 AND key = $2",
            "feat-atomic",
            key,
        )
        assert count == 0
    # No activation event.
    ev_count = await mq_conn.fetchval(
        "SELECT count(*) FROM events WHERE feature_id = $1 "
        "AND event_type = 'dag_regroup_overlay_activated'",
        "feat-atomic",
    )
    assert ev_count == 0


@pytest.mark.asyncio
async def test_activation_rejects_unknown_overlay(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-unk")
    store = RegroupOverlayStore(mq_conn)
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-unk", overlay_id="does-not-exist"
        )
    assert exc.value.reason == "dag_regroup_overlay_not_found"


@pytest.mark.asyncio
async def test_activation_revalidates_a_never_validated_overlay(mq_conn) -> None:
    """A staged, structurally-valid, never-validated overlay activates — the
    activation path re-runs the validator itself (step 3)."""

    await _insert_feature(mq_conn, "feat-noval")
    store = RegroupOverlayStore(mq_conn)
    dag_id, sha = await _insert_base_dag(mq_conn, "feat-noval")
    overlay = _valid_overlay(
        feature_id="feat-noval", base_dag_artifact_id=dag_id, base_dag_sha256=sha
    )
    await store.insert_overlay(overlay)  # inserted but NOT pre-validated
    await _insert_artifact(mq_conn, "feat-noval", "dag-group:1")
    result = await activate_overlay(
        store, feature_id="feat-noval", overlay_id=overlay.overlay_id
    )
    assert result.overlay_id == overlay.overlay_id
    # The activation re-validation recorded a passing validation row that
    # advanced the overlay row's latest_successful_validation_id.
    row = await mq_conn.fetchval(
        "SELECT latest_successful_validation_id "
        "FROM execution_regroup_overlays WHERE overlay_id = $1",
        overlay.overlay_id,
    )
    assert row is not None


@pytest.mark.asyncio
async def test_activation_rejects_structurally_invalid_overlay(mq_conn) -> None:
    """A staged overlay that fails the validator (stale base DAG sha) is
    rejected during the activation re-validation."""

    await _insert_feature(mq_conn, "feat-inval")
    store = RegroupOverlayStore(mq_conn)
    dag_id, sha = await _insert_base_dag(mq_conn, "feat-inval")
    # Build the overlay against a WRONG base sha so step 2 of the validator
    # rejects it (dag_regroup_base_dag_hash_mismatch).
    overlay = _valid_overlay(
        feature_id="feat-inval",
        base_dag_artifact_id=dag_id,
        base_dag_sha256="0" * 64,
    )
    await store.insert_overlay(overlay)
    await _insert_artifact(mq_conn, "feat-inval", "dag-group:1")
    with pytest.raises(RegroupActivationRejected) as exc:
        await activate_overlay(
            store, feature_id="feat-inval", overlay_id=overlay.overlay_id
        )
    assert exc.value.reason == "dag_regroup_base_dag_hash_mismatch"


# ════════════════════════════════════════════════════════════════════════════
# Activation — the 09b-2 reviewer-watch item 1 conflict path
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_activation_conflict_restages_fresh_overlay(mq_conn) -> None:
    """Reviewer-watch item 1: a corrected overlay re-validating to a different
    digest -> RegroupOverlayValidationConflict -> a FRESH overlay is staged."""

    await _insert_feature(mq_conn, "feat-conf")
    store = RegroupOverlayStore(mq_conn)
    dag_id, sha = await _insert_base_dag(mq_conn, "feat-conf")
    overlay = _valid_overlay(
        feature_id="feat-conf", base_dag_artifact_id=dag_id, base_dag_sha256=sha
    )
    row_id = await store.insert_overlay(overlay)
    await _insert_artifact(mq_conn, "feat-conf", "dag-group:1")

    # Record a FAILED validation for this overlay_id under a DIFFERENT digest,
    # so the activation's re-validation (which computes the genuine digest)
    # conflicts with the already-recorded digest.
    await store.record_validation(
        feature_id="feat-conf",
        overlay_id=overlay.overlay_id,
        overlay_row_id=row_id,
        valid=False,
        validation_digest="stale-conflicting-digest",
        reason="dag_regroup_some_earlier_failure",
    )

    with pytest.raises(OverlayConflictNeedsFreshOverlay) as exc:
        await activate_overlay(
            store, feature_id="feat-conf", overlay_id=overlay.overlay_id
        )
    assert exc.value.original_overlay_id == overlay.overlay_id
    assert exc.value.fresh_overlay_id != overlay.overlay_id
    # The fresh overlay row durably survives (it was inserted OUTSIDE the
    # rolled-back activation transaction).
    fresh_row = await store.get_overlay_by_overlay_id(
        "feat-conf", exc.value.fresh_overlay_id
    )
    assert fresh_row is not None
    assert fresh_row.status == "staged"
    assert fresh_row.overlay_id == exc.value.fresh_overlay_id
    # The original overlay row is untouched (still staged, not activated).
    original = await store.get_overlay_by_overlay_id(
        "feat-conf", overlay.overlay_id
    )
    assert original is not None
    assert original.status == "staged"


# ════════════════════════════════════════════════════════════════════════════
# Rollback — before the first derived wave (allowed)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rollback_before_first_wave_succeeds(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-rb")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-rb"
    )
    await _insert_artifact(mq_conn, "feat-rb", "dag-group:1")
    await activate_overlay(store, feature_id="feat-rb", overlay_id=overlay.overlay_id)

    result = await rollback_overlay(
        store, feature_id="feat-rb", overlay_id=overlay.overlay_id,
        reason="scheduler-feedback supersedes",
    )
    # The typed row flipped active -> rolled_back.
    row = await mq_conn.fetchrow(
        "SELECT status, rolled_back_at FROM execution_regroup_overlays "
        "WHERE id = $1",
        row_id,
    )
    assert row["status"] == "rolled_back"
    assert row["rolled_back_at"] is not None
    # A NEW status="rolled_back" active marker was written.
    marker_value = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE id = $1",
        result.rolled_back_marker_artifact_id,
    )
    assert json.loads(marker_value)["status"] == "rolled_back"
    # The latest active-marker artifact (highest id) is the rolled_back one.
    latest = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-rb",
        "dag-regroup-active:g2-g3",
    )
    assert json.loads(latest)["status"] == "rolled_back"
    # A typed rollback event was written.
    event = await mq_conn.fetchval(
        "SELECT event_type FROM events WHERE id = $1", result.rollback_event_id
    )
    assert event == "dag_regroup_overlay_rolled_back"
    # The canonical overlay + rollback artifacts are NOT deleted.
    assert await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-rb",
        "dag-regroup:g2-g3",
    ) == 1


@pytest.mark.asyncio
async def test_rollback_requires_reason(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-noreason")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-noreason"
    )
    await _insert_artifact(mq_conn, "feat-noreason", "dag-group:1")
    await activate_overlay(
        store, feature_id="feat-noreason", overlay_id=overlay.overlay_id
    )
    with pytest.raises(RegroupRollbackRejected) as exc:
        await rollback_overlay(
            store, feature_id="feat-noreason", overlay_id=overlay.overlay_id,
            reason="  ",
        )
    assert exc.value.reason == "dag_regroup_rollback_requires_reason"


@pytest.mark.asyncio
async def test_rollback_rejects_non_active_overlay(mq_conn) -> None:
    """A `staged` (never activated) overlay cannot be rolled back."""

    await _insert_feature(mq_conn, "feat-rbstg")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-rbstg"
    )
    with pytest.raises(RegroupRollbackRejected) as exc:
        await rollback_overlay(
            store, feature_id="feat-rbstg", overlay_id=overlay.overlay_id,
            reason="x",
        )
    assert exc.value.reason == "dag_regroup_overlay_not_active"


# ════════════════════════════════════════════════════════════════════════════
# Rollback — after the first derived wave (rejected)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "started_key,expected_reason",
    [
        ("dag-task:T20", "dag_regroup_first_wave_task_started"),
        ("dag-group:2", "dag_regroup_boundary_checkpoint_exists"),
        ("dag-verify:g2:initial", "dag_regroup_group_artifact_exists"),
        ("dag-commit-failure:g2:checkpoint", "dag_regroup_group_artifact_exists"),
    ],
)
@pytest.mark.asyncio
async def test_rollback_rejected_after_first_wave_artifact(
    mq_conn, started_key: str, expected_reason: str
) -> None:
    feature_id = "feat-rbrej-" + started_key.replace(":", "-")
    await _insert_feature(mq_conn, feature_id)
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id=feature_id
    )
    await _insert_artifact(mq_conn, feature_id, "dag-group:1")
    await activate_overlay(
        store, feature_id=feature_id, overlay_id=overlay.overlay_id
    )
    # The first derived wave started AFTER activation.
    await _insert_artifact(mq_conn, feature_id, started_key)

    with pytest.raises(RegroupRollbackRejected) as exc:
        await rollback_overlay(
            store, feature_id=feature_id, overlay_id=overlay.overlay_id,
            reason="too late",
        )
    assert exc.value.reason == expected_reason
    # Rollback rejection leaves the active marker untouched (still active) and
    # the typed row still active — no partial rolled-back projection.
    status = await mq_conn.fetchval(
        "SELECT status FROM execution_regroup_overlays WHERE id = $1", row_id
    )
    assert status == "active"
    latest_marker = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        feature_id,
        "dag-regroup-active:g2-g3",
    )
    assert json.loads(latest_marker)["status"] == "active"


@pytest.mark.asyncio
async def test_rollback_rejected_after_typed_attempt(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-rbatt")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-rbatt"
    )
    await _insert_artifact(mq_conn, "feat-rbatt", "dag-group:1")
    await activate_overlay(
        store, feature_id="feat-rbatt", overlay_id=overlay.overlay_id
    )
    await mq_conn.execute(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, group_idx, "
        " request_digest) "
        "VALUES ($1, 'k-rbatt', 'task_attempt', 'started', 2, 'rd')",
        "feat-rbatt",
    )
    with pytest.raises(RegroupRollbackRejected) as exc:
        await rollback_overlay(
            store, feature_id="feat-rbatt", overlay_id=overlay.overlay_id,
            reason="started",
        )
    assert exc.value.reason == "dag_regroup_typed_attempt_exists"
    assert (
        await mq_conn.fetchval(
            "SELECT status FROM execution_regroup_overlays WHERE id = $1", row_id
        )
        == "active"
    )


@pytest.mark.asyncio
async def test_rollback_rejects_tampered_active_marker(mq_conn) -> None:
    """A marker whose fields disagree with the typed overlay row fails closed."""

    await _insert_feature(mq_conn, "feat-tamper")
    store = RegroupOverlayStore(mq_conn)
    overlay, _, _, _ = await _stage_validated_overlay(
        mq_conn, store, feature_id="feat-tamper"
    )
    await _insert_artifact(mq_conn, "feat-tamper", "dag-group:1")
    await activate_overlay(
        store, feature_id="feat-tamper", overlay_id=overlay.overlay_id
    )
    # Append a tampered active marker (latest row wins) whose base_dag_sha256
    # disagrees with the typed overlay row.
    latest_marker = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-tamper",
        "dag-regroup-active:g2-g3",
    )
    tampered = json.loads(latest_marker)
    tampered["base_dag_sha256"] = "tampered-sha"
    await _insert_artifact(
        mq_conn, "feat-tamper", "dag-regroup-active:g2-g3", json.dumps(tampered)
    )
    with pytest.raises(RegroupRollbackRejected) as exc:
        await rollback_overlay(
            store, feature_id="feat-tamper", overlay_id=overlay.overlay_id,
            reason="x",
        )
    assert exc.value.reason == "dag_regroup_rollback_marker_mismatch"


# ════════════════════════════════════════════════════════════════════════════
# Canonical projection
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_canonical_projection_round_trips(mq_conn) -> None:
    """The canonical projection round-trips through DerivedDAGArtifact and
    carries the overlay identity in speed_index['overlay']."""

    from iriai_build_v2.models.outputs import DerivedDAGArtifact

    overlay = _valid_overlay(
        feature_id="feat-proj", base_dag_artifact_id=1, base_dag_sha256="sha"
    )
    projection = build_canonical_projection(overlay, _BASE_DAG)
    body = projection.model_dump_json()
    reparsed = DerivedDAGArtifact.model_validate_json(body)
    assert reparsed.artifact_key == "dag-regroup:g2-g3"
    # Derived task ids match the suffix; execution_order is the regrouped one.
    assert {t.id for t in reparsed.dag.tasks} == {"T20", "T21", "T30"}
    assert reparsed.dag.execution_order == [["T20", "T21"], ["T30"]]
    # The overlay identity is carried for the validator's step-1 cross-check.
    assert reparsed.speed_index["overlay"]["overlay_id"] == overlay.overlay_id
    assert (
        reparsed.speed_index["overlay"]["overlay_sha256"] == overlay.overlay_sha256
    )
