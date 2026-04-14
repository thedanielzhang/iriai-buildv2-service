from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ...models.outputs import EvidenceBundle


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_short_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:4]}"


def report_key(report_id: str) -> str:
    return f"bugflow-report:{report_id}"


def cluster_key(cluster_id: str) -> str:
    return f"bugflow-cluster:{cluster_id}"


def lane_key(lane_id: str) -> str:
    return f"bugflow-lane:{lane_id}"


def decision_key(decision_id: str) -> str:
    return f"bugflow-decision:{decision_id}"


def proof_key(report_id: str, stage: str) -> str:
    return f"bugflow-proof:{report_id}:{stage}"


def default_counts() -> dict[str, int]:
    return {
        "intake_pending": 0,
        "awaiting_confirmation": 0,
        "queued": 0,
        "active_fix": 0,
        "pending_retriage": 0,
        "blocked": 0,
        "resolved": 0,
    }


def lane_for_status(status: str | None) -> str:
    value = (status or "").strip().lower()
    if value.startswith("resolved") or value in {"complete", "closed"}:
        return "resolved"
    if value in {"blocked", "cancelled"}:
        return "blocked"
    if value == "pending_retriage":
        return "pending_retriage"
    if value in {
        "active_fix",
        "active",
        "fixing",
        "triage",
        "rca",
        "reverify",
        "regression",
        "pushing",
    }:
        return "active_fix"
    if value in {
        "awaiting_confirmation",
        "clarification_pending",
        "waiting_for_confirmation",
    }:
        return "awaiting_confirmation"
    if value in {"intake_pending", "classification_pending", "validation_pending"}:
        return "intake_pending"
    return "queued"


class BugflowDecisionRecord(BaseModel):
    decision_id: str
    report_ids: list[str] = Field(default_factory=list)
    title: str = ""
    summary: str = ""
    old_expectation: str = ""
    new_decision: str = ""
    approved: bool = True
    created_at: str = Field(default_factory=utc_now)


class BugflowIntake(BaseModel):
    title: str = ""
    description: str = ""
    steps_to_reproduce: list[str] = Field(default_factory=list)
    expected_behavior: str = ""
    actual_behavior: str = ""
    affected_area: str = ""
    severity: str = "major"
    candidate_category: str = ""
    candidate_decision: str = ""
    summary: str = ""
    complete: bool = False


class BugflowRepoEntry(BaseModel):
    repo_name: str
    repo_path: str
    branch_name: str = ""
    head_commit: str = ""
    last_pushed_commit: str = ""
    last_push_at: str | None = None
    touched: bool = False


class BugflowRepoStatus(BaseModel):
    branch_name: str = ""
    repos: list[BugflowRepoEntry] = Field(default_factory=list)
    has_unpushed_verified_work: bool = False
    unpromoted_lane_ids: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


class BugflowClusterSnapshot(BaseModel):
    cluster_id: str
    group_id: str = ""
    report_ids: list[str] = Field(default_factory=list)
    lane_id: str = ""
    status: str = "queued"
    current_phase: str = ""
    wait_reason: str = ""
    likely_root_cause: str = ""
    affected_files: list[str] = Field(default_factory=list)
    repo_paths: list[str] = Field(default_factory=list)
    schedule_round: int | None = None
    schedule_total_rounds: int | None = None
    attempt_number: int | None = None
    latest_rca_key: str = ""
    latest_dispatch_key: str = ""
    latest_reverify_key: str = ""
    latest_regression_key: str = ""
    latest_rca_summary: str = ""
    latest_fix_summary: str = ""
    latest_reverify_summary: str = ""
    latest_regression_summary: str = ""
    strategy_mode: str = ""
    strategy_decision_key: str = ""
    stable_bundle_key: str = ""
    stable_failure_family: str = ""
    strategy_round: int = 0
    strategy_reason: str = ""
    similar_cluster_ids: list[str] = Field(default_factory=list)
    strategy_status: str = ""
    strategy_started_at: str = ""
    strategy_decided_at: str = ""
    strategy_applied_at: str = ""
    round_plan: list[str] = Field(default_factory=list)
    last_push_at: str | None = None
    last_push_result: str = ""
    updated_at: str = Field(default_factory=utc_now)


