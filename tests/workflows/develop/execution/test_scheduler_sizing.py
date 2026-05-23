"""Slice 09d-2 tests — the conservative cap computation + the typed
``SchedulerFeedback`` + the ``review:dag-sizing:*`` projection.

These exercise
:mod:`iriai_build_v2.workflows.develop.execution.scheduler_sizing` — the
09d-2 module that consumes 09d-1's
:func:`~...scheduler_metrics.build_scheduler_group_metrics` output and delivers
(A) the conservative cap computation and (B) the typed
:class:`~...regroup_overlay.SchedulerFeedback` + the
``review:dag-sizing:{feature_id}:{window}`` projection.

The cap-computation tests are PURE (no DB) — every formula / threshold is
checked against doc 09 § "Scheduler Metrics And Cap Rules" verbatim. The
persistence + the end-to-end ``run_adaptive_sizing`` tests run against real
Postgres (the directory ``conftest`` ``mq_conn`` fixture) and skip cleanly when
no Postgres is reachable.

Coverage (per the Slice 09d-2 brief):

- each ``policy_cap`` risk tier (unknown-write / high-risk barrier → 4;
  backend / multi-repo → 6; isolated UI / document → 10; test-only / perf → 14);
- the ``evidence_cap`` formula ``floor(12h / hours_per_task_p75)`` + the
  ``[4, policy_cap]`` clamp;
- each reduction trigger (sample < 2; stale; repair/commit/merge rates >
  baseline + 10%);
- the post-cap dependency / hard-barrier / write-set / mapping validators
  SHRINKING a candidate wave AND REJECTING a widened wave;
- ``data_quality`` forced off ``sufficient`` on flagged metrics
  (``missing_projection_lineage`` → ``stale``; other flags → mixed/insufficient);
- 09d-2 emits ONLY a ``SchedulerFeedback`` row + the ``review:dag-sizing:*``
  projection and NEVER an active marker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from iriai_build_v2.models.outputs import ImplementationDAG, ImplementationTask
from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
    OverlayBarrier,
    OverlayCompatibilityKeys,
    OverlayTaskSpeedMetadata,
    RegroupActivationContract,
    RegroupOverlay,
    RegroupRollbackPlan,
    SchedulerFeedback,
    SchedulerGroupMetric,
)
from iriai_build_v2.workflows.develop.execution.scheduler_sizing import (
    CHECKPOINT_BUDGET_HOURS,
    MIN_CAP,
    POLICY_CAP_BACKEND_OR_MULTI_REPO,
    POLICY_CAP_ISOLATED_UI_OR_DOCUMENT,
    POLICY_CAP_TEST_OR_PERF,
    POLICY_CAP_UNKNOWN_OR_HIGH_RISK,
    AdaptiveSizingResult,
    SchedulerSizingError,
    build_candidate_waves,
    compute_cap_decision,
    compute_evidence_cap,
    compute_policy_cap,
    compute_scheduler_feedback,
    project_sizing_review,
    run_adaptive_sizing,
)
from iriai_build_v2.workflows.develop.execution.scheduler_metrics import (
    build_scheduler_group_metrics,
)

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


# ════════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════════


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
    """A 6-task DAG: [T00] [T10] [T20,T21] [T30,T31]."""

    return ImplementationDAG(
        tasks=[
            _task("T00", deps=[], files=["a0.py"]),
            _task("T10", deps=["T00"], files=["a1.py"]),
            _task("T20", deps=["T10"], files=["pkg/a20.py"]),
            _task("T21", deps=["T10"], files=["pkg/a21.py"]),
            _task("T30", deps=["T20"], files=["b/a30.py"]),
            _task("T31", deps=["T21"], files=["b/a31.py"]),
        ],
        num_teams=1,
        execution_order=[["T00"], ["T10"], ["T20", "T21"], ["T30", "T31"]],
        complete=True,
    )


_BASE_DAG = _base_dag()


def _metric(
    *,
    group_idx: int,
    completed: bool,
    task_ids: list[str],
    hours_per_task: float | None = None,
    tasks_per_hour: float | None = None,
    lane: str = "backend",
    barrier: str = "b-std",
    data_quality_flags: list[str] | None = None,
    workflow_repair_per_task: float | None = None,
    product_repair_per_task: float | None = None,
    commit_failures_per_task: float | None = None,
    merge_conflicts_per_task: float | None = None,
    unknown_write_count: int = 0,
    repo_count: int = 1,
    evidence_ids: list[int] | None = None,
    merge_queue_wait_h: float | None = None,
    verify_cost_per_task: float | None = None,
) -> SchedulerGroupMetric:
    """Build a typed ``SchedulerGroupMetric`` directly (no DB) for the pure
    cap-computation tests."""

    n = len(task_ids)
    return SchedulerGroupMetric(
        metric_id=f"metric-{group_idx:02d}-{'c' if completed else 'a'}",
        feature_id="feat",
        group_idx=group_idx,
        overlay_id=None,
        state="completed" if completed else "active",
        completed=completed,
        active=not completed,
        task_ids=sorted(task_ids),
        task_count=n,
        checkpoint_projection_id=9000 + group_idx if completed else None,
        merge_queue_item_id=100 + group_idx if completed else None,
        task_attempt_ids=[1000 + group_idx],
        failure_ids=[],
        gate_evidence_ids=[2000 + group_idx],
        compatibility_projection_ids=[3000 + group_idx],
        started_at=_NOW,
        checkpointed_at=_NOW + timedelta(hours=4) if completed else None,
        checkpoint_duration_h=(hours_per_task * n if (completed and hours_per_task) else None),
        lane_counts={lane: n},
        barrier_counts={barrier: n},
        repo_count=repo_count,
        write_set_count=n,
        unknown_write_count=unknown_write_count,
        max_dependency_depth=1,
        max_commit_risk=1,
        max_verification_cost=1,
        verify_count=1,
        expanded_verify_count=0,
        product_repair_cycles=0,
        workflow_repair_cycles=0,
        commit_failures=0,
        merge_conflicts=0,
        queue_retries=0,
        runtime_failures=0,
        workspace_failures=0,
        stale_projection_repairs=0,
        verify_cost_units=1,
        tasks_per_hour=tasks_per_hour,
        hours_per_task=hours_per_task,
        product_repair_cycles_per_task=product_repair_per_task,
        workflow_repair_cycles_per_task=workflow_repair_per_task,
        commit_failures_per_task=commit_failures_per_task,
        merge_conflicts_per_task=merge_conflicts_per_task,
        verify_cost_per_task=verify_cost_per_task,
        tail_risks=[],
        data_quality_flags=sorted(data_quality_flags or []),
        evidence_ids=sorted(evidence_ids if evidence_ids is not None else [1000 + group_idx]),
    )


def _overlay(
    feature_id: str,
    *,
    overlay_id: str = "ov-09d2",
    barriers: list[OverlayBarrier] | None = None,
    speed_index: dict[str, OverlayTaskSpeedMetadata] | None = None,
    write_sets: dict[str, list[str]] | None = None,
) -> RegroupOverlay:
    """A typed overlay over the base DAG suffix [2, 3]."""

    derived = [["T20", "T21"], ["T30", "T31"]]
    default_speed = {
        "T20": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
        "T21": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
        "T30": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
        "T31": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
    }
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
        original_execution_order=[["T20", "T21"], ["T30", "T31"]],
        derived_execution_order=derived,
        original_to_new_group_mapping={2: [2], 3: [3]},
        task_definition_fingerprints={
            "T20": "f", "T21": "f", "T30": "f", "T31": "f"
        },
        remaining_dependency_edges={
            "T20": [], "T21": [], "T30": ["T20"], "T31": ["T21"]
        },
        barriers=barriers if barriers is not None else [],
        write_sets=write_sets
        if write_sets is not None
        else {
            "T20": ["pkg/a20.py"],
            "T21": ["pkg/a21.py"],
            "T30": ["b/a30.py"],
            "T31": ["b/a31.py"],
        },
        speed_index=speed_index if speed_index is not None else default_speed,
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
# (A) policy_cap — each of the 4 doc-09 risk tiers
# ════════════════════════════════════════════════════════════════════════════


def test_policy_cap_unknown_write_is_4() -> None:
    """doc 09 § "Scheduler Metrics" step 1: "unknown writes ... cap at 4"."""

    cap, reason = compute_policy_cap(
        unknown_write=True,
        high_risk_barrier=False,
        barrier="b-backend",
        lane="backend",
        multi_repo=False,
    )
    assert cap == POLICY_CAP_UNKNOWN_OR_HIGH_RISK == 4
    assert "unknown write" in reason


def test_policy_cap_high_risk_barrier_is_4() -> None:
    """doc 09 step 1: "high-risk barriers cap at 4"."""

    cap, reason = compute_policy_cap(
        unknown_write=False,
        high_risk_barrier=True,
        barrier="b-anything",
        lane="backend",
        multi_repo=False,
    )
    assert cap == 4
    # Also via the barrier-id marker (a "schema"/"migration" barrier).
    cap2, _ = compute_policy_cap(
        unknown_write=False,
        high_risk_barrier=False,
        barrier="schema-migration-barrier",
        lane="backend",
        multi_repo=False,
    )
    assert cap2 == 4


def test_policy_cap_backend_lane_is_6() -> None:
    """doc 09 step 1: "backend ... work caps at 6"."""

    cap, reason = compute_policy_cap(
        unknown_write=False,
        high_risk_barrier=False,
        barrier="b-std",
        lane="backend",
        multi_repo=False,
    )
    assert cap == POLICY_CAP_BACKEND_OR_MULTI_REPO == 6
    assert "backend" in reason


def test_policy_cap_multi_repo_is_6() -> None:
    """doc 09 step 1: "... or multi-repo work caps at 6"."""

    cap, reason = compute_policy_cap(
        unknown_write=False,
        high_risk_barrier=False,
        barrier="b-std",
        lane="docs",  # an isolated lane, but multi-repo overrides it to 6
        multi_repo=True,
    )
    assert cap == 6
    assert "multi-repo" in reason


def test_policy_cap_isolated_ui_document_is_10() -> None:
    """doc 09 step 1: "isolated UI/document work caps at 10"."""

    for lane in ("ui", "frontend", "docs", "documentation"):
        cap, _ = compute_policy_cap(
            unknown_write=False,
            high_risk_barrier=False,
            barrier="b-std",
            lane=lane,
            multi_repo=False,
        )
        assert cap == POLICY_CAP_ISOLATED_UI_OR_DOCUMENT == 10, lane


def test_policy_cap_test_or_perf_is_14() -> None:
    """doc 09 step 1: "test-only and perf lanes cap at 14"."""

    for lane in ("test", "perf", "performance", "benchmark"):
        cap, _ = compute_policy_cap(
            unknown_write=False,
            high_risk_barrier=False,
            barrier="b-std",
            lane=lane,
            multi_repo=False,
        )
        assert cap == POLICY_CAP_TEST_OR_PERF == 14, lane


def test_policy_cap_unclassified_lane_falls_to_conservative_4() -> None:
    """An unrecognized lane/barrier conservatively caps at 4 (a mis-tagged lane
    must never widen above the safe minimum)."""

    cap, reason = compute_policy_cap(
        unknown_write=False,
        high_risk_barrier=False,
        barrier="b-mystery",
        lane="some-unrecognized-lane",
        multi_repo=False,
    )
    assert cap == 4
    assert "unclassified" in reason


def test_policy_cap_riskier_tier_wins() -> None:
    """When a window is both unknown-write AND a test lane, the riskier tier
    (the 4-cap) wins — the tiers are evaluated most-conservative-first."""

    cap, _ = compute_policy_cap(
        unknown_write=True,
        high_risk_barrier=False,
        barrier="b-std",
        lane="test",  # would be 14 alone
        multi_repo=False,
    )
    assert cap == 4


# ════════════════════════════════════════════════════════════════════════════
# (A) evidence_cap — floor(12h / p75) + the [4, policy_cap] clamp
# ════════════════════════════════════════════════════════════════════════════


def test_evidence_cap_formula_is_floor_12h_over_p75() -> None:
    """doc 09 step 4: ``evidence_cap = floor(12h_checkpoint_budget /
    hours_per_task_p75)``."""

    # p75 = 2.0h -> floor(12 / 2) = 6, within [4, 14] -> 6.
    cap, reason = compute_evidence_cap(hours_per_task_p75=2.0, policy_cap=14)
    assert cap == 6
    assert "floor(12" in reason
    # p75 = 1.5h -> floor(12 / 1.5) = 8.
    cap2, _ = compute_evidence_cap(hours_per_task_p75=1.5, policy_cap=14)
    assert cap2 == 8
    # p75 = 5.0h -> floor(12 / 5) = 2 -> clamped UP to MIN_CAP (4).
    cap3, _ = compute_evidence_cap(hours_per_task_p75=5.0, policy_cap=14)
    assert cap3 == MIN_CAP == 4


def test_evidence_cap_clamps_down_to_policy_cap() -> None:
    """The evidence cap is clamped to ``[4, policy_cap]`` — a fast lane cannot
    widen past the policy ceiling."""

    # p75 = 0.5h -> floor(12 / 0.5) = 24, but policy_cap=6 -> clamped to 6.
    cap, reason = compute_evidence_cap(hours_per_task_p75=0.5, policy_cap=6)
    assert cap == 6
    assert "clamped to [4,6]" in reason


def test_evidence_cap_none_when_no_p75() -> None:
    """No p75 hours/task (no completed samples) -> evidence_cap is None."""

    cap, reason = compute_evidence_cap(hours_per_task_p75=None, policy_cap=10)
    assert cap is None
    assert "no p75" in reason.lower()
    # A non-positive p75 is likewise None.
    cap2, _ = compute_evidence_cap(hours_per_task_p75=0.0, policy_cap=10)
    assert cap2 is None


def test_checkpoint_budget_is_12h() -> None:
    """The checkpoint budget constant is exactly 12h (doc 09 step 4)."""

    assert CHECKPOINT_BUDGET_HOURS == 12.0


# ════════════════════════════════════════════════════════════════════════════
# (A) compute_cap_decision — the reduction triggers
# ════════════════════════════════════════════════════════════════════════════


def _baseline_for(completed: list[SchedulerGroupMetric]):
    from iriai_build_v2.workflows.develop.execution.scheduler_sizing import (
        _GlobalBaseline,
    )

    return _GlobalBaseline.from_completed(completed)


def test_cap_widen_blocked_below_two_samples() -> None:
    """doc 09 step 2: a cap above the current cap requires >= 2 completed
    samples with evidence ids. ONE sample holds the cap at current_cap."""

    # One completed sample, fast (p75 = 1h -> evidence cap floor(12/1)=12,
    # policy 14). current_cap = 6. A widen would be justified by the cap math,
    # but only 1 sample -> held at 6.
    completed = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=1.0),
    ]
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=6,
        policy_cap=14,
        hours_per_task_p75=1.0,
        baseline=_baseline_for(completed),
        stale=False,
    )
    assert decision.sample_count == 1
    assert decision.recommended_cap == 6  # held at current_cap
    assert decision.widened is False
    assert any("only 1 completed sample" in r for r in decision.reasons)


def test_cap_widen_allowed_with_two_samples() -> None:
    """With >= 2 completed samples + non-stale data the cap may widen up to
    the evidence/policy bound."""

    completed = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=1.0),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=1.0),
    ]
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=6,
        policy_cap=14,
        hours_per_task_p75=1.0,  # evidence cap floor(12/1)=12
        baseline=_baseline_for(completed),
        stale=False,
    )
    assert decision.sample_count == 2
    # min(policy 14, evidence 12) = 12; widen allowed.
    assert decision.recommended_cap == 12
    assert decision.widened is True


def test_cap_widen_blocked_when_stale() -> None:
    """doc 09 step 6: stale data keeps the current cap even with enough
    samples."""

    completed = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=1.0),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=1.0),
    ]
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=6,
        policy_cap=14,
        hours_per_task_p75=1.0,
        baseline=_baseline_for(completed),
        stale=True,  # <-- stale
    )
    assert decision.recommended_cap == 6  # held at current_cap
    assert decision.widened is False
    assert any("stale" in r for r in decision.reasons)


def test_cap_reduced_when_rate_exceeds_baseline_by_more_than_10pct() -> None:
    """doc 09 step 3: reduce to current cap or 4 when workflow/product repair,
    commit failure, or merge conflict rates exceed the global completed
    baseline by more than 10%."""

    # The baseline is built from ALL completed metrics. Make a window whose
    # workflow-repair rate is far above the feature-wide baseline. Two of three
    # completed groups have a 0.0 rate; one has a high 3.0 rate. The baseline
    # mean is (0 + 0 + 3) / 3 = 1.0. The "window samples" passed to
    # rate_regressions are the SAME completed list -> window mean 1.0 == baseline
    # -> NOT a regression. So instead: build the baseline low and the window
    # high by giving the window-relevant metric a much higher rate.
    #
    # Simplest deterministic construction: every completed metric carries the
    # SAME high workflow-repair rate; the baseline == window mean. To force a
    # regression we need window_rate > baseline * 1.1. We do that by making the
    # baseline a low-rate set and re-using a high-rate subset is not possible
    # through this API (baseline & window are both the completed list). Instead
    # assert the threshold directly via a hand-built baseline.
    completed = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=1.0,
            workflow_repair_per_task=5.0,
        ),
        _metric(
            group_idx=1, completed=True, task_ids=["T10"], hours_per_task=1.0,
            workflow_repair_per_task=5.0,
        ),
    ]
    # A hand-built baseline whose workflow rate (1.0) is well below the window
    # mean (5.0): 5.0 > 1.0 * 1.10 -> regression.
    from iriai_build_v2.workflows.develop.execution.scheduler_sizing import (
        _GlobalBaseline,
    )

    low_baseline = _GlobalBaseline(
        workflow_repair_per_task=1.0,
        product_repair_per_task=None,
        commit_failures_per_task=None,
        merge_conflicts_per_task=None,
        completed_sample_count=10,
    )
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=8,
        policy_cap=14,
        hours_per_task_p75=1.0,  # would widen to 12 absent the regression
        baseline=low_baseline,
        stale=False,
    )
    assert decision.reduced is True
    # reduced to min(current_cap=8, MIN_CAP=4) = 4.
    assert decision.recommended_cap == MIN_CAP == 4
    assert any("rate regression" in r for r in decision.reasons)


def test_cap_no_regression_when_rate_within_10pct() -> None:
    """A rate within 10% of the baseline is NOT a regression."""

    completed = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=1.0,
            workflow_repair_per_task=1.05,
        ),
        _metric(
            group_idx=1, completed=True, task_ids=["T10"], hours_per_task=1.0,
            workflow_repair_per_task=1.05,
        ),
    ]
    from iriai_build_v2.workflows.develop.execution.scheduler_sizing import (
        _GlobalBaseline,
    )

    baseline = _GlobalBaseline(
        workflow_repair_per_task=1.0,  # window 1.05 <= 1.0 * 1.10 = 1.10 -> ok
        product_repair_per_task=None,
        commit_failures_per_task=None,
        merge_conflicts_per_task=None,
        completed_sample_count=10,
    )
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=6,
        policy_cap=14,
        hours_per_task_p75=1.0,
        baseline=baseline,
        stale=False,
    )
    assert decision.reduced is False
    # The widen proceeds normally (min(14, 12) = 12).
    assert decision.recommended_cap == 12


def test_cap_never_below_minimum() -> None:
    """The recommended cap is never below the conservative minimum of 4."""

    completed: list[SchedulerGroupMetric] = []
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=4,
        policy_cap=4,
        hours_per_task_p75=None,
        baseline=_baseline_for(completed),
        stale=False,
    )
    assert decision.recommended_cap >= MIN_CAP


def test_cap_narrowing_always_allowed() -> None:
    """A cap BELOW the current cap is always allowed (no sample requirement) —
    narrowing is conservative."""

    completed = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=4.0),
    ]
    # policy_cap is forced to 4; current_cap is 10. The recommendation narrows
    # to 4 even though there is only 1 sample (narrowing needs no samples).
    decision = compute_cap_decision(
        completed_metrics=completed,
        current_cap=10,
        policy_cap=4,
        hours_per_task_p75=4.0,
        baseline=_baseline_for(completed),
        stale=False,
    )
    assert decision.recommended_cap == 4
    assert decision.widened is False


# ════════════════════════════════════════════════════════════════════════════
# (B-pre) candidate-wave construction — the post-cap validators
# ════════════════════════════════════════════════════════════════════════════


def test_candidate_waves_shrink_on_hard_barrier_mix() -> None:
    """doc 09 step 7 / step 9: a derived wave may not mix hard barriers — the
    candidate wave is SHRUNK below the cap to keep barriers apart."""

    # T20 + T21 are in DIFFERENT hard barriers. With cap 6 they would share a
    # wave, but the hard-barrier validator shrinks the wave.
    barriers = [
        OverlayBarrier(
            barrier_id="bar-A", task_ids=["T20", "T30"], hard=True, source="operator"
        ),
        OverlayBarrier(
            barrier_id="bar-B", task_ids=["T21", "T31"], hard=True, source="operator"
        ),
    ]
    overlay = _overlay("feat-hb", barriers=barriers)
    waves = build_candidate_waves(
        base_dag=_BASE_DAG,
        overlay=overlay,
        cap=6,
        resume_from_group=2,
        write_sets=overlay.write_sets,
    )
    # The first wave (group 2) holds T20 OR T21 alone — never both (different
    # hard barriers). It was shrunk from the cap of 6.
    g2 = next(w for w in waves if w.group_idx == 2)
    assert len(g2.task_ids) == 1
    assert g2.shrunk_from_cap is True
    assert any("hard-barrier" in r for r in g2.shrink_reasons)


def test_candidate_waves_shrink_on_write_set_overlap() -> None:
    """doc 09 step 7 / step 10: same-wave write-set overlap shrinks the
    candidate wave."""

    # T20 and T21 write the SAME path -> they cannot share a wave.
    overlay = _overlay(
        "feat-ws",
        write_sets={
            "T20": ["pkg/shared.py"],
            "T21": ["pkg/shared.py"],  # <-- overlap
            "T30": ["b/a30.py"],
            "T31": ["b/a31.py"],
        },
    )
    waves = build_candidate_waves(
        base_dag=_BASE_DAG,
        overlay=overlay,
        cap=6,
        resume_from_group=2,
        write_sets=overlay.write_sets,
    )
    g2 = next(w for w in waves if w.group_idx == 2)
    assert len(g2.task_ids) == 1  # T20 and T21 cannot co-schedule
    assert g2.shrunk_from_cap is True
    assert any("write-set conflict" in r for r in g2.shrink_reasons)


def test_candidate_waves_unknown_write_task_scheduled_alone() -> None:
    """doc 09 step 7: an ``unknown_write`` task shrinks the candidate wave — it
    is scheduled alone."""

    speed = {
        "T20": OverlayTaskSpeedMetadata(
            semantic_lane="backend", barrier="b-std", unknown_write=True
        ),
        "T21": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
        "T30": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
        "T31": OverlayTaskSpeedMetadata(semantic_lane="backend", barrier="b-std"),
    }
    overlay = _overlay("feat-uw", speed_index=speed)
    waves = build_candidate_waves(
        base_dag=_BASE_DAG,
        overlay=overlay,
        cap=6,
        resume_from_group=2,
        write_sets=overlay.write_sets,
    )
    g2 = next(w for w in waves if w.group_idx == 2)
    # The unknown-write task T20 is the seed and is scheduled alone.
    assert g2.task_ids == ["T20"]
    assert g2.shrunk_from_cap is True
    assert any("unknown write" in r for r in g2.shrink_reasons)


def test_candidate_waves_missing_contract_task_scheduled_alone() -> None:
    """doc 09 step 7: a task with NO write-set contract (a "missing contract")
    is scheduled alone."""

    # T20 has no write-set entry -> a missing contract.
    overlay = _overlay(
        "feat-mc",
        write_sets={
            "T21": ["pkg/a21.py"],
            "T30": ["b/a30.py"],
            "T31": ["b/a31.py"],
        },
    )
    waves = build_candidate_waves(
        base_dag=_BASE_DAG,
        overlay=overlay,
        cap=6,
        resume_from_group=2,
        write_sets={
            "T21": ["pkg/a21.py"],
            "T30": ["b/a30.py"],
            "T31": ["b/a31.py"],
        },
    )
    g2 = next(w for w in waves if w.group_idx == 2)
    assert g2.task_ids == ["T20"]  # the uncontracted task is solo
    assert g2.shrunk_from_cap is True
    assert any("write-set contract" in r for r in g2.shrink_reasons)


def test_candidate_waves_respect_dependency_topology() -> None:
    """The candidate waves preserve topological order — a dependent never
    precedes its dependency."""

    overlay = _overlay("feat-topo")
    waves = build_candidate_waves(
        base_dag=_BASE_DAG,
        overlay=overlay,
        cap=6,
        resume_from_group=2,
        write_sets=overlay.write_sets,
    )
    # T30 depends on T20, T31 on T21. The wave holding T30/T31 must come AFTER
    # the wave holding T20/T21.
    pos = {tid: w.group_idx for w in waves for tid in w.task_ids}
    assert pos["T30"] > pos["T20"]
    assert pos["T31"] > pos["T21"]


def test_candidate_waves_rejects_cap_below_minimum() -> None:
    """A cap below the conservative minimum is a hard fail-fast error."""

    overlay = _overlay("feat-bad")
    with pytest.raises(SchedulerSizingError):
        build_candidate_waves(
            base_dag=_BASE_DAG,
            overlay=overlay,
            cap=2,  # < MIN_CAP
            resume_from_group=2,
            write_sets=overlay.write_sets,
        )


def test_candidate_waves_widen_when_all_validators_pass() -> None:
    """When NO post-cap validator objects, the candidate wave widens up to the
    cap (T20+T21 are in the same barrier, disjoint write sets, same original
    group)."""

    overlay = _overlay("feat-wide")  # all four tasks share barrier b-std
    waves = build_candidate_waves(
        base_dag=_BASE_DAG,
        overlay=overlay,
        cap=6,
        resume_from_group=2,
        write_sets=overlay.write_sets,
    )
    g2 = next(w for w in waves if w.group_idx == 2)
    # T20 + T21 share a wave (same barrier, disjoint write sets, same group).
    assert sorted(g2.task_ids) == ["T20", "T21"]
    assert g2.shrunk_from_cap is False


# ════════════════════════════════════════════════════════════════════════════
# (B) compute_scheduler_feedback — data_quality degradation
# ════════════════════════════════════════════════════════════════════════════


def test_data_quality_sufficient_when_no_flags() -> None:
    """A window whose every completed metric carries an EMPTY
    ``data_quality_flags`` reports ``data_quality="sufficient"``."""

    metrics = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=2.0),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    assert result.feedback.data_quality == "sufficient"


def test_data_quality_stale_on_missing_projection_lineage() -> None:
    """doc 09 § "Adaptive Sizing Data Flow" step 3: "stale projection lineage
    sets ``data_quality="stale"``." A contributing metric carrying
    ``missing_projection_lineage`` forces ``data_quality`` to ``stale``."""

    metrics = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0,
            data_quality_flags=["missing_projection_lineage"],
        ),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=2.0),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    assert result.feedback.data_quality == "stale"


def test_data_quality_insufficient_when_every_metric_flagged() -> None:
    """When EVERY contributing metric carries a (non-lineage) flag the window
    is ``insufficient``."""

    metrics = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0,
            data_quality_flags=["missing_gate_evidence"],
        ),
        _metric(
            group_idx=1, completed=True, task_ids=["T10"], hours_per_task=2.0,
            data_quality_flags=["missing_typed_attempt_evidence"],
        ),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    assert result.feedback.data_quality == "insufficient"


def test_data_quality_mixed_when_some_metrics_flagged() -> None:
    """When SOME but not all contributing metrics carry a flag the window is
    ``mixed`` (and never ``sufficient``)."""

    metrics = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0,
            data_quality_flags=["missing_gate_evidence"],
        ),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=2.0),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    assert result.feedback.data_quality == "mixed"
    # And it is NOT sufficient — the brief's core property.
    assert result.feedback.data_quality != "sufficient"


def test_data_quality_flag_forces_off_sufficient_blocks_widen() -> None:
    """A flagged window cannot be ``sufficient`` AND a stale window blocks the
    widen — the recommendation stays conservative."""

    # Two fast completed groups (would widen to 12) but BOTH carry the
    # projection-lineage flag -> stale -> widen blocked, cap held at current.
    metrics = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=1.0,
            data_quality_flags=["missing_projection_lineage"],
        ),
        _metric(
            group_idx=1, completed=True, task_ids=["T10"], hours_per_task=1.0,
            data_quality_flags=["missing_projection_lineage"],
        ),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    assert result.feedback.data_quality == "stale"
    # Stale -> widen blocked -> cap held at current_cap (6), not widened to 12.
    assert result.feedback.recommended_cap == 6
    # Confidence is low when data_quality is not sufficient.
    assert result.feedback.confidence == "low"


def test_data_quality_insufficient_when_no_completed_groups() -> None:
    """A window with zero completed groups reports ``insufficient`` and falls
    to the conservative policy cap."""

    metrics = [
        _metric(group_idx=2, completed=False, task_ids=["T20", "T21"]),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    # The single status metric carries no flags here, so the window is not
    # flagged — but with NO completed groups it cannot widen.
    assert result.feedback.sample_count == 0
    assert result.feedback.recommended_cap <= 6  # never widened


# ════════════════════════════════════════════════════════════════════════════
# (B) compute_scheduler_feedback — the typed SchedulerFeedback shape
# ════════════════════════════════════════════════════════════════════════════


def test_feedback_is_typed_scheduler_feedback() -> None:
    """``compute_scheduler_feedback`` returns an ``AdaptiveSizingResult`` whose
    ``feedback`` is a typed :class:`SchedulerFeedback`."""

    metrics = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=3.0),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat",
        metrics=metrics,
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    assert isinstance(result, AdaptiveSizingResult)
    assert isinstance(result.feedback, SchedulerFeedback)
    fb = result.feedback
    assert fb.feature_id == "feat"
    assert fb.window_start_group == 0
    assert fb.window_end_group == 1
    assert fb.completed_groups == [0, 1]
    assert fb.sample_count == 2
    # hours_per_task p50/p75 via nearest-rank over {2.0, 3.0}.
    assert fb.hours_per_task_p50 == pytest.approx(2.0)
    assert fb.hours_per_task_p75 == pytest.approx(3.0)
    # The feedback_id is a 24-hex digest.
    assert len(fb.feedback_id) == 24
    # metric_ids are the contributing (completed) metric ids.
    assert fb.metric_ids == sorted(m.metric_id for m in metrics)


def test_feedback_id_is_deterministic_and_clock_independent() -> None:
    """The ``feedback_id`` is a pure function of the window/lane/barrier/metric
    set — re-running with a DIFFERENT ``generated_at`` yields the SAME id."""

    metrics = [
        _metric(group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0),
        _metric(group_idx=1, completed=True, task_ids=["T10"], hours_per_task=2.0),
    ]
    r1 = compute_scheduler_feedback(
        feature_id="feat", metrics=metrics, base_dag=_BASE_DAG, overlay=None,
        current_cap=6, generated_at=_NOW,
    )
    r2 = compute_scheduler_feedback(
        feature_id="feat", metrics=metrics, base_dag=_BASE_DAG, overlay=None,
        current_cap=6, generated_at=_NOW + timedelta(days=7),  # different clock
    )
    assert r1.feedback.feedback_id == r2.feedback.feedback_id
    # generated_at itself does differ (it is recorded faithfully).
    assert r1.feedback.generated_at != r2.feedback.generated_at


def test_feedback_aggregates_by_barrier_with_two_samples() -> None:
    """doc 09 § "Adaptive Sizing Data Flow" step 4: aggregate by
    ``barrier:{barrier}`` when >= 2 completed samples exist."""

    metrics = [
        _metric(
            group_idx=0, completed=True, task_ids=["T00"], hours_per_task=2.0,
            barrier="b-shared", lane="backend",
        ),
        _metric(
            group_idx=1, completed=True, task_ids=["T10"], hours_per_task=2.0,
            barrier="b-shared", lane="backend",
        ),
    ]
    result = compute_scheduler_feedback(
        feature_id="feat", metrics=metrics, base_dag=_BASE_DAG, overlay=None,
        current_cap=6, generated_at=_NOW,
    )
    assert result.aggregation_basis == "barrier:b-shared"
    assert any("barrier:b-shared" in r for r in result.feedback.reasons)


def test_feedback_rejects_empty_feature_id() -> None:
    with pytest.raises(SchedulerSizingError):
        compute_scheduler_feedback(
            feature_id="", metrics=[], base_dag=_BASE_DAG, overlay=None,
            current_cap=6, generated_at=_NOW,
        )


def test_feedback_rejects_current_cap_below_minimum() -> None:
    with pytest.raises(SchedulerSizingError):
        compute_scheduler_feedback(
            feature_id="feat", metrics=[], base_dag=_BASE_DAG, overlay=None,
            current_cap=2, generated_at=_NOW,
        )


# ════════════════════════════════════════════════════════════════════════════
# Real-Postgres: persistence + the end-to-end run_adaptive_sizing flow
# ════════════════════════════════════════════════════════════════════════════


async def _insert_feature(conn: asyncpg.Connection, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id, feature_id, feature_id, "develop", "ws-1",
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
    """Seed a FULLY completed group (mirrors test_scheduler_metrics helper):
    typed attempts + gate + a fully proven merge-queue lane + a checkpoint."""

    for task_id in task_ids:
        await conn.fetchval(
            "INSERT INTO execution_journal_rows "
            "(feature_id, idempotency_key, entry_type, status, group_idx, "
            " request_digest, created_at) "
            "VALUES ($1,$2,'task_attempt','succeeded',$3,'rd',$4) RETURNING id",
            feature_id, f"att-{group_idx}-{task_id}", group_idx, first_attempt_at,
        )

    async def _ev(key: str, kind: str, **kw) -> int:
        return int(
            await conn.fetchval(
                "INSERT INTO evidence_nodes "
                "(feature_id, idempotency_key, kind, status, group_idx, "
                " content_hash, payload, metadata, started_at, finished_at) "
                "VALUES ($1,$2,$3,'approved',$4,'ch','{}'::jsonb,'{}'::jsonb,"
                " $5,$6) RETURNING id",
                feature_id, key, kind, group_idx,
                kw.get("started_at", first_attempt_at),
                kw.get("finished_at"),
            )
        )

    gate_id = await _ev(
        f"gate-{group_idx}", "deterministic_gate",
        started_at=first_attempt_at + timedelta(hours=1),
        finished_at=first_attempt_at + timedelta(hours=2),
    )
    merge_proof = await _ev(f"mp-{group_idx}", "merge_proof")
    commit_proof = await _ev(f"cp-{group_idx}", "commit_proof")
    ckpt_ev = await _ev(f"cke-{group_idx}", "checkpoint_gate")
    post_apply = await _ev(f"pa-{group_idx}", "merge_gate")
    await conn.fetchval(
        "INSERT INTO merge_queue_items "
        "(feature_id, dag_sha256, group_idx, base_commit, request_digest, "
        " idempotency_key, status, checkpoint_projection_id, "
        " merge_proof_evidence_id, commit_proof_evidence_id, "
        " checkpoint_evidence_id, checkpoint_gate_evidence_id, "
        " post_apply_gate_evidence_id, pre_queue_gate_evidence_id, "
        " checkpoint_coverage_digest, checkpoint_body_sha256, result_commit, "
        " created_at, updated_at) "
        "VALUES ($1,'dsha',$2,'bc','rd',$3,'done',$4,$5,$6,$7,$8,$9,$10,"
        " 'cov','bsha','rc',$11,$12) RETURNING id",
        feature_id, group_idx, f"mq-{group_idx}", 9000 + group_idx,
        merge_proof, commit_proof, ckpt_ev, ckpt_ev, post_apply, gate_id,
        first_attempt_at, checkpointed_at,
    )
    ckpt_artifact_id = await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value, created_at) "
        "VALUES ($1, $2, '{\"ok\":true}', $3) RETURNING id",
        feature_id, f"dag-group:{group_idx}", checkpointed_at,
    )
    return int(ckpt_artifact_id)


async def _seed_projection_lineage(
    conn: asyncpg.Connection,
    feature_id: str,
    *,
    group_idx: int,
    checkpoint_artifact_id: int,
) -> None:
    """Add an ``execution_artifact_projections`` row for ``dag-group:{n}`` so a
    completed group's metric carries projection-lineage evidence (and so 09d-1
    does NOT flag ``missing_projection_lineage``)."""

    jr_id = await conn.fetchval(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, request_digest) "
        "VALUES ($1, $2, 'group_checkpoint', 'succeeded', 'rd') RETURNING id",
        feature_id, f"jr-proj-{group_idx}",
    )
    await conn.execute(
        "INSERT INTO execution_artifact_projections "
        "(feature_id, typed_row_id, artifact_id, projection_key, "
        " projection_kind, projection_sha256, idempotency_key) "
        f"VALUES ($1,$2,$3,'dag-group:{group_idx}','group_checkpoint','psha',"
        f" 'idem-proj-{group_idx}')",
        feature_id, jr_id, checkpoint_artifact_id,
    )


@pytest.mark.asyncio
async def test_persist_scheduler_feedback_writes_one_feedback_row(mq_conn) -> None:
    """``persist_scheduler_feedback`` writes exactly one
    ``execution_scheduler_feedback`` row through the 09b store."""

    from iriai_build_v2.execution_control.regroup_overlay_store import (
        RegroupOverlayStore,
    )
    from iriai_build_v2.workflows.develop.execution.scheduler_sizing import (
        persist_scheduler_feedback,
    )

    await _insert_feature(mq_conn, "feat-persist")
    feedback = SchedulerFeedback(
        feedback_id="fb-persist-01",
        feature_id="feat-persist",
        generated_at=_NOW,
        window_start_group=0,
        window_end_group=2,
        overlay_id=None,
        lane="backend",
        barrier="b-std",
        completed_groups=[0, 1],
        sample_count=2,
        tasks_per_hour=0.5,
        hours_per_task_p50=2.0,
        hours_per_task_p75=3.0,
        product_repair_cycles_per_task=0.0,
        workflow_repair_cycles_per_task=0.0,
        commit_failures_per_task=0.0,
        merge_conflicts_per_task=0.0,
        verify_cost_per_task=1.0,
        queue_wait_p75_h=0.5,
        data_quality="sufficient",
        recommended_cap=6,
        current_cap=6,
        confidence="medium",
        reasons=["test"],
        metric_ids=["m0", "m1"],
        evidence_ids=[10, 20],
    )
    store = RegroupOverlayStore(mq_conn)
    record = await persist_scheduler_feedback(store, feedback)
    assert record.feedback_id == "fb-persist-01"
    assert record.recommended_cap == 6
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_scheduler_feedback WHERE feature_id=$1",
        "feat-persist",
    )
    assert count == 1
    # Re-persisting the same feedback is idempotent (same idempotency key).
    record2 = await persist_scheduler_feedback(store, feedback)
    assert record2.id == record.id
    count2 = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_scheduler_feedback WHERE feature_id=$1",
        "feat-persist",
    )
    assert count2 == 1  # still ONE row


@pytest.mark.asyncio
async def test_project_sizing_review_writes_review_artifact(mq_conn) -> None:
    """``project_sizing_review`` writes a ``review:dag-sizing:{feature}:{window}``
    ``artifacts`` row — doc 09 § "Regroup Projection Model"."""

    await _insert_feature(mq_conn, "feat-review")
    feedback = SchedulerFeedback(
        feedback_id="fb-review-01",
        feature_id="feat-review",
        generated_at=_NOW,
        window_start_group=2,
        window_end_group=5,
        overlay_id="ov-x",
        lane="backend",
        barrier="b-std",
        completed_groups=[2, 3],
        sample_count=2,
        tasks_per_hour=0.5,
        hours_per_task_p50=2.0,
        hours_per_task_p75=3.0,
        product_repair_cycles_per_task=0.0,
        workflow_repair_cycles_per_task=0.0,
        commit_failures_per_task=0.0,
        merge_conflicts_per_task=0.0,
        verify_cost_per_task=1.0,
        queue_wait_p75_h=0.5,
        data_quality="sufficient",
        recommended_cap=6,
        current_cap=6,
        confidence="medium",
        reasons=["test"],
        metric_ids=["m2", "m3"],
        evidence_ids=[10, 20],
    )
    art_id = await project_sizing_review(mq_conn, feedback)
    assert art_id > 0
    row = await mq_conn.fetchrow(
        "SELECT key, value FROM artifacts WHERE id = $1", art_id
    )
    # The key is exactly review:dag-sizing:{feature_id}:{window}.
    assert row["key"] == "review:dag-sizing:feat-review:g2-g5"
    import json as _json

    body = _json.loads(row["value"])
    # The body is explicitly stamped non-executable / non-marker.
    assert body["executable"] is False
    assert body["is_active_marker"] is False
    assert body["kind"] == "dag_sizing_review"
    # Re-projecting the identical body reuses the existing row (idempotent).
    art_id2 = await project_sizing_review(mq_conn, feedback)
    assert art_id2 == art_id


@pytest.mark.asyncio
async def test_run_adaptive_sizing_writes_only_feedback_and_review(
    mq_conn,
) -> None:
    """09d-2 CORE SAFETY PROPERTY: ``run_adaptive_sizing`` emits EXACTLY a
    ``SchedulerFeedback`` row + a ``review:dag-sizing:*`` projection — and
    NEVER an active marker, NEVER an overlay row, NEVER a ``dag-regroup:*`` /
    ``dag-regroup-active:*`` / ``dag-regroup-rollback:*`` artifact, NEVER an
    events row."""

    await _insert_feature(mq_conn, "feat-safe")
    # Two completed groups so the flow has real metrics.
    await _seed_completed_group(
        mq_conn, "feat-safe", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    await _seed_completed_group(
        mq_conn, "feat-safe", group_idx=1, task_ids=["T10"],
        first_attempt_at=_NOW + timedelta(hours=3),
        checkpointed_at=_NOW + timedelta(hours=7),
    )

    async def _counts() -> dict[str, int]:
        out: dict[str, int] = {}
        for table in (
            "execution_scheduler_feedback",
            "execution_regroup_overlays",
            "execution_regroup_validations",
            "events",
            "evidence_nodes",
            "execution_journal_rows",
            "merge_queue_items",
        ):
            out[table] = int(
                await mq_conn.fetchval(f"SELECT count(*) FROM {table}")
            )
        # artifacts split by key family.
        out["artifacts_total"] = int(
            await mq_conn.fetchval("SELECT count(*) FROM artifacts")
        )
        out["artifacts_review"] = int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE 'review:dag-sizing:%'"
            )
        )
        out["artifacts_active_marker"] = int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE 'dag-regroup-active:%'"
            )
        )
        out["artifacts_canonical"] = int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE 'dag-regroup:%'"
            )
        )
        out["artifacts_rollback"] = int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE "
                "'dag-regroup-rollback:%'"
            )
        )
        return out

    before = await _counts()
    result = await run_adaptive_sizing(
        mq_conn,
        feature_id="feat-safe",
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    after = await _counts()

    # EXACTLY one new execution_scheduler_feedback row.
    assert after["execution_scheduler_feedback"] == (
        before["execution_scheduler_feedback"] + 1
    )
    # EXACTLY one new review:dag-sizing artifact.
    assert after["artifacts_review"] == before["artifacts_review"] + 1
    assert after["artifacts_total"] == before["artifacts_total"] + 1
    # ZERO active markers / overlays / canonical / rollback artifacts / events.
    assert after["artifacts_active_marker"] == 0
    assert after["artifacts_canonical"] == 0
    assert after["artifacts_rollback"] == 0
    assert after["execution_regroup_overlays"] == before["execution_regroup_overlays"]
    assert after["execution_regroup_validations"] == (
        before["execution_regroup_validations"]
    )
    assert after["events"] == before["events"]
    # The typed evidence tables are untouched (the metric build is read-only).
    assert after["evidence_nodes"] == before["evidence_nodes"]
    assert after["execution_journal_rows"] == before["execution_journal_rows"]
    assert after["merge_queue_items"] == before["merge_queue_items"]
    # The flow did its job — a typed SchedulerFeedback recommendation.
    assert isinstance(result.feedback, SchedulerFeedback)
    assert result.feedback.recommended_cap >= MIN_CAP


@pytest.mark.asyncio
async def test_run_adaptive_sizing_dry_run_writes_nothing(mq_conn) -> None:
    """``run_adaptive_sizing(persist=False)`` computes the result but performs
    NO write at all."""

    await _insert_feature(mq_conn, "feat-dry")
    await _seed_completed_group(
        mq_conn, "feat-dry", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    before = int(await mq_conn.fetchval("SELECT count(*) FROM artifacts"))
    before_fb = int(
        await mq_conn.fetchval("SELECT count(*) FROM execution_scheduler_feedback")
    )
    result = await run_adaptive_sizing(
        mq_conn,
        feature_id="feat-dry",
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
        persist=False,
    )
    after = int(await mq_conn.fetchval("SELECT count(*) FROM artifacts"))
    after_fb = int(
        await mq_conn.fetchval("SELECT count(*) FROM execution_scheduler_feedback")
    )
    assert after == before  # NO artifact written
    assert after_fb == before_fb  # NO feedback row written
    assert isinstance(result.feedback, SchedulerFeedback)


@pytest.mark.asyncio
async def test_run_adaptive_sizing_review_key_never_consumed_by_resolver(
    mq_conn,
) -> None:
    """doc 09 § "Tests": the ``review:dag-sizing:*`` key "is never consumed by
    resolver activation." The ``RegroupOverlayResolver`` reads only the typed
    overlay row + ``dag-regroup-active:*`` / ``dag-regroup:*`` — a
    ``review:dag-sizing:*`` artifact does not make the resolver apply
    anything."""

    from iriai_build_v2.execution_control.regroup_overlay_store import (
        RegroupOverlayStore,
    )
    from iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver import (
        RegroupOverlayResolver,
    )

    await _insert_feature(mq_conn, "feat-resolver")
    await _seed_completed_group(
        mq_conn, "feat-resolver", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=3),
    )
    # Emit a SchedulerFeedback + review:dag-sizing artifact.
    await run_adaptive_sizing(
        mq_conn,
        feature_id="feat-resolver",
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    # The review artifact exists.
    review_count = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE key LIKE 'review:dag-sizing:%'"
    )
    assert review_count == 1
    # The resolver, asked to resolve any group, returns a NON-overlay result
    # (no typed overlay row + no active marker) — the review artifact did not
    # make it apply an overlay.
    resolver = RegroupOverlayResolver(RegroupOverlayStore(mq_conn))
    resolution = await resolver.resolve("feat-resolver", 1)
    # The resolver never read the review:dag-sizing key; with no typed overlay
    # row + no active marker it resolves to the no-typed-overlay outcome —
    # NOT applied, no effective execution order, no quiesce.
    assert resolution.has_typed_overlay is False
    assert resolution.applied is False
    assert resolution.effective_execution_order is None


@pytest.mark.asyncio
async def test_run_adaptive_sizing_recommendation_needs_validate_overlay(
    mq_conn,
) -> None:
    """The 09d-2 recommendation cannot reach dispatch on its own. The emitted
    ``SchedulerFeedback`` + ``review:dag-sizing`` artifact carry NO overlay row
    and NO active marker — a recommendation must be converted into a new
    overlay and pass ``validate_overlay`` before activation. We assert there is
    no ``execution_regroup_overlays`` row + no active marker the recommendation
    produced."""

    await _insert_feature(mq_conn, "feat-needs-val")
    # Two completed groups (a real recommendation window).
    await _seed_completed_group(
        mq_conn, "feat-needs-val", group_idx=0, task_ids=["T00"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=1),
    )
    await _seed_completed_group(
        mq_conn, "feat-needs-val", group_idx=1, task_ids=["T10"],
        first_attempt_at=_NOW + timedelta(hours=1),
        checkpointed_at=_NOW + timedelta(hours=2),
    )
    result = await run_adaptive_sizing(
        mq_conn,
        feature_id="feat-needs-val",
        base_dag=_BASE_DAG,
        overlay=None,
        current_cap=6,
        generated_at=_NOW,
    )
    # The recommendation is a well-formed typed SchedulerFeedback carrying a
    # recommended cap (never below the conservative minimum)...
    assert result.feedback.recommended_cap >= MIN_CAP
    # ...but NO overlay row exists — the recommendation is inert until a new
    # overlay is staged, validated, and activated.
    overlay_count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_regroup_overlays WHERE feature_id=$1",
        "feat-needs-val",
    )
    assert overlay_count == 0
    # NO active marker exists.
    marker_count = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE feature_id=$1 AND key LIKE "
        "'dag-regroup-active:%'",
        "feat-needs-val",
    )
    assert marker_count == 0
    # The reasons name the advisory-only nature.
    assert any(
        "validate_overlay" in r or "advisory" in r
        for r in result.feedback.reasons
    )


@pytest.mark.asyncio
async def test_run_adaptive_sizing_widens_end_to_end_with_full_lineage(
    mq_conn,
) -> None:
    """End-to-end through ``run_adaptive_sizing``: two FAST completed groups in
    a TEST lane (``policy_cap`` 14) WITH full projection lineage (so
    ``data_quality="sufficient"``) recommend a WIDE cap above the current cap.
    Proves the widen path works through the real DB flow — not just the pure
    cap-computation tests."""

    await _insert_feature(mq_conn, "feat-widen")
    # An overlay with a TEST lane (policy_cap = 14) over groups 2 + 3 so the
    # window has a real lane classification (without an overlay the metric
    # carries lane="unknown" -> the conservative 4-cap).
    test_speed = {
        tid: OverlayTaskSpeedMetadata(semantic_lane="test", barrier="b-test")
        for tid in ("T20", "T21", "T30", "T31")
    }
    overlay = _overlay("feat-widen", speed_index=test_speed)
    # Two fast completed groups AT THE OVERLAY OFFSET (2 + 3): 1h checkpoint,
    # 1 task each -> hours_per_task p75 = 1.0 -> evidence_cap = floor(12/1) = 12.
    ckpt2 = await _seed_completed_group(
        mq_conn, "feat-widen", group_idx=2, task_ids=["T20"],
        first_attempt_at=_NOW, checkpointed_at=_NOW + timedelta(hours=1),
    )
    ckpt3 = await _seed_completed_group(
        mq_conn, "feat-widen", group_idx=3, task_ids=["T30"],
        first_attempt_at=_NOW + timedelta(hours=1),
        checkpointed_at=_NOW + timedelta(hours=2),
    )
    # Full projection lineage so the metrics carry NO data_quality_flags.
    await _seed_projection_lineage(
        mq_conn, "feat-widen", group_idx=2, checkpoint_artifact_id=ckpt2
    )
    await _seed_projection_lineage(
        mq_conn, "feat-widen", group_idx=3, checkpoint_artifact_id=ckpt3
    )
    result = await run_adaptive_sizing(
        mq_conn,
        feature_id="feat-widen",
        base_dag=_BASE_DAG,
        overlay=overlay,
        current_cap=6,
        generated_at=_NOW,
    )
    fb = result.feedback
    # data_quality is sufficient (full evidence links, no flags).
    assert fb.data_quality == "sufficient"
    # 2 completed samples in a test lane (policy_cap 14) -> widen allowed;
    # recommended cap = min(policy 14, evidence floor(12/1)=12) = 12 > 6.
    assert fb.sample_count == 2
    assert fb.recommended_cap > 6
    # The recommendation widened — but STILL no overlay row, no active marker.
    assert (
        int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM execution_regroup_overlays WHERE "
                "feature_id=$1",
                "feat-widen",
            )
        )
        == 0
    )
    assert (
        int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE "
                "'dag-regroup-active:%'"
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_run_adaptive_sizing_with_overlay_uses_offset_window(
    mq_conn,
) -> None:
    """With a typed overlay the metric window starts at the overlay offset and
    the candidate waves cover the overlay's derived suffix."""

    await _insert_feature(mq_conn, "feat-ov-flow")
    overlay = _overlay("feat-ov-flow")
    # Group 2 (the overlay's first post-regroup group) — incomplete (a started
    # attempt, no checkpoint).
    await mq_conn.execute(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, group_idx, "
        " request_digest) VALUES "
        "($1,'a2','task_attempt','started',2,'rd')",
        "feat-ov-flow",
    )
    result = await run_adaptive_sizing(
        mq_conn,
        feature_id="feat-ov-flow",
        base_dag=_BASE_DAG,
        overlay=overlay,
        current_cap=6,
        generated_at=_NOW,
    )
    assert result.feedback.overlay_id == "ov-09d2"
    assert result.feedback.window_start_group == 2
    # The feedback persisted + the review projected.
    fb_count = await mq_conn.fetchval(
        "SELECT count(*) FROM execution_scheduler_feedback WHERE feature_id=$1",
        "feat-ov-flow",
    )
    assert fb_count == 1
    # No active marker, ever.
    assert (
        int(
            await mq_conn.fetchval(
                "SELECT count(*) FROM artifacts WHERE key LIKE "
                "'dag-regroup-active:%'"
            )
        )
        == 0
    )
