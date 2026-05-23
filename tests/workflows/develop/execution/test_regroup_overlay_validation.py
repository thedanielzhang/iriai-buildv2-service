"""Slice 09b-2 tests — the 13-step deterministic ``validate_overlay`` algorithm.

These tests exercise :func:`iriai_build_v2.workflows.develop.execution.regroup_overlay_validation.validate_overlay`.
Most run against a real Postgres database (the ``mq_conn`` / ``mq_dsn``
fixtures in this directory's conftest) because step 2 loads the base DAG
through the store and step 13 persists the typed validation row + compatibility
artifact transactionally. They skip cleanly when no Postgres is reachable.

Per the IMPLEMENTATION_PROMPT, EACH of the 13 steps has a pass case AND a
fail/rejection case; the determinism of ``validation_digest`` and the
idempotency-on-``(overlay_id, validation_digest)`` behavior are covered
explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from iriai_build_v2.execution_control.regroup_overlay_store import (
    RegroupOverlayStore,
    RegroupOverlayValidationConflict,
)
from iriai_build_v2.models.outputs import (
    DerivedDAGArtifact,
    ImplementationDAG,
    ImplementationTask,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
    OverlayBarrier,
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupActiveMarker,
    RegroupOverlay,
    RegroupRollbackPlan,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
    OverlayValidationContext,
    OverlayValidationResult,
    validate_overlay,
)

_NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


# ── base DAG fixture ────────────────────────────────────────────────────────
#
# A 4-group DAG. The regroup overlay targets the suffix from group 2
# (checkpointed_group=1, group_idx_offset=2): original groups [2, 3] carrying
# tasks T20/T21 (group 2) and T30 (group 3). T30 depends on T20.


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
    conn: asyncpg.Connection, feature_id: str, *, key: str = "dag",
    body: str | None = None,
) -> tuple[int, str]:
    """Insert the base DAG as an ``artifacts`` row; return ``(id, sha256)``."""

    import hashlib

    value = body if body is not None else _BASE_DAG_JSON
    artifact_id = await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value) VALUES ($1,$2,$3) "
        "RETURNING id",
        feature_id,
        key,
        value,
    )
    return int(artifact_id), hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── overlay builders ────────────────────────────────────────────────────────
#
# `_valid_overlay` builds the canonical valid overlay for the base DAG suffix
# [2,3], then re-injects the canonically-computed overlay sha so the activation
# contract's required_overlay_sha256 matches (step 11).


def _fingerprints() -> dict[str, str]:
    """Task-definition fingerprints computed exactly as the validator does."""

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _task_definition_fingerprint,
    )

    by_id = {t.id: t for t in _BASE_DAG.tasks}
    return {tid: _task_definition_fingerprint(by_id[tid]) for tid in ("T20", "T21", "T30")}


def _raw_overlay(
    *,
    base_dag_artifact_id: int,
    base_dag_sha256: str,
    derived_execution_order: list[list[str]] | None = None,
    overrides: dict[str, object] | None = None,
) -> RegroupOverlay:
    """Build an overlay WITHOUT the canonical-sha re-injection (for raw tests)."""

    derived = derived_execution_order or [["T20", "T21"], ["T30"]]
    first_wave = derived[0]
    base: dict[str, object] = {
        "overlay_id": "overlay-09b2",
        "overlay_slug": "g2-g3",
        "feature_id": "feat-1",
        "status": "staged",
        "artifact_key": "dag-regroup:g2-g3",
        "source_dag_key": "dag",
        "base_dag_artifact_id": base_dag_artifact_id,
        "base_dag_sha256": base_dag_sha256,
        "checkpointed_group": 1,
        "group_idx_offset": 2,
        "last_original_group": 3,
        "original_execution_order": [["T20", "T21"], ["T30"]],
        "derived_execution_order": derived,
        "original_to_new_group_mapping": {2: [2], 3: [3]},
        "task_definition_fingerprints": _fingerprints(),
        "remaining_dependency_edges": {"T20": [], "T21": [], "T30": ["T20"]},
        "barriers": [
            OverlayBarrier(
                barrier_id="b-backend",
                task_ids=["T20", "T21", "T30"],
                hard=True,
                source="task_contract",
            )
        ],
        "write_sets": {"T20": ["a20.py"], "T21": ["a21.py"], "T30": ["a30.py"]},
        "speed_index": {
            "T20": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-backend"),
            "T21": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-backend"),
            "T30": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-backend"),
        },
        "activation_contract": RegroupActivationContract(
            required_checkpoint_key="dag-group:1",
            forbidden_checkpoint_key="dag-group:2",
            forbidden_first_wave_task_keys=sorted(
                f"dag-task:{tid}" for tid in first_wave
            ),
            forbidden_group_artifact_prefixes=["dag-verify:2"],
            forbidden_group_event_idx=2,
            required_base_dag_artifact_id=base_dag_artifact_id,
            required_base_dag_sha256=base_dag_sha256,
            required_overlay_sha256="PLACEHOLDER",
        ),
        "rollback_plan": RegroupRollbackPlan(
            restore_source_dag_key="dag",
            restore_from_checkpoint_group=1,
            rollback_marker_key="dag-regroup-rollback:g2-g3",
            allowed_until_group_idx=2,
            forbidden_started_keys=sorted(f"dag-task:{tid}" for tid in first_wave),
            forbidden_started_event_group_idx=2,
            forbidden_typed_attempt_group_idx=2,
            forbidden_merge_queue_group_idx=2,
        ),
        "compatibility_keys": OverlayCompatibilityKeys(
            canonical_artifact_key="dag-regroup:g2-g3",
            active_marker_key="dag-regroup-active:g2-g3",
            rollback_artifact_key="dag-regroup-rollback:g2-g3",
            observation_artifact_key="dag-regroup-observation:g2-g3",
            sizing_review_key_prefix="review:dag-sizing:feat-1",
        ),
        "created_at": _NOW,
        "overlay_sha256": "PLACEHOLDER",
        "validation_digest": "valdig",
    }
    if overrides:
        base.update(overrides)
    return RegroupOverlay(**base)


def _valid_overlay(
    *,
    base_dag_artifact_id: int,
    base_dag_sha256: str,
    derived_execution_order: list[list[str]] | None = None,
    overrides: dict[str, object] | None = None,
) -> RegroupOverlay:
    """A fully valid overlay: canonical sha re-injected so step 11 passes."""

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _canonical_overlay_sha,
    )

    overlay = _raw_overlay(
        base_dag_artifact_id=base_dag_artifact_id,
        base_dag_sha256=base_dag_sha256,
        derived_execution_order=derived_execution_order,
        overrides=overrides,
    )
    # _canonical_overlay_sha excludes activation_contract.required_overlay_sha256
    # (a self-reference), so the sha is stable in a single pass.
    canonical = _canonical_overlay_sha(overlay)
    contract = overlay.activation_contract.model_copy(
        update={"required_overlay_sha256": canonical}
    )
    return overlay.model_copy(
        update={"overlay_sha256": canonical, "activation_contract": contract}
    )


def _reseal_overlay(overlay: RegroupOverlay) -> RegroupOverlay:
    """Recompute the canonical sha after a mutation and re-inject it.

    Mutating any *substantive* overlay field (e.g. the rollback plan) shifts
    the canonical sha; this re-seals ``overlay_sha256`` /
    ``required_overlay_sha256`` so step 11's activation-contract check passes
    and the test can isolate a *different* step-11 failure (the rollback plan).
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _canonical_overlay_sha,
    )

    canonical = _canonical_overlay_sha(overlay)
    contract = overlay.activation_contract.model_copy(
        update={"required_overlay_sha256": canonical}
    )
    return overlay.model_copy(
        update={"overlay_sha256": canonical, "activation_contract": contract}
    )


