"""SlackInteractionRuntime: bridges iriai-compose interaction tasks to Slack.

All user interactions are self-contained within Block Kit cards — buttons for
choices, inline text inputs for quick answers, and an Expand button that opens
a modal for longer responses. No "reply in channel" patterns.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from iriai_compose.prompts import Confirm, Select
from iriai_compose.runner import InteractionRuntime
from iriai_compose.tasks import Ask

from .cards import ApproveCard, ChooseCard, RespondCard, build_modal_view
from .helpers import build_resolved_blocks, split_mrkdwn_blocks
from ...planning_signals import BACKGROUND_RESPONSE, GateRejection

if TYPE_CHECKING:
    from iriai_compose.pending import Pending

    from .adapter import SlackAdapter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _SlackPending:
    """Compatibility wrapper for both old Pending and new Ask-based runtimes."""

    id: str
    kind: str
    prompt: str
    feature_id: str
    phase_name: str
    options: list[str] | None = None


@dataclass(slots=True)
class _ModalSubmission:
    pending_id: str
    kind: str = "reply"


class SlackInteractionRuntime(InteractionRuntime):
    """Resolves interaction requests via Slack Block Kit cards.

    Card designs:
    - Respond: question text + option buttons + inline text input + Expand
    - Approve: context + Approve/Reject + inline feedback + Expand
    - Choose: question + one button per option
    """

    name = "terminal"  # matches user actor's resolver="terminal"

    def __init__(self, adapter: SlackAdapter) -> None:
        self._adapter = adapter

        # pending_id → Future
        self._pending_futures: dict[str, asyncio.Future] = {}
        # pending_id → options list (for mapping button index → text)
        self._pending_options: dict[str, list[str]] = {}
        # pending_id → (channel, message_ts) for updating card on resolution
        self._pending_messages: dict[str, tuple[str, str]] = {}
        # pending_id → thread_ts for the posted card, if any
        self._pending_threads: dict[str, str | None] = {}
        # pending_id → feature_id for turn persistence
        self._pending_features: dict[str, str] = {}
        # pending_id → whether to persist user turns back into an agent session
        self._pending_persist: dict[str, bool] = {}
        # pending_id → runtime instance whose active session should receive the user turn
        self._pending_agent_runtimes: dict[str, Any] = {}
        # pending_id → card title for resolved-state updates
        self._pending_titles: dict[str, str] = {}
        # pending_id → original resolved payload before any downstream coercion
        self._pending_values: dict[str, str | bool | GateRejection] = {}
        # feature_id ↔ channel bidirectional mapping
        self._feature_channels: dict[str, str] = {}
        self._channel_features: dict[str, str] = {}

        # Turn persistence: set by orchestrator when a runtime is available
        self._session_store: Any = None
        self._agent_runtime: Any = None  # Runtime with get_active_session_key()
        self._feature_store: Any = None

    # ── Channel Registration ──────────────────────────────────────────────

    def register_channel(self, feature_id: str, channel: str) -> None:
        self._feature_channels[feature_id] = channel
        self._channel_features[channel] = feature_id

    def unregister_channel(self, feature_id: str) -> None:
        channel = self._feature_channels.pop(feature_id, None)
        if channel:
            self._channel_features.pop(channel, None)

    def _schedule_feature_log(
        self,
        feature_id: str | None,
        event_type: str,
        *,
        source: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not feature_id or self._feature_store is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._feature_store.log_event(
                feature_id,
                event_type,
                source,
                content=content,
                metadata=metadata,
            )
        )

    def _feature_id_for_pending(
        self,
        pending_id: str,
        *,
        channel: str = "",
    ) -> str | None:
        feature_id = self._pending_features.get(pending_id)
        if feature_id:
            return feature_id
        if channel:
            return self._channel_features.get(channel)
        return None

    def has_pending(self, channel: str) -> bool:
        """Check if any pending interaction exists for this channel's feature."""
        feature_id = self._channel_features.get(channel)
        if not feature_id:
            return False
        return any(
            pending_feature_id == feature_id
            for pending_feature_id in self._pending_features.values()
        )

    def make_thread_runtime(
        self,
        *,
        feature_id: str,
        channel: str,
        thread_ts: str,
        persist_turns: bool = False,
        agent_runtime: Any = None,
        label: str | None = None,
    ) -> SlackThreadInteractionRuntime:
        """Return a wrapper runtime that posts cards into a fixed Slack thread."""
        del label
        return SlackThreadInteractionRuntime(
            root=self,
            feature_id=feature_id,
            channel=channel,
            thread_ts=thread_ts,
            persist_turns=persist_turns,
            agent_runtime=agent_runtime,
        )

    # ── InteractionRuntime.resolve() ──────────────────────────────────────

    async def ask(self, task: Ask, **kwargs: Any) -> str | bool | GateRejection:
        pending = _pending_from_task(
            task,
            feature_id=str(kwargs.get("feature_id", "") or ""),
            phase_name=str(kwargs.get("phase_name", "") or ""),
            kind_hint=str(kwargs.get("kind", "") or "") or None,
            options_hint=list(kwargs.get("options", []) or []),
        )
        return await self.resolve(pending)

    async def notify(
        self,
        *,
        feature_id: str,
        phase_name: str,
        message: str,
    ) -> None:
        channel = self._feature_channels.get(feature_id)
        if not channel:
            logger.warning(
                "No Slack channel registered for feature %s; dropping notification",
                feature_id,
            )
            return
        await self._adapter.post_message(channel, message)
        self._schedule_feature_log(
            feature_id,
            "slack_notification_posted",
            source="slack-interaction",
            content=phase_name or "notification",
            metadata={"message": message[:1000], "channel": channel},
        )

    async def resolve(self, pending: Pending) -> str | bool | GateRejection:
        channel = self._feature_channels.get(pending.feature_id)
        if not channel:
            raise RuntimeError(
                f"No Slack channel registered for feature {pending.feature_id}"
            )
        return await self._resolve_with_target(
            pending,
            channel=channel,
            thread_ts=None,
            persist_turns=True,
        )

    async def _resolve_with_target(
        self,
        pending: Pending,
        *,
        channel: str,
        thread_ts: str | None,
        persist_turns: bool,
        agent_runtime: Any | None = None,
    ) -> str | bool | GateRejection:
        """Resolve a pending interaction against a specific Slack target."""

        # Don't block on empty-question Envelopes — let the Interview
        # continue without user input so the agent gets another turn.
        if pending.kind == "respond" and not _has_question(pending.prompt):
            return "Continue"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | bool | GateRejection] = loop.create_future()
        self._pending_futures[pending.id] = future
        self._pending_features[pending.id] = pending.feature_id
        self._pending_persist[pending.id] = persist_turns
        self._pending_threads[pending.id] = thread_ts
        self._pending_agent_runtimes[pending.id] = agent_runtime
        self._pending_titles[pending.id] = _resolved_title_for_pending(pending)
        self._schedule_feature_log(
            pending.feature_id,
            "slack_pending_registered",
            source="slack-interaction",
            content=pending.kind,
            metadata={
                "pending_id": pending.id,
                "phase_name": pending.phase_name,
                "channel": channel,
                "thread_ts": thread_ts,
            },
        )

        if pending.kind == "approve":
            await self._post_approve(pending, channel, thread_ts=thread_ts)
        elif pending.kind == "choose":
            await self._post_choose(pending, channel, thread_ts=thread_ts)
        else:  # respond
            await self._post_respond(pending, channel, thread_ts=thread_ts)

        try:
            result = await future
            stored_value = self._pending_values.get(pending.id, result)
            if (
                pending.kind == "approve"
                and isinstance(result, bool)
                and not isinstance(stored_value, bool)
            ):
                self._schedule_feature_log(
                    pending.feature_id,
                    "slack_pending_result_mismatch",
                    source="slack-interaction",
                    content=pending.kind,
                    metadata={
                        "pending_id": pending.id,
                        "future_type": type(result).__name__,
                        "stored_type": type(stored_value).__name__,
                    },
                )
                result = stored_value
        finally:
            self._pending_futures.pop(pending.id, None)
            self._pending_options.pop(pending.id, None)
            self._pending_messages.pop(pending.id, None)
            self._pending_features.pop(pending.id, None)
            self._pending_persist.pop(pending.id, None)
            self._pending_threads.pop(pending.id, None)
            self._pending_agent_runtimes.pop(pending.id, None)
            self._pending_titles.pop(pending.id, None)
            self._pending_values.pop(pending.id, None)

        return result

    # ── Inbound Event Handlers ────────────────────────────────────────────

    async def handle_action(self, body: dict, action: dict) -> None:
        """Route button clicks, inline text input, and dropdown selections."""
        action_id = action.get("action_id", "")
        trigger_id = body.get("trigger_id", "")
        channel = body.get("channel", {}).get("id", "")
        message_ts = body.get("message", {}).get("ts", "")
        user_id = body.get("user", {}).get("id", "")
        pending_id = _pending_id_from_action_id(action_id)
        feature_id = self._feature_id_for_pending(pending_id, channel=channel)

        self._schedule_feature_log(
            feature_id,
            "slack_action_received",
            source="slack-interaction",
            content=action_id,
            metadata={
                "pending_id": pending_id,
                "channel": channel,
                "message_ts": message_ts,
                "user_id": user_id,
                "trigger_id_present": bool(trigger_id),
                "pending_live": bool(pending_id and pending_id in self._pending_futures),
                },
            )
        missing_pending = bool(pending_id and pending_id not in self._pending_futures)
        if missing_pending:
            self._schedule_feature_log(
                feature_id,
                "slack_action_missing_pending",
                source="slack-interaction",
                content=action_id,
                metadata={
                    "pending_id": pending_id,
                    "channel": channel,
                    "message_ts": message_ts,
                    "user_id": user_id,
                },
            )
            await self._mark_action_stale(
                action_id,
                channel=channel,
                message_ts=message_ts,
                user_id=user_id,
            )
            return

        if action_id.startswith("respond_"):
            await self._handle_respond_action(
                action_id, action, trigger_id, channel, message_ts, user_id
            )
        elif action_id.startswith("gate_"):
            await self._handle_gate_action(
                action_id, action, trigger_id, channel, message_ts, user_id
            )
        elif action_id.startswith("choose_"):
            await self._handle_choose_action(
                action_id, action, channel, message_ts, user_id
            )
        elif action_id.startswith("decision_"):
            # Backward compat: orchestrator mode selection uses decision_ prefix
            await self._handle_legacy_decision(
                action_id, action, trigger_id, channel, message_ts, user_id
            )

    async def handle_view_submission(self, payload: dict) -> None:
        """Handle modal form submissions.

        For reject modals (optional feedback), empty text resolves as a
        plain rejection (``"Please revise."``).  Non-empty text is passed
        as the feedback string.
        """
        view = payload.get("view", {})
        submission = _parse_modal_submission(view.get("private_metadata", ""))
        pending_id = submission.pending_id
        user_id = payload.get("user", {}).get("id", "")
        feature_id = self._feature_id_for_pending(pending_id)

        if not pending_id:
            return

        self._schedule_feature_log(
            feature_id,
            "slack_view_submission_received",
            source="slack-interaction",
            content=submission.kind,
            metadata={
                "pending_id": pending_id,
                "user_id": user_id,
                "pending_live": pending_id in self._pending_futures,
            },
        )
        if pending_id not in self._pending_futures:
            self._schedule_feature_log(
                feature_id,
                "slack_view_submission_missing_pending",
                source="slack-interaction",
                content=submission.kind,
                metadata={
                    "pending_id": pending_id,
                    "user_id": user_id,
                },
            )

        # Extract text from the modal input
        values = view.get("state", {}).get("values", {})
        reply_block = values.get("reply_block", {})
        reply_input = reply_block.get("reply_input", {})
        text = (reply_input.get("value") or "").strip()

        if submission.kind == "gate_reject":
            self._resolve_pending(
                pending_id,
                GateRejection(feedback=text),
                label="Rejected",
                user_id=user_id,
                title="Approval Required",
                feedback=text,
            )
            return

        if text:
            self._resolve_pending(pending_id, text, label=text[:50], user_id=user_id)
        else:
            self._resolve_pending(pending_id, "Please revise.", label="Rejected", user_id=user_id)

    async def handle_message(self, event: dict) -> None:
        """No-op: all interactions must go through cards, not channel messages.

        The orchestrator calls this when a pending card is active, but per
        the self-contained card design, we do not resolve from channel messages.
        """

    # ── Respond Card Actions ─────────────────────────────────────────────

    async def _handle_respond_action(
        self,
        action_id: str,
        action: dict,
        trigger_id: str,
        channel: str,
        message_ts: str,
        user_id: str,
    ) -> None:
        # respond_{pid}_opt_{idx}
        if "_opt_" in action_id:
            parts = action_id.rsplit("_opt_", 1)
            pending_id = parts[0][len("respond_"):]
            try:
                idx = int(parts[1])
            except (ValueError, IndexError):
                return
            options = self._pending_options.get(pending_id, [])
            if 0 <= idx < len(options):
                self._resolve_pending(
                    pending_id, options[idx], label=options[idx][:50], user_id=user_id
                )
            return

        # respond_{pid}_reply — open modal for free-form text input
        if action_id.endswith("_reply"):
            pending_id = action_id[len("respond_"):-len("_reply")]
            if trigger_id:
                view = build_modal_view(
                    _encode_modal_submission(pending_id, kind="reply"),
                    "Reply",
                )
                await self._adapter.open_modal(trigger_id, view)
            return

        if action_id.endswith("_background"):
            pending_id = action_id[len("respond_"):-len("_background")]
            self._resolve_pending(
                pending_id,
                BACKGROUND_RESPONSE,
                label="Finish In Background",
                user_id=user_id,
            )
            return

        # respond_{pid}_select — dropdown selection
        if action_id.endswith("_select"):
            pending_id = action_id[len("respond_"):-len("_select")]
            selected = action.get("selected_option", {})
            try:
                idx = int(selected.get("value", ""))
            except (ValueError, TypeError):
                return
            options = self._pending_options.get(pending_id, [])
            if 0 <= idx < len(options):
                self._resolve_pending(
                    pending_id, options[idx], label=options[idx][:50], user_id=user_id
                )

    # ── Gate Card Actions ────────────────────────────────────────────────

    async def _handle_gate_action(
        self,
        action_id: str,
        action: dict,
        trigger_id: str,
        channel: str,
        message_ts: str,
        user_id: str,
    ) -> None:
        # gate_{pid}_approve
        if action_id.endswith("_approve"):
            pending_id = action_id[len("gate_"):-len("_approve")]
            self._resolve_pending(pending_id, True, label="Approved", user_id=user_id)
            return

        # gate_{pid}_reject — open modal for optional feedback, then reject
        if action_id.endswith("_reject"):
            pending_id = action_id[len("gate_"):-len("_reject")]
            if trigger_id:
                view = build_modal_view(
                    _encode_modal_submission(pending_id, kind="gate_reject"),
                    "Reject",
                    label="Feedback (optional)",
                    optional=True,
                    placeholder="Add comments for the revision...",
                )
                await self._adapter.open_modal(trigger_id, view)
            return

    # ── Choose Card Actions ──────────────────────────────────────────────

    async def _handle_choose_action(
        self,
        action_id: str,
        action: dict,
        channel: str,
        message_ts: str,
        user_id: str,
    ) -> None:
        # choose_{pid}_opt_{idx}
        if "_opt_" in action_id:
            parts = action_id.rsplit("_opt_", 1)
            pending_id = parts[0][len("choose_"):]
            try:
                idx = int(parts[1])
            except (ValueError, IndexError):
                return
            options = self._pending_options.get(pending_id, [])
            if 0 <= idx < len(options):
                self._resolve_pending(
                    pending_id, options[idx], label=options[idx][:50], user_id=user_id
                )

    # ── Legacy Decision Actions (backward compat) ────────────────────────

    async def _handle_legacy_decision(
        self,
        action_id: str,
        action: dict,
        trigger_id: str,
        channel: str,
        message_ts: str,
        user_id: str,
    ) -> None:
        """Handle decision_{id}_{option} format used by orchestrator mode selection."""
        parts = action_id.split("_", 2)
        if len(parts) < 3:
            return
        pending_id = parts[1]
        option_id = parts[2]

        if option_id == "approve":
            self._resolve_pending(pending_id, True, label="Approved", user_id=user_id)
        elif option_id == "reject":
            self._resolve_pending(
                pending_id,
                GateRejection(),
                label="Rejected",
                user_id=user_id,
                title="Approval Required",
            )
        elif option_id == "feedback":
            if trigger_id:
                view = build_modal_view(
                    _encode_modal_submission(pending_id, kind="legacy_feedback"),
                    "Feedback",
                )
                await self._adapter.open_modal(trigger_id, view)
        else:
            self._resolve_pending(pending_id, option_id, label=option_id, user_id=user_id)

    # ── Post Helpers ──────────────────────────────────────────────────────

    async def _post_respond(
        self,
        pending: Pending,
        channel: str,
        *,
        thread_ts: str | None = None,
    ) -> None:
        """Post an Interview question card with options + inline text input + Expand.

        If the question exceeds Slack's 3000-char section limit, the full
        text is posted as preceding context message(s), and the interactive
        card gets a short summary.
        """
        question, options, allow_background = _extract_question_payload(pending.prompt)

        full_text = f"*{pending.phase_name}*\n{question}"
        if len(full_text) > 2900:
            # Post full content as context message(s) — no truncation
            context_blocks = split_mrkdwn_blocks(full_text)
            await self._adapter.post_blocks(
                channel,
                context_blocks,
                question[:100],
                thread_ts=thread_ts,
            )

            # Post compact interactive card with just the buttons
            short_q = question[:200] + "..." if len(question) > 200 else question
            card = RespondCard(
                pending_id=pending.id,
                phase_name=pending.phase_name,
                question=short_q,
                options=options,
                allow_background=allow_background,
            )
        else:
            card = RespondCard(
                pending_id=pending.id,
                phase_name=pending.phase_name,
                question=question,
                options=options,
                allow_background=allow_background,
            )

        blocks = card.build_blocks()
        ts = await self._adapter.post_blocks(
            channel,
            blocks,
            question[:100],
            thread_ts=thread_ts,
        )
        self._pending_messages[pending.id] = (channel, ts)
        if options:
            self._pending_options[pending.id] = options

    async def _post_approve(
        self,
        pending: Pending,
        channel: str,
        *,
        thread_ts: str | None = None,
    ) -> None:
        """Post a Gate approval card with buttons + feedback modal.

        If the context exceeds Slack's 3000-char section limit, the full
        text is posted as preceding context message(s).
        """
        prompt = pending.prompt
        artifact_name, review_urls = _extract_gate_info(prompt)

        full_text = f"*Approval Required*\n{artifact_name}"
        if review_urls:
            full_text += "\n" + "\n".join(f"<{u}|Review in browser>" for u in review_urls)

        if len(full_text) > 2900:
            context_blocks = split_mrkdwn_blocks(full_text)
            await self._adapter.post_blocks(
                channel,
                context_blocks,
                "Approval Required",
                thread_ts=thread_ts,
            )
            # Compact card with just title + URLs + buttons
            card = ApproveCard(
                pending_id=pending.id,
                title="Approval Required",
                context=artifact_name[:200],
                review_urls=review_urls or None,
            )
        else:
            card = ApproveCard(
                pending_id=pending.id,
                title="Approval Required",
                context=artifact_name,
                review_urls=review_urls or None,
            )

        blocks = card.build_blocks()
        ts = await self._adapter.post_blocks(
            channel,
            blocks,
            "Approval Required",
            thread_ts=thread_ts,
        )
        self._pending_messages[pending.id] = (channel, ts)

    async def _post_choose(
        self,
        pending: Pending,
        channel: str,
        *,
        thread_ts: str | None = None,
    ) -> None:
        """Post a Choose selection card with one button per option."""
        question, _ = _extract_question(pending.prompt)
        options_list = pending.options or []
        card = ChooseCard(
            pending_id=pending.id,
            title="Selection Required",
            question=question,
            options=options_list,
        )
        blocks = card.build_blocks()
        ts = await self._adapter.post_blocks(
            channel,
            blocks,
            question[:100],
            thread_ts=thread_ts,
        )
        self._pending_messages[pending.id] = (channel, ts)
        if options_list:
            self._pending_options[pending.id] = options_list

    # ── Internal ──────────────────────────────────────────────────────────

    def _resolve_pending(
        self,
        pending_id: str,
        value: str | bool | GateRejection,
        *,
        label: str = "",
        user_id: str = "",
        title: str | None = None,
        feedback: str = "",
    ) -> None:
        """Resolve a pending Future and update the card to resolved state.

        Also persists the user's turn to ``session.metadata["turns"]`` for
        mid-interview resume support.
        """
        def _value_label() -> str:
            if isinstance(value, GateRejection):
                return "Rejected"
            if isinstance(value, str):
                return value
            return "Approved" if value else "Rejected"

        future = self._pending_futures.get(pending_id)
        feature_id = self._pending_features.get(pending_id)
        self._pending_values[pending_id] = value
        if future and not future.done():
            future.set_result(value)
            self._schedule_feature_log(
                feature_id,
                "slack_pending_resolved",
                source="slack-interaction",
                content=label or _value_label(),
                metadata={
                    "pending_id": pending_id,
                    "user_id": user_id,
                    "title": title or self._pending_titles.get(pending_id, "Response"),
                    "feedback": feedback,
                    "value_type": type(value).__name__,
                    "value_preview": (
                        value.feedback[:1000]
                        if isinstance(value, GateRejection)
                        else str(value)[:1000]
                    ),
                },
            )
        else:
            self._schedule_feature_log(
                feature_id,
                "slack_pending_missing",
                source="slack-interaction",
                content=label or _value_label(),
                metadata={
                    "pending_id": pending_id,
                    "user_id": user_id,
                    "title": title or self._pending_titles.get(pending_id, "Response"),
                    "feedback": feedback,
                    "value_type": type(value).__name__,
                    "value_preview": (
                        value.feedback[:1000]
                        if isinstance(value, GateRejection)
                        else str(value)[:1000]
                    ),
                },
            )

        # Persist user turn for mid-interview resume
        should_persist = self._pending_persist.get(pending_id, True)
        if (
            should_persist
            and (
                (isinstance(value, str) and value != BACKGROUND_RESPONSE)
                or (isinstance(value, GateRejection) and bool(value.feedback.strip()))
            )
            and self._session_store
        ):
            feature_id = self._pending_features.get(pending_id)
            if feature_id:
                try:
                    loop = asyncio.get_running_loop()
                    agent_runtime = self._pending_agent_runtimes.get(pending_id) or self._agent_runtime
                    loop.create_task(
                        self._persist_user_turn(
                            feature_id,
                            value if isinstance(value, str) else value.feedback,
                            agent_runtime=agent_runtime,
                        )
                    )
                except RuntimeError:
                    pass

        # Update card to resolved state
        msg_info = self._pending_messages.get(pending_id)
        if msg_info:
            channel, ts = msg_info
            display = label or _value_label()
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._update_to_resolved(
                        channel,
                        ts,
                        display,
                        user_id,
                        title=title or self._pending_titles.get(pending_id, "Response"),
                        feedback=feedback,
                    )
                )
            except RuntimeError:
                pass

    async def _persist_user_turn(
        self,
        feature_id: str,
        text: str,
        *,
        agent_runtime: Any | None,
    ) -> None:
        """Persist a user turn to session metadata for mid-interview resume."""
        try:
            if not agent_runtime:
                return
            session_key = agent_runtime.get_active_session_key(feature_id)
            if not session_key:
                return
            session = await self._session_store.load(session_key)
            if not session:
                return
            turns = session.metadata.get("turns", [])
            turns.append({"role": "user", "text": text, "turn": len(turns) + 1})
            session.metadata["turns"] = turns
            await self._session_store.save(session)
        except Exception:
            logger.debug("Failed to persist user turn for %s", feature_id, exc_info=True)

    async def _update_to_resolved(
        self,
        channel: str,
        ts: str,
        display: str,
        user_id: str,
        *,
        title: str,
        feedback: str = "",
    ) -> None:
        """Replace the card with a resolved-state block."""
        try:
            blocks = build_resolved_blocks(title, display, user_id, feedback)
            await self._adapter.update_message(channel, ts, blocks=blocks, text=f"Resolved: {display}")
        except Exception:
            logger.exception("Failed to update card to resolved state")

    async def _mark_action_stale(
        self,
        action_id: str,
        *,
        channel: str,
        message_ts: str,
        user_id: str,
    ) -> None:
        if not channel or not message_ts:
            return
        await self._update_to_resolved(
            channel,
            message_ts,
            "Expired",
            user_id,
            title=_stale_title_for_action_id(action_id),
            feedback=(
                "This card is stale. The bridge was restarted or the prompt was "
                "replaced. Use the latest prompt in this thread."
            ),
        )


