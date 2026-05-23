from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

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
    STALE_CODEX_INVOCATION = "stale_codex_invocation"


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
    value: Any = ""
    created_at: datetime | None = None
    sha256: str | None = None
    stored_bytes: int | None = None
    value_preview: str | None = None
    summary_only: bool = False

    @property
    def citation(self) -> str:
        return f"artifact:{self.key}" if self.id is None else f"artifact:{self.key} id={self.id}"


class ArtifactEvidenceSummary(BaseModel):
    """Compact artifact index entry handed to the supervisor agent."""

    id: int | None = None
    key: str
    citation: str
    created_at: datetime | None = None
    size_chars: int = 0
    sha256: str | None = None
    status: str | None = None
    approved: bool | None = None
    route: str | None = None
    reason: str | None = None
    summary: str = ""
    concerns: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    path_snippets: list[str] = Field(default_factory=list)
    chunk_refs: list[str] = Field(default_factory=list)
    detail_available: bool = True


class ArtifactEvidenceChunk(BaseModel):
    """Bounded raw artifact slice retrievable by id and chunk index."""

    artifact_id: int
    key: str
    citation: str
    chunk_ref: str
    chunk_index: int
    char_start: int
    char_end: int
    total_chars: int
    text: str


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
    truncated_log_line_count: int = 0
    truncated_error_count: int = 0

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


class StaleCodexInvocation(BaseModel):
    """Evidence that one Codex subprocess is alive but no longer progressing."""

    actor: str = ""
    invocation_id: str | None = None
    group_idx: int | None = None
    retry: int | None = None
    task_id: str | None = None
    pid: int
    parent_pid: int | None = None
    child_pids: list[int] = Field(default_factory=list)
    cpu_percent: float | None = None
    mem_percent: float | None = None
    command: str = ""
    trace_path: str = ""
    output_path: str | None = None
    elapsed_seconds: float = 0.0
    idle_seconds: float = 0.0
    liveness_timeout_seconds: int = 600
    threshold_seconds: int = 1800
    stdout_events: int = 0
    stderr_lines: int = 0
    output_bytes: int = 0
    last_event: str = ""
    last_item: str = ""
    heartbeat_count: int = 0
    stable_heartbeat_count: int = 0
    last_activity_at: datetime | None = None
    evidence_token: str = ""
    citations: list[str] = Field(default_factory=list)


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
    control_plane_snapshot: dict[str, Any] | None = None
    # Slice 10c-2: the TYPED control-plane snapshot (doc 10 § "Refactoring
    # Steps" step 3). `control_plane` carries the bounded typed
    # `ControlPlaneSnapshot` when typed rows are present; `evidence_mode`
    # records which contract the classifier should treat as PRIMARY.
    # `typed` -> typed failure/route decisions are PRIMARY (the legacy
    # artifact classifiers are skipped); `legacy_fallback` / `mixed` /
    # `""` -> the legacy artifact classifiers run as the fallback. The field
    # is `Any` (not the imported `ControlPlaneSnapshot` type) so this module
    # stays a leaf with no `workflows.develop.execution` import edge — the
    # classifier resolves the typed shape by attribute access.
    control_plane: Any = None
    evidence_mode: str = ""
    worktrees: list[WorktreeProbe] = Field(default_factory=list)
    stale_codex_invocations: list[StaleCodexInvocation] = Field(default_factory=list)
    query_labels: list[str] = Field(default_factory=list)

    def latest_artifacts(self, prefix: str) -> list[ArtifactRecord]:
        return [artifact for artifact in self.artifacts if artifact.key.startswith(prefix)]


class SupervisorArtifactRef(BaseModel):
    id: int | None = None
    key: str
    citation: str
    created_at: datetime | None = None
    stored_bytes: int | None = None
    summary_only: bool = False


class SupervisorBridgeDigest(BaseModel):
    ok: bool = False
    process_state: str = ""
    status: dict[str, Any] = Field(default_factory=dict)
    log_cursor: int = 0
    recent_log_lines: list[str] = Field(default_factory=list)
    recent_errors: list[str] = Field(default_factory=list)
    truncated_log_line_count: int = 0
    truncated_error_count: int = 0


class SupervisorDbPressureDigest(BaseModel):
    free_bytes: int | None = None
    total_bytes: int | None = None
    used_bytes: int | None = None
    path: str = ""
    pressure: bool = False


