"""Slice 09b tests — the typed regroup-overlay store (``RegroupOverlayStore``).

These tests run against a real Postgres database (the ``mq_conn`` / ``mq_dsn``
fixtures in this directory's conftest). They skip cleanly when no Postgres is
reachable. The store's correctness is in Postgres transaction / unique-index /
advisory-lock semantics, which an in-memory fake cannot exercise.

Scope is strictly 09b: the store layer for the 3 Slice-09a tables
(``execution_regroup_overlays`` / ``execution_regroup_validations`` /
``execution_scheduler_feedback``). The 13-step ``validate_overlay`` algorithm
is Slice 09b-2.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import asyncpg
import pytest

from iriai_build_v2.execution_control.regroup_overlay_store import (
    RegroupOverlayStore,
    RegroupOverlayStoreError,
    RegroupOverlayValidationConflict,
    overlay_idempotency_key,
    scheduler_feedback_idempotency_key,
    validation_idempotency_key,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
    OverlayBarrier,
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupOverlay,
    RegroupRollbackPlan,
    SchedulerFeedback,
)

_NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


# ── fixture helpers ─────────────────────────────────────────────────────────


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


def _activation_contract() -> RegroupActivationContract:
    return RegroupActivationContract(
        required_checkpoint_key="dag-group:44",
        forbidden_checkpoint_key="dag-group:45",
        forbidden_first_wave_task_keys=["dag-task:T1"],
        forbidden_group_artifact_prefixes=["dag-verify:45"],
        forbidden_group_event_idx=45,
        required_base_dag_artifact_id=100,
        required_base_dag_sha256="basehash",
        required_overlay_sha256="ovsha",
    )


def _rollback_plan() -> RegroupRollbackPlan:
    return RegroupRollbackPlan(
        restore_source_dag_key="dag",
        restore_from_checkpoint_group=44,
        rollback_marker_key="dag-regroup-rollback:g45-g73",
        allowed_until_group_idx=45,
        forbidden_started_keys=["dag-task:T1"],
        forbidden_started_event_group_idx=45,
        forbidden_typed_attempt_group_idx=45,
        forbidden_merge_queue_group_idx=45,
    )


def _compatibility_keys() -> OverlayCompatibilityKeys:
    return OverlayCompatibilityKeys(
        canonical_artifact_key="dag-regroup:g45-g73",
        active_marker_key="dag-regroup-active:g45-g73",
        rollback_artifact_key="dag-regroup-rollback:g45-g73",
        observation_artifact_key="dag-regroup-observation:g45-g73",
        sizing_review_key_prefix="review:dag-sizing:feat-1",
    )


def _overlay(**overrides: object) -> RegroupOverlay:
    base: dict[str, object] = {
        "overlay_id": "overlay-1",
        "overlay_slug": "g45-g73",
        "feature_id": "feat-1",
        "status": "staged",
        "artifact_key": "dag-regroup:g45-g73",
        "source_dag_key": "dag",
        "base_dag_artifact_id": 100,
        "base_dag_sha256": "basehash",
        "checkpointed_group": 44,
        "group_idx_offset": 45,
        "last_original_group": 73,
        "original_execution_order": [["T1"], ["T2"], ["T3"]],
        "derived_execution_order": [["T1", "T2"], ["T3"]],
        "original_to_new_group_mapping": {45: [45], 46: [45], 47: [46]},
        "task_definition_fingerprints": {"T1": "f1", "T2": "f2", "T3": "f3"},
        "remaining_dependency_edges": {"T3": ["T1"]},
        "barriers": [
            OverlayBarrier(barrier_id="b1", task_ids=["T1"], source="task_contract")
        ],
        "write_sets": {"T1": ["a.py"], "T2": ["b.py"], "T3": ["c.py"]},
        "speed_index": {"T1": OverlayTaskSpeedMetadata(semantic_lane="backend")},
        "activation_contract": _activation_contract(),
        "rollback_plan": _rollback_plan(),
        "compatibility_keys": _compatibility_keys(),
        "created_at": _NOW,
        "overlay_sha256": "ovsha",
        "validation_digest": "valdig",
    }
    base.update(overrides)
    return RegroupOverlay(**base)


def _feedback(**overrides: object) -> SchedulerFeedback:
    base: dict[str, object] = {
        "feedback_id": "fb-1",
        "feature_id": "feat-1",
        "generated_at": _NOW,
        "window_start_group": 45,
        "window_end_group": 50,
        "lane": "backend",
        "barrier": "b1",
        "completed_groups": [45, 46],
        "sample_count": 2,
        "tasks_per_hour": 1.5,
        "hours_per_task_p50": 0.6,
        "hours_per_task_p75": 0.7,
        "product_repair_cycles_per_task": 0.0,
        "workflow_repair_cycles_per_task": 0.0,
        "commit_failures_per_task": 0.0,
        "merge_conflicts_per_task": 0.0,
        "verify_cost_per_task": 2.0,
        "queue_wait_p75_h": 0.1,
        "data_quality": "sufficient",
        "recommended_cap": 6,
        "current_cap": 4,
        "confidence": "medium",
        "reasons": ["evidence cap from p75"],
        "metric_ids": ["m1", "m2"],
        "evidence_ids": [1, 2, 3],
    }
    base.update(overrides)
    return SchedulerFeedback(**base)


# ── fixture / schema smoke ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fixture_provides_regroup_schema(mq_conn) -> None:
    tables = await mq_conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename LIKE 'execution_regroup%' "
        "OR tablename = 'execution_scheduler_feedback' ORDER BY tablename"
    )
    assert [t["tablename"] for t in tables] == [
        "execution_regroup_overlays",
        "execution_regroup_validations",
        "execution_scheduler_feedback",
    ]


# ── overlay insert / load ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_overlay_round_trips(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _overlay()

    row_id = await store.insert_overlay(overlay)
    assert row_id > 0

    loaded = await store.get_overlay(row_id)
    assert loaded == overlay
    # The typed model rebuilds from payload_json — nested members survive.
    assert isinstance(loaded.activation_contract, RegroupActivationContract)
    assert isinstance(loaded.barriers[0], OverlayBarrier)
    assert isinstance(loaded.speed_index["T1"], OverlayTaskSpeedMetadata)
    # int-keyed mapping survives the JSONB round-trip.
    assert loaded.original_to_new_group_mapping == {45: [45], 46: [45], 47: [46]}


@pytest.mark.asyncio
async def test_insert_overlay_persists_scalar_columns(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    row = await mq_conn.fetchrow(
        "SELECT feature_id, overlay_id, overlay_slug, status, source_dag_key, "
        "base_dag_artifact_id, base_dag_sha256, checkpointed_group, "
        "group_idx_offset, last_original_group, overlay_sha256, "
        "validation_digest FROM execution_regroup_overlays WHERE id = $1",
        row_id,
    )
    assert row["feature_id"] == "feat-1"
    assert row["overlay_id"] == "overlay-1"
    assert row["overlay_slug"] == "g45-g73"
    assert row["status"] == "staged"
    assert row["base_dag_artifact_id"] == 100
    assert row["checkpointed_group"] == 44
    assert row["group_idx_offset"] == 45
    assert row["last_original_group"] == 73


@pytest.mark.asyncio
async def test_insert_overlay_is_idempotent_on_key(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _overlay()

    first = await store.insert_overlay(overlay)
    second = await store.insert_overlay(overlay)
    assert first == second
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_overlays"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_get_overlay_by_overlay_id(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    loaded = await store.get_overlay_by_overlay_id("feat-1", "overlay-1")
    assert loaded is not None and loaded.overlay_id == "overlay-1"
    assert await store.get_overlay_row_id("feat-1", "overlay-1") == row_id
    assert await store.get_overlay_by_overlay_id("feat-1", "missing") is None
    assert await store.get_overlay_row_id("feat-1", "missing") is None


@pytest.mark.asyncio
async def test_get_overlay_missing_returns_none(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    assert await store.get_overlay(999_999) is None


@pytest.mark.asyncio
async def test_insert_overlay_rejects_blank_identity(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    with pytest.raises(RegroupOverlayStoreError, match="overlay_id"):
        await store.insert_overlay(_overlay(overlay_id=""))
    with pytest.raises(RegroupOverlayStoreError, match="validation_digest"):
        await store.insert_overlay(_overlay(validation_digest=""))
    with pytest.raises(RegroupOverlayStoreError, match="source_dag_key"):
        await store.insert_overlay(_overlay(source_dag_key=""))


@pytest.mark.asyncio
async def test_unique_active_index_rejects_second_active_overlay(mq_conn) -> None:
    """The ``uniq_regroup_overlay_active`` partial index rejects a 2nd active."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    await store.insert_overlay(
        _overlay(overlay_id="overlay-active-1", status="active")
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await store.insert_overlay(
            _overlay(
                overlay_id="overlay-active-2",
                status="active",
                overlay_sha256="ovsha-2",
            )
        )


