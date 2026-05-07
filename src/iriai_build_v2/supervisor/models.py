from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FailureClass(str, Enum):
    HEALTHY_PROGRESS = "healthy_progress"
    NORMAL_PRODUCT_REPAIR = "normal_product_repair"
    DETERMINISTIC_UNBLOCK = "deterministic_unblock"
    PIPELINE_BUG_SUSPECTED = "pipeline_bug_suspected"
    OPERATOR_REQUIRED = "operator_required"
    WATCH_ONLY = "watch_only"
    SAFE_RESTART_CANDIDATE = "safe_restart_candidate"


class ActionLevel(str, Enum):
    OBSERVE = "observe"
    DIGEST = "digest"
    RECOMMEND = "recommend"
    ACT_GUARDED = "act_guarded"
    STOP_ESCALATE = "stop/escalate"


class SupervisorMode(str, Enum):
    READ_ONLY = "read_only"
    GUARDED = "guarded"


class ArtifactRecord(BaseModel):
    id: int | None = None
    key: str
    value: Any
    created_at: datetime | None = None
    sha256: str | None = None

    @property
    def citation(self) -> str:
        return f"artifact:{self.key}" if self.id is None else f"artifact:{self.key} id={self.id}"


class EventRecord(BaseModel):
    id: int | None = None
    event_type: str
    source: str = ""
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None

    @property
    def citation(self) -> str:
        return f"event:{self.event_type}" if self.id is None else f"event:{self.id}"


class FeatureSnapshot(BaseModel):
    feature_id: str
    name: str = ""
    slug: str = ""
    workflow_name: str = ""
    workspace_id: str = ""
    phase: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CurrentWorkflowSnapshot(BaseModel):
    """Best-effort live workflow cursor independent of historical artifacts."""

    group_idx: int | None = None
    retry: int | None = None
    phase: str = ""
    state: str = ""
    source: str = ""
    active_agents: list[str] = Field(default_factory=list)
    queued_agents: list[str] = Field(default_factory=list)
    latest_event_id: int | None = None
    latest_artifact_id: int | None = None
    citations: list[str] = Field(default_factory=list)


class BridgeProbe(BaseModel):
    dashboard_url: str | None = None
    ok: bool = False
    status: dict[str, Any] = Field(default_factory=dict)
    log_cursor: int = 0
    log_lines: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def process_state(self) -> str:
        state = self.status.get("state") or self.status.get("process_state")
        if isinstance(state, str) and state:
            return state
        if not self.ok:
            return "unreachable"
        running = self.status.get("running")
        if running is False:
            return "stopped"
        return "running"


class GitPathFact(BaseModel):
    path: str
    reason: str
    status: str = ""


class WorktreeProbe(BaseModel):
    root: str
    ok: bool = True
    branch: str | None = None
    dirty_paths: list[GitPathFact] = Field(default_factory=list)
    embedded_git_paths: list[str] = Field(default_factory=list)
    gitlinks: list[str] = Field(default_factory=list)
    forbidden_paths: list[GitPathFact] = Field(default_factory=list)
    pending_paths: list[str] = Field(default_factory=list)
    proposed_paths: list[str] = Field(default_factory=list)
    unwritable_paths: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SupervisorObservation(BaseModel):
    feature_id: str
    phase: str = ""
    observed_at: datetime = Field(default_factory=utc_now)
    event_cursor: int = 0
    next_event_cursor: int = 0
    artifact_cursor: int = 0
    next_artifact_cursor: int = 0
    bridge_log_cursor: int = 0
    cursor: int = 0
    next_cursor: int = 0
    feature: FeatureSnapshot | None = None
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    bridge: BridgeProbe | None = None
    current: CurrentWorkflowSnapshot | None = None
    worktrees: list[WorktreeProbe] = Field(default_factory=list)
    query_labels: list[str] = Field(default_factory=list)

    def latest_artifacts(self, prefix: str) -> list[ArtifactRecord]:
        return [artifact for artifact in self.artifacts if artifact.key.startswith(prefix)]


class ClassificationResult(BaseModel):
    feature_id: str
    group_idx: int | None = None
    retry: int | None = None
    phase: str = ""
    observed_at: datetime = Field(default_factory=utc_now)
    classification: FailureClass
    confidence: float = Field(ge=0.0, le=1.0)
    facts: dict[str, Any] = Field(default_factory=dict)
    inference: str
    recommended_action: ActionLevel
    false_positive_checks: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class EvidencePacket(ClassificationResult):
    """Compact payload handed to the supervisor agent/action layer."""


class SupervisorSeedPacket(BaseModel):
    """Initial deterministic hint handed to the agent investigator."""

    feature_id: str
    created_at: datetime = Field(default_factory=utc_now)
    packet: EvidencePacket


