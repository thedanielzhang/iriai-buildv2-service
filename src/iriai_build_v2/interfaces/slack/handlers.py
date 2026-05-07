"""Inbound Slack event handlers.

Each handler applies filtering (e.g. multiplayer/singleplayer mode) and
dispatches to callback hooks on the adapter. The Slack workflow (built later)
registers the actual business logic callbacks.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapter import SlackAdapter

logger = logging.getLogger(__name__)
_SLACK_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")


async def handle_message(adapter: SlackAdapter, event: dict) -> None:
    """Route incoming Slack messages.

    Applies multiplayer/singleplayer filtering before forwarding.
    """
    # Ignore bot/app-origin messages. Slack does not always set subtype=bot_message
    # for app-authored messages, so filter on every bot marker before workflow
    # routing. Otherwise a supervisor/status bot can accidentally resume a
    # recovered workflow because workflow channels treat "any message" as input.
    if _is_bot_message(event, adapter.bot_user_id):
        return

    text = event.get("text", "")
    channel = event.get("channel", "")
    bot_mention = f"<@{adapter.bot_user_id}>"
    if _mentions_ignored_user(
        text,
        adapter.ignored_mention_user_ids,
        adapter.bot_user_id,
    ):
        logger.debug(
            "[slack] ignoring message in %s directed at ignored mention: %s",
            channel,
            text[:100],
        )
        return

    if channel == adapter.planning_channel:
        # Planning channel: only forward messages matching a [TAG] pattern
        from .parser import parse_workflow_request

        if parse_workflow_request(text) is None:
            return
    elif adapter.get_channel_mode(channel) == "multiplayer":
        # Multiplayer workflow channels: only respond if bot is @mentioned
        if bot_mention not in text:
            return
        # Strip the mention from the message text
        text = re.sub(rf"\s*{re.escape(bot_mention)}\s*", " ", text).strip()
    elif bot_mention in text:
        # Singleplayer channels accept natural messages, but still clean up an
        # explicit mention when Slack delivers an app_mention event.
        text = re.sub(rf"\s*{re.escape(bot_mention)}\s*", " ", text).strip()

    # singleplayer: all messages pass through

    logger.info(
        "[slack] message from %s in %s: %s",
        event.get("user"),
        event.get("channel"),
        text[:100],
    )

    if adapter.on_message_callback:
        await adapter.on_message_callback({**event, "text": text})


def _mentions_ignored_user(
    text: str,
    ignored_user_ids: set[str],
    own_bot_user_id: str | None,
) -> bool:
    """Return True when a human message is explicitly addressed elsewhere."""

    if not ignored_user_ids:
        return False
    mentions = set(_SLACK_MENTION_RE.findall(text or ""))
    if not mentions or (own_bot_user_id and own_bot_user_id in mentions):
        return False
    return bool(mentions & ignored_user_ids)


def _is_bot_message(event: dict, own_bot_user_id: str | None) -> bool:
    """Return True for Slack messages that were not authored by a human user."""

    if own_bot_user_id and event.get("user") == own_bot_user_id:
        return True
    subtype = event.get("subtype")
    if subtype in ("bot_message", "message_changed", "message_deleted"):
        return True
    return any(
        event.get(key)
        for key in (
            "bot_id",
            "app_id",
            "bot_profile",
            "is_bot",
        )
    )


async def handle_action(adapter: SlackAdapter, body: dict, action: dict) -> None:
    """Route Block Kit button clicks.

    Parses action_id format: decision_{decision_id}_{option_id}
    """
    action_id = action.get("action_id", "")
    user_id = body.get("user", {}).get("id", "")
    channel = body.get("channel", {}).get("id", "")
    message_ts = body.get("message", {}).get("ts", "")

    logger.info(
        "[slack] action %s by %s in %s",
        action_id,
        user_id,
        channel,
    )

    if adapter.on_action_callback:
        await adapter.on_action_callback(body, action)


async def handle_reaction(adapter: SlackAdapter, event: dict) -> None:
    """Route emoji reactions."""
    reaction = event.get("reaction", "")
    user_id = event.get("user", "")

    # Ignore bot's own reactions
    if user_id == adapter.bot_user_id:
        return

    logger.info(
        "[slack] reaction :%s: by %s on %s",
        reaction,
        user_id,
        event.get("item", {}).get("ts", ""),
    )

    if adapter.on_reaction_callback:
        await adapter.on_reaction_callback(event)


async def handle_view_submission(adapter: SlackAdapter, payload: dict) -> None:
    """Route modal view submissions."""
    view = payload.get("view", {})
    pending_id = view.get("private_metadata", "")
    user_id = payload.get("user", {}).get("id", "")

    logger.info("[slack] view_submission for pending %s by %s", pending_id, user_id)

    if adapter.on_view_submission_callback:
        await adapter.on_view_submission_callback(payload)
