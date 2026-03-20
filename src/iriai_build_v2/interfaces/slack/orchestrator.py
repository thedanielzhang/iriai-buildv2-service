"""Slack workflow orchestrator.

Routes messages from #planning to create feature channels and start workflows.
Manages the lifecycle of concurrent workflow runs.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._bootstrap import (
    bootstrap,
    create_feature,
    rebuild_state,
    select_workflow,
    build_state,
    slugify,
    teardown,
)
from .parser import parse_workflow_request
from .streamer import SlackStreamer

if TYPE_CHECKING:
    from .adapter import SlackAdapter
    from .interaction import SlackInteractionRuntime
    from .._bootstrap import BootstrappedEnv

logger = logging.getLogger(__name__)

# Roles that benefit from persistent client + mid-stream user message injection.
# Short-lived reviewers use the fast ephemeral client path.
_INTERACTIVE_ROLES = {"pm", "designer", "architect", "task_planner", "implementer"}


class SlackWorkflowOrchestrator:
    """Manages workflow lifecycle, mode selection, and message routing."""

    def __init__(
        self,
        adapter: SlackAdapter,
        interaction_runtime: SlackInteractionRuntime,
        workspace_path: Path | None = None,
    ) -> None:
        self._adapter = adapter
        self._interaction = interaction_runtime
        self._default_workspace = workspace_path  # suggestion, not binding
        self._env: BootstrappedEnv | None = None

        # Workflow tracking
        self._active_workflows: dict[str, asyncio.Task] = {}  # feature_id → task
        self._active_runtimes: dict[str, Any] = {}  # feature_id → ClaudeAgentRuntime
        self._channel_features: dict[str, str] = {}  # channel → feature_id
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
        logger.info("Orchestrator started, default_workspace=%s", self._default_workspace)

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
            return

        # Messages in workflow channels
        feature_id = self._channel_features.get(channel)
        if not feature_id:
            return

        if self._interaction.has_pending(channel):
            # Mode 1: card pending → resolve it
            await self._interaction.handle_message(event)
        elif self._has_active_agent(feature_id):
            # Mode 2: agent working → inject mid-stream
            runtime = self._active_runtimes.get(feature_id)
            if runtime:
                injected = await runtime.inject_user_message(feature_id, text)
                if injected:
                    await self._adapter.add_reaction(
                        channel, event.get("ts", ""), "eyes"
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

        # 5. Post mode selection card
        mode = await self._ask_mode_selection(channel_id, feature.id)
        self._adapter.set_channel_mode(channel_id, mode)

        # 6. Ask which project workspace to target
        workspace_path = await self._ask_workspace(channel_id, feature.id)

        # 6b. Persist channel/workspace/mode for resume after restart
        await self._env.feature_store.update_metadata(feature.id, {
            "channel_id": channel_id,
            "workspace_path": str(workspace_path),
            "mode": mode,
        })

        # 7. Register channel with interaction runtime
        self._interaction.register_channel(feature.id, channel_id)

        # 8. Create streamer + runner with per-feature workspace
        streamer = SlackStreamer(self._adapter, channel_id)

        from iriai_compose import Workspace

        from ...runtimes.claude import ClaudeAgentRuntime
        from ...workflows import TrackedWorkflowRunner

        agent_runtime = ClaudeAgentRuntime(
            session_store=self._env.sessions,
            on_message=streamer.on_message,
            interactive_roles=_INTERACTIVE_ROLES,
        )
        self._active_runtimes[feature.id] = agent_runtime

        ws = Workspace(id="main", path=workspace_path)
        runner = TrackedWorkflowRunner(
            feature_store=self._env.feature_store,
            agent_runtime=agent_runtime,
            interaction_runtimes={"terminal": self._interaction},
            artifacts=self._env.artifacts,
            sessions=self._env.sessions,
            context_provider=self._env.context_provider,
            workspaces={"main": ws},
            services={
                "feedback": self._env.feedback_service,
                "preview": self._env.preview_service,
                "playwright": self._env.playwright_service,
                "artifact_mirror": self._env.artifact_mirror,
                "tunnel": self._tunnel,
            },
        )

        # 9. Select workflow + build state
        workflow = select_workflow(parsed.workflow_name)
        state = build_state(parsed.workflow_name)

        # 10. Seed project artifact
        await self._env.artifacts.put(
            "project",
            f"Project workspace: {workspace_path}\n\nFeature: {parsed.feature_name}",
            feature=feature,
        )

        # 11. Post kickoff message
        await self._adapter.post_message(
            channel_id,
            f"Starting *{parsed.workflow_name}* workflow for: *{parsed.feature_name}*\n"
            f"Workspace: `{workspace_path}`\nMode: _{mode}_",
        )

        # 12. Launch workflow as background task
        task = asyncio.create_task(
            self._run_workflow(runner, workflow, feature, state, channel_id)
        )
        self._active_workflows[feature.id] = task

    async def _run_workflow(
        self,
        runner: Any,
        workflow: Any,
        feature: Any,
        state: Any,
        channel_id: str,
    ) -> None:
        try:
            await runner.execute_workflow(workflow, feature, state)
            await self._adapter.post_message(channel_id, "Workflow complete!")
            await self._adapter.add_reaction(
                self._adapter.planning_channel, "", "white_check_mark"
            )
        except Exception as e:
            logger.exception("Workflow failed for %s", feature.id)
            await self._adapter.post_message(
                channel_id, f"Workflow failed: {e}"
            )
        finally:
            self._active_workflows.pop(feature.id, None)
            self._active_runtimes.pop(feature.id, None)
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
            channel_id = meta.get("channel_id")
            workspace_path = meta.get("workspace_path")
            mode = meta.get("mode", "singleplayer")

            if not channel_id or not workspace_path:
                logger.warning(
                    "Skipping recovery for feature %s (%s): missing channel_id or workspace_path in metadata",
                    feature.id, feature.name,
                )
                continue

            # Rebuild routing tables
            self._channel_features[channel_id] = feature.id
            self._interaction.register_channel(feature.id, channel_id)
            self._adapter.set_channel_mode(channel_id, mode)

            phase = meta.get("_db_phase", "unknown")

            self._recoverable_features[feature.id] = {
                "workspace_path": workspace_path,
                "mode": mode,
                "phase": phase,
            }

            try:
                await self._adapter.post_message(
                    channel_id,
                    f"Bridge restarted. Feature is in phase `{phase}`. "
                    "Send any message to resume.",
                )
            except Exception:
                logger.warning("Failed to post recovery message in %s", channel_id, exc_info=True)

            recovered += 1

        if recovered:
            logger.info("Recovered %d feature channel mappings", recovered)

    async def _resume_workflow(self, feature_id: str, channel_id: str) -> None:
        """Resume an interrupted workflow from its last known phase."""
        assert self._env is not None
        recovery_info = self._recoverable_features.pop(feature_id, None)
        if not recovery_info:
            return

        workspace_path = Path(recovery_info["workspace_path"])
        mode = recovery_info["mode"]
        resume_phase = recovery_info["phase"]

        # Fail fast: workspace must still exist
        if not workspace_path.is_dir():
            await self._adapter.post_message(
                channel_id,
                f"Cannot resume: workspace `{workspace_path}` no longer exists. "
                f"Feature was in phase `{resume_phase}`.",
            )
            return

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

        # Create streamer, runtime, runner (same as _start_workflow steps 8-9)
        streamer = SlackStreamer(self._adapter, channel_id)

        from iriai_compose import Workspace

        from ...runtimes.claude import ClaudeAgentRuntime
        from ...workflows import TrackedWorkflowRunner

        agent_runtime = ClaudeAgentRuntime(
            session_store=self._env.sessions,
            on_message=streamer.on_message,
            interactive_roles=_INTERACTIVE_ROLES,
        )
        self._active_runtimes[feature_id] = agent_runtime

        ws = Workspace(id="main", path=workspace_path)
        runner = TrackedWorkflowRunner(
            feature_store=self._env.feature_store,
            agent_runtime=agent_runtime,
            interaction_runtimes={"terminal": self._interaction},
            artifacts=self._env.artifacts,
            sessions=self._env.sessions,
            context_provider=self._env.context_provider,
            workspaces={"main": ws},
            services={
                "feedback": self._env.feedback_service,
                "preview": self._env.preview_service,
                "playwright": self._env.playwright_service,
                "artifact_mirror": self._env.artifact_mirror,
                "tunnel": self._tunnel,
            },
        )

        await self._adapter.post_message(
            channel_id,
            f"Resuming *{feature.workflow_name}* workflow from phase: *{resume_phase}*\n"
            f"Workspace: `{workspace_path}`\nMode: _{mode}_",
        )

        task = asyncio.create_task(
            self._run_workflow_resumed(
                runner, workflow, feature, state, channel_id, resume_phase
            )
        )
        self._active_workflows[feature_id] = task

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
            await runner.resume_workflow(
                workflow, feature, state, resume_from_phase=resume_phase
            )
            await self._adapter.post_message(channel_id, "Workflow complete!")
        except Exception as e:
            logger.exception("Resumed workflow failed for %s", feature.id)
            await self._adapter.post_message(
                channel_id, f"Resumed workflow failed: {e}"
            )
        finally:
            self._active_workflows.pop(feature.id, None)
            self._active_runtimes.pop(feature.id, None)
            self._interaction.unregister_channel(feature.id)
            self._user_notes.pop(feature.id, None)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _has_active_agent(self, feature_id: str) -> bool:
        runtime = self._active_runtimes.get(feature_id)
        if not runtime:
            return False
        return runtime.has_active_agent(feature_id)

    def _queue_user_note(self, feature_id: str, text: str) -> None:
        self._user_notes.setdefault(feature_id, []).append(text)