class SlackThreadInteractionRuntime(InteractionRuntime):
    """Wrapper runtime that posts all interactions into a fixed Slack thread."""

    def __init__(
        self,
        *,
        root: SlackInteractionRuntime,
        feature_id: str,
        channel: str,
        thread_ts: str,
        persist_turns: bool = False,
        agent_runtime: Any = None,
    ) -> None:
        self.name = f"terminal.thread.{thread_ts}"
        self._root = root
        self._feature_id = feature_id
        self._channel = channel
        self._thread_ts = thread_ts
        self._persist_turns = persist_turns
        self._agent_runtime = agent_runtime

    async def ask(self, task: Ask, **kwargs: Any) -> str | bool | GateRejection:
        pending = _pending_from_task(
            task,
            feature_id=self._feature_id,
            phase_name=str(kwargs.get("phase_name", "") or ""),
            kind_hint=str(kwargs.get("kind", "") or "") or None,
            options_hint=list(kwargs.get("options", []) or []),
        )
        return await self.resolve(pending)

    async def resolve(self, pending: Pending) -> str | bool | GateRejection:
        return await self._root._resolve_with_target(
            pending,
            channel=self._channel,
            thread_ts=self._thread_ts,
            persist_turns=self._persist_turns,
            agent_runtime=self._agent_runtime,
        )


