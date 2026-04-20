from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from iriai_compose import Ask, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    BugFixAttempt,
    Check,
    EvidenceArtifact,
    EvidenceBundle,
    Envelope,
    ImplementationResult,
    Issue,
    Observation,
    RepairStrategyDecision,
    ReproductionResult,
    RootCauseAnalysis,
    Verdict,
    envelope_done,
)
from ....models.state import BugFixV2State
from ....roles import (
    bug_interviewer,
    bug_reproducer,
    convergence_strategist,
    implementer,
    integration_tester,
    user,
    verifier,
)
from ..._common import Gate, Interview
from ....runtimes import create_agent_runtime
from ...develop.phases.implementation import (
    PlannedBugDispatch,
    PlannedBugGroup,
    _commit_repos_in_root,
    _discover_repo_roots_under,
    _format_feedback,
    _format_prior_attempts,
    _get_feature_root,
    _load_prior_attempts,
    _make_parallel_actor,
    _plan_bug_groups,
    _push_clones_to_source_root,
    _repo_heads_for_root,
    _resolve_fix_workspace_from_root,
    _run_git,
    _run_regression,
    _single_rca_fix_verify,
    _store_attempts,
)
from ...develop.phases.post_test_observation import _dispatch_observation
from ..models import (
    BugflowClusterSnapshot,
    BugflowDecisionRecord,
    BugflowIntake,
    BugflowLaneSnapshot,
    BugflowProofRecord,
    BugflowPromotionQueueSnapshot,
    BugflowQueueSnapshot,
    BugflowReportSnapshot,
    BugflowRepoStatus,
    cluster_key,
    compute_counts,
    decision_key,
    default_counts,
    lane_for_status,
    lane_key,
    new_short_id,
    parse_model,
    proof_key,
    report_key,
    utc_now,
)
from ..proof import (
    core_evidence_modes,
    core_surfaces_for_directives,
    directive_core_surface,
    evidence_missing_requirements,
    normalize_evidence_directives,
    persist_proof_record,
    proof_requirement_diagnostics,
    proof_root_for_main_root,
    required_evidence_modes,
    snapshot_proof_record,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_BUG_PLANNING_DEBOUNCE_SECONDS = 3.0
_BUG_REPORT_EVENT = "bugflow_report_created"
_PROMOTION_ARTIFACT = "bugflow-promotion-queue"
# Implementation's VERIFY_RETRIES only governs nested verify/regression retries
# inside one fix cycle. Bugflow lane attempts are the outer report-level budget
# and intentionally much larger.
_MAX_REPORT_ATTEMPTS = 50
_MAX_PROMOTION_PROOF_CAPTURE_RETRIES = 3
_EVIDENCE_CHECK_OK_RESULTS = frozenset({"satisfied", "not_needed", "pass"})
_COUNTED_FAILURE_KINDS = frozenset(
    {
        "lane-verify",
        "lane-regression",
        "promotion-verify",
        "promotion-regression",
    }
)
_STRATEGY_MODES = frozenset(
    {
        "ordinary_retry",
        "minimize_counterexample",
        "broaden_scope",
        "contract_reconciliation",
        "human_attention",
    }
)
_FAILURE_HISTORY_DETAIL_LIMIT = 5
_GLOBAL_CLUSTER_HINT_LIMIT = 3
_EXECUTION_STATES = frozenset({"running", "recovering", "stalled", "strategy_pending"})
_STRATEGY_STATUSES = frozenset({"pending", "decided", "applied"})
_RECOVERABLE_FAILURE_KINDS = frozenset({"infrastructure", "report-task", "planning-task", "promotion-task", "strategy-task"})
_EXECUTION_RECOVERY_GRACE_SECONDS = 60


@dataclass
class _ExecutionTaskState:
    kind: str
    resource_id: str
    nonce: str
    timeout_seconds: int = 0
    invocation_ids: set[str] = field(default_factory=set)
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    started_at_wall: str = field(default_factory=utc_now)
    last_activity_at: str = field(default_factory=utc_now)

    def touch(self) -> None:
        self.last_activity = time.monotonic()
        self.last_activity_at = utc_now()


class _ExecutionObserver:
    def __init__(self, state: _ExecutionTaskState) -> None:
        self._state = state

    def on_invocation_start(self, invocation_id: str, **payload: Any) -> None:
        self._state.invocation_ids.add(invocation_id)
        timeout_seconds = int(payload.get("timeout_seconds") or 0)
        self._state.timeout_seconds = max(self._state.timeout_seconds, timeout_seconds)
        self._state.touch()

    def on_invocation_activity(self, invocation_id: str, **_payload: Any) -> None:
        if invocation_id in self._state.invocation_ids:
            self._state.touch()

    def on_invocation_finish(self, invocation_id: str, **_payload: Any) -> None:
        self._state.invocation_ids.discard(invocation_id)
        self._state.touch()


class BugflowQueuePhase(Phase):
    name = "bugflow-queue"

    def __init__(self) -> None:
        self._report_tasks: dict[str, asyncio.Task[None]] = {}
        self._report_task_state: dict[str, _ExecutionTaskState] = {}
        self._lane_tasks: dict[str, asyncio.Task[None]] = {}
        self._lane_task_state: dict[str, _ExecutionTaskState] = {}
        self._planning_task: asyncio.Task[None] | None = None
        self._planning_task_state: _ExecutionTaskState | None = None
        self._planning_report_ids: list[str] = []
        self._promotion_task: asyncio.Task[None] | None = None
        self._promotion_task_state: _ExecutionTaskState | None = None
        self._promotion_lane_id: str = ""

    def _new_execution_state(self, kind: str, resource_id: str) -> _ExecutionTaskState:
        return _ExecutionTaskState(kind=kind, resource_id=resource_id, nonce=new_short_id("exec"))

    async def _run_with_execution_observer(
        self,
        runner: WorkflowRunner,
        state: _ExecutionTaskState,
        coro: Any,
    ) -> None:
        observer = _ExecutionObserver(state)
        binder = getattr(runner, "bind_invocation_observer", None)
        state.touch()
        with binder(observer) if callable(binder) else nullcontext():
            await coro

    async def _track_report_task(
        self,
        runner: WorkflowRunner,
        report_id: str,
        coro: Any,
    ) -> asyncio.Task[None]:
        state = self._new_execution_state("report", report_id)
        self._report_task_state[report_id] = state
        task = asyncio.create_task(self._run_with_execution_observer(runner, state, coro))
        self._report_tasks[report_id] = task
        return task

    async def _track_lane_task(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane: BugflowLaneSnapshot,
        coro: Any,
    ) -> asyncio.Task[None]:
        state = self._new_execution_state("lane", lane.lane_id)
        self._lane_task_state[lane.lane_id] = state
        lane.execution_state = "running"
        lane.execution_nonce = state.nonce
        lane.execution_kind = "lane"
        lane.execution_owner = f"feature:{feature.id}"
        lane.execution_started_at = state.started_at_wall
        lane.last_progress_at = state.last_activity_at
        lane.execution_failure_kind = ""
        lane.execution_failure_reason = ""
        await _save_lane(runner, feature, lane)
        task = asyncio.create_task(self._run_with_execution_observer(runner, state, coro))
        self._lane_tasks[lane.lane_id] = task
        return task

    async def _track_planning_task(
        self,
        runner: WorkflowRunner,
        coro: Any,
    ) -> asyncio.Task[None]:
        state = self._new_execution_state("planning", "planning")
        self._planning_task_state = state
        task = asyncio.create_task(self._run_with_execution_observer(runner, state, coro))
        self._planning_task = task
        return task

    async def _track_promotion_task(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane_id: str,
        coro: Any,
    ) -> asyncio.Task[None]:
        state = self._new_execution_state("promotion", lane_id)
        self._promotion_task_state = state
        queue = await _load_promotion_queue(runner, feature)
        queue.execution_state = "running"
        queue.execution_nonce = state.nonce
        queue.execution_kind = "promotion"
        queue.execution_owner = f"feature:{feature.id}"
        queue.execution_started_at = state.started_at_wall
        queue.last_progress_at = state.last_activity_at
        queue.execution_failure_kind = ""
        queue.execution_failure_reason = ""
        await _save_promotion_queue(runner, feature, queue)
        task = asyncio.create_task(self._run_with_execution_observer(runner, state, coro))
        self._promotion_task = task
        return task

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        state: BugFixV2State,
    ) -> BugFixV2State:
        main_root = _get_feature_root(runner, feature)
        if main_root:
            runner.services["worktree_root"] = main_root

        try:
            async with runner.feature_store.advisory_lock(feature.id, "bugflow-scheduler"):
                while True:
                    await self._detect_stalled_tasks(runner, feature)
                    await self._reap_report_tasks(runner, feature)
                    await self._reap_planning_task(runner, feature)
                    await self._reap_lane_tasks(runner, feature)
                    await self._reap_promotion_task(runner, feature)

                    reports = await _load_reports(runner, feature)
                    lanes = await _load_lanes(runner, feature)
                    clusters = await _load_clusters(
                        runner,
                        feature,
                        await _load_queue(runner, feature),
                        reports,
                        lanes,
                    )
                    await self._recover_stale_execution_state(runner, feature, lanes)

                    reports = await _load_reports(runner, feature)
                    lanes = await _load_lanes(runner, feature)
                    clusters = await _load_clusters(
                        runner,
                        feature,
                        await _load_queue(runner, feature),
                        reports,
                        lanes,
                    )
                    await self._recover_retryable_blocked_reports(runner, feature, reports, lanes)

                    reports = await _load_reports(runner, feature)
                    lanes = await _load_lanes(runner, feature)
                    clusters = await _load_clusters(
                        runner,
                        feature,
                        await _load_queue(runner, feature),
                        reports,
                        lanes,
                    )

                    active_bug_work = bool(
                        self._planning_task
                        or any(
                            lane.category == "bug"
                            and lane.status in {
                                "planned",
                                "active_fix",
                                "active_verify",
                                "verified_pending_promotion",
                                "promoting",
                            }
                            for lane in lanes
                        )
                    )
                    await self._maybe_revalidate_pending_retriage(runner, feature)
                    for report in reports:
                        if report.report_id in self._report_tasks:
                            continue
                        if report.status in {
                            "intake_pending",
                            "classification_pending",
                            "validation_pending",
                            "awaiting_confirmation",
                        }:
                            await self._track_report_task(
                                runner,
                                report.report_id,
                                self._process_report(
                                    runner,
                                    feature,
                                    report.report_id,
                                ),
                            )

                    queued_bug_reports = [
                        report
                        for report in reports
                        if report.category == "bug"
                        and report.status == "queued"
                        and not report.lane_id
                    ]
                    if queued_bug_reports and self._planning_task is None:
                        newest_update = max(
                            report.updated_at or report.created_at for report in queued_bug_reports
                        )
                        if _age_seconds(newest_update) >= _BUG_PLANNING_DEBOUNCE_SECONDS:
                            self._planning_report_ids = [report.report_id for report in queued_bug_reports]
                            await self._track_planning_task(
                                runner,
                                self._plan_bug_reports(
                                    runner,
                                    feature,
                                    self._planning_report_ids,
                                ),
                            )

                    reports = await _load_reports(runner, feature)
                    lanes = await _load_lanes(runner, feature)
                    clusters = await _load_clusters(
                        runner,
                        feature,
                        await _load_queue(runner, feature),
                        reports,
                        lanes,
                    )

                    await self._ensure_non_bug_lanes(runner, feature, reports)
                    reports = await _load_reports(runner, feature)
                    lanes = await _load_lanes(runner, feature)
                    clusters = await _load_clusters(
                        runner,
                        feature,
                        await _load_queue(runner, feature),
                        reports,
                        lanes,
                    )

                    await self._admit_planned_lanes(runner, feature, lanes)
                    await self._maybe_start_promotion(runner, feature, lanes)

                    reports = await _load_reports(runner, feature)
                    lanes = await _load_lanes(runner, feature)
                    clusters = await _load_clusters(
                        runner,
                        feature,
                        await _load_queue(runner, feature),
                        reports,
                        lanes,
                    )
                    queue = await self._write_queue_snapshot(runner, feature, reports, lanes, clusters)
                    state.queue_summary = queue.model_dump_json()
                    state.decision_summary = str(
                        await runner.artifacts.get("bugflow-decisions", feature=feature) or "[]"
                    )
                    state.phase = self.name
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            for task in self._report_tasks.values():
                task.cancel()
            for task in self._lane_tasks.values():
                task.cancel()
            if self._planning_task:
                self._planning_task.cancel()
            if self._promotion_task:
                self._promotion_task.cancel()
            raise

    async def _recover_stale_execution_state(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lanes: list[BugflowLaneSnapshot],
    ) -> None:
        seed_lane_ids = {lane.lane_id for lane in lanes if lane.lane_id}
        seed_report_ids = {
            report_id
            for lane in lanes
            for report_id in lane.report_ids
            if report_id
        }

        async def _reload_recovery_context(
            current_lanes: list[BugflowLaneSnapshot],
        ) -> tuple[list[BugflowReportSnapshot], list[BugflowLaneSnapshot], list[BugflowClusterSnapshot]]:
            queue = await _load_queue(runner, feature)
            reports = await _load_reports(runner, feature)
            report_by_id = {report.report_id: report for report in reports}
            explicit_report_ids = set(seed_report_ids)
            explicit_report_ids.update(
                report_id
                for lane in current_lanes
                for report_id in lane.report_ids
                if report_id
            )
            if explicit_report_ids:
                report_by_id.update(
                    await _load_reports_by_id(
                        runner,
                        feature,
                        sorted(explicit_report_ids),
                    )
                )
            reports = sorted(
                report_by_id.values(),
                key=lambda report: (report.created_at, report.report_id),
            )

            lane_by_id = {lane.lane_id: lane for lane in await _load_lanes(runner, feature)}
            explicit_lane_ids = set(seed_lane_ids)
            explicit_lane_ids.update(
                lane.lane_id for lane in current_lanes if lane.lane_id
            )
            explicit_lane_ids.update(
                report.lane_id for report in reports if report.lane_id
            )
            for lane_id in sorted(explicit_lane_ids):
                lane = await _load_lane(runner, feature, lane_id)
                if lane is not None:
                    lane_by_id[lane_id] = lane
            lanes = sorted(
                lane_by_id.values(),
                key=lambda lane: (lane.updated_at or "", lane.lane_id),
                reverse=True,
            )
            clusters = await _load_clusters(
                runner,
                feature,
                queue,
                reports,
                lanes,
            )
            return reports, lanes, clusters

        reports, lanes, clusters = await _reload_recovery_context(lanes)
        stale_promoting_ids = [
            lane.lane_id
            for lane in lanes
            if lane.status == "promoting"
            and self._promotion_task is None
        ]
        main_root = _get_feature_root(runner, feature)

        pending_respawn_lane_ids: list[str] = []
        for lane in lanes:
            intent = await _load_respawn_intent(runner, feature, lane.lane_id)
            if not intent:
                continue
            if str(intent.get("status", "")).strip().lower() == "applied":
                continue
            pending_respawn_lane_ids.append(lane.lane_id)

        for lane_id in pending_respawn_lane_ids:
            lane = await _load_lane(runner, feature, lane_id)
            if not lane:
                continue
            intent = await _load_respawn_intent(runner, feature, lane_id)
            if not intent:
                continue
            decision = await _load_strategy_decision_by_key(
                runner,
                feature,
                str(intent.get("strategy_decision_key", "")),
            )
            await _respawn_lane_from_latest_main(
                runner,
                feature,
                lane,
                str(intent.get("reason", "") or lane.wait_reason or "Resuming pending respawn intent."),
                failed_attempt=(
                    int(intent["failed_attempt"])
                    if intent.get("failed_attempt") is not None
                    else None
                ),
                failure_kind=str(intent.get("failure_kind", "") or lane.execution_failure_kind),
                strategy_decision=decision,
                strategy_decision_key=str(intent.get("strategy_decision_key", "")),
                failure_bundle_key=str(intent.get("failure_bundle_key", "")),
            )

        if pending_respawn_lane_ids:
            reports, lanes, clusters = await _reload_recovery_context(lanes)
        clusters, normalized = await _normalize_cluster_strategy_states(
            runner,
            feature,
            clusters,
        )
        if normalized:
            reports, lanes, clusters = await _reload_recovery_context(lanes)
        cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
        strategy_checkpoint_cluster_ids = [
            cluster.cluster_id
            for cluster in clusters
            if _strategy_checkpoint_kind(cluster) in {"pending", "decided"}
            and cluster.lane_id
            and cluster.lane_id not in self._lane_tasks
        ]
        applied_notice_cluster_ids = [
            cluster.cluster_id
            for cluster in clusters
            if _strategy_checkpoint_kind(cluster) == "applied"
            and any(
                report.cluster_id == cluster.cluster_id
                and report.latest_strategy_notice_key != cluster.strategy_decision_key
                for report in reports
            )
        ]
        stale_lane_ids = [
            lane.lane_id
            for lane in lanes
            if lane.status in {"active_fix", "active_verify"}
            and lane.lane_id not in self._lane_tasks
            and not (
                lane.source_cluster_id
                and cluster_by_id.get(lane.source_cluster_id)
                and _strategy_checkpoint_kind(cluster_by_id[lane.source_cluster_id]) in {"pending", "decided", "applied"}
            )
        ]

        if (
            not stale_lane_ids
            and not stale_promoting_ids
            and not pending_respawn_lane_ids
            and not strategy_checkpoint_cluster_ids
            and not applied_notice_cluster_ids
        ):
            promotion = await _load_promotion_queue(runner, feature)
            if promotion.promoting_lane_id and not any(
                lane.lane_id == promotion.promoting_lane_id and lane.status == "promoting"
                for lane in lanes
            ):
                await _refresh_promotion_queue(
                    runner,
                    feature,
                    status_text="Promotion idle",
                )
            return

        for cluster_id in strategy_checkpoint_cluster_ids:
            cluster = cluster_by_id.get(cluster_id) or await _load_cluster(runner, feature, cluster_id)
            if not cluster or not cluster.lane_id:
                continue
            lane = await _load_lane(runner, feature, cluster.lane_id)
            if not lane:
                continue
            reports = list((await _load_reports_by_id(runner, feature, cluster.report_ids)).values())
            if not reports:
                continue
            failure_bundle = await _load_failure_bundle_payload(
                runner,
                feature,
                cluster.stable_bundle_key,
            )
            if not failure_bundle:
                continue
            decision = await _load_strategy_decision_by_key(
                runner,
                feature,
                cluster.strategy_decision_key,
            )
            if decision is None:
                decision_key, decision = await _decide_cluster_strategy(
                    runner,
                    feature,
                    cluster,
                    lane,
                    reports,
                    failure_bundle_key=cluster.stable_bundle_key,
                    failure_bundle=failure_bundle,
                    reason=str(
                        failure_bundle.get("failure_reason")
                        or lane.wait_reason
                        or cluster.wait_reason
                        or "Resuming pending strategy decision."
                    ),
                )
                cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
                cluster.strategy_decision_key = decision_key
                await _set_cluster_strategy_status(
                    runner,
                    feature,
                    cluster,
                    status="decided",
                )
            else:
                decision_key = cluster.strategy_decision_key

            await _apply_cluster_strategy(
                runner,
                feature,
                cluster,
                lane,
                reports,
                decision=decision,
                decision_key=decision_key,
                failure_bundle_key=cluster.stable_bundle_key,
                failure_bundle=failure_bundle,
                reason=str(
                    failure_bundle.get("failure_reason")
                    or lane.wait_reason
                    or cluster.wait_reason
                    or "Resuming pending strategy application."
                ),
                failed_attempt=(_lane_attempt_budget(reports)[0] if lane.status != "planned" else None),
                failure_kind=str(failure_bundle.get("failure_kind", "") or lane.execution_failure_kind),
                initial=lane.status == "planned" and not lane.supersedes_lane_id,
            )

        for cluster in clusters:
            if cluster.cluster_id not in applied_notice_cluster_ids:
                continue
            lane = await _load_lane(runner, feature, cluster.lane_id) if cluster.lane_id else None
            decision = await _load_strategy_decision_by_key(
                runner,
                feature,
                cluster.strategy_decision_key,
            )
            if decision is None:
                continue
            reports = list((await _load_reports_by_id(runner, feature, cluster.report_ids)).values())
            if not reports:
                continue
            initial_notice = bool(lane and lane.status == "planned" and not lane.supersedes_lane_id)
            for report in reports:
                if report.latest_strategy_notice_key == cluster.strategy_decision_key:
                    continue
                await _post_strategy_notice(
                    runner,
                    feature,
                    report,
                    cluster,
                    decision,
                    decision_key=cluster.strategy_decision_key,
                    initial=initial_notice,
                )

        for lane_id in stale_lane_ids:
            lane = await _load_lane(runner, feature, lane_id)
            if not lane or lane.status not in {"active_fix", "active_verify"}:
                continue
            if lane.execution_state in {"recovering", "stalled"}:
                await _respawn_lane_from_latest_main(
                    runner,
                    feature,
                    lane,
                    lane.execution_failure_reason
                    or lane.wait_reason
                    or "Recovering a stalled isolated lane from the latest main bugflow head.",
                    failure_kind=lane.execution_failure_kind or "infrastructure",
                )
                continue
            await _respawn_lane_from_latest_main(
                runner,
                feature,
                lane,
                "Bridge restarted while this isolated lane was still executing. I respawned it from the latest main bugflow head.",
            )

        for lane_id in stale_promoting_ids:
            lane = await _load_lane(runner, feature, lane_id)
            if not lane or lane.status != "promoting":
                continue
            if lane.promotion_status == "applied-main":
                await _finalize_promoted_lane(
                    runner,
                    feature,
                    lane,
                    ensure_push=True,
                )
                await self._revalidate_pending_retriage(runner, feature)
                continue
            if lane.promotion_status == "applying-main":
                await _block_interrupted_main_promotion(
                    runner,
                    feature,
                    lane,
                    "Bridge restarted while promotion was applying changes onto the main bugflow worktree.",
                )
                continue
            if main_root and await _lane_commits_already_on_main(main_root, lane):
                await _finalize_promoted_lane(
                    runner,
                    feature,
                    lane,
                    ensure_push=True,
                )
                await self._revalidate_pending_retriage(runner, feature)
                continue
            await _respawn_lane_from_latest_main(
                runner,
                feature,
                lane,
                "Bridge restarted while this isolated lane was being promoted. I respawned it from the latest main bugflow head.",
            )

        await _refresh_promotion_queue(
            runner,
            feature,
            status_text="Promotion idle",
        )

    async def _recover_retryable_blocked_reports(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        reports: list[BugflowReportSnapshot],
        lanes: list[BugflowLaneSnapshot],
    ) -> None:
        lane_by_id = {lane.lane_id: lane for lane in lanes}
        recovered_lane_ids: set[str] = set()
        active_statuses = {
            "planned",
            "active_fix",
            "active_verify",
            "verified_pending_promotion",
            "promoting",
        }
        for report in reports:
            if report.status != "blocked":
                continue
            if report.terminal_reason_kind in {
                "human_attention",
                "contradiction",
                "exhausted-budget",
            }:
                continue
            if _normalize_strategy_mode(report.strategy_mode) == "human_attention":
                continue
            lane_id = report.lane_id or report.last_failed_lane_id
            if not lane_id:
                continue
            if lane_id in recovered_lane_ids:
                continue
            lane = lane_by_id.get(lane_id) or await _load_lane(runner, feature, lane_id)
            if not lane or lane.status != "blocked":
                continue
            lane_reports = list((await _load_reports_by_id(runner, feature, lane.report_ids)).values()) or [report]
            if report.terminal_reason_kind == "proof-policy":
                if not _is_recoverable_proof_policy_block(report, lane):
                    continue
                await _recover_blocked_promotion_proof_capture(
                    runner,
                    feature,
                    lane,
                    lane_reports,
                )
                recovered_lane_ids.add(lane_id)
                continue
            if "missing rca context" in (lane.wait_reason or "").lower():
                await _respawn_lane_from_latest_main(
                    runner,
                    feature,
                    lane,
                    (
                        "Recovered this lane because a prior bridge version dropped its RCA context during respawn. "
                        "I recreated it from the latest main bugflow head."
                    ),
                )
                recovered_lane_ids.add(lane_id)
                continue
            if any(_normalize_strategy_mode(entry.strategy_mode) == "human_attention" for entry in lane_reports):
                continue
            if any(
                entry.lane_id != lane.lane_id
                and report.report_id in entry.report_ids
                and entry.status in active_statuses
                for entry in lanes
            ):
                continue
            failure_kind = (
                report.terminal_reason_kind
                or report.last_failure_kind
                or _infer_retryable_failure_kind(report, lane)
            )
            is_recoverable = _classify_execution_failure(failure_kind=failure_kind) == "recoverable"
            if not is_recoverable and not _is_retryable_counted_failure(failure_kind):
                continue
            if any(
                _ensure_report_retry_state(entry).attempts_used >= _ensure_report_retry_state(entry).max_attempts
                for entry in lane_reports
            ):
                continue
            _ensure_report_retry_state(report)
            if not is_recoverable:
                report.attempts_used = _legacy_attempts_used_from_lane(report, lane)
                _ensure_report_retry_state(report)
                if report.attempts_used >= report.max_attempts:
                    await _save_report(runner, feature, report)
                    continue
            report.last_failed_lane_id = lane.lane_id
            report.last_failure_kind = failure_kind
            report.last_failure_reason = (
                report.last_failure_reason
                or report.terminal_reason_summary
                or lane.wait_reason
                or lane.latest_regression_summary
                or lane.latest_verify_summary
                or lane.latest_fix_summary
            )
            report.terminal_reason_kind = ""
            report.terminal_reason_summary = ""
            await _save_report(runner, feature, report)
            await _respawn_lane_from_latest_main(
                runner,
                feature,
                lane,
                (
                    "Recovered this report because it was blocked by a recoverable execution failure."
                    if is_recoverable
                    else "Recovered this report because it was blocked before the retry budget was exhausted."
                ),
                failed_attempt=None if is_recoverable else report.attempts_used,
                failure_kind=failure_kind,
            )
            recovered_lane_ids.add(lane_id)

    async def _detect_stalled_tasks(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        async def _has_live_invocation(state: _ExecutionTaskState | None) -> bool:
            if state is None:
                return False
            checker = getattr(runner, "invocation_has_live_work", None)
            if not callable(checker):
                return False
            return any(checker(invocation_id) for invocation_id in list(state.invocation_ids))

        async def _cancel(task: asyncio.Task[None]) -> None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        now = time.monotonic()
        for report_id, task in list(self._report_tasks.items()):
            state = self._report_task_state.get(report_id)
            if not state or task.done() or state.timeout_seconds <= 0:
                continue
            if state.invocation_ids and await _has_live_invocation(state):
                continue
            if now - state.last_activity < state.timeout_seconds + _EXECUTION_RECOVERY_GRACE_SECONDS:
                continue
            await _cancel(task)
            self._report_tasks.pop(report_id, None)
            self._report_task_state.pop(report_id, None)
            await self._handle_report_task_failure(
                runner,
                feature,
                report_id,
                RuntimeError(
                    f"Report task stalled after {int(now - state.last_activity)}s without progress."
                ),
            )

        if self._planning_task and self._planning_task_state and not self._planning_task.done():
            state = self._planning_task_state
            if state.timeout_seconds > 0 and not (state.invocation_ids and await _has_live_invocation(state)):
                if now - state.last_activity >= state.timeout_seconds + _EXECUTION_RECOVERY_GRACE_SECONDS:
                    await _cancel(self._planning_task)
                    self._planning_task = None
                    self._planning_task_state = None
                    await self._handle_planning_task_failure(
                        runner,
                        feature,
                        list(self._planning_report_ids),
                        RuntimeError(
                            f"Planning task stalled after {int(now - state.last_activity)}s without progress."
                        ),
                    )
                    self._planning_report_ids = []

        for lane_id, task in list(self._lane_tasks.items()):
            state = self._lane_task_state.get(lane_id)
            if not state or task.done() or state.timeout_seconds <= 0:
                continue
            await _touch_lane_execution(
                runner,
                feature,
                lane_id,
                nonce=state.nonce,
                progress_at=state.last_activity_at,
            )
            if state.invocation_ids and await _has_live_invocation(state):
                continue
            if now - state.last_activity < state.timeout_seconds + _EXECUTION_RECOVERY_GRACE_SECONDS:
                continue
            lane = await _load_lane(runner, feature, lane_id)
            if lane is None or lane.execution_nonce != state.nonce:
                continue
            await _set_lane_execution_state(
                runner,
                feature,
                lane,
                state="stalled",
                expected_nonce=state.nonce,
                failure_kind="infrastructure",
                failure_reason=f"Lane task stalled after {int(now - state.last_activity)}s without progress.",
                wait_reason=f"Lane task stalled after {int(now - state.last_activity)}s without progress.",
                current_phase="stalled",
            )
            await _cancel(task)
            self._lane_tasks.pop(lane_id, None)
            self._lane_task_state.pop(lane_id, None)
            lane = await _load_lane(runner, feature, lane_id)
            if lane is not None:
                await self._handle_lane_task_failure(
                    runner,
                    feature,
                    lane,
                    RuntimeError(
                        f"Lane task stalled after {int(now - state.last_activity)}s without progress."
                    ),
                    failure_kind="infrastructure",
                    expected_nonce=state.nonce,
                    notice_key=f"lane-task:{lane_id}:{state.nonce}",
                )

        if self._promotion_task and self._promotion_task_state and not self._promotion_task.done():
            state = self._promotion_task_state
            await _touch_promotion_execution(
                runner,
                feature,
                nonce=state.nonce,
                progress_at=state.last_activity_at,
            )
            if state.timeout_seconds > 0 and not (state.invocation_ids and await _has_live_invocation(state)):
                if now - state.last_activity >= state.timeout_seconds + _EXECUTION_RECOVERY_GRACE_SECONDS:
                    await _set_promotion_execution_state(
                        runner,
                        feature,
                        state="stalled",
                        expected_nonce=state.nonce,
                        failure_kind="promotion-task",
                        failure_reason=f"Promotion task stalled after {int(now - state.last_activity)}s without progress.",
                        status_text="Promotion stalled; recovering",
                    )
                    await _cancel(self._promotion_task)
                    self._promotion_task = None
                    promotion_lane_id = self._promotion_lane_id
                    lane = await _load_lane(runner, feature, promotion_lane_id) if promotion_lane_id else None
                    self._promotion_lane_id = ""
                    self._promotion_task_state = None
                    await self._handle_promotion_task_failure(
                        runner,
                        feature,
                        lane,
                        RuntimeError(
                            f"Promotion task stalled after {int(now - state.last_activity)}s without progress."
                        ),
                        expected_nonce=state.nonce,
                        notice_key=f"promotion-task:{promotion_lane_id}:{state.nonce}" if promotion_lane_id else "",
                    )

    async def _handle_report_task_failure(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        report_id: str,
        exc: BaseException,
        *,
        notice_key: str = "",
    ) -> None:
        report = await _load_report(runner, feature, report_id)
        if not report:
            return
        report.current_step = "Recovering after report task failure"
        report.terminal_reason_kind = ""
        report.terminal_reason_summary = ""
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
        await _post_execution_recovery_notice(
            runner,
            feature,
            report,
            notice_key=notice_key,
            text=(
                f"{report.report_id}: a recoverable report-task failure occurred, so I'm retrying automatically.\n\n"
                f"Reason: {_summarize_exception(exc)}"
            ),
        )

    async def _handle_planning_task_failure(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        report_ids: list[str],
        exc: BaseException,
        *,
        notice_key: str = "",
    ) -> None:
        summary = _summarize_exception(exc)
        for report_id in report_ids:
            report = await _load_report(runner, feature, report_id)
            if not report or report.status != "queued" or report.lane_id:
                continue
            report.current_step = "Recovering after shared RCA planning failure"
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)
            await _post_execution_recovery_notice(
                runner,
                feature,
                report,
                notice_key=f"{notice_key}:{report.report_id}" if notice_key else "",
                text=(
                    f"{report.report_id}: the shared RCA planning pass failed in a recoverable way, so I'm retrying it.\n\n"
                    f"Reason: {summary}"
                ),
            )

    async def _handle_lane_task_failure(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane: BugflowLaneSnapshot,
        exc: BaseException,
        *,
        failure_kind: str = "infrastructure",
        expected_nonce: str | None = None,
        notice_key: str = "",
    ) -> None:
        current_lane = await _load_lane(runner, feature, lane.lane_id)
        if current_lane is None:
            return
        if expected_nonce is not None and current_lane.execution_nonce != expected_nonce:
            return
        reason = _summarize_exception(exc)
        classification = _classify_execution_failure(failure_kind=failure_kind)
        current_lane.wait_reason = reason
        current_lane.execution_failure_kind = failure_kind
        current_lane.execution_failure_reason = reason
        current_lane.updated_at = utc_now()
        await _save_lane(runner, feature, current_lane)
        if classification == "retryable_counted" and current_lane.category == "bug":
            await _retry_or_block_bug_lane(
                runner,
                feature,
                current_lane,
                reason=reason,
                failure_kind=failure_kind,
            )
            return
        if classification == "recoverable":
            current_lane = await _set_lane_execution_state(
                runner,
                feature,
                current_lane,
                state="recovering",
                expected_nonce=expected_nonce,
                failure_kind=failure_kind,
                failure_reason=reason,
                wait_reason=reason,
                current_phase="recovering",
            )
            if current_lane is None:
                return
            await _mark_cluster_from_lane(
                runner,
                feature,
                current_lane,
                status="active_fix",
                current_phase="recovering",
                wait_reason=reason,
            )
            for report in (await _load_reports_by_id(runner, feature, current_lane.report_ids)).values():
                await _post_execution_recovery_notice(
                    runner,
                    feature,
                    report,
                    notice_key=f"{notice_key}:{report.report_id}" if notice_key else "",
                    text=(
                        f"{report.report_id}: isolated lane {current_lane.lane_id} hit a recoverable execution failure, "
                        "so I'm recreating it from the latest main bugflow head.\n\n"
                        f"Reason: {reason}"
                    ),
                )
            try:
                await _respawn_lane_from_latest_main(
                    runner,
                    feature,
                    current_lane,
                    f"Recovering from a lane execution failure: {reason}",
                    failure_kind=failure_kind,
                )
            except Exception as respawn_exc:
                logger.exception("Recoverable lane failure could not be respawned for %s", current_lane.lane_id, exc_info=respawn_exc)
            return
        current_lane.status = "blocked"
        current_lane.promotion_status = "blocked"
        await _save_lane(runner, feature, current_lane)
        await _mark_cluster_from_lane(
            runner,
            feature,
            current_lane,
            status="blocked",
            current_phase="blocked",
            wait_reason=reason,
        )
        await _mark_lane_reports_blocked(
            runner,
            feature,
            current_lane,
            _lane_failure_message(current_lane),
            failure_kind=failure_kind,
            failure_reason=reason,
        )

    async def _handle_promotion_task_failure(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane: BugflowLaneSnapshot | None,
        exc: BaseException,
        *,
        expected_nonce: str | None = None,
        notice_key: str = "",
    ) -> None:
        if lane is None:
            await _clear_promotion_execution(
                runner,
                feature,
                expected_nonce=expected_nonce,
                status_text="Promotion idle",
            )
            return
        current_lane = await _load_lane(runner, feature, lane.lane_id)
        if current_lane is None:
            return
        reason = _summarize_exception(exc)
        main_root = _get_feature_root(runner, feature)
        if current_lane.promotion_status == "applied-main" or (
            main_root and await _lane_commits_already_on_main(main_root, current_lane)
        ):
            await _finalize_promoted_lane(
                runner,
                feature,
                current_lane,
                ensure_push=True,
            )
            await self._maybe_revalidate_pending_retriage(runner, feature)
            await _clear_promotion_execution(
                runner,
                feature,
                expected_nonce=expected_nonce,
                status_text="Promotion idle",
            )
            return
        if current_lane.promotion_status == "applying-main":
            await _block_interrupted_main_promotion(
                runner,
                feature,
                current_lane,
                reason,
            )
            await _clear_promotion_execution(
                runner,
                feature,
                expected_nonce=expected_nonce,
                status_text="Promotion idle",
            )
            return
        current_lane.wait_reason = reason
        current_lane.updated_at = utc_now()
        await _save_lane(runner, feature, current_lane)
        await _set_promotion_execution_state(
            runner,
            feature,
            state="recovering",
            expected_nonce=expected_nonce,
            failure_kind="promotion-task",
            failure_reason=reason,
            promoting_lane_id="",
            status_text=f"Recovering promotion for {current_lane.lane_id}",
        )
        for report in (await _load_reports_by_id(runner, feature, current_lane.report_ids)).values():
            await _post_execution_recovery_notice(
                runner,
                feature,
                report,
                notice_key=f"{notice_key}:{report.report_id}" if notice_key else "",
                text=(
                    f"{report.report_id}: promotion for isolated lane {current_lane.lane_id} hit a recoverable execution failure, "
                    "so I'm replaying it automatically.\n\n"
                    f"Reason: {reason}"
                ),
            )
        try:
            await _respawn_lane_from_latest_main(
                runner,
                feature,
                current_lane,
                f"Recovering from a promotion failure: {reason}",
                failure_kind="promotion-task",
            )
            await _clear_promotion_execution(
                runner,
                feature,
                expected_nonce=expected_nonce,
                status_text="Promotion idle",
            )
        except Exception as respawn_exc:
            logger.exception("Recoverable promotion failure could not be respawned for %s", current_lane.lane_id, exc_info=respawn_exc)
            await _set_promotion_execution_state(
                runner,
                feature,
                state="recovering",
                expected_nonce=expected_nonce,
                failure_kind="promotion-task",
                failure_reason=f"Promotion recovery failed: {_summarize_exception(respawn_exc)}",
                promoting_lane_id="",
                status_text=f"Promotion recovery failed for {current_lane.lane_id}; awaiting retry",
            )

    async def _reap_report_tasks(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        for report_id, task in list(self._report_tasks.items()):
            if not task.done():
                continue
            state = self._report_task_state.get(report_id)
            self._report_tasks.pop(report_id, None)
            self._report_task_state.pop(report_id, None)
            if task.cancelled():
                await self._handle_report_task_failure(
                    runner,
                    feature,
                    report_id,
                    RuntimeError("Report task was cancelled before completion."),
                    notice_key=f"report-task:{report_id}:{state.nonce}" if state else "",
                )
                continue
            exc = task.exception()
            if exc:
                logger.exception("Report task failed for %s", report_id, exc_info=exc)
                await self._handle_report_task_failure(
                    runner,
                    feature,
                    report_id,
                    exc,
                    notice_key=f"report-task:{report_id}:{state.nonce}" if state else "",
                )

    async def _reap_planning_task(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        if not self._planning_task or not self._planning_task.done():
            return
        task = self._planning_task
        state = self._planning_task_state
        self._planning_task = None
        self._planning_task_state = None
        report_ids = list(self._planning_report_ids)
        self._planning_report_ids = []
        if task.cancelled():
            await self._handle_planning_task_failure(
                runner,
                feature,
                report_ids,
                RuntimeError("Shared RCA planning task was cancelled before completion."),
                notice_key=f"planning-task:{state.nonce}" if state else "",
            )
            return
        exc = task.exception()
        if not exc:
            return
        logger.exception("Bug planning task failed", exc_info=exc)
        await self._handle_planning_task_failure(
            runner,
            feature,
            report_ids,
            exc,
            notice_key=f"planning-task:{state.nonce}" if state else "",
        )
        await self._maybe_revalidate_pending_retriage(runner, feature)

    async def _reap_lane_tasks(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        for lane_id, task in list(self._lane_tasks.items()):
            if not task.done():
                continue
            state = self._lane_task_state.get(lane_id)
            self._lane_tasks.pop(lane_id, None)
            self._lane_task_state.pop(lane_id, None)
            lane = await _load_lane(runner, feature, lane_id)
            if task.cancelled():
                if lane is None:
                    continue
                if state and lane.execution_nonce != state.nonce:
                    if await _respawn_intent_is_applied(runner, feature, lane_id):
                        continue
                    continue
                await self._handle_lane_task_failure(
                    runner,
                    feature,
                    lane,
                    RuntimeError("Lane task was cancelled before completion."),
                    failure_kind="infrastructure",
                    expected_nonce=state.nonce if state else None,
                    notice_key=f"lane-task:{lane_id}:{state.nonce}" if state else "",
                )
                continue
            exc = task.exception()
            if exc:
                logger.exception("Lane task failed for %s", lane_id, exc_info=exc)
                if not lane:
                    continue
                if state and lane.execution_nonce != state.nonce:
                    if await _respawn_intent_is_applied(runner, feature, lane_id):
                        continue
                    continue
                await self._handle_lane_task_failure(
                    runner,
                    feature,
                    lane,
                    exc,
                    failure_kind="infrastructure",
                    expected_nonce=state.nonce if state else None,
                    notice_key=f"lane-task:{lane_id}:{state.nonce}" if state else "",
                )
        await self._maybe_revalidate_pending_retriage(runner, feature)

    async def _reap_promotion_task(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        if not self._promotion_task or not self._promotion_task.done():
            return
        task = self._promotion_task
        state = self._promotion_task_state
        self._promotion_task = None
        self._promotion_task_state = None
        promotion_lane_id = self._promotion_lane_id
        self._promotion_lane_id = ""
        if task.cancelled():
            promotion = await _load_promotion_queue(runner, feature)
            if state and promotion.execution_nonce != state.nonce:
                return
            lane_id = promotion.promoting_lane_id or promotion_lane_id
            lane = await _load_lane(runner, feature, lane_id) if lane_id else None
            await self._handle_promotion_task_failure(
                runner,
                feature,
                lane,
                RuntimeError("Lane promotion task was cancelled before completion."),
                expected_nonce=state.nonce if state else None,
                notice_key=f"promotion-task:{lane_id}:{state.nonce}" if state and lane_id else "",
            )
            return
        exc = task.exception()
        if not exc:
            return
        logger.exception("Lane promotion task failed", exc_info=exc)
        promotion = await _load_promotion_queue(runner, feature)
        if state and promotion.execution_nonce != state.nonce:
            return
        lane_id = promotion.promoting_lane_id or promotion_lane_id
        lane = await _load_lane(runner, feature, lane_id) if lane_id else None
        await self._handle_promotion_task_failure(
            runner,
            feature,
            lane,
            exc,
            expected_nonce=state.nonce if state else None,
            notice_key=f"promotion-task:{lane_id}:{state.nonce}" if state and lane_id else "",
        )
        await self._maybe_revalidate_pending_retriage(runner, feature)

    async def _maybe_revalidate_pending_retriage(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        if not getattr(runner, "feature_store", None):
            return
        if self._planning_task or self._promotion_task:
            return
        if await _has_active_bug_lanes(runner, feature):
            return
        pending = [
            report
            for report in await _load_reports(runner, feature)
            if report.status == "pending_retriage"
        ]
        if not pending:
            return
        await self._revalidate_pending_retriage(runner, feature)

    async def _process_report(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        report_id: str,
    ) -> None:
        report = await _load_report(runner, feature, report_id)
        if not report:
            return

        resolver, thread_user = _make_thread_user(runner, feature, report)
        if not resolver:
            raise RuntimeError("Thread interaction runtime is unavailable for bugflow")

        if report.status == "intake_pending":
            await _post_thread_message(
                runner,
                feature,
                report.thread_ts,
                f"{report.report_id}: intake started. I'll ask clarifying questions here.",
            )
            report.thread_status = "waiting on user"
            report.current_step = f"Interviewing {report.report_id}"
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)

            envelope: Envelope[BugflowIntake] = await runner.run(
                Interview(
                    questioner=_make_thread_actor(
                        runner,
                        feature,
                        report,
                        bug_interviewer,
                        f"bugflow-intake-{report.report_id}",
                    ),
                    responder=thread_user,
                    initial_prompt=(
                        f"## Bugflow Intake\n\n"
                        f"Source feature: {feature.metadata.get('source_feature_name') or feature.metadata.get('source_feature_id')}\n\n"
                        f"Original root report:\n{report.root_message_text or report.summary or report.title}\n\n"
                        "I'll help capture this report. What happened, what did you expect, "
                        "and what should I pay attention to while verifying it? "
                        "Use the original root report as the starting point, and only ask focused follow-up questions."
                    ),
                    output_type=Envelope[BugflowIntake],
                    done=envelope_done,
                ),
                feature,
                phase_name=self.name,
            )

            intake = envelope.output or BugflowIntake()
            if not (intake.title or intake.description or report.root_message_text):
                report.status = "cancelled"
                report.current_step = "No actionable report captured"
                report.thread_status = "ready"
                report.updated_at = utc_now()
                await _save_report(runner, feature, report)
                return

            report.title = intake.title or report.title or _derive_title(report.root_message_text)
            report.summary = intake.summary or intake.description or report.summary
            report.interview_output = to_str(intake)
            report.expected_behavior = intake.expected_behavior
            report.actual_behavior = intake.actual_behavior
            report.affected_area = intake.affected_area
            report.severity = intake.severity or report.severity or "major"
            report.category = intake.candidate_category or report.category
            report.status = "classification_pending"
            report.current_step = f"Classifying {report.report_id}"
            report.thread_status = "ready"
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)

        report = await _load_report(runner, feature, report.report_id)
        if not report:
            return

        if report.status == "classification_pending":
            classification: Observation = await runner.run(
                Ask(
                    actor=_make_thread_actor(
                        runner,
                        feature,
                        report,
                        bug_interviewer,
                        f"bugflow-classify-{report.report_id}",
                    ),
                    prompt=_classification_prompt(report),
                    output_type=Observation,
                ),
                feature,
                phase_name=self.name,
            )

            report.category = _normalize_category(classification.category or report.category)
            report.severity = classification.severity or report.severity or "major"
            report.title = classification.title or report.title
            report.classification_summary = to_str(classification)
            ui_involved, evidence_modes = _classify_surface_flags(report, classification)
            report.ui_involved = ui_involved
            report.evidence_modes = evidence_modes
            if not report.summary:
                report.summary = classification.description or report.summary
            report.expected_behavior = classification.expected_behavior or report.expected_behavior
            report.affected_area = classification.affected_area or report.affected_area
            report.updated_at = utc_now()

            if report.category == "clarification":
                report.status = "awaiting_confirmation"
                report.current_step = f"Awaiting clarification approval for {report.report_id}"
                report.thread_status = "waiting on user"
            elif report.category == "requirement":
                report.status = "queued"
                report.current_step = f"Queued requirement lane for {report.report_id}"
                report.thread_status = "ready"
            elif report.category == "missing_test":
                report.status = "queued"
                report.current_step = f"Queued test lane for {report.report_id}"
                report.thread_status = "ready"
            else:
                report.category = "bug"
                report.status = "validation_pending"
                report.current_step = f"Validating {report.report_id}"
                report.thread_status = "ready"
            await _save_report(runner, feature, report)

        report = await _load_report(runner, feature, report.report_id)
        if not report:
            return

        if report.status == "awaiting_confirmation":
            decision_text = report.summary or report.title or report.report_id
            approved = await runner.run(
                Gate(
                    approver=thread_user,
                    prompt=(
                        f"Current behavior appears to differ from the latest request.\n\n"
                        f"Prior expectation:\n{report.expected_behavior or 'Not explicitly captured'}\n\n"
                        f"Proposed new decision:\n{decision_text}\n\n"
                        "Approve this clarification override?"
                    ),
                ),
                feature,
                phase_name=self.name,
            )

            if approved is not True:
                report.status = "intake_pending"
                report.current_step = "Clarification rejected; awaiting revised intake"
                report.summary = str(approved) if isinstance(approved, str) else report.summary
                report.thread_status = "ready"
                report.updated_at = utc_now()
                await _save_report(runner, feature, report)
                return

            decision = BugflowDecisionRecord(
                decision_id=new_short_id("D"),
                report_ids=[report.report_id],
                title=report.title or report.report_id,
                summary=decision_text,
                old_expectation=report.expected_behavior,
                new_decision=decision_text,
                approved=True,
            )
            await _append_decision(runner, feature, decision)
            await runner.artifacts.put(
                decision_key(decision.decision_id),
                decision.model_dump_json(),
                feature=feature,
            )
            report.decision_id = decision.decision_id
            report.decision = decision
            report.thread_status = "ready"
            report.status = "queued"
            report.current_step = f"Queued clarification lane for {report.report_id}"
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)
            return

        if report.status != "validation_pending":
            return

        reproduction: ReproductionResult = await runner.run(
            Ask(
                actor=_make_thread_actor(
                    runner,
                    feature,
                    report,
                    bug_reproducer,
                    f"bugflow-repro-{report.report_id}",
                    runtime="secondary",
                ),
                prompt=_reproduction_prompt(report),
                output_type=ReproductionResult,
            ),
            feature,
            phase_name=self.name,
        )

        if not reproduction.reproduced:
            proof_record = await _store_report_proof(
                runner,
                feature,
                report,
                stage="reproduce",
                bundle=reproduction.proof,
                checks=reproduction.checks,
                context_root=_proof_context_root(runner, feature),
            )
            missing = _missing_terminal_approval_requirements(report, reproduction.proof, reproduction)
            if missing:
                await _reject_missing_terminal_proof(
                    runner,
                    feature,
                    report,
                    stage="reproduction",
                    missing=missing,
                    bundle=reproduction.proof,
                    approval_source=reproduction,
                )
                return
            report.status = "resolved-no-repro"
            report.current_step = f"{report.report_id} did not reproduce"
            report.validation_summary = reproduction.summary
            report.thread_status = "ready"
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)
            terminal_record = await _record_terminal_proof(
                runner,
                feature,
                report,
                source_record=proof_record,
                summary=reproduction.summary,
                bundle=reproduction.proof,
            )
            await _post_terminal_notice(
                runner,
                feature,
                report,
                notice=f"{report.report_id}: I couldn't reproduce this on the current bugflow branch, so I closed it as no-repro.",
                proof_record=terminal_record,
            )
            return

        verdict: Verdict = await runner.run(
            Ask(
                actor=_make_thread_actor(
                    runner,
                    feature,
                    report,
                    integration_tester,
                    f"bugflow-validate-{report.report_id}",
                    runtime="secondary",
                ),
                prompt=_validation_prompt(report, reproduction),
                output_type=Verdict,
            ),
            feature,
            phase_name=self.name,
        )

        report.validation_summary = verdict.summary or reproduction.summary
        report.validation_verdict = verdict.model_dump(mode="json")
        proof_record = await _store_report_proof(
            runner,
            feature,
            report,
            stage="validate",
            bundle=verdict.proof,
            checks=verdict.checks,
            context_root=_proof_context_root(runner, feature),
        )
        report.updated_at = utc_now()
        if verdict.approved:
            missing = _missing_terminal_approval_requirements(report, verdict.proof, verdict)
            if missing:
                await _reject_missing_terminal_proof(
                    runner,
                    feature,
                    report,
                    stage="validation",
                    missing=missing,
                    bundle=verdict.proof,
                    approval_source=verdict,
                )
                return
            report.status = "resolved-no-repro"
            report.current_step = f"{report.report_id} validated cleanly"
            report.thread_status = "ready"
            await _save_report(runner, feature, report)
            terminal_record = await _record_terminal_proof(
                runner,
                feature,
                report,
                source_record=proof_record,
                summary=verdict.summary or reproduction.summary,
                bundle=verdict.proof,
            )
            await _post_terminal_notice(
                runner,
                feature,
                report,
                notice=f"{report.report_id}: validation shows this no longer needs a fix, so I resolved it without code changes.",
                proof_record=terminal_record,
            )
            return

        if self._planning_task or await _has_active_bug_lanes(runner, feature):
            report.status = "pending_retriage"
            report.current_step = f"{report.report_id} waiting for post-fix retriage"
        else:
            report.status = "queued"
            report.current_step = f"{report.report_id} queued for RCA"
        report.thread_status = "ready"
        await _save_report(runner, feature, report)

    async def _plan_bug_reports(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        report_ids: list[str],
    ) -> None:
        async with runner.feature_store.advisory_lock(feature.id, "bugflow-planning"):
            reports = [
                report
                for report in [await _load_report(runner, feature, report_id) for report_id in report_ids]
                if report and report.category == "bug" and report.status == "queued" and not report.lane_id
            ]
            if not reports:
                return

            for report in reports:
                report.current_step = f"Planning RCA for {report.report_id}"
                report.updated_at = utc_now()
                await _save_report(runner, feature, report)

            prior_attempts = _load_prior_attempts(
                await runner.artifacts.get("bug-fix-attempts", feature=feature)
            )
            source = f"bugflow-plan-{new_short_id('P').lower()}"
            planning_actor_factory = None
            if len(reports) == 1:
                planning_report = reports[0]

                def planning_actor_factory(base: Any, suffix: str) -> Any:
                    return _make_thread_actor(
                        runner,
                        feature,
                        planning_report,
                        base,
                        suffix,
                        runtime="secondary",
                    )

            dispatch = await _plan_bug_groups(
                runner,
                feature,
                _build_synthetic_bug_verdict(reports),
                source,
                prior_attempts,
                phase_name=self.name,
                repos_root=_get_feature_root(runner, feature),
                rca_runtime="secondary",
                actor_factory=planning_actor_factory,
            )

            if not dispatch.groups:
                for report in reports:
                    await _block_report_with_notice(
                        runner,
                        feature,
                        report,
                        current_step="Planning produced no executable RCA groups",
                        summary="Planning produced no executable RCA groups",
                        notice=f"{report.report_id}: I couldn't derive an executable RCA plan for this report, so I marked it blocked.",
                        terminal_reason_kind="planning-no-rca",
                    )
                return

            reports_by_index = {index: report for index, report in enumerate(reports)}
            round_lookup = _schedule_lookup(dispatch)
            fixable_groups: list[PlannedBugGroup] = []
            for planned in dispatch.groups:
                if planned.rca.confidence != "contradiction":
                    fixable_groups.append(planned)
                    continue
                resolved = await _resolve_contradiction_group(
                    runner,
                    feature,
                    planned,
                    [reports_by_index[index] for index in planned.group.issue_indices if index in reports_by_index],
                )
                if resolved is None:
                    continue
                fixable_groups.append(
                    PlannedBugGroup(
                        group=planned.group,
                        rca=resolved,
                        issue_text=planned.issue_text,
                        rca_key=planned.rca_key,
                    )
                )

            for planned in fixable_groups:
                group_reports = [
                    reports_by_index[index]
                    for index in planned.group.issue_indices
                    if index in reports_by_index
                ]
                if not group_reports:
                    continue
                if any(report.lane_id or report.status != "queued" for report in group_reports):
                    continue
                current_attempt, _max_attempts = _lane_attempt_budget(group_reports)
                for report in group_reports:
                    _ensure_report_retry_state(report)
                    report.attempts_used = max(report.attempts_used, current_attempt - 1)
                cluster_id = new_short_id("C")
                lane_id = new_short_id("L")
                main_root = _get_feature_root(runner, feature)
                if not main_root:
                    raise RuntimeError("Missing main bugflow root for lane planning")
                lane_root, branch_names_by_repo, base_heads = await _create_lane_worktree_root(
                    main_root,
                    feature,
                    lane_id,
                )
                lock_scope, repo_paths = _derive_lock_scope(planned.rca.affected_files)
                round_number, total_rounds = round_lookup.get(
                    planned.group.group_id,
                    (None, len(dispatch.schedule) or None),
                )
                cluster = BugflowClusterSnapshot(
                    cluster_id=cluster_id,
                    group_id=planned.group.group_id,
                    report_ids=[report.report_id for report in group_reports],
                    lane_id=lane_id,
                    status="planned",
                    current_phase="planned",
                    likely_root_cause=planned.group.likely_root_cause,
                    affected_files=planned.rca.affected_files or planned.group.affected_files_hint,
                    repo_paths=repo_paths,
                    schedule_round=round_number,
                    schedule_total_rounds=total_rounds,
                    attempt_number=current_attempt,
                    latest_rca_key=planned.rca_key,
                    latest_dispatch_key=dispatch.dispatch_key,
                    latest_rca_summary=to_str(planned.rca),
                    round_plan=_round_plan(dispatch.schedule),
                )
                lane = BugflowLaneSnapshot(
                    lane_id=lane_id,
                    report_ids=[report.report_id for report in group_reports],
                    category="bug",
                    source_cluster_id=cluster_id,
                    status="planned",
                    lock_scope=lock_scope,
                    repo_paths=repo_paths,
                    workspace_root=str(lane_root),
                    branch_names_by_repo=branch_names_by_repo,
                    base_main_commits_by_repo=base_heads,
                    latest_rca_keys=[planned.rca_key],
                    latest_dispatch_key=dispatch.dispatch_key,
                    latest_rca_summary=to_str(planned.rca),
                    issue_summary=planned.issue_text,
                    verification_actor="integration_tester",
                )
                await _save_cluster(runner, feature, cluster)
                await _save_lane(runner, feature, lane)

                for report in group_reports:
                    report.cluster_id = cluster_id
                    report.cluster = cluster.model_dump(mode="json")
                    report.lane_id = lane_id
                    report.current_step = f"Queued for lane {lane_id}"
                    report.updated_at = utc_now()
                    await _save_report(runner, feature, report)
                if not await _initialize_cluster_strategy(
                    runner,
                    feature,
                    cluster,
                    lane,
                    group_reports,
                    planned,
                ):
                    continue
                refreshed_cluster = await _load_cluster(runner, feature, cluster_id) or cluster
                for report in group_reports:
                    report.cluster = refreshed_cluster.model_dump(mode="json")
                    report.updated_at = utc_now()
                    await _save_report(runner, feature, report)
                    await _post_thread_message(
                        runner,
                        feature,
                        report.thread_ts,
                        f"{report.report_id}: assigned to isolated lane {lane_id} for RCA and fixing.",
                    )

    async def _admit_planned_lanes(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lanes: list[BugflowLaneSnapshot],
    ) -> None:
        active_locks: list[list[str]] = [
            lane.lock_scope
            for lane in lanes
            if lane.status in {"active_fix", "active_verify"}
        ]
        for lane in sorted(lanes, key=lambda item: (item.created_at, item.lane_id)):
            if lane.lane_id in self._lane_tasks:
                continue
            if lane.status != "planned":
                continue
            if any(_lock_scopes_overlap(lock_scope, lane.lock_scope) for lock_scope in active_locks):
                lane.wait_reason = "Waiting for overlapping lane work to finish"
                lane.updated_at = utc_now()
                await _save_lane(runner, feature, lane)
                continue

            lane.status = "active_fix"
            lane.wait_reason = ""
            lane.updated_at = utc_now()
            await _mark_cluster_from_lane(
                runner,
                feature,
                lane,
                status="active_fix",
                current_phase="fixing",
                wait_reason="Executing in isolated lane",
            )
            await _mark_lane_reports_active(
                runner,
                feature,
                lane,
                current_step=f"Executing isolated lane {lane.lane_id}",
            )
            await self._track_lane_task(
                runner,
                feature,
                lane,
                self._execute_lane(runner, feature, lane.lane_id),
            )
            active_locks.append(lane.lock_scope)

    async def _ensure_non_bug_lanes(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        reports: list[BugflowReportSnapshot],
    ) -> None:
        main_root = _get_feature_root(runner, feature)
        if not main_root:
            return
        for report in reports:
            if report.category not in {"clarification", "requirement", "missing_test"}:
                continue
            if report.status != "queued" or report.lane_id:
                continue
            lane_id = new_short_id("L")
            cluster_id = new_short_id("C")
            lane_root, branch_names_by_repo, base_heads = await _create_lane_worktree_root(
                main_root,
                feature,
                lane_id,
            )
            scope_seed = []
            if isinstance(report.affected_area, str) and "/" in report.affected_area:
                scope_seed = [report.affected_area]
            lock_scope, repo_paths = _derive_lock_scope(scope_seed)
            cluster = BugflowClusterSnapshot(
                cluster_id=cluster_id,
                report_ids=[report.report_id],
                lane_id=lane_id,
                status="planned",
                current_phase="planned",
                    wait_reason=f"Queued {report.category} lane",
                    likely_root_cause=report.summary or report.title or report.report_id,
                    affected_files=scope_seed,
                    repo_paths=repo_paths,
                    attempt_number=_current_report_attempt(report),
                )
            lane = BugflowLaneSnapshot(
                lane_id=lane_id,
                report_ids=[report.report_id],
                category=report.category,
                source_cluster_id=cluster_id,
                status="planned",
                current_phase="planned",
                lock_scope=lock_scope,
                repo_paths=repo_paths,
                workspace_root=str(lane_root),
                branch_names_by_repo=branch_names_by_repo,
                base_main_commits_by_repo=base_heads,
                issue_summary=report.summary or report.title or report.report_id,
                verification_actor="verifier",
            )
            await _save_cluster(runner, feature, cluster)
            await _save_lane(runner, feature, lane)
            report.cluster_id = cluster_id
            report.cluster = cluster.model_dump(mode="json")
            report.lane_id = lane_id
            report.current_step = f"Queued for lane {lane_id}"
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)
            await _post_thread_message(
                runner,
                feature,
                report.thread_ts,
                f"{report.report_id}: assigned to isolated lane {lane_id} for {report.category} work.",
            )

    async def _maybe_start_promotion(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lanes: list[BugflowLaneSnapshot],
    ) -> None:
        if self._promotion_task is not None:
            return
        promotion = await _load_promotion_queue(runner, feature)
        if promotion.promoting_lane_id:
            return
        lane = next(
            (
                item for item in sorted(lanes, key=lambda entry: (entry.updated_at, entry.lane_id))
                if item.status == "verified_pending_promotion"
            ),
            None,
        )
        if lane is None:
            return
        self._promotion_lane_id = lane.lane_id
        await self._track_promotion_task(
            runner,
            feature,
            lane.lane_id,
            self._promote_lane(runner, feature, lane.lane_id),
        )

    async def _execute_lane(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane_id: str,
    ) -> None:
        lane = await _load_lane(runner, feature, lane_id)
        if not lane:
            return

        if lane.category == "bug":
            success = await self._execute_bug_lane(runner, feature, lane)
        else:
            success = await self._execute_observation_lane(runner, feature, lane)

        lane = await _load_lane(runner, feature, lane_id)
        if not lane:
            return
        if success:
            lane.status = "verified_pending_promotion"
            lane.promotion_status = "queued"
            lane.updated_at = utc_now()
            await _save_lane(runner, feature, lane)
            await _mark_cluster_from_lane(
                runner,
                feature,
                lane,
                status="verified_pending_promotion",
                current_phase="reverify",
                wait_reason="Verified in lane; waiting promotion",
            )
            await _mark_lane_reports_waiting_promotion(runner, feature, lane)
        else:
            if lane.category == "bug":
                reason = await _lane_failure_reason(runner, feature, lane)
                await _retry_or_block_bug_lane(
                    runner,
                    feature,
                    lane,
                    reason=reason,
                    failure_kind=_infer_retryable_failure_kind(None, lane) or "lane-verify",
                )
                return
            lane.status = "blocked"
            lane.promotion_status = "blocked"
            lane.wait_reason = (
                lane.wait_reason
                or lane.latest_regression_summary
                or lane.latest_verify_summary
                or lane.latest_fix_summary
                or "Lane execution ended without a promotable result."
            )
            lane.updated_at = utc_now()
            await _save_lane(runner, feature, lane)
            await _mark_cluster_from_lane(
                runner,
                feature,
                lane,
                status="blocked",
                current_phase="blocked",
                wait_reason=lane.wait_reason,
            )
            await _mark_lane_reports_blocked(
                runner,
                feature,
                lane,
                _lane_failure_message(lane),
                failure_kind="non-bug-lane",
                failure_reason=lane.wait_reason,
            )

    async def _execute_bug_lane(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane: BugflowLaneSnapshot,
    ) -> bool:
        lane_root = Path(lane.workspace_root)
        cluster = await _load_cluster_from_lane(runner, feature, lane)
        report_map = await _load_reports_by_id(runner, feature, lane.report_ids)
        reports = [report_map[report_id] for report_id in lane.report_ids if report_id in report_map]
        if not reports:
            raise RuntimeError(f"{lane.lane_id} has no report snapshots")
        strategy_decision = await _load_cluster_strategy_decision(runner, feature, cluster)

        rca = parse_model(
            await runner.artifacts.get(lane.latest_rca_keys[0], feature=feature)
            if lane.latest_rca_keys else None,
            RootCauseAnalysis,
        )
        if not isinstance(rca, RootCauseAnalysis):
            raise RuntimeError(f"{lane.lane_id} is missing RCA context")

        prior_attempts = _load_prior_attempts(
            await runner.artifacts.get("bug-fix-attempts", feature=feature)
        )
        prior_context = _format_prior_attempts(prior_attempts, context_base=lane_root)
        ws_path = _resolve_fix_workspace_from_root(lane_root, rca.affected_files) or str(lane_root)
        lane_actor_factory = lambda base, suffix, *, runtime=None, workspace_path=None: _make_lane_actor(
            runner,
            feature,
            reports,
            base,
            suffix,
            runtime=runtime,
            workspace_path=workspace_path,
        )

        lane.current_phase = "fixing"
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)

        fix_result: ImplementationResult = await runner.run(
            Ask(
                actor=_make_lane_actor(
                    runner,
                    feature,
                    reports,
                    implementer,
                    f"lane-fix-{lane.lane_id}",
                    runtime="primary",
                    workspace_path=ws_path,
                ),
                prompt=_lane_fix_prompt(lane, reports, rca, strategy_decision),
                output_type=ImplementationResult,
            ),
            feature,
            phase_name=self.name,
        )
        await _commit_repos_in_root(lane_root, f"fix: {lane.lane_id} attempt {lane.lane_attempt}")

        lane.latest_fix_summary = fix_result.summary
        lane.modified_files = sorted(set(fix_result.files_created + fix_result.files_modified))
        lane.implementation_result = fix_result.model_dump(mode="json")
        lane.status = "active_verify"
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)
        await _mark_cluster_from_lane(
            runner,
            feature,
            lane,
            status="active_fix",
            current_phase="reverify",
            wait_reason="Re-verifying isolated lane",
        )
        await _mark_lane_reports_active(
            runner,
            feature,
            lane,
            current_step=f"Re-verifying isolated lane {lane.lane_id}",
        )

        reverify_key = f"bug-reverify:lane:{lane.lane_id}:attempt-{lane.lane_attempt}"
        re_verdict: Verdict = await runner.run(
            Ask(
                actor=_make_lane_actor(
                    runner,
                    feature,
                    reports,
                    integration_tester,
                    f"lane-verify-{lane.lane_id}",
                    runtime="secondary",
                    workspace_path=str(lane_root),
                ),
                prompt=_lane_verify_prompt(lane, reports, rca, fix_result.summary, strategy_decision),
                output_type=Verdict,
            ),
            feature,
            phase_name=self.name,
        )
        await runner.artifacts.put(reverify_key, to_str(re_verdict), feature=feature)
        lane.latest_verify_keys = [reverify_key]
        lane.latest_verify_summary = to_str(re_verdict)
        for report in reports:
            await _store_report_proof(
                runner,
                feature,
                report,
                stage="lane-verify",
                bundle=re_verdict.proof,
                checks=re_verdict.checks,
                context_root=lane_root,
            )

        attempt = BugFixAttempt(
            bug_id=f"{lane.lane_id}-ATTEMPT-{lane.lane_attempt}",
            group_id=cluster.group_id if cluster else lane.source_cluster_id,
            source_verdict=f"lane:{lane.lane_id}",
            description=lane.issue_summary,
            root_cause=rca.hypothesis,
            fix_applied=fix_result.summary,
            files_modified=lane.modified_files,
            re_verify_result="PASS" if re_verdict.approved else "FAIL",
            attempt_number=lane.lane_attempt,
        )
        updated_attempts = prior_attempts + [attempt]
        new_attempts = [attempt]

        if not re_verdict.approved:
            retry_attempt = await _single_rca_fix_verify(
                runner,
                feature,
                _format_feedback("Lane Reverify", re_verdict),
                f"lane-retry:{lane.lane_id}",
                _make_lane_actor(
                    runner,
                    feature,
                    reports,
                    integration_tester,
                    f"lane-retry-reviewer-{lane.lane_id}",
                    runtime="secondary",
                ),
                _make_lane_actor(
                    runner,
                    feature,
                    reports,
                    implementer,
                    f"lane-retry-fixer-{lane.lane_id}",
                    runtime="primary",
                ),
                _format_prior_attempts(updated_attempts, context_base=lane_root),
                bug_id=f"{lane.lane_id}-retry-{lane.lane_attempt}",
                attempt_number=lane.lane_attempt + 1,
                phase_name=self.name,
                workspace_root=lane_root,
                rca_runtime="secondary",
                actor_factory=lane_actor_factory,
            )
            updated_attempts.append(retry_attempt)
            new_attempts.append(retry_attempt)
            await _append_bug_fix_attempts(runner, feature, new_attempts)
            retry_verify_key = await _store_attempt_verdict_artifact(
                runner,
                feature,
                reports[0],
                retry_attempt,
                key=f"bug-reverify:lane-retry:{lane.lane_id}:{lane.lane_id}-retry-{lane.lane_attempt}",
                summary=(
                    f"Retry re-verify for {lane.lane_id} finished with "
                    f"{retry_attempt.re_verify_result}."
                ),
                stage="lane-retry",
            )
            lane.latest_verify_keys = [retry_verify_key]
            lane.latest_verify_summary = retry_attempt.re_verify_result
            lane.latest_fix_summary = retry_attempt.fix_applied
            lane.modified_files = retry_attempt.files_modified
            lane.implementation_result = {
                "summary": retry_attempt.fix_applied,
                "files_modified": retry_attempt.files_modified,
                "files_created": [],
            }
            for report in reports:
                await _store_report_proof(
                    runner,
                    feature,
                    report,
                    stage="lane-verify",
                    bundle=_attempt_proof_bundle(
                        report,
                        retry_attempt,
                        summary=(
                            f"Retry re-verify for {lane.lane_id} finished with "
                            f"{retry_attempt.re_verify_result}."
                        ),
                        stage="lane-retry",
                    ),
                    context_root=lane_root,
                )
            await _save_lane(runner, feature, lane)
            return retry_attempt.re_verify_result == "PASS"

        regression_key = f"bug-regression:lane:{lane.lane_id}:attempt-{lane.lane_attempt}"
        regression = await _run_regression(
            runner,
            feature,
            lane.modified_files,
            phase_name=self.name,
            workspace_root=lane_root,
            regression_runtime="secondary",
            integration_runtime="secondary",
            actor_factory=lane_actor_factory,
        )
        if regression is not None:
            await runner.artifacts.put(regression_key, to_str(regression), feature=feature)
            lane.latest_regression_keys = [regression_key]
            lane.latest_regression_summary = to_str(regression)
            for report in reports:
                await _store_report_proof(
                    runner,
                    feature,
                    report,
                    stage="lane-verify",
                    bundle=regression.proof,
                    checks=regression.checks,
                    context_root=lane_root,
                )
        if regression is not None and not regression.approved:
            retry_attempt = await _single_rca_fix_verify(
                runner,
                feature,
                _format_feedback("Lane Regression", regression),
                f"lane-regression:{lane.lane_id}",
                _make_lane_actor(
                    runner,
                    feature,
                    reports,
                    integration_tester,
                    f"lane-regression-reviewer-{lane.lane_id}",
                    runtime="secondary",
                ),
                _make_lane_actor(
                    runner,
                    feature,
                    reports,
                    implementer,
                    f"lane-regression-fixer-{lane.lane_id}",
                    runtime="primary",
                ),
                _format_prior_attempts(updated_attempts, context_base=lane_root),
                bug_id=f"{lane.lane_id}-regression-{lane.lane_attempt}",
                attempt_number=lane.lane_attempt + 1,
                handover_context=lane.issue_summary,
                phase_name=self.name,
                workspace_root=lane_root,
                rca_runtime="secondary",
                actor_factory=lane_actor_factory,
            )
            updated_attempts.append(retry_attempt)
            new_attempts.append(retry_attempt)
            await _append_bug_fix_attempts(runner, feature, new_attempts)
            retry_verify_key = await _store_attempt_verdict_artifact(
                runner,
                feature,
                reports[0],
                retry_attempt,
                key=f"bug-reverify:lane-regression:{lane.lane_id}:{lane.lane_id}-retry-{lane.lane_attempt}",
                summary=(
                    f"Regression retry for {lane.lane_id} finished with "
                    f"{retry_attempt.re_verify_result}."
                ),
                stage="lane-regression-retry",
            )
            lane.latest_verify_keys = [retry_verify_key]
            lane.latest_regression_keys = []
            lane.latest_fix_summary = retry_attempt.fix_applied
            lane.modified_files = retry_attempt.files_modified
            lane.latest_verify_summary = retry_attempt.re_verify_result
            lane.latest_regression_summary = ""
            for report in reports:
                await _store_report_proof(
                    runner,
                    feature,
                    report,
                    stage="lane-verify",
                    bundle=_attempt_proof_bundle(
                        report,
                        retry_attempt,
                        summary=(
                            f"Regression retry for {lane.lane_id} finished with "
                            f"{retry_attempt.re_verify_result}."
                        ),
                        stage="lane-regression-retry",
                    ),
                    context_root=lane_root,
                )
            await _save_lane(runner, feature, lane)
            return retry_attempt.re_verify_result == "PASS"

        await _append_bug_fix_attempts(runner, feature, new_attempts)
        lane.latest_fix_summary = fix_result.summary
        lane.latest_verify_summary = to_str(re_verdict)
        await _save_lane(runner, feature, lane)
        return True

    async def _execute_observation_lane(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane: BugflowLaneSnapshot,
    ) -> bool:
        lane_root = Path(lane.workspace_root)
        report_map = await _load_reports_by_id(runner, feature, lane.report_ids)
        if not report_map:
            raise RuntimeError(f"{lane.lane_id} has no report snapshots")
        report = next(iter(report_map.values()))
        observation = _observation_from_report(report)
        lane.observation_payload = observation.model_dump(mode="json")
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)
        result = await _dispatch_observation(
            runner,
            feature,
            observation,
            observation_context=await _source_context_text(runner, feature),
            phase_name=self.name,
            workspace_root=lane_root,
            rca_runtime="secondary",
            implement_runtime="primary",
            test_runtime="primary",
            verify_runtime="secondary",
            actor_factory=lambda base, suffix, *, runtime=None, workspace_path=None: _make_lane_actor(
                runner,
                feature,
                list(report_map.values()),
                base,
                suffix,
                runtime=runtime,
                workspace_path=workspace_path,
            ),
        )
        lane.modified_files = await _lane_modified_files(
            lane_root,
            lane.base_main_commits_by_repo,
        )
        lane.latest_fix_summary = result.get("summary", "")
        lane.latest_verify_summary = result.get("status", "")
        verdict = parse_model(
            json.dumps(result.get("verdict")) if result.get("verdict") is not None else None,
            Verdict,
        )
        if isinstance(verdict, Verdict):
            for item in report_map.values():
                await _store_report_proof(
                    runner,
                    feature,
                    item,
                    stage="lane-verify",
                    bundle=verdict.proof,
                    checks=verdict.checks,
                    context_root=lane_root,
                )
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)
        return result.get("status") == "FIXED"

    async def _promote_lane(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        lane_id: str,
    ) -> None:
        main_root = _get_feature_root(runner, feature)
        if not main_root:
            raise RuntimeError("Missing main bugflow root for promotion")

        async with runner.feature_store.advisory_lock(feature.id, "bugflow-promotion"):
            lane = await _load_lane(runner, feature, lane_id)
            if not lane:
                await _refresh_promotion_queue(
                    runner,
                    feature,
                    status_text="Promotion idle",
                )
                return
            if lane.status == "promoted":
                await _refresh_promotion_queue(
                    runner,
                    feature,
                    status_text="Promotion idle",
                )
                return
            if lane.status != "verified_pending_promotion":
                await _refresh_promotion_queue(
                    runner,
                    feature,
                    status_text="Promotion idle",
                )
                return

            lane.status = "promoting"
            lane.promotion_status = "promoting"
            lane.promotion_attempt += 1
            lane.updated_at = utc_now()
            await _save_lane(runner, feature, lane)
            await _save_promotion_queue(
                runner,
                feature,
                BugflowPromotionQueueSnapshot(
                    promoting_lane_id=lane_id,
                    pending_lane_ids=[
                        entry.lane_id
                        for entry in await _load_lanes(runner, feature)
                        if entry.status == "verified_pending_promotion" and entry.lane_id != lane_id
                    ],
                    lock_owner=f"feature:{feature.id}",
                    status_text=f"Promoting {lane_id}",
                ),
            )
            await _mark_lane_reports_promoting(runner, feature, lane)

            promotion_root, _branches, _heads = await _create_promotion_worktree_root(
                main_root,
                feature,
                lane,
            )
            try:
                commits_by_repo = await _lane_commit_sequences(
                    Path(lane.workspace_root),
                    lane.base_main_commits_by_repo,
                )
                if not any(commits_by_repo.values()):
                    await _respawn_lane_from_latest_main(
                        runner,
                        feature,
                        lane,
                        "No lane-local commits were available to promote.",
                    )
                    await _refresh_promotion_queue(
                        runner,
                        feature,
                        status_text="Promotion idle",
                    )
                    return

                try:
                    await _cherry_pick_lane_commits(
                        main_root,
                        promotion_root,
                        commits_by_repo,
                    )
                except RuntimeError as exc:
                    await _respawn_lane_from_latest_main(
                        runner,
                        feature,
                        lane,
                        f"Replay conflict while preparing promotion: {_summarize_exception(exc)}",
                    )
                    await _refresh_promotion_queue(
                        runner,
                        feature,
                        status_text="Promotion idle",
                    )
                    return

                promotion_verdict = await _promotion_verify_lane(
                    runner,
                    feature,
                    lane,
                    promotion_root,
                )
                promotion_reports = list((await _load_reports_by_id(runner, feature, lane.report_ids)).values())
                for report in promotion_reports:
                    await _store_report_proof(
                        runner,
                        feature,
                        report,
                        stage="promotion-verify",
                        bundle=promotion_verdict.proof,
                        checks=promotion_verdict.checks,
                        context_root=promotion_root,
                    )
                if not promotion_verdict.approved:
                    await _retry_or_block_bug_lane(
                        runner,
                        feature,
                        lane,
                        reason=f"Promotion verification failed: {promotion_verdict.summary}",
                        failure_kind="promotion-verify",
                        current_verdict=promotion_verdict,
                    )
                    await _refresh_promotion_queue(
                        runner,
                        feature,
                        status_text="Promotion idle",
                    )
                    return
                missing_report_proof = {
                    report.report_id: _missing_terminal_approval_requirements(
                        report,
                        promotion_verdict.proof,
                        promotion_verdict,
                    )
                    for report in promotion_reports
                }
                missing_report_proof = {
                    report_id: missing
                    for report_id, missing in missing_report_proof.items()
                    if missing
                }
                if missing_report_proof:
                    await _retry_missing_promotion_proof(
                        runner,
                        feature,
                        lane,
                        promotion_reports,
                        bundle=promotion_verdict.proof,
                        missing_by_report_id=missing_report_proof,
                        approval_source=promotion_verdict,
                    )
                    await _refresh_promotion_queue(
                        runner,
                        feature,
                        status_text="Promotion idle",
                    )
                    return

                promotion_regression = await _run_regression(
                    runner,
                    feature,
                    lane.modified_files,
                    phase_name=self.name,
                    workspace_root=promotion_root,
                    regression_runtime="secondary",
                    integration_runtime="secondary",
                    actor_factory=lambda base, suffix, *, runtime=None, workspace_path=None: _make_lane_actor(
                        runner,
                        feature,
                        promotion_reports,
                        base,
                        suffix,
                        runtime=runtime,
                        workspace_path=workspace_path,
                    ),
                )
                if promotion_regression is not None and not promotion_regression.approved:
                    for report in promotion_reports:
                        await _store_report_proof(
                            runner,
                            feature,
                            report,
                            stage="promotion-verify",
                            bundle=promotion_regression.proof,
                            checks=promotion_regression.checks,
                            context_root=promotion_root,
                        )
                if promotion_regression is not None and not promotion_regression.approved:
                    await _retry_or_block_bug_lane(
                        runner,
                        feature,
                        lane,
                        reason=f"Promotion regression failed: {promotion_regression.summary}",
                        failure_kind="promotion-regression",
                        current_verdict=promotion_regression,
                    )
                    await _refresh_promotion_queue(
                        runner,
                        feature,
                        status_text="Promotion idle",
                    )
                    return

                lane = await _load_lane(runner, feature, lane_id) or lane
                lane.promotion_status = "applying-main"
                lane.updated_at = utc_now()
                await _save_lane(runner, feature, lane)
                await _cherry_pick_lane_commits(main_root, main_root, commits_by_repo)
                lane = await _load_lane(runner, feature, lane_id) or lane
                lane.promotion_status = "applied-main"
                lane.updated_at = utc_now()
                await _save_lane(runner, feature, lane)
                await _push_clones_to_source_root(main_root)

                lane = await _load_lane(runner, feature, lane_id) or lane
                await _finalize_promoted_lane(
                    runner,
                    feature,
                    lane,
                    ensure_push=False,
                )
                await _refresh_promotion_queue(
                    runner,
                    feature,
                    status_text="Promotion idle",
                )
                await self._revalidate_pending_retriage(runner, feature)
            finally:
                await _remove_worktree_root(main_root, promotion_root)

    async def _revalidate_pending_retriage(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        reports = [
            report
            for report in await _load_reports(runner, feature)
            if report.status == "pending_retriage"
        ]
        for report in reports:
            reproduction: ReproductionResult = await runner.run(
                Ask(
                    actor=_make_thread_actor(
                        runner,
                        feature,
                        report,
                        bug_reproducer,
                        f"bugflow-retriage-repro-{report.report_id}",
                        runtime="secondary",
                    ),
                    prompt=_reproduction_prompt(report),
                    output_type=ReproductionResult,
                ),
                feature,
                phase_name=self.name,
            )
            if not reproduction.reproduced:
                proof_record = await _store_report_proof(
                    runner,
                    feature,
                    report,
                    stage="reproduce",
                    bundle=reproduction.proof,
                    checks=reproduction.checks,
                    context_root=_proof_context_root(runner, feature),
                )
                missing = _missing_terminal_approval_requirements(report, reproduction.proof, reproduction)
                if missing:
                    await _reject_missing_terminal_proof(
                        runner,
                        feature,
                        report,
                        stage="retriage reproduction",
                        missing=missing,
                        bundle=reproduction.proof,
                        approval_source=reproduction,
                    )
                    continue
                report.status = "resolved"
                report.current_step = f"{report.report_id} covered by promoted fix"
                report.validation_summary = reproduction.summary
                report.lane_id = ""
                report.cluster_id = ""
                report.cluster = None
                report.pending_retriage_for_lane = ""
                terminal_record = await _record_terminal_proof(
                    runner,
                    feature,
                    report,
                    source_record=proof_record,
                    summary=reproduction.summary,
                    bundle=reproduction.proof,
                )
                await _post_terminal_notice(
                    runner,
                    feature,
                    report,
                    notice=f"{report.report_id}: a promoted lane appears to have covered this already, so I resolved it.",
                    proof_record=terminal_record,
                )
            else:
                verdict: Verdict = await runner.run(
                    Ask(
                        actor=_make_thread_actor(
                            runner,
                            feature,
                            report,
                            integration_tester,
                            f"bugflow-retriage-{report.report_id}",
                            runtime="secondary",
                        ),
                        prompt=_validation_prompt(report, reproduction),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                report.validation_summary = verdict.summary
                report.validation_verdict = verdict.model_dump(mode="json")
                proof_record = await _store_report_proof(
                    runner,
                    feature,
                    report,
                    stage="validate",
                    bundle=verdict.proof,
                    checks=verdict.checks,
                    context_root=_proof_context_root(runner, feature),
                )
                if verdict.approved:
                    missing = _missing_terminal_approval_requirements(report, verdict.proof, verdict)
                    if missing:
                        await _reject_missing_terminal_proof(
                            runner,
                            feature,
                            report,
                            stage="retriage validation",
                            missing=missing,
                            bundle=verdict.proof,
                            approval_source=verdict,
                        )
                        continue
                report.status = "resolved" if verdict.approved else "queued"
                report.current_step = (
                    f"{report.report_id} covered by promoted fix"
                    if verdict.approved
                    else f"{report.report_id} requeued after retriage"
                )
                if report.status == "queued":
                    report.cluster_id = ""
                    report.cluster = None
                    report.lane_id = ""
                else:
                    terminal_record = await _record_terminal_proof(
                        runner,
                        feature,
                        report,
                        source_record=proof_record,
                        summary=verdict.summary,
                        bundle=verdict.proof,
                    )
                    await _post_terminal_notice(
                        runner,
                        feature,
                        report,
                        notice=f"{report.report_id}: promotion-time retriage shows this has already been covered, so I resolved it.",
                        proof_record=terminal_record,
                    )
                report.pending_retriage_for_lane = ""
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)

    async def _write_queue_snapshot(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        reports: list[BugflowReportSnapshot],
        lanes: list[BugflowLaneSnapshot],
        clusters: list[BugflowClusterSnapshot],
    ) -> BugflowQueueSnapshot:
        clusters, normalized = await _normalize_cluster_strategy_states(
            runner,
            feature,
            clusters,
        )
        if normalized:
            reports = await _load_reports(runner, feature)
            lanes = await _load_lanes(runner, feature)
            clusters = await _load_clusters(
                runner,
                feature,
                await _load_queue(runner, feature),
                reports,
                lanes,
            )
        previous = await _load_queue(runner, feature)
        counts = compute_counts(reports)
        active_lanes = [lane for lane in lanes if lane.status in {"active_fix", "active_verify"}]
        recovering_lane_ids = [
            lane.lane_id for lane in lanes if lane.execution_state == "recovering"
        ]
        stalled_lane_ids = [
            lane.lane_id for lane in lanes if lane.execution_state == "stalled"
        ]
        proof_capture_retry_lane_ids = [
            lane.lane_id
            for lane in lanes
            if lane.promotion_status == "proof-capture-retry"
        ]
        strategy_pending_cluster_ids = [
            cluster.cluster_id
            for cluster in clusters
            if _strategy_checkpoint_kind(cluster) in {"pending", "decided"}
        ]
        verified_pending = [
            lane.lane_id for lane in lanes if lane.status == "verified_pending_promotion"
        ]
        promoting_lane = next((lane for lane in lanes if lane.status == "promoting"), None)
        active_report = next(
            (report for report in reports if lane_for_status(report.status) == "active_fix"),
            None,
        )
        active_cluster = next(
            (cluster for cluster in clusters if lane_for_status(cluster.status) == "active_fix"),
            None,
        )
        pending_retriage_ids = [
            report.report_id for report in reports if report.status == "pending_retriage"
        ]
        blocked_ids = [
            report.report_id for report in reports if lane_for_status(report.status) == "blocked"
        ]

        status_text, active_step, health = _queue_status(reports, lanes, clusters)
        queue = BugflowQueueSnapshot(
            source_feature_id=str(feature.metadata.get("source_feature_id", "") or ""),
            dashboard_url=str(feature.metadata.get("dashboard_url", "") or ""),
            health=health,
            active_step=active_step,
            active_report_id=active_report.report_id if active_report else "",
            active_cluster_id=active_cluster.cluster_id if active_cluster else "",
            active_lane_ids=[lane.lane_id for lane in active_lanes],
            verified_pending_promotion_ids=verified_pending,
            promoting_lane_id=promoting_lane.lane_id if promoting_lane else "",
            promotion_status_text=(
                f"Promoting {promoting_lane.lane_id}"
                if promoting_lane
                else f"Recapturing promotion proof for {', '.join(proof_capture_retry_lane_ids[:3])}"
                if proof_capture_retry_lane_ids
                else f"{len(verified_pending)} lanes waiting for promotion"
                if verified_pending
                else ""
            ),
            active_round=active_cluster.schedule_round if active_cluster else None,
            total_rounds=active_cluster.schedule_total_rounds if active_cluster else None,
            active_attempt=active_cluster.attempt_number if active_cluster else None,
            counts=counts,
            pending_retriage_ids=pending_retriage_ids,
            blocked_ids=blocked_ids,
            stalled_lane_ids=stalled_lane_ids,
            recovering_lane_ids=recovering_lane_ids,
            proof_capture_retry_lane_ids=proof_capture_retry_lane_ids,
            strategy_pending_cluster_ids=strategy_pending_cluster_ids,
            report_ids=[report.report_id for report in reports],
            cluster_ids=[cluster.cluster_id for cluster in clusters],
            lane_ids=[lane.lane_id for lane in lanes],
            status_text=status_text,
            last_transition_at=max(
                [previous.last_transition_at]
                + [report.updated_at for report in reports if report.updated_at]
                + [cluster.updated_at for cluster in clusters if cluster.updated_at]
                + [lane.updated_at for lane in lanes if lane.updated_at]
            ),
        )
        await runner.artifacts.put("bugflow-queue", queue.model_dump_json(), feature=feature)
        return queue


async def _load_queue(
    runner: WorkflowRunner,
    feature: Feature,
) -> BugflowQueueSnapshot:
    queue = parse_model(
        await runner.artifacts.get("bugflow-queue", feature=feature),
        BugflowQueueSnapshot,
        default=BugflowQueueSnapshot(),
    )
    assert isinstance(queue, BugflowQueueSnapshot)
    return queue


async def _load_reports(
    runner: WorkflowRunner,
    feature: Feature,
) -> list[BugflowReportSnapshot]:
    queue = await _load_queue(runner, feature)
    report_ids = set(queue.report_ids)
    for event in await runner.feature_store.get_events(feature.id):
        event_type = event.get("event_type") or event.get("kind")
        if event_type != _BUG_REPORT_EVENT:
            continue
        metadata = event.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        report_id = metadata.get("report_id") or event.get("content")
        if report_id:
            report_ids.add(str(report_id))

    reports: list[BugflowReportSnapshot] = []
    for report_id in sorted(report_ids):
        report = await _load_report(runner, feature, report_id)
        if report:
            reports.append(report)
    reports.sort(key=lambda report: (report.created_at, report.report_id))
    return reports


async def _load_reports_by_id(
    runner: WorkflowRunner,
    feature: Feature,
    report_ids: list[str],
) -> dict[str, BugflowReportSnapshot]:
    reports: dict[str, BugflowReportSnapshot] = {}
    for report_id in report_ids:
        report = await _load_report(runner, feature, report_id)
        if report:
            reports[report_id] = report
    return reports


async def _load_report(
    runner: WorkflowRunner,
    feature: Feature,
    report_id: str,
) -> BugflowReportSnapshot | None:
    raw = await runner.artifacts.get(report_key(report_id), feature=feature)
    report = parse_model(raw, BugflowReportSnapshot)
    if not isinstance(report, BugflowReportSnapshot):
        return None
    if not report.report_id:
        report.report_id = report_id
    return _ensure_report_retry_state(report)


async def _save_report(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
) -> None:
    _ensure_report_retry_state(report)
    report.updated_at = report.updated_at or utc_now()
    await runner.artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)


async def _load_proof_record(
    runner: WorkflowRunner,
    feature: Feature,
    artifact_key: str,
) -> BugflowProofRecord | None:
    raw = await runner.artifacts.get(artifact_key, feature=feature)
    proof = parse_model(raw, BugflowProofRecord)
    if not isinstance(proof, BugflowProofRecord):
        return None
    return proof


async def _store_report_proof(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    *,
    stage: str,
    bundle: EvidenceBundle | None,
    checks: list[Check] | None = None,
    context_root: Path | None = None,
) -> BugflowProofRecord | None:
    if bundle is None:
        return None
    main_root = _get_feature_root(runner, feature)
    if not main_root:
        return None
    record = persist_proof_record(
        feature=feature,
        feature_proof_root=proof_root_for_main_root(main_root),
        report_id=report.report_id,
        stage=stage,
        bundle=bundle,
        checks=checks,
        context_root=context_root or main_root,
    )
    key = proof_key(report.report_id, stage)
    await runner.artifacts.put(key, record.model_dump_json(), feature=feature)
    report.latest_proof_key = key
    report.ui_involved = report.ui_involved or bundle.ui_involved
    report.evidence_modes = _merge_evidence_modes(
        report.evidence_modes,
        bundle.evidence_modes,
        ui_involved=report.ui_involved or bundle.ui_involved,
    )
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)
    return record