def _context(**overrides: object) -> OverlayValidationContext:
    base: dict[str, object] = {
        "feature_id": "feat-1",
        "boundary_checkpoint_exists": False,
        "checkpointed_group_exists": True,
    }
    base.update(overrides)
    return OverlayValidationContext(**base)


# ════════════════════════════════════════════════════════════════════════════
# Step 1 — parse + normalize
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step1_pass_typed_overlay_normalizes(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)

    result = await validate_overlay(
        overlay, _context(), store, persist=False
    )
    assert result.valid, result.reason
    assert result.normalized is not None
    # within-wave task ids are sorted in the normalized form.
    assert result.normalized.derived_execution_order == [["T20", "T21"], ["T30"]]


@pytest.mark.asyncio
async def test_step1_fail_malformed_json(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    result = await validate_overlay(
        "{not json", _context(), store, persist=False
    )
    assert not result.valid
    assert result.reason == "dag_regroup_overlay_malformed_json"
    assert result.failed_step == 1


@pytest.mark.asyncio
async def test_step1_fail_bare_derived_dag_artifact_rejected(mq_conn) -> None:
    """A bare DerivedDAGArtifact is the projection, not the typed overlay."""

    store = RegroupOverlayStore(mq_conn)
    artifact = DerivedDAGArtifact(
        artifact_key="dag-regroup:g2-g3", source_dag_key="dag", dag=_BASE_DAG
    )
    result = await validate_overlay(artifact, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_overlay_not_typed"
    assert result.failed_step == 1


@pytest.mark.asyncio
async def test_step1_fail_artifact_key_mismatch(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    overlay = _raw_overlay(
        base_dag_artifact_id=1,
        base_dag_sha256="x",
        overrides={"artifact_key": "dag-regroup:WRONG"},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_overlay_artifact_key_mismatch"


@pytest.mark.asyncio
async def test_step1_fail_feature_mismatch(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    overlay = _raw_overlay(base_dag_artifact_id=1, base_dag_sha256="x")
    result = await validate_overlay(
        overlay, _context(feature_id="other-feature"), store, persist=False
    )
    assert not result.valid
    assert result.reason == "dag_regroup_overlay_feature_mismatch"


@pytest.mark.asyncio
async def test_step1_fail_unvalidatable_status(mq_conn) -> None:
    store = RegroupOverlayStore(mq_conn)
    overlay = _raw_overlay(
        base_dag_artifact_id=1, base_dag_sha256="x",
        overrides={"status": "rolled_back"},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_overlay_status_unvalidatable"


@pytest.mark.asyncio
async def test_step1_fail_compat_projection_identity_mismatch(mq_conn) -> None:
    """doc 09 step 1: speed_index['overlay'] identity must match the overlay."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    projection = DerivedDAGArtifact(
        artifact_key="dag-regroup:g2-g3",
        source_dag_key="dag",
        dag=_BASE_DAG,
        speed_index={"overlay": {"overlay_id": "WRONG", "overlay_sha256": "WRONG"}},
    )
    result = await validate_overlay(
        overlay, _context(), store, persist=False,
        compatibility_projection=projection,
    )
    assert not result.valid
    assert result.reason == "dag_regroup_projection_identity_mismatch"


@pytest.mark.asyncio
async def test_step1_pass_compat_projection_identity_matches(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    projection = DerivedDAGArtifact(
        artifact_key="dag-regroup:g2-g3",
        source_dag_key="dag",
        dag=_BASE_DAG,
        speed_index={
            "overlay": {
                "overlay_id": overlay.overlay_id,
                "overlay_sha256": overlay.overlay_sha256,
            }
        },
    )
    result = await validate_overlay(
        overlay, _context(), store, persist=False,
        compatibility_projection=projection,
    )
    assert result.valid, result.reason


# ════════════════════════════════════════════════════════════════════════════
# Step 2 — load + match source DAG
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step2_pass_base_dag_exact_match(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step2_fail_base_dag_missing(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=999, base_dag_sha256="x")
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_base_dag_missing"
    assert result.failed_step == 2


@pytest.mark.asyncio
async def test_step2_fail_base_dag_artifact_id_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id + 5000, base_dag_sha256=sha
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_base_dag_artifact_mismatch"


@pytest.mark.asyncio
async def test_step2_fail_base_dag_hash_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, _sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256="stale-hash"
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_base_dag_hash_mismatch"


# ════════════════════════════════════════════════════════════════════════════
# Step 3 — offset / checkpoint / suffix
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step3_pass_offset_and_suffix(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step3_fail_offset_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"checkpointed_group": 0},  # 0 + 1 != 2
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_offset_mismatch"
    assert result.failed_step == 3


@pytest.mark.asyncio
async def test_step3_fail_boundary_checkpoint_missing(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(
        overlay, _context(checkpointed_group_exists=False), store, persist=False
    )
    assert not result.valid
    assert result.reason == "dag_regroup_boundary_checkpoint_missing"


@pytest.mark.asyncio
async def test_step3_fail_boundary_checkpoint_exists(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(
        overlay, _context(boundary_checkpoint_exists=True), store, persist=False
    )
    assert not result.valid
    assert result.reason == "dag_regroup_boundary_checkpoint_exists"


@pytest.mark.asyncio
async def test_step3_fail_original_execution_order_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"original_execution_order": [["T20"], ["T21"], ["T30"]]},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_original_execution_order_mismatch"


# ════════════════════════════════════════════════════════════════════════════
# Step 4 — task-id multisets
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step4_pass_task_multisets(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step4_fail_missing_task(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Drop T21 from the derived order entirely.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        derived_execution_order=[["T20"], ["T30"]],
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_task_preservation_mismatch"
    assert result.failed_step == 4


@pytest.mark.asyncio
async def test_step4_fail_duplicate_task(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        derived_execution_order=[["T20", "T21"], ["T20", "T30"]],  # T20 twice
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_duplicate_task_ids"


# ════════════════════════════════════════════════════════════════════════════
# Step 5 — task-definition fingerprints
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step5_pass_pure_placement_change(mq_conn) -> None:
    """A pure re-waving (placement only) keeps the fingerprints intact."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Re-wave to 3 derived groups; same tasks, same definitions.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        derived_execution_order=[["T20"], ["T21"], ["T30"]],
        overrides={"original_to_new_group_mapping": {2: [2, 3], 3: [4]}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step5_fail_mutated_task_definition(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    fps = _fingerprints()
    fps["T20"] = "tampered-fingerprint"
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"task_definition_fingerprints": fps},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_task_definition_mismatch"
    assert result.failed_step == 5


# ════════════════════════════════════════════════════════════════════════════
# Step 6 — remaining dependency edges
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step6_pass_dependency_edges_preserved(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step6_fail_dropped_dependency(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T30 depends on T20 in the base; drop it.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"remaining_dependency_edges": {"T20": [], "T21": [], "T30": []}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_dependency_preservation_mismatch"
    assert result.failed_step == 6


@pytest.mark.asyncio
async def test_step6_fail_added_dependency(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Add a spurious T21->T20 edge not in the base suffix.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={
            "remaining_dependency_edges": {
                "T20": [], "T21": ["T20"], "T30": ["T20"]
            }
        },
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_dependency_preservation_mismatch"


# ════════════════════════════════════════════════════════════════════════════
# Step 7 — derived group placement of dependencies
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step7_pass_dependency_before_dependent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step7_fail_same_wave_dependency(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Put T20 and its dependent T30 in the SAME derived wave.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        derived_execution_order=[["T20", "T21", "T30"]],
        overrides={"original_to_new_group_mapping": {2: [2], 3: [2]}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_dependency_same_wave"
    assert result.failed_step == 7


@pytest.mark.asyncio
async def test_step7_fail_dependency_after_dependent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T30 (depends on T20) scheduled BEFORE T20.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        derived_execution_order=[["T21", "T30"], ["T20"]],
        overrides={"original_to_new_group_mapping": {2: [2, 3], 3: [2]}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_dependency_after_dependent"


# ════════════════════════════════════════════════════════════════════════════
# Step 8 — original_to_new_group_mapping
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step8_pass_mapping_valid(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step8_fail_mapping_missing_original_group(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Original suffix groups are {2, 3}; omit group 3.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"original_to_new_group_mapping": {2: [2]}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_mapping_group_coverage_mismatch"
    assert result.failed_step == 8


@pytest.mark.asyncio
async def test_step8_fail_mapping_target_out_of_range(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Derived groups are [2,3]; map group 3 to an out-of-range new group 99.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"original_to_new_group_mapping": {2: [2], 3: [99]}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_mapping_target_out_of_range"


@pytest.mark.asyncio
async def test_step8_fail_mapping_task_membership(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T21 in derived group 2, T20 in group 3, T30 in group 4 — dependency
    # order (T30 after T20) is fine, so step 7 passes. But original group 2
    # (T20/T21) is mapped only to derived group 2, while T20 actually sits in
    # derived group 3 — a task/source membership mismatch.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        derived_execution_order=[["T21"], ["T20"], ["T30"]],
        overrides={"original_to_new_group_mapping": {2: [2], 3: [3, 4]}},
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_mapping_task_membership_mismatch"


# ════════════════════════════════════════════════════════════════════════════
# Step 9 — hard barriers
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step9_pass_single_barrier_per_wave(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step9_pass_soft_barrier_mix_allowed(mq_conn) -> None:
    """doc 09 step 9: only HARD barriers can violate; soft mixes pass."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # Two distinct SOFT barriers in the first wave — must NOT reject.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={
            "barriers": [
                OverlayBarrier(
                    barrier_id="soft-a", task_ids=["T20"], hard=False,
                    source="task_contract",
                ),
                OverlayBarrier(
                    barrier_id="soft-b", task_ids=["T21"], hard=False,
                    source="task_contract",
                ),
            ],
            "speed_index": {
                "T20": OverlayTaskSpeedMetadata(semantic_lane="backend"),
                "T21": OverlayTaskSpeedMetadata(semantic_lane="backend"),
                "T30": OverlayTaskSpeedMetadata(semantic_lane="backend"),
            },
        },
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step9_fail_hard_barrier_mix(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T20 and T21 are in the SAME first wave but under DIFFERENT hard barriers.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={
            "barriers": [
                OverlayBarrier(
                    barrier_id="hard-a", task_ids=["T20"], hard=True,
                    source="task_contract",
                ),
                OverlayBarrier(
                    barrier_id="hard-b", task_ids=["T21", "T30"], hard=True,
                    source="task_contract",
                ),
            ],
        },
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_barrier_violation"
    assert result.failed_step == 9


# ════════════════════════════════════════════════════════════════════════════
# Step 10 — authoritative write sets
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step10_pass_disjoint_write_sets(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step10_fail_same_wave_write_overlap(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T20 and T21 share the first wave; the overlay write_sets add an
    # overlapping path "shared.py" to both.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={
            "write_sets": {
                "T20": ["a20.py", "shared.py"],
                "T21": ["a21.py", "shared.py"],
                "T30": ["a30.py"],
            }
        },
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_write_set_conflict"
    assert result.failed_step == 10


@pytest.mark.asyncio
async def test_step10_fail_write_set_removes_authoritative_path(mq_conn) -> None:
    """doc 09 step 10: overlay additions may add, never remove/narrow."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T20 declares a20.py in the base; the overlay write_set omits it.
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={
            "write_sets": {
                "T20": ["different.py"],  # drops the base-declared a20.py
                "T21": ["a21.py"],
                "T30": ["a30.py"],
            }
        },
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_write_set_removes_authoritative_path"


@pytest.mark.asyncio
async def test_step10_fail_unknown_write_in_widened_wave(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # T20 is unknown_write and sits in the WIDENED first wave (T20, T21).
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={
            "speed_index": {
                "T20": OverlayTaskSpeedMetadata(
                    semantic_lane="backend", barrier="b-backend",
                    unknown_write=True,
                ),
                "T21": OverlayTaskSpeedMetadata(
                    semantic_lane="backend", barrier="b-backend"
                ),
                "T30": OverlayTaskSpeedMetadata(
                    semantic_lane="backend", barrier="b-backend"
                ),
            }
        },
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_unknown_write_in_widened_wave"


# ════════════════════════════════════════════════════════════════════════════
# Step 11 — activation + rollback contracts
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step11_pass_contracts_consistent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step11_fail_activation_contract_invalid(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    # Corrupt the activation contract's forbidden checkpoint key.
    bad_contract = overlay.activation_contract.model_copy(
        update={"forbidden_checkpoint_key": "dag-group:999"}
    )
    overlay = overlay.model_copy(update={"activation_contract": bad_contract})
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_activation_contract_invalid"
    assert result.failed_step == 11


@pytest.mark.asyncio
async def test_step11_fail_rollback_plan_invalid(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    bad_rollback = overlay.rollback_plan.model_copy(
        update={"restore_from_checkpoint_group": 99}
    )
    # Re-seal so the activation-contract sha check passes — isolates the
    # rollback-plan failure as the first step-11 rejection.
    overlay = _reseal_overlay(
        overlay.model_copy(update={"rollback_plan": bad_rollback})
    )
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert not result.valid
    assert result.reason == "dag_regroup_rollback_plan_invalid"


# ════════════════════════════════════════════════════════════════════════════
# Step 12 — RegroupActiveMarker (activation / resolver checks)
# ════════════════════════════════════════════════════════════════════════════


def _matching_marker(overlay: RegroupOverlay, *, overlay_row_id: int) -> RegroupActiveMarker:
    return RegroupActiveMarker(
        status="active",
        feature_id=overlay.feature_id,
        overlay_id=overlay.overlay_id,
        overlay_slug=overlay.overlay_slug,
        overlay_row_id=overlay_row_id,
        canonical_artifact_key=overlay.compatibility_keys.canonical_artifact_key,
        canonical_artifact_id=500,
        canonical_sha256="csha",
        active_marker_key=overlay.compatibility_keys.active_marker_key,
        rollback_artifact_key=overlay.compatibility_keys.rollback_artifact_key,
        rollback_artifact_id=501,
        source_dag_key=overlay.source_dag_key,
        base_dag_artifact_id=overlay.base_dag_artifact_id,
        base_dag_sha256=overlay.base_dag_sha256,
        checkpointed_group=overlay.checkpointed_group,
        group_idx_offset=overlay.group_idx_offset,
        validation_digest=overlay.validation_digest,
    )


@pytest.mark.asyncio
async def test_step12_pass_active_marker_matches(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"status": "active"},
    )
    marker = _matching_marker(overlay, overlay_row_id=42)
    result = await validate_overlay(
        overlay,
        _context(
            active_marker=marker,
            overlay_row_id=42,
            latest_successful_validation_digest=overlay.validation_digest,
        ),
        store,
        activation_check=True,
        persist=False,
    )
    assert result.valid, result.reason


@pytest.mark.asyncio
async def test_step12_fail_active_marker_missing(mq_conn) -> None:
    """activation_check=True with no marker fails closed."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"status": "active"},
    )
    result = await validate_overlay(
        overlay, _context(), store, activation_check=True, persist=False
    )
    assert not result.valid
    assert result.reason == "dag_regroup_active_marker_missing"
    assert result.failed_step == 12


@pytest.mark.asyncio
async def test_step12_fail_active_marker_field_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"status": "active"},
    )
    marker = _matching_marker(overlay, overlay_row_id=42).model_copy(
        update={"base_dag_artifact_id": artifact_id + 7777}
    )
    result = await validate_overlay(
        overlay,
        _context(active_marker=marker, overlay_row_id=42),
        store,
        activation_check=True,
        persist=False,
    )
    assert not result.valid
    assert result.reason == "dag_regroup_active_marker_field_mismatch"


@pytest.mark.asyncio
async def test_step12_fail_active_marker_validation_digest_stale(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"status": "active"},
    )
    marker = _matching_marker(overlay, overlay_row_id=42)
    result = await validate_overlay(
        overlay,
        _context(
            active_marker=marker,
            overlay_row_id=42,
            latest_successful_validation_digest="a-different-digest",
        ),
        store,
        activation_check=True,
        persist=False,
    )
    assert not result.valid
    assert result.reason == "dag_regroup_active_marker_validation_digest_stale"


@pytest.mark.asyncio
async def test_step12_skipped_when_not_activation_check(mq_conn) -> None:
    """A non-activation validation never runs step 12 (no marker needed)."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    # No active_marker in context; activation_check defaults False.
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert result.valid, result.reason


# ════════════════════════════════════════════════════════════════════════════
# Step 13 — persist typed row + compatibility artifact
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step13_pass_persists_typed_row_and_artifact(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    overlay_row_id = await store.insert_overlay(overlay)

    result = await validate_overlay(
        overlay, _context(overlay_row_id=overlay_row_id), store, persist=True
    )
    assert result.valid, result.reason
    # A typed execution_regroup_validations row exists.
    validation_count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations "
        "WHERE overlay_id = $1",
        overlay.overlay_id,
    )
    assert validation_count == 1
    # The dag-regroup-validation:* compatibility artifact exists.
    artifact_count = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-1",
        "dag-regroup-validation:g2-g3",
    )
    assert artifact_count == 1
    # The validation row id is cited in the result evidence.
    assert result.evidence_ids


@pytest.mark.asyncio
async def test_step13_fail_validation_row_records_rejection(mq_conn) -> None:
    """A failing validation still persists a typed row (valid=false), no artifact."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    # An overlay that fails step 6 (dropped dependency).
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"remaining_dependency_edges": {"T20": [], "T21": [], "T30": []}},
    )
    overlay_row_id = await store.insert_overlay(overlay)
    result = await validate_overlay(
        overlay, _context(overlay_row_id=overlay_row_id), store, persist=True
    )
    assert not result.valid
    row = await mq_conn.fetchrow(
        "SELECT valid, reason FROM execution_regroup_validations "
        "WHERE overlay_id = $1",
        overlay.overlay_id,
    )
    assert row is not None
    assert row["valid"] is False
    assert row["reason"] == "dag_regroup_dependency_preservation_mismatch"
    # No compatibility artifact for a rejected validation.
    artifact_count = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-1",
        "dag-regroup-validation:g2-g3",
    )
    assert artifact_count == 0


# ── determinism of validation_digest ────────────────────────────────────────


@pytest.mark.asyncio
async def test_validation_digest_is_deterministic_across_runs(mq_conn) -> None:
    """The validation_digest is reproducible — a non-deterministic digest is P1."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)

    first = await validate_overlay(overlay, _context(), store, persist=False)
    second = await validate_overlay(overlay, _context(), store, persist=False)
    assert first.validation_digest == second.validation_digest
    assert first.valid and second.valid
    assert len(first.validation_digest) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_validation_digest_differs_for_activation_mode(mq_conn) -> None:
    """An activation-mode validation is distinct evidence => distinct digest."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(
        base_dag_artifact_id=artifact_id, base_dag_sha256=sha,
        overrides={"status": "active"},
    )
    structural = await validate_overlay(overlay, _context(), store, persist=False)
    marker = _matching_marker(overlay, overlay_row_id=1)
    activation = await validate_overlay(
        overlay,
        _context(active_marker=marker, overlay_row_id=1),
        store,
        activation_check=True,
        persist=False,
    )
    assert structural.valid and activation.valid
    assert structural.validation_digest != activation.validation_digest


def test_validation_digest_is_pure_function_of_inputs() -> None:
    """The digest helper itself has no hidden state (no clock/random)."""

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _compute_validation_digest,
    )

    overlay = _raw_overlay(base_dag_artifact_id=1, base_dag_sha256="x")
    a = _compute_validation_digest(
        overlay, valid=True, reason="", activation_check=False
    )
    b = _compute_validation_digest(
        overlay, valid=True, reason="", activation_check=False
    )
    assert a == b
    # A different reason yields a different digest.
    c = _compute_validation_digest(
        overlay, valid=False, reason="some_reason", activation_check=False
    )
    assert c != a


# ── idempotency on (overlay_id, validation_digest) ──────────────────────────


@pytest.mark.asyncio
async def test_step13_idempotent_on_overlay_id_and_digest(mq_conn) -> None:
    """doc 09 step 13: re-validating the same overlay+digest reuses the row."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    overlay_row_id = await store.insert_overlay(overlay)

    first = await validate_overlay(
        overlay, _context(overlay_row_id=overlay_row_id), store, persist=True
    )
    second = await validate_overlay(
        overlay, _context(overlay_row_id=overlay_row_id), store, persist=True
    )
    assert first.validation_digest == second.validation_digest
    # Exactly one validation row — the second run reused it.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations "
        "WHERE overlay_id = $1",
        overlay.overlay_id,
    )
    assert count == 1
    # And exactly one compatibility artifact.
    artifact_count = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-1",
        "dag-regroup-validation:g2-g3",
    )
    assert artifact_count == 1


@pytest.mark.asyncio
async def test_step13_conflicting_digest_for_same_overlay_rejected(mq_conn) -> None:
    """doc 09 step 13: a *different* digest for the same overlay id fails closed.

    A failed validation records digest_fail; a later passing validation of the
    *same overlay id* produces a different digest. ``record_validation`` rejects
    it fail-closed with ``RegroupOverlayValidationConflict`` and writes nothing.
    """

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    overlay_row_id = await store.insert_overlay(overlay)

    # Record a failing validation first (it persists a valid=false row).
    failing = await validate_overlay(
        overlay,
        _context(overlay_row_id=overlay_row_id, boundary_checkpoint_exists=True),
        store,
        persist=True,
    )
    assert not failing.valid

    # Now a PASSING validation of the same overlay id => different digest.
    with pytest.raises(RegroupOverlayValidationConflict):
        await validate_overlay(
            overlay, _context(overlay_row_id=overlay_row_id), store, persist=True
        )
    # The conflicting digest wrote nothing — still exactly one row.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations "
        "WHERE overlay_id = $1",
        overlay.overlay_id,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_validate_overlay_dry_run_does_not_persist(mq_conn) -> None:
    """persist=False writes no validation row even with an overlay_row_id."""

    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    overlay_row_id = await store.insert_overlay(overlay)

    result = await validate_overlay(
        overlay, _context(overlay_row_id=overlay_row_id), store, persist=False
    )
    assert result.valid
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_validations"
    )
    assert count == 0


# ── result shape ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_result_shape_has_all_documented_fields(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-1")
    artifact_id, sha = await _insert_base_dag(mq_conn, "feat-1")
    store = RegroupOverlayStore(mq_conn)
    overlay = _valid_overlay(base_dag_artifact_id=artifact_id, base_dag_sha256=sha)
    result = await validate_overlay(overlay, _context(), store, persist=False)
    assert isinstance(result, OverlayValidationResult)
    # doc 09: OverlayValidationResult(valid, reason, details, evidence_ids,
    # normalized).
    assert isinstance(result.valid, bool)
    assert isinstance(result.reason, str)
    assert isinstance(result.details, list)
    assert isinstance(result.evidence_ids, list)
    assert isinstance(result.normalized, RegroupOverlay)
    assert isinstance(result.validation_digest, str)