def _extract_gate_info(prompt: str) -> tuple[str, list[str]]:
    """Extract artifact name and review URLs from a Gate prompt.

    Gate prompts look like::

        PRD
        Review in browser: https://...

        {full text}

        Approve?

    Or with multiple URLs::

        Design Decisions
        Review in browser: Design decisions: https://url1 | Mockup: https://url2

    Returns ``(artifact_name, list_of_review_urls)``.
    """
    import re

    # Split at double newline to get the label area (before the artifact text)
    label_area = prompt.split("\n\n", 1)[0]

    # Extract ALL URLs from the label area
    urls = re.findall(r"https?://\S+", label_area)
    urls = [u.rstrip(":|,") for u in urls]

    # Extract artifact name from the first line
    first_line = label_area.split("\n", 1)[0].rstrip(":")
    if "Review in browser:" in first_line:
        first_line = first_line.split("Review in browser:")[0].strip().rstrip(":")

    return first_line or "Artifact", urls


def _encode_modal_submission(pending_id: str, *, kind: str) -> str:
    return json.dumps({"pending_id": pending_id, "kind": kind}, separators=(",", ":"))


def _parse_modal_submission(private_metadata: str) -> _ModalSubmission:
    try:
        data = json.loads(private_metadata)
    except (json.JSONDecodeError, TypeError):
        return _ModalSubmission(pending_id=private_metadata)
    if not isinstance(data, dict):
        return _ModalSubmission(pending_id=private_metadata)
    pending_id = str(data.get("pending_id", "") or "")
    if not pending_id:
        return _ModalSubmission(pending_id=private_metadata)
    return _ModalSubmission(
        pending_id=pending_id,
        kind=str(data.get("kind", "reply") or "reply"),
    )