async def _record_terminal_proof(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    *,
    source_record: BugflowProofRecord | None = None,
    summary: str,
    bundle: EvidenceBundle | None = None,
) -> BugflowProofRecord | None:
    terminal_key = proof_key(report.report_id, "terminal")
    if source_record is not None:
        main_root = _get_feature_root(runner, feature)
        if not main_root:
            return None
        record = snapshot_proof_record(
            feature=feature,
            feature_proof_root=proof_root_for_main_root(main_root),
            source=source_record,
            stage="terminal",
        )
    else:
        fallback_bundle = bundle or EvidenceBundle(
            ui_involved=report.ui_involved,
            evidence_modes=_requested_terminal_evidence_for_report(report),
            summary=summary,
            steps_executed=[],
            environment_notes="Fallback terminal proof generated by bugflow.",
            artifacts=[],
        )
        main_root = _get_feature_root(runner, feature)
        if not main_root:
            return None
        record = persist_proof_record(
            feature=feature,
            feature_proof_root=proof_root_for_main_root(main_root),
            report_id=report.report_id,
            stage="terminal",
            bundle=fallback_bundle,
            context_root=main_root,
        )

    record = record.model_copy(update={"created_at": utc_now()})
    await runner.artifacts.put(terminal_key, record.model_dump_json(), feature=feature)
    report.terminal_proof_key = terminal_key
    report.terminal_proof_summary = record.bundle.summary or summary
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)
    return record


