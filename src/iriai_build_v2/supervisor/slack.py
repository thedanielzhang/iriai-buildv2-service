"""Supervisor Slack app wiring and natural-message routing."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import signal
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Literal, Protocol

from .actions import ActionPolicy
from .agent import SupervisorAgent
from .app import SupervisorApp
from .digest_dedupe import (
    DigestDedupeStoreError,
    SupervisorDigestDedupeStore,
    compute_dedupe_key,
)
from .models import (
    ActionLevel,
    FailureClass,
    SupervisorAgentAssessmentRecord,
    SupervisorAssessment,
    SupervisorActionRecord,
    SupervisorActionStatus,
    SupervisorDigestDecision,
    SupervisorDigestKey,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
    SupervisorMode,
    SupervisorThreadContextRecord,
    action_key,
    assessment_key,
    thread_context_key,
    thread_context_prefix,
)
from .slack_blocks import (
    SupervisorStaleInvocationCard,
    build_resolved_notice_blocks,
    build_status_blocks,
)
from ..interfaces.slack.streamer import (
    _format_thinking,
    _format_tool_result,
    _format_tool_use,
)

logger = logging.getLogger(__name__)

SupervisorRouteKind = Literal[
    "supervisor_question",
    "supervisor_action_request",
    "workflow_instruction",
    "ignore",
]
SupervisorSlackMode = Literal["multiplayer", "singleplayer"]

_QUESTION_RE = re.compile(
    r"^(how|what|why|when|where|who|is|are|did|does|do|can|could|should|would)\b",
    re.IGNORECASE,
)
_QUESTION_KEYWORDS_RE = re.compile(
    r"\b(status|stuck|wedged|healthy|looking|running|failed|failure|changed|"
    r"happened|progress|blocked|risk|restart|artifact|artifacts|revision|"
    r"revisions|revise|cycle|cycles|retry|retries|root cause|health|preflight|"
    r"commit|commits|verify|verification)\b",
    re.IGNORECASE,
)
_INFORMATION_REQUEST_RE = re.compile(
    r"^\s*(give me|show me|summarize|summarise|review|analyze|analyse|look|"
    r"dig|list|find|check|compare|explain|trace)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(restart|reboot|resume|pause|stop|patch|fix the bridge|repair the pipeline|"
    r"launch maintainer|apply)\b",
    re.IGNORECASE,
)
_DETAIL_QUESTION_RE = re.compile(
    r"\b("
    r"status|current status|health|healthy|looking|running|progress|"
    r"failure|failed|fail|root cause|why|stuck|blocked|blocker|what happened|"
    r"what changed|recent update|most recent|revision|revisions|revise|cycle|"
    r"cycles|retry|retries|fix|fixed|repair|repaired"
    r")\b",
    re.IGNORECASE,
)
_CURRENT_STATUS_RE = re.compile(
    r"\b(current status|status|health|healthy|looking|running|progress)\b",
    re.IGNORECASE,
)
_STALE_CODEX_STATUS_RE = re.compile(
    r"\b("
    r"heartbeat|codex|watchdog|liveness|alive|still alive|still running|"
    r"running|hung|stale|wedged|stuck|blocked|blocker|status|current status|"
    r"health|healthy|progress|what is happening|what's happening|what happened"
    r")\b",
    re.IGNORECASE,
)
_DEEP_HISTORY_RE = re.compile(
    r"\b("
    r"all|entire|history|historical|revision|revisions|revise|cycle|cycles|"
    r"compare|timeline|forensics|root cause|why|deep|thorough"
    r")\b",
    re.IGNORECASE,
)
_GROUP_RE = re.compile(r"\b(?:g|group)\s*[-#:]?\s*(\d{1,3})\b", re.IGNORECASE)
_ARTIFACT_ID_RE = re.compile(r"\bid=(\d+)\b")
_WORKFLOW_INSTRUCTION_RE = re.compile(
    r"\b(?:tell|ask|send|forward|route|pass)\s+(?:the\s+)?("
    r"workflow|agent|implementer|verifier|reviewer|runner|pm|designer|architect"
    r")\s+(?:to|that)\b|\blet\s+(?:the\s+)?("
    r"workflow|agent|implementer|verifier|reviewer|runner|pm|designer|architect"
    r")\s+know\b",
    re.IGNORECASE | re.DOTALL,
)
_PROGRESS_INITIAL_TEXT = "\U0001f4ad _Checking workflow evidence..._"
_PROGRESS_MIN_UPDATE_INTERVAL = 1.0


@dataclass(frozen=True)
class SupervisorSlackRoute:
    """Classified supervisor Slack message."""

    kind: SupervisorRouteKind
    text: str
    channel: str
    user: str
    feature_id: str | None = None
    dashboard_url: str | None = None
    thread_ts: str | None = None


class SupervisorSlackService(Protocol):
    """Service surface used by the Slack router.

    Production implementations can back these methods with evidence retrieval,
    an agent runtime, guarded actions, or workflow-note forwarding. Tests can
    inject a tiny fake service with the same async methods.
    """

    async def answer_question(self, route: SupervisorSlackRoute) -> str:
        ...

    async def evaluate_action_request(self, route: SupervisorSlackRoute) -> str:
        ...

    async def route_workflow_instruction(self, route: SupervisorSlackRoute) -> str:
        ...

    async def handle_stale_codex_action(self, action_id: str, value: str) -> str:
        ...


class PlaceholderSupervisorService:
    """Read-only fallback when no feature/evidence service is configured."""

    def __init__(self, *, runtime: str) -> None:
        self._runtime = runtime

    async def answer_question(self, route: SupervisorSlackRoute) -> str:
        subject = (
            f"feature `{route.feature_id}`"
            if route.feature_id
            else "the active feature"
        )
        dashboard = f"\nDashboard: {route.dashboard_url}" if route.dashboard_url else ""
        return (
            f"I can route supervisor chat for {subject}, but the evidence service "
            f"is not wired yet. Runtime: `{self._runtime}`.{dashboard}"
        )

    async def evaluate_action_request(self, route: SupervisorSlackRoute) -> str:
        return (
            "I heard the action request. Guarded supervisor actions are not "
            "enabled in this skeleton yet, so I will not mutate the workflow."
        )

    async def route_workflow_instruction(self, route: SupervisorSlackRoute) -> str:
        return (
            "I noted this as a workflow instruction and would forward it when "
            "the workflow sink is wired."
        )

    async def handle_stale_codex_action(self, action_id: str, value: str) -> str:
        return "Supervisor stale Codex actions are not wired in placeholder mode."


class SupervisorSlackProgress:
    """Single-message progress surface for supervisor replies.

    This mirrors the bridge's SlackStreamer behavior for runtime activity, but
    replaces the progress message with the final supervisor answer so natural
    Slack Q&A remains compact.
    """

    def __init__(
        self,
        *,
        adapter: Any,
        channel: str,
        thread_ts: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._channel = channel
        self._thread_ts = thread_ts
        self._message_ts: str | None = None
        self._current_status = ""
        self._flushing = False
        self._pending = False
        self._closed = False
        self._last_flush_time = 0.0

    async def start(self) -> None:
        self._message_ts = await self._adapter.post_message(
            self._channel,
            _PROGRESS_INITIAL_TEXT,
            thread_ts=self._thread_ts,
        )
        self._last_flush_time = asyncio.get_running_loop().time()

    def on_message(self, msg: Any) -> None:
        if self._closed:
            return
        status = _status_from_runtime_message(msg)
        if not status:
            return
        self._current_status = status
        self._schedule_flush()

    async def finish(self, text: str, *, blocks: list[dict[str, Any]] | None = None) -> None:
        self._closed = True
        self._current_status = ""
        self._pending = False
        if self._message_ts is None:
            await self._adapter.post_message(
                self._channel,
                text,
                thread_ts=self._thread_ts,
            )
            return
        try:
            await self._adapter.update_message(
                self._channel,
                self._message_ts,
                text=text,
                blocks=blocks,
            )
        except Exception:
            logger.debug("Failed to update supervisor progress message", exc_info=True)
            await self._adapter.post_message(
                self._channel,
                text,
                thread_ts=self._thread_ts,
            )

    def _schedule_flush(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._flushing:
            self._pending = True
            return
        loop.create_task(self._flush())

    async def _flush(self) -> None:
        self._flushing = True
        try:
            elapsed = asyncio.get_running_loop().time() - self._last_flush_time
            if elapsed < _PROGRESS_MIN_UPDATE_INTERVAL:
                await asyncio.sleep(_PROGRESS_MIN_UPDATE_INTERVAL - elapsed)
            if self._closed:
                return
            text = self._current_status
            if not text:
                return
            if self._message_ts is None:
                self._message_ts = await self._adapter.post_message(
                    self._channel,
                    text,
                    thread_ts=self._thread_ts,
                )
            else:
                await self._adapter.update_message(
                    self._channel,
                    self._message_ts,
                    text=text,
                )
            self._last_flush_time = asyncio.get_running_loop().time()
        except Exception:
            logger.exception("Failed to update supervisor progress message")
        finally:
            self._flushing = False
            if self._pending and not self._closed:
                self._pending = False
                self._schedule_flush()


def _status_from_runtime_message(msg: Any) -> str | None:
    if type(msg).__name__ != "AssistantMessage":
        return None
    status: str | None = None
    for block in getattr(msg, "content", []) or []:
        block_type = type(block).__name__
        if block_type == "ThinkingBlock":
            status = _format_thinking(str(getattr(block, "thinking", "")))
        elif block_type == "ToolUseBlock":
            status = _format_tool_use(
                str(getattr(block, "name", "")),
                getattr(block, "input", {}) or {},
            )
        elif block_type == "ToolResultBlock":
            status = _format_tool_result(
                getattr(block, "content", None),
                getattr(block, "is_error", None),
            )
    return status


def _detail_evidence_requests(
    packet,
    question: str,
) -> list[SupervisorInvestigationRequest]:
    if not _DETAIL_QUESTION_RE.search(question or ""):
        return []
    groups = _groups_for_question(packet, question)
    if not groups:
        return []
    status_only = bool(_CURRENT_STATUS_RE.search(question or "")) and not bool(
        _DEEP_HISTORY_RE.search(question or "")
    )
    artifact_ids = [] if status_only else _artifact_ids_from_packet(packet)
    current = packet.facts.get("current_workflow") if packet.facts else None
    latest_event_id = 0
    latest_artifact_id = 0
    if isinstance(current, dict):
        latest_event_id = int(current.get("latest_event_id") or 0)
        latest_artifact_id = int(current.get("latest_artifact_id") or 0)
    event_after_id = max(0, latest_event_id - 250) if latest_event_id else None
    prefixes: list[str] = []
    for group in groups[:3]:
        prefixes.extend(
            _current_status_group_prefixes(group)
            if status_only
            else _group_detail_prefixes(group)
        )
    prefixes = _dedupe_strings(prefixes)[:20]
    artifact_after_id = 0
    if status_only:
        id_floor = latest_artifact_id
        artifact_after_id = max(0, id_floor - 1_500) if id_floor else 0
    return [
        SupervisorInvestigationRequest(
            reason=(
                "Operator asked for current status; preload a compact current-material "
                "index plus exact details for the latest seed citations."
                if status_only
                else "Operator asked for failure/root-cause/revision detail; preload "
                "current-group verify, RCA, repair, route, and commit evidence."
            ),
            artifact_prefixes=prefixes,
            artifact_ids=artifact_ids,
            artifact_after_id=artifact_after_id,
            event_after_id=event_after_id,
            event_limit=60 if status_only else 200,
            include_bridge=True,
            include_worktrees=not status_only,
        )
    ]


def _groups_for_question(packet, question: str) -> list[int]:
    groups: list[int] = []
    for match in _GROUP_RE.finditer(question or ""):
        try:
            groups.append(int(match.group(1)))
        except (TypeError, ValueError):
            continue
    if groups:
        return _dedupe_ints(groups)
    current = packet.facts.get("current_workflow") if packet.facts else None
    if isinstance(current, dict) and current.get("group_idx") is not None:
        try:
            return [int(current["group_idx"])]
        except (TypeError, ValueError):
            pass
    if packet.group_idx is None:
        return []
    return [int(packet.group_idx)]


def _group_detail_prefixes(group: int) -> list[str]:
    return [
        f"dag-verify:g{group}:",
        f"dag-repair-preflight:g{group}:",
        f"dag-authority-gate:g{group}:",
        f"dag-direct-repair-route:g{group}:",
        f"dag-repair-expanded-verify:g{group}:",
        f"dag-repair-lens:g{group}:",
        f"dag-verify-rca:g{group}:",
        f"dag-repair-dispatch:g{group}:",
        f"dag-fix:g{group}:",
        f"dag-task-reconcile:g{group}:",
        f"dag-task-spec-reconcile:g{group}:",
        f"dag-task-product-reconcile:g{group}:",
        f"dag-commit-failure:g{group}:",
        f"dag-group:{group}",
        "bug-",
        "finding-ledger",
    ]


def _current_status_group_prefixes(group: int) -> list[str]:
    return [
        f"dag-verify:g{group}:",
        f"dag-repair-preflight:g{group}:",
        f"dag-authority-gate:g{group}:",
        f"dag-direct-repair-route:g{group}:",
        f"dag-repair-expanded-verify:g{group}:",
        f"dag-repair-lens:g{group}:",
        f"dag-verify-rca:g{group}:",
        f"dag-fix:g{group}:",
        f"dag-task-reconcile:g{group}:",
        f"dag-task-spec-reconcile:g{group}:",
        f"dag-task-product-reconcile:g{group}:",
        f"dag-commit-failure:g{group}:",
        f"dag-group:{group}",
    ]


def _artifact_ids_from_packet(
    packet,
) -> list[int]:
    ids: list[int] = []
    for citation in packet.citations or []:
        text = str(citation)
        for match in _ARTIFACT_ID_RE.finditer(text):
            try:
                ids.append(int(match.group(1)))
            except ValueError:
                continue
    return _dedupe_ints(ids)[:50]


def _dedupe_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _session_scope(prefix: str, value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-")
    return f"{prefix}-{safe_value or 'session'}"


def _stale_token_from_action_id(action_id: str) -> str:
    if not action_id.startswith("stale_codex_"):
        return ""
    return action_id.rsplit("_", 1)[-1]


def _stale_codex_fact(packet) -> dict[str, Any]:
    stale = packet.facts.get("stale_codex_invocation") if packet.facts else None
    return stale if isinstance(stale, dict) else {}


def _stale_codex_question(text: str) -> bool:
    return bool(_STALE_CODEX_STATUS_RE.search(text or ""))


def _format_duration(seconds: float | int | None) -> str:
    total = int(float(seconds or 0))
    if total <= 0:
        return "unknown duration"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _compact_citations(citations: list[str], *, limit: int = 4) -> str:
    values = [str(item) for item in citations if str(item).strip()]
    if not values:
        return "none"
    shown = values[:limit]
    suffix = f"; +{len(values) - limit} more" if len(values) > limit else ""
    return "; ".join(shown) + suffix


def _stale_codex_direct_message(packet, *, mode: SupervisorMode) -> str:
    stale = _stale_codex_fact(packet)
    actor = str(stale.get("actor") or "unknown actor")
    pid = stale.get("pid")
    children = stale.get("child_pids") or []
    group = stale.get("group_idx", packet.group_idx)
    retry = stale.get("retry", packet.retry)
    task = str(stale.get("task_id") or "unknown task")
    elapsed = _format_duration(stale.get("elapsed_seconds"))
    idle = _format_duration(stale.get("idle_seconds"))
    stdout_events = stale.get("stdout_events")
    stderr_lines = stale.get("stderr_lines")
    output_bytes = stale.get("output_bytes")
    last_event = str(stale.get("last_event") or "unknown")
    last_item = str(stale.get("last_item") or "unknown")
    stable_count = stale.get("stable_heartbeat_count")
    trace = str(stale.get("trace_path") or "unknown")
    action = (
        "The reset card can kill only this validated Codex process tree."
        if mode == SupervisorMode.GUARDED
        else "Supervisor is read-only, so it should present the exact PID/tree for operator reset."
    )
    return (
        "Yes, the heartbeat is still alive, but that is the warning sign here: "
        "this is heartbeat-only liveness, not evidence of useful progress. "
        f"The supervisor classifies `{actor}` as `stale_codex_invocation` for "
        f"G{group} retry {retry} task `{task}` after {elapsed} elapsed and {idle} idle. "
        f"The repeated heartbeat evidence is stable: pid `{pid}`, children `{children}`, "
        f"`stdout_events={stdout_events}`, `stderr_lines={stderr_lines}`, "
        f"`output_bytes={output_bytes}`, `last_event={last_event}`, "
        f"`last_item={last_item}`, stable heartbeat count `{stable_count}`. "
        f"Current action: reset the stale Codex invocation, not observe it as healthy progress. "
        f"{action} Trace: `{trace}`. Citations: {_compact_citations(packet.citations)}."
    )


def _stale_codex_direct_assessment(
    packet,
    *,
    message: str,
    mode: SupervisorMode,
) -> SupervisorAssessment:
    stale = _stale_codex_fact(packet)
    actor = str(stale.get("actor") or "unknown actor")
    pid = stale.get("pid")
    children = stale.get("child_pids") or []
    facts = [
        (
            f"Current packet is classified as stale_codex_invocation for {actor} "
            f"on group {stale.get('group_idx', packet.group_idx)} retry "
            f"{stale.get('retry', packet.retry)}."
        ),
        (
            f"Process evidence: pid={pid}, child_pids={children}, "
            f"elapsed={stale.get('elapsed_seconds')}, idle={stale.get('idle_seconds')}, "
            f"cpu_percent={stale.get('cpu_percent')}."
        ),
        (
            f"Heartbeat signature is stable with stdout_events={stale.get('stdout_events')}, "
            f"stderr_lines={stale.get('stderr_lines')}, output_bytes={stale.get('output_bytes')}, "
            f"last_event={stale.get('last_event')}, last_item={stale.get('last_item')}."
        ),
    ]
    return SupervisorAssessment(
        status=FailureClass.STALE_CODEX_INVOCATION.value,
        message=message,
        facts=facts,
        inferences=[
            "Heartbeat-only liveness should not be presented as healthy workflow progress.",
            "The appropriate unblock is an exact-process Codex reset or guarded reset card.",
        ],
        citations=list(packet.citations or []),
        confidence=packet.confidence,
        recommended_action=packet.recommended_action,
        proposed_action=(
            "kill_stale_codex"
            if mode == SupervisorMode.GUARDED
            else "operator_reset_stale_codex"
        ),
        evidence_mode="deterministic_current_state",
        tool_names_used=["supervisor-stale-codex-detector"],
    )


_DIGEST_DECISION_PREFIX = "supervisor-slack-digest-dedupe"
_DIGEST_CITATION_LIMIT = 12
_DIGEST_PENDING_STALE_INTERVAL_SQL = "5 minutes"


class SupervisorSlackDigestDecisionStore:
    """Supervisor-table-backed digest send/suppress state and audit log."""

    def __init__(self, *, pool: Any, feature_id: str) -> None:
        self._pool = pool
        self._feature_id = feature_id

    @property
    def prefix(self) -> str:
        return f"{_DIGEST_DECISION_PREFIX}:{self._feature_id}:"

    async def delivered_duplicate_exists(
        self,
        *,
        dedupe_key: str,
        signature_hash: str,
        semantic_signature_hash: str,
        semantic_dedupe: bool,
    ) -> bool:
        rows = await self._fetch(
            """
            SELECT dedupe_key, signature_hash, semantic_signature_hash, status
            FROM supervisor_slack_digest_state
            WHERE feature_id = $1
              AND status = 'delivered'
	              AND (
	                    dedupe_key = $2
	                    OR signature_hash = $3
	                    OR (
	                        $4::boolean
	                        AND COALESCE((payload->>'semantic_dedupe')::boolean, FALSE)
	                        AND semantic_signature_hash = $5
	                    )
	              )
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            self._feature_id,
            dedupe_key,
            signature_hash,
            bool(semantic_dedupe),
            semantic_signature_hash,
        )
        return bool(rows)

    async def pending_duplicate_exists(
        self,
        *,
        dedupe_key: str,
        signature_hash: str,
        semantic_signature_hash: str,
        semantic_dedupe: bool,
    ) -> bool:
        rows = await self._fetch(
            f"""
            SELECT dedupe_key, signature_hash, semantic_signature_hash, status
            FROM supervisor_slack_digest_state
            WHERE feature_id = $1
              AND (
                    status = 'suppressed'
                    OR (
                        status = 'pending'
                        AND updated_at > NOW() - INTERVAL '{_DIGEST_PENDING_STALE_INTERVAL_SQL}'
                    )
              )
              AND (
                    dedupe_key = $2
                    OR signature_hash = $3
                    OR (
                        $4::boolean
                        AND COALESCE((payload->>'semantic_dedupe')::boolean, FALSE)
                        AND semantic_signature_hash = $5
                    )
              )
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            self._feature_id,
            dedupe_key,
            signature_hash,
            bool(semantic_dedupe),
            semantic_signature_hash,
        )
        return bool(rows)

    async def record_attempt(
        self,
        *,
        dedupe_key: str,
        snapshot_version: str,
        signature_hash: str,
        semantic_signature_hash: str,
        reason: str,
        packet: Any,
        semantic_dedupe: bool = False,
        channel: str | None = None,
        thread_ts: str | None = None,
    ) -> bool:
        payload = {
            "kind": "supervisor-slack-digest-dedupe",
            "feature_id": self._feature_id,
            "dedupe_key": dedupe_key,
            "snapshot_version": snapshot_version,
            "signature_hash": signature_hash,
            "semantic_signature_hash": semantic_signature_hash,
            "semantic_dedupe": bool(semantic_dedupe),
            "decision": "attempt",
            "reason": reason,
            "classification": packet.classification.value,
            "recommended_action": packet.recommended_action.value,
            "group_idx": packet.group_idx,
            "retry": packet.retry,
            "citations": _bounded_digest_citations(packet),
        }
        try:
            inserted = await self._fetchrow(
                f"""
            WITH active_duplicate AS (
                SELECT 1
                FROM supervisor_slack_digest_state
                WHERE feature_id = $1
                  AND (
                        status IN ('delivered', 'suppressed')
                        OR (
                            status = 'pending'
                            AND updated_at > NOW() - INTERVAL '{_DIGEST_PENDING_STALE_INTERVAL_SQL}'
                        )
                  )
                  AND (
                        dedupe_key = $2
                        OR signature_hash = $4
                        OR (
                            $15::boolean
                            AND COALESCE((payload->>'semantic_dedupe')::boolean, FALSE)
                            AND semantic_signature_hash = $5
                        )
                  )
                LIMIT 1
            ),
            stale_pending AS (
                SELECT id
                FROM supervisor_slack_digest_state
                WHERE feature_id = $1
                  AND status = 'pending'
                  AND updated_at <= NOW() - INTERVAL '{_DIGEST_PENDING_STALE_INTERVAL_SQL}'
                  AND (
                        dedupe_key = $2
                        OR signature_hash = $4
                        OR (
                            $15::boolean
                            AND COALESCE((payload->>'semantic_dedupe')::boolean, FALSE)
                            AND semantic_signature_hash = $5
                        )
                  )
                ORDER BY updated_at ASC
                LIMIT 1
                FOR UPDATE
            ),
            reclaimed AS (
                UPDATE supervisor_slack_digest_state
                SET dedupe_key = $2,
                    snapshot_version = $3,
                    signature_hash = $4,
                    semantic_signature_hash = $5,
                    classification = $6,
                    recommended_action = $7,
                    group_idx = $8,
                    retry = $9,
                    status = 'pending',
                    channel = $10,
                    thread_ts = $11,
                    message_ts = NULL,
                    send_reason = $12,
                    suppress_reason = '',
                    citations = $13::jsonb,
                    payload = $14::jsonb,
                    updated_at = NOW(),
                    delivered_at = NULL
                WHERE id IN (SELECT id FROM stale_pending)
                  AND NOT EXISTS (SELECT 1 FROM active_duplicate)
                RETURNING dedupe_key
            ),
            revived AS (
                UPDATE supervisor_slack_digest_state
                SET status = 'pending',
                    snapshot_version = $3,
                    signature_hash = $4,
                    semantic_signature_hash = $5,
                    classification = $6,
                    recommended_action = $7,
                    group_idx = $8,
                    retry = $9,
                    channel = $10,
                    thread_ts = $11,
                    message_ts = NULL,
                    send_reason = $12,
                    suppress_reason = '',
                    citations = $13::jsonb,
                    payload = $14::jsonb,
                    updated_at = NOW(),
                    delivered_at = NULL
                WHERE feature_id = $1
                  AND dedupe_key = $2
                  AND status = 'failed'
                  AND NOT EXISTS (SELECT 1 FROM active_duplicate)
                  AND NOT EXISTS (SELECT 1 FROM reclaimed)
                RETURNING dedupe_key
            ),
            inserted AS (
                INSERT INTO supervisor_slack_digest_state (
                    feature_id, dedupe_key, snapshot_version, signature_hash,
                    semantic_signature_hash, classification, recommended_action,
                    group_idx, retry, status, channel, thread_ts, send_reason,
                    suppress_reason, citations, payload, updated_at
                )
                SELECT
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10, $11, $12,
                    '', $13::jsonb, $14::jsonb, NOW()
                WHERE NOT EXISTS (SELECT 1 FROM active_duplicate)
                  AND NOT EXISTS (SELECT 1 FROM reclaimed)
                  AND NOT EXISTS (SELECT 1 FROM revived)
                ON CONFLICT DO NOTHING
                RETURNING dedupe_key
            )
            SELECT dedupe_key FROM reclaimed
            UNION ALL
            SELECT dedupe_key FROM revived
            UNION ALL
            SELECT dedupe_key FROM inserted
            LIMIT 1
                """,
                self._feature_id,
                dedupe_key,
                snapshot_version,
                signature_hash,
                semantic_signature_hash,
                packet.classification.value,
                packet.recommended_action.value,
                packet.group_idx,
                packet.retry,
                channel or "",
                thread_ts,
                reason,
                json.dumps(_bounded_digest_citations(packet)),
                json.dumps(payload, sort_keys=True, default=str),
                bool(semantic_dedupe),
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                return False
            raise
        if inserted is None:
            return False
        try:
            await self._record_audit(
                decision="attempt",
                reason=reason,
                dedupe_key=dedupe_key,
                snapshot_version=snapshot_version,
                signature_hash=signature_hash,
                semantic_signature_hash=semantic_signature_hash,
                channel=channel,
                thread_ts=thread_ts,
                message_ts=None,
                packet=packet,
            )
        except Exception:
            logger.debug(
                "Failed to audit supervisor digest attempt after durable claim",
                exc_info=True,
            )
        return True

    async def record_suppressed(
        self,
        *,
        dedupe_key: str,
        snapshot_version: str,
        signature_hash: str,
        semantic_signature_hash: str,
        reason: str,
        packet: Any,
        channel: str | None = None,
        thread_ts: str | None = None,
    ) -> None:
        await self._execute(
            """
            UPDATE supervisor_slack_digest_state
            SET status = 'suppressed',
                suppress_reason = $3,
                updated_at = NOW()
            WHERE feature_id = $1
              AND dedupe_key = $2
              AND status = 'pending'
            """,
            self._feature_id,
            dedupe_key,
            reason[:500],
        )
        await self._record_audit(
            decision="suppress",
            reason=reason,
            dedupe_key=dedupe_key,
            snapshot_version=snapshot_version,
            signature_hash=signature_hash,
            semantic_signature_hash=semantic_signature_hash,
            channel=channel,
            thread_ts=thread_ts,
            message_ts=None,
            packet=packet,
        )

    async def record_delivered(
        self,
        *,
        dedupe_key: str,
        snapshot_version: str,
        signature_hash: str,
        semantic_signature_hash: str,
        message_ts: str,
        packet: Any,
        channel: str | None = None,
        thread_ts: str | None = None,
        reason: str = "delivered",
    ) -> None:
        await self._execute(
            """
            UPDATE supervisor_slack_digest_state
            SET status = 'delivered',
                channel = $3,
                thread_ts = $4,
                message_ts = $5,
                updated_at = NOW(),
                delivered_at = NOW()
            WHERE feature_id = $1 AND dedupe_key = $2
            """,
            self._feature_id,
            dedupe_key,
            channel or "",
            thread_ts,
            message_ts,
        )
        await self._record_audit(
            decision="delivered",
            reason=reason,
            dedupe_key=dedupe_key,
            snapshot_version=snapshot_version,
            signature_hash=signature_hash,
            semantic_signature_hash=semantic_signature_hash,
            channel=channel,
            thread_ts=thread_ts,
            message_ts=message_ts,
            packet=packet,
        )

    async def record_failed(
        self,
        *,
        dedupe_key: str,
        snapshot_version: str,
        signature_hash: str,
        semantic_signature_hash: str,
        reason: str,
        packet: Any,
        channel: str | None = None,
        thread_ts: str | None = None,
    ) -> None:
        await self._execute(
            """
            UPDATE supervisor_slack_digest_state
            SET status = 'failed',
                suppress_reason = $3,
                updated_at = NOW()
            WHERE feature_id = $1 AND dedupe_key = $2
            """,
            self._feature_id,
            dedupe_key,
            reason[:500],
        )
        await self._record_audit(
            decision="failed",
            reason=reason[:500],
            dedupe_key=dedupe_key,
            snapshot_version=snapshot_version,
            signature_hash=signature_hash,
            semantic_signature_hash=semantic_signature_hash,
            channel=channel,
            thread_ts=thread_ts,
            message_ts=None,
            packet=packet,
        )

    async def _record_audit(
        self,
        *,
        decision: str,
        reason: str,
        dedupe_key: str,
        snapshot_version: str,
        signature_hash: str,
        semantic_signature_hash: str,
        channel: str | None,
        thread_ts: str | None,
        message_ts: str | None,
        packet: Any,
    ) -> None:
        payload = {
            "kind": "supervisor-slack-digest-audit",
            "feature_id": self._feature_id,
            "dedupe_key": dedupe_key,
            "snapshot_version": snapshot_version,
            "decision": decision,
            "reason": reason,
            "classification": packet.classification.value,
            "recommended_action": packet.recommended_action.value,
            "group_idx": packet.group_idx,
            "retry": packet.retry,
        }
        await self._execute(
            """
            INSERT INTO supervisor_slack_digest_audit (
                feature_id, dedupe_key, snapshot_version, decision, reason,
                signature_hash, semantic_signature_hash, channel, thread_ts,
                message_ts, citations, payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb)
            """,
            self._feature_id,
            dedupe_key,
            snapshot_version,
            decision,
            reason,
            signature_hash,
            semantic_signature_hash,
            channel or "",
            thread_ts,
            message_ts,
            json.dumps(_bounded_digest_citations(packet)),
            json.dumps(payload, sort_keys=True, default=str),
        )

    async def _fetch(self, query: str, *args: Any) -> list[Any]:
        fetch = getattr(self._pool, "fetch", None)
        if not callable(fetch):
            return []
        return list(await fetch(query, *args))

    async def _fetchrow(self, query: str, *args: Any) -> Any:
        fetchrow = getattr(self._pool, "fetchrow", None)
        if callable(fetchrow):
            return await fetchrow(query, *args)
        result = await self._execute(query, *args)
        if isinstance(result, str):
            return {"status": result} if result.upper().startswith("INSERT 0 1") else None
        return result

    async def _execute(self, query: str, *args: Any) -> Any:
        execute = getattr(self._pool, "execute", None)
        if callable(execute):
            return await execute(query, *args)
        fetchrow = getattr(self._pool, "fetchrow", None)
        if callable(fetchrow):
            return await fetchrow(query, *args)
        return None


def _is_unique_violation(exc: Exception) -> bool:
    if getattr(exc, "sqlstate", None) == "23505":
        return True
    if getattr(exc, "pgcode", None) == "23505":
        return True
    return "UniqueViolation" in type(exc).__name__


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _digest_key_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:120] or "snapshot"


def _bounded_digest_citations(packet: Any) -> list[str]:
    return [
        str(item)[:240]
        for item in list(getattr(packet, "citations", []) or [])[:_DIGEST_CITATION_LIMIT]
        if str(item).strip()
    ]


def _digest_failure_signature(packet: Any) -> tuple[str, ...]:
    facts = packet.facts if isinstance(getattr(packet, "facts", None), dict) else {}
    parts: list[str] = []
    for key in (
        "runtime_failure_events",
        "operator_required_runtime_failures",
        "pipeline_runtime_failures",
        "product_defect_runtime_failures",
    ):
        values = facts.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            parts.append(_digest_signature_part(
                key,
                {
                    "failure_class": value.get("failure_class"),
                    "failure_type": value.get("failure_type"),
                    "route": value.get("route"),
                    "deterministic": value.get("deterministic"),
                    "retryable": value.get("retryable"),
                    "content": _semantic_digest_text(value.get("content")),
                },
            ))
    for key in (
        "workflow_blocker_failure_classes",
        "paths",
        "operator_required_paths",
        "commit_targets",
    ):
        values = facts.get(key)
        if isinstance(values, list):
            parts.append(_digest_signature_part(key, sorted(str(item) for item in values)))
    for key in (
        "workflow_blocker_artifacts",
        "stale_or_path_problem_artifacts",
        "commit_failure_artifacts",
        "commit_failure_events",
        "latest_failed_verify_artifacts",
    ):
        values = facts.get(key)
        if isinstance(values, list):
            parts.append(_digest_signature_part(
                key,
                sorted(_semantic_digest_citation(item) for item in values),
            ))
    if getattr(packet, "citations", None):
        parts.append(_digest_signature_part(
            "citations",
            sorted(_semantic_digest_citation(item) for item in packet.citations or []),
        ))
    return tuple(sorted(part for part in parts if part))


def _digest_signature_part(label: str, value: Any) -> str:
    return json.dumps(
        [label, value],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _semantic_digest_citation(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+id=\d+\b", "", text)
    text = re.sub(r":evidence_node:\d+\b", ":evidence_node", text)
    text = re.sub(r"\bevent:\d+\b", "event", text)
    return text


def _semantic_digest_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return _semantic_digest_citation(text)


def _signature_hash(signature: tuple[str, ...]) -> str:
    encoded = json.dumps(list(signature), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest_snapshot_version(packet: Any) -> str:
    facts = packet.facts if isinstance(packet.facts, dict) else {}
    explicit = facts.get("control_plane_snapshot_version") or facts.get("snapshot_version")
    if explicit not in (None, ""):
        return str(explicit)
    current = facts.get("current_workflow")
    latest_event_id = ""
    latest_artifact_id = ""
    if isinstance(current, dict):
        latest_event_id = str(current.get("latest_event_id") or "")
        latest_artifact_id = str(current.get("latest_artifact_id") or "")
    return ":".join(
        str(part)
        for part in (
            facts.get("next_cursor") or "",
            facts.get("next_event_cursor") or latest_event_id,
            facts.get("next_artifact_cursor") or latest_artifact_id,
            facts.get("bridge_log_cursor") or "",
        )
    )


# Slice 10d-2 — a sentinel distinguishing "the background digest was
# intentionally NOT posted" (e.g. a stale token was concurrently ignored) from
# a real Slack message timestamp. `_post_background_digest` returns this so
# `_route_background_digest` skips the `record_sent` for a non-send. It is a
# unique object so it can never collide with a real (string) Slack `ts`.
_DIGEST_NOT_POSTED: Any = object()


def _packet_recommended_route(packet: Any) -> str:
    """Best-effort derive the typed route label for a :class:`SupervisorDigestKey`.

    doc 10 § "Slack Dedupe And Suppression": the dedupe key includes the typed
    ``recommended_route`` so a route change always invents a new key. The
    classifier packet does not carry a single typed ``recommended_route``
    field; the route is read, in order, from: an explicit ``recommended_route``
    fact, the compact typed ``control_plane`` fact's first active/failure
    route, or the empty string (a digest with no typed route — a degraded /
    legacy-fallback observation — keys on ``""`` and still dedupes correctly).
    """

    facts = packet.facts if isinstance(getattr(packet, "facts", None), dict) else {}
    explicit = facts.get("recommended_route")
    if explicit not in (None, ""):
        return str(explicit)
    control_plane = facts.get("control_plane")
    if isinstance(control_plane, dict):
        route = control_plane.get("recommended_route") or control_plane.get("route")
        if route not in (None, ""):
            return str(route)
    # The classifier records the active typed route under these fact keys when
    # a typed snapshot is present (see classifier `control_plane` facts).
    for key in ("typed_route", "active_route", "route"):
        value = facts.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _packet_merge_queue_statuses(
    control_plane: dict[str, Any],
    facts: dict[str, Any],
) -> list[str]:
    """Derive the typed merge-queue status list for a :class:`SupervisorDigestKey`.

    doc 10: the dedupe key folds ``merge_queue_statuses`` so a queue-state
    transition (the doc-10 ``healthy_progress`` queue signal) invents a new
    key. The classifier's compact ``control_plane`` fact may carry the typed
    statuses directly (``merge_queue_statuses``) or a typed ``merge_queue``
    list of items each with a ``status``; both are tolerated. Returns a
    de-duplicated, lexicographically-stable list (the digest also sorts).
    """

    raw: list[str] = []
    statuses = control_plane.get("merge_queue_statuses") or facts.get(
        "merge_queue_statuses"
    )
    if isinstance(statuses, (list, tuple)):
        raw.extend(str(item) for item in statuses if str(item).strip())
    merge_queue = control_plane.get("merge_queue")
    if isinstance(merge_queue, dict):
        items = merge_queue.get("items")
        if isinstance(items, (list, tuple)):
            raw.extend(
                str(item.get("status"))
                for item in items
                if isinstance(item, dict) and str(item.get("status") or "").strip()
            )
    elif isinstance(merge_queue, (list, tuple)):
        raw.extend(
            str(item.get("status"))
            for item in merge_queue
            if isinstance(item, dict) and str(item.get("status") or "").strip()
        )
    return sorted(_dedupe_strings(raw))


def _packet_active_attempt_ids(
    control_plane: dict[str, Any],
    facts: dict[str, Any],
) -> list[int]:
    """Derive the typed active-attempt id list for a :class:`SupervisorDigestKey`.

    doc 10: the dedupe key folds ``active_attempt_ids`` so a change in the set
    of active dispatcher attempts invents a new key. The ids are read from the
    typed ``control_plane`` fact's ``active_attempts`` list (or a flat
    ``active_attempt_ids`` list). Non-integer ids are skipped — a malformed id
    must not crash the digest routing.
    """

    raw: list[int] = []
    attempts = control_plane.get("active_attempts")
    if isinstance(attempts, (list, tuple)):
        for attempt in attempts:
            attempt_id = (
                attempt.get("attempt_id")
                if isinstance(attempt, dict)
                else attempt
            )
            try:
                raw.append(int(attempt_id))
            except (TypeError, ValueError):
                continue
    flat = control_plane.get("active_attempt_ids") or facts.get("active_attempt_ids")
    if isinstance(flat, (list, tuple)):
        for attempt_id in flat:
            try:
                raw.append(int(attempt_id))
            except (TypeError, ValueError):
                continue
    return sorted(_dedupe_ints(raw))


class SupervisorRuntimeService:
    """Evidence-backed service used by the separate supervisor Slack bot."""

    def __init__(
        self,
        *,
        app: SupervisorApp,
        feature_id: str,
        agent: SupervisorAgent | None = None,
        agent_runtime: Any | None = None,
        poll_interval_seconds: float = 30.0,
        min_digest_interval_seconds: float = 120.0,
        action_policy: ActionPolicy | None = None,
        workflow_instruction_sink: Callable[[SupervisorSlackRoute], Awaitable[dict[str, Any]]]
        | None = None,
        session_epoch: str | None = None,
        digest_decision_store: SupervisorSlackDigestDecisionStore | None = None,
        digest_dedupe_store: SupervisorDigestDedupeStore | None = None,
    ) -> None:
        self._app = app
        self._feature_id = feature_id
        self._agent = agent or SupervisorAgent()
        self._agent_runtime = agent_runtime
        self._poll_interval_seconds = poll_interval_seconds
        self._min_digest_interval_seconds = min_digest_interval_seconds
        self._action_policy = action_policy
        self._workflow_instruction_sink = workflow_instruction_sink
        self._session_epoch = (
            session_epoch
            or os.environ.get("IRIAI_SUPERVISOR_SESSION_EPOCH")
            or uuid.uuid4().hex[:12]
        )
        self._cursor = 0
        self._event_cursor = 0
        self._artifact_cursor = 0
        self._bridge_log_cursor = 0
        self._last_digest_signature: tuple[str, ...] | None = None
        self._last_digest_semantic_signature: tuple[str, ...] | None = None
        self._last_digest_at = 0.0
        self._pending_digest_packet: Any | None = None
        self._pending_digest_signature: tuple[str, ...] | None = None
        self._pending_digest_semantic_signature: tuple[str, ...] | None = None
        self._pending_digest_delivery: dict[str, Any] | None = None
        self._stale_codex_packets: dict[str, Any] = {}
        self._ignored_stale_codex_tokens: set[str] = set()
        self._digest_decision_store = digest_decision_store
        # Slice 10d-2: the typed-control-plane Slack dedupe store (doc 10 §
        # "Slack Dedupe And Suppression" + § "Refactoring Steps" step 7). EVERY
        # background digest in `watch_and_digest` is routed through
        # `SupervisorDigestDedupeStore.decide()` — it is THE enforcement point
        # for the doc-10 never-suppress guarantee. Resolved from the same pool
        # the legacy `SupervisorSlackDigestDecisionStore` uses (the new store
        # is additive: doc 10 keeps the legacy `supervisor_slack_digest_*`
        # tables byte-for-byte unchanged on the allowed audit side).
        self._digest_dedupe_store = digest_dedupe_store
        if self._digest_decision_store is None or self._digest_dedupe_store is None:
            feature_store = getattr(app, "feature_store", None)
            artifact_store = getattr(app, "artifact_store", None)
            pool = (
                getattr(app, "supervisor_pool", None)
                or getattr(artifact_store, "_pool", None)
                or getattr(feature_store, "_pool", None)
            )
            if pool is not None:
                if self._digest_decision_store is None:
                    self._digest_decision_store = SupervisorSlackDigestDecisionStore(
                        pool=pool,
                        feature_id=feature_id,
                    )
                if self._digest_dedupe_store is None:
                    self._digest_dedupe_store = SupervisorDigestDedupeStore(
                        pool=pool,
                        feature_id=feature_id,
                    )

    @asynccontextmanager
    async def bind_progress(self, progress: SupervisorSlackProgress):
        runtime = self._agent_runtime
        if runtime is None or not hasattr(runtime, "on_message"):
            yield
            return
        previous = getattr(runtime, "on_message", None)
        setattr(runtime, "on_message", progress.on_message)
        try:
            yield
        finally:
            setattr(runtime, "on_message", previous)

    async def answer_question(self, route: SupervisorSlackRoute) -> str:
        packet = await self._packet()
        question = await self._question_with_thread_context(route)
        if (
            packet.classification == FailureClass.STALE_CODEX_INVOCATION
            and _stale_codex_question(route.text)
        ):
            stale = _stale_codex_fact(packet)
            token = str(stale.get("evidence_token") or "").strip()
            if token:
                self._stale_codex_packets[token] = packet
            message = _stale_codex_direct_message(packet, mode=self._app.mode)
            assessment = _stale_codex_direct_assessment(
                packet,
                message=message,
                mode=self._app.mode,
            )
            await self._write_assessment(
                packet,
                route.text,
                assessment,
                [],
                False,
                session_scope=self._question_session_scope(route),
                slack_channel=route.channel,
                slack_thread_ts=route.thread_ts,
                slack_user=route.user,
            )
            return message
        toolbox = self._app.evidence_toolbox(self._feature_id)
        session_scope = self._question_session_scope(route)
        return await self._agent.compose_message(
            packet,
            question=question,
            runtime=self._agent_runtime,
            feature_id=self._feature_id,
            toolbox=toolbox,
            timeout_seconds=None,
            session_epoch=self._session_epoch,
            session_scope=session_scope,
            assessment_sink=lambda assessment, bundles, fallback: self._write_assessment(
                packet,
                route.text,
                assessment,
                bundles,
                fallback,
                session_scope=session_scope,
                slack_channel=route.channel,
                slack_thread_ts=route.thread_ts,
                slack_user=route.user,
            ),
        )

    async def evaluate_action_request(self, route: SupervisorSlackRoute) -> str:
        packet = await self._packet()
        question = await self._question_with_thread_context(route)
        wants_restart = "restart" in route.text.lower() or "reboot" in route.text.lower()
        assessment, bundles, fallback = await self._agent.assess(
            packet,
            question=question,
            runtime=self._agent_runtime,
            feature_id=self._feature_id,
            toolbox=self._app.evidence_toolbox(self._feature_id),
            timeout_seconds=None,
            session_epoch=self._session_epoch,
            session_scope=self._action_session_scope(route),
        )
        await self._write_assessment(
            packet,
            route.text,
            assessment,
            bundles,
            fallback,
            session_scope=self._action_session_scope(route),
            slack_channel=route.channel,
            slack_thread_ts=route.thread_ts,
            slack_user=route.user,
        )
        if (
            wants_restart
            and self._action_policy is not None
            and assessment.proposed_action == "restart_bridge"
        ):
            record = await self._action_policy.maybe_restart(packet)
            status = (
                f"Action `restart_bridge` is `{record.status.value}`. "
                f"Reason: {record.reason}"
            )
            if record.error:
                status += f" Error: {record.error}"
            return f"{status}\n\n{assessment.message}"

        if assessment.proposed_action == "supervisor_maintainer_dry_run":
            record = SupervisorActionRecord(
                feature_id=self._feature_id,
                cursor=int(packet.facts.get("next_cursor") or 0),
                action="supervisor_maintainer_dry_run",
                mode=self._app.mode,
                status=SupervisorActionStatus.PLANNED,
                reason=(
                    "Supervisor agent proposed a read-only maintainer investigation "
                    "or pipeline patch plan. No code was changed."
                ),
                before={
                    "question": route.text,
                    "assessment": assessment.model_dump(mode="json"),
                },
                packet=packet,
            )
            await self._write_action(record, "planned")
            return assessment.message

        return (
            "I treated this as a supervisor action request, but no guarded "
            "action matched it. I did not mutate the workflow.\n\n"
            f"{assessment.message}"
        )

    async def route_workflow_instruction(self, route: SupervisorSlackRoute) -> str:
        packet = await self._packet()
        if self._workflow_instruction_sink is not None and self._app.mode != SupervisorMode.READ_ONLY:
            result = await self._workflow_instruction_sink(route)
            return (
                "I forwarded that workflow instruction through the configured sink. "
                f"Result: `{result}`"
            )

        reason = (
            "Separate supervisor bot captured a workflow instruction, but read-only "
            "supervisor mode forbids forwarding workflow instructions."
            if self._app.mode == SupervisorMode.READ_ONLY
            else (
                "Separate supervisor bot captured a workflow instruction, but "
                "no bridge/workflow instruction sink is configured yet."
            )
        )
        record = SupervisorActionRecord(
            feature_id=self._feature_id,
            cursor=int(packet.facts.get("next_cursor") or 0),
            action="workflow_instruction",
            mode=self._app.mode,
            status=SupervisorActionStatus.BLOCKED,
            reason=reason,
            before={"text": route.text, "channel": route.channel, "user": route.user},
            packet=packet,
        )
        await self._write_action(record, "blocked")
        if self._app.mode == SupervisorMode.READ_ONLY:
            return (
                "I captured that workflow instruction, but this supervisor is in "
                "read-only mode, so I did not forward it to an implementer/verifier."
            )
        return (
            "I captured that workflow instruction, but this supervisor process "
            "does not yet have a live workflow-instruction sink. I did not send "
            "it to an implementer/verifier."
        )

    async def handle_stale_codex_action(self, action_id: str, value: str) -> str:
        token = str(value or "").strip()
        packet = self._stale_codex_packets.get(token)
        if packet is None:
            return (
                "This stale Codex card is no longer active in the supervisor process. "
                "Ask for current status to refresh the evidence before taking action."
            )
        if action_id.startswith("stale_codex_ignore_"):
            self._ignored_stale_codex_tokens.add(token)
            return "Ignored this stale Codex diagnosis once. I will repost only if the evidence changes."
        if action_id.startswith("stale_codex_dismiss_"):
            self._ignored_stale_codex_tokens.add(token)
            return "Dismissed this stale Codex card."
        if action_id.startswith("stale_codex_trace_"):
            stale = packet.facts.get("stale_codex_invocation") if packet.facts else {}
            if not isinstance(stale, dict):
                stale = {}
            return (
                "Trace details:\n"
                f"- Trace: `{stale.get('trace_path') or 'unknown'}`\n"
                f"- Output: `{stale.get('output_path') or 'unknown'}`\n"
                f"- PID: `{stale.get('pid')}` children `{stale.get('child_pids') or []}`"
            )
        if action_id.startswith("stale_codex_why_"):
            return packet.inference
        if action_id.startswith("stale_codex_readonly_"):
            return (
                "Supervisor is running in read-only mode, so I did not kill the process. "
                "Restart the supervisor with guarded mode to enable the approval button, "
                "or manually kill the exact PID listed on the card."
            )
        if action_id.startswith("stale_codex_kill_"):
            if self._action_policy is None:
                return "No supervisor action policy is configured; I did not kill anything."
            record = await self._action_policy.maybe_kill_stale_codex(
                packet,
                evidence_token=token,
            )
            if record.status == SupervisorActionStatus.COMPLETED:
                return (
                    "Killed the stale Codex process tree and recorded the guarded action. "
                    f"Terminated PIDs: `{record.after.get('terminated_pids')}`."
                )
            return f"Stale Codex reset was `{record.status.value}`: {record.reason}"
        if action_id.startswith("stale_codex_feedback_"):
            return "Thanks. I recorded the feedback signal in Slack; no workflow action was taken."
        return "Unknown stale Codex action. No workflow action was taken."

    async def watch_and_digest(self, adapter: Any, channel: str) -> None:
        """Poll evidence and send one agent-written digest per material change.

        Slice 10d-2 (doc 10 § "Slack Dedupe And Suppression" + § "Refactoring
        Steps" step 7): EVERY background digest produced here is routed through
        :meth:`SupervisorDigestDedupeStore.decide` BEFORE the Slack client is
        ever touched. There is NO branch where a background digest reaches
        ``adapter.post_blocks`` without a preceding ``should_send=True``
        decision — the only ``post_blocks`` calls below sit inside
        :meth:`_post_background_digest`, which is unreachable unless
        :meth:`_decide_background_digest` returned ``send=True``. That makes a
        direct background send structurally impossible and makes ``decide()``
        the single enforcement point for the doc-10 never-suppress guarantee
        (10d-1's ``_never_suppress_reason()`` short-circuit only protects
        digests that actually reach ``decide()``).
        """
        while True:
            try:
                packet = await self._packet()
                digest_packet = await self._digest_packet_to_send_durable(
                    packet,
                    channel=channel,
                )
                if digest_packet is not None:
                    await self._route_background_digest(
                        digest_packet,
                        adapter=adapter,
                        channel=channel,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Supervisor watch digest failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _route_background_digest(
        self,
        digest_packet,
        *,
        adapter: Any,
        channel: str,
    ) -> None:
        """Route ONE background digest through the dedupe ``decide()`` gate.

        doc 10 § "Slack Dedupe And Suppression" step 7. The flow is strict:

        1. Build the typed :class:`SupervisorDigestKey` and the never-suppress
           flags from the typed observation/packet.
        2. Call :meth:`_decide_background_digest` — it ALWAYS calls
           ``SupervisorDigestDedupeStore.decide()`` (or applies the doc-10
           fail-open / fail-quiet split on a store error).
        3. On ``should_send=False`` — call :meth:`record_suppressed` and return
           WITHOUT sending. On ``should_send=True`` — render + send the digest,
           THEN call :meth:`record_sent`.

        The two Slack send shapes (the stale-Codex card and the regular status
        digest) live in :meth:`_post_background_digest`; neither is reachable
        unless this method already decided to send.
        """

        snapshot_version = _digest_snapshot_version(digest_packet)
        key = self._build_digest_key(digest_packet)
        flags = await self._derive_never_suppress_flags(digest_packet, key=key)

        # The stale-Codex card carries its own ignore/missing-token gate; that
        # gate runs FIRST so an ignored/untokened stale card is recorded as a
        # suppression and never reaches `decide()`/the Slack client.
        if digest_packet.classification == FailureClass.STALE_CODEX_INVOCATION:
            stale = digest_packet.facts.get("stale_codex_invocation") or {}
            token = str(stale.get("evidence_token") or "")
            if not (token and token not in self._ignored_stale_codex_tokens):
                reason = (
                    "stale_codex_ignored" if token else "stale_codex_missing_token"
                )
                await self._record_digest_suppressed(
                    digest_packet,
                    channel=channel,
                    reason=reason,
                )
                return

        decision, send = await self._decide_background_digest(
            digest_packet,
            key=key,
            snapshot_version=snapshot_version,
            flags=flags,
        )
        if not send:
            # doc 10 § "Slack Dedupe And Suppression": "Reprocessing the same
            # snapshot may append a suppress/coalesce audit row, but it must
            # not send a second background Slack message." Record the suppress
            # decision and the legacy claim suppression; do NOT post.
            await self._record_dedupe_suppressed(
                key=key,
                snapshot_version=snapshot_version,
                decision=decision,
                packet=digest_packet,
                channel=channel,
            )
            await self._record_digest_suppressed(
                digest_packet,
                channel=channel,
                reason=f"dedupe:{decision.reason}",
            )
            return

        message_ts = await self._post_background_digest(
            digest_packet,
            adapter=adapter,
            channel=channel,
        )
        if message_ts is _DIGEST_NOT_POSTED:
            # `_post_background_digest` already recorded the failed legacy
            # claim and re-raised on a genuine Slack failure; a sentinel return
            # means the send was intentionally skipped (e.g. the stale token
            # was concurrently ignored). No `record_sent` for a non-send.
            return

        # doc 10: send first, THEN persist the SENT decision (so the audit row
        # records the real outcome and `last_sent_at` reflects a real send).
        await self._record_dedupe_sent(
            key=key,
            snapshot_version=snapshot_version,
            decision=decision,
            packet=digest_packet,
            channel=channel,
            message_ts=str(message_ts or ""),
        )
        await self._record_digest_delivered(
            digest_packet,
            channel=channel,
            message_ts=message_ts,
        )

    async def _post_background_digest(
        self,
        digest_packet,
        *,
        adapter: Any,
        channel: str,
    ) -> Any:
        """Render + post one already-decided background digest to Slack.

        This is the ONLY place ``adapter.post_blocks`` is called for a
        background digest, and it is unreachable unless
        :meth:`_route_background_digest` already decided ``should_send=True``.
        Returns the Slack message timestamp, or :data:`_DIGEST_NOT_POSTED` when
        the send was intentionally skipped. On a genuine Slack/compose failure
        it records the failed legacy claim and re-raises (the loop logs it).
        """

        if digest_packet.classification == FailureClass.STALE_CODEX_INVOCATION:
            stale = digest_packet.facts.get("stale_codex_invocation") or {}
            token = str(stale.get("evidence_token") or "")
            if not (token and token not in self._ignored_stale_codex_tokens):
                # Concurrency guard: the token was ignored after the decision.
                return _DIGEST_NOT_POSTED
            self._stale_codex_packets[token] = digest_packet
            card = SupervisorStaleInvocationCard(
                digest_packet,
                mode=self._app.mode,
            )
            try:
                return await adapter.post_blocks(
                    channel,
                    card.build_blocks(),
                    card.fallback_text(),
                )
            except Exception as exc:
                await self._record_digest_failed(
                    digest_packet,
                    channel=channel,
                    error=exc,
                )
                raise

        try:
            message = await self._agent.compose_message(
                digest_packet,
                runtime=self._agent_runtime,
                feature_id=self._feature_id,
                toolbox=self._app.evidence_toolbox(self._feature_id),
                session_epoch=self._session_epoch,
                session_scope="digest",
                assessment_sink=lambda assessment, bundles, fallback: self._write_assessment(
                    digest_packet,
                    None,
                    assessment,
                    bundles,
                    fallback,
                    session_scope="digest",
                    slack_channel=channel,
                ),
            )
        except Exception as exc:
            await self._record_digest_failed(
                digest_packet,
                channel=channel,
                error=exc,
            )
            raise
        try:
            return await adapter.post_blocks(
                channel,
                build_status_blocks(message),
                message[:300],
            )
        except Exception as exc:
            await self._record_digest_failed(
                digest_packet,
                channel=channel,
                error=exc,
            )
            raise

    # ── Slice 10d-2 — typed dedupe-store routing ─────────────────────────────

    def _build_digest_key(self, packet) -> SupervisorDigestKey:
        """Build the doc-10 :class:`SupervisorDigestKey` from a typed packet.

        doc 10 § "Slack Dedupe And Suppression": the dedupe key is a stable
        JSON digest over ``SupervisorDigestKey``; evidence ids alone never
        create a new key. The eight key fields are derived from the typed
        observation/packet — ``classification`` / ``recommended_action`` from
        the classifier verdict, ``recommended_route`` /
        ``merge_queue_statuses`` / ``active_attempt_ids`` from the compact
        typed ``control_plane`` fact, and ``failure_signature_hashes`` from the
        deterministic :func:`_digest_failure_signature` (the same signature the
        legacy claim uses), so list-ordering churn never invents a key.
        """

        facts = packet.facts if isinstance(getattr(packet, "facts", None), dict) else {}
        control_plane = (
            facts.get("control_plane")
            if isinstance(facts.get("control_plane"), dict)
            else {}
        )
        signature_hash = _signature_hash(_digest_failure_signature(packet))
        return SupervisorDigestKey(
            feature_id=self._feature_id,
            group_idx=packet.group_idx,
            classification=str(packet.classification.value),
            recommended_action=str(packet.recommended_action.value),
            recommended_route=_packet_recommended_route(packet),
            failure_signature_hashes=[signature_hash] if signature_hash else [],
            merge_queue_statuses=_packet_merge_queue_statuses(control_plane, facts),
            active_attempt_ids=_packet_active_attempt_ids(control_plane, facts),
        )

    async def _derive_never_suppress_flags(
        self,
        packet,
        *,
        key: SupervisorDigestKey,
    ) -> dict[str, bool]:
        """Derive the three doc-10 never-suppress flags from a typed packet.

        doc 10 § "Slack Dedupe And Suppression": "Never suppress direct
        operator answers, first ``stop/escalate`` for a new failure signature,
        or first ``operator_required`` for a new typed route."

        * ``is_operator_answer`` — ALWAYS ``False`` here. ``watch_and_digest``
          only ever produces BACKGROUND (poll-driven) digests; a direct
          operator answer flows through :meth:`answer_question` /
          :meth:`evaluate_action_request`, which never reach this routing
          (doc 10: "Direct operator Slack questions bypass suppression"). The
          flag is still threaded through so the contract is explicit.
        * ``new_failure_signature`` — set when this digest's typed failure
          signature was NOT seen before. The :class:`SupervisorDigestKey`
          digest FOLDS ``failure_signature_hashes``, so a genuinely new failure
          signature yields a brand-new dedupe key with no prior
          ``supervisor_digest_state`` row. The derivation is therefore exact:
          ``new_failure_signature`` ⇔ no prior state row for the dedupe key.
          This is why "the store cannot itself know" — the store sees only the
          one dedupe key; this caller checks whether THAT key is first-seen.
        * ``new_operator_route`` — same exact derivation: the dedupe key folds
          ``recommended_route``, so a first ``operator_required`` for a NEW
          typed route is exactly a first-seen dedupe key.

        Deriving both from "the dedupe key has no prior state row" means the
        never-suppress arm fires ONLY for the genuine FIRST digest of a new
        signature/route — a repeat of the SAME signature/route reuses the key,
        finds the prior row, and is correctly suppressible (no operator flood).

        On a :class:`DigestDedupeStoreError` the state read degrades
        CONSERVATIVELY to "first seen" (``True``) so a store outage NEVER
        swallows a first ``stop/escalate`` / ``operator_required`` — doc 10 §
        "Edge Cases And Failure Handling" "fail open" for an escalation.
        """

        first_seen = True
        store = self._digest_dedupe_store
        if store is not None:
            try:
                prior = await store.get_state(key)
                first_seen = prior is None
            except DigestDedupeStoreError:
                logger.warning(
                    "Supervisor digest dedupe prior-state read failed while "
                    "deriving never-suppress flags; treating as first-seen "
                    "(fail-open, doc 10)",
                    exc_info=True,
                )
                first_seen = True
        return {
            "is_operator_answer": False,
            "new_failure_signature": first_seen,
            "new_operator_route": first_seen,
        }

    async def _decide_background_digest(
        self,
        packet,
        *,
        key: SupervisorDigestKey,
        snapshot_version: str,
        flags: dict[str, bool],
    ) -> tuple[SupervisorDigestDecision, bool]:
        """Always call ``decide()``; return ``(decision, should_send)``.

        doc 10 § "Edge Cases And Failure Handling": "Dedupe store write
        failure: fail open for operator-requested replies, fail quiet for
        background duplicate candidates." ``decide()`` itself already fails
        open for the never-suppress arms (10d-1) and re-raises a typed
        :class:`DigestDedupeStoreError` for a non-exception background digest.
        This wrapper applies the doc-10 split for THAT error:

        * a never-suppress candidate (a first ``stop/escalate`` for a new
          signature, a first ``operator_required`` for a new route, or an
          operator answer) -> ``send=True`` (fail open);
        * any other background duplicate candidate -> ``send=False`` (fail
          quiet — suppress rather than risk a duplicate send) + a warning.

        When the dedupe store is unavailable entirely the same split applies.
        """

        store = self._digest_dedupe_store
        never_suppress = self._is_never_suppress_candidate(packet, flags)
        if store is None:
            # No dedupe store wired: fail open for a never-suppress candidate,
            # fail quiet otherwise. A `material_change` reason keeps the
            # fail-open send auditable as a genuine new material state.
            decision = SupervisorDigestDecision(
                dedupe_key="",
                should_send=bool(never_suppress),
                reason="material_change" if never_suppress else "suppressed_duplicate",
            )
            if not never_suppress:
                logger.warning(
                    "Supervisor digest dedupe store unavailable; suppressing a "
                    "background digest candidate (fail-quiet, doc 10)"
                )
            return decision, decision.should_send
        try:
            decision = await store.decide(
                key=key,
                snapshot_version=snapshot_version,
                is_operator_answer=bool(flags.get("is_operator_answer")),
                new_failure_signature=bool(flags.get("new_failure_signature")),
                new_operator_route=bool(flags.get("new_operator_route")),
            )
        except DigestDedupeStoreError:
            # doc 10 fail-open / fail-quiet split on a store read failure.
            if never_suppress:
                logger.warning(
                    "Supervisor digest dedupe decide() failed for a "
                    "never-suppress digest; sending fail-open (doc 10)",
                    exc_info=True,
                )
                return (
                    SupervisorDigestDecision(
                        dedupe_key=compute_dedupe_key(key),
                        should_send=True,
                        reason="material_change",
                    ),
                    True,
                )
            logger.warning(
                "Supervisor digest dedupe decide() failed for a background "
                "digest; suppressing the candidate (fail-quiet, doc 10)",
                exc_info=True,
            )
            return (
                SupervisorDigestDecision(
                    dedupe_key=compute_dedupe_key(key),
                    should_send=False,
                    reason="suppressed_duplicate",
                ),
                False,
            )
        return decision, bool(decision.should_send)

    @staticmethod
    def _is_never_suppress_candidate(packet, flags: dict[str, bool]) -> bool:
        """Is this digest one of the doc-10 never-suppress cases?

        Mirrors :meth:`SupervisorDigestDedupeStore._never_suppress_reason` so
        the fail-open / fail-quiet split (which runs WHEN the store read fails,
        i.e. before ``decide()`` can classify it) makes the same call the store
        would have made: an operator answer, a first ``stop/escalate`` for a
        new failure signature, or a first ``operator_required`` for a new typed
        route.
        """

        if flags.get("is_operator_answer"):
            return True
        action = str(getattr(packet.recommended_action, "value", "")).strip().lower()
        classification = str(getattr(packet.classification, "value", "")).strip().lower()
        if action in ("stop/escalate", "stop_escalate") and flags.get(
            "new_failure_signature"
        ):
            return True
        if classification == "operator_required" and flags.get("new_operator_route"):
            return True
        return False

    async def _record_dedupe_sent(
        self,
        *,
        key: SupervisorDigestKey,
        snapshot_version: str,
        decision: SupervisorDigestDecision,
        packet,
        channel: str,
        message_ts: str,
    ) -> None:
        """Best-effort persist a SENT dedupe decision after a real Slack send.

        doc 10 requires the send/suppress decision to be recorded; a recording
        failure must NOT undo a send already shown to the operator, so a store
        error here is logged and swallowed (the digest WAS delivered).
        """

        store = self._digest_dedupe_store
        if store is None or not decision.dedupe_key:
            return
        try:
            await store.record_sent(
                decision=decision,
                key=key,
                snapshot_version=snapshot_version,
                slack_channel=channel,
                slack_message_ts=message_ts,
                citation_refs=_bounded_digest_citations(packet),
            )
        except DigestDedupeStoreError:
            logger.warning(
                "Failed to record supervisor digest SENT decision after a "
                "successful Slack send (the digest was delivered)",
                exc_info=True,
            )

    async def _record_dedupe_suppressed(
        self,
        *,
        key: SupervisorDigestKey,
        snapshot_version: str,
        decision: SupervisorDigestDecision,
        packet,
        channel: str,
    ) -> None:
        """Best-effort persist a SUPPRESSED dedupe decision (no Slack send)."""

        store = self._digest_dedupe_store
        if store is None or not decision.dedupe_key:
            return
        try:
            await store.record_suppressed(
                decision=decision,
                key=key,
                snapshot_version=snapshot_version,
                slack_channel=channel,
                citation_refs=_bounded_digest_citations(packet),
            )
        except DigestDedupeStoreError:
            logger.warning(
                "Failed to record supervisor digest SUPPRESSED decision",
                exc_info=True,
            )

    async def _packet(self):
        packet = await self._app.run_once(
            feature_id=self._feature_id,
            cursor=self._cursor,
            event_cursor=self._event_cursor,
            artifact_cursor=self._artifact_cursor,
            bridge_log_cursor=self._bridge_log_cursor,
        )
        self._cursor = max(self._cursor, int(packet.facts.get("next_cursor") or 0))
        self._event_cursor = max(
            self._event_cursor,
            int(packet.facts.get("next_event_cursor") or 0),
        )
        self._artifact_cursor = max(
            self._artifact_cursor,
            int(packet.facts.get("next_artifact_cursor") or 0),
        )
        self._bridge_log_cursor = max(
            self._bridge_log_cursor,
            int(packet.facts.get("bridge_log_cursor") or 0),
        )
        return packet

    def _digest_signature(self, packet) -> tuple[str, ...]:
        current = packet.facts.get("current_workflow") if packet.facts else None
        current_state = ""
        current_phase = ""
        active_agents = ""
        queued_agents = ""
        latest_event_id = ""
        latest_artifact_id = ""
        stale_token = ""
        if isinstance(current, dict):
            current_phase = str(current.get("phase") or "")
            current_state = str(current.get("state") or "")
            active_agents = ",".join(str(item) for item in current.get("active_agents") or [])
            queued_agents = ",".join(str(item) for item in current.get("queued_agents") or [])
            latest_event_id = str(current.get("latest_event_id") or "")
            latest_artifact_id = str(current.get("latest_artifact_id") or "")
        stale = packet.facts.get("stale_codex_invocation") if packet.facts else None
        if isinstance(stale, dict):
            stale_token = str(stale.get("evidence_token") or "")
        citations_token = ",".join(str(item) for item in (packet.citations or []))
        operator_failures = packet.facts.get("operator_required_runtime_failures") if packet.facts else None
        operator_failure_token = ""
        if isinstance(operator_failures, list):
            operator_failure_token = ",".join(
                sorted(
                    str(
                        item.get("evidence_node_id")
                        or item.get("citation")
                        or item
                    )
                    for item in operator_failures
                    if isinstance(item, dict)
                )
            )
        return (
            packet.classification.value,
            str(packet.group_idx),
            str(packet.retry),
            packet.recommended_action.value,
            current_phase,
            current_state,
            active_agents,
            queued_agents,
            latest_event_id,
            latest_artifact_id,
            stale_token,
            citations_token,
            operator_failure_token,
        )

    def _digest_semantic_signature(self, packet) -> tuple[str, ...]:
        current = packet.facts.get("current_workflow") if packet.facts else None
        current_phase = ""
        current_state = ""
        if isinstance(current, dict):
            current_phase = str(current.get("phase") or "")
            current_state = str(current.get("state") or "")

        stale = packet.facts.get("stale_codex_invocation") if packet.facts else None
        stale_token = ""
        if isinstance(stale, dict):
            stale_token = str(stale.get("evidence_token") or "")

        return (
            packet.classification.value,
            str(packet.group_idx),
            str(packet.retry),
            packet.recommended_action.value,
            current_phase,
            current_state,
            str(packet.facts.get("bridge_state") or "") if packet.facts else "",
            f"{packet.confidence:.0%}",
            str(packet.inference or ""),
            stale_token,
            *_digest_failure_signature(packet),
        )

    def _uses_semantic_digest_dedupe(self, packet) -> bool:
        del packet
        return True

    def _digest_uses_seed_fallback(self) -> bool:
        runtime = self._agent_runtime
        if runtime is None:
            return True
        runtime_type = type(runtime)
        return (
            runtime_type.__name__ == "CodexAgentRuntime"
            and runtime_type.__module__.startswith("iriai_build_v2.runtimes")
        )

    def _digest_packet_to_send(self, packet):
        now = asyncio.get_running_loop().time()
        pending_due = (
            self._pending_digest_packet is not None
            and now - self._last_digest_at >= self._min_digest_interval_seconds
        )

        if packet.recommended_action == ActionLevel.OBSERVE:
            if pending_due:
                return self._take_pending_digest(now)
            return None

        signature = self._digest_signature(packet)
        semantic_signature = self._digest_semantic_signature(packet)
        semantic_dedupe = self._uses_semantic_digest_dedupe(packet)
        if (
            semantic_dedupe
            and semantic_signature == self._last_digest_semantic_signature
        ):
            return None
        if signature == self._last_digest_signature:
            return None
        if (
            self._last_digest_at > 0
            and now - self._last_digest_at < self._min_digest_interval_seconds
        ):
            if (
                semantic_dedupe
                and semantic_signature == self._pending_digest_semantic_signature
            ):
                return None
            self._pending_digest_packet = packet
            self._pending_digest_signature = signature
            self._pending_digest_semantic_signature = semantic_signature
            return None
        self._pending_digest_packet = None
        self._pending_digest_signature = None
        self._pending_digest_semantic_signature = None
        self._last_digest_signature = signature
        self._last_digest_semantic_signature = semantic_signature
        self._last_digest_at = now
        return packet

    async def _digest_packet_to_send_durable(
        self,
        packet,
        *,
        channel: str | None = None,
        thread_ts: str | None = None,
    ):
        prior_digest_state = (
            self._last_digest_signature,
            self._last_digest_semantic_signature,
            self._last_digest_at,
            self._pending_digest_packet,
            self._pending_digest_signature,
            self._pending_digest_semantic_signature,
        )

        def _restore_prior_digest_state() -> None:
            (
                self._last_digest_signature,
                self._last_digest_semantic_signature,
                self._last_digest_at,
                self._pending_digest_packet,
                self._pending_digest_signature,
                self._pending_digest_semantic_signature,
            ) = prior_digest_state

        retained_delivery = self._pending_digest_delivery
        digest_packet = self._digest_packet_to_send(packet)
        if digest_packet is None:
            return None
        store = self._digest_decision_store
        if store is None:
            logger.debug(
                "Suppressing supervisor digest because durable decision store is unavailable"
            )
            _restore_prior_digest_state()
            return None
        signature = self._digest_signature(digest_packet)
        semantic_signature = self._digest_semantic_signature(digest_packet)
        signature_hash = _signature_hash(signature)
        semantic_signature_hash = _signature_hash(semantic_signature)
        snapshot_version = _digest_snapshot_version(digest_packet)
        semantic_dedupe = self._uses_semantic_digest_dedupe(digest_packet)
        dedupe_key = _signature_hash(
            (
                self._feature_id,
                snapshot_version,
                semantic_signature_hash if semantic_dedupe else signature_hash,
            )
        )
        if (
            retained_delivery
            and str(retained_delivery.get("dedupe_key") or "") == dedupe_key
            and str(retained_delivery.get("snapshot_version") or "") == snapshot_version
            and str(retained_delivery.get("signature_hash") or "") == signature_hash
            and str(retained_delivery.get("semantic_signature_hash") or "") == semantic_signature_hash
            and retained_delivery.get("channel") == channel
            and retained_delivery.get("thread_ts") == thread_ts
        ):
            retained_delivery["packet"] = digest_packet
            retained_delivery["prior_digest_state"] = prior_digest_state
            self._pending_digest_delivery = retained_delivery
            return digest_packet
        self._pending_digest_delivery = None
        try:
            async def _record_suppressed_best_effort(reason: str) -> None:
                try:
                    await store.record_suppressed(
                        dedupe_key=dedupe_key,
                        snapshot_version=snapshot_version,
                        signature_hash=signature_hash,
                        semantic_signature_hash=semantic_signature_hash,
                        reason=reason,
                        packet=digest_packet,
                        channel=channel,
                        thread_ts=thread_ts,
                    )
                except Exception:
                    logger.debug(
                        "Failed to audit supervisor digest suppression decision",
                        exc_info=True,
                    )

            duplicate = await store.delivered_duplicate_exists(
                dedupe_key=dedupe_key,
                signature_hash=signature_hash,
                semantic_signature_hash=semantic_signature_hash,
                semantic_dedupe=semantic_dedupe,
            )
            if duplicate:
                await _record_suppressed_best_effort("delivered_duplicate")
                _restore_prior_digest_state()
                return None
            pending_duplicate = await store.pending_duplicate_exists(
                dedupe_key=dedupe_key,
                signature_hash=signature_hash,
                semantic_signature_hash=semantic_signature_hash,
                semantic_dedupe=semantic_dedupe,
            )
            if pending_duplicate:
                await _record_suppressed_best_effort("pending_duplicate")
                _restore_prior_digest_state()
                return None
            claimed = await store.record_attempt(
                dedupe_key=dedupe_key,
                snapshot_version=snapshot_version,
                signature_hash=signature_hash,
                semantic_signature_hash=semantic_signature_hash,
                reason="material_digest_attempt",
                packet=digest_packet,
                semantic_dedupe=semantic_dedupe,
                channel=channel,
                thread_ts=thread_ts,
            )
            if not claimed:
                await _record_suppressed_best_effort("pending_claim_lost")
                _restore_prior_digest_state()
                return None
            self._pending_digest_delivery = {
                "dedupe_key": dedupe_key,
                "snapshot_version": snapshot_version,
                "signature_hash": signature_hash,
                "semantic_signature_hash": semantic_signature_hash,
                "channel": channel,
                "thread_ts": thread_ts,
                "packet": digest_packet,
                "prior_digest_state": prior_digest_state,
            }
        except Exception:
            logger.debug("Failed to persist supervisor digest dedupe decision", exc_info=True)
            if self._pending_digest_delivery is None:
                _restore_prior_digest_state()
                return None
        return digest_packet

    async def _record_digest_delivered(
        self,
        packet,
        *,
        channel: str,
        message_ts: str | None,
        thread_ts: str | None = None,
    ) -> None:
        delivery = self._pending_digest_delivery
        if not delivery or delivery.get("packet") is not packet:
            return
        store = self._digest_decision_store
        if store is None:
            self._pending_digest_delivery = None
            return
        try:
            await store.record_delivered(
                dedupe_key=str(delivery["dedupe_key"]),
                snapshot_version=str(delivery["snapshot_version"]),
                signature_hash=str(delivery["signature_hash"]),
                semantic_signature_hash=str(delivery["semantic_signature_hash"]),
                message_ts=str(message_ts or ""),
                packet=packet,
                channel=channel,
                thread_ts=thread_ts or delivery.get("thread_ts"),
            )
        except Exception:
            logger.debug("Failed to persist supervisor digest delivery", exc_info=True)
        finally:
            self._pending_digest_delivery = None

    async def _record_digest_failed(
        self,
        packet,
        *,
        channel: str,
        error: Exception,
        thread_ts: str | None = None,
    ) -> None:
        delivery = self._pending_digest_delivery
        if not delivery or delivery.get("packet") is not packet:
            return
        store = self._digest_decision_store
        if store is None:
            prior_digest_state = delivery.get("prior_digest_state")
            if prior_digest_state is not None:
                (
                    self._last_digest_signature,
                    self._last_digest_semantic_signature,
                    self._last_digest_at,
                    self._pending_digest_packet,
                    self._pending_digest_signature,
                    self._pending_digest_semantic_signature,
                ) = prior_digest_state
            self._pending_digest_delivery = None
            return
        failure_recorded = False
        try:
            await store.record_failed(
                dedupe_key=str(delivery["dedupe_key"]),
                snapshot_version=str(delivery["snapshot_version"]),
                signature_hash=str(delivery["signature_hash"]),
                semantic_signature_hash=str(delivery["semantic_signature_hash"]),
                reason=f"{type(error).__name__}: {error}",
                packet=packet,
                channel=channel,
                thread_ts=thread_ts or delivery.get("thread_ts"),
            )
            failure_recorded = True
        except Exception:
            logger.debug("Failed to persist supervisor digest delivery failure", exc_info=True)
            try:
                pending_duplicate = await store.pending_duplicate_exists(
                    dedupe_key=str(delivery["dedupe_key"]),
                    signature_hash=str(delivery["signature_hash"]),
                    semantic_signature_hash=str(delivery["semantic_signature_hash"]),
                    semantic_dedupe=self._uses_semantic_digest_dedupe(packet),
                )
            except Exception:
                pending_duplicate = True
            failure_recorded = not bool(pending_duplicate)
        finally:
            prior_digest_state = delivery.get("prior_digest_state")
            if prior_digest_state is not None:
                (
                    self._last_digest_signature,
                    self._last_digest_semantic_signature,
                    self._last_digest_at,
                    self._pending_digest_packet,
                    self._pending_digest_signature,
                    self._pending_digest_semantic_signature,
                ) = prior_digest_state
            self._pending_digest_delivery = None if failure_recorded else delivery

    async def _record_digest_suppressed(
        self,
        packet,
        *,
        channel: str,
        reason: str,
        thread_ts: str | None = None,
    ) -> None:
        delivery = self._pending_digest_delivery
        if not delivery or delivery.get("packet") is not packet:
            return
        store = self._digest_decision_store
        if store is None:
            self._pending_digest_delivery = None
            return
        try:
            await store.record_suppressed(
                dedupe_key=str(delivery["dedupe_key"]),
                snapshot_version=str(delivery["snapshot_version"]),
                signature_hash=str(delivery["signature_hash"]),
                semantic_signature_hash=str(delivery["semantic_signature_hash"]),
                reason=reason,
                packet=packet,
                channel=channel,
                thread_ts=thread_ts or delivery.get("thread_ts"),
            )
        except Exception:
            logger.debug("Failed to persist supervisor digest suppression", exc_info=True)
        finally:
            self._pending_digest_delivery = None

    async def _initial_question_evidence(
        self,
        packet,
        question: str,
        *,
        toolbox,
    ) -> list[SupervisorEvidenceBundle]:
        requests = _detail_evidence_requests(packet, question)
        if not requests:
            return []
        try:
            return await toolbox.gather_many(requests)
        except Exception:
            logger.debug("Failed to preload supervisor question evidence", exc_info=True)
            return []

    def _take_pending_digest(self, now: float):
        packet = self._pending_digest_packet
        signature = self._pending_digest_signature
        semantic_signature = self._pending_digest_semantic_signature
        self._pending_digest_packet = None
        self._pending_digest_signature = None
        self._pending_digest_semantic_signature = None
        if packet is None or signature is None:
            return None
        self._last_digest_signature = signature
        self._last_digest_semantic_signature = semantic_signature
        self._last_digest_at = now
        return packet

    async def _question_with_thread_context(self, route: SupervisorSlackRoute) -> str:
        context = await self._thread_context(route)
        if not context:
            return route.text
        return f"{route.text}\n\n## Slack Thread Context\n{context}"

    async def _thread_context(self, route: SupervisorSlackRoute) -> str:
        if not route.thread_ts:
            return ""
        artifact_store = getattr(self._app, "artifact_store", None)
        if artifact_store is None:
            return ""
        prefix = thread_context_prefix(self._feature_id, route.thread_ts)
        try:
            rows = await self._list_thread_context_rows(artifact_store, prefix)
        except Exception:
            logger.debug("Failed to load supervisor thread context", exc_info=True)
            return ""
        for row in rows[:1]:
            value = row.get("value_preview") or row.get("value")
            if not value and hasattr(artifact_store, "get_slice") and row.get("id") is not None:
                try:
                    slice_row = await artifact_store.get_slice(
                        feature_id=self._feature_id,
                        artifact_id=int(row["id"]),
                        start=0,
                        chars=8_000,
                    )
                except Exception:
                    logger.debug("Failed to load supervisor thread context slice", exc_info=True)
                    slice_row = None
                value = (slice_row or {}).get("text") if slice_row else ""
            if not value:
                continue
            try:
                record = (
                    SupervisorThreadContextRecord.model_validate_json(value)
                    if isinstance(value, str)
                    else SupervisorThreadContextRecord.model_validate(value)
                )
            except Exception:
                continue
            if record.slack_thread_ts != route.thread_ts:
                continue
            return (
                f"Previous supervisor assessment in this thread: question={record.question!r}; "
                f"answered_status={record.assessment_status!r}; "
                f"answered_group={record.answered_group!r}; "
                f"live_group_at_answer={record.live_group_at_answer!r}. "
                "If the new message is a follow-up like 'is the group healthy?', resolve that "
                "against the thread focus, while still noting the current live group if different."
            )
        return ""

    async def _list_thread_context_rows(self, artifact_store, prefix: str) -> list[dict[str, Any]]:
        if hasattr(artifact_store, "list_record_summaries"):
            try:
                return await artifact_store.list_record_summaries(
                    feature_id=self._feature_id,
                    prefixes=(prefix,),
                    after_id=0,
                    limit=1,
                    order="desc",
                )
            except TypeError:
                try:
                    return await artifact_store.list_record_summaries(
                        feature_id=self._feature_id,
                        prefixes=(prefix,),
                        after_id=0,
                        limit=1,
                    )
                except TypeError:
                    return await artifact_store.list_record_summaries(
                        feature_id=self._feature_id,
                        prefixes=(prefix,),
                        after_id=0,
                    )
        if hasattr(artifact_store, "list_records"):
            try:
                return await artifact_store.list_records(
                    feature_id=self._feature_id,
                    prefixes=(prefix,),
                    after_id=0,
                    limit=1,
                    order="desc",
                )
            except TypeError:
                try:
                    return await artifact_store.list_records(
                        feature_id=self._feature_id,
                        prefixes=(prefix,),
                        after_id=0,
                        limit=1,
                    )
                except TypeError:
                    return await artifact_store.list_records(
                        feature_id=self._feature_id,
                        prefixes=(prefix,),
                        after_id=0,
                    )
        return []

    async def _write_action(self, record: SupervisorActionRecord, suffix: str) -> None:
        feature = await self._app.feature_store.get_feature(self._feature_id)
        if feature is None or not hasattr(self._app.artifact_store, "put"):
            return
        await self._app.artifact_store.put(
            action_key(record.feature_id, record.cursor, record.action, suffix),
            record.model_dump_json(),
            feature=feature,
        )

    async def _write_assessment(
        self,
        packet,
        question: str | None,
        assessment: SupervisorAssessment,
        bundles: list[SupervisorEvidenceBundle],
        fallback: bool,
        *,
        session_scope: str | None = None,
        slack_channel: str | None = None,
        slack_thread_ts: str | None = None,
        slack_user: str | None = None,
    ) -> None:
        feature_store = getattr(self._app, "feature_store", None)
        artifact_store = getattr(self._app, "artifact_store", None)
        if (
            feature_store is None
            or artifact_store is None
            or not hasattr(artifact_store, "put")
        ):
            return
        feature = await feature_store.get_feature(self._feature_id)
        if feature is None:
            return
        cursor = int(packet.facts.get("next_cursor") or packet.facts.get("cursor") or 0)
        key = assessment_key(
            self._feature_id,
            cursor,
            event_cursor=int(packet.facts.get("next_event_cursor") or 0),
            artifact_cursor=int(packet.facts.get("next_artifact_cursor") or 0),
            bridge_log_cursor=int(packet.facts.get("bridge_log_cursor") or 0),
        )
        persisted_assessment = assessment.model_copy(
            update={
                "session_epoch": self._session_epoch,
                "session_scope": session_scope,
            }
        )
        record = SupervisorAgentAssessmentRecord(
            feature_id=self._feature_id,
            cursor=cursor,
            question=question,
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            slack_user=slack_user,
            seed={"feature_id": self._feature_id, "packet": packet},
            evidence_bundles=[],
            evidence_requests=[
                bundle.request.model_dump(mode="json")
                for bundle in bundles
            ],
            evidence_artifact_refs=_assessment_artifact_refs(bundles),
            evidence_chunk_refs=_assessment_chunk_refs(bundles),
            assessment=persisted_assessment,
            fallback=fallback,
            fallback_reason=persisted_assessment.fallback_reason,
            prompt_chars=persisted_assessment.prompt_chars,
            round_count=persisted_assessment.round_count,
            evidence_artifact_count=persisted_assessment.evidence_artifact_count,
            evidence_summary_count=persisted_assessment.evidence_summary_count,
            omitted_detail_refs=persisted_assessment.omitted_detail_refs,
            evidence_mode=persisted_assessment.evidence_mode,
            tool_names_used=persisted_assessment.tool_names_used,
            session_epoch=self._session_epoch,
            session_scope=session_scope,
        )
        await artifact_store.put(key, record.model_dump_json(), feature=feature)
        if slack_thread_ts:
            current = packet.facts.get("current_workflow") if packet.facts else None
            current_group = current.get("group_idx") if isinstance(current, dict) else None
            context_record = SupervisorThreadContextRecord(
                feature_id=self._feature_id,
                question=question,
                slack_channel=slack_channel,
                slack_thread_ts=slack_thread_ts,
                slack_user=slack_user,
                source_assessment_key=key,
                assessment_status=persisted_assessment.status,
                answered_group=packet.group_idx,
                live_group_at_answer=current_group,
                fallback=fallback,
                citations=list(persisted_assessment.citations[:20]),
            )
            try:
                await artifact_store.put(
                    thread_context_key(
                        self._feature_id,
                        slack_thread_ts,
                        cursor,
                        event_cursor=int(packet.facts.get("next_event_cursor") or 0),
                        artifact_cursor=int(packet.facts.get("next_artifact_cursor") or 0),
                        bridge_log_cursor=int(packet.facts.get("bridge_log_cursor") or 0),
                    ),
                    context_record.model_dump_json(),
                    feature=feature,
                )
            except Exception:
                logger.debug("Failed to persist supervisor thread context", exc_info=True)

    def _question_session_scope(self, route: SupervisorSlackRoute) -> str:
        if route.thread_ts:
            return _session_scope("question-thread", route.thread_ts)
        if route.user:
            return _session_scope("question-user", route.user)
        return "question"

    def _action_session_scope(self, route: SupervisorSlackRoute) -> str:
        if route.thread_ts:
            return _session_scope("action-thread", route.thread_ts)
        if route.user:
            return _session_scope("action-user", route.user)
        return "action"


def _assessment_artifact_refs(bundles: list[SupervisorEvidenceBundle]) -> list[str]:
    refs: list[str] = []
    for bundle in bundles:
        for artifact in bundle.artifacts:
            refs.append(artifact.citation)
        for summary in bundle.artifact_summaries:
            refs.append(summary.citation)
    return _dedupe_strings(refs)


def _assessment_chunk_refs(bundles: list[SupervisorEvidenceBundle]) -> list[str]:
    refs: list[str] = []
    for bundle in bundles:
        refs.extend(bundle.request.artifact_chunks)
        refs.extend(chunk.chunk_ref for chunk in bundle.artifact_chunks)
    return _dedupe_strings(refs)


class SupervisorSlackRouter:
    """Classify and answer natural supervisor Slack messages."""

    def __init__(
        self,
        *,
        adapter: Any,
        channel: str,
        service: SupervisorSlackService,
        feature_id: str | None = None,
        dashboard_url: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._channel = channel
        self._service = service
        self._feature_id = feature_id
        self._dashboard_url = dashboard_url

    def classify(self, event: dict[str, Any]) -> SupervisorSlackRoute:
        channel = str(event.get("channel") or "")
        text = str(event.get("text") or "").strip()
        mentioned_bot = bool(event.get("mentioned_bot"))
        is_dm = channel.startswith("D")
        if not text or (channel != self._channel and not mentioned_bot and not is_dm):
            return self._route("ignore", event, text)
        if channel != self._channel:
            return self._route("supervisor_question", event, text)

        if _WORKFLOW_INSTRUCTION_RE.search(text):
            return self._route("workflow_instruction", event, text)
        if _ACTION_RE.search(text):
            return self._route("supervisor_action_request", event, text)
        return self._route("supervisor_question", event, text)

    async def handle_message(self, event: dict[str, Any]) -> None:
        route = self.classify(event)
        if route.kind == "ignore":
            return

        progress = SupervisorSlackProgress(
            adapter=self._adapter,
            channel=route.channel,
            thread_ts=route.thread_ts,
        )
        await progress.start()

        try:
            bind_progress = getattr(self._service, "bind_progress", None)
            if callable(bind_progress):
                async with bind_progress(progress):
                    reply = await self._reply(route)
            else:
                reply = await self._reply(route)
        except Exception as exc:
            logger.warning("Supervisor Slack reply failed", exc_info=True)
            reply = f"Supervisor failed while answering: {type(exc).__name__}: {exc}"

        if reply:
            blocks = (
                build_status_blocks(reply)
                if route.kind == "supervisor_question" and _DETAIL_QUESTION_RE.search(route.text or "")
                else None
            )
            await progress.finish(reply, blocks=blocks)

    async def handle_action(self, body: dict[str, Any], action: dict[str, Any]) -> None:
        action_id = str(action.get("action_id") or "")
        if not action_id.startswith("stale_codex_"):
            return
        value = str(action.get("value") or "") or _stale_token_from_action_id(action_id)
        if ":" in value:
            value = value.rsplit(":", 1)[-1]
        channel = str(body.get("channel", {}).get("id") or "")
        message = body.get("message", {}) or {}
        message_ts = str(message.get("ts") or "")
        thread_ts = str(message.get("thread_ts") or message_ts or "")
        user_id = str(body.get("user", {}).get("id") or "")
        if self._channel and channel != self._channel:
            reply = "Stale Codex actions are only allowed in the configured supervisor channel."
            if message_ts:
                await self._adapter.update_message(
                    channel,
                    message_ts,
                    text=reply,
                    blocks=build_resolved_notice_blocks(
                        "Stale Codex action blocked",
                        f"{reply}\n\nRequested by <@{user_id}>.",
                    ),
                )
            else:
                await self._adapter.post_blocks(
                    channel,
                    build_resolved_notice_blocks("Stale Codex action blocked", reply),
                    reply,
                    thread_ts=thread_ts,
                )
            return
        reply = await self._service.handle_stale_codex_action(action_id, value)
        if action_id.startswith(
            (
                "stale_codex_ignore_",
                "stale_codex_dismiss_",
                "stale_codex_kill_",
                "stale_codex_readonly_",
            )
        ):
            await self._adapter.update_message(
                channel,
                message_ts,
                text=reply,
                blocks=build_resolved_notice_blocks(
                    "Stale Codex action",
                    f"{reply}\n\nResolved by <@{user_id}>.",
                ),
            )
            return
        await self._adapter.post_blocks(
            channel,
            build_resolved_notice_blocks("Stale Codex details", reply),
            reply[:300],
            thread_ts=thread_ts,
        )

    async def _reply(self, route: SupervisorSlackRoute) -> str:
        if route.kind == "supervisor_question":
            return await self._service.answer_question(route)
        if route.kind == "supervisor_action_request":
            return await self._service.evaluate_action_request(route)
        return await self._service.route_workflow_instruction(route)

    def _route(
        self,
        kind: SupervisorRouteKind,
        event: dict[str, Any],
        text: str,
    ) -> SupervisorSlackRoute:
        return SupervisorSlackRoute(
            kind=kind,
            text=text,
            channel=str(event.get("channel") or ""),
            user=str(event.get("user") or ""),
            feature_id=self._feature_id,
            dashboard_url=self._dashboard_url,
            thread_ts=str(event.get("thread_ts") or event.get("ts") or "") or None,
        )


async def run_supervisor_slack_app(
    *,
    channel: str,
    feature_id: str | None = None,
    dashboard_url: str | None = None,
    runtime: str = "codex",
    mode: SupervisorSlackMode = "singleplayer",
    supervisor_mode: SupervisorMode | str = SupervisorMode.READ_ONLY,
    poll_interval_seconds: float = 30.0,
    min_digest_interval_seconds: float = 120.0,
    worktree_roots: list[str | Path] | None = None,
    forbidden_paths: list[str] | None = None,
    app_token_env: str = "SUPERVISOR_SLACK_APP_TOKEN",
    bot_token_env: str = "SUPERVISOR_SLACK_BOT_TOKEN",
    service: SupervisorSlackService | None = None,
) -> None:
    """Start the separate supervisor Slack bot using its own token env names."""
    if mode not in {"multiplayer", "singleplayer"}:
        raise ValueError("mode must be 'multiplayer' or 'singleplayer'")

    app_token = os.environ.get(app_token_env, "")
    bot_token = os.environ.get(bot_token_env, "")
    if not app_token:
        raise RuntimeError(f"{app_token_env} environment variable is required.")
    if not bot_token:
        raise RuntimeError(f"{bot_token_env} environment variable is required.")

    from iriai_build_v2.interfaces.slack.adapter import SlackAdapter

    adapter = SlackAdapter(
        app_token=app_token,
        bot_token=bot_token,
        planning_channel="",
        mode=mode,
    )
    adapter.set_channel_mode(channel, mode)

    env = None
    watch_task: asyncio.Task | None = None
    supervisor_service: SupervisorSlackService
    if service is not None:
        supervisor_service = service
    elif feature_id:
        from ..runtimes import create_agent_runtime
        from .evidence import UrlDashboardClient

        env = await _bootstrap_supervisor_env()
        mode_value = SupervisorMode(supervisor_mode)
        dashboard_client = UrlDashboardClient(dashboard_url) if dashboard_url else None
        feature = await env.feature_store.get_feature(feature_id)

        async def _restart_bridge() -> dict[str, Any]:
            if dashboard_client is None:
                raise RuntimeError("dashboard_url is required for bridge restart")
            return await dashboard_client.post_json("/api/bridge/restart")

        action_policy = ActionPolicy(
            mode=mode_value,
            restart=_restart_bridge if dashboard_client is not None else None,
            artifact_sink=env.artifacts,
            feature=feature,
        )
        supervisor_app = SupervisorApp(
            feature_store=env.feature_store,
            artifact_store=env.artifacts,
            mode=mode_value,
            dashboard_url=dashboard_url,
            dashboard_client=dashboard_client,
            worktree_roots=worktree_roots,
            forbidden_paths=forbidden_paths,
            action_policy=action_policy,
        )
        agent_runtime = create_agent_runtime(runtime, session_store=env.sessions)
        supervisor_service = SupervisorRuntimeService(
            app=supervisor_app,
            feature_id=feature_id,
            agent_runtime=agent_runtime,
            poll_interval_seconds=poll_interval_seconds,
            min_digest_interval_seconds=min_digest_interval_seconds,
            action_policy=action_policy,
        )
    else:
        supervisor_service = PlaceholderSupervisorService(runtime=runtime)

    router = SupervisorSlackRouter(
        adapter=adapter,
        channel=channel,
        service=supervisor_service,
        feature_id=feature_id,
        dashboard_url=dashboard_url,
    )
    adapter.on_message_callback = router.handle_message
    adapter.on_action_callback = router.handle_action

    await adapter.connect()
    print("\niriai-build-v2 Supervisor Slack bot")
    print(f"  Channel: {channel}")
    print(f"  Feature: {feature_id or 'any'}")
    print(f"  Dashboard URL: {dashboard_url or 'none'}")
    print(f"  Runtime: {runtime}")
    print(f"  Mode: {mode}")
    print(f"  Bot: @{adapter.bot_user_id}\n")

    if isinstance(supervisor_service, SupervisorRuntimeService) and poll_interval_seconds > 0:
        watch_task = asyncio.create_task(
            supervisor_service.watch_and_digest(adapter, channel)
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        print("\nShutting down supervisor Slack bot...")
        if watch_task is not None:
            watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await watch_task
        await adapter.disconnect()
        if env is not None:
            await env.pool.close()


async def _bootstrap_supervisor_env() -> SimpleNamespace:
    """Initialize only the stores needed by the supervisor process."""
    from ..config import DATABASE_URL
    from ..db import create_pool, ensure_schema
    from ..public_dashboard import PublicDashboardOutbox
    from ..storage import (
        PostgresArtifactStore,
        PostgresFeatureStore,
        PostgresSessionStore,
    )

    pool = await create_pool(DATABASE_URL)
    await ensure_schema(pool)
    public_dashboard = PublicDashboardOutbox(pool)
    return SimpleNamespace(
        pool=pool,
        artifacts=PostgresArtifactStore(pool, public_dashboard=public_dashboard),
        feature_store=PostgresFeatureStore(pool, public_dashboard=public_dashboard),
        sessions=PostgresSessionStore(pool),
    )