def _resolved_title_for_pending(pending: _SlackPending | Any) -> str:
    kind = getattr(pending, "kind", "respond")
    if kind == "approve":
        return "Approval Required"
    if kind == "choose":
        return "Selection Required"
    phase_name = str(getattr(pending, "phase_name", "") or "").strip()
    return phase_name or "Response"


def _pending_id_from_action_id(action_id: str) -> str:
    prefixes = ("respond_", "gate_", "choose_", "decision_")
    for prefix in prefixes:
        if not action_id.startswith(prefix):
            continue
        remainder = action_id[len(prefix):]
        for suffix in ("_approve", "_reject", "_reply", "_background", "_select"):
            if remainder.endswith(suffix):
                return remainder[:-len(suffix)]
        if "_opt_" in remainder:
            return remainder.rsplit("_opt_", 1)[0]
        if prefix == "decision_" and "_" in remainder:
            return remainder.split("_", 1)[0]
        return remainder
    return ""


def _stale_title_for_action_id(action_id: str) -> str:
    if action_id.startswith("gate_"):
        return "Approval Required"
    if action_id.startswith("choose_"):
        return "Selection Required"
    return "Response"


def _has_question(prompt: str) -> bool:
    """Check if the prompt contains a real question for the user.

    Returns False for JSON Envelopes with empty ``question`` — these are
    intermediate agent turns (tool use, investigation) that should not
    block on user input.
    """
    try:
        data = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return True  # Plain text — always show
    if not isinstance(data, dict):
        return True
    return bool(data.get("question"))