async def _load_cluster(
    runner: WorkflowRunner,
    feature: Feature,
    cluster_id: str,
) -> BugflowClusterSnapshot | None:
    raw = await runner.artifacts.get(cluster_key(cluster_id), feature=feature)
    cluster = parse_model(raw, BugflowClusterSnapshot)
    if not isinstance(cluster, BugflowClusterSnapshot):
        return None
    return cluster


async def _load_clusters(
    runner: WorkflowRunner,
    feature: Feature,
    queue: BugflowQueueSnapshot,
    reports: list[BugflowReportSnapshot],
    lanes: list[BugflowLaneSnapshot],
) -> list[BugflowClusterSnapshot]:
    cluster_ids = set(queue.cluster_ids)
    cluster_ids.update(report.cluster_id for report in reports if report.cluster_id)
    cluster_ids.update(lane.source_cluster_id for lane in lanes if lane.source_cluster_id)
    clusters: list[BugflowClusterSnapshot] = []
    for cluster_id in sorted(cluster_ids):
        cluster = await _load_cluster(runner, feature, cluster_id)
        if cluster:
            clusters.append(cluster)
    clusters.sort(key=lambda cluster: cluster.updated_at or "", reverse=True)
    return clusters


async def _save_cluster(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot,
) -> None:
    cluster.updated_at = utc_now()
    await runner.artifacts.put(cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)