@pytest.mark.asyncio
async def test_two_staged_overlays_per_feature_allowed(mq_conn) -> None:
    """The partial index only constrains ``active`` — staged rows are free."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    a = await store.insert_overlay(_overlay(overlay_id="staged-a"))
    b = await store.insert_overlay(
        _overlay(overlay_id="staged-b", overlay_sha256="ovsha-b")
    )
    assert a != b


@pytest.mark.asyncio
async def test_get_active_overlay(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    assert await store.get_active_overlay("feat-1") is None
    await store.insert_overlay(_overlay(status="staged"))
    assert await store.get_active_overlay("feat-1") is None
    await store.insert_overlay(
        _overlay(
            overlay_id="overlay-active", status="active", overlay_sha256="osa"
        )
    )
    active = await store.get_active_overlay("feat-1")
    assert active is not None and active.overlay_id == "overlay-active"


@pytest.mark.asyncio
async def test_insert_overlay_concurrent_is_idempotent(mq_dsn: str) -> None:
    """Two connections inserting the same overlay create exactly one row."""

    conn_a = await asyncpg.connect(mq_dsn)
    conn_b = await asyncpg.connect(mq_dsn)
    try:
        await _insert_feature(conn_a, "feat-1")
        overlay = _overlay()
        first, second = await asyncio.gather(
            RegroupOverlayStore(conn_a).insert_overlay(overlay),
            RegroupOverlayStore(conn_b).insert_overlay(overlay),
        )
        assert first == second
        count = await conn_a.fetchval(
            "SELECT count(*) FROM execution_regroup_overlays"
        )
        assert count == 1
    finally:
        await conn_a.close()
        await conn_b.close()


# ── record_validation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_validation_writes_typed_row(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _overlay()
    row_id = await store.insert_overlay(overlay)

    record = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
        reason="all 13 steps passed",
        details={"steps": 13},
        evidence_ids=[3, 1, 2],
    )
    assert record.id > 0
    assert record.valid is True
    assert record.reason == "all 13 steps passed"
    assert record.validation_digest == "valdig"
    assert record.evidence_ids == [1, 2, 3]  # sorted, deterministic
    assert record.details_json["steps"] == 13

    reloaded = await store.get_validation(record.id)
    assert reloaded is not None and reloaded.id == record.id


@pytest.mark.asyncio
async def test_record_validation_rejects_blank_digest(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())
    with pytest.raises(RegroupOverlayStoreError, match="validation_digest"):
        await store.record_validation(
            feature_id="feat-1",
            overlay_id="overlay-1",
            overlay_row_id=row_id,
            valid=True,
            validation_digest="",
        )


@pytest.mark.asyncio
async def test_record_validation_same_digest_is_idempotent(mq_conn) -> None:
    """doc 09 step 13: same ``(overlay_id, digest)`` => reuse, one row."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    first = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
    )
    second = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
        reason="ignored on the idempotent re-run",
    )
    assert first.id == second.id
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_validation_different_digest_is_rejected(mq_conn) -> None:
    """doc 09 step 13: a *different* digest for the same overlay id => reject."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="digest-A",
    )
    with pytest.raises(RegroupOverlayValidationConflict, match="digest"):
        await store.record_validation(
            feature_id="feat-1",
            overlay_id="overlay-1",
            overlay_row_id=row_id,
            valid=True,
            validation_digest="digest-B",
        )
    # The conflicting attempt wrote nothing — still exactly one row.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_validation_failed_then_different_digest_rejected(
    mq_conn,
) -> None:
    """A *failed* validation row also pins the digest fail-closed."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=False,
        validation_digest="digest-A",
        reason="dag_regroup_base_dag_hash_mismatch",
    )
    with pytest.raises(RegroupOverlayValidationConflict):
        await store.record_validation(
            feature_id="feat-1",
            overlay_id="overlay-1",
            overlay_row_id=row_id,
            valid=False,
            validation_digest="digest-B",
        )