class SupervisorInvestigationRequest(BaseModel):
    """Bounded read-only evidence request from the supervisor agent."""

    reason: str = ""
    artifact_keys: list[str] = Field(default_factory=list)
    artifact_prefixes: list[str] = Field(default_factory=list)
    artifact_ids: list[int] = Field(default_factory=list)
    artifact_after_id: int | None = None
    event_after_id: int | None = None
    event_limit: int = 50
    include_bridge: bool = False
    include_worktrees: bool = False
    sql: list[str] = Field(default_factory=list)


class SupervisorEvidenceBundle(BaseModel):
    """Read-only evidence returned to the agent investigator."""

    request: SupervisorInvestigationRequest
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    bridge: BridgeProbe | None = None
    worktrees: list[WorktreeProbe] = Field(default_factory=list)
    sql_results: list[dict[str, Any]] = Field(default_factory=list)
    rejected_sql: list[dict[str, str]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SupervisorAssessment(BaseModel):
    """Agent-authored status/root-cause assessment."""

    status: str
    message: str
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    recommended_action: ActionLevel = ActionLevel.OBSERVE
    proposed_action: str | None = None


class SupervisorAgentAssessmentRecord(BaseModel):
    kind: str = "supervisor-agent-assessment"
    feature_id: str
    cursor: int
    created_at: datetime = Field(default_factory=utc_now)
    question: str | None = None
    slack_channel: str | None = None
    slack_thread_ts: str | None = None
    slack_user: str | None = None
    seed: SupervisorSeedPacket
    evidence_bundles: list[SupervisorEvidenceBundle] = Field(default_factory=list)
    assessment: SupervisorAssessment
    fallback: bool = False


class SupervisorDecision(BaseModel):
    kind: str = "supervisor-decision"
    feature_id: str
    cursor: int
    created_at: datetime = Field(default_factory=utc_now)
    observation_key: str | None = None
    packet: EvidencePacket


class SupervisorActionStatus(str, Enum):
    PLANNED = "planned"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class SupervisorActionRecord(BaseModel):
    kind: str = "supervisor-action"
    feature_id: str
    cursor: int
    action: str
    mode: SupervisorMode
    status: SupervisorActionStatus
    created_at: datetime = Field(default_factory=utc_now)
    reason: str
    packet: EvidencePacket | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


def _artifact_key_cursor_token(
    cursor: int,
    *,
    event_cursor: int | None = None,
    artifact_cursor: int | None = None,
    bridge_log_cursor: int | None = None,
    observed_at: datetime | None = None,
) -> str:
    if event_cursor is None and artifact_cursor is None and bridge_log_cursor is None:
        return str(cursor)
    timestamp = (
        (observed_at or utc_now())
        .astimezone(timezone.utc)
        .strftime("%Y%m%dT%H%M%S%fZ")
    )
    return (
        f"e{event_cursor if event_cursor is not None else cursor}:"
        f"a{artifact_cursor if artifact_cursor is not None else cursor}:"
        f"b{bridge_log_cursor if bridge_log_cursor is not None else 0}:"
        f"{timestamp}"
    )


def observation_key(
    feature_id: str,
    cursor: int,
    *,
    event_cursor: int | None = None,
    artifact_cursor: int | None = None,
    bridge_log_cursor: int | None = None,
    observed_at: datetime | None = None,
) -> str:
    token = _artifact_key_cursor_token(
        cursor,
        event_cursor=event_cursor,
        artifact_cursor=artifact_cursor,
        bridge_log_cursor=bridge_log_cursor,
        observed_at=observed_at,
    )
    return f"supervisor-observation:{feature_id}:{token}"


def decision_key(
    feature_id: str,
    cursor: int,
    *,
    event_cursor: int | None = None,
    artifact_cursor: int | None = None,
    bridge_log_cursor: int | None = None,
    observed_at: datetime | None = None,
) -> str:
    token = _artifact_key_cursor_token(
        cursor,
        event_cursor=event_cursor,
        artifact_cursor=artifact_cursor,
        bridge_log_cursor=bridge_log_cursor,
        observed_at=observed_at,
    )
    return f"supervisor-decision:{feature_id}:{token}"


def assessment_key(
    feature_id: str,
    cursor: int,
    *,
    event_cursor: int | None = None,
    artifact_cursor: int | None = None,
    bridge_log_cursor: int | None = None,
    observed_at: datetime | None = None,
) -> str:
    token = _artifact_key_cursor_token(
        cursor,
        event_cursor=event_cursor,
        artifact_cursor=artifact_cursor,
        bridge_log_cursor=bridge_log_cursor,
        observed_at=observed_at,
    )
    return f"supervisor-agent-assessment:{feature_id}:{token}"


def action_key(feature_id: str, cursor: int, action: str, suffix: str) -> str:
    return f"supervisor-action:{feature_id}:{cursor}:{action}:{suffix}"