async def _load_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane_id: str,
) -> BugflowLaneSnapshot | None:
    raw = await runner.artifacts.get(lane_key(lane_id), feature=feature)
    lane = parse_model(raw, BugflowLaneSnapshot)
    if not isinstance(lane, BugflowLaneSnapshot):
        return None
    return lane


async def _load_lanes(
    runner: WorkflowRunner,
    feature: Feature,
) -> list[BugflowLaneSnapshot]:
    queue = await _load_queue(runner, feature)
    lane_ids = set(queue.lane_ids)
    reports = await _load_reports(runner, feature)
    lane_ids.update(report.lane_id for report in reports if report.lane_id)
    lanes: list[BugflowLaneSnapshot] = []
    for lane_id in sorted(lane_ids):
        lane = await _load_lane(runner, feature, lane_id)
        if lane:
            lanes.append(lane)
    lanes.sort(key=lambda lane: (lane.updated_at or "", lane.lane_id), reverse=True)
    return lanes


async def _save_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> None:
    lane.updated_at = utc_now()
    await runner.artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)


async def _load_promotion_queue(
    runner: WorkflowRunner,
    feature: Feature,
) -> BugflowPromotionQueueSnapshot:
    queue = parse_model(
        await runner.artifacts.get(_PROMOTION_ARTIFACT, feature=feature),
        BugflowPromotionQueueSnapshot,
        default=BugflowPromotionQueueSnapshot(),
    )
    assert isinstance(queue, BugflowPromotionQueueSnapshot)
    return queue


async def _save_promotion_queue(
    runner: WorkflowRunner,
    feature: Feature,
    queue: BugflowPromotionQueueSnapshot,
) -> None:
    queue.updated_at = utc_now()
    await runner.artifacts.put(_PROMOTION_ARTIFACT, queue.model_dump_json(), feature=feature)


async def _refresh_promotion_queue(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    promoting_lane_id: str = "",
    lock_owner: str = "",
    status_text: str = "Promotion idle",
) -> None:
    lanes = await _load_lanes(runner, feature)
    pending_lane_ids = [
        lane.lane_id
        for lane in lanes
        if lane.status == "verified_pending_promotion" and lane.lane_id != promoting_lane_id
    ]
    await _save_promotion_queue(
        runner,
        feature,
        BugflowPromotionQueueSnapshot(
            promoting_lane_id=promoting_lane_id,
            pending_lane_ids=pending_lane_ids,
            lock_owner=lock_owner,
            status_text=status_text,
        ),
    )


def _respawn_intent_artifact_key(lane_id: str) -> str:
    return f"bugflow-respawn-intent:{lane_id}"


async def _load_respawn_intent(
    runner: WorkflowRunner,
    feature: Feature,
    lane_id: str,
) -> dict[str, Any] | None:
    raw = await runner.artifacts.get(_respawn_intent_artifact_key(lane_id), feature=feature)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


async def _save_respawn_intent(
    runner: WorkflowRunner,
    feature: Feature,
    lane_id: str,
    payload: dict[str, Any],
) -> None:
    await runner.artifacts.put(
        _respawn_intent_artifact_key(lane_id),
        json.dumps(payload),
        feature=feature,
    )


async def _load_failure_bundle_payload(
    runner: WorkflowRunner,
    feature: Feature,
    artifact_key: str,
) -> dict[str, Any] | None:
    raw = await runner.artifacts.get(artifact_key, feature=feature)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


async def _normalize_cluster_strategy_state(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot,
) -> tuple[BugflowClusterSnapshot, bool]:
    current = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
    if _strategy_checkpoint_kind(current) != "invalid":
        return current, False
    logger.debug(
        "Normalizing invalid strategy state for cluster %s (status=%s, decision=%s, round=%s, bundle=%s)",
        current.cluster_id,
        current.strategy_status,
        current.strategy_decision_key,
        current.strategy_round,
        current.stable_bundle_key,
    )
    current = _clear_cluster_strategy_fields(current)
    await _save_cluster(runner, feature, current)
    reports_by_id = await _load_reports_by_id(runner, feature, current.report_ids)
    for report in reports_by_id.values():
        keep_bundle = False
        bundle_key = (report.latest_failure_bundle_key or "").strip()
        if bundle_key:
            keep_bundle = await _load_failure_bundle_payload(runner, feature, bundle_key) is not None
        report = _clear_report_strategy_fields(
            report,
            keep_failure_bundle_key=keep_bundle,
        )
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
    return current, True


async def _normalize_cluster_strategy_states(
    runner: WorkflowRunner,
    feature: Feature,
    clusters: list[BugflowClusterSnapshot],
) -> tuple[list[BugflowClusterSnapshot], bool]:
    normalized: list[BugflowClusterSnapshot] = []
    changed = False
    for cluster in clusters:
        current, was_changed = await _normalize_cluster_strategy_state(
            runner,
            feature,
            cluster,
        )
        normalized.append(current)
        changed = changed or was_changed
    return normalized, changed


async def _set_lane_execution_state(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    *,
    state: str,
    nonce: str | None = None,
    expected_nonce: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    failure_kind: str = "",
    failure_reason: str = "",
    wait_reason: str | None = None,
    current_phase: str | None = None,
) -> BugflowLaneSnapshot | None:
    current = await _load_lane(runner, feature, lane.lane_id)
    if current is None:
        return None
    if expected_nonce is not None and current.execution_nonce != expected_nonce:
        return None
    if nonce is not None:
        current.execution_nonce = nonce
    if kind is not None:
        current.execution_kind = kind
    if owner is not None:
        current.execution_owner = owner
    if state in _EXECUTION_STATES:
        current.execution_state = state
    elif not state:
        current.execution_state = ""
    now = utc_now()
    if current.execution_started_at == "" or nonce is not None:
        current.execution_started_at = now
    current.last_progress_at = now
    current.execution_failure_kind = failure_kind
    current.execution_failure_reason = failure_reason
    if wait_reason is not None:
        current.wait_reason = wait_reason
    if current_phase is not None:
        current.current_phase = current_phase
    await _save_lane(runner, feature, current)
    return current


async def _touch_lane_execution(
    runner: WorkflowRunner,
    feature: Feature,
    lane_id: str,
    *,
    nonce: str | None = None,
    progress_at: str | None = None,
    current_phase: str | None = None,
    wait_reason: str | None = None,
) -> BugflowLaneSnapshot | None:
    lane = await _load_lane(runner, feature, lane_id)
    if lane is None:
        return None
    if nonce is not None and lane.execution_nonce != nonce:
        return None
    lane.last_progress_at = progress_at or utc_now()
    if current_phase is not None:
        lane.current_phase = current_phase
    if wait_reason is not None:
        lane.wait_reason = wait_reason
    await _save_lane(runner, feature, lane)
    return lane


async def _clear_lane_execution(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    *,
    expected_nonce: str | None = None,
) -> BugflowLaneSnapshot | None:
    current = await _load_lane(runner, feature, lane.lane_id)
    if current is None:
        return None
    if expected_nonce is not None and current.execution_nonce != expected_nonce:
        return None
    current.execution_state = ""
    current.execution_nonce = ""
    current.execution_kind = ""
    current.execution_owner = ""
    current.execution_started_at = ""
    current.last_progress_at = ""
    current.execution_failure_kind = ""
    current.execution_failure_reason = ""
    await _save_lane(runner, feature, current)
    return current


async def _set_cluster_strategy_status(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot | None,
    *,
    status: str,
) -> BugflowClusterSnapshot | None:
    if cluster is None:
        return None
    current = await _load_cluster(runner, feature, cluster.cluster_id)
    if current is None:
        return None
    normalized_status = status if status in _STRATEGY_STATUSES else ""
    candidate = current.model_copy(deep=True)
    candidate.strategy_status = normalized_status
    if normalized_status and _strategy_checkpoint_kind(candidate) == "invalid":
        logger.debug(
            "Refusing to persist invalid strategy status %s for cluster %s",
            normalized_status,
            current.cluster_id,
        )
        current.strategy_status = ""
        await _save_cluster(runner, feature, current)
        return current
    now = utc_now()
    current.strategy_status = normalized_status
    if status == "pending":
        current.strategy_started_at = now
    elif status == "decided":
        current.strategy_decided_at = now
    elif status == "applied":
        current.strategy_applied_at = now
    await _save_cluster(runner, feature, current)
    return current


async def _load_strategy_decision_by_key(
    runner: WorkflowRunner,
    feature: Feature,
    decision_key: str,
) -> RepairStrategyDecision | None:
    if not decision_key:
        return None
    raw = await runner.artifacts.get(decision_key, feature=feature)
    decision = parse_model(raw, RepairStrategyDecision)
    if not isinstance(decision, RepairStrategyDecision):
        return None
    return decision


def _strategy_checkpoint_kind(cluster: BugflowClusterSnapshot | None) -> str:
    if cluster is None:
        return "none"
    status = (cluster.strategy_status or "").strip().lower()
    if not status:
        return "none"
    if status not in _STRATEGY_STATUSES:
        return "invalid"
    has_lane = bool((cluster.lane_id or "").strip())
    has_bundle = bool((cluster.stable_bundle_key or "").strip())
    has_decision = bool((cluster.strategy_decision_key or "").strip())
    has_round = int(cluster.strategy_round or 0) >= 1
    if status == "pending":
        return "pending" if has_bundle and has_lane else "invalid"
    if status == "decided":
        return "decided" if has_decision and has_round and has_lane else "invalid"
    if status == "applied":
        return "applied" if has_decision and has_round else "invalid"
    return "invalid"


def _clear_cluster_strategy_fields(
    cluster: BugflowClusterSnapshot,
) -> BugflowClusterSnapshot:
    cluster.strategy_mode = ""
    cluster.strategy_decision_key = ""
    cluster.stable_bundle_key = ""
    cluster.stable_failure_family = ""
    cluster.strategy_round = 0
    cluster.strategy_reason = ""
    cluster.similar_cluster_ids = []
    cluster.strategy_status = ""
    cluster.strategy_started_at = ""
    cluster.strategy_decided_at = ""
    cluster.strategy_applied_at = ""
    return cluster


def _clear_report_strategy_fields(
    report: BugflowReportSnapshot,
    *,
    keep_failure_bundle_key: bool,
    clear_proof_contract: bool = True,
) -> BugflowReportSnapshot:
    report.strategy_mode = ""
    report.strategy_decision_key = ""
    report.strategy_reason = ""
    report.strategy_round = 0
    report.stable_failure_family = ""
    if not keep_failure_bundle_key:
        report.latest_failure_bundle_key = ""
    report.latest_strategy_notice_key = ""
    if clear_proof_contract:
        report.strategy_required_evidence_modes = []
    return report


def _proof_capture_retry_in_flight(
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
) -> bool:
    return lane.promotion_status == "proof-capture-retry" or any(
        report.promotion_status == "proof-capture-retry"
        for report in reports
    )


async def _set_promotion_execution_state(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    state: str,
    nonce: str | None = None,
    expected_nonce: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    failure_kind: str = "",
    failure_reason: str = "",
    promoting_lane_id: str | None = None,
    status_text: str | None = None,
) -> BugflowPromotionQueueSnapshot:
    queue = await _load_promotion_queue(runner, feature)
    if expected_nonce is not None and queue.execution_nonce != expected_nonce:
        return queue
    now = utc_now()
    if nonce is not None:
        queue.execution_nonce = nonce
    if kind is not None:
        queue.execution_kind = kind
    if owner is not None:
        queue.execution_owner = owner
    queue.execution_state = state if state in _EXECUTION_STATES else ""
    if queue.execution_started_at == "" or nonce is not None:
        queue.execution_started_at = now
    queue.last_progress_at = now
    queue.execution_failure_kind = failure_kind
    queue.execution_failure_reason = failure_reason
    if promoting_lane_id is not None:
        queue.promoting_lane_id = promoting_lane_id
    if status_text is not None:
        queue.status_text = status_text
    await _save_promotion_queue(runner, feature, queue)
    return queue


async def _clear_promotion_execution(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    expected_nonce: str | None = None,
    status_text: str | None = None,
) -> BugflowPromotionQueueSnapshot:
    queue = await _load_promotion_queue(runner, feature)
    if expected_nonce is not None and queue.execution_nonce != expected_nonce:
        return queue
    queue.execution_state = ""
    queue.execution_nonce = ""
    queue.execution_kind = ""
    queue.execution_owner = ""
    queue.execution_started_at = ""
    queue.last_progress_at = ""
    queue.execution_failure_kind = ""
    queue.execution_failure_reason = ""
    if status_text is not None:
        queue.status_text = status_text
    await _save_promotion_queue(runner, feature, queue)
    return queue


async def _touch_promotion_execution(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    nonce: str | None = None,
    progress_at: str | None = None,
) -> BugflowPromotionQueueSnapshot | None:
    queue = await _load_promotion_queue(runner, feature)
    if nonce is not None and queue.execution_nonce != nonce:
        return None
    queue.last_progress_at = progress_at or utc_now()
    await _save_promotion_queue(runner, feature, queue)
    return queue


def _classify_execution_failure(
    *,
    failure_kind: str,
    terminal: bool = False,
    counted: bool = False,
) -> str:
    normalized = (failure_kind or "").strip().lower()
    if terminal:
        return "terminal"
    if counted or normalized in _COUNTED_FAILURE_KINDS:
        return "retryable_counted"
    if normalized in _RECOVERABLE_FAILURE_KINDS or normalized == "infrastructure":
        return "recoverable"
    return "terminal"


async def _load_cluster_from_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> BugflowClusterSnapshot | None:
    if not lane.source_cluster_id:
        return None
    return await _load_cluster(runner, feature, lane.source_cluster_id)


async def _append_decision(
    runner: WorkflowRunner,
    feature: Feature,
    decision: BugflowDecisionRecord,
) -> None:
    async with runner.feature_store.advisory_lock(feature.id, "bugflow-decisions"):
        raw = await runner.artifacts.get("bugflow-decisions", feature=feature)
        decisions: list[dict[str, Any]] = []
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    decisions = [item for item in parsed if isinstance(item, dict)]
                elif isinstance(parsed, dict):
                    decisions = [
                        item for item in parsed.get("decisions", []) if isinstance(item, dict)
                    ]
            except Exception:
                decisions = []
        if not any(str(item.get("decision_id", "")) == decision.decision_id for item in decisions):
            decisions.append(decision.model_dump(mode="json"))
        await runner.artifacts.put("bugflow-decisions", json.dumps(decisions), feature=feature)


def _attempt_identity(attempt: BugFixAttempt) -> tuple[str, str, str, int]:
    return (
        attempt.bug_id,
        attempt.group_id,
        attempt.source_verdict,
        attempt.attempt_number,
    )


async def _append_bug_fix_attempts(
    runner: WorkflowRunner,
    feature: Feature,
    attempts: list[BugFixAttempt],
) -> None:
    async with runner.feature_store.advisory_lock(feature.id, "bug-fix-attempts"):
        existing = _load_prior_attempts(
            await runner.artifacts.get("bug-fix-attempts", feature=feature)
        )
        seen = {_attempt_identity(attempt) for attempt in existing}
        merged = list(existing)
        for attempt in attempts:
            identity = _attempt_identity(attempt)
            if identity in seen:
                continue
            merged.append(attempt)
            seen.add(identity)
        await _store_attempts(runner, feature, merged)


async def _source_context_text(runner: WorkflowRunner, feature: Feature) -> str:
    return str(await runner.artifacts.get("bugflow-source-context", feature=feature) or "")


def _classification_prompt(report: BugflowReportSnapshot) -> str:
    return (
        "Classify this user report into one of: bug, clarification, requirement, missing_test.\n\n"
        f"Title: {report.title}\n"
        f"Original report: {report.root_message_text}\n"
        f"Captured summary: {report.summary}\n"
        f"Expected behavior: {report.expected_behavior}\n"
        f"Actual behavior: {report.actual_behavior}\n"
        f"Affected area: {report.affected_area}\n\n"
        "Return a single Observation. Use the report ID as Observation.id.\n"
        "Set Observation.ui_involved=true when a browser-rendered surface, visual state, navigation, click/fill, or drag/drop behavior is part of the truth.\n"
        "Set Observation.evidence_modes using any of: ui, api, database, logs, repo.\n"
        "Do not replace the raw user report with code theory; preserve the user's description of the bug."
    )


