"""SlackAdapter: Socket Mode connection + WebClient for sending/receiving messages."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Literal

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from .handlers import handle_action, handle_message, handle_reaction, handle_view_submission
from .helpers import (
    build_decision_blocks,
    build_resolved_blocks,
    markdown_to_mrkdwn,
    upload_file as _upload_file,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 1.0  # seconds

AsyncEventCallback = Callable[[dict], Awaitable[None]]
AsyncActionCallback = Callable[[dict, dict], Awaitable[None]]


async def _call_with_retry(coro_factory: Callable[[], Any]) -> Any:
    """Call a Slack API method with retry on rate limit (429).

    ``coro_factory`` must be a zero-arg callable that returns a new awaitable
    each invocation (we can't re-await the same coroutine on retry).
    """
    from slack_sdk.errors import SlackApiError

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except SlackApiError as exc:
            if exc.response.status_code != 429 or attempt == _MAX_RETRIES:
                raise
            retry_after = float(exc.response.headers.get("Retry-After", _DEFAULT_RETRY_DELAY))
            logger.warning(
                "Slack rate limit hit (attempt %d/%d) — retrying in %.1fs",
                attempt + 1, _MAX_RETRIES + 1, retry_after,
            )
            await asyncio.sleep(retry_after)


class SlackAdapter:
    """Manages the Slack Socket Mode connection and provides outbound messaging.

    Inbound events are dispatched to handlers.py which apply filtering
    (multiplayer/singleplayer) and forward to registered callback hooks.
    """

    def __init__(
        self,
        *,
        app_token: str,
        bot_token: str,
        planning_channel: str,
        mode: Literal["multiplayer", "singleplayer"] = "multiplayer",
    ) -> None:
        self._app_token = app_token
        self._bot_token = bot_token
        self._planning_channel = planning_channel
        self.mode = mode

        self._web = AsyncWebClient(token=bot_token)
        self._socket = SocketModeClient(app_token=app_token, web_client=self._web)
        self._bot_user_id: str | None = None
        self._channel_modes: dict[str, str] = {}  # per-channel mode overrides

        # Callback hooks for inbound events (set by workflow/consumer later)
        self.on_message_callback: AsyncEventCallback | None = None
        self.on_action_callback: AsyncActionCallback | None = None
        self.on_reaction_callback: AsyncEventCallback | None = None
        self.on_view_submission_callback: AsyncEventCallback | None = None

    # ── Connection ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Authenticate and start Socket Mode. Fails fast on auth failure."""
        auth = await self._web.auth_test()
        self._bot_user_id = auth["user_id"]
        bot_name = auth.get("user", "unknown")
        logger.info("Authenticated as @%s (%s)", bot_name, self._bot_user_id)

        self._socket.socket_mode_request_listeners.append(self._dispatch)

        await self._socket.connect()
        logger.info(
            "Socket Mode connected — mode=%s, channel=%s",
            self.mode,
            self._planning_channel,
        )

    async def disconnect(self) -> None:
        """Clean shutdown of Socket Mode."""
        await self._socket.disconnect()
        await self._socket.close()
        logger.info("Socket Mode disconnected")

    # ── Inbound dispatch ───────────────────────────────────────────────────

    async def _dispatch(self, client: SocketModeClient, req: SocketModeRequest) -> None:
        """Route Socket Mode events to the appropriate handler."""
        # Acknowledge immediately to prevent retries
        response = SocketModeResponse(envelope_id=req.envelope_id)
        await client.send_socket_mode_response(response)

        if req.type == "events_api":
            event = req.payload.get("event", {})
            event_type = event.get("type", "")

            if event_type == "message":
                await handle_message(self, event)
            elif event_type == "reaction_added":
                await handle_reaction(self, event)

        elif req.type == "interactive":
            payload = req.payload
            if payload.get("type") == "view_submission":
                await handle_view_submission(self, payload)
            else:
                actions = payload.get("actions", [])
                for action in actions:
                    await handle_action(self, payload, action)

    # ── Outbound messaging ─────────────────────────────────────────────────

    async def post_message(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> str:
        """Post a mrkdwn message. Returns message ts."""
        kwargs: dict[str, Any] = {
            "channel": channel,
            "text": markdown_to_mrkdwn(text),
            "mrkdwn": True,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        result = await _call_with_retry(lambda: self._web.chat_postMessage(**kwargs))
        return result["ts"]

    async def post_blocks(
        self,
        channel: str,
        blocks: list[dict[str, Any]],
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> str:
        """Post Block Kit blocks with a text fallback. Returns message ts."""
        kwargs: dict[str, Any] = {
            "channel": channel,
            "blocks": blocks,
            "text": text,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        result = await _call_with_retry(lambda: self._web.chat_postMessage(**kwargs))
        return result["ts"]

    async def update_message(
        self,
        channel: str,
        ts: str,
        *,
        text: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update an existing message."""
        kwargs: dict[str, Any] = {"channel": channel, "ts": ts}
        if text is not None:
            kwargs["text"] = text
        if blocks is not None:
            kwargs["blocks"] = blocks
        await _call_with_retry(lambda: self._web.chat_update(**kwargs))

    async def post_decision(
        self,
        channel: str,
        decision_id: str,
        title: str,
        context: str,
        options: list[dict[str, str]],
        *,
        thread_ts: str | None = None,
    ) -> str:
        """Post a Block Kit decision (approve/reject/choose). Returns ts."""
        blocks = build_decision_blocks(decision_id, title, context, options)
        return await self.post_blocks(channel, blocks, title, thread_ts=thread_ts)

    async def resolve_decision(
        self,
        channel: str,
        ts: str,
        title: str,
        selected_label: str,
        resolved_by: str,
        feedback: str = "",
    ) -> None:
        """Replace decision buttons with resolved state."""
        blocks = build_resolved_blocks(title, selected_label, resolved_by, feedback)
        await self.update_message(
            channel, ts, blocks=blocks, text=f"Decision resolved: {selected_label}"
        )

    async def upload_file(
        self,
        channel: str,
        file_path: str,
        title: str,
        *,
        thread_ts: str | None = None,
    ) -> bool:
        """Upload a file to a channel."""
        return await _upload_file(
            self._web, channel, file_path, title, thread_ts=thread_ts
        )

    async def open_modal(self, trigger_id: str, view: dict[str, Any]) -> None:
        """Open a Slack modal via views.open."""
        await _call_with_retry(lambda: self._web.views_open(trigger_id=trigger_id, view=view))

    async def add_reaction(self, channel: str, ts: str, reaction: str) -> None:
        """Add a reaction emoji to a message."""
        from .helpers import add_reaction

        await add_reaction(self._web, channel, ts, reaction)

    async def remove_reaction(self, channel: str, ts: str, reaction: str) -> None:
        """Remove a reaction emoji from a message."""
        from .helpers import remove_reaction

        await remove_reaction(self._web, channel, ts, reaction)

    # ── Channel Management ────────────────────────────────────────────────

    async def create_channel(self, name: str) -> str:
        """Create a public channel. Returns channel ID.

        On ``name_taken`` error, retries with a random 4-char suffix.
        """
        import secrets

        for attempt in range(3):
            try_name = name if attempt == 0 else f"{name}-{secrets.token_hex(2)}"
            try:
                result = await _call_with_retry(lambda: self._web.conversations_create(name=try_name))
                channel_id = result["channel"]["id"]
                logger.info("Created channel %s (%s)", try_name, channel_id)
                return channel_id
            except Exception as e:
                if "name_taken" in str(e) and attempt < 2:
                    continue
                raise

        raise RuntimeError(f"Failed to create channel {name} after 3 attempts")

    def set_channel_mode(self, channel: str, mode: str) -> None:
        """Set the interaction mode for a specific channel."""
        self._channel_modes[channel] = mode

    def get_channel_mode(self, channel: str) -> str:
        """Get the interaction mode for a channel, falling back to the global default."""
        return self._channel_modes.get(channel, self.mode)

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def planning_channel(self) -> str:
        return self._planning_channel

    @property
    def bot_user_id(self) -> str | None:
        return self._bot_user_id

    @property
    def web(self) -> AsyncWebClient:
        return self._web
