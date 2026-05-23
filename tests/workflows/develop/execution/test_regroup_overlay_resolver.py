"""Slice 09c-2 tests — the typed regroup-overlay dispatch resolver + swap.

These exercise
:class:`iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver.RegroupOverlayResolver`
and the ``implementation.py`` dispatch-resolution swap
(``_resolve_active_regroup_typed_overlay`` /
``_resolve_active_regroup_before_group_dispatch``). They run against a real
Postgres database (the ``mq_conn`` / ``mq_dsn`` fixtures in this directory's
conftest) because the resolver reads the typed overlay row + the
``artifacts`` / ``events`` tables and re-runs the 13-step validator over them.
They skip cleanly when no Postgres is reachable.

Coverage (per the Slice 09c-2 brief):

- the resolver's happy path (a valid active overlay resolves to the derived
  execution order: base prefix waves + the overlay's derived suffix waves);
- every fail-closed mismatch — id / hash / status / digest / projection-link,
  the carried P3-A canonical-artifact-id / canonical-sha256 /
  rollback-artifact-id cross-check;
- the stale / orphaned-marker ``regroup_invalid`` quiesce;
- the dispatch swap: a feature with NO active overlay dispatches unchanged
  (the legacy path is reached); a feature WITH an active typed overlay does
  the overlay; a typed quiesce surfaces as a dispatch failure.
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
    activate_overlay,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver import (
    RegroupOverlayResolver,
)
from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
    OverlayValidationContext,
    validate_overlay,
)

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


# ── base DAG fixture (same 4-group shape as the 09c-1 activation suite) ──────


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


# ── overlay builder (identical valid shape to the 09c-1 activation suite) ────


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
    overlay_id: str = "overlay-09c2",
) -> RegroupOverlay:
    """A fully valid overlay over the base DAG suffix [2, 3]."""

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


def _regrouped_overlay(
    *,
    feature_id: str,
    base_dag_artifact_id: int,
    base_dag_sha256: str,
    overlay_id: str = "overlay-09c2-rg",
) -> RegroupOverlay:
    """A valid overlay that GENUINELY regroups the suffix [2, 3].

    The base suffix is ``[["T20","T21"],["T30"]]`` (2 waves). This overlay
    splits it into 3 waves ``[["T20"],["T21"],["T30"]]`` — T20 and T21 have no
    dependency between them so splitting them is valid, and T30 still follows
    T20. The resolved effective DAG therefore genuinely DIFFERS from the base
    DAG, so the dispatch swap returns a distinct (deep-copied) object.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _canonical_overlay_sha,
    )

    derived = [["T20"], ["T21"], ["T30"]]
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
        # original group 2 (T20,T21) → derived groups 2 and 3; group 3 (T30) → 4.
        original_to_new_group_mapping={2: [2, 3], 3: [4]},
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