def _reproduction_prompt(report: BugflowReportSnapshot) -> str:
    requested_directives = _requested_terminal_evidence_text(report)
    required_modes = _required_terminal_core_surfaces_text(report)
    return (
        "Validate whether this bug still reproduces on the current bugflow branch.\n\n"
        f"Report: {report.report_id}\n"
        f"Title: {report.title}\n"
        f"Original report: {report.root_message_text}\n"
        f"Captured summary: {report.summary}\n"
        f"Interview context: {report.interview_output or 'No extra interview notes recorded.'}\n"
        f"Expected: {report.expected_behavior}\n"
        f"Actual: {report.actual_behavior}\n"
        f"Requested evidence directives: {requested_directives}\n"
        f"Required core proof surfaces: {required_modes}\n\n"
        "Use available repo, browser, API, database, and verification tools as needed.\n"
        "If UI is involved, capture explicit Playwright proof with a trace artifact and screenshot artifact.\n"
        "For backend evidence, include request/response, database, logs, or repo evidence as needed, and include an independent postcondition check for state-changing flows.\n"
        "Return ReproductionResult.proof with concrete artifact metadata instead of prose-only claims.\n"
        "For each requested evidence directive that is not one of ui/api/database/logs/repo, include a ReproductionResult.check entry "
        "with criterion `evidence:<directive>`, result `satisfied` or `not-needed`, and detail naming the artifact or rationale."
    )


def _validation_prompt(report: BugflowReportSnapshot, reproduction: ReproductionResult) -> str:
    requested_directives = _requested_terminal_evidence_text(report, reproduction.proof)
    required_modes = _required_terminal_core_surfaces_text(report, reproduction.proof)
    return (
        "Review whether this reproduced report still represents a bug that needs fixing.\n\n"
        f"Report: {report.report_id}\n"
        f"Title: {report.title}\n"
        f"Original report: {report.root_message_text}\n"
        f"Captured summary: {report.summary}\n"
        f"Expected: {report.expected_behavior}\n"
        f"Actual: {report.actual_behavior}\n\n"
        f"Reproduction summary:\n{to_str(reproduction)}\n\n"
        f"Requested evidence directives: {requested_directives}\n"
        f"Required core proof surfaces: {required_modes}\n\n"
        "Return a blocking Verdict when this needs a fix. Return approved=true only if no change is needed.\n"
        "Any terminal approval must include Verdict.proof. For UI-involved reports, that proof must include Playwright trace and screenshot artifacts.\n"
        "For backend/stateful reports, include request/response, database, logs, or repo/static evidence as needed, plus an independent postcondition check for write flows.\n"
        "For each requested evidence directive that is not one of ui/api/database/logs/repo, include a Verdict.check entry with criterion `evidence:<directive>`, result `satisfied` or `not-needed`, and detail naming the artifact or rationale."
    )


def _build_synthetic_bug_verdict(reports: list[BugflowReportSnapshot]) -> Verdict:
    from ....models.outputs import Issue

    concerns = [
        Issue(
            severity=_normalize_severity(report.severity),
            description=f"[{report.report_id}] {report.title or report.summary or report.root_message_text}",
            file="",
            line=0,
        )
        for report in reports
    ]
    return Verdict(
        approved=False,
        summary=f"{len(reports)} queued bugflow reports require RCA and fixing.",
        concerns=concerns,
        suggestions=[],
        checks=[],
        gaps=[],
    )


def _merge_evidence_modes(
    *mode_sets: list[str] | tuple[str, ...],
    ui_involved: bool = False,
) -> list[str]:
    merged: list[str] = []
    if ui_involved:
        merged.append("ui")
    for modes in mode_sets:
        for mode in modes:
            value = str(mode or "").strip().lower()
            if not value or value in merged:
                continue
            merged.append(value)
    return merged


def _classify_surface_flags(
    report: BugflowReportSnapshot,
    classification: Observation | None = None,
) -> tuple[bool, list[str]]:
    ui_involved, inferred_modes, classifier_modes = _surface_inference_inputs(
        report,
        classification,
    )
    return ui_involved, _merge_evidence_modes(
        report.evidence_modes,
        classifier_modes,
        inferred_modes,
        ui_involved=ui_involved,
    )


def _surface_inference_inputs(
    report: BugflowReportSnapshot,
    classification: Observation | None = None,
) -> tuple[bool, list[str], list[str]]:
    fields = [
        report.root_message_text,
        report.title,
        report.summary,
        report.interview_output,
        report.expected_behavior,
        report.actual_behavior,
        report.affected_area,
        classification.description if classification else "",
        classification.affected_area if classification else "",
        " ".join(classification.steps_to_reproduce) if classification else "",
    ]
    corpus = " ".join(str(field or "") for field in fields).lower()
    ui_keywords = {
        "button", "click", "drag", "drop", "canvas", "page", "screen", "modal", "dialog",
        "form", "input", "browser", "frontend", "react", "ui", "layout", "render", "nav",
    }
    api_keywords = {
        "api", "endpoint", "request", "response", "http", "graphql", "mutation", "query", "500", "404",
    }
    database_keywords = {
        "database", "db", "sql", "query", "table", "record", "row", "persist", "saved", "stored",
    }
    log_keywords = {
        "logs", "worker", "job", "queue", "cron", "stderr", "stdout", "deploy", "server", "timeout",
    }
    repo_keywords = {
        "config", "build", "compile", "migration", "schema", "manifest", "env", "dependency",
    }

    ui_involved = report.ui_involved or bool(
        getattr(classification, "ui_involved", False)
        or any(keyword in corpus for keyword in ui_keywords)
    )
    inferred_modes: list[str] = []
    if ui_involved:
        inferred_modes.append("ui")
    if any(keyword in corpus for keyword in api_keywords):
        inferred_modes.append("api")
    if any(keyword in corpus for keyword in database_keywords):
        inferred_modes.append("database")
    if any(keyword in corpus for keyword in log_keywords):
        inferred_modes.append("logs")
    if any(keyword in corpus for keyword in repo_keywords):
        inferred_modes.append("repo")

    classifier_modes = (
        list(classification.evidence_modes)
        if classification and getattr(classification, "evidence_modes", None)
        else []
    )
    return ui_involved, inferred_modes, classifier_modes


def _observed_modes_for_report(report: BugflowReportSnapshot) -> list[str]:
    return required_evidence_modes(
        ui_involved=report.ui_involved,
        evidence_modes=report.evidence_modes,
    )


def _requested_terminal_evidence_for_report(
    report: BugflowReportSnapshot,
) -> list[str]:
    explicit_modes = normalize_evidence_directives(
        report.strategy_required_evidence_modes,
        ui_involved=report.ui_involved,
    )
    if explicit_modes:
        return explicit_modes

    ui_involved, inferred_modes, classifier_modes = _surface_inference_inputs(report)
    fallback_modes = normalize_evidence_directives(
        classifier_modes,
        inferred_modes,
        ui_involved=ui_involved,
    )
    if _report_looks_state_changing(report):
        fallback_modes = normalize_evidence_directives(
            fallback_modes,
            ["api", "database"],
            ui_involved=ui_involved,
        )
    return fallback_modes


def _required_terminal_core_surfaces_for_report(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None = None,
) -> list[str]:
    del bundle
    return required_evidence_modes(
        ui_involved=report.ui_involved,
        evidence_modes=core_surfaces_for_directives(
            _requested_terminal_evidence_for_report(report),
            ui_involved=report.ui_involved,
        ),
    )


def _requested_terminal_evidence_text(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None = None,
) -> str:
    del bundle
    return ", ".join(_requested_terminal_evidence_for_report(report)) or "none recorded"


def _required_terminal_core_surfaces_text(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None = None,
) -> str:
    return ", ".join(_required_terminal_core_surfaces_for_report(report, bundle)) or "none recorded"


def _proof_context_root(
    runner: WorkflowRunner,
    feature: Feature,
) -> Path | None:
    try:
        return _get_feature_root(runner, feature)
    except Exception:
        return None


def _report_looks_state_changing(report: BugflowReportSnapshot) -> bool:
    corpus = " ".join(
        [
            report.root_message_text,
            report.title,
            report.summary,
            report.expected_behavior,
            report.actual_behavior,
            report.interview_output,
        ]
    ).lower()
    write_words = {
        "save", "saved", "submit", "submitted", "create", "created", "update", "updated",
        "delete", "deleted", "persist", "stored", "send", "sent", "post", "patch",
    }
    return any(word in corpus for word in write_words)


def _requested_non_core_evidence_directives(
    report: BugflowReportSnapshot,
) -> list[str]:
    return [
        directive
        for directive in _requested_terminal_evidence_for_report(report)
        if directive not in {"ui", "api", "database", "logs", "repo"}
    ]


def _evidence_check_lookup_from_checks(checks: list[Check] | None) -> dict[str, Check]:
    if not checks:
        return {}
    lookup: dict[str, Check] = {}
    for check in checks:
        criterion = str(check.criterion or "").strip().lower()
        if not criterion.startswith("evidence:"):
            continue
        directive = normalize_evidence_directives([criterion.split(":", 1)[1]])
        if not directive:
            continue
        lookup[directive[0]] = check
    return lookup


def _evidence_check_lookup_from_result(
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None,
) -> dict[str, Check]:
    if approval_source is None:
        return {}
    return _evidence_check_lookup_from_checks(list(getattr(approval_source, "checks", []) or []))


def _missing_non_core_evidence_directives(
    report: BugflowReportSnapshot,
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None,
) -> list[str]:
    lookup = _evidence_check_lookup_from_result(approval_source)
    missing: list[str] = []
    for directive in _requested_non_core_evidence_directives(report):
        check = lookup.get(directive)
        if check is None:
            missing.append(directive)
            continue
        result = normalize_evidence_directives([check.result or ""])
        if not result or result[0] not in _EVIDENCE_CHECK_OK_RESULTS:
            missing.append(directive)
    return missing


def _missing_terminal_approval_requirements(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None,
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None = None,
) -> list[str]:
    missing = list(_missing_terminal_proof_requirements(report, bundle))
    for directive in _missing_non_core_evidence_directives(report, approval_source):
        missing.append(f"agent validation for evidence:{directive}")
    return missing


def _non_core_evidence_check_summaries(
    report: BugflowReportSnapshot,
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None,
) -> list[str]:
    lookup = _evidence_check_lookup_from_result(approval_source)
    summaries: list[str] = []
    for directive in _requested_non_core_evidence_directives(report):
        check = lookup.get(directive)
        if check is None:
            continue
        result = normalize_evidence_directives([check.result or ""])
        status = result[0] if result else "unknown"
        detail = str(check.detail or "").strip()
        summaries.append(
            f"{directive}={status}{f' ({detail})' if detail else ''}"
        )
    return summaries


def _missing_terminal_proof_requirements(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None,
) -> list[str]:
    return evidence_missing_requirements(
        required_modes=_required_terminal_core_surfaces_for_report(report, bundle),
        bundle=bundle,
        require_ui_proof=report.ui_involved,
        state_change=(bundle.state_change if bundle is not None else False) or _report_looks_state_changing(report),
    )


def _proof_policy_diagnostics(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None,
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None = None,
) -> dict[str, object]:
    diagnostics = proof_requirement_diagnostics(
        required_modes=_required_terminal_core_surfaces_for_report(report, bundle),
        bundle=bundle,
        require_ui_proof=report.ui_involved,
        state_change=(bundle.state_change if bundle is not None else False) or _report_looks_state_changing(report),
    )
    diagnostics["requested_directives"] = _requested_terminal_evidence_for_report(report)
    diagnostics["missing_non_core_directives"] = _missing_non_core_evidence_directives(report, approval_source)
    diagnostics["non_core_check_summaries"] = _non_core_evidence_check_summaries(report, approval_source)
    return diagnostics


def _proof_policy_detail_text(
    report: BugflowReportSnapshot,
    bundle: EvidenceBundle | None,
    missing: list[str],
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None = None,
) -> str:
    diagnostics = _proof_policy_diagnostics(report, bundle, approval_source)
    requested_directives = diagnostics["requested_directives"]
    required_modes = diagnostics["required_modes"]
    provided_modes = diagnostics["provided_modes"]
    artifact_surfaces = diagnostics["artifact_surfaces"]
    declared_without = diagnostics["declared_without_artifacts"]
    missing_non_core = diagnostics["missing_non_core_directives"]
    non_core_summaries = diagnostics["non_core_check_summaries"]
    lines = [f"Missing evidence: {', '.join(missing)}."]
    lines.append(
        "Requested evidence directives: "
        + (", ".join(str(item) for item in requested_directives) or "none")
        + "."
    )
    lines.append(
        "Required core proof surfaces: "
        + (", ".join(str(item) for item in required_modes) or "none")
        + "."
    )
    lines.append(
        "Provided proof surfaces: "
        + (", ".join(str(item) for item in provided_modes) or "none")
        + "."
    )
    lines.append(
        "Artifact surfaces: "
        + (", ".join(str(item) for item in artifact_surfaces) or "none")
        + "."
    )
    if declared_without:
        lines.append(
            "Declared without matching artifacts: "
            + ", ".join(str(item) for item in declared_without)
            + "."
        )
    if missing_non_core:
        lines.append(
            "Non-core directives still needing explicit agent validation: "
            + ", ".join(str(item) for item in missing_non_core)
            + "."
        )
    if non_core_summaries:
        lines.append(
            "Non-core directive coverage: "
            + "; ".join(str(item) for item in non_core_summaries)
            + "."
        )
    return " ".join(lines)


async def _reject_missing_terminal_proof(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    *,
    stage: str,
    missing: list[str],
    bundle: EvidenceBundle | None = None,
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None = None,
) -> None:
    requirement_text = ", ".join(missing)
    detail_text = _proof_policy_detail_text(report, bundle, missing, approval_source)
    next_step = f"Retrying {report.report_id} with required {stage} proof"
    should_notify = report.current_step != next_step
    report.current_step = next_step
    report.validation_summary = f"Evidence bundle was missing required proof: {requirement_text}. {detail_text}"
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)
    if should_notify:
        await _post_thread_message(
            runner,
            feature,
            report.thread_ts,
            (
                f"{report.report_id}: I need stronger proof before I can close this.\n\n"
                f"Missing evidence: {requirement_text}.\n\n"
                f"{detail_text}\n\n"
                "I'll retry with explicit proof capture requirements."
            ),
        )


def _promotion_proof_retry_reason(
    reports: list[BugflowReportSnapshot],
    bundle: EvidenceBundle | None,
    missing_by_report_id: dict[str, list[str]],
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None,
) -> str:
    details = [
        f"{report.report_id}: {_proof_policy_detail_text(report, bundle, missing, approval_source)}"
        for report in reports
        if (missing := missing_by_report_id.get(report.report_id))
    ]
    return " ".join(details) or "Promotion verification was missing required proof."


async def _retry_missing_promotion_proof(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    *,
    bundle: EvidenceBundle | None,
    missing_by_report_id: dict[str, list[str]],
    approval_source: Verdict | ReproductionResult | BugflowProofRecord | None = None,
) -> bool:
    detail_text = _promotion_proof_retry_reason(reports, bundle, missing_by_report_id, approval_source)
    if lane.promotion_proof_capture_attempt >= _MAX_PROMOTION_PROOF_CAPTURE_RETRIES:
        lane.status = "blocked"
        lane.promotion_status = "blocked"
        lane.wait_reason = (
            "Promotion verification was missing required proof after "
            f"{lane.promotion_proof_capture_attempt}/{_MAX_PROMOTION_PROOF_CAPTURE_RETRIES} proof-capture attempts. "
            f"{detail_text}"
        )
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)
        await _mark_cluster_from_lane(
            runner,
            feature,
            lane,
            status="blocked",
            current_phase="blocked",
            wait_reason=lane.wait_reason,
        )
        await _mark_lane_reports_blocked(
            runner,
            feature,
            lane,
            (
                f"{lane.lane_id}: promotion verification was missing required proof after "
                f"{lane.promotion_proof_capture_attempt}/{_MAX_PROMOTION_PROOF_CAPTURE_RETRIES} proof-capture attempts.\n\n"
                f"{detail_text}"
            ),
            failure_kind="proof-policy",
            failure_reason=lane.wait_reason,
        )
        return False

    lane.promotion_proof_capture_attempt += 1
    lane.status = "verified_pending_promotion"
    lane.promotion_status = "proof-capture-retry"
    lane.wait_reason = (
        f"Retrying promotion proof capture for {lane.lane_id} "
        f"({lane.promotion_proof_capture_attempt}/{_MAX_PROMOTION_PROOF_CAPTURE_RETRIES}). "
        f"{detail_text}"
    )
    lane.updated_at = utc_now()
    await _save_lane(runner, feature, lane)
    await _mark_cluster_from_lane(
        runner,
        feature,
        lane,
        status="verified_pending_promotion",
        current_phase="promotion_pending",
        wait_reason=lane.wait_reason,
    )
    for report in reports:
        missing = missing_by_report_id.get(report.report_id)
        if missing:
            await _reject_missing_terminal_proof(
                runner,
                feature,
                report,
                stage="promotion verification",
                missing=missing,
                bundle=bundle,
                approval_source=approval_source,
            )
        refreshed = await _load_report(runner, feature, report.report_id) or report
        refreshed.status = "active_fix"
        refreshed.promotion_status = "proof-capture-retry"
        refreshed.last_failed_lane_id = lane.lane_id
        refreshed.last_failure_kind = "proof-policy"
        refreshed.last_failure_reason = detail_text
        refreshed.updated_at = utc_now()
        await _save_report(runner, feature, refreshed)
    return True


async def _post_terminal_notice(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    *,
    notice: str,
    proof_record: BugflowProofRecord | None,
) -> None:
    key = report.terminal_proof_key or report.latest_proof_key
    if key and report.terminal_notice_sent_for_key == key:
        return

    lines = [notice]
    if proof_record and proof_record.bundle_url:
        lines.extend(["", f"Proof bundle: {proof_record.bundle_url}"])
    if proof_record and proof_record.primary_artifact_url:
        lines.append(f"Key artifact: {proof_record.primary_artifact_url}")
    summary = ""
    if proof_record:
        summary = proof_record.bundle.summary or report.terminal_proof_summary
    if summary:
        lines.extend(["", f"Evidence summary: {summary}"])
    await _post_thread_message(
        runner,
        feature,
        report.thread_ts,
        "\n".join(lines),
    )
    report.terminal_notice_sent_for_key = key
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)


def _is_recoverable_proof_policy_block(
    report: BugflowReportSnapshot,
    lane: BugflowLaneSnapshot,
) -> bool:
    failure_kind = report.terminal_reason_kind or report.last_failure_kind
    if failure_kind != "proof-policy":
        return False
    return int(lane.promotion_proof_capture_attempt or 0) < _MAX_PROMOTION_PROOF_CAPTURE_RETRIES


async def _recover_blocked_promotion_proof_capture(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
) -> None:
    lane.promotion_proof_capture_attempt = max(1, int(lane.promotion_proof_capture_attempt or 0))
    lane.status = "verified_pending_promotion"
    lane.promotion_status = "proof-capture-retry"
    lane.wait_reason = (
        f"Recovered stale proof-policy block for {lane.lane_id}; "
        f"retrying promotion proof capture ({lane.promotion_proof_capture_attempt}/{_MAX_PROMOTION_PROOF_CAPTURE_RETRIES})."
    )
    lane.updated_at = utc_now()
    await _save_lane(runner, feature, lane)
    await _mark_cluster_from_lane(
        runner,
        feature,
        lane,
        status="verified_pending_promotion",
        current_phase="promotion_pending",
        wait_reason=lane.wait_reason,
    )
    await _refresh_promotion_queue(
        runner,
        feature,
        status_text=f"Recapturing promotion proof for {lane.lane_id}",
    )

    for report in reports:
        report.status = "active_fix"
        report.promotion_status = "proof-capture-retry"
        report.last_failed_lane_id = lane.lane_id
        report.last_failure_kind = "proof-policy"
        report.last_failure_reason = (
            report.last_failure_reason
            or report.terminal_reason_summary
            or lane.wait_reason
        )
        report.terminal_reason_kind = ""
        report.terminal_reason_summary = ""
        report.current_step = f"Retrying promotion proof capture for {report.report_id}"
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)


def _lane_failure_message(lane: BugflowLaneSnapshot) -> str:
    detail = (
        lane.wait_reason
        or lane.latest_regression_summary
        or lane.latest_verify_summary
        or lane.latest_fix_summary
        or "Lane execution ended without a promotable result."
    )
    return (
        f"{lane.lane_id}: the isolated lane could not reach a promotable result.\n\n"
        f"Reason: {detail}\n\n"
        "The lane is blocked and needs manual attention before more work continues here."
    )


async def _lane_failure_reason(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> str:
    for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
        if not report.latest_proof_key:
            continue
        proof_record = await _load_proof_record(runner, feature, report.latest_proof_key)
        if proof_record and proof_record.bundle.summary:
            return proof_record.bundle.summary
    return (
        lane.wait_reason
        or lane.latest_regression_summary
        or lane.latest_verify_summary
        or lane.latest_fix_summary
        or "Lane execution ended without a promotable result."
    )


async def _block_report_with_notice(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    *,
    current_step: str,
    summary: str,
    notice: str,
    terminal_reason_kind: str = "blocked",
) -> None:
    report.status = "blocked"
    report.current_step = current_step
    report.summary = summary
    report.thread_status = "ready"
    report.terminal_reason_kind = terminal_reason_kind
    report.terminal_reason_summary = summary
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)
    source_record = None
    if report.latest_proof_key:
        source_record = await _load_proof_record(runner, feature, report.latest_proof_key)
    terminal_record = await _record_terminal_proof(
        runner,
        feature,
        report,
        source_record=source_record,
        summary=summary,
    )
    await _post_terminal_notice(
        runner,
        feature,
        report,
        notice=notice,
        proof_record=terminal_record,
    )


def _attempt_proof_bundle(
    report: BugflowReportSnapshot,
    attempt: BugFixAttempt,
    *,
    summary: str,
    stage: str,
) -> EvidenceBundle:
    artifacts: list[EvidenceArtifact] = []
    if attempt.files_modified:
        artifacts.append(
            EvidenceArtifact(
                kind="repo",
                label=f"{stage}-files",
                role="supporting",
                source="repo",
                excerpt="\n".join(attempt.files_modified),
            )
        )
    artifacts.append(
        EvidenceArtifact(
            kind="logs",
            label=f"{stage}-result",
            role="response",
            source="other",
            excerpt=(
                f"Re-verify result: {attempt.re_verify_result}\n\n"
                f"Root cause: {attempt.root_cause}\n\n"
                f"Fix applied: {attempt.fix_applied}"
            ).strip(),
        )
    )
    return EvidenceBundle(
        ui_involved=report.ui_involved,
        evidence_modes=_requested_terminal_evidence_for_report(report),
        summary=summary,
        steps_executed=[f"Retry attempt {attempt.attempt_number} during {stage}"],
        environment_notes="Fallback proof generated from retry attempt metadata.",
        state_change=_report_looks_state_changing(report),
        artifacts=artifacts,
    )


def _attempt_verdict(
    report: BugflowReportSnapshot,
    attempt: BugFixAttempt,
    *,
    summary: str,
    stage: str,
) -> Verdict:
    file_hint = attempt.files_modified[0] if attempt.files_modified else ""
    passed = str(attempt.re_verify_result).upper() == "PASS"
    concerns = [] if passed else [
        Issue(
            severity=report.severity or "major",
            description=attempt.description or attempt.root_cause or summary,
            file=file_hint,
            line=0,
        )
    ]
    checks = [
        Check(
            criterion=f"{stage} verification",
            result="PASS" if passed else "FAIL",
            detail=attempt.fix_applied or attempt.root_cause or summary,
        )
    ]
    return Verdict(
        approved=passed,
        summary=summary,
        concerns=concerns,
        suggestions=[],
        checks=checks,
        gaps=[],
        proof=_attempt_proof_bundle(report, attempt, summary=summary, stage=stage),
    )


async def _store_attempt_verdict_artifact(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    attempt: BugFixAttempt,
    *,
    key: str,
    summary: str,
    stage: str,
) -> str:
    verdict = _attempt_verdict(report, attempt, summary=summary, stage=stage)
    await runner.artifacts.put(key, verdict.model_dump_json(), feature=feature)
    return key


def _normalize_category(category: str | None) -> str:
    value = (category or "").strip().lower()
    if value in {"clarification", "requirement", "missing_test", "bug"}:
        return value
    return "bug"


def _normalize_severity(severity: str | None) -> str:
    value = (severity or "").strip().lower()
    if value in {"blocker", "major", "minor", "nit"}:
        return value
    return "major"


def _derive_title(text: str) -> str:
    if not text:
        return "Untitled bug report"
    trimmed = text.strip()
    if len(trimmed) <= 80:
        return trimmed
    return trimmed[:77].rstrip() + "..."


def _derive_lock_scope(affected_files: list[str]) -> tuple[list[str], list[str]]:
    if not affected_files:
        return (["global:unknown"], [])
    repo_tokens: set[str] = set()
    file_tokens: set[str] = set()
    repo_paths: set[str] = set()
    for file_path in affected_files:
        normalized = str(Path(file_path))
        parts = Path(normalized).parts
        repo = parts[0] if parts else "__workspace__"
        repo_paths.add(repo)
        if _requires_repo_lock(normalized):
            repo_tokens.add(f"repo:{repo}")
        else:
            file_tokens.add(f"file:{normalized}")
    if repo_tokens:
        file_tokens = {token for token in file_tokens if f"repo:{token.split(':', 1)[1].split('/', 1)[0]}" not in repo_tokens}
    tokens = sorted(repo_tokens | file_tokens)
    return (tokens or ["global:unknown"], sorted(repo_paths))


