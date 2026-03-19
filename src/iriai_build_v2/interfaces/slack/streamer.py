"""SlackStreamer: live-update a Slack message with Claude SDK output.

Creates a message on first content, then updates it in-place with the latest
activity line. Shows a single status (thinking, tool use, etc.) at a time.
On completion, the final update contains only the actual text response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from .helpers import markdown_to_mrkdwn

if TYPE_CHECKING:
    from .adapter import SlackAdapter

logger = logging.getLogger(__name__)

# Minimum seconds between Slack API updates (avoids 429 rate limits)
_MIN_FLUSH_INTERVAL = 1.5

# Max characters for thinking / text block preview
_THINKING_TRUNCATE = 200

# Max characters for tool result preview
_RESULT_TRUNCATE = 150


def _format_thinking(thinking: str) -> str:
    text = thinking.strip().replace("\n", " ")
    if len(text) > _THINKING_TRUNCATE:
        text = text[:_THINKING_TRUNCATE] + "..."
    return f"\U0001f4ad _{text}_"


def _format_tool_use(name: str, input_data: dict[str, Any]) -> str:
    # Extract the most informative param
    target = (
        input_data.get("file_path")
        or input_data.get("command")
        or input_data.get("pattern")
        or input_data.get("query")
        or ""
    )
    if isinstance(target, str) and len(target) > 80:
        target = target[:80] + "..."
    return f"\U0001f527 *{name}* {target}"


def _format_tool_result(content: Any, is_error: bool | None) -> str:
    if is_error:
        return "  \u21b3 \u2717 error"
    if content is None:
        return "  \u21b3 \u2713"
    text = str(content).strip().replace("\n", " ")
    if len(text) > _RESULT_TRUNCATE:
        text = text[:_RESULT_TRUNCATE] + "..."
    return f"  \u21b3 {text}"


class SlackStreamer:
    """Streams Claude SDK messages to a single Slack message via chat.update.

    Shows one status line at a time (latest activity). Flushes immediately on
    each new message. A ``_flushing`` flag serialises Slack API calls — if a
    new status arrives mid-flush it is picked up once the current call returns.
    """

    def __init__(
        self,
        adapter: SlackAdapter,
        channel: str,
        *,
        thread_ts: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._channel = channel
        self._thread_ts = thread_ts
        self._message_ts: str | None = None
        self._final_text: str = ""
        self._current_status: str = ""
        self._flushing: bool = False
        self._pending: bool = False
        self._seen_ids: set[str] = set()
        self._last_flush_time: float = 0.0

    def on_message(self, msg: Any) -> None:
        """Synchronous callback for ClaudeAgentRuntime. Schedules async updates."""
        typ = type(msg).__name__

        if typ == "AssistantMessage":
            # Deduplicate by message ID
            msg_id = getattr(msg, "id", None)
            if msg_id:
                if msg_id in self._seen_ids:
                    return
                self._seen_ids.add(msg_id)

            status: str | None = None
            for block in msg.content:
                block_type = type(block).__name__
                if block_type == "ThinkingBlock":
                    status = _format_thinking(block.thinking)
                elif block_type == "ToolUseBlock":
                    status = _format_tool_use(block.name, block.input)
                elif block_type == "ToolResultBlock":
                    status = _format_tool_result(block.content, block.is_error)
                elif block_type == "TextBlock":
                    self._final_text += block.text
                    if not _is_structured_output(block.text):
                        preview = block.text.strip().replace("\n", " ")
                        if len(preview) > _THINKING_TRUNCATE:
                            preview = preview[:_THINKING_TRUNCATE] + "..."
                        status = preview

            if status:
                self._current_status = status
                self._schedule_flush()

        elif typ == "ResultMessage":
            # Capture state before reset (flush is async — avoids race condition)
            final_text = self._final_text
            final_ts = self._message_ts

            # Reset state immediately so the next invocation creates a fresh message
            self._current_status = ""
            self._final_text = ""
            self._message_ts = None
            self._seen_ids = set()
            self._pending = False
            self._last_flush_time = 0.0

            # Schedule final flush with captured state
            self._schedule_final_flush(final_text, final_ts)

    def _schedule_flush(self) -> None:
        """Schedule an immediate flush, serialised by the _flushing flag."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if self._flushing:
            # A flush is in-flight — it will pick up the latest status when done.
            self._pending = True
            return

        loop.create_task(self._flush())

    async def _flush(self) -> None:
        """Post or update the Slack message with the current single status line."""
        self._flushing = True
        try:
            # Throttle: wait if we flushed too recently
            elapsed = time.monotonic() - self._last_flush_time
            if elapsed < _MIN_FLUSH_INTERVAL:
                await asyncio.sleep(_MIN_FLUSH_INTERVAL - elapsed)

            text = self._current_status
            if not text:
                return

            if self._message_ts is None:
                self._message_ts = await self._adapter.post_message(
                    self._channel, text, thread_ts=self._thread_ts
                )
            else:
                await self._adapter.update_message(
                    self._channel, self._message_ts, text=text
                )
            self._last_flush_time = time.monotonic()
        except Exception:
            logger.exception("Failed to flush streamer to Slack")
        finally:
            self._flushing = False
            if self._pending:
                self._pending = False
                self._schedule_flush()

    def _schedule_final_flush(self, text: str, message_ts: str | None) -> None:
        """Schedule the final text update with captured state."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.create_task(self._do_final_flush(text, message_ts))

    async def _do_final_flush(self, text: str, message_ts: str | None) -> None:
        """Final update with captured state — immune to reset race."""
        # Don't replace verbose output with raw structured output JSON or empty text
        if not text or _is_structured_output(text):
            return

        display_text = markdown_to_mrkdwn(text)
        if len(display_text) > 39_000:
            display_text = display_text[:39_000] + "\n\n_(truncated)_"
        try:
            if message_ts:
                await self._adapter.update_message(
                    self._channel, message_ts, text=display_text
                )
            else:
                await self._adapter.post_message(
                    self._channel, display_text, thread_ts=self._thread_ts
                )
        except Exception:
            logger.exception("Failed final flush to Slack")


def _is_structured_output(text: str) -> bool:
    """Detect raw structured output JSON that shouldn't be displayed.

    Returns True if ``text`` looks like an Envelope or similar structured output
    (contains ``question`` or ``output`` keys in a JSON object).
    """
    stripped = text.strip()
    if not stripped or not stripped.startswith("{"):
        return False
    try:
        data = json.loads(stripped)
        return isinstance(data, dict) and ("question" in data or "output" in data)
    except (json.JSONDecodeError, TypeError):
        return False


def make_slack_on_message(
    adapter: SlackAdapter,
    channel: str,
    thread_ts: str | None = None,
) -> Callable[[Any], None]:
    """Create an on_message callback bound to a specific channel/thread."""
    streamer = SlackStreamer(adapter, channel, thread_ts=thread_ts)
    return streamer.on_message
