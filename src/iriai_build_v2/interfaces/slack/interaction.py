"""SlackInteractionRuntime: bridges iriai-compose interaction tasks to Slack.

All user interactions are self-contained within Block Kit cards — buttons for
choices, inline text inputs for quick answers, and an Expand button that opens
a modal for longer responses. No "reply in channel" patterns.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from iriai_compose.runner import InteractionRuntime

from .cards import ApproveCard, ChooseCard, RespondCard, build_modal_view
from .helpers import build_resolved_blocks, split_mrkdwn_blocks

if TYPE_CHECKING:
    from iriai_compose.pending import Pending

    from .adapter import SlackAdapter

logger = logging.getLogger(__name__)


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
        # pending_id → feature_id for turn persistence
        self._pending_features: dict[str, str] = {}
        # feature_id ↔ channel bidirectional mapping
        self._feature_channels: dict[str, str] = {}
        self._channel_features: dict[str, str] = {}

        # Turn persistence: set by orchestrator when a runtime is available
        self._session_store: Any = None
        self._agent_runtime: Any = None  # ClaudeAgentRuntime with get_active_session_key()

    # ── Channel Registration ──────────────────────────────────────────────

    def register_channel(self, feature_id: str, channel: str) -> None:
        self._feature_channels[feature_id] = channel
        self._channel_features[channel] = feature_id

    def unregister_channel(self, feature_id: str) -> None:
        channel = self._feature_channels.pop(feature_id, None)
        if channel:
            self._channel_features.pop(channel, None)

    def has_pending(self, channel: str) -> bool:
        """Check if any pending interaction exists for this channel's feature."""
        feature_id = self._channel_features.get(channel)
        if not feature_id:
            return False
        return any(
            pid.endswith(f"_{feature_id}") or feature_id in pid
            for pid in self._pending_futures
        )

    # ── InteractionRuntime.resolve() ──────────────────────────────────────

    async def resolve(self, pending: Pending) -> str | bool:
        channel = self._feature_channels.get(pending.feature_id)
        if not channel:
            raise RuntimeError(
                f"No Slack channel registered for feature {pending.feature_id}"
            )

        # Don't block on empty-question Envelopes — let the Interview
        # continue without user input so the agent gets another turn.
        if pending.kind == "respond" and not _has_question(pending.prompt):
            return "Continue"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | bool] = loop.create_future()
        self._pending_futures[pending.id] = future
        self._pending_features[pending.id] = pending.feature_id

        if pending.kind == "approve":
            await self._post_approve(pending, channel)
        elif pending.kind == "choose":
            await self._post_choose(pending, channel)
        else:  # respond
            await self._post_respond(pending, channel)

        try:
            result = await future
        finally:
            self._pending_futures.pop(pending.id, None)
            self._pending_options.pop(pending.id, None)
            self._pending_messages.pop(pending.id, None)
            self._pending_features.pop(pending.id, None)

        return result

    # ── Inbound Event Handlers ────────────────────────────────────────────

    async def handle_action(self, body: dict, action: dict) -> None:
        """Route button clicks, inline text input, and dropdown selections."""
        action_id = action.get("action_id", "")
        trigger_id = body.get("trigger_id", "")
        channel = body.get("channel", {}).get("id", "")
        message_ts = body.get("message", {}).get("ts", "")
        user_id = body.get("user", {}).get("id", "")

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
        pending_id = view.get("private_metadata", "")
        user_id = payload.get("user", {}).get("id", "")

        if not pending_id:
            return

        # Extract text from the modal input
        values = view.get("state", {}).get("values", {})
        reply_block = values.get("reply_block", {})
        reply_input = reply_block.get("reply_input", {})
        text = (reply_input.get("value") or "").strip()

        if text:
            self._resolve_pending(pending_id, text, label=text[:50], user_id=user_id)
        else:
            # Empty submission = reject without specific feedback
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
                view = build_modal_view(pending_id, "Reply")
                await self._adapter.open_modal(trigger_id, view)
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
                    pending_id, "Reject",
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
            self._resolve_pending(pending_id, False, label="Rejected", user_id=user_id)
        elif option_id == "feedback":
            if trigger_id:
                view = build_modal_view(pending_id, "Feedback")
                await self._adapter.open_modal(trigger_id, view)
        else:
            self._resolve_pending(pending_id, option_id, label=option_id, user_id=user_id)

    # ── Post Helpers ──────────────────────────────────────────────────────

    async def _post_respond(self, pending: Pending, channel: str) -> None:
        """Post an Interview question card with options + inline text input + Expand.

        If the question exceeds Slack's 3000-char section limit, the full
        text is posted as preceding context message(s), and the interactive
        card gets a short summary.
        """
        question, options = _extract_question(pending.prompt)

        full_text = f"*{pending.phase_name}*\n{question}"
        if len(full_text) > 2900:
            # Post full content as context message(s) — no truncation
            context_blocks = split_mrkdwn_blocks(full_text)
            await self._adapter.post_blocks(channel, context_blocks, question[:100])

            # Post compact interactive card with just the buttons
            short_q = question[:200] + "..." if len(question) > 200 else question
            card = RespondCard(
                pending_id=pending.id,
                phase_name=pending.phase_name,
                question=short_q,
                options=options,
            )
        else:
            card = RespondCard(
                pending_id=pending.id,
                phase_name=pending.phase_name,
                question=question,
                options=options,
            )

        blocks = card.build_blocks()
        ts = await self._adapter.post_blocks(channel, blocks, question[:100])
        self._pending_messages[pending.id] = (channel, ts)
        if options:
            self._pending_options[pending.id] = options

    async def _post_approve(self, pending: Pending, channel: str) -> None:
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
            await self._adapter.post_blocks(channel, context_blocks, "Approval Required")
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
        ts = await self._adapter.post_blocks(channel, blocks, "Approval Required")
        self._pending_messages[pending.id] = (channel, ts)

    async def _post_choose(self, pending: Pending, channel: str) -> None:
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
        ts = await self._adapter.post_blocks(channel, blocks, question[:100])
        self._pending_messages[pending.id] = (channel, ts)
        if options_list:
            self._pending_options[pending.id] = options_list

    # ── Internal ──────────────────────────────────────────────────────────

    def _resolve_pending(
        self,
        pending_id: str,
        value: str | bool,
        *,
        label: str = "",
        user_id: str = "",
    ) -> None:
        """Resolve a pending Future and update the card to resolved state.

        Also persists the user's turn to ``session.metadata["turns"]`` for
        mid-interview resume support.
        """
        future = self._pending_futures.get(pending_id)
        if future and not future.done():
            future.set_result(value)

        # Persist user turn for mid-interview resume
        if isinstance(value, str) and self._session_store and self._agent_runtime:
            feature_id = self._pending_features.get(pending_id)
            if feature_id:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._persist_user_turn(feature_id, value))
                except RuntimeError:
                    pass

        # Update card to resolved state
        msg_info = self._pending_messages.get(pending_id)
        if msg_info:
            channel, ts = msg_info
            display = label or (
                str(value)
                if isinstance(value, str)
                else ("Approved" if value else "Rejected")
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._update_to_resolved(channel, ts, display, user_id))
            except RuntimeError:
                pass

    async def _persist_user_turn(self, feature_id: str, text: str) -> None:
        """Persist a user turn to session metadata for mid-interview resume."""
        try:
            session_key = self._agent_runtime.get_active_session_key(feature_id)
            if not session_key:
                return
            session = await self._session_store.load(session_key)
            if not session:
                return
            turns = session.metadata.get("turns", [])
            turns.append({"role": "user", "text": text[:5000], "turn": len(turns) + 1})
            session.metadata["turns"] = turns
            await self._session_store.save(session)
        except Exception:
            logger.debug("Failed to persist user turn for %s", feature_id, exc_info=True)

    async def _update_to_resolved(
        self, channel: str, ts: str, display: str, user_id: str
    ) -> None:
        """Replace the card with a resolved-state block."""
        try:
            blocks = build_resolved_blocks("Response", display, user_id)
            await self._adapter.update_message(channel, ts, blocks=blocks, text=f"Resolved: {display}")
        except Exception:
            logger.exception("Failed to update card to resolved state")


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


def _extract_question(prompt: str) -> tuple[str, list[str]]:
    """Parse prompt that may be JSON with question/options fields.

    Mirrors iriai_compose/runtimes/terminal.py:_display_prompt.
    Returns (question_text, options_list).
    """
    try:
        data = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return prompt, []

    if not isinstance(data, dict):
        return prompt, []

    question = data.get("question")
    if question:
        options = data.get("options", [])
        return question, options if isinstance(options, list) else []

    # Any JSON object without a question is structured model data —
    # don't dump raw JSON on the user (catches Envelopes with empty
    # question AND raw model JSON like PRD, TechnicalPlan, etc.)
    return (
        "The agent is processing. Reply with feedback or guidance.",
        [],
    )
