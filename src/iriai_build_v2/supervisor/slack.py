"""Supervisor Slack app wiring and natural-message routing."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import logging
import os
import re
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Literal, Protocol

from .actions import ActionPolicy
from .agent import SupervisorAgent
from .app import SupervisorApp
from .models import (
    ActionLevel,
    SupervisorAgentAssessmentRecord,
    SupervisorAssessment,
    SupervisorActionRecord,
    SupervisorActionStatus,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
    SupervisorMode,
    action_key,
    assessment_key,
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
    r"failure|failed|fail|root cause|why|stuck|blocked|blocker|what happened|"
    r"what changed|recent update|most recent|revision|revisions|revise|cycle|"
    r"cycles|retry|retries|fix|fixed|repair|repaired"
    r")\b",
    re.IGNORECASE,
)
_GROUP_RE = re.compile(r"\b(?:g|group)\s*[-#:]?\s*(\d{1,3})\b", re.IGNORECASE)
_ARTIFACT_ID_RE = re.compile(r"\bid=(\d+)\b")
_WORKFLOW_INSTRUCTION_RE = re.compile(
    r"\b(tell|ask|send|forward|route|pass|let)\b.*\b("
    r"workflow|agent|implementer|verifier|reviewer|runner|pm|designer|architect"
    r")\b",
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

    async def finish(self, text: str) -> None:
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
    artifact_ids = _artifact_ids_from_packet(packet)
    current = packet.facts.get("current_workflow") if packet.facts else None
    latest_event_id = 0
    if isinstance(current, dict):
        latest_event_id = int(current.get("latest_event_id") or 0)
    event_after_id = max(0, latest_event_id - 250) if latest_event_id else None
    prefixes: list[str] = []
    for group in groups[:3]:
        prefixes.extend(_group_detail_prefixes(group))
    prefixes = _dedupe_strings(prefixes)[:20]
    return [
        SupervisorInvestigationRequest(
            reason=(
                "Operator asked for failure/root-cause/revision detail; preload "
                "current-group verify, RCA, repair, route, and commit evidence."
            ),
            artifact_prefixes=prefixes,
            artifact_ids=artifact_ids,
            artifact_after_id=0,
            event_after_id=event_after_id,
            event_limit=200,
            include_bridge=True,
            include_worktrees=True,
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


def _artifact_ids_from_packet(packet) -> list[int]:
    ids: list[int] = []
    for citation in packet.citations or []:
        for match in _ARTIFACT_ID_RE.finditer(str(citation)):
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
    ) -> None:
        self._app = app
        self._feature_id = feature_id
        self._agent = agent or SupervisorAgent()
        self._agent_runtime = agent_runtime
        self._poll_interval_seconds = poll_interval_seconds
        self._min_digest_interval_seconds = min_digest_interval_seconds
        self._action_policy = action_policy
        self._workflow_instruction_sink = workflow_instruction_sink
        self._cursor = 0
        self._event_cursor = 0
        self._artifact_cursor = 0
        self._bridge_log_cursor = 0
        self._last_digest_signature: tuple[str, ...] | None = None
        self._last_digest_at = 0.0
        self._pending_digest_packet: Any | None = None
        self._pending_digest_signature: tuple[str, ...] | None = None

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
        toolbox = self._app.evidence_toolbox(self._feature_id)
        initial_bundles = await self._initial_question_evidence(
            packet,
            question,
            toolbox=toolbox,
        )
        return await self._agent.compose_message(
            packet,
            question=question,
            runtime=self._agent_runtime,
            feature_id=self._feature_id,
            toolbox=toolbox,
            initial_bundles=initial_bundles,
            assessment_sink=lambda assessment, bundles, fallback: self._write_assessment(
                packet,
                route.text,
                assessment,
                bundles,
                fallback,
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
        )
        await self._write_assessment(
            packet,
            route.text,
            assessment,
            bundles,
            fallback,
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
        if self._workflow_instruction_sink is not None:
            result = await self._workflow_instruction_sink(route)
            return (
                "I forwarded that workflow instruction through the configured sink. "
                f"Result: `{result}`"
            )

        record = SupervisorActionRecord(
            feature_id=self._feature_id,
            cursor=int(packet.facts.get("next_cursor") or 0),
            action="workflow_instruction",
            mode=self._app.mode,
            status=SupervisorActionStatus.BLOCKED,
            reason=(
                "Separate supervisor bot captured a workflow instruction, but "
                "no bridge/workflow instruction sink is configured yet."
            ),
            before={"text": route.text, "channel": route.channel, "user": route.user},
            packet=packet,
        )
        await self._write_action(record, "blocked")
        return (
            "I captured that workflow instruction, but this supervisor process "
            "does not yet have a live workflow-instruction sink. I did not send "
            "it to an implementer/verifier."
        )

    async def watch_and_digest(self, adapter: Any, channel: str) -> None:
        """Poll evidence and send one agent-written digest per material change."""
        while True:
            try:
                packet = await self._packet()
                digest_packet = self._digest_packet_to_send(packet)
                if digest_packet is not None:
                    message = await self._agent.compose_message(
                        digest_packet,
                        runtime=self._agent_runtime,
                        feature_id=self._feature_id,
                        toolbox=self._app.evidence_toolbox(self._feature_id),
                        assessment_sink=lambda assessment, bundles, fallback: self._write_assessment(
                            digest_packet,
                            None,
                            assessment,
                            bundles,
                            fallback,
                            slack_channel=channel,
                        ),
                    )
                    await adapter.post_message(channel, message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Supervisor watch digest failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_seconds)

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
        active_agents = ""
        queued_agents = ""
        latest_event_id = ""
        latest_artifact_id = ""
        if isinstance(current, dict):
            current_state = str(current.get("state") or "")
            active_agents = ",".join(str(item) for item in current.get("active_agents") or [])
            queued_agents = ",".join(str(item) for item in current.get("queued_agents") or [])
            latest_event_id = str(current.get("latest_event_id") or "")
            latest_artifact_id = str(current.get("latest_artifact_id") or "")
        return (
            packet.classification.value,
            str(packet.group_idx),
            str(packet.retry),
            packet.recommended_action.value,
            current_state,
            active_agents,
            queued_agents,
            latest_event_id,
            latest_artifact_id,
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
        if signature == self._last_digest_signature:
            return None
        if (
            self._last_digest_at > 0
            and now - self._last_digest_at < self._min_digest_interval_seconds
        ):
            self._pending_digest_packet = packet
            self._pending_digest_signature = signature
            return None
        self._pending_digest_packet = None
        self._pending_digest_signature = None
        self._last_digest_signature = signature
        self._last_digest_at = now
        return packet

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
        self._pending_digest_packet = None
        self._pending_digest_signature = None
        if packet is None or signature is None:
            return None
        self._last_digest_signature = signature
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
        if artifact_store is None or not hasattr(artifact_store, "list_records"):
            return ""
        try:
            rows = await artifact_store.list_records(
                feature_id=self._feature_id,
                prefixes=(f"supervisor-agent-assessment:{self._feature_id}:",),
                after_id=0,
                limit=100,
                order="desc",
            )
        except TypeError:
            try:
                rows = await artifact_store.list_records(
                    feature_id=self._feature_id,
                    prefixes=(f"supervisor-agent-assessment:{self._feature_id}:",),
                    after_id=0,
                    limit=100,
                )
            except TypeError:
                rows = await artifact_store.list_records(
                    feature_id=self._feature_id,
                    prefixes=(f"supervisor-agent-assessment:{self._feature_id}:",),
                    after_id=0,
                )
        except Exception:
            logger.debug("Failed to load supervisor thread context", exc_info=True)
            return ""
        for row in rows:
            value = row.get("value")
            try:
                record = (
                    SupervisorAgentAssessmentRecord.model_validate_json(value)
                    if isinstance(value, str)
                    else SupervisorAgentAssessmentRecord.model_validate(value)
                )
            except Exception:
                continue
            if record.slack_thread_ts != route.thread_ts:
                continue
            seed_packet = record.seed.packet
            current = seed_packet.facts.get("current_workflow") if seed_packet.facts else None
            current_group = None
            if isinstance(current, dict):
                current_group = current.get("group_idx")
            return (
                f"Previous supervisor assessment in this thread: question={record.question!r}; "
                f"answered_status={record.assessment.status!r}; "
                f"answered_group={seed_packet.group_idx!r}; live_group_at_answer={current_group!r}. "
                "If the new message is a follow-up like 'is the group healthy?', resolve that "
                "against the thread focus, while still noting the current live group if different."
            )
        return ""

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
        record = SupervisorAgentAssessmentRecord(
            feature_id=self._feature_id,
            cursor=cursor,
            question=question,
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            slack_user=slack_user,
            seed={"feature_id": self._feature_id, "packet": packet},
            evidence_bundles=bundles,
            assessment=assessment,
            fallback=fallback,
        )
        await artifact_store.put(key, record.model_dump_json(), feature=feature)


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
        if channel != self._channel or not text:
            return self._route("ignore", event, text)

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
            await progress.finish(reply)

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
