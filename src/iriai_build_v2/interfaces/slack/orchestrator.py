"""Slack workflow orchestrator.

Routes messages from #planning to create feature channels and start workflows.
Manages the lifecycle of concurrent workflow runs.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import DASHBOARD_BASE_URL
from ...workflows.bugfix_v2.models import BugflowReportSnapshot, new_short_id, report_key, utc_now
from .._bootstrap import (
    bootstrap,
    create_feature,
    rebuild_state,
    select_workflow,
    build_state,
    slugify,
    teardown,
)
from ...runtimes import create_agent_runtime
from ..auto_interaction import AgentDelegateInteractionRuntime
from .parser import parse_workflow_request
from .streamer import SlackStreamer

if TYPE_CHECKING:
    from .adapter import SlackAdapter
    from .interaction import SlackInteractionRuntime
    from .._bootstrap import BootstrappedEnv

logger = logging.getLogger(__name__)

_SILENT_INVOCATION_NOTICE_DELAY = 8.0
_SILENT_INVOCATION_UPDATE_INTERVAL = 15.0

# Roles that benefit from persistent client + mid-stream user message injection.
# Short-lived reviewers use the fast ephemeral client path.
_INTERACTIVE_ROLES = {
    "pm",
    "designer",
    "architect",
    "task_planner",
    "implementer",
    "bug-interviewer",
    "observation-collector",
}


@dataclass
class _SilentInvocationState:
    actor_name: str
    started_at: float
    last_activity: float
    timeout_seconds: int
    notice_ts: str | None = None
    last_notice_update_at: float = 0.0


class _SlackInvocationObserver:
    """Post heartbeat updates when an invocation is alive but Slack is quiet."""

    def __init__(
        self,
        adapter: SlackAdapter,
        channel_id: str,
        streamer: SlackStreamer | None,
    ) -> None:
        self._adapter = adapter
        self._channel_id = channel_id
        self._streamer = streamer
        self._states: dict[str, _SilentInvocationState] = {}
        self._monitor_tasks: dict[str, asyncio.Task[None]] = {}

    def on_invocation_start(self, invocation_id: str, **payload: Any) -> None:
        now = time.monotonic()
        self._states[invocation_id] = _SilentInvocationState(
            actor_name=str(payload.get("actor_name") or "agent"),
            started_at=now,
            last_activity=now,
            timeout_seconds=int(payload.get("timeout_seconds") or 0),
        )
        self._monitor_tasks[invocation_id] = asyncio.create_task(
            self._monitor_invocation(invocation_id)
        )

    def on_invocation_activity(self, invocation_id: str, **_payload: Any) -> None:
        state = self._states.get(invocation_id)
        if state is not None:
            state.last_activity = time.monotonic()

    def on_invocation_finish(self, invocation_id: str, **_payload: Any) -> None:
        state = self._states.pop(invocation_id, None)
        task = self._monitor_tasks.pop(invocation_id, None)
        if task is not None:
            task.cancel()
        if state is None or state.notice_ts is None:
            return
        asyncio.create_task(self._mark_finished(state))

    async def _monitor_invocation(self, invocation_id: str) -> None:
        try:
            while True:
                state = self._states.get(invocation_id)
                if state is None:
                    return

                await asyncio.sleep(
                    _SILENT_INVOCATION_NOTICE_DELAY
                    if state.notice_ts is None
                    else _SILENT_INVOCATION_UPDATE_INTERVAL
                )

                state = self._states.get(invocation_id)
                if state is None:
                    return

                if self._has_visible_stream_since(state.started_at):
                    return

                text = self._build_notice_text(state)
                now = time.monotonic()
                if state.notice_ts is None:
                    state.notice_ts = await self._adapter.post_message(
                        self._channel_id, text
                    )
                else:
                    await self._adapter.update_message(
                        self._channel_id, state.notice_ts, text=text
                    )
                state.last_notice_update_at = now
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning(
                "Failed to post silent-invocation notice in %s",
                self._channel_id,
                exc_info=True,
            )

    def _has_visible_stream_since(self, started_at: float) -> bool:
        if self._streamer is None:
            return False
        return self._streamer.last_visible_update_at >= started_at

    def _build_notice_text(self, state: _SilentInvocationState) -> str:
        now = time.monotonic()
        running_for = max(0, int(now - state.started_at))
        idle_for = max(0, int(now - state.last_activity))
        return (
            f"_{state.actor_name}_ is still running, but it hasn't produced "
            f"Slack-visible progress yet.\n"
            f"Running for: `{running_for}s`  Last runtime activity: `{idle_for}s` ago."
        )

    async def _mark_finished(self, state: _SilentInvocationState) -> None:
        try:
            await self._adapter.update_message(
                self._channel_id,
                state.notice_ts,
                text=f"\u2713 `{state.actor_name}` finished after a silent run.",
            )
        except Exception:
            logger.debug(
                "Failed to update silent-invocation notice to done",
                exc_info=True,
            )


class SlackWorkflowOrchestrator:
    """Manages workflow lifecycle, mode selection, and message routing."""

    def __init__(
        self,
        adapter: SlackAdapter,
        interaction_runtime: SlackInteractionRuntime,
        workspace_path: Path | None = None,
        agent_runtime_name: str = "claude",
        agent_runtime_override: bool = False,
        single_agent_runtime: bool = False,
        budget: bool = False,
        autonomous_remainder: bool = False,
    ) -> None:
        self._adapter = adapter
        self._interaction = interaction_runtime
        self._default_workspace = workspace_path  # suggestion, not binding
        self._agent_runtime_name = agent_runtime_name
        self._agent_runtime_override = agent_runtime_override
        self._single_agent_runtime = single_agent_runtime
        self._budget = budget
        self._autonomous_remainder = autonomous_remainder
        self._env: BootstrappedEnv | None = None

        # Workflow tracking
        self._active_workflows: dict[str, asyncio.Task] = {}  # feature_id → task
        self._active_runtimes: dict[str, Any] = {}  # feature_id → runtime instance
        self._feature_streamers: dict[str, SlackStreamer] = {}
        self._channel_features: dict[str, str] = {}  # channel → feature_id
        self._feature_workflows: dict[str, str] = {}  # feature_id → workflow_name
        self._user_notes: dict[str, list[str]] = {}  # feature_id → queued notes

        # Recovery: features that survived a restart and can be resumed on first message
        self._recoverable_features: dict[str, dict] = {}  # feature_id → recovery info

        # Mode / workspace selection pending futures
        self._mode_futures: dict[str, asyncio.Future] = {}  # decision_id → Future
        self._workspace_futures: dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        """Bootstrap environment and wire adapter callbacks."""
        self._env = await bootstrap(self._default_workspace)
        self._adapter.on_message_callback = self._on_message
        self._adapter.on_action_callback = self._on_action
        self._adapter.on_view_submission_callback = self._on_view_submission

        # Start artifact server + optional tunnel
        from ...services.tunnel import CloudflareTunnel

        self._tunnel: CloudflareTunnel | None = None
        try:
            self._tunnel = CloudflareTunnel()
            tunnel_url = await self._tunnel.start(self._env.artifact_mirror._base)
            if tunnel_url:
                logger.info("Tunnel started: %s", tunnel_url)
            else:
                logger.info("Artifact server started (local only, no tunnel)")
        except Exception:
            logger.warning("Artifact server failed to start", exc_info=True)
            self._tunnel = None

        await self._recover_active_features()
        logger.info(
            "Orchestrator started, default_workspace=%s, agent_runtime=%s, single_agent_runtime=%s, autonomous_remainder=%s",
            self._default_workspace,
            self._agent_runtime_name,
            self._single_agent_runtime,
            self._autonomous_remainder,
        )

    async def shutdown(self) -> None:
        """Cancel active workflows, stop tunnel, and teardown environment."""
        for feature_id, task in list(self._active_workflows.items()):
            task.cancel()
        if self._tunnel:
            try:
                await self._tunnel.stop_all()
            except Exception:
                logger.warning("Failed to stop tunnel", exc_info=True)
        if self._env:
            await teardown(self._env)

    # ── Inbound Routing ───────────────────────────────────────────────────

    async def _on_message(self, event: dict) -> None:
        channel = event.get("channel", "")
        text = event.get("text", "")

        # Messages in #planning: check for workflow trigger
        if channel == self._adapter.planning_channel:
            parsed = parse_workflow_request(text)
            if parsed and parsed.workflow_name == "full-develop":
                await self._start_workflow(parsed, event)
            elif parsed and parsed.workflow_name == "bugfix-v2":
                await self._start_bugflow_workflow(parsed, event)
            return

        # Messages in workflow channels
        feature_id = self._channel_features.get(channel)
        if not feature_id:
            return

        if await self._maybe_capture_bugflow_report(feature_id, event):
            return

        if self._interaction.has_pending(channel):
            # Mode 1: card pending → resolve it
            await self._interaction.handle_message(event)
        elif self._has_active_agent(feature_id):
            # Mode 2: agent working → inject mid-stream
            runtime = self._active_runtimes.get(feature_id)
            injected = False
            if runtime:
                injected = await runtime.inject_user_message(feature_id, text)
            if injected:
                await self._adapter.add_reaction(
                    channel, event.get("ts", ""), "eyes"
                )
            else:
                self._queue_user_note(feature_id, text)
                await self._adapter.add_reaction(
                    channel, event.get("ts", ""), "memo"
                )
        elif feature_id in self._recoverable_features and feature_id not in self._active_workflows:
            # Mode 3a: recovered feature, no active workflow → resume
            await self._adapter.add_reaction(
                channel, event.get("ts", ""), "rocket"
            )
            await self._resume_workflow(feature_id, channel)
        else:
            # Mode 3b: between phases → accumulate as user_notes
            self._queue_user_note(feature_id, text)
            await self._adapter.add_reaction(
                channel, event.get("ts", ""), "memo"
            )

    async def _on_action(self, body: dict, action: dict) -> None:
        if self._env is not None:
            channel_id = body.get("channel", {}).get("id", "")
            feature_id = self._channel_features.get(channel_id)
            if feature_id:
                await self._env.feature_store.log_event(
                    feature_id,
                    "slack_action_received",
                    "slack-orchestrator",
                    content=action.get("action_id", ""),
                    metadata={
                        "channel": channel_id,
                        "user_id": body.get("user", {}).get("id", ""),
                    },
                )
        action_id = action.get("action_id", "")

        # Mode selection actions
        if action_id.startswith("decision_mode_"):
            await self._handle_mode_selection(body, action)
            return

        # Workspace selection actions
        if action_id.startswith("workspace_"):
            await self._handle_workspace_action(body, action)
            return

        # Forward to interaction runtime
        await self._interaction.handle_action(body, action)

    async def _on_view_submission(self, payload: dict) -> None:
        """Forward modal submissions — workspace or interaction runtime."""
        view = payload.get("view", {})
        private_metadata = view.get("private_metadata", "")
        if private_metadata.startswith("ws_"):
            await self._handle_workspace_submission(payload)
        else:
            await self._interaction.handle_view_submission(payload)

    # ── Workflow Startup ──────────────────────────────────────────────────

    async def _start_workflow(self, parsed: Any, trigger_event: dict) -> None:
        assert self._env is not None

        # 1. React to trigger
        await self._adapter.add_reaction(
            self._adapter.planning_channel,
            trigger_event.get("ts", ""),
            "rocket",
        )

        # 2. Create feature (to get feature_id for channel name)
        feature = await create_feature(
            self._env.feature_store, parsed.feature_name, parsed.workflow_name
        )
        self._feature_workflows[feature.id] = parsed.workflow_name

        # 3. Create channel
        channel_name = f"iriai-{slugify(parsed.feature_name)}-{feature.id}"
        channel_id = await self._adapter.create_channel(channel_name)
        self._channel_features[channel_id] = feature.id

        # 4. Reply in thread of the trigger message with channel link
        trigger_ts = trigger_event.get("ts", "")
        if trigger_ts:
            from .helpers import post_to_thread

            await post_to_thread(
                self._adapter.web,
                self._adapter.planning_channel,
                trigger_ts,
                f"Workflow started in <#{channel_id}>",
            )

        # 5. Post dashboard URL first for bugflow-style workflows.
        await self._maybe_post_dashboard_url(
            feature.id,
            channel_id,
            workflow_name=parsed.workflow_name,
            recovery=False,
        )

        # 6. Post mode selection card
        mode = await self._ask_mode_selection(channel_id, feature.id)
        self._adapter.set_channel_mode(channel_id, mode)

        # 7. Ask which project workspace to target
        workspace_path = await self._ask_workspace(channel_id, feature.id)

        # 7b. Persist channel/workspace/mode for resume after restart
        await self._env.feature_store.update_metadata(feature.id, {
            "channel_id": channel_id,
            "workspace_path": str(workspace_path),
            "mode": mode,
            "agent_runtime": self._agent_runtime_name,
        })
        feature = await self._env.feature_store.get_feature(feature.id) or feature

        # 8. Register channel with interaction runtime
        self._interaction.register_channel(feature.id, channel_id)

        # 9. Create streamer + runner with per-feature workspace
        agent_runtime, runner = self._create_runtime_and_runner(
            workspace_path=workspace_path,
            channel_id=channel_id,
            runtime_name=self._agent_runtime_name,
        )
        self._active_runtimes[feature.id] = agent_runtime
        streamer = getattr(runner, "_slack_streamer", None)
        if isinstance(streamer, SlackStreamer):
            self._feature_streamers[feature.id] = streamer

        # 10. Select workflow + build state
        workflow = select_workflow(parsed.workflow_name)
        state = build_state(parsed.workflow_name)

        # 11. Seed project artifact
        await self._env.artifacts.put(
            "project",
            f"Project workspace: {workspace_path}\n\nFeature: {parsed.feature_name}",
            feature=feature,
        )

        # 12. Post kickoff message
        await self._adapter.post_message(
            channel_id,
            f"Starting *{parsed.workflow_name}* workflow for: *{parsed.feature_name}*\n"
            f"Workspace: `{workspace_path}`\nMode: _{mode}_\nRuntime: _{self._agent_runtime_name}_",
        )

        # 13. Launch workflow as background task
        task = asyncio.create_task(
            self._run_workflow(runner, workflow, feature, state, channel_id)
        )
        self._active_workflows[feature.id] = task

    async def _start_bugflow_workflow(self, parsed: Any, trigger_event: dict) -> None:
        assert self._env is not None

        trigger_ts = trigger_event.get("ts", "")
        await self._adapter.add_reaction(
            self._adapter.planning_channel,
            trigger_ts,
            "rocket",
        )

        source_feature_id = parsed.source_feature_id or parsed.feature_name
        source_feature = await self._env.feature_store.get_feature(source_feature_id)
        if not source_feature:
            await self._post_planning_error(
                trigger_ts,
                f"Could not start bugflow: source feature `{source_feature_id}` was not found.",
            )
            return

        source_workspace = str(source_feature.metadata.get("workspace_path", "") or "")
        if not source_workspace:
            await self._post_planning_error(
                trigger_ts,
                f"Could not start bugflow for `{source_feature_id}`: source feature is missing `workspace_path` metadata.",
            )
            return

        feature_name = f"Bugflow: {source_feature.name}"
        feature = await create_feature(
            self._env.feature_store,
            feature_name,
            "bugfix-v2",
        )
        self._feature_workflows[feature.id] = "bugfix-v2"
        channel_id = ""
        try:
            channel_name = f"iriai-{slugify(source_feature.name)}-bugs-{feature.id}"
            channel_id = await self._adapter.create_channel(channel_name)
            self._channel_features[channel_id] = feature.id
            self._adapter.set_channel_mode(channel_id, "singleplayer")

            if trigger_ts:
                from .helpers import post_to_thread

                await post_to_thread(
                    self._adapter.web,
                    self._adapter.planning_channel,
                    trigger_ts,
                    f"Bugflow started in <#{channel_id}>",
                )

            await self._env.feature_store.update_metadata(
                feature.id,
                {
                    "channel_id": channel_id,
                    "workspace_path": source_workspace,
                    "mode": "singleplayer",
                    "agent_runtime": self._agent_runtime_name,
                    "source_feature_id": source_feature.id,
                    "source_feature_name": source_feature.name,
                    "source_channel_id": source_feature.metadata.get("channel_id", ""),
                },
            )
            await self._env.feature_store.transition_phase(feature.id, "bugflow-setup")
            feature = await self._env.feature_store.get_feature(feature.id) or feature

            await self._maybe_post_dashboard_url(
                feature.id,
                channel_id,
                workflow_name="bugfix-v2",
                recovery=False,
            )

            self._interaction.register_channel(feature.id, channel_id)
            agent_runtime, runner = self._create_runtime_and_runner(
                workspace_path=Path(source_workspace),
                channel_id=channel_id,
                runtime_name=self._agent_runtime_name,
            )
            self._active_runtimes[feature.id] = agent_runtime

            workflow = select_workflow("bugfix-v2")
            state = build_state("bugfix-v2")
            state.source_feature_id = source_feature.id
            state.source_feature_name = source_feature.name
            state.source_workspace_path = source_workspace

            await self._adapter.post_message(
                channel_id,
                f"Starting *bugfix-v2* for source feature *{source_feature.name}* (`{source_feature.id}`)\n"
                f"Workspace: `{source_workspace}`\nMode: _singleplayer_\nRuntime: _{self._agent_runtime_name}_",
            )

            task = asyncio.create_task(
                self._run_workflow(runner, workflow, feature, state, channel_id)
            )
            self._active_workflows[feature.id] = task
        except Exception as exc:
            logger.exception("Failed to start bugflow workflow for %s", source_feature_id)
            self._active_runtimes.pop(feature.id, None)
            self._feature_workflows.pop(feature.id, None)
            self._interaction.unregister_channel(feature.id)
            if channel_id:
                self._channel_features.pop(channel_id, None)
            try:
                await self._env.feature_store.transition_phase(feature.id, "failed")
            except Exception:
                logger.warning(
                    "Failed to mark bugflow feature %s as failed after launch error",
                    feature.id,
                    exc_info=True,
                )
            await self._post_planning_error(
                trigger_ts,
                f"Could not start bugflow for `{source_feature_id}`: {exc}",
            )
            if channel_id:
                try:
                    await self._adapter.post_message(
                        channel_id,
                        f"Bugflow startup failed: {exc}",
                    )
                except Exception:
                    logger.warning(
                        "Failed to post bugflow launch failure in %s",
                        channel_id,
                        exc_info=True,
                    )
            return

    async def _run_workflow(
        self,
        runner: Any,
        workflow: Any,
        feature: Any,
        state: Any,
        channel_id: str,
    ) -> None:
        try:
            observer = self._make_invocation_observer(feature.id, feature.workflow_name, channel_id)
            binder = getattr(runner, "bind_invocation_observer", None)
            with binder(observer) if observer is not None and callable(binder) else nullcontext():
                await runner.execute_workflow(workflow, feature, state)
            await self._adapter.post_message(channel_id, "Workflow complete!")
            await self._adapter.add_reaction(
                self._adapter.planning_channel, "", "white_check_mark"
            )
        except Exception as e:
            logger.exception("Workflow failed for %s", feature.id)
            if feature.workflow_name == "bugfix-v2":
                current_feature = await self._env.feature_store.get_feature(feature.id) or feature
                current_meta = current_feature.metadata or {}
                self._recoverable_features[feature.id] = {
                    "workspace_path": str(current_meta.get("workspace_path", "") or ""),
                    "mode": str(current_meta.get("mode", "singleplayer") or "singleplayer"),
                    "phase": str(current_meta.get("_db_phase", "bugflow-setup") or "bugflow-setup"),
                    "agent_runtime": str(current_meta.get("agent_runtime", self._agent_runtime_name) or self._agent_runtime_name),
                }
                self._feature_workflows[feature.id] = current_feature.workflow_name
                self._interaction.register_channel(feature.id, channel_id)
            await self._adapter.post_message(
                channel_id,
                f"Workflow failed: {e}"
                + ("\nSend any message to retry." if feature.workflow_name == "bugfix-v2" else ""),
            )
        finally:
            self._active_workflows.pop(feature.id, None)
            self._active_runtimes.pop(feature.id, None)
            self._feature_streamers.pop(feature.id, None)
            if feature.id not in self._recoverable_features:
                self._feature_workflows.pop(feature.id, None)
                self._interaction.unregister_channel(feature.id)
            self._user_notes.pop(feature.id, None)

    # ── Mode Selection ────────────────────────────────────────────────────

    async def _ask_mode_selection(self, channel_id: str, feature_id: str) -> str:
        """Post mode selection card and wait for user choice. Returns mode string."""
        decision_id = f"mode_{feature_id}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._mode_futures[decision_id] = future

        await self._adapter.post_decision(
            channel_id,
            decision_id,
            "Choose Interaction Mode",
            "How should this channel handle messages?",
            [
                {
                    "id": "singleplayer",
                    "label": "Singleplayer",
                    "style": "primary",
                },
                {
                    "id": "multiplayer",
                    "label": "Multiplayer",
                },
            ],
        )

        try:
            mode = await future
        finally:
            self._mode_futures.pop(decision_id, None)

        return mode

    async def _handle_mode_selection(self, body: dict, action: dict) -> None:
        """Resolve a mode selection button click."""
        action_id = action.get("action_id", "")
        # action_id format: decision_mode_{feature_id}_{option_id}
        parts = action_id.split("_", 3)
        if len(parts) < 4:
            return
        decision_id = f"mode_{parts[2]}"
        option_id = parts[3]

        future = self._mode_futures.get(decision_id)
        if not future or future.done():
            return

        channel = body.get("channel", {}).get("id", "")
        message_ts = body.get("message", {}).get("ts", "")
        user_id = body.get("user", {}).get("id", "")

        await self._adapter.resolve_decision(
            channel, message_ts, "Interaction Mode", option_id.title(), user_id
        )
        future.set_result(option_id)

    # ── Workspace Selection ─────────────────────────────────────────────

    async def _ask_workspace(self, channel_id: str, feature_id: str) -> Path:
        """Post workspace scoping card and wait for user to provide a project path."""
        ws_id = f"ws_{feature_id}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._workspace_futures[ws_id] = future

        from .cards import RespondCard
        options: list[str] = []
        if self._default_workspace:
            options.append(str(self._default_workspace))

        card = RespondCard(
            pending_id=ws_id,
            phase_name="Project Setup",
            question="Which project should this feature target?\nEnter the absolute path to the project workspace.",
            options=options,
        )
        # Override action_id prefix to workspace_ so the orchestrator intercepts it
        blocks = card.build_blocks()
        # Rewrite action_ids from respond_ to workspace_ so _on_action routes here
        for block in blocks:
            if "accessory" in block:
                aid = block["accessory"].get("action_id", "")
                block["accessory"]["action_id"] = aid.replace("respond_", "workspace_", 1)
            for el in block.get("elements", []):
                aid = el.get("action_id", "")
                el["action_id"] = aid.replace("respond_", "workspace_", 1)

        await self._adapter.post_blocks(channel_id, blocks, "Project Setup")

        try:
            raw_path = await future
        finally:
            self._workspace_futures.pop(ws_id, None)

        resolved = Path(raw_path.strip()).expanduser().resolve()
        if not resolved.is_dir():
            await self._adapter.post_message(
                channel_id,
                f"Warning: `{resolved}` does not exist — the workspace may fail to initialize.",
            )
        return resolved

    async def _handle_workspace_action(self, body: dict, action: dict) -> None:
        """Handle workspace card button clicks."""
        action_id = action.get("action_id", "")
        trigger_id = body.get("trigger_id", "")
        channel = body.get("channel", {}).get("id", "")
        message_ts = body.get("message", {}).get("ts", "")
        user_id = body.get("user", {}).get("id", "")

        # workspace_{ws_id}_opt_{idx} — default workspace option selected
        if "_opt_" in action_id:
            parts = action_id.rsplit("_opt_", 1)
            ws_id = parts[0][len("workspace_"):]
            future = self._workspace_futures.get(ws_id)
            if future and not future.done() and self._default_workspace:
                await self._adapter.resolve_decision(
                    channel, message_ts, "Project Setup",
                    str(self._default_workspace), user_id,
                )
                future.set_result(str(self._default_workspace))
            return

        # workspace_{ws_id}_reply — open modal for custom path
        if action_id.endswith("_reply"):
            ws_id = action_id[len("workspace_"):-len("_reply")]
            if trigger_id:
                from .cards import build_modal_view
                view = build_modal_view(f"ws_{ws_id}" if not ws_id.startswith("ws_") else ws_id, "Project Path", label="Absolute path to the project")
                await self._adapter.open_modal(trigger_id, view)

    async def _handle_workspace_submission(self, payload: dict) -> None:
        """Handle workspace modal submission."""
        view = payload.get("view", {})
        ws_id = view.get("private_metadata", "")
        user_id = payload.get("user", {}).get("id", "")

        values = view.get("state", {}).get("values", {})
        reply_block = values.get("reply_block", {})
        reply_input = reply_block.get("reply_input", {})
        text = reply_input.get("value", "")

        if ws_id and text:
            future = self._workspace_futures.get(ws_id)
            if future and not future.done():
                future.set_result(text)

    # ── Session Recovery ────────────────────────────────────────────────

    async def _recover_active_features(self) -> None:
        """Rebuild channel→feature mappings from DB for non-complete features."""
        assert self._env is not None
        features = await self._env.feature_store.list_active()
        recovered = 0

        for feature in features:
            meta = feature.metadata or {}
            workflow_name = getattr(feature, "workflow_name", "full-develop")
            channel_id = meta.get("channel_id")
            workspace_path = meta.get("workspace_path")
            mode = meta.get("mode", "singleplayer")
            saved_agent_runtime = meta.get("agent_runtime")
            agent_runtime = (
                self._agent_runtime_name
                if self._agent_runtime_override
                else saved_agent_runtime or self._agent_runtime_name
            )

            if not channel_id or not workspace_path:
                logger.warning(
                    "Skipping recovery for feature %s (%s): missing channel_id or workspace_path in metadata",
                    feature.id, feature.name,
                )
                continue

            # Rebuild routing tables
            self._channel_features[channel_id] = feature.id
            self._feature_workflows[feature.id] = workflow_name
            self._interaction.register_channel(feature.id, channel_id)
            self._adapter.set_channel_mode(channel_id, mode)

            phase = meta.get("_db_phase", "unknown")

            self._recoverable_features[feature.id] = {
                "workspace_path": workspace_path,
                "mode": mode,
                "phase": phase,
                "agent_runtime": agent_runtime,
            }

            try:
                try:
                    await self._maybe_post_dashboard_url(
                        feature.id,
                        channel_id,
                        workflow_name=workflow_name,
                        recovery=True,
                    )
                except Exception:
                    logger.warning(
                        "Failed to post dashboard URL in %s",
                        channel_id,
                        exc_info=True,
                    )
                if workflow_name == "bugfix-v2":
                    await self._adapter.post_message(
                        channel_id,
                        f"Bridge restarted. Feature is in phase `{phase}`. "
                        f"Runtime: `{agent_runtime}`. Resuming bugflow automatically.",
                    )
                else:
                    await self._adapter.post_message(
                        channel_id,
                        f"Bridge restarted. Feature is in phase `{phase}`. "
                        f"Runtime: `{agent_runtime}`. Send any message to resume.",
                    )
            except Exception:
                logger.warning("Failed to post recovery message in %s", channel_id, exc_info=True)

            recovered += 1
            if workflow_name == "bugfix-v2":
                try:
                    await self._resume_workflow(feature.id, channel_id)
                except Exception:
                    logger.warning(
                        "Failed to auto-resume bugflow feature %s",
                        feature.id,
                        exc_info=True,
                    )

        if recovered:
            logger.info("Recovered %d feature channel mappings", recovered)

    async def _resume_workflow(self, feature_id: str, channel_id: str) -> None:
        """Resume an interrupted workflow from its last known phase."""
        assert self._env is not None
        recovery_info = self._recoverable_features.get(feature_id)
        if not recovery_info:
            return

        workspace_path = Path(recovery_info["workspace_path"])
        mode = recovery_info["mode"]
        resume_phase = recovery_info["phase"]
        agent_runtime_name = recovery_info["agent_runtime"]

        # Fail fast: workspace must still exist
        if not workspace_path.is_dir():
            await self._adapter.post_message(
                channel_id,
                f"Cannot resume: workspace `{workspace_path}` no longer exists. "
                f"Feature was in phase `{resume_phase}`.",
            )
            return

        try:
            # Load feature from DB
            feature = await self._env.feature_store.get_feature(feature_id)
            if not feature:
                await self._adapter.post_message(
                    channel_id,
                    f"Cannot resume: feature `{feature_id}` not found in database.",
                )
                return

            # Reconstruct state from artifacts
            workflow = select_workflow(feature.workflow_name)
            state = await rebuild_state(
                feature.workflow_name, self._env.artifacts, feature
            )

            self._interaction.register_channel(feature.id, channel_id)
            self._feature_workflows[feature.id] = feature.workflow_name

            # Create streamer, runtime, runner (same as _start_workflow steps 8-9)
            agent_runtime, runner = self._create_runtime_and_runner(
                workspace_path=workspace_path,
                channel_id=channel_id,
                runtime_name=agent_runtime_name,
            )
            self._active_runtimes[feature_id] = agent_runtime
            streamer = getattr(runner, "_slack_streamer", None)
            if isinstance(streamer, SlackStreamer):
                self._feature_streamers[feature_id] = streamer

            await self._adapter.post_message(
                channel_id,
                f"Resuming *{feature.workflow_name}* workflow from phase: *{resume_phase}*\n"
                f"Workspace: `{workspace_path}`\nMode: _{mode}_\nRuntime: _{agent_runtime_name}_",
            )

            task = asyncio.create_task(
                self._run_workflow_resumed(
                    runner, workflow, feature, state, channel_id, resume_phase
                )
            )
            self._active_workflows[feature_id] = task
            self._recoverable_features.pop(feature_id, None)
        except Exception as exc:
            logger.exception("Failed to resume workflow for %s", feature_id)
            self._active_runtimes.pop(feature_id, None)
            await self._adapter.post_message(
                channel_id,
                f"Resume failed for `{feature_id}`: {exc}\nSend any message to retry.",
            )
            return

    async def _run_workflow_resumed(
        self,
        runner: Any,
        workflow: Any,
        feature: Any,
        state: Any,
        channel_id: str,
        resume_phase: str,
    ) -> None:
        try:
            observer = self._make_invocation_observer(feature.id, feature.workflow_name, channel_id)
            binder = getattr(runner, "bind_invocation_observer", None)
            with binder(observer) if observer is not None and callable(binder) else nullcontext():
                await runner.resume_workflow(
                    workflow, feature, state, resume_from_phase=resume_phase
                )
            await self._adapter.post_message(channel_id, "Workflow complete!")
        except Exception as e:
            logger.exception("Resumed workflow failed for %s", feature.id)
            current_feature = await self._env.feature_store.get_feature(feature.id) or feature
            current_meta = current_feature.metadata or {}
            self._recoverable_features[feature.id] = {
                "workspace_path": str(current_meta.get("workspace_path", "") or ""),
                "mode": str(current_meta.get("mode", "singleplayer") or "singleplayer"),
                "phase": str(current_meta.get("_db_phase", resume_phase) or resume_phase),
                "agent_runtime": str(current_meta.get("agent_runtime", self._agent_runtime_name) or self._agent_runtime_name),
            }
            self._feature_workflows[feature.id] = current_feature.workflow_name
            self._interaction.register_channel(feature.id, channel_id)
            await self._adapter.post_message(
                channel_id, f"Resumed workflow failed: {e}\nSend any message to retry."
            )
        finally:
            self._active_workflows.pop(feature.id, None)
            self._active_runtimes.pop(feature.id, None)
            self._feature_streamers.pop(feature.id, None)
            if feature.id not in self._recoverable_features:
                self._feature_workflows.pop(feature.id, None)
                self._interaction.unregister_channel(feature.id)
            self._user_notes.pop(feature.id, None)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _has_active_agent(self, feature_id: str) -> bool:
        runtime = self._active_runtimes.get(feature_id)
        if not runtime:
            return False
        return bool(getattr(runtime, "has_active_agent", lambda _feature_id: False)(feature_id))

    def _make_invocation_observer(
        self,
        feature_id: str,
        workflow_name: str,
        channel_id: str,
    ) -> _SlackInvocationObserver | None:
        if workflow_name == "bugfix-v2":
            return None
        streamer = self._feature_streamers.get(feature_id)
        return _SlackInvocationObserver(self._adapter, channel_id, streamer)

    def _queue_user_note(self, feature_id: str, text: str) -> None:
        self._user_notes.setdefault(feature_id, []).append(text)
        runtime = self._active_runtimes.get(feature_id)
        if runtime and hasattr(runtime, "queue_user_note"):
            runtime.queue_user_note(feature_id, text)

    async def _maybe_capture_bugflow_report(self, feature_id: str, event: dict) -> bool:
        assert self._env is not None

        if self._feature_workflows.get(feature_id) != "bugfix-v2":
            return False

        text = (event.get("text") or "").strip()
        if not text:
            return False

        event_ts = event.get("ts", "")
        thread_ts = event.get("thread_ts")
        if thread_ts and thread_ts != event_ts:
            return False

        match = re.match(r"^\[bug\]\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        if not match:
            return False

        summary = match.group(1).strip()
        if not summary:
            return False

        feature = await self._env.feature_store.get_feature(feature_id)
        if not feature:
            return False

        report_id = new_short_id("BR")
        timestamp = utc_now()
        snapshot = BugflowReportSnapshot(
            report_id=report_id,
            root_message_ts=event_ts,
            thread_ts=event_ts,
            root_message_text=text,
            title=summary[:80],
            summary=summary,
            status="intake_pending",
            current_step=f"Awaiting intake interview for {report_id}",
            created_at=timestamp,
            updated_at=timestamp,
        )
        await self._env.artifacts.put(
            report_key(report_id),
            snapshot.model_dump_json(),
            feature=feature,
        )
        await self._env.feature_store.log_event(
            feature_id,
            "bugflow_report_created",
            "slack",
            report_id,
            metadata={
                "report_id": report_id,
                "thread_ts": event_ts,
                "root_message_ts": event_ts,
                "summary": summary,
            },
        )
        await self._adapter.post_message(
            event.get("channel", ""),
            f"Captured *{report_id}*. I'll ask clarifying questions in this thread before launching the fix flow.",
            thread_ts=event_ts,
        )
        if feature_id in self._recoverable_features and feature_id not in self._active_workflows:
            await self._resume_workflow(feature_id, event.get("channel", ""))
        return True

    async def _post_planning_error(self, trigger_ts: str, text: str) -> None:
        if not trigger_ts:
            return
        from .helpers import post_to_thread

        await post_to_thread(
            self._adapter.web,
            self._adapter.planning_channel,
            trigger_ts,
            text,
        )

    async def _maybe_post_dashboard_url(
        self,
        feature_id: str,
        channel_id: str,
        *,
        workflow_name: str | None,
        recovery: bool,
    ) -> None:
        """Post or repost the dashboard URL when one is configured for the feature."""
        assert self._env is not None
        feature_store = self._env.feature_store
        if not hasattr(feature_store, "get_feature"):
            return

        feature = await feature_store.get_feature(feature_id)
        if not feature:
            return

        metadata = feature.metadata or {}
        effective_workflow = workflow_name or feature.workflow_name
        dashboard_url = (
            f"{DASHBOARD_BASE_URL}/feature/{feature_id}"
            if effective_workflow == "bugfix-v2" and DASHBOARD_BASE_URL
            else metadata.get("dashboard_url")
        )

        if not dashboard_url:
            return

        suffix = "\nBridge restarted — reposting dashboard link." if recovery else ""
        message_ts = await self._adapter.post_message(
            channel_id,
            f"Dashboard: {dashboard_url}{suffix}",
        )
        await self._env.feature_store.update_metadata(
            feature_id,
            {
                "dashboard_url": dashboard_url,
                "dashboard_message_ts": message_ts,
            },
        )

    def _create_runtime_and_runner(
        self,
        *,
        workspace_path: Path,
        channel_id: str,
        runtime_name: str,
    ) -> tuple[Any, Any]:
        assert self._env is not None

        streamer = SlackStreamer(self._adapter, channel_id)

        from iriai_compose import Workspace

        from ...workflows import TrackedWorkflowRunner

        agent_runtime = create_agent_runtime(
            runtime_name,
            session_store=self._env.sessions,
            on_message=streamer.on_message,
            interactive_roles=_INTERACTIVE_ROLES,
        )

        # Secondary runtime mirrors shared runner behavior: Claude-primary
        # pairs with Codex, while Codex-primary stays fully Codex-only.
        from ...runtimes import secondary_agent_runtime_name

        secondary_name = secondary_agent_runtime_name(
            runtime_name,
            single_runtime=self._single_agent_runtime,
        )
        secondary_runtime = create_agent_runtime(
            secondary_name,
            session_store=self._env.sessions,
            on_message=streamer.on_message,
        )

        self._interaction._session_store = self._env.sessions
        self._interaction._agent_runtime = agent_runtime
        self._interaction._feature_store = self._env.feature_store
        auto_interaction = (
            AgentDelegateInteractionRuntime(agent_runtime=agent_runtime)
            if self._autonomous_remainder
            else self._interaction
        )

        # Workspace manager for worktree creation
        from ...services.workspace import WorkspaceManager

        workspace_manager = WorkspaceManager(base_path=workspace_path)

        ws = Workspace(id="main", path=workspace_path)
        runner = TrackedWorkflowRunner(
            feature_store=self._env.feature_store,
            agent_runtime=agent_runtime,
            secondary_runtime=secondary_runtime,
            interaction_runtimes={"terminal": self._interaction, "auto": auto_interaction},
            artifacts=self._env.artifacts,
            sessions=self._env.sessions,
            context_provider=self._env.context_provider,
            workspaces={"main": ws},
            budget=self._budget,
            services={
                "feedback": self._env.feedback_service,
                "preview": self._env.preview_service,
                "playwright": self._env.playwright_service,
                "artifact_mirror": self._env.artifact_mirror,
                "workspace_manager": workspace_manager,
                "tunnel": self._tunnel,
                "slack_adapter": self._adapter,
                "autonomous_remainder": self._autonomous_remainder,
            },
        )
        runner._slack_streamer = streamer  # type: ignore[attr-defined]
        return agent_runtime, runner