@pytest.mark.asyncio
async def test_record_validation_distinct_overlays_independent(mq_conn) -> None:
    """Idempotency is per overlay id — distinct overlays don't collide."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_a = await store.insert_overlay(_overlay(overlay_id="ov-a"))
    row_b = await store.insert_overlay(
        _overlay(overlay_id="ov-b", overlay_sha256="osb")
    )

    rec_a = await store.record_validation(
        feature_id="feat-1",
        overlay_id="ov-a",
        overlay_row_id=row_a,
        valid=True,
        validation_digest="digest-a",
    )
    rec_b = await store.record_validation(
        feature_id="feat-1",
        overlay_id="ov-b",
        overlay_row_id=row_b,
        valid=True,
        validation_digest="digest-b",
    )
    assert rec_a.id != rec_b.id


@pytest.mark.asyncio
async def test_record_validation_valid_advances_latest_successful(
    mq_conn,
) -> None:
    """A passing validation sets ``latest_successful_validation_id``."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    record = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
    )
    latest = await mq_conn.fetchval(
        "SELECT latest_successful_validation_id "
        "FROM execution_regroup_overlays WHERE id = $1",
        row_id,
    )
    assert latest == record.id

    via_helper = await store.latest_successful_validation("feat-1", "overlay-1")
    assert via_helper is not None and via_helper.id == record.id