def _requires_repo_lock(file_path: str) -> bool:
    path = Path(file_path)
    if len(path.parts) <= 2:
        return True
    broad_names = {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "pyproject.toml",
        "tsconfig.json",
        "vite.config.ts",
        "vite.config.js",
        "next.config.js",
        "next.config.mjs",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
    if path.name in broad_names:
        return True
    lowered_parts = {part.lower() for part in path.parts}
    return bool({"migrations", "schema", "schemas", "contracts"} & lowered_parts)


def _lock_scopes_overlap(lhs: list[str], rhs: list[str]) -> bool:
    if not lhs or not rhs:
        return False
    if "global:unknown" in lhs or "global:unknown" in rhs:
        return True
    lhs_set = set(lhs)
    rhs_set = set(rhs)
    if lhs_set & rhs_set:
        return True
    lhs_repos = {token.split(":", 1)[1] for token in lhs if token.startswith("repo:")}
    rhs_repos = {token.split(":", 1)[1] for token in rhs if token.startswith("repo:")}
    lhs_files = {token.split(":", 1)[1] for token in lhs if token.startswith("file:")}
    rhs_files = {token.split(":", 1)[1] for token in rhs if token.startswith("file:")}
    if any(file_path.split("/", 1)[0] in rhs_repos for file_path in lhs_files):
        return True
    if any(file_path.split("/", 1)[0] in lhs_repos for file_path in rhs_files):
        return True
    return False


def _schedule_lookup(dispatch: PlannedBugDispatch) -> dict[str, tuple[int | None, int | None]]:
    lookup: dict[str, tuple[int | None, int | None]] = {}
    for round_index, group_ids in enumerate(dispatch.schedule, start=1):
        for group_id in group_ids:
            lookup[group_id] = (round_index, len(dispatch.schedule))
    return lookup


def _round_plan(schedule: list[list[str]]) -> list[str]:
    return [f"Round {index}: {', '.join(group_ids)}" for index, group_ids in enumerate(schedule, start=1)]


def _queue_status(
    reports: list[BugflowReportSnapshot],
    lanes: list[BugflowLaneSnapshot],
    clusters: list[BugflowClusterSnapshot],
) -> tuple[str, str, str]:
    stalled_lanes = [lane for lane in lanes if lane.execution_state == "stalled"]
    recovering_lanes = [lane for lane in lanes if lane.execution_state == "recovering"]
    proof_capture_retry_lanes = [
        lane for lane in lanes if lane.promotion_status == "proof-capture-retry"
    ]
    strategy_pending_clusters = [
        cluster for cluster in clusters if _strategy_checkpoint_kind(cluster) in {"pending", "decided"}
    ]
    if stalled_lanes:
        text = ", ".join(lane.lane_id for lane in stalled_lanes[:3])
        return (
            f"Recovering stalled lanes: {text}",
            f"Recovering stalled lanes: {text}",
            "degraded",
        )
    if recovering_lanes:
        text = ", ".join(lane.lane_id for lane in recovering_lanes[:3])
        return (
            f"Recovering lanes: {text}",
            f"Recovering lanes: {text}",
            "degraded",
        )
    if proof_capture_retry_lanes:
        text = ", ".join(lane.lane_id for lane in proof_capture_retry_lanes[:3])
        return (
            f"Recapturing promotion proof for lanes: {text}",
            f"Recapturing promotion proof for lanes: {text}",
            "degraded",
        )
    if strategy_pending_clusters:
        text = ", ".join(cluster.cluster_id for cluster in strategy_pending_clusters[:3])
        return (
            f"Strategy pending for clusters: {text}",
            f"Strategy pending for clusters: {text}",
            "degraded",
        )
    if any(lane.status == "blocked" for lane in lanes) or any(lane_for_status(report.status) == "blocked" for report in reports):
        return "Blocked lanes or reports need attention", "Blocked lanes or reports need attention", "blocked"
    promoting_lane = next((lane for lane in lanes if lane.status == "promoting"), None)
    if promoting_lane:
        text = f"Promoting {promoting_lane.lane_id}"
        return text, text, "running"

    active_lanes = [lane for lane in lanes if lane.status in {"active_fix", "active_verify"}]
    if active_lanes:
        text = ", ".join(
            f"{lane.lane_id} ({'verify' if lane.status == 'active_verify' else 'fix'})"
            for lane in active_lanes[:3]
        )
        return f"Active lanes: {text}", f"Active lanes: {text}", "running"

    waiting_promotion = [lane for lane in lanes if lane.status == "verified_pending_promotion"]
    if waiting_promotion:
        text = f"{len(waiting_promotion)} verified lanes waiting for promotion"
        return text, text, "running"

    for report in reports:
        if report.status == "awaiting_confirmation":
            text = f"Awaiting clarification approval for {report.report_id}"
            return text, text, "awaiting-user"
        if report.status == "validation_pending":
            text = f"Validating {report.report_id}"
            return text, text, "running"
        if report.status == "classification_pending":
            text = f"Classifying {report.report_id}"
            return text, text, "running"
        if report.status == "intake_pending":
            text = f"Interviewing {report.report_id}"
            return text, text, "awaiting-user"
        if report.status == "pending_retriage":
            text = f"Pending retriage for {report.report_id}"
            return text, text, "running"

    if any(lane_for_status(report.status) != "resolved" for report in reports):
        return "Queue active", "Queue active", "running"
    return "Queue idle", "Waiting for reports", "idle"


def _age_seconds(iso_timestamp: str) -> float:
    try:
        parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except Exception:
        return _BUG_PLANNING_DEBOUNCE_SECONDS
    now = datetime.now(parsed.tzinfo)
    return max((now - parsed).total_seconds(), 0.0)


def _summarize_exception(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    if not message:
        return exc.__class__.__name__
    if len(message) <= 220:
        return message
    return message[:217].rstrip() + "..."


def _ensure_report_retry_state(report: BugflowReportSnapshot) -> BugflowReportSnapshot:
    max_attempts = max(report.max_attempts or 0, _MAX_REPORT_ATTEMPTS)
    report.max_attempts = max_attempts
    report.attempts_used = max(0, min(report.attempts_used or 0, report.max_attempts))
    return report


def _current_report_attempt(report: BugflowReportSnapshot) -> int:
    report = _ensure_report_retry_state(report)
    return min(report.attempts_used + 1, report.max_attempts)


def _lane_attempt_budget(reports: list[BugflowReportSnapshot]) -> tuple[int, int]:
    if not reports:
        return (1, _MAX_REPORT_ATTEMPTS)
    current_attempt = max(_current_report_attempt(report) for report in reports)
    max_attempts = max(_ensure_report_retry_state(report).max_attempts for report in reports)
    return current_attempt, max_attempts


def _failure_kind_label(kind: str) -> str:
    labels = {
        "lane-verify": "re-verification",
        "lane-regression": "regression verification",
        "promotion-verify": "promotion verification",
        "promotion-regression": "promotion regression",
    }
    return labels.get(kind, "verification")


def _strategy_artifact_key(cluster_id: str, strategy_round: int) -> str:
    return f"bugflow-strategy:{cluster_id}:{strategy_round}"


def _failure_bundle_artifact_key(cluster_id: str, strategy_round: int) -> str:
    return f"bugflow-failure-bundle:{cluster_id}:{strategy_round}"


def _normalize_strategy_mode(mode: str | None) -> str:
    value = (mode or "").strip().lower()
    if value in _STRATEGY_MODES:
        return value
    return "ordinary_retry"


def _strategy_mode_label(mode: str | None) -> str:
    labels = {
        "ordinary_retry": "ordinary retry",
        "minimize_counterexample": "minimize counterexample",
        "broaden_scope": "broaden scope",
        "contract_reconciliation": "contract reconciliation",
        "human_attention": "human attention",
    }
    return labels.get(_normalize_strategy_mode(mode), "ordinary retry")


def _issue_signature(issue: Issue) -> tuple[str, str]:
    return (
        str(issue.file or "").strip().lower(),
        " ".join(str(issue.description or "").strip().lower().split()),
    )


def _check_signature(check: Check) -> tuple[str, str]:
    return (
        " ".join(str(check.criterion or "").strip().lower().split()),
        " ".join(str(check.detail or "").strip().lower().split()),
    )


def _format_issue(issue: Issue) -> str:
    location = f" ({issue.file}:{issue.line})" if issue.file else ""
    return f"[{issue.severity}] {issue.description}{location}"


def _format_check(check: Check) -> str:
    detail = f" — {check.detail}" if check.detail else ""
    return f"{check.criterion}: {check.result}{detail}"


def _fallback_blockers(reason: str, lane: BugflowLaneSnapshot) -> list[Issue]:
    description = reason or lane.latest_regression_summary or lane.latest_verify_summary or lane.wait_reason
    if not description:
        description = "The lane could not reach a promotable result."
    file_hint = lane.modified_files[0] if lane.modified_files else ""
    return [Issue(severity="major", description=description, file=file_hint, line=0)]


def _fallback_checks(reason: str, failure_kind: str) -> list[Check]:
    return [
        Check(
            criterion=_failure_kind_label(failure_kind),
            result="FAIL",
            detail=reason,
        )
    ]


def _tokenize_similarity_text(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        for token in re.findall(r"[a-z0-9_./-]+", str(part or "").lower()):
            if len(token) < 3:
                continue
            tokens.add(token)
    return tokens


def _normalize_failure_family(
    *,
    failure_kind: str,
    lane: BugflowLaneSnapshot,
    cluster: BugflowClusterSnapshot | None,
    reason: str,
    verdict: Verdict | None,
) -> str:
    affected = list(cluster.affected_files if cluster else []) or list(lane.modified_files)
    issue_tokens = [
        issue.description
        for issue in (verdict.concerns if verdict is not None else [])[:3]
    ]
    check_tokens = [
        check.criterion
        for check in (verdict.checks if verdict is not None else [])
        if str(check.result).upper() != "PASS"
    ][:3]
    seed_tokens = sorted(
        _tokenize_similarity_text(
            failure_kind,
            reason,
            lane.issue_summary,
            cluster.likely_root_cause if cluster else "",
            " ".join(affected[:5]),
            " ".join(issue_tokens),
            " ".join(check_tokens),
        )
    )
    if not seed_tokens:
        return failure_kind or "verification"
    return " | ".join(seed_tokens[:12])


async def _load_cluster_strategy_decision(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot | None,
) -> RepairStrategyDecision | None:
    if not cluster or not cluster.strategy_decision_key:
        return None
    raw = await runner.artifacts.get(cluster.strategy_decision_key, feature=feature)
    decision = parse_model(raw, RepairStrategyDecision)
    if not isinstance(decision, RepairStrategyDecision):
        return None
    return decision


async def _load_latest_lane_verdict(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> Verdict | None:
    for key in list(lane.latest_regression_keys) + list(lane.latest_verify_keys):
        raw = await runner.artifacts.get(key, feature=feature)
        verdict = parse_model(raw, Verdict)
        if isinstance(verdict, Verdict):
            return verdict
    return None


def _default_strategy_decision(
    *,
    stable_blockers: list[Issue],
    new_blockers: list[Issue],
    failing_checks: list[Check],
    stable_failure_family: str,
    bundle_summary: str,
    similar_cluster_hints: list[str],
    reason: str,
) -> RepairStrategyDecision:
    return RepairStrategyDecision(
        strategy_mode="ordinary_retry",
        reasoning=reason or bundle_summary or "Use an ordinary retry with the current RCA and failure context.",
        stable_blockers=stable_blockers,
        new_blockers=new_blockers,
        failing_checks=failing_checks,
        stable_failure_family=stable_failure_family,
        bundle_summary=bundle_summary,
        scope_expansion=[],
        required_files=[],
        required_checks=[],
        required_evidence_modes=[],
        similar_cluster_hints=similar_cluster_hints,
        merge_recommendation="none",
        why_not_ordinary_retry="",
    )


def _cluster_attempt_payload(lane: BugflowLaneSnapshot) -> dict[str, Any]:
    return {
        "lane_id": lane.lane_id,
        "lane_attempt": lane.lane_attempt,
        "status": lane.status,
        "current_phase": lane.current_phase,
        "wait_reason": lane.wait_reason,
        "latest_fix_summary": lane.latest_fix_summary,
        "latest_verify_summary": lane.latest_verify_summary,
        "latest_regression_summary": lane.latest_regression_summary,
        "modified_files": list(lane.modified_files),
        "lock_scope": list(lane.lock_scope),
        "updated_at": lane.updated_at,
    }


def _summarize_cluster_history(
    *,
    cluster: BugflowClusterSnapshot | None,
    lanes: list[BugflowLaneSnapshot],
) -> tuple[str, list[dict[str, Any]]]:
    lineage = sorted(
        [entry for entry in lanes if cluster and entry.source_cluster_id == cluster.cluster_id],
        key=lambda entry: (entry.lane_attempt, entry.updated_at, entry.lane_id),
    )
    detailed = [_cluster_attempt_payload(entry) for entry in lineage[-_FAILURE_HISTORY_DETAIL_LIMIT:]]
    if not lineage:
        return ("No prior counted product failures are recorded for this cluster yet.", detailed)
    lines = []
    for entry in lineage:
        detail = (
            entry.latest_regression_summary
            or entry.latest_verify_summary
            or entry.wait_reason
            or entry.latest_fix_summary
            or "No summary recorded."
        )
        lines.append(
            f"- lane {entry.lane_id} attempt {entry.lane_attempt}: {entry.status} — {detail}"
        )
    return ("\n".join(lines), detailed)


def _cluster_similarity_score(
    cluster: BugflowClusterSnapshot,
    other: BugflowClusterSnapshot,
) -> int:
    if cluster.cluster_id == other.cluster_id:
        return -1
    score = 0
    affected_overlap = set(cluster.affected_files) & set(other.affected_files)
    repo_overlap = set(cluster.repo_paths) & set(other.repo_paths)
    if affected_overlap:
        score += 4 * len(affected_overlap)
    if repo_overlap:
        score += 2 * len(repo_overlap)
    if cluster.stable_failure_family and cluster.stable_failure_family == other.stable_failure_family:
        score += 6
    token_overlap = _tokenize_similarity_text(
        cluster.likely_root_cause,
        cluster.latest_rca_summary,
    ) & _tokenize_similarity_text(
        other.likely_root_cause,
        other.latest_rca_summary,
    )
    score += len(token_overlap)
    return score


def _format_similarity_hint(cluster: BugflowClusterSnapshot) -> str:
    reason = cluster.strategy_reason or cluster.likely_root_cause or cluster.latest_rca_summary
    return (
        f"{cluster.cluster_id}: {cluster.status}; "
        f"family={cluster.stable_failure_family or 'unclassified'}; "
        f"reason={reason or 'no summary'}"
    )


async def _collect_global_similarity_hints(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot,
) -> tuple[list[str], list[str]]:
    all_clusters = await _load_clusters(
        runner,
        feature,
        await _load_queue(runner, feature),
        await _load_reports(runner, feature),
        await _load_lanes(runner, feature),
    )
    scored = sorted(
        (
            (other.cluster_id, _cluster_similarity_score(cluster, other), _format_similarity_hint(other))
            for other in all_clusters
            if other.cluster_id != cluster.cluster_id
        ),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    top = [item for item in scored if item[1] > 0][:_GLOBAL_CLUSTER_HINT_LIMIT]
    return ([item[2] for item in top], [item[0] for item in top])

def _infer_retryable_failure_kind(
    report: BugflowReportSnapshot | None,
    lane: BugflowLaneSnapshot,
) -> str:
    if report and _normalize_strategy_mode(report.strategy_mode) == "human_attention":
        return ""
    if report and report.last_failure_kind in _COUNTED_FAILURE_KINDS:
        return report.last_failure_kind
    if lane.latest_regression_summary:
        return "lane-regression"
    if lane.latest_verify_summary:
        return "lane-verify"
    if lane.promotion_status in {"failed", "blocked"}:
        lowered = (lane.wait_reason or "").lower()
        if "regression" in lowered:
            return "promotion-regression"
        return "promotion-verify"
    return ""


def _is_retryable_counted_failure(kind: str) -> bool:
    return kind in _COUNTED_FAILURE_KINDS


def _legacy_attempts_used_from_lane(
    report: BugflowReportSnapshot,
    lane: BugflowLaneSnapshot,
) -> int:
    if report.attempts_used > 0:
        return report.attempts_used
    if lane.status == "blocked" and report.current_step == "Lane blocked":
        return max(1, lane.lane_attempt)
    return 0


def _make_thread_user(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
) -> tuple[str | None, Any]:
    root_runtime = runner.interaction_runtimes.get("terminal")
    if not root_runtime or not hasattr(root_runtime, "make_thread_runtime"):
        return None, user
    runtimes = _thread_agent_runtimes(runner, feature, report) or {}
    resolver = f"terminal.thread.{report.report_id}"
    runner.interaction_runtimes[resolver] = root_runtime.make_thread_runtime(
        feature_id=feature.id,
        channel=str(feature.metadata.get("channel_id", "") or ""),
        thread_ts=report.thread_ts,
        persist_turns=True,
        agent_runtime=runtimes.get("primary"),
    )
    return resolver, user.model_copy(update={"resolver": resolver})


def _thread_agent_runtimes(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
) -> dict[str, Any] | None:
    services = getattr(runner, "services", None)
    if not isinstance(services, dict):
        return None
    cache = services.setdefault("bugflow_thread_agent_runtimes", {})
    cached = cache.get(report.report_id)
    if cached:
        return cached

    adapter = services.get("slack_adapter")
    channel_id = str(feature.metadata.get("channel_id", "") or "")
    session_store = getattr(runner, "sessions", None)
    if not adapter or not channel_id or session_store is None:
        return None

    from ....interfaces.slack.streamer import make_slack_on_message

    primary_name = getattr(runner.agent_runtime, "name", "claude")
    secondary_name = getattr(runner.secondary_runtime, "name", primary_name)
    runtimes = {
        "primary": create_agent_runtime(
            primary_name,
            session_store=session_store,
            on_message=make_slack_on_message(adapter, channel_id, report.thread_ts),
            interactive_roles=getattr(runner.agent_runtime, "_interactive_roles", None),
        ),
        "secondary": create_agent_runtime(
            secondary_name,
            session_store=session_store,
            on_message=make_slack_on_message(adapter, channel_id, report.thread_ts),
            interactive_roles=getattr(runner.secondary_runtime, "_interactive_roles", None),
        ),
    }
    cache[report.report_id] = runtimes
    return runtimes


def _make_thread_actor(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    base: Any,
    suffix: str,
    *,
    runtime: str | None = None,
    workspace_path: str | None = None,
) -> Any:
    actor = _make_parallel_actor(
        base,
        suffix,
        runtime=runtime,
        workspace_path=workspace_path,
    )
    runtimes = _thread_agent_runtimes(runner, feature, report)
    if not runtimes:
        return actor
    runtime_key = "secondary" if runtime == "secondary" else "primary"
    metadata = dict(actor.role.metadata)
    metadata["runtime_instance"] = runtimes[runtime_key]
    role = actor.role.model_copy(update={"metadata": metadata})
    return actor.model_copy(update={"role": role})


def _make_lane_actor(
    runner: WorkflowRunner,
    feature: Feature,
    reports: list[BugflowReportSnapshot],
    base: Any,
    suffix: str,
    *,
    runtime: str | None = None,
    workspace_path: str | None = None,
) -> Any:
    if not reports:
        return _make_parallel_actor(
            base,
            suffix,
            runtime=runtime,
            workspace_path=workspace_path,
        )
    primary_report = reports[0]
    return _make_thread_actor(
        runner,
        feature,
        primary_report,
        base,
        suffix,
        runtime=runtime,
        workspace_path=workspace_path,
    )


async def _post_thread_message(
    runner: WorkflowRunner,
    feature: Feature,
    thread_ts: str,
    text: str,
) -> None:
    services = getattr(runner, "services", {}) or {}
    adapter = services.get("slack_adapter") if isinstance(services, dict) else None
    channel_id = str(feature.metadata.get("channel_id", "") or "")
    if not adapter or not channel_id:
        return
    await adapter.post_message(channel_id, text, thread_ts=thread_ts)


async def _post_execution_recovery_notice(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    *,
    notice_key: str,
    text: str,
) -> None:
    if not notice_key:
        await _post_thread_message(runner, feature, report.thread_ts, text)
        return
    if report.latest_execution_notice_key == notice_key:
        return
    await _post_thread_message(runner, feature, report.thread_ts, text)
    report.latest_execution_notice_key = notice_key
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)


async def _respawn_intent_is_applied(
    runner: WorkflowRunner,
    feature: Feature,
    lane_id: str,
) -> bool:
    intent = await _load_respawn_intent(runner, feature, lane_id)
    if not intent:
        return False
    if str(intent.get("status", "")).strip().lower() != "applied":
        return False
    applied_lane_id = str(intent.get("new_lane_id", "")).strip()
    if not applied_lane_id:
        return False
    return await _load_lane(runner, feature, applied_lane_id) is not None


async def _mark_cluster_from_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    *,
    status: str,
    current_phase: str,
    wait_reason: str,
) -> None:
    cluster = await _load_cluster_from_lane(runner, feature, lane)
    if not cluster:
        return
    reports = list((await _load_reports_by_id(runner, feature, lane.report_ids)).values())
    cluster.status = status
    cluster.current_phase = current_phase
    cluster.wait_reason = wait_reason
    cluster.lane_id = lane.lane_id
    if reports:
        cluster.attempt_number = _lane_attempt_budget(reports)[0]
    cluster.latest_fix_summary = lane.latest_fix_summary
    cluster.latest_reverify_summary = lane.latest_verify_summary
    cluster.latest_regression_summary = lane.latest_regression_summary
    cluster.last_push_result = wait_reason
    await _save_cluster(runner, feature, cluster)


async def _mark_lane_reports_active(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    *,
    current_step: str,
) -> None:
    for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
        report.status = "active_fix"
        report.current_step = current_step
        report.promotion_status = lane.promotion_status
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)


async def _mark_lane_reports_waiting_promotion(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> None:
    for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
        report.status = "active_fix"
        report.current_step = f"Verified in {lane.lane_id}; waiting for promotion"
        report.promotion_status = "queued"
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
        await _post_thread_message(
            runner,
            feature,
            report.thread_ts,
            f"{report.report_id}: isolated lane {lane.lane_id} verified cleanly and is queued for promotion onto the main bugflow branch.",
        )


async def _mark_lane_reports_promoting(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> None:
    for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
        report.status = "active_fix"
        report.current_step = f"Promoting {lane.lane_id}"
        report.promotion_status = "promoting"
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)


async def _mark_lane_reports_resolved(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> None:
    for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
        report.status = "resolved"
        report.current_step = "Promoted and pushed to the bugflow branch"
        report.promotion_status = "pushed"
        report.terminal_reason_kind = ""
        report.terminal_reason_summary = ""
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
        source_record = await _load_proof_record(
            runner,
            feature,
            proof_key(report.report_id, "promotion-verify"),
        )
        terminal_record = await _record_terminal_proof(
            runner,
            feature,
            report,
            source_record=source_record,
            summary=source_record.bundle.summary if source_record else f"{lane.lane_id} promoted successfully.",
        )
        await _post_terminal_notice(
            runner,
            feature,
            report,
            notice=f"{report.report_id}: lane {lane.lane_id} promoted successfully and the bugflow branch was pushed.",
            proof_record=terminal_record,
        )


async def _mark_lane_reports_blocked(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    message: str,
    *,
    failure_kind: str = "",
    failure_reason: str = "",
) -> None:
    for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
        report.status = "blocked"
        report.current_step = "Lane blocked"
        report.summary = message
        report.terminal_reason_kind = failure_kind or "blocked"
        report.terminal_reason_summary = failure_reason or message
        if failure_kind:
            report.last_failed_lane_id = lane.lane_id
            report.last_failure_kind = failure_kind
            report.last_failure_reason = failure_reason or message
        report.promotion_status = lane.promotion_status
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
        source_record = None
        if report.latest_proof_key:
            source_record = await _load_proof_record(runner, feature, report.latest_proof_key)
        terminal_record = await _record_terminal_proof(
            runner,
            feature,
            report,
            source_record=source_record,
            summary=message,
        )
        await _post_terminal_notice(
            runner,
            feature,
            report,
            notice=message,
            proof_record=terminal_record,
        )


async def _resolve_contradiction_group(
    runner: WorkflowRunner,
    feature: Feature,
    planned: PlannedBugGroup,
    reports: list[BugflowReportSnapshot],
) -> RootCauseAnalysis | None:
    primary = reports[0] if reports else None
    if not primary:
        return None
    _resolver, thread_user = _make_thread_user(runner, feature, primary)
    approved = await runner.run(
        Gate(
            approver=thread_user,
            prompt=(
                f"A shared RCA for reports {', '.join(report.report_id for report in reports)} found a likely spec or product contradiction.\n\n"
                f"Contradiction detail:\n{planned.rca.contradiction_detail or planned.rca.hypothesis}\n\n"
                f"Best-guess resolution:\n{planned.rca.proposed_approach}\n\n"
                "Approve this direction so I can continue implementing it in an isolated lane?"
            ),
        ),
        feature,
        phase_name="bugflow-queue",
    )
    if approved is not True:
        reason = (
            "Spec contradiction needs manual resolution before lane execution can continue."
        )
        for report in reports:
            await _block_report_with_notice(
                runner,
                feature,
                report,
                current_step=reason,
                summary=reason,
                notice=f"{report.report_id}: I paused this group because the RCA found a contradiction and the proposed resolution was not approved.",
                terminal_reason_kind="contradiction",
            )
        return None

    decision = BugflowDecisionRecord(
        decision_id=new_short_id("D"),
        report_ids=[report.report_id for report in reports],
        title=reports[0].title or planned.group.group_id,
        summary=planned.rca.proposed_approach,
        old_expectation=planned.rca.contradiction_detail,
        new_decision=planned.rca.proposed_approach,
        approved=True,
    )
    await _append_decision(runner, feature, decision)
    await runner.artifacts.put(decision_key(decision.decision_id), decision.model_dump_json(), feature=feature)
    for report in reports:
        report.decision_id = decision.decision_id
        report.decision = decision
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
    return planned.rca.model_copy(
        update={
            "confidence": "high",
            "prior_attempt_analysis": (
                f"{planned.rca.prior_attempt_analysis}\n\nUser approved contradiction resolution during bugflow."
            ).strip(),
        }
    )


def _observation_from_report(report: BugflowReportSnapshot) -> Observation:
    return Observation(
        id=report.report_id,
        category=report.category or "requirement",
        severity=report.severity or "major",
        title=report.title or report.report_id,
        description=report.summary or report.root_message_text,
        affected_area=report.affected_area,
        expected_behavior=report.expected_behavior,
        decision=report.decision.new_decision if report.decision else "",
        ui_involved=report.ui_involved,
        evidence_modes=_observed_modes_for_report(report),
    )


def _strategy_context_prompt(
    strategy: RepairStrategyDecision | None,
) -> str:
    if strategy is None:
        return ""
    stable_blockers = "\n".join(
        f"- {_format_issue(issue)}"
        for issue in strategy.stable_blockers
    ) or "- none recorded"
    new_blockers = "\n".join(
        f"- {_format_issue(issue)}"
        for issue in strategy.new_blockers
    ) or "- none recorded"
    required_checks = "\n".join(f"- {item}" for item in strategy.required_checks) or "- none recorded"
    required_files = "\n".join(f"- `{item}`" for item in strategy.required_files) or "- none recorded"
    required_evidence_modes = "\n".join(f"- {item}" for item in strategy.required_evidence_modes) or "- none recorded"
    similar_hints = "\n".join(f"- {item}" for item in strategy.similar_cluster_hints) or "- none recorded"
    return (
        "### Convergence Strategy\n"
        f"Mode: {_strategy_mode_label(strategy.strategy_mode)}\n"
        f"Reasoning: {strategy.reasoning}\n"
        f"Why not ordinary retry: {strategy.why_not_ordinary_retry or 'not provided'}\n"
        f"Stable failure family: {strategy.stable_failure_family or 'not yet named'}\n"
        f"Bundle summary: {strategy.bundle_summary or 'not recorded'}\n\n"
        f"Stable blockers:\n{stable_blockers}\n\n"
        f"New blockers:\n{new_blockers}\n\n"
        f"Required checks:\n{required_checks}\n\n"
        f"Required files:\n{required_files}\n\n"
        f"Required evidence modes:\n{required_evidence_modes}\n\n"
        f"Similar cluster hints:\n{similar_hints}\n"
    )


def _lane_fix_prompt(
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    rca: RootCauseAnalysis,
    strategy: RepairStrategyDecision | None = None,
) -> str:
    report_lines = "\n".join(
        f"- {report.report_id}: {report.title or report.summary or report.root_message_text}"
        for report in reports
    )
    affected_files = "\n".join(f"- `{path}`" for path in rca.affected_files)
    evidence = "\n".join(f"- {item}" for item in rca.evidence)
    strategy_context = _strategy_context_prompt(strategy)
    return (
        f"## Isolated Bug Lane {lane.lane_id}\n\n"
        f"Reports in this lane:\n{report_lines}\n\n"
        f"RCA hypothesis: {rca.hypothesis}\n\n"
        f"Evidence:\n{evidence}\n\n"
        f"Affected files:\n{affected_files}\n\n"
        f"Proposed approach: {rca.proposed_approach}\n\n"
        f"{strategy_context}\n"
        "Apply the fix in this isolated lane only. Fix only the root cause described above, "
        "and report all files you changed. When a convergence strategy is present, satisfy the full stable blocker bundle "
        "instead of only the most recent symptom."
    )


def _lane_verify_prompt(
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    rca: RootCauseAnalysis,
    fix_summary: str,
    strategy: RepairStrategyDecision | None = None,
) -> str:
    report_lines = "\n".join(
        f"- {report.report_id}: {report.summary or report.root_message_text}"
        for report in reports
    )
    file_lines = "\n".join(f"- `{path}`" for path in lane.modified_files) or "- no files reported"
    strategy_context = _strategy_context_prompt(strategy)
    return (
        f"## Re-verify Isolated Lane {lane.lane_id}\n\n"
        f"Reports:\n{report_lines}\n\n"
        f"Root cause: {rca.hypothesis}\n\n"
        f"Fix applied: {fix_summary}\n\n"
        f"Files modified:\n{file_lines}\n\n"
        f"{strategy_context}\n"
        "Verify whether the original reports are resolved in this isolated lane. "
        "Return approved=true only if the lane is ready for promotion.\n"
        "Return Verdict.proof. For UI-involved reports include Playwright trace and screenshot artifacts. "
        "For backend/stateful reports include surface-appropriate evidence and an independent postcondition check.\n"
        "Evaluate the full stable blocker bundle when one is provided, not just the latest top concern."
    )


async def _promotion_verify_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    promotion_root: Path,
) -> Verdict:
    reports = list((await _load_reports_by_id(runner, feature, lane.report_ids)).values())
    cluster = (
        await _load_cluster(runner, feature, lane.source_cluster_id)
        if lane.source_cluster_id
        else None
    )
    strategy_decision = await _load_cluster_strategy_decision(runner, feature, cluster)
    strategy_context = _strategy_context_prompt(strategy_decision)
    requested_directives = ", ".join(
        sorted({
            directive
            for report in reports
            for directive in _requested_terminal_evidence_for_report(report)
        })
    ) or "none recorded"
    required_modes = ", ".join(
        sorted({
            mode
            for report in reports
            for mode in _required_terminal_core_surfaces_for_report(report)
        })
    ) or "none recorded"
    proof_retry_context = ""
    if lane.promotion_status == "proof-capture-retry" and lane.wait_reason:
        proof_retry_context = f"Current proof gap to close:\n{lane.wait_reason}\n\n"
    if lane.category == "bug":
        prompt = (
            f"## Promotion Candidate Verification: {lane.lane_id}\n\n"
            f"Reports:\n"
            + "\n".join(
                f"- {report.report_id}: {report.summary or report.root_message_text}"
                for report in reports
            )
            + f"\n\nFix summary: {lane.latest_fix_summary}\n\n"
            + f"Requested evidence directives: {requested_directives}\n"
            + f"Required core proof surfaces: {required_modes}\n\n"
            + proof_retry_context
            + f"{strategy_context}\n"
            "Verify the candidate on the latest main bugflow head. Approve only if this replayed lane is still correct.\n"
            "Return Verdict.proof. UI-involved reports require Playwright trace and screenshot artifacts. "
            "State-changing backend flows require surface-specific evidence plus an independent postcondition check.\n"
            "For each requested evidence directive that is not one of ui/api/database/logs/repo, include a Verdict.check entry with criterion `evidence:<directive>`, result `satisfied` or `not-needed`, and detail naming the artifact or rationale."
        )
        actor = _make_lane_actor(
            runner,
            feature,
            reports,
            integration_tester,
            f"promote-verify-{lane.lane_id}",
            runtime="secondary",
            workspace_path=str(promotion_root),
        )
    else:
        prompt = (
            f"## Promotion Candidate Verification: {lane.lane_id}\n\n"
            f"Category: {lane.category}\n\n"
            f"Reports:\n"
            + "\n".join(
                f"- {report.report_id}: {report.summary or report.root_message_text}"
                for report in reports
            )
            + f"\n\nFix summary: {lane.latest_fix_summary}\n\n"
            + f"Requested evidence directives: {requested_directives}\n"
            + f"Required core proof surfaces: {required_modes}\n\n"
            + proof_retry_context
            + f"{strategy_context}\n"
            "Verify that this replayed clarification/requirement/test change is still correct on the latest main bugflow head.\n"
            "Return Verdict.proof with the evidence needed to justify a terminal outcome.\n"
            "For each requested evidence directive that is not one of ui/api/database/logs/repo, include a Verdict.check entry with criterion `evidence:<directive>`, result `satisfied` or `not-needed`, and detail naming the artifact or rationale."
        )
        actor = _make_lane_actor(
            runner,
            feature,
            reports,
            verifier,
            f"promote-verify-{lane.lane_id}",
            runtime="secondary",
            workspace_path=str(promotion_root),
        )
    return await runner.run(
        Ask(
            actor=actor,
            prompt=prompt,
            output_type=Verdict,
        ),
        feature,
        phase_name="bugflow-queue",
    )


async def _initialize_cluster_strategy(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    planned_group: PlannedBugGroup,
) -> bool:
    initial_verdict = Verdict(
        approved=False,
        summary=planned_group.rca.proposed_approach or planned_group.rca.hypothesis,
        concerns=[
            Issue(
                severity=planned_group.group.severity or "major",
                description=planned_group.issue_text or planned_group.group.likely_root_cause,
                file=(planned_group.rca.affected_files or planned_group.group.affected_files_hint or [""])[0],
                line=0,
            )
        ],
        suggestions=[],
        checks=[],
        gaps=[],
    )
    failure_bundle_key, failure_bundle = await _build_cluster_failure_bundle(
        runner,
        feature,
        cluster,
        lane,
        reports,
        reason=planned_group.rca.proposed_approach or planned_group.rca.hypothesis,
        failure_kind="planning-rca",
        current_verdict=initial_verdict,
    )
    cluster.stable_bundle_key = failure_bundle_key
    await _save_cluster(runner, feature, cluster)
    await _set_cluster_strategy_status(
        runner,
        feature,
        cluster,
        status="pending",
    )
    await _set_lane_execution_state(
        runner,
        feature,
        lane,
        state="strategy_pending",
        nonce=new_short_id("exec"),
        failure_kind="planning-rca",
        failure_reason=planned_group.rca.proposed_approach or planned_group.rca.hypothesis,
        wait_reason="Choosing initial repair strategy",
        current_phase="strategy_pending",
    )
    for report in reports:
        report.current_step = f"Choosing initial repair strategy for {report.report_id}"
        report.latest_failure_bundle_key = failure_bundle_key
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
    decision_key, decision = await _decide_cluster_strategy(
        runner,
        feature,
        cluster,
        lane,
        reports,
        failure_bundle_key=failure_bundle_key,
        failure_bundle=failure_bundle,
        reason=planned_group.rca.proposed_approach or planned_group.rca.hypothesis,
    )
    cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
    cluster.strategy_decision_key = decision_key
    await _set_cluster_strategy_status(
        runner,
        feature,
        cluster,
        status="decided",
    )
    return await _apply_cluster_strategy(
        runner,
        feature,
        cluster,
        lane,
        reports,
        decision=decision,
        decision_key=decision_key,
        failure_bundle_key=failure_bundle_key,
        failure_bundle=failure_bundle,
        reason=planned_group.rca.proposed_approach or planned_group.rca.hypothesis,
        failed_attempt=None,
        failure_kind="planning-rca",
        initial=True,
    )


async def _create_lane_worktree_root(
    main_root: Path,
    feature: Feature,
    lane_id: str,
) -> tuple[Path, dict[str, str], dict[str, str]]:
    lane_root = main_root.parent / "lanes" / lane_id / "repos"
    branch_names: dict[str, str] = {}
    base_heads: dict[str, str] = {}
    if lane_root.parent.exists():
        await _remove_worktree_root(main_root, lane_root)
    lane_root.mkdir(parents=True, exist_ok=True)
    for repo_dir in _discover_repo_roots_under(main_root):
        rel_path = str(repo_dir.relative_to(main_root))
        dest_repo = lane_root / rel_path
        branch_name = f"lane/{feature.slug}/{lane_id}/{repo_dir.name}"
        base_branch = await _run_git(repo_dir, "branch", "--show-current")
        base_heads[rel_path] = await _run_git(repo_dir, "rev-parse", "HEAD")
        dest_repo.parent.mkdir(parents=True, exist_ok=True)
        await _prepare_ephemeral_worktree_branch(repo_dir, branch_name)
        await _run_git(
            repo_dir,
            "worktree",
            "add",
            "-b",
            branch_name,
            str(dest_repo),
            base_branch,
        )
        branch_names[rel_path] = branch_name
    return lane_root, branch_names, base_heads


async def _create_promotion_worktree_root(
    main_root: Path,
    feature: Feature,
    lane: BugflowLaneSnapshot,
) -> tuple[Path, dict[str, str], dict[str, str]]:
    promotion_root = (
        main_root.parent
        / "promotion"
        / lane.lane_id
        / f"attempt-{lane.promotion_attempt}"
        / "repos"
    )
    branch_names: dict[str, str] = {}
    base_heads: dict[str, str] = {}
    if promotion_root.parent.exists():
        await _remove_worktree_root(main_root, promotion_root)
    promotion_root.mkdir(parents=True, exist_ok=True)
    for repo_dir in _discover_repo_roots_under(main_root):
        rel_path = str(repo_dir.relative_to(main_root))
        dest_repo = promotion_root / rel_path
        branch_name = f"promote/{feature.slug}/{lane.lane_id}/{lane.promotion_attempt}/{repo_dir.name}"
        base_branch = await _run_git(repo_dir, "branch", "--show-current")
        base_heads[rel_path] = await _run_git(repo_dir, "rev-parse", "HEAD")
        dest_repo.parent.mkdir(parents=True, exist_ok=True)
        await _prepare_ephemeral_worktree_branch(repo_dir, branch_name)
        await _run_git(
            repo_dir,
            "worktree",
            "add",
            "-b",
            branch_name,
            str(dest_repo),
            base_branch,
        )
        branch_names[rel_path] = branch_name
    return promotion_root, branch_names, base_heads


async def _prepare_ephemeral_worktree_branch(repo_dir: Path, branch_name: str) -> None:
    try:
        await _run_git(repo_dir, "worktree", "prune")
    except Exception:
        logger.warning("Failed to prune stale worktrees in %s", repo_dir, exc_info=True)

    try:
        await _run_git(repo_dir, "rev-parse", "--verify", f"refs/heads/{branch_name}")
    except Exception:
        return

    try:
        await _run_git(repo_dir, "branch", "-D", branch_name)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to reset stale worktree branch '{branch_name}' in {repo_dir}: {exc}"
        ) from exc


async def _remove_worktree_root(main_root: Path, worktree_root: Path) -> None:
    if worktree_root.exists():
        for repo_dir in _discover_repo_roots_under(worktree_root):
            rel_path = repo_dir.relative_to(worktree_root)
            base_repo = main_root / rel_path
            try:
                await _run_git(base_repo, "worktree", "remove", "--force", str(repo_dir))
            except Exception:
                logger.warning("Failed to remove worktree %s", repo_dir, exc_info=True)
    parent_root = worktree_root.parent
    if not parent_root.exists():
        return
    for attempt in range(3):
        try:
            shutil.rmtree(parent_root)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 2:
                quarantine_root = parent_root.with_name(
                    f"{parent_root.name}-stale-{new_short_id('tmp')}"
                )
                try:
                    parent_root.rename(quarantine_root)
                except FileNotFoundError:
                    return
                except Exception:
                    logger.warning(
                        "Failed to quarantine stale worktree root %s",
                        parent_root,
                        exc_info=True,
                    )
                    break
                logger.warning(
                    "Quarantined stale worktree root %s to %s after repeated cleanup failures",
                    parent_root,
                    quarantine_root,
                )
                shutil.rmtree(quarantine_root, ignore_errors=True)
                return
            await asyncio.sleep(0.1 * (attempt + 1))
    shutil.rmtree(parent_root, ignore_errors=True)


async def _lane_commit_sequences(
    lane_root: Path,
    base_main_commits_by_repo: dict[str, str],
) -> dict[str, list[str]]:
    commits: dict[str, list[str]] = {}
    for rel_path, base_commit in base_main_commits_by_repo.items():
        repo_dir = lane_root / rel_path
        if not repo_dir.exists():
            continue
        raw = await _run_git(repo_dir, "rev-list", "--reverse", f"{base_commit}..HEAD")
        commits[rel_path] = [line.strip() for line in raw.splitlines() if line.strip()]
    return commits


async def _lane_modified_files(
    lane_root: Path,
    base_main_commits_by_repo: dict[str, str],
) -> list[str]:
    files: set[str] = set()
    for rel_path, base_commit in base_main_commits_by_repo.items():
        repo_dir = lane_root / rel_path
        if not repo_dir.exists():
            continue
        raw = await _run_git(repo_dir, "diff", "--name-only", f"{base_commit}..HEAD")
        for line in raw.splitlines():
            path = line.strip()
            if not path:
                continue
            files.add(f"{rel_path}/{path}" if rel_path else path)
    return sorted(files)


async def _lane_commits_already_on_main(
    main_root: Path,
    lane: BugflowLaneSnapshot,
) -> bool:
    lane_root = Path(lane.workspace_root)
    if not lane_root.exists():
        return False
    commits_by_repo = await _lane_commit_sequences(
        lane_root,
        lane.base_main_commits_by_repo,
    )
    if not any(commits_by_repo.values()):
        return False
    for rel_path, commits in commits_by_repo.items():
        if not commits:
            continue
        main_repo = main_root / rel_path
        if not main_repo.exists():
            return False
        for commit in commits:
            if not await _git_is_ancestor(main_repo, commit, "HEAD"):
                return False
    return True


async def _cherry_pick_lane_commits(
    main_root: Path,
    dest_root: Path,
    commits_by_repo: dict[str, list[str]],
) -> None:
    for rel_path, commits in commits_by_repo.items():
        if not commits:
            continue
        dest_repo = dest_root / rel_path
        for commit in commits:
            try:
                await _run_git(dest_repo, "cherry-pick", commit)
            except Exception:
                try:
                    await _run_git(dest_repo, "cherry-pick", "--abort")
                except Exception:
                    logger.debug("No cherry-pick abort needed for %s", dest_repo, exc_info=True)
                raise


async def _block_interrupted_main_promotion(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    reason: str,
) -> None:
    lane.status = "blocked"
    lane.promotion_status = "interrupted-main"
    lane.wait_reason = reason
    lane.updated_at = utc_now()
    await _save_lane(runner, feature, lane)
    await _mark_cluster_from_lane(
        runner,
        feature,
        lane,
        status="blocked",
        current_phase="blocked",
        wait_reason=reason,
    )
    await _mark_lane_reports_blocked(
        runner,
        feature,
        lane,
        (
            f"{lane.lane_id}: promotion was interrupted while applying changes onto the main bugflow worktree.\n\n"
            f"Reason: {reason}\n\n"
            "I paused this lane instead of respawning it so we do not build new work on top of a possibly partial promotion."
        ),
        failure_kind="infrastructure",
        failure_reason=reason,
    )


async def _finalize_promoted_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    *,
    ensure_push: bool,
) -> None:
    main_root = _get_feature_root(runner, feature)
    if not main_root:
        raise RuntimeError("Missing main bugflow root for promotion finalization")
    if ensure_push:
        await _push_clones_to_source_root(main_root)

    lane = await _load_lane(runner, feature, lane.lane_id) or lane
    lane.status = "promoted"
    lane.promotion_status = "pushed"
    lane.promotion_proof_capture_attempt = 0
    lane.wait_reason = ""
    lane.updated_at = utc_now()
    await _save_lane(runner, feature, lane)
    await _mark_cluster_from_lane(
        runner,
        feature,
        lane,
        status="resolved",
        current_phase="resolved",
        wait_reason="Promoted and pushed",
    )
    await _mark_lane_reports_resolved(runner, feature, lane)
    queued_lanes = [
        entry
        for entry in await _load_lanes(runner, feature)
        if entry.status == "verified_pending_promotion" and entry.lane_id != lane.lane_id
    ]
    await _refresh_repo_status(
        runner,
        feature,
        pushed=True,
        has_unpushed_verified_work=bool(queued_lanes),
        unpromoted_lane_ids=[entry.lane_id for entry in queued_lanes],
    )


async def _retry_or_block_bug_lane(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    *,
    reason: str,
    failure_kind: str,
    current_verdict: Verdict | None = None,
) -> bool:
    reports = list((await _load_reports_by_id(runner, feature, lane.report_ids)).values())
    cluster = await _load_cluster_from_lane(runner, feature, lane)
    if not reports:
        lane.status = "blocked"
        lane.promotion_status = "blocked"
        lane.wait_reason = reason
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)
        await _mark_cluster_from_lane(
            runner,
            feature,
            lane,
            status="blocked",
            current_phase="blocked",
            wait_reason=reason,
        )
        return False

    failed_attempt, max_attempts = _lane_attempt_budget(reports)
    for report in reports:
        _ensure_report_retry_state(report)
        report.attempts_used = max(report.attempts_used, failed_attempt)
        report.last_failed_lane_id = lane.lane_id
        report.last_failure_kind = failure_kind
        report.last_failure_reason = reason
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)

    if all(report.attempts_used < report.max_attempts for report in reports):
        failure_bundle_key, failure_bundle = await _build_cluster_failure_bundle(
            runner,
            feature,
            cluster,
            lane,
            reports,
            reason=reason,
            failure_kind=failure_kind,
            current_verdict=current_verdict,
        )
        if cluster is not None:
            cluster.stable_bundle_key = failure_bundle_key
            await _save_cluster(runner, feature, cluster)
            await _set_cluster_strategy_status(
                runner,
                feature,
                cluster,
                status="pending",
            )
        await _set_lane_execution_state(
            runner,
            feature,
            lane,
            state="strategy_pending",
            nonce=new_short_id("exec"),
            failure_kind=failure_kind,
            failure_reason=reason,
            wait_reason=reason,
            current_phase="strategy_pending",
        )
        for report in reports:
            report.current_step = f"Choosing next repair strategy after {lane.lane_id}"
            report.latest_failure_bundle_key = failure_bundle_key
            report.updated_at = utc_now()
            await _save_report(runner, feature, report)
        decision_key, decision = await _decide_cluster_strategy(
            runner,
            feature,
            cluster,
            lane,
            reports,
            failure_bundle_key=failure_bundle_key,
            failure_bundle=failure_bundle,
            reason=reason,
        )
        if cluster is not None:
            cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
            cluster.strategy_decision_key = decision_key
            await _set_cluster_strategy_status(
                runner,
                feature,
                cluster,
                status="decided",
            )
        return await _apply_cluster_strategy(
            runner,
            feature,
            cluster,
            lane,
            reports,
            decision=decision,
            decision_key=decision_key,
            failure_bundle_key=failure_bundle_key,
            failure_bundle=failure_bundle,
            reason=reason,
            failed_attempt=failed_attempt,
            failure_kind=failure_kind,
            initial=False,
        )

    if cluster:
        cluster.strategy_mode = "human_attention"
        cluster.strategy_reason = (
            f"The outer bugflow attempt budget was exhausted after {failed_attempt}/{max_attempts} attempts."
        )
        cluster.updated_at = utc_now()
        await _save_cluster(runner, feature, cluster)
    lane.status = "blocked"
    lane.promotion_status = "blocked"
    lane.wait_reason = reason
    lane.updated_at = utc_now()
    await _save_lane(runner, feature, lane)
    await _mark_cluster_from_lane(
        runner,
        feature,
        lane,
        status="blocked",
        current_phase="blocked",
        wait_reason=reason,
    )
    await _mark_lane_reports_blocked(
        runner,
        feature,
        lane,
        (
            f"{lane.lane_id}: attempt {failed_attempt}/{max_attempts} failed in "
            f"{_failure_kind_label(failure_kind)}.\n\n"
            f"Reason: {reason}\n\n"
            "The retry budget is exhausted and this report is now blocked pending manual attention."
        ),
        failure_kind=failure_kind,
        failure_reason=reason,
    )
    return False