class BugflowLaneSnapshot(BaseModel):
    lane_id: str
    lane_attempt: int = 1
    report_ids: list[str] = Field(default_factory=list)
    category: str = "bug"
    source_cluster_id: str = ""
    status: str = "planned"
    current_phase: str = ""
    lock_scope: list[str] = Field(default_factory=list)
    repo_paths: list[str] = Field(default_factory=list)
    workspace_root: str = ""
    branch_names_by_repo: dict[str, str] = Field(default_factory=dict)
    base_main_commits_by_repo: dict[str, str] = Field(default_factory=dict)
    latest_rca_keys: list[str] = Field(default_factory=list)
    latest_verify_keys: list[str] = Field(default_factory=list)
    latest_regression_keys: list[str] = Field(default_factory=list)
    latest_dispatch_key: str = ""
    latest_rca_summary: str = ""
    latest_fix_summary: str = ""
    latest_verify_summary: str = ""
    latest_regression_summary: str = ""
    issue_summary: str = ""
    modified_files: list[str] = Field(default_factory=list)
    verification_actor: str = ""
    promotion_status: str = ""
    promotion_attempt: int = 0
    supersedes_lane_id: str = ""
    wait_reason: str = ""
    execution_state: str = ""
    execution_nonce: str = ""
    execution_kind: str = ""
    execution_owner: str = ""
    execution_started_at: str = ""
    last_progress_at: str = ""
    execution_failure_kind: str = ""
    execution_failure_reason: str = ""
    observation_payload: dict[str, Any] | None = None
    implementation_result: dict[str, Any] | None = None
    test_result: dict[str, Any] | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class BugflowProofRecord(BaseModel):
    report_id: str
    stage: str
    storage_stage: str = ""
    bundle: EvidenceBundle = Field(default_factory=EvidenceBundle)
    bundle_url: str = ""
    primary_artifact_url: str = ""
    created_at: str = Field(default_factory=utc_now)


class BugflowPromotionQueueSnapshot(BaseModel):
    promoting_lane_id: str = ""
    pending_lane_ids: list[str] = Field(default_factory=list)
    lock_owner: str = ""
    status_text: str = ""
    execution_state: str = ""
    execution_nonce: str = ""
    execution_kind: str = ""
    execution_owner: str = ""
    execution_started_at: str = ""
    last_progress_at: str = ""
    execution_failure_kind: str = ""
    execution_failure_reason: str = ""
    updated_at: str = Field(default_factory=utc_now)


class BugflowReportSnapshot(BaseModel):
    report_id: str
    root_message_ts: str
    thread_ts: str
    root_message_text: str = ""
    title: str = ""
    category: str = ""
    severity: str = ""
    status: str = "intake_pending"
    cluster_id: str = ""
    lane_id: str = ""
    current_step: str = ""
    summary: str = ""
    validation_summary: str = ""
    decision_id: str = ""
    promotion_status: str = ""
    pending_retriage_for_lane: str = ""
    thread_status: str = ""
    ui_involved: bool = False
    evidence_modes: list[str] = Field(default_factory=list)
    expected_behavior: str = ""
    actual_behavior: str = ""
    affected_area: str = ""
    interview_output: str = ""
    classification_summary: str = ""
    latest_proof_key: str = ""
    terminal_proof_key: str = ""
    terminal_proof_summary: str = ""
    terminal_notice_sent_for_key: str = ""
    strategy_mode: str = ""
    strategy_decision_key: str = ""
    strategy_reason: str = ""
    strategy_round: int = 0
    stable_failure_family: str = ""
    latest_failure_bundle_key: str = ""
    latest_strategy_notice_key: str = ""
    latest_execution_notice_key: str = ""
    strategy_required_evidence_modes: list[str] = Field(default_factory=list)
    attempts_used: int = 0
    max_attempts: int = 0
    last_failed_lane_id: str = ""
    last_failure_kind: str = ""
    last_failure_reason: str = ""
    terminal_reason_kind: str = ""
    terminal_reason_summary: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    decision: BugflowDecisionRecord | None = None
    validation_verdict: dict[str, Any] | None = None
    detail_timeline: list[dict[str, Any]] = Field(default_factory=list)
    observation_verdicts: list[dict[str, Any]] = Field(default_factory=list)
    fix_attempts: list[dict[str, Any]] = Field(default_factory=list)
    cluster: dict[str, Any] | None = None


class BugflowQueueSnapshot(BaseModel):
    source_feature_id: str = ""
    dashboard_url: str = ""
    health: str = "idle"
    active_step: str = ""
    active_report_id: str = ""
    active_cluster_id: str = ""
    active_lane_ids: list[str] = Field(default_factory=list)
    verified_pending_promotion_ids: list[str] = Field(default_factory=list)
    promoting_lane_id: str = ""
    promotion_status_text: str = ""
    active_round: int | None = None
    total_rounds: int | None = None
    active_attempt: int | None = None
    counts: dict[str, int] = Field(default_factory=default_counts)
    pending_retriage_ids: list[str] = Field(default_factory=list)
    blocked_ids: list[str] = Field(default_factory=list)
    stalled_lane_ids: list[str] = Field(default_factory=list)
    recovering_lane_ids: list[str] = Field(default_factory=list)
    strategy_pending_cluster_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    cluster_ids: list[str] = Field(default_factory=list)
    lane_ids: list[str] = Field(default_factory=list)
    status_text: str = ""
    last_transition_at: str = Field(default_factory=utc_now)


def parse_model(raw: str | None, model_type: type[BaseModel], *, default: BaseModel | None = None) -> BaseModel | None:
    if not raw:
        return default
    try:
        return model_type.model_validate_json(raw)
    except Exception:
        return default


def compute_counts(reports: list[BugflowReportSnapshot]) -> dict[str, int]:
    counts = default_counts()
    for report in reports:
        lane = lane_for_status(report.status)
        counts[lane] = counts.get(lane, 0) + 1
    return counts