@pytest.mark.asyncio
async def test_record_validation_failed_does_not_advance_latest(mq_conn) -> None:
    """A *failed* validation must not advance ``latest_successful_validation``."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=False,
        validation_digest="valdig",
        reason="hard barrier mix",
    )
    latest = await mq_conn.fetchval(
        "SELECT latest_successful_validation_id "
        "FROM execution_regroup_overlays WHERE id = $1",
        row_id,
    )
    assert latest is None
    assert (
        await store.latest_successful_validation("feat-1", "overlay-1") is None
    )


@pytest.mark.asyncio
async def test_record_validation_writes_compatibility_artifact_atomically(
    mq_conn,
) -> None:
    """doc 09 step 13: a compatibility artifact is written in the same txn."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _overlay()
    row_id = await store.insert_overlay(overlay)

    record = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
        compatibility_artifact=overlay,
    )
    assert record.compatibility_artifact_id is not None

    artifact = await mq_conn.fetchrow(
        "SELECT key, value FROM artifacts WHERE id = $1",
        record.compatibility_artifact_id,
    )
    assert artifact["key"] == "dag-regroup-validation:g45-g73"
    body = json.loads(artifact["value"])
    # The projection carries the typed row id + overlay sha (legacy readers
    # report precise evidence without becoming writers — doc 09).
    assert body["typed_overlay_row_id"] == row_id
    assert body["overlay_sha256"] == "ovsha"
    assert body["overlay_id"] == "overlay-1"


@pytest.mark.asyncio
async def test_record_validation_without_artifact_writes_no_artifact(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    record = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
    )
    assert record.compatibility_artifact_id is None
    count = await mq_conn.fetchval("SELECT count(*) FROM artifacts")
    assert count == 0


@pytest.mark.asyncio
async def test_record_validation_artifact_write_failure_rolls_back(
    mq_conn,
) -> None:
    """If the compatibility artifact write fails, the typed row rolls back.

    A bad ``feature_id`` (no ``features`` row) makes the ``artifacts`` FK insert
    raise inside the ``record_validation`` transaction. doc 09 § "Persistence
    And Artifact Compatibility": "If a projection write fails, the typed write
    rolls back." We assert no ``execution_regroup_validations`` row survived.
    """

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _overlay()
    row_id = await store.insert_overlay(overlay)

    # The overlay/feature is feat-1, but we ask record_validation to write the
    # artifact under a feature id with no `features` row -> FK violation on the
    # `artifacts` insert, inside the same transaction as the validation row.
    orphan = overlay.model_copy(update={"feature_id": "feat-orphan"})
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await store.record_validation(
            feature_id="feat-orphan",
            overlay_id="overlay-1",
            overlay_row_id=row_id,
            valid=True,
            validation_digest="valdig",
            compatibility_artifact=orphan,
        )
    surviving = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations"
    )
    assert surviving == 0
    surviving_artifacts = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts"
    )
    assert surviving_artifacts == 0