class SupervisorObservationDigest(BaseModel):
    """Compact persisted supervisor observation.

    The full in-memory ``SupervisorObservation`` may contain raw evidence for
    classification. Rows written as ``supervisor-observation:*`` must remain
    bounded and reference-based.
    """

    kind: str = "supervisor-observation-digest"
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
    current: CurrentWorkflowSnapshot | None = None
    artifact_refs: list[SupervisorArtifactRef] = Field(default_factory=list)
    event_refs: list[str] = Field(default_factory=list)
    bridge: SupervisorBridgeDigest | None = None
    stale_codex_invocations: list[dict[str, Any]] = Field(default_factory=list)
    db_pressure: SupervisorDbPressureDigest | None = None
    truncated: bool = False
    source_observation_artifact_count: int = 0
    source_observation_event_count: int = 0


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
    artifact_chunks: list[str] = Field(default_factory=list)
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
    artifact_summaries: list[ArtifactEvidenceSummary] = Field(default_factory=list)
    artifact_chunks: list[ArtifactEvidenceChunk] = Field(default_factory=list)
    omitted_detail_refs: list[str] = Field(default_factory=list)
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
    fallback_reason: str | None = None
    prompt_chars: int = 0
    round_count: int = 0
    evidence_artifact_count: int = 0
    evidence_summary_count: int = 0
    omitted_detail_refs: list[str] = Field(default_factory=list)
    evidence_mode: str = ""
    tool_names_used: list[str] = Field(default_factory=list)
    session_epoch: str | None = None
    session_scope: str | None = None


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
    evidence_requests: list[dict[str, Any]] = Field(default_factory=list)
    evidence_artifact_refs: list[str] = Field(default_factory=list)
    evidence_chunk_refs: list[str] = Field(default_factory=list)
    assessment: SupervisorAssessment
    fallback: bool = False
    fallback_reason: str | None = None
    prompt_chars: int = 0
    round_count: int = 0
    evidence_artifact_count: int = 0
    evidence_summary_count: int = 0
    omitted_detail_refs: list[str] = Field(default_factory=list)
    evidence_mode: str = ""
    tool_names_used: list[str] = Field(default_factory=list)
    session_epoch: str | None = None
    session_scope: str | None = None


class SupervisorThreadContextRecord(BaseModel):
    kind: str = "supervisor-thread-context"
    feature_id: str
    created_at: datetime = Field(default_factory=utc_now)
    question: str | None = None
    slack_channel: str | None = None
    slack_thread_ts: str
    slack_user: str | None = None
    source_assessment_key: str
    assessment_status: str = ""
    answered_group: int | None = None
    live_group_at_answer: int | None = None
    fallback: bool = False
    citations: list[str] = Field(default_factory=list)


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


# ── Slice 10d: Slack dedupe / suppression typed contract ────────────────────
#
# doc 10 ("Supervisor And Dashboard Integration") § "Slack Dedupe And
# Suppression" is the SPEC for the two models below. They are the typed
# contract the `SupervisorDigestDedupeStore` (``supervisor/digest_dedupe.py``)
# uses to decide whether a background Slack digest is sent or suppressed.
#
# The shapes are transcribed VERBATIM from doc 10 § "Slack Dedupe And
# Suppression". `SupervisorDigestKey` is the material state of a digest — the
# stable JSON digest over it is the dedupe key; evidence ids alone never create
# a new key (doc 10: "evidence ids alone do not create a new Slack message
# unless classification, route, action, active attempt, queue status, or
# failure signature changes").


class SupervisorDigestKey(BaseModel):
    """The material state that identifies one background Slack digest.

    doc 10 § "Slack Dedupe And Suppression": the stable JSON digest over this
    model is the dedupe key. Two digests with the same key are "the same
    material state" — a second background Slack message for the same key inside
    the cooldown is suppressed and coalesced.

    ``failure_signature_hashes`` / ``merge_queue_statuses`` / ``active_attempt_
    ids`` are sorted at digest time so list ordering churn never invents a new
    key (see :func:`digest_dedupe.compute_dedupe_key`).
    """

    feature_id: str
    group_idx: int | None = None
    classification: str
    recommended_action: str
    recommended_route: str
    failure_signature_hashes: list[str] = Field(default_factory=list)
    merge_queue_statuses: list[str] = Field(default_factory=list)
    active_attempt_ids: list[int] = Field(default_factory=list)


class SupervisorDigestDecision(BaseModel):
    """The outcome of a send/suppress evaluation for one background digest.

    doc 10 § "Slack Dedupe And Suppression": every background Slack digest is
    routed through a :class:`SupervisorDigestDecision`. ``should_send`` is the
    gate; ``reason`` records why; ``suppressed_count`` carries the coalesced
    duplicate count so a later post-cooldown send can report how many identical
    digests were suppressed.
    """

    dedupe_key: str
    should_send: bool
    reason: Literal[
        "first_seen",
        "material_change",
        "operator_requested",
        "suppressed_duplicate",
        "suppressed_within_cooldown",
        "coalesced",
    ]
    suppressed_count: int = 0
    prior_digest_id: int | None = None


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


def _safe_key_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)[:120]


def thread_context_prefix(feature_id: str, thread_ts: str) -> str:
    return f"supervisor-thread-context:{feature_id}:{_safe_key_component(thread_ts)}:"


def thread_context_key(
    feature_id: str,
    thread_ts: str,
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
    return f"{thread_context_prefix(feature_id, thread_ts)}{token}"


def action_key(feature_id: str, cursor: int, action: str, suffix: str) -> str:
    return f"supervisor-action:{feature_id}:{cursor}:{action}:{suffix}"