async def _build_cluster_failure_bundle(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot | None,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    *,
    reason: str,
    failure_kind: str,
    current_verdict: Verdict | None = None,
) -> tuple[str, dict[str, Any]]:
    prior_strategy = await _load_cluster_strategy_decision(runner, feature, cluster)
    verdict = current_verdict or await _load_latest_lane_verdict(runner, feature, lane)
    proof_records = []
    for report in reports:
        if not report.latest_proof_key:
            continue
        proof = await _load_proof_record(runner, feature, report.latest_proof_key)
        if proof is None:
            continue
        diagnostics = _proof_policy_diagnostics(report, proof.bundle, proof)
        proof_records.append(
            {
                "report_id": report.report_id,
                "key": report.latest_proof_key,
                "storage_stage": proof.storage_stage,
                "bundle_url": proof.bundle_url,
                "primary_artifact_url": proof.primary_artifact_url,
                "requested_directives": list(diagnostics["requested_directives"]),
                "required_evidence_modes": list(diagnostics["required_modes"]),
                "provided_evidence_modes": list(diagnostics["provided_modes"]),
                "declared_evidence_modes": list(diagnostics["declared_modes"]),
                "artifact_surfaces": list(diagnostics["artifact_surfaces"]),
                "missing_non_core_directives": list(diagnostics["missing_non_core_directives"]),
                "non_core_check_summaries": list(diagnostics["non_core_check_summaries"]),
            }
        )
    current_blockers = list(verdict.concerns) if verdict is not None and verdict.concerns else _fallback_blockers(reason, lane)
    previous_signatures = {
        _issue_signature(issue)
        for issue in (prior_strategy.stable_blockers if prior_strategy is not None else [])
    }
    if previous_signatures:
        stable_blockers = [issue for issue in current_blockers if _issue_signature(issue) in previous_signatures]
        new_blockers = [issue for issue in current_blockers if _issue_signature(issue) not in previous_signatures]
        if not stable_blockers:
            stable_blockers = list(current_blockers)
    else:
        stable_blockers = list(current_blockers)
        new_blockers = []
    failing_checks = (
        [
            check
            for check in (verdict.checks if verdict is not None else [])
            if str(check.result).upper() != "PASS"
        ]
        or _fallback_checks(reason, failure_kind)
    )
    history_summary, detailed_attempts = _summarize_cluster_history(
        cluster=cluster,
        lanes=await _load_lanes(runner, feature),
    )
    similar_cluster_hints, similar_cluster_ids = (
        await _collect_global_similarity_hints(runner, feature, cluster)
        if cluster is not None
        else ([], [])
    )
    stable_failure_family = _normalize_failure_family(
        failure_kind=failure_kind,
        lane=lane,
        cluster=cluster,
        reason=reason,
        verdict=verdict,
    )
    strategy_round = max(
        (cluster.strategy_round if cluster is not None else 0),
        max((report.strategy_round for report in reports), default=0),
    ) + 1
    bundle_key = _failure_bundle_artifact_key(
        cluster.cluster_id if cluster is not None else lane.source_cluster_id or lane.lane_id,
        strategy_round,
    )
    bundle_summary = (
        (verdict.summary if verdict is not None else "")
        or reason
        or lane.latest_regression_summary
        or lane.latest_verify_summary
        or lane.wait_reason
        or "No failure summary recorded."
    )
    bundle = {
        "cluster_id": cluster.cluster_id if cluster is not None else lane.source_cluster_id,
        "lane_id": lane.lane_id,
        "report_ids": [report.report_id for report in reports],
        "strategy_round": strategy_round,
        "failure_kind": failure_kind,
        "failure_label": _failure_kind_label(failure_kind),
        "failure_reason": reason,
        "bundle_summary": bundle_summary,
        "stable_failure_family": stable_failure_family,
        "stable_blockers": [issue.model_dump(mode="json") for issue in stable_blockers],
        "new_blockers": [issue.model_dump(mode="json") for issue in new_blockers],
        "failing_checks": [check.model_dump(mode="json") for check in failing_checks],
        "history_summary": history_summary,
        "detailed_attempts": detailed_attempts,
        "similar_cluster_hints": similar_cluster_hints,
        "similar_cluster_ids": similar_cluster_ids,
        "current_rca_key": cluster.latest_rca_key if cluster is not None else "",
        "current_rca_summary": cluster.latest_rca_summary if cluster is not None else lane.latest_rca_summary,
        "proof_keys": [item["storage_stage"] or item["key"] for item in proof_records],
        "proof_records": proof_records,
        "required_evidence_modes": sorted({
            directive
            for report in reports
            for directive in _requested_terminal_evidence_for_report(report)
        }),
        "required_core_evidence_modes": sorted({
            mode
            for report in reports
            for mode in _required_terminal_core_surfaces_for_report(report)
        }),
        "merge_recommendation": "none",
        "updated_at": utc_now(),
    }
    await runner.artifacts.put(bundle_key, json.dumps(bundle), feature=feature)
    return bundle_key, bundle


async def _decide_cluster_strategy(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot | None,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    *,
    failure_bundle_key: str,
    failure_bundle: dict[str, Any],
    reason: str,
) -> tuple[str, RepairStrategyDecision]:
    stable_blockers = [
        Issue.model_validate(item)
        for item in failure_bundle.get("stable_blockers", [])
        if isinstance(item, dict)
    ]
    new_blockers = [
        Issue.model_validate(item)
        for item in failure_bundle.get("new_blockers", [])
        if isinstance(item, dict)
    ]
    failing_checks = [
        Check.model_validate(item)
        for item in failure_bundle.get("failing_checks", [])
        if isinstance(item, dict)
    ]
    default = _default_strategy_decision(
        stable_blockers=stable_blockers,
        new_blockers=new_blockers,
        failing_checks=failing_checks,
        stable_failure_family=str(failure_bundle.get("stable_failure_family", "")),
        bundle_summary=str(failure_bundle.get("bundle_summary", "")),
        similar_cluster_hints=[
            str(item) for item in failure_bundle.get("similar_cluster_hints", []) if str(item).strip()
        ],
        reason=reason,
    )
    cluster_id = cluster.cluster_id if cluster is not None else lane.source_cluster_id or lane.lane_id
    strategy_round = int(failure_bundle.get("strategy_round", 1) or 1)
    decision_key = _strategy_artifact_key(cluster_id, strategy_round)
    existing_decision = await _load_strategy_decision_by_key(runner, feature, decision_key)
    if existing_decision is not None:
        failure_bundle.update(
            {
                "strategy_mode": existing_decision.strategy_mode,
                "strategy_reason": existing_decision.reasoning,
                "merge_recommendation": existing_decision.merge_recommendation,
                "required_files": list(existing_decision.required_files),
                "required_checks": list(existing_decision.required_checks),
                "required_evidence_modes": list(existing_decision.required_evidence_modes),
                "why_not_ordinary_retry": existing_decision.why_not_ordinary_retry,
            }
        )
        await runner.artifacts.put(failure_bundle_key, json.dumps(failure_bundle), feature=feature)
        return decision_key, existing_decision
    current_strategy = await _load_cluster_strategy_decision(runner, feature, cluster)
    report_lines = "\n".join(
        f"- {report.report_id}: {report.title or report.summary or report.root_message_text}"
        for report in reports
    ) or "- none recorded"
    current_strategy_text = (
        f"Current strategy mode: {current_strategy.strategy_mode}\n"
        f"Current strategy reasoning: {current_strategy.reasoning}\n"
        if current_strategy is not None
        else "Current strategy mode: none recorded yet\n"
    )
    prompt = (
        f"## Bugflow Cluster {cluster_id}\n\n"
        f"Reports:\n{report_lines}\n\n"
        f"{current_strategy_text}\n"
        f"Latest RCA summary:\n{failure_bundle.get('current_rca_summary') or 'No RCA summary recorded.'}\n\n"
        f"Failure family: {failure_bundle.get('stable_failure_family') or 'unclassified'}\n"
        f"Failure reason: {failure_bundle.get('failure_reason') or reason}\n"
        f"Bundle summary: {failure_bundle.get('bundle_summary') or reason}\n\n"
        f"Summarized cluster history:\n{failure_bundle.get('history_summary') or 'No history recorded.'}\n\n"
        "Last detailed attempts (prompt texture only):\n"
        f"{json.dumps(failure_bundle.get('detailed_attempts', []), indent=2)}\n\n"
        "Stable blockers:\n"
        + ("\n".join(f"- {_format_issue(item)}" for item in stable_blockers) or "- none recorded")
        + "\n\nNew blockers:\n"
        + ("\n".join(f"- {_format_issue(item)}" for item in new_blockers) or "- none recorded")
        + "\n\nFailing checks:\n"
        + ("\n".join(f"- {_format_check(item)}" for item in failing_checks) or "- none recorded")
        + "\n\nSimilar cluster hints (advisory only, do not auto-merge):\n"
        + ("\n".join(f"- {item}" for item in failure_bundle.get("similar_cluster_hints", [])) or "- none recorded")
        + "\n\nChoose the next automated repair strategy for this cluster."
    )
    decision = await runner.run(
        Ask(
            actor=_make_lane_actor(
                runner,
                feature,
                reports,
                convergence_strategist,
                f"strategy-{cluster_id}-{strategy_round}",
                runtime="secondary",
                workspace_path=lane.workspace_root,
            ),
            prompt=prompt,
            output_type=RepairStrategyDecision,
        ),
        feature,
        phase_name="bugflow-queue",
    )
    if not isinstance(decision, RepairStrategyDecision):
        raise RuntimeError(f"Convergence strategist returned invalid output for {cluster_id}")
    decision = decision.model_copy(update={"strategy_mode": _normalize_strategy_mode(decision.strategy_mode)})
    failure_bundle.update(
        {
            "strategy_mode": decision.strategy_mode,
            "strategy_reason": decision.reasoning,
            "merge_recommendation": decision.merge_recommendation,
            "required_files": list(decision.required_files),
            "required_checks": list(decision.required_checks),
            "required_evidence_modes": list(decision.required_evidence_modes),
            "why_not_ordinary_retry": decision.why_not_ordinary_retry,
        }
    )
    await runner.artifacts.put(failure_bundle_key, json.dumps(failure_bundle), feature=feature)
    await runner.artifacts.put(decision_key, decision.model_dump_json(), feature=feature)
    return decision_key, decision


