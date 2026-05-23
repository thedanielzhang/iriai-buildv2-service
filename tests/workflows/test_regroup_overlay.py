"""Slice 09a tests — typed regroup overlay schema + models.

Scope is strictly 09a: the deterministic ``overlay_id`` / ``overlay_slug`` /
``metric_id`` derivations and the typed-model field/validator behavior. The
``validate_overlay`` algorithm (09b), activation / rollback / resolver (09c),
and scheduler-feedback logic (09d) are tested in later sub-slices.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.models import stable_digest
from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
    OverlayBarrier,
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupActiveMarker,
    RegroupOverlay,
    RegroupRollbackPlan,
    SchedulerFeedback,
    SchedulerGroupMetric,
    derive_metric_id,
    derive_overlay_id,
    derive_overlay_slug,
)

_NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


# ── overlay_id derivation ───────────────────────────────────────────────────


def _overlay_id_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "feature_id": "feat-1",
        "source_dag_key": "dag",
        "base_dag_artifact_id": 100,
        "base_dag_sha256": "basehash",
        "group_idx_offset": 45,
        "derived_execution_order": [["T1", "T2"], ["T3"]],
    }
    base.update(overrides)
    return base


def test_overlay_id_is_deterministic_and_24_hex() -> None:
    first = derive_overlay_id(**_overlay_id_kwargs())  # type: ignore[arg-type]
    second = derive_overlay_id(**_overlay_id_kwargs())  # type: ignore[arg-type]
    assert first == second
    assert len(first) == 24
    assert all(ch in "0123456789abcdef" for ch in first)


def test_overlay_id_is_invariant_to_within_wave_task_order() -> None:
    """doc 09: the id is hashed over the *canonical* derived order."""

    ordered = derive_overlay_id(
        **_overlay_id_kwargs(derived_execution_order=[["T1", "T2"], ["T3"]])  # type: ignore[arg-type]
    )
    shuffled = derive_overlay_id(
        **_overlay_id_kwargs(derived_execution_order=[["T2", "T1"], ["T3"]])  # type: ignore[arg-type]
    )
    assert ordered == shuffled


def test_overlay_id_changes_when_wave_partition_changes() -> None:
    """A different wave partition of the same tasks is a different overlay."""

    two_waves = derive_overlay_id(
        **_overlay_id_kwargs(derived_execution_order=[["T1", "T2"], ["T3"]])  # type: ignore[arg-type]
    )
    three_waves = derive_overlay_id(
        **_overlay_id_kwargs(derived_execution_order=[["T1"], ["T2"], ["T3"]])  # type: ignore[arg-type]
    )
    assert two_waves != three_waves


@pytest.mark.parametrize(
    "field, value",
    [
        ("feature_id", "feat-2"),
        ("source_dag_key", "dag-alt"),
        ("base_dag_artifact_id", 999),
        ("base_dag_sha256", "otherhash"),
        ("group_idx_offset", 46),
    ],
)
def test_overlay_id_changes_with_every_identity_field(field: str, value: object) -> None:
    baseline = derive_overlay_id(**_overlay_id_kwargs())  # type: ignore[arg-type]
    mutated = derive_overlay_id(**_overlay_id_kwargs(**{field: value}))  # type: ignore[arg-type]
    assert mutated != baseline


def test_overlay_id_matches_documented_hash_formula() -> None:
    """The id is exactly sha256(identity tuple)[:24] over the canonical order."""

    expected = stable_digest(
        {
            "feature_id": "feat-1",
            "source_dag_key": "dag",
            "base_dag_artifact_id": 100,
            "base_dag_sha256": "basehash",
            "group_idx_offset": 45,
            "derived_execution_order": [["T1", "T2"], ["T3"]],
        }
    )[:24]
    assert derive_overlay_id(**_overlay_id_kwargs()) == expected  # type: ignore[arg-type]


# ── overlay_slug derivation ─────────────────────────────────────────────────


def test_overlay_slug_preserves_legacy_g45_g73() -> None:
    """doc 09: the legacy g45-g73 spelling falls out of the formula."""

    assert (
        derive_overlay_slug(group_idx_offset=45, last_original_group=73)
        == "g45-g73"
    )


def test_overlay_slug_for_unbounded_suffix_uses_tail() -> None:
    assert (
        derive_overlay_slug(group_idx_offset=45, last_original_group=None)
        == "g45-tail"
    )


def test_overlay_slug_is_deterministic_for_non_g45_suffix() -> None:
    assert (
        derive_overlay_slug(group_idx_offset=12, last_original_group=20)
        == "g12-g20"
    )
    assert (
        derive_overlay_slug(group_idx_offset=7, last_original_group=None)
        == "g7-tail"
    )


# ── metric_id derivation ────────────────────────────────────────────────────


def _metric_id_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "feature_id": "feat-1",
        "group_idx": 45,
        "overlay_id": "abc123",
        "checkpoint_projection_id": 7,
        "task_ids": ["T1", "T2"],
        "evidence_ids": [1, 2, 3],
    }
    base.update(overrides)
    return base


def test_metric_id_is_deterministic_and_24_hex() -> None:
    first = derive_metric_id(**_metric_id_kwargs())  # type: ignore[arg-type]
    second = derive_metric_id(**_metric_id_kwargs())  # type: ignore[arg-type]
    assert first == second
    assert len(first) == 24
    assert all(ch in "0123456789abcdef" for ch in first)


def test_metric_id_invariant_to_task_and_evidence_order() -> None:
    ordered = derive_metric_id(
        **_metric_id_kwargs(task_ids=["T1", "T2"], evidence_ids=[1, 2, 3])  # type: ignore[arg-type]
    )
    shuffled = derive_metric_id(
        **_metric_id_kwargs(task_ids=["T2", "T1"], evidence_ids=[3, 1, 2])  # type: ignore[arg-type]
    )
    assert ordered == shuffled


def test_metric_id_uses_root_sentinel_for_missing_overlay() -> None:
    """doc 09: ``overlay_id or "root"`` — root metrics hash a stable sentinel."""

    none_overlay = derive_metric_id(**_metric_id_kwargs(overlay_id=None))  # type: ignore[arg-type]
    literal_root = derive_metric_id(**_metric_id_kwargs(overlay_id="root"))  # type: ignore[arg-type]
    assert none_overlay == literal_root


def test_metric_id_changes_with_group_idx() -> None:
    baseline = derive_metric_id(**_metric_id_kwargs())  # type: ignore[arg-type]
    mutated = derive_metric_id(**_metric_id_kwargs(group_idx=46))  # type: ignore[arg-type]
    assert mutated != baseline


# ── model construction / validator behavior ────────────────────────────────


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
        "overlay_id": "abc123",
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
            OverlayBarrier(
                barrier_id="b1", task_ids=["T1"], source="task_contract"
            )
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


def test_regroup_overlay_constructs_and_round_trips() -> None:
    overlay = _overlay()
    assert overlay.schema_version == 1
    assert overlay.status == "staged"
    # JSON round-trip preserves the typed structure.
    restored = RegroupOverlay.model_validate_json(overlay.model_dump_json())
    assert restored == overlay
    # Nested typed members survive the round-trip as models, not dicts.
    assert isinstance(restored.activation_contract, RegroupActivationContract)
    assert isinstance(restored.barriers[0], OverlayBarrier)
    assert isinstance(restored.speed_index["T1"], OverlayTaskSpeedMetadata)


def test_regroup_overlay_schema_version_pinned_to_1() -> None:
    with pytest.raises(ValidationError):
        _overlay(schema_version=2)


@pytest.mark.parametrize(
    "status",
    ["staged", "active", "rolled_back", "superseded", "rejected"],
)
def test_regroup_overlay_accepts_every_documented_status(status: str) -> None:
    assert _overlay(status=status).status == status


def test_regroup_overlay_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        _overlay(status="paused")


def test_regroup_overlay_optional_timestamps_default_none() -> None:
    overlay = _overlay()
    assert overlay.activated_at is None
    assert overlay.rolled_back_at is None
    assert overlay.reason == ""
    assert overlay.validation_evidence_ids == []
    assert overlay.scheduler_feedback_ids == []


def test_overlay_barrier_defaults_hard_true() -> None:
    barrier = OverlayBarrier(barrier_id="b", task_ids=["T1"], source="legacy")
    assert barrier.hard is True


def test_overlay_barrier_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        OverlayBarrier(barrier_id="b", task_ids=["T1"], source="autopilot")


@pytest.mark.parametrize(
    "source", ["task_contract", "speed_index", "operator", "legacy"]
)
def test_overlay_barrier_accepts_every_documented_source(source: str) -> None:
    barrier = OverlayBarrier(barrier_id="b", task_ids=["T1"], source=source)
    assert barrier.source == source


def test_overlay_task_speed_metadata_defaults() -> None:
    meta = OverlayTaskSpeedMetadata()
    assert meta.semantic_lane == "unknown"
    assert meta.barrier == "unknown"
    assert meta.critical_path_depth == 0
    assert meta.commit_risk == 0
    assert meta.verification_cost == 0
    assert meta.unknown_write is False
    assert meta.scheduler_feedback_ids == []


def test_activation_contract_requires_advisory_lock_by_default() -> None:
    assert _activation_contract().requires_feature_advisory_lock is True


def test_rollback_plan_forward_only_after_start_default() -> None:
    assert _rollback_plan().forward_only_after_start is True


def test_regroup_active_marker_constructs_and_round_trips() -> None:
    marker = RegroupActiveMarker(
        status="active",
        feature_id="feat-1",
        overlay_id="abc123",
        overlay_slug="g45-g73",
        overlay_row_id=1,
        canonical_artifact_key="dag-regroup:g45-g73",
        canonical_artifact_id=200,
        canonical_sha256="csha",
        active_marker_key="dag-regroup-active:g45-g73",
        rollback_artifact_key="dag-regroup-rollback:g45-g73",
        rollback_artifact_id=201,
        source_dag_key="dag",
        base_dag_artifact_id=100,
        base_dag_sha256="basehash",
        checkpointed_group=44,
        group_idx_offset=45,
        validation_digest="valdig",
    )
    assert marker.schema_version == 1
    restored = RegroupActiveMarker.model_validate_json(marker.model_dump_json())
    assert restored == marker


def test_regroup_active_marker_rejects_non_marker_status() -> None:
    """The marker status is only ``active`` / ``rolled_back`` (not ``staged``)."""

    with pytest.raises(ValidationError):
        RegroupActiveMarker(
            status="staged",
            feature_id="feat-1",
            overlay_id="abc123",
            overlay_slug="g45-g73",
            overlay_row_id=1,
            canonical_artifact_key="dag-regroup:g45-g73",
            canonical_artifact_id=200,
            canonical_sha256="csha",
            active_marker_key="dag-regroup-active:g45-g73",
            rollback_artifact_key="dag-regroup-rollback:g45-g73",
            rollback_artifact_id=201,
            source_dag_key="dag",
            base_dag_artifact_id=100,
            base_dag_sha256="basehash",
            checkpointed_group=44,
            group_idx_offset=45,
            validation_digest="valdig",
        )


def test_scheduler_group_metric_constructs_and_round_trips() -> None:
    metric = SchedulerGroupMetric(
        metric_id="m1",
        feature_id="feat-1",
        group_idx=45,
        state="completed",
        completed=True,
        task_ids=["T1", "T2"],
        task_count=2,
        lane_counts={"backend": 2},
        barrier_counts={"b1": 1},
        repo_count=1,
        write_set_count=2,
        unknown_write_count=0,
        max_dependency_depth=1,
        max_commit_risk=0,
        max_verification_cost=0,
        verify_count=2,
        expanded_verify_count=0,
        product_repair_cycles=0,
        workflow_repair_cycles=0,
        commit_failures=0,
        merge_conflicts=0,
        queue_retries=0,
        runtime_failures=0,
        workspace_failures=0,
        stale_projection_repairs=0,
        verify_cost_units=4,
        tasks_per_hour=1.0,
        hours_per_task=1.0,
        product_repair_cycles_per_task=0.0,
        workflow_repair_cycles_per_task=0.0,
        commit_failures_per_task=0.0,
        merge_conflicts_per_task=0.0,
        verify_cost_per_task=2.0,
        tail_risks=[],
        evidence_ids=[1, 2],
    )
    restored = SchedulerGroupMetric.model_validate_json(metric.model_dump_json())
    assert restored == metric
    assert restored.active is False
    assert restored.data_quality_flags == []
    # Nullable metric fields are genuinely optional.
    incomplete = metric.model_copy(
        update={"completed": False, "tasks_per_hour": None, "hours_per_task": None}
    )
    assert incomplete.tasks_per_hour is None


def test_scheduler_group_metric_rejects_unknown_state() -> None:
    with pytest.raises(ValidationError):
        SchedulerGroupMetric(
            metric_id="m1",
            feature_id="feat-1",
            group_idx=45,
            state="paused",
            completed=False,
            task_ids=["T1"],
            task_count=1,
            lane_counts={},
            barrier_counts={},
            repo_count=1,
            write_set_count=1,
            unknown_write_count=0,
            max_dependency_depth=0,
            max_commit_risk=0,
            max_verification_cost=0,
            verify_count=0,
            expanded_verify_count=0,
            product_repair_cycles=0,
            workflow_repair_cycles=0,
            commit_failures=0,
            merge_conflicts=0,
            queue_retries=0,
            runtime_failures=0,
            workspace_failures=0,
            stale_projection_repairs=0,
            verify_cost_units=0,
            tasks_per_hour=None,
            hours_per_task=None,
            product_repair_cycles_per_task=None,
            workflow_repair_cycles_per_task=None,
            commit_failures_per_task=None,
            merge_conflicts_per_task=None,
            verify_cost_per_task=None,
            tail_risks=[],
            evidence_ids=[],
        )


def test_scheduler_feedback_constructs_and_round_trips() -> None:
    feedback = SchedulerFeedback(
        feedback_id="fb1",
        feature_id="feat-1",
        generated_at=_NOW,
        window_start_group=45,
        window_end_group=50,
        lane="backend",
        barrier="b1",
        completed_groups=[45, 46],
        sample_count=2,
        tasks_per_hour=1.5,
        hours_per_task_p50=0.6,
        hours_per_task_p75=0.7,
        product_repair_cycles_per_task=0.0,
        workflow_repair_cycles_per_task=0.0,
        commit_failures_per_task=0.0,
        merge_conflicts_per_task=0.0,
        verify_cost_per_task=2.0,
        queue_wait_p75_h=0.1,
        data_quality="sufficient",
        recommended_cap=6,
        current_cap=4,
        confidence="medium",
        reasons=["evidence cap from p75"],
        metric_ids=["m1", "m2"],
        evidence_ids=[1, 2, 3],
    )
    assert feedback.schema_version == 1
    restored = SchedulerFeedback.model_validate_json(feedback.model_dump_json())
    assert restored == feedback


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("data_quality", "unknown"),
        ("confidence", "certain"),
    ],
)
def test_scheduler_feedback_rejects_bad_enums(field: str, bad_value: str) -> None:
    kwargs: dict[str, object] = {
        "feedback_id": "fb1",
        "feature_id": "feat-1",
        "generated_at": _NOW,
        "window_start_group": 45,
        "window_end_group": 50,
        "lane": "backend",
        "barrier": "b1",
        "completed_groups": [45],
        "sample_count": 1,
        "tasks_per_hour": None,
        "hours_per_task_p50": None,
        "hours_per_task_p75": None,
        "product_repair_cycles_per_task": None,
        "workflow_repair_cycles_per_task": None,
        "commit_failures_per_task": None,
        "merge_conflicts_per_task": None,
        "verify_cost_per_task": None,
        "queue_wait_p75_h": None,
        "data_quality": "insufficient",
        "recommended_cap": 4,
        "current_cap": 4,
        "confidence": "low",
        "reasons": [],
        "metric_ids": [],
        "evidence_ids": [],
    }
    kwargs[field] = bad_value
    with pytest.raises(ValidationError):
        SchedulerFeedback(**kwargs)