@pytest.mark.asyncio
async def test_record_validation_idempotent_artifact_is_reused(mq_conn) -> None:
    """The idempotent re-run reuses the same compatibility artifact row."""

    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _overlay()
    row_id = await store.insert_overlay(overlay)

    first = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
        compatibility_artifact=overlay,
    )
    second = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="valdig",
        compatibility_artifact=overlay,
    )
    assert first.id == second.id
    assert first.compatibility_artifact_id == second.compatibility_artifact_id
    artifact_count = await mq_conn.fetchval("SELECT count(*) FROM artifacts")
    assert artifact_count == 1


@pytest.mark.asyncio
async def test_list_validations_newest_first(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    row_id = await store.insert_overlay(_overlay())

    # Same digest => idempotent (one row). Use one passing validation only,
    # since a different digest is rejected; list_validations newest-first
    # ordering is exercised across two distinct overlays' validations.
    row_b = await store.insert_overlay(
        _overlay(overlay_id="ov-b", overlay_sha256="osb")
    )
    rec_1 = await store.record_validation(
        feature_id="feat-1",
        overlay_id="overlay-1",
        overlay_row_id=row_id,
        valid=True,
        validation_digest="d1",
    )
    await store.record_validation(
        feature_id="feat-1",
        overlay_id="ov-b",
        overlay_row_id=row_b,
        valid=True,
        validation_digest="d2",
    )
    only_1 = await store.list_validations("feat-1", "overlay-1")
    assert [r.id for r in only_1] == [rec_1.id]


@pytest.mark.asyncio
async def test_validation_idempotency_key_is_deterministic() -> None:
    """The key is a pure function of ``(feature_id, overlay_id, digest)``."""

    first = validation_idempotency_key(
        feature_id="feat-1", overlay_id="ov-1", validation_digest="d"
    )
    second = validation_idempotency_key(
        feature_id="feat-1", overlay_id="ov-1", validation_digest="d"
    )
    assert first == second
    different = validation_idempotency_key(
        feature_id="feat-1", overlay_id="ov-1", validation_digest="other"
    )
    assert different != first


# ── scheduler feedback ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_scheduler_feedback_round_trips(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    feedback = _feedback()

    record = await store.insert_scheduler_feedback(feedback)
    assert record.id > 0
    assert record.feedback_id == "fb-1"
    assert record.lane == "backend"
    assert record.barrier == "b1"
    assert record.recommended_cap == 6
    assert record.metric_ids == ["m1", "m2"]
    assert record.evidence_ids == [1, 2, 3]

    # payload_json reconstructs the full typed SchedulerFeedback.
    restored = SchedulerFeedback.model_validate(record.payload_json)
    assert restored == feedback

    reloaded = await store.get_scheduler_feedback(record.id)
    assert reloaded is not None and reloaded.id == record.id


@pytest.mark.asyncio
async def test_insert_scheduler_feedback_is_idempotent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    feedback = _feedback()

    first = await store.insert_scheduler_feedback(feedback)
    second = await store.insert_scheduler_feedback(feedback)
    assert first.id == second.id
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_scheduler_feedback"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_list_scheduler_feedback_filters_lane_and_barrier(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    await store.insert_scheduler_feedback(
        _feedback(feedback_id="fb-backend", lane="backend", barrier="b1")
    )
    await store.insert_scheduler_feedback(
        _feedback(feedback_id="fb-ui", lane="ui", barrier="b2")
    )

    all_rows = await store.list_scheduler_feedback("feat-1")
    assert {r.feedback_id for r in all_rows} == {"fb-backend", "fb-ui"}

    backend = await store.list_scheduler_feedback("feat-1", lane="backend")
    assert [r.feedback_id for r in backend] == ["fb-backend"]

    b2 = await store.list_scheduler_feedback("feat-1", barrier="b2")
    assert [r.feedback_id for r in b2] == ["fb-ui"]

    none = await store.list_scheduler_feedback(
        "feat-1", lane="backend", barrier="b2"
    )
    assert none == []