def _strategy_scope_override(
    lane: BugflowLaneSnapshot,
    cluster: BugflowClusterSnapshot | None,
    decision: RepairStrategyDecision,
) -> tuple[list[str], list[str], list[str]]:
    file_candidates: set[str] = set(cluster.affected_files if cluster is not None else [])
    file_candidates.update(path for path in decision.required_files if "/" in path)
    explicit_tokens: set[str] = set(lane.lock_scope)
    repo_paths: set[str] = set(lane.repo_paths)
    for item in decision.scope_expansion:
        value = str(item or "").strip()
        if not value:
            continue
        if value.startswith("repo:"):
            explicit_tokens.add(value)
            repo_paths.add(value.split(":", 1)[1])
            continue
        if value.startswith("file:"):
            explicit_tokens.add(value)
            continue
        if "/" in value:
            file_candidates.add(value)
        else:
            repo_paths.add(value)
            explicit_tokens.add(f"repo:{value}")
    if file_candidates:
        derived_lock_scope, derived_repo_paths = _derive_lock_scope(sorted(file_candidates))
        explicit_tokens.update(derived_lock_scope)
        repo_paths.update(derived_repo_paths)
    return sorted(explicit_tokens), sorted(repo_paths), sorted(file_candidates)


async def _post_strategy_notice(
    runner: WorkflowRunner,
    feature: Feature,
    report: BugflowReportSnapshot,
    cluster: BugflowClusterSnapshot | None,
    decision: RepairStrategyDecision,
    *,
    decision_key: str,
    initial: bool,
) -> None:
    if report.latest_strategy_notice_key == decision_key:
        return
    if initial and _normalize_strategy_mode(decision.strategy_mode) == "ordinary_retry":
        return
    lines = [
        f"{report.report_id}: switching to {_strategy_mode_label(decision.strategy_mode)} mode"
        + (f" for cluster {cluster.cluster_id}." if cluster is not None else "."),
        "",
        f"Reason: {decision.reasoning or decision.bundle_summary or 'No strategy reason recorded.'}",
    ]
    if decision.why_not_ordinary_retry and _normalize_strategy_mode(decision.strategy_mode) != "ordinary_retry":
        lines.extend(["", f"Why not ordinary retry: {decision.why_not_ordinary_retry}"])
    if decision.stable_failure_family:
        lines.extend(["", f"Stable failure family: {decision.stable_failure_family}"])
    if decision.required_files:
        lines.extend(["", "Required files:"] + [f"- `{item}`" for item in decision.required_files])
    if decision.required_checks:
        lines.extend(["", "Required checks:"] + [f"- {item}" for item in decision.required_checks])
    if decision.similar_cluster_hints:
        lines.extend(["", "Similar cluster hints:"] + [f"- {item}" for item in decision.similar_cluster_hints])
    if decision.merge_recommendation != "none":
        lines.extend(["", f"Similarity recommendation: {decision.merge_recommendation} (advisory only; no auto-merge)."])
    await _post_thread_message(
        runner,
        feature,
        report.thread_ts,
        "\n".join(lines),
    )
    report.latest_strategy_notice_key = decision_key
    report.updated_at = utc_now()
    await _save_report(runner, feature, report)


async def _minimize_cluster_counterexample(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    *,
    failure_bundle: dict[str, Any],
) -> bool:
    prompt = (
        f"## Minimize Counterexample For Cluster {lane.source_cluster_id or lane.lane_id}\n\n"
        f"Reports:\n"
        + "\n".join(
            f"- {report.report_id}: {report.title or report.summary or report.root_message_text}"
            for report in reports
        )
        + "\n\n"
        f"Current failure family: {failure_bundle.get('stable_failure_family') or 'unclassified'}\n"
        f"Bundle summary: {failure_bundle.get('bundle_summary') or 'No bundle summary recorded.'}\n\n"
        "Reduce this to the smallest deterministic failing journey or proof package you can capture.\n"
        "Do not implement a fix. Return a Verdict whose summary names the reduced counterexample and whose proof captures the minimized evidence.\n"
        "UI-involved failures should include Playwright trace and screenshot proof."
    )
    result = await runner.run(
        Ask(
            actor=_make_lane_actor(
                runner,
                feature,
                reports,
                integration_tester,
                f"strategy-minimize-{lane.source_cluster_id or lane.lane_id}",
                runtime="secondary",
                workspace_path=lane.workspace_root,
            ),
            prompt=prompt,
            output_type=Verdict,
        ),
        feature,
        phase_name="bugflow-queue",
    )
    if not isinstance(result, Verdict) or result.proof is None:
        return False
    stored_any = False
    context_root = Path(lane.workspace_root) if lane.workspace_root else _proof_context_root(runner, feature)
    for report in reports:
        proof = await _store_report_proof(
            runner,
            feature,
            report,
            stage="strategy-minimize",
            bundle=result.proof,
            checks=result.checks,
            context_root=context_root,
        )
        stored_any = stored_any or proof is not None
    return stored_any


def _apply_cluster_strategy_state(
    cluster: BugflowClusterSnapshot,
    *,
    decision: RepairStrategyDecision,
    decision_key: str,
    failure_bundle_key: str,
    strategy_round: int,
    similar_cluster_ids: list[str],
    reason: str,
) -> BugflowClusterSnapshot:
    cluster.strategy_mode = _normalize_strategy_mode(decision.strategy_mode)
    cluster.strategy_decision_key = (decision_key or "").strip()
    cluster.stable_bundle_key = (failure_bundle_key or "").strip()
    cluster.stable_failure_family = decision.stable_failure_family
    cluster.strategy_round = max(1, int(strategy_round or 0))
    cluster.strategy_reason = decision.reasoning or decision.bundle_summary or reason
    cluster.similar_cluster_ids = list(similar_cluster_ids)
    return cluster


def _apply_report_strategy_state(
    report: BugflowReportSnapshot,
    *,
    decision: RepairStrategyDecision,
    decision_key: str,
    failure_bundle_key: str,
    strategy_round: int,
    reason: str,
    clear_terminal: bool,
) -> BugflowReportSnapshot:
    report.strategy_mode = _normalize_strategy_mode(decision.strategy_mode)
    report.strategy_decision_key = decision_key
    report.strategy_reason = decision.reasoning or decision.bundle_summary or reason
    report.strategy_round = strategy_round
    report.stable_failure_family = decision.stable_failure_family
    report.latest_failure_bundle_key = failure_bundle_key
    report.strategy_required_evidence_modes = list(decision.required_evidence_modes)
    if clear_terminal:
        report.terminal_proof_key = ""
        report.terminal_proof_summary = ""
        report.terminal_notice_sent_for_key = ""
    return report


async def _apply_cluster_strategy(
    runner: WorkflowRunner,
    feature: Feature,
    cluster: BugflowClusterSnapshot | None,
    lane: BugflowLaneSnapshot,
    reports: list[BugflowReportSnapshot],
    *,
    decision: RepairStrategyDecision,
    decision_key: str,
    failure_bundle_key: str,
    failure_bundle: dict[str, Any],
    reason: str,
    failed_attempt: int | None,
    failure_kind: str,
    initial: bool,
) -> bool:
    mode = _normalize_strategy_mode(decision.strategy_mode)
    decision = decision.model_copy(update={"strategy_mode": mode})
    strategy_round = int(failure_bundle.get("strategy_round", 1) or 1)
    similar_cluster_ids = [
        str(item) for item in failure_bundle.get("similar_cluster_ids", []) if str(item).strip()
    ]
    if cluster is not None:
        cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
    if cluster is not None:
        cluster = _apply_cluster_strategy_state(
            cluster,
            decision=decision.model_copy(
                update={
                    "stable_failure_family": (
                        decision.stable_failure_family
                        or str(failure_bundle.get("stable_failure_family", ""))
                    ),
                }
            ),
            decision_key=decision_key,
            failure_bundle_key=failure_bundle_key,
            strategy_round=strategy_round,
            similar_cluster_ids=similar_cluster_ids,
            reason=reason,
        )
        if mode in {"broaden_scope", "contract_reconciliation"}:
            lock_scope, repo_paths, affected_files = _strategy_scope_override(lane, cluster, decision)
            lane.lock_scope = lock_scope
            lane.repo_paths = repo_paths
            cluster.repo_paths = sorted(set(cluster.repo_paths) | set(repo_paths))
            cluster.affected_files = sorted(set(cluster.affected_files) | set(affected_files))
        await _save_cluster(runner, feature, cluster)
    lane.wait_reason = decision.reasoning or reason
    await _save_lane(runner, feature, lane)

    updated_reports: list[BugflowReportSnapshot] = []
    for report in reports:
        report = _apply_report_strategy_state(
            report,
            decision=decision.model_copy(
                update={
                    "stable_failure_family": (
                        decision.stable_failure_family
                        or str(failure_bundle.get("stable_failure_family", ""))
                    ),
                }
            ),
            decision_key=decision_key,
            failure_bundle_key=failure_bundle_key,
            strategy_round=strategy_round,
            reason=reason,
            clear_terminal=mode != "human_attention",
        )
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
        updated_reports.append(report)

    if mode == "human_attention":
        lane.status = "blocked"
        lane.promotion_status = "blocked"
        lane.wait_reason = decision.reasoning or reason
        lane.updated_at = utc_now()
        await _save_lane(runner, feature, lane)
        await _mark_cluster_from_lane(
            runner,
            feature,
            lane,
            status="blocked",
            current_phase="blocked",
            wait_reason=lane.wait_reason,
        )
        await _mark_lane_reports_blocked(
            runner,
            feature,
            lane,
            (
                f"{lane.lane_id}: the convergence strategist chose human attention.\n\n"
                f"Reason: {decision.reasoning or reason}\n\n"
                f"Stable failure family: {decision.stable_failure_family or failure_bundle.get('stable_failure_family') or 'unclassified'}"
            ),
            failure_kind="human_attention",
            failure_reason=decision.reasoning or reason,
        )
        if cluster is not None:
            await _set_cluster_strategy_status(
                runner,
                feature,
                cluster,
                status="applied",
            )
        return False

    if mode == "minimize_counterexample":
        minimized = await _minimize_cluster_counterexample(
            runner,
            feature,
            lane,
            reports,
            failure_bundle=failure_bundle,
        )
        if not minimized:
            fallback = decision.model_copy(
                update={
                    "strategy_mode": "human_attention",
                    "reasoning": (
                        "Counterexample minimization did not produce reusable proof for the next automated attempt."
                    ),
                    "why_not_ordinary_retry": (
                        decision.why_not_ordinary_retry
                        or "A minimized counterexample could not be captured automatically."
                    ),
                }
            )
            return await _apply_cluster_strategy(
                runner,
                feature,
                cluster,
                lane,
                reports,
                decision=fallback,
                decision_key=decision_key,
                failure_bundle_key=failure_bundle_key,
                failure_bundle=failure_bundle,
                reason=reason,
                failed_attempt=failed_attempt,
                failure_kind=failure_kind,
                initial=False,
            )
        if initial:
            if cluster is not None:
                cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
                await _set_cluster_strategy_status(
                    runner,
                    feature,
                    cluster,
                    status="applied",
                )
            await _clear_lane_execution(runner, feature, lane)
            for report in updated_reports:
                await _post_strategy_notice(
                    runner,
                    feature,
                    report,
                    cluster,
                    decision,
                    decision_key=decision_key,
                    initial=initial,
                )
            return True

    if initial:
        if cluster is not None:
            cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
            await _set_cluster_strategy_status(
                runner,
                feature,
                cluster,
                status="applied",
            )
        await _clear_lane_execution(runner, feature, lane)
        for report in updated_reports:
            await _post_strategy_notice(
                runner,
                feature,
                report,
                cluster,
                decision,
                decision_key=decision_key,
                initial=initial,
            )
        return True

    await _respawn_lane_from_latest_main(
        runner,
        feature,
        lane,
        reason,
        failed_attempt=failed_attempt,
        failure_kind=failure_kind,
        strategy_decision=decision,
        strategy_decision_key=decision_key,
        failure_bundle_key=failure_bundle_key,
    )
    if cluster is not None:
        cluster = await _load_cluster(runner, feature, cluster.cluster_id) or cluster
        await _set_cluster_strategy_status(
            runner,
            feature,
            cluster,
            status="applied",
        )
    for report in updated_reports:
        refreshed = await _load_report(runner, feature, report.report_id) or report
        await _post_strategy_notice(
            runner,
            feature,
            refreshed,
            cluster,
            decision,
            decision_key=decision_key,
            initial=initial,
        )
    return True


async def _respawn_lane_from_latest_main(
    runner: WorkflowRunner,
    feature: Feature,
    lane: BugflowLaneSnapshot,
    reason: str,
    *,
    failed_attempt: int | None = None,
    failure_kind: str = "",
    strategy_decision: RepairStrategyDecision | None = None,
    strategy_decision_key: str = "",
    failure_bundle_key: str = "",
) -> None:
    main_root = _get_feature_root(runner, feature)
    if not main_root:
        raise RuntimeError("Missing main bugflow root for lane respawn")
    intent = await _load_respawn_intent(runner, feature, lane.lane_id)
    if intent and str(intent.get("status", "")).strip().lower() == "applied":
        applied_lane_id = str(intent.get("new_lane_id", "")).strip()
        applied_lane = await _load_lane(runner, feature, applied_lane_id) if applied_lane_id else None
        if applied_lane is not None:
            cluster = await _load_cluster_from_lane(runner, feature, lane)
            if cluster is not None and cluster.lane_id != applied_lane.lane_id:
                cluster.lane_id = applied_lane.lane_id
                cluster.current_phase = applied_lane.current_phase
                cluster.status = applied_lane.status
                cluster.wait_reason = applied_lane.wait_reason
                await _save_cluster(runner, feature, cluster)
            for report in (await _load_reports_by_id(runner, feature, lane.report_ids)).values():
                if report.lane_id == lane.lane_id:
                    report.lane_id = applied_lane.lane_id
                    report.updated_at = utc_now()
                    await _save_report(runner, feature, report)
            return
    reports = list((await _load_reports_by_id(runner, feature, lane.report_ids)).values())
    proof_capture_retry = _proof_capture_retry_in_flight(lane, reports)
    prior_proofs_by_report_id = {
        report.report_id: (
            await _load_proof_record(runner, feature, report.latest_proof_key)
            if report.latest_proof_key
            else None
        )
        for report in reports
    }
    current_attempt, _max_attempts = _lane_attempt_budget(reports)
    respawn_attempt = int(intent.get("respawn_attempt", 0)) + 1 if intent else 1
    new_lane_id = str(intent.get("new_lane_id", "")).strip() if intent else ""
    if not new_lane_id:
        new_lane_id = new_short_id("L")
    intent_payload = {
        "old_lane_id": lane.lane_id,
        "new_lane_id": new_lane_id,
        "status": "pending",
        "reason": reason,
        "failed_attempt": failed_attempt,
        "failure_kind": failure_kind,
        "strategy_decision_key": strategy_decision_key,
        "failure_bundle_key": failure_bundle_key,
        "respawn_attempt": respawn_attempt,
        "updated_at": utc_now(),
    }
    await _save_respawn_intent(
        runner,
        feature,
        lane.lane_id,
        intent_payload,
    )
    existing_new_lane = await _load_lane(runner, feature, new_lane_id)
    if existing_new_lane is not None:
        lane_root = Path(existing_new_lane.workspace_root)
        branch_names = dict(existing_new_lane.branch_names_by_repo)
        base_heads = dict(existing_new_lane.base_main_commits_by_repo)
    else:
        lane_root, branch_names, base_heads = await _create_lane_worktree_root(
            main_root,
            feature,
            new_lane_id,
        )
    cluster = await _load_cluster_from_lane(runner, feature, lane)
    respawn_rca_key = (
        cluster.latest_rca_key
        if cluster is not None and cluster.latest_rca_key
        else (lane.latest_rca_keys[0] if lane.latest_rca_keys else "")
    )
    respawn_dispatch_key = (
        cluster.latest_dispatch_key
        if cluster is not None and cluster.latest_dispatch_key
        else lane.latest_dispatch_key
    )
    respawn_rca_summary = (
        cluster.latest_rca_summary
        if cluster is not None and cluster.latest_rca_summary
        else lane.latest_rca_summary
    )
    lock_scope = list(lane.lock_scope)
    repo_paths = list(lane.repo_paths)
    if strategy_decision is not None and _normalize_strategy_mode(strategy_decision.strategy_mode) in {
        "broaden_scope",
        "contract_reconciliation",
    }:
        lock_scope, repo_paths, affected_files = _strategy_scope_override(
            lane,
            cluster,
            strategy_decision,
        )
        if cluster is not None:
            cluster.affected_files = sorted(set(cluster.affected_files) | set(affected_files))
            cluster.repo_paths = sorted(set(cluster.repo_paths) | set(repo_paths))
    new_lane = (existing_new_lane or lane).model_copy(
        update={
            "lane_id": new_lane_id,
            "lane_attempt": lane.lane_attempt + 1,
            "status": "verified_pending_promotion" if proof_capture_retry else "planned",
            "workspace_root": str(lane_root),
            "branch_names_by_repo": branch_names,
            "base_main_commits_by_repo": base_heads,
            "lock_scope": lock_scope,
            "repo_paths": repo_paths,
            "promotion_status": "proof-capture-retry" if proof_capture_retry else "",
            "promotion_attempt": 0,
            "promotion_proof_capture_attempt": (
                lane.promotion_proof_capture_attempt if proof_capture_retry else 0
            ),
            "supersedes_lane_id": lane.lane_id,
            "wait_reason": reason,
            "execution_state": "",
            "execution_nonce": "",
            "execution_kind": "",
            "execution_owner": "",
            "execution_started_at": "",
            "last_progress_at": "",
            "execution_failure_kind": "",
            "execution_failure_reason": "",
            "latest_rca_keys": [respawn_rca_key] if lane.category == "bug" and respawn_rca_key else [],
            "latest_verify_keys": [],
            "latest_regression_keys": [],
            "latest_dispatch_key": respawn_dispatch_key if lane.category == "bug" else "",
            "latest_rca_summary": respawn_rca_summary if lane.category == "bug" else "",
            "latest_fix_summary": lane.latest_fix_summary if proof_capture_retry else "",
            "latest_verify_summary": lane.latest_verify_summary if proof_capture_retry else "",
            "latest_regression_summary": "",
            "modified_files": list(lane.modified_files) if proof_capture_retry else [],
            "verification_actor": lane.verification_actor if lane.category == "bug" else "",
            "observation_payload": None,
            "implementation_result": None,
            "test_result": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
    )
    lane.status = "superseded"
    lane.promotion_status = "respawned"
    lane.wait_reason = reason
    lane.updated_at = utc_now()
    await _clear_lane_execution(runner, feature, lane)
    lane.wait_reason = reason
    lane.status = "superseded"
    lane.promotion_status = "respawned"
    lane.updated_at = utc_now()
    await _save_lane(runner, feature, lane)
    await _save_lane(runner, feature, new_lane)
    await _mark_cluster_from_lane(
        runner,
        feature,
        new_lane,
        status="verified_pending_promotion" if proof_capture_retry else "planned",
        current_phase="promotion_pending" if proof_capture_retry else "planned",
        wait_reason=reason,
    )
    cluster = await _load_cluster_from_lane(runner, feature, new_lane)
    if cluster:
        cluster.attempt_number = current_attempt
        if strategy_decision is not None and strategy_decision_key.strip():
            cluster = _apply_cluster_strategy_state(
                cluster,
                decision=strategy_decision,
                decision_key=strategy_decision_key,
                failure_bundle_key=failure_bundle_key,
                strategy_round=max(cluster.strategy_round, max((report.strategy_round for report in reports), default=0)),
                similar_cluster_ids=cluster.similar_cluster_ids,
                reason=reason,
            )
            cluster.strategy_status = cluster.strategy_status or "decided"
        else:
            cluster = _clear_cluster_strategy_fields(cluster)
        await _save_cluster(runner, feature, cluster)
    for report in reports:
        report.lane_id = new_lane_id
        report.status = "queued"
        if strategy_decision is not None and strategy_decision_key.strip():
            report = _apply_report_strategy_state(
                report,
                decision=strategy_decision,
                decision_key=strategy_decision_key,
                failure_bundle_key=failure_bundle_key,
                strategy_round=max(report.strategy_round, cluster.strategy_round if cluster is not None else report.strategy_round),
                reason=reason,
                clear_terminal=True,
            )
        else:
            report = _clear_report_strategy_fields(
                report,
                keep_failure_bundle_key=bool((report.latest_failure_bundle_key or "").strip()),
                clear_proof_contract=not proof_capture_retry,
            )
            report.terminal_proof_key = ""
            report.terminal_proof_summary = ""
            report.terminal_notice_sent_for_key = ""
        report.terminal_reason_kind = ""
        report.terminal_reason_summary = ""
        report.latest_proof_key = ""
        report.current_step = (
            (
                f"Respawned promotion proof capture into isolated lane {new_lane_id}"
                if failed_attempt is None
                else f"Attempt {failed_attempt}/{report.max_attempts} failed during promotion proof capture; respawned into isolated lane {new_lane_id}"
            )
            if proof_capture_retry
            else (
                f"Respawned into isolated lane {new_lane_id}"
                if failed_attempt is None
                else (
                    f"Attempt {failed_attempt}/{report.max_attempts} failed; respawned into isolated lane {new_lane_id}"
                    if strategy_decision is None
                    else f"Attempt {failed_attempt}/{report.max_attempts} failed; switching to {_strategy_mode_label(strategy_decision.strategy_mode)} in isolated lane {new_lane_id}"
                )
            )
        )
        report.status = "active_fix" if proof_capture_retry else "queued"
        report.promotion_status = "proof-capture-retry" if proof_capture_retry else ""
        report.updated_at = utc_now()
        await _save_report(runner, feature, report)
        proof_record = prior_proofs_by_report_id.get(report.report_id)
        lines = []
        if failed_attempt is None:
            lines.extend(
                [
                    f"{report.report_id}: lane {lane.lane_id} was restarted on the latest main bugflow head.",
                    "",
                    f"Reason: {reason}",
                    "",
                    f"I created a fresh lane attempt: {new_lane_id}.",
                ]
            )
        else:
            lines.extend(
                [
                    f"{report.report_id}: attempt {failed_attempt}/{report.max_attempts} failed in {_failure_kind_label(failure_kind)}.",
                    "",
                    f"Reason: {reason}",
                    "",
                    (
                        f"Next strategy: {_strategy_mode_label(strategy_decision.strategy_mode)}."
                        if strategy_decision is not None
                        else "I created a fresh lane attempt."
                    ),
                    "",
                    f"Fresh lane attempt: {new_lane_id}.",
                ]
            )
        if strategy_decision is not None and strategy_decision.why_not_ordinary_retry:
            lines.extend(["", f"Why not ordinary retry: {strategy_decision.why_not_ordinary_retry}"])
        if proof_record and proof_record.bundle_url:
            lines.extend(["", f"Proof bundle: {proof_record.bundle_url}"])
        if proof_record and proof_record.primary_artifact_url:
            lines.append(f"Key artifact: {proof_record.primary_artifact_url}")
        if proof_record and proof_record.bundle.summary:
            lines.extend(["", f"Evidence summary: {proof_record.bundle.summary}"])
        await _post_thread_message(
            runner,
            feature,
            report.thread_ts,
            "\n".join(lines),
        )
    intent_payload["status"] = "applied"
    intent_payload["applied_at"] = utc_now()
    await _save_respawn_intent(
        runner,
        feature,
        lane.lane_id,
        intent_payload,
    )


async def _has_active_bug_lanes(
    runner: WorkflowRunner,
    feature: Feature,
) -> bool:
    for lane in await _load_lanes(runner, feature):
        if lane.category == "bug" and lane.status in {
            "planned",
            "active_fix",
            "active_verify",
            "verified_pending_promotion",
            "promoting",
        }:
            return True
    return False


async def _refresh_repo_status(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    pushed: bool,
    has_unpushed_verified_work: bool,
    unpromoted_lane_ids: list[str],
) -> None:
    feature_root = _get_feature_root(runner, feature)
    if not feature_root:
        return
    existing = parse_model(
        await runner.artifacts.get("bugflow-repo-status", feature=feature),
        BugflowRepoStatus,
        default=BugflowRepoStatus(),
    )
    assert isinstance(existing, BugflowRepoStatus)

    repos = []
    for repo in existing.repos:
        repo_path = feature_root / repo.repo_path
        if not repo_path.exists():
            repos.append(repo)
            continue
        head_commit = await _git_stdout(repo_path, "rev-parse", "HEAD")
        branch_name = await _git_stdout(repo_path, "branch", "--show-current")
        repos.append(
            repo.model_copy(
                update={
                    "head_commit": head_commit,
                    "branch_name": branch_name or existing.branch_name,
                    "last_pushed_commit": head_commit if pushed else repo.last_pushed_commit,
                    "last_push_at": utc_now() if pushed else repo.last_push_at,
                    "touched": True,
                }
            )
        )

    status = existing.model_copy(
        update={
            "repos": repos,
            "has_unpushed_verified_work": has_unpushed_verified_work,
            "unpromoted_lane_ids": unpromoted_lane_ids,
            "updated_at": utc_now(),
        }
    )
    await runner.artifacts.put("bugflow-repo-status", status.model_dump_json(), feature=feature)


async def _git_stdout(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "git %s failed in %s: %s",
            " ".join(args),
            cwd,
            stderr.decode().strip(),
        )
        return ""
    return stdout.decode().strip()


async def _git_is_ancestor(cwd: Path, older: str, newer: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "merge-base",
        "--is-ancestor",
        older,
        newer,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    logger.warning(
        "git merge-base --is-ancestor %s %s failed in %s: %s",
        older,
        newer,
        cwd,
        stderr.decode().strip(),
    )
    return False