async def _activated_overlay(
    conn: asyncpg.Connection,
    store: RegroupOverlayStore,
    *,
    feature_id: str,
    overlay_id: str = "overlay-09c2",
    regrouped: bool = False,
) -> tuple[RegroupOverlay, int]:
    """Stage + validate + activate an overlay; return (overlay, row_id).

    After this the overlay is the single ``active`` typed row, with its
    canonical / rollback / active-marker compatibility projections written —
    exactly the state the resolver reads. When ``regrouped`` is True the overlay
    GENUINELY regroups the suffix (3 waves) so the resolved effective DAG
    differs from the base DAG.
    """

    dag_id, sha = await _insert_base_dag(conn, feature_id)
    builder = _regrouped_overlay if regrouped else _valid_overlay
    overlay = builder(
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
    await _insert_artifact(conn, feature_id, "dag-group:1", '{"ok":true}')
    await activate_overlay(
        store, feature_id=feature_id, overlay_id=overlay_id, reason="go"
    )
    return overlay, row_id


# ════════════════════════════════════════════════════════════════════════════
# Resolver — happy path
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resolver_happy_path_resolves_derived_execution_order(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-rs-ok")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id = await _activated_overlay(
        mq_conn, store, feature_id="feat-rs-ok"
    )

    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-ok", 2)

    assert resolution.has_typed_overlay is True
    assert resolution.applied is True
    assert resolution.quiesce_reason == ""
    assert resolution.overlay_id == overlay.overlay_id
    # The effective order is the base prefix waves [0, 1] + the overlay's
    # derived suffix waves [2, 3].
    assert resolution.effective_execution_order == [
        ["T00"], ["T10"], ["T20", "T21"], ["T30"]
    ]
    # The observation carries the selected overlay id + evidence ids.
    assert resolution.observation["overlay_id"] == overlay.overlay_id
    assert resolution.observation["status"] == "applied"
    assert resolution.observation["resume_group_idx"] == 2
    assert resolution.observation["effective_group_count"] == 4
    assert resolution.observation["derived_group_count"] == 2


@pytest.mark.asyncio
async def test_resolver_regrouped_overlay_resolves_changed_order(mq_conn) -> None:
    """An overlay that genuinely regroups the suffix resolves to an effective
    order that DIFFERS from the base DAG (base prefix + the 3-wave suffix)."""

    await _insert_feature(mq_conn, "feat-rs-rg")
    store = RegroupOverlayStore(mq_conn)
    overlay, _ = await _activated_overlay(
        mq_conn, store, feature_id="feat-rs-rg",
        overlay_id="overlay-09c2-rg", regrouped=True,
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-rg", 2)
    assert resolution.applied is True
    assert resolution.overlay_id == overlay.overlay_id
    # base prefix waves [0,1] + the overlay's 3-wave derived suffix.
    assert resolution.effective_execution_order == [
        ["T00"], ["T10"], ["T20"], ["T21"], ["T30"]
    ]
    assert resolution.observation["derived_group_count"] == 3
    assert resolution.observation["effective_group_count"] == 5


@pytest.mark.asyncio
async def test_resolver_no_active_overlay_reports_no_typed_overlay(mq_conn) -> None:
    """A feature with no active typed overlay → has_typed_overlay=False (the
    caller then keeps its non-overlay behavior)."""

    await _insert_feature(mq_conn, "feat-rs-none")
    store = RegroupOverlayStore(mq_conn)
    # No overlay inserted at all.
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-none", 2)
    assert resolution.has_typed_overlay is False
    assert resolution.applied is False
    assert resolution.effective_execution_order is None
    assert resolution.quiesce_reason == ""


@pytest.mark.asyncio
async def test_resolver_staged_only_overlay_is_not_active(mq_conn) -> None:
    """A `staged` (never-activated) overlay is not `active` → the resolver
    makes no decision (has_typed_overlay=False)."""

    await _insert_feature(mq_conn, "feat-rs-stg")
    store = RegroupOverlayStore(mq_conn)
    dag_id, sha = await _insert_base_dag(mq_conn, "feat-rs-stg")
    overlay = _valid_overlay(
        feature_id="feat-rs-stg", base_dag_artifact_id=dag_id, base_dag_sha256=sha
    )
    await store.insert_overlay(overlay)  # staged, never activated
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-stg", 2)
    assert resolution.has_typed_overlay is False
    assert resolution.applied is False


@pytest.mark.asyncio
async def test_resolver_probe_before_offset_reports_no_typed_overlay(mq_conn) -> None:
    """A dispatch probe BEFORE the overlay's group_idx_offset is not the
    overlay's concern — has_typed_overlay=False so the pre-offset groups
    dispatch from the base DAG unchanged."""

    await _insert_feature(mq_conn, "feat-rs-pre")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-pre")
    # group_idx 1 < the overlay's group_idx_offset 2.
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-pre", 1)
    assert resolution.has_typed_overlay is False
    assert resolution.applied is False


@pytest.mark.asyncio
async def test_resolver_rolled_back_overlay_is_not_active(mq_conn) -> None:
    """After a rollback the typed row is `rolled_back`, not `active` → the
    resolver makes no typed decision."""

    from iriai_build_v2.workflows.develop.execution.regroup_overlay_activation import (
        rollback_overlay,
    )

    await _insert_feature(mq_conn, "feat-rs-rb")
    store = RegroupOverlayStore(mq_conn)
    overlay, _ = await _activated_overlay(mq_conn, store, feature_id="feat-rs-rb")
    await rollback_overlay(
        store, feature_id="feat-rs-rb", overlay_id=overlay.overlay_id,
        reason="superseded",
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-rb", 2)
    assert resolution.has_typed_overlay is False
    assert resolution.applied is False


# ════════════════════════════════════════════════════════════════════════════
# Resolver — fail-closed mismatches (every one quiesces, NEVER applies)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resolver_quiesces_on_missing_active_marker(mq_conn) -> None:
    """An active typed row whose active-marker projection artifact is gone is
    an orphaned typed row — fail closed."""

    await _insert_feature(mq_conn, "feat-rs-nomark")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-nomark")
    # Delete the active-marker projection artifact rows.
    await mq_conn.execute(
        "DELETE FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-rs-nomark",
        "dag-regroup-active:g2-g3",
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-nomark", 2)
    assert resolution.has_typed_overlay is True
    assert resolution.applied is False
    assert resolution.effective_execution_order is None
    assert resolution.quiesce_reason == "regroup_invalid_active_marker_missing"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_missing_canonical_projection(mq_conn) -> None:
    """An active typed row whose canonical projection artifact is gone is an
    orphaned typed row — fail closed."""

    await _insert_feature(mq_conn, "feat-rs-nocanon")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-nocanon")
    await mq_conn.execute(
        "DELETE FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-rs-nocanon",
        "dag-regroup:g2-g3",
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-nocanon", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_canonical_projection_missing"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_inactive_marker(mq_conn) -> None:
    """A `rolled_back`-status marker appended over an `active` typed row (the
    latest marker row wins) is an inconsistency — fail closed."""

    await _insert_feature(mq_conn, "feat-rs-inact")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-inact")
    latest = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-rs-inact",
        "dag-regroup-active:g2-g3",
    )
    flipped = json.loads(latest)
    flipped["status"] = "rolled_back"
    await _insert_artifact(
        mq_conn, "feat-rs-inact", "dag-regroup-active:g2-g3", json.dumps(flipped)
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-inact", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_active_marker_inactive"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_marker_field_mismatch(mq_conn) -> None:
    """A marker whose identity fields disagree with the typed overlay row
    (e.g. base_dag_sha256) fails closed."""

    await _insert_feature(mq_conn, "feat-rs-field")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-field")
    latest = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-rs-field",
        "dag-regroup-active:g2-g3",
    )
    tampered = json.loads(latest)
    tampered["base_dag_sha256"] = "tampered-base-sha"
    await _insert_artifact(
        mq_conn, "feat-rs-field", "dag-regroup-active:g2-g3", json.dumps(tampered)
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-field", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_marker_field_mismatch"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_marker_canonical_sha_mismatch(mq_conn) -> None:
    """The carried P3-A id/sha cross-check: a marker whose canonical_sha256
    does not equal the loaded canonical artifact body sha fails closed."""

    await _insert_feature(mq_conn, "feat-rs-sha")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-sha")
    latest = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-rs-sha",
        "dag-regroup-active:g2-g3",
    )
    tampered = json.loads(latest)
    # Tamper canonical_sha256 only — keep canonical_artifact_id consistent so
    # the sha branch (not the id branch) is what fires.
    tampered["canonical_sha256"] = "0" * 64
    await _insert_artifact(
        mq_conn, "feat-rs-sha", "dag-regroup-active:g2-g3", json.dumps(tampered)
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-sha", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_marker_id_sha_mismatch"
    # The detail names the canonical_sha256 field.
    fields = {d.get("field") for d in resolution.details}
    assert "canonical_sha256" in fields


@pytest.mark.asyncio
async def test_resolver_quiesces_on_marker_canonical_id_mismatch(mq_conn) -> None:
    """The carried P3-A id/sha cross-check: a marker whose
    canonical_artifact_id does not match the loaded canonical artifact row id
    fails closed."""

    await _insert_feature(mq_conn, "feat-rs-cid")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-cid")
    latest = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-rs-cid",
        "dag-regroup-active:g2-g3",
    )
    tampered = json.loads(latest)
    tampered["canonical_artifact_id"] = 999999  # not the real canonical row id
    await _insert_artifact(
        mq_conn, "feat-rs-cid", "dag-regroup-active:g2-g3", json.dumps(tampered)
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-cid", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_marker_id_sha_mismatch"
    fields = {d.get("field") for d in resolution.details}
    assert "canonical_artifact_id" in fields


@pytest.mark.asyncio
async def test_resolver_quiesces_on_marker_rollback_id_not_in_compat(mq_conn) -> None:
    """The carried P3-A id cross-check: a marker whose rollback_artifact_id is
    not in the typed row's compatibility_artifact_ids fails closed."""

    await _insert_feature(mq_conn, "feat-rs-rid")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-rid")
    latest = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-rs-rid",
        "dag-regroup-active:g2-g3",
    )
    tampered = json.loads(latest)
    tampered["rollback_artifact_id"] = 888888  # not tracked in compat ids
    await _insert_artifact(
        mq_conn, "feat-rs-rid", "dag-regroup-active:g2-g3", json.dumps(tampered)
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-rid", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_marker_id_sha_mismatch"
    fields = {d.get("field") for d in resolution.details}
    assert "rollback_artifact_id_in_compat_ids" in fields


@pytest.mark.asyncio
async def test_resolver_quiesces_on_stale_base_dag_hash(mq_conn) -> None:
    """The source DAG drifted after activation — the loaded DAG sha no longer
    matches the overlay's base_dag_sha256 → fail closed."""

    await _insert_feature(mq_conn, "feat-rs-basesha")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-basesha")
    # Append a NEW `dag` artifact row with a different body — load_dag_artifact
    # picks the latest row (highest id), so the base DAG hash now drifts.
    drifted = ImplementationDAG(
        tasks=list(_BASE_DAG.tasks),
        num_teams=1,
        execution_order=[["T00"], ["T10"], ["T20"], ["T21"], ["T30"]],
        complete=True,
    )
    await _insert_artifact(
        mq_conn, "feat-rs-basesha", "dag", drifted.model_dump_json()
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-basesha", 2)
    assert resolution.applied is False
    # The id check fires first (the new row has a different id).
    assert resolution.quiesce_reason in (
        "regroup_invalid_base_dag_artifact_mismatch",
        "regroup_invalid_base_dag_hash_mismatch",
    )


@pytest.mark.asyncio
async def test_resolver_quiesces_on_missing_source_dag(mq_conn) -> None:
    """The source DAG artifact is gone — fail closed."""

    await _insert_feature(mq_conn, "feat-rs-nodag")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-nodag")
    await mq_conn.execute(
        "DELETE FROM artifacts WHERE feature_id = $1 AND key = 'dag'",
        "feat-rs-nodag",
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-nodag", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_source_dag_missing"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_validation_digest_mismatch(mq_conn) -> None:
    """The typed row's validation_digest no longer matches the latest
    successful validation record's digest → fail closed."""

    await _insert_feature(mq_conn, "feat-rs-digest")
    store = RegroupOverlayStore(mq_conn)
    overlay, row_id = await _activated_overlay(
        mq_conn, store, feature_id="feat-rs-digest"
    )
    # Corrupt the row's validation_digest column directly.
    await mq_conn.execute(
        "UPDATE execution_regroup_overlays SET validation_digest = $2 "
        "WHERE id = $1",
        row_id,
        "tampered-digest",
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-digest", 2)
    assert resolution.applied is False
    # Either the latest-validation digest mismatch or the payload digest
    # mismatch fires (the payload still carries the original digest).
    assert resolution.quiesce_reason in (
        "regroup_invalid_overlay_validation_digest_mismatch",
        "regroup_invalid_overlay_payload_digest_mismatch",
    )


@pytest.mark.asyncio
async def test_resolver_quiesces_on_no_successful_validation(mq_conn) -> None:
    """An active typed row whose latest_successful_validation_id is NULL
    fails closed (no validation evidence backs the active overlay)."""

    await _insert_feature(mq_conn, "feat-rs-noval")
    store = RegroupOverlayStore(mq_conn)
    _, row_id = await _activated_overlay(
        mq_conn, store, feature_id="feat-rs-noval"
    )
    await mq_conn.execute(
        "UPDATE execution_regroup_overlays "
        "SET latest_successful_validation_id = NULL WHERE id = $1",
        row_id,
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-noval", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_overlay_no_successful_validation"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_canonical_projection_drift(mq_conn) -> None:
    """The stored canonical projection body drifted from the typed overlay —
    a tampered canonical artifact body (same key, new row) fails closed.

    The marker's canonical_artifact_id/sha point at the ORIGINAL canonical row;
    appending a tampered canonical row makes the resolver load the tampered
    one, whose body sha no longer matches the marker — the P3-A id/sha
    cross-check fires (the marker references a different canonical row id)."""

    await _insert_feature(mq_conn, "feat-rs-drift")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-drift")
    # Append a tampered canonical artifact (latest row wins on load).
    await _insert_artifact(
        mq_conn, "feat-rs-drift", "dag-regroup:g2-g3",
        json.dumps({"tampered": "canonical-body"}),
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-drift", 2)
    assert resolution.applied is False
    assert resolution.effective_execution_order is None
    # The marker's canonical_artifact_id no longer matches the loaded (tampered)
    # canonical row id — the P3-A id/sha cross-check fails closed.
    assert resolution.quiesce_reason == "regroup_invalid_marker_id_sha_mismatch"


@pytest.mark.asyncio
async def test_resolver_quiesces_on_corrupt_active_marker_json(mq_conn) -> None:
    """A non-JSON / malformed active-marker artifact body fails closed."""

    await _insert_feature(mq_conn, "feat-rs-badjson")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(mq_conn, store, feature_id="feat-rs-badjson")
    await _insert_artifact(
        mq_conn, "feat-rs-badjson", "dag-regroup-active:g2-g3", "not-json{{{"
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-badjson", 2)
    assert resolution.applied is False
    assert resolution.quiesce_reason == "regroup_invalid_active_marker_unparseable"


@pytest.mark.asyncio
async def test_resolver_quiesces_when_revalidation_fails(mq_conn) -> None:
    """The active typed overlay no longer passes the 13-step validator (a
    base-DAG suffix mutation breaks task preservation) → fail closed via the
    validate_overlay(activation_check=True) funnel."""

    await _insert_feature(mq_conn, "feat-rs-reval")
    store = RegroupOverlayStore(mq_conn)
    _, row_id = await _activated_overlay(
        mq_conn, store, feature_id="feat-rs-reval"
    )
    # Mutate the typed row's payload_json so its derived_execution_order drops
    # a task — the resolver's validate_overlay re-run rejects it. The base DAG
    # id/hash columns are kept so the resolver reaches the validator.
    row = await mq_conn.fetchrow(
        "SELECT payload_json FROM execution_regroup_overlays WHERE id = $1",
        row_id,
    )
    payload = json.loads(row["payload_json"]) if isinstance(
        row["payload_json"], (str, bytes)
    ) else row["payload_json"]
    payload["derived_execution_order"] = [["T20"], ["T30"]]  # drops T21
    await mq_conn.execute(
        "UPDATE execution_regroup_overlays SET payload_json = $2::jsonb "
        "WHERE id = $1",
        row_id,
        json.dumps(payload),
    )
    resolution = await RegroupOverlayResolver(store).resolve("feat-rs-reval", 2)
    assert resolution.applied is False
    assert resolution.effective_execution_order is None
    # The validate_overlay(activation_check=True) funnel rejected it — the
    # mutated payload's derived order drops T21, so the validator's task-set
    # check fails. The resolver surfaces it as the umbrella reason.
    assert resolution.quiesce_reason == "regroup_invalid_overlay_validation_failed"
    # The detail carries the validator's own reason / failed step.
    assert resolution.details
    assert resolution.details[0].get("validation_reason")


# ════════════════════════════════════════════════════════════════════════════
# The dispatch-resolution swap (implementation.py)
# ════════════════════════════════════════════════════════════════════════════
#
# The dispatch swap is driven through the single ``mq_conn`` connection. The
# fake artifact store + the fake execution-control store both bind to that one
# bare connection: ``implementation._merge_queue_connection`` recognises a bare
# connection (no ``.acquire`` attribute) and yields it directly, so the resolver
# reads through the SAME connection the artifact store uses — no pool, no
# connection-exhaustion deadlock. The resolver is a pure read path and the
# legacy fallback also only reads, so one connection is correct.


class _FakeArtifacts:
    """A minimal artifact store over the test's bare Postgres connection.

    Implements just the ``.get`` / ``.get_record`` / ``.put`` surface
    ``_resolve_active_regroup_*`` consumes. ``feature`` is keyword-only on each
    method, matching the real artifact store the dispatch code calls.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get(self, key: str, *, feature) -> str | None:
        row = await self._conn.fetchval(
            "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
            "ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        return None if row is None else str(row)

    async def get_record(self, key: str, *, feature) -> dict | None:
        row = await self._conn.fetchrow(
            "SELECT id, value, created_at FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "value": str(row["value"]),
            "created_at": str(row["created_at"]),
        }

    async def put(self, key: str, value: str, *, feature) -> None:
        await self._conn.execute(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1,$2,$3)",
            feature.id,
            key,
            value,
        )


class _FakeFeature:
    def __init__(self, feature_id: str) -> None:
        self.id = feature_id
        self.slug = feature_id


class _FakeRunner:
    """A runner stub carrying just `artifacts` + `services` for the swap."""

    def __init__(self, artifacts: _FakeArtifacts, store: object) -> None:
        self.artifacts = artifacts
        # `_execution_control_store_for_runner` picks up
        # services['execution_control_store'] when it exposes `put_task_contract`.
        self.services = {"execution_control_store": store}


class _StoreConnShim:
    """An execution-control-store shim over a bare Postgres connection.

    `_execution_control_store_for_runner` accepts a services entry that has a
    `put_task_contract` attribute; `_merge_queue_connection` then reads its
    `_pool` — and when `_pool` is a bare `asyncpg.Connection` (no `.acquire`),
    `_merge_queue_connection` yields it directly. This shim is the minimum
    surface to route the dispatch swap to a real Postgres connection without
    standing up a full ExecutionControlStore or a pool.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._pool = conn

    def put_task_contract(self, *args, **kwargs):  # pragma: no cover - marker
        raise NotImplementedError


def _swap_runner(mq_conn: asyncpg.Connection) -> _FakeRunner:
    """A runner stub whose artifact store + control store share ``mq_conn``."""

    return _FakeRunner(_FakeArtifacts(mq_conn), _StoreConnShim(mq_conn))


@pytest.mark.asyncio
async def test_dispatch_swap_no_overlay_dispatches_unchanged(mq_conn) -> None:
    """A feature with NO active typed overlay AND no legacy marker dispatches
    EXACTLY as before — `_resolve_active_regroup_before_group_dispatch`
    returns the unchanged dag (the legacy `missing_active_regroup_marker`
    path is reached)."""

    from iriai_build_v2.workflows.develop.phases.implementation import (
        _resolve_active_regroup_before_group_dispatch,
    )

    await _insert_feature(mq_conn, "feat-sw-none")
    feature = _FakeFeature("feat-sw-none")
    runner = _swap_runner(mq_conn)
    # group_idx 45 (the legacy boundary) so the legacy path is fully
    # exercised; no typed overlay and no legacy marker exist.
    effective, failure, observation = (
        await _resolve_active_regroup_before_group_dispatch(
            runner, feature, _BASE_DAG, group_idx=45
        )
    )
    # No typed overlay → falls through to legacy → legacy quiesces on the
    # MISSING legacy active marker. Crucially the typed resolver did NOT
    # apply or quiesce — the behavior is the unchanged legacy behavior.
    assert effective is None
    assert "dag-regroup-active:g45-g73 is missing" in failure
    assert observation["reason"] == "missing_active_regroup_marker"


@pytest.mark.asyncio
async def test_dispatch_swap_pre_boundary_dispatches_unchanged(mq_conn) -> None:
    """A pre-G45 dispatch group with no typed overlay returns the unchanged
    dag (the legacy `not_regroup_boundary` early return)."""

    from iriai_build_v2.workflows.develop.phases.implementation import (
        _resolve_active_regroup_before_group_dispatch,
    )

    await _insert_feature(mq_conn, "feat-sw-pre")
    feature = _FakeFeature("feat-sw-pre")
    runner = _swap_runner(mq_conn)
    effective, failure, observation = (
        await _resolve_active_regroup_before_group_dispatch(
            runner, feature, _BASE_DAG, group_idx=3
        )
    )
    # Unchanged: the dag is returned as-is, no failure.
    assert effective is _BASE_DAG
    assert failure == ""
    assert observation["reason"] == "not_regroup_boundary"


@pytest.mark.asyncio
async def test_dispatch_swap_applies_active_typed_overlay(mq_conn) -> None:
    """A feature WITH an active typed overlay that GENUINELY regroups the
    suffix does the overlay — the dispatch swap returns a DISTINCT effective
    dag carrying the derived (3-wave) execution order; the root dag is not
    mutated."""

    from iriai_build_v2.workflows.develop.phases.implementation import (
        _resolve_active_regroup_before_group_dispatch,
    )

    await _insert_feature(mq_conn, "feat-sw-ov")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(
        mq_conn, store, feature_id="feat-sw-ov",
        overlay_id="overlay-09c2-rg", regrouped=True,
    )
    feature = _FakeFeature("feat-sw-ov")
    runner = _swap_runner(mq_conn)
    # group_idx 2 == the overlay's group_idx_offset.
    effective, failure, observation = (
        await _resolve_active_regroup_before_group_dispatch(
            runner, feature, _BASE_DAG, group_idx=2
        )
    )
    assert failure == ""
    assert effective is not None
    # The effective dag carries the regrouped 3-wave derived suffix (base
    # prefix waves [0,1] + the overlay's derived waves). The root dag is NOT
    # mutated — `effective` is a distinct deep copy.
    assert effective.execution_order == [
        ["T00"], ["T10"], ["T20"], ["T21"], ["T30"]
    ]
    assert effective is not _BASE_DAG
    assert _BASE_DAG.execution_order == [["T00"], ["T10"], ["T20", "T21"], ["T30"]]
    assert observation["status"] == "applied"
    assert observation["overlay_id"] == "overlay-09c2-rg"
    # The observation artifact was projected.
    obs = await mq_conn.fetchval(
        "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
        "ORDER BY id DESC LIMIT 1",
        "feat-sw-ov",
        "dag-regroup-observation:g2-g3",
    )
    assert obs is not None
    assert json.loads(obs)["overlay_id"] == "overlay-09c2-rg"


@pytest.mark.asyncio
async def test_dispatch_swap_quiesces_on_invalid_typed_overlay(mq_conn) -> None:
    """A feature with an active typed overlay whose marker is orphaned →
    the dispatch swap fails closed (a regroup_invalid quiesce failure)."""

    from iriai_build_v2.workflows.develop.phases.implementation import (
        _resolve_active_regroup_before_group_dispatch,
    )

    await _insert_feature(mq_conn, "feat-sw-bad")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(
        mq_conn, store, feature_id="feat-sw-bad",
        overlay_id="overlay-09c2-rg", regrouped=True,
    )
    # Orphan the typed row: delete the active-marker projection.
    await mq_conn.execute(
        "DELETE FROM artifacts WHERE feature_id = $1 AND key = $2",
        "feat-sw-bad",
        "dag-regroup-active:g2-g3",
    )
    feature = _FakeFeature("feat-sw-bad")
    runner = _swap_runner(mq_conn)
    effective, failure, observation = (
        await _resolve_active_regroup_before_group_dispatch(
            runner, feature, _BASE_DAG, group_idx=2
        )
    )
    # Fail-closed: no executable effective dag, a regroup_invalid failure.
    assert effective is None
    assert "failed validation" in failure
    assert observation["reason"].startswith("regroup_invalid")
    assert observation["overlay_id"] == "overlay-09c2-rg"


@pytest.mark.asyncio
async def test_dispatch_swap_typed_overlay_precedes_legacy_marker(mq_conn) -> None:
    """When BOTH an active typed overlay AND a legacy g45-g73 marker exist,
    the typed resolver is authoritative — the typed overlay is applied (the
    legacy marker is not consulted)."""

    from iriai_build_v2.workflows.develop.phases.implementation import (
        _resolve_active_regroup_before_group_dispatch,
    )

    await _insert_feature(mq_conn, "feat-sw-both")
    store = RegroupOverlayStore(mq_conn)
    await _activated_overlay(
        mq_conn, store, feature_id="feat-sw-both",
        overlay_id="overlay-09c2-rg", regrouped=True,
    )
    # Also drop a (bogus) legacy g45-g73 active marker — if the legacy
    # path were consulted it would error on this bogus marker.
    await _insert_artifact(
        mq_conn, "feat-sw-both", "dag-regroup-active:g45-g73",
        json.dumps({"status": "active", "garbage": True}),
    )
    feature = _FakeFeature("feat-sw-both")
    runner = _swap_runner(mq_conn)
    effective, failure, observation = (
        await _resolve_active_regroup_before_group_dispatch(
            runner, feature, _BASE_DAG, group_idx=2
        )
    )
    # The typed overlay was applied; the legacy marker was never consulted.
    assert failure == ""
    assert effective is not None
    assert effective.execution_order == [
        ["T00"], ["T10"], ["T20"], ["T21"], ["T30"]
    ]
    assert observation["status"] == "applied"
    assert observation["overlay_id"] == "overlay-09c2-rg"