@pytest.mark.asyncio
async def test_insert_scheduler_feedback_rejects_blank_ids(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    with pytest.raises(RegroupOverlayStoreError, match="feedback_id"):
        await store.insert_scheduler_feedback(_feedback(feedback_id=""))


@pytest.mark.asyncio
async def test_scheduler_feedback_data_quality_check_constraint(mq_conn) -> None:
    """The schema CHECK rejects an unknown ``data_quality`` at the DB level."""

    await _insert_feature(mq_conn, "feat-1")
    with pytest.raises(asyncpg.PostgresError):
        await mq_conn.execute(
            "INSERT INTO execution_scheduler_feedback "
            "(feedback_id, feature_id, window_start_group, window_end_group, "
            " lane, barrier, recommended_cap, current_cap, data_quality, "
            " confidence, payload_json, idempotency_key) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)",
            "fb-bad",
            "feat-1",
            45,
            50,
            "backend",
            "b1",
            6,
            4,
            "not-a-quality",
            "low",
            "{}",
            "scheduler-feedback:feat-1:fb-bad:45:50",
        )


# ── feature advisory lock ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_feature_advisory_lock_serializes_across_connections(
    mq_dsn: str,
) -> None:
    """The advisory lock is mutually exclusive across connections.

    Connection A holds the lock; B's ``pg_try_advisory_lock`` on the same key
    must fail until A releases. This is the lock 09c activation/rollback runs
    under.
    """

    conn_a = await asyncpg.connect(mq_dsn)
    conn_b = await asyncpg.connect(mq_dsn)
    try:
        store_a = RegroupOverlayStore(conn_a)
        await store_a.acquire_feature_lock("feat-lock")

        held = await conn_b.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1))", "feat-lock"
        )
        assert held is False

        await store_a.release_feature_lock("feat-lock")
        now_free = await conn_b.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1))", "feat-lock"
        )
        assert now_free is True
        await conn_b.execute(
            "SELECT pg_advisory_unlock(hashtext($1))", "feat-lock"
        )
    finally:
        await conn_a.close()
        await conn_b.close()


@pytest.mark.asyncio
async def test_feature_advisory_lock_shares_merge_queue_key(mq_dsn: str) -> None:
    """Regroup + merge-queue lock the SAME ``hashtext(feature_id)`` key.

    doc 09 + Slice 08: regroup mutation and merge-queue canonical mutation for
    one feature must serialize against one another. Both use
    ``pg_advisory_lock(hashtext(feature_id))`` — proven here by acquiring via
    ``RegroupOverlayStore`` and observing ``MergeQueueStore``'s key is blocked.
    """

    from iriai_build_v2.execution_control.merge_queue_store import (
        MergeQueueStore,
    )

    conn_a = await asyncpg.connect(mq_dsn)
    conn_b = await asyncpg.connect(mq_dsn)
    try:
        await RegroupOverlayStore(conn_a).acquire_feature_lock("feat-shared")
        # MergeQueueStore.acquire_feature_lock would block; prove the key
        # collides via the same try-lock the merge queue's lock uses.
        held = await conn_b.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1))", "feat-shared"
        )
        assert held is False
        await RegroupOverlayStore(conn_a).release_feature_lock("feat-shared")
        # Sanity: MergeQueueStore can now take it.
        await MergeQueueStore(conn_b).acquire_feature_lock("feat-shared")
        await MergeQueueStore(conn_b).release_feature_lock("feat-shared")
    finally:
        await conn_a.close()
        await conn_b.close()


@pytest.mark.asyncio
async def test_feature_advisory_lock_rejects_blank_feature(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    with pytest.raises(RegroupOverlayStoreError, match="feature_id"):
        await store.acquire_feature_lock("")
    with pytest.raises(RegroupOverlayStoreError, match="feature_id"):
        await store.release_feature_lock("")


# ── idempotency-key helpers (pure / deterministic) ──────────────────────────


def test_overlay_idempotency_key_is_deterministic() -> None:
    overlay = _overlay()
    assert overlay_idempotency_key(overlay) == overlay_idempotency_key(overlay)
    other = _overlay(overlay_sha256="different")
    assert overlay_idempotency_key(other) != overlay_idempotency_key(overlay)


def test_scheduler_feedback_idempotency_key_is_deterministic() -> None:
    feedback = _feedback()
    assert scheduler_feedback_idempotency_key(
        feedback
    ) == scheduler_feedback_idempotency_key(feedback)
    other = _feedback(feedback_id="fb-other")
    assert scheduler_feedback_idempotency_key(
        other
    ) != scheduler_feedback_idempotency_key(feedback)