def _pending_from_task(
    task: Ask,
    *,
    feature_id: str,
    phase_name: str,
    kind_hint: str | None = None,
    options_hint: list[str] | None = None,
) -> _SlackPending:
    kind, options = _pending_kind_and_options(task, kind_hint=kind_hint, options_hint=options_hint)
    return _SlackPending(
        id=str(uuid4()),
        kind=kind,
        prompt=task.prompt,
        feature_id=feature_id,
        phase_name=phase_name,
        options=options,
    )


def _pending_kind_and_options(
    task: Ask,
    *,
    kind_hint: str | None = None,
    options_hint: list[str] | None = None,
) -> tuple[str, list[str] | None]:
    if kind_hint == "approve":
        return "approve", list(options_hint or ["Approve", "Reject", "Give feedback"])
    if kind_hint == "choose":
        return "choose", list(options_hint or [])
    if kind_hint == "respond":
        return "respond", None
    task_input = getattr(task, "input", None)
    options = getattr(task_input, "options", None)
    if options is not None:
        options = list(options)
        if options == ["Approve", "Reject", "Give feedback"]:
            return "approve", options
        return "choose", options
    if isinstance(task_input, Confirm):
        return "approve", ["Approve", "Reject"]
    return "respond", None


def _extract_question_payload(prompt: str) -> tuple[str, list[str], bool]:
    """Parse prompt that may be JSON with question/options fields.

    Mirrors iriai_compose/runtimes/terminal.py:_display_prompt.
    Returns (question_text, options_list, allow_background).
    """
    try:
        data = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return prompt, [], False

    if not isinstance(data, dict):
        return prompt, [], False

    question = data.get("question")
    if question:
        options = data.get("options", [])
        return question, options if isinstance(options, list) else [], bool(data.get("allow_background"))

    # Any JSON object without a question is structured model data —
    # don't dump raw JSON on the user (catches Envelopes with empty
    # question AND raw model JSON like PRD, TechnicalPlan, etc.)
    return (
        "The agent is processing. Reply with feedback or guidance.",
        [],
        bool(data.get("allow_background")),
    )


def _extract_question(prompt: str) -> tuple[str, list[str]]:
    question, options, _allow_background = _extract_question_payload(prompt)
    return question, options
