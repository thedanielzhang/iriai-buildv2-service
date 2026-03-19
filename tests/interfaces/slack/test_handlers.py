"""Tests for Slack message handlers: multiplayer/singleplayer filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from iriai_build_v2.interfaces.slack.handlers import handle_message


@dataclass
class MockAdapter:
    """Lightweight mock adapter with just the attributes handlers need."""

    mode: str = "multiplayer"
    bot_user_id: str = "B001"
    planning_channel: str = "C_PLANNING"
    on_message_callback: Any = None
    received: list[dict] = field(default_factory=list)

    def __post_init__(self):
        async def _capture(event: dict) -> None:
            self.received.append(event)

        self.on_message_callback = _capture

    def get_channel_mode(self, channel: str) -> str:
        return self.mode


def _msg(text: str = "hello", user: str = "U999", subtype: str | None = None) -> dict:
    event: dict[str, Any] = {"text": text, "user": user, "channel": "C123"}
    if subtype is not None:
        event["subtype"] = subtype
    return event


# ── Multiplayer mode ──────────────────────────────────────────────────────


class TestMultiplayer:
    @pytest.mark.asyncio
    async def test_ignores_without_mention(self):
        adapter = MockAdapter(mode="multiplayer")
        await handle_message(adapter, _msg("just chatting"))
        assert adapter.received == []

    @pytest.mark.asyncio
    async def test_processes_with_mention(self):
        adapter = MockAdapter(mode="multiplayer")
        await handle_message(adapter, _msg("<@B001> do something"))
        assert len(adapter.received) == 1

    @pytest.mark.asyncio
    async def test_strips_mention_from_text(self):
        adapter = MockAdapter(mode="multiplayer")
        await handle_message(adapter, _msg("<@B001> do something"))
        assert adapter.received[0]["text"] == "do something"


# ── Singleplayer mode ────────────────────────────────────────────────────


class TestSingleplayer:
    @pytest.mark.asyncio
    async def test_processes_all_messages(self):
        adapter = MockAdapter(mode="singleplayer")
        await handle_message(adapter, _msg("anything"))
        assert len(adapter.received) == 1


# ── Both modes ────────────────────────────────────────────────────────────


class TestBothModes:
    @pytest.mark.asyncio
    async def test_ignores_own_messages_multiplayer(self):
        adapter = MockAdapter(mode="multiplayer")
        await handle_message(adapter, _msg("<@B001> hi", user="B001"))
        assert adapter.received == []

    @pytest.mark.asyncio
    async def test_ignores_own_messages_singleplayer(self):
        adapter = MockAdapter(mode="singleplayer")
        await handle_message(adapter, _msg("hi", user="B001"))
        assert adapter.received == []

    @pytest.mark.asyncio
    async def test_ignores_bot_message_subtype(self):
        adapter = MockAdapter(mode="singleplayer")
        await handle_message(adapter, _msg(subtype="bot_message"))
        assert adapter.received == []

    @pytest.mark.asyncio
    async def test_ignores_message_changed_subtype(self):
        adapter = MockAdapter(mode="singleplayer")
        await handle_message(adapter, _msg(subtype="message_changed"))
        assert adapter.received == []

    @pytest.mark.asyncio
    async def test_callback_receives_cleaned_event(self):
        adapter = MockAdapter(mode="multiplayer")
        event = _msg("<@B001> build it", user="U555")
        await handle_message(adapter, event)
        received = adapter.received[0]
        assert received["text"] == "build it"
        assert received["user"] == "U555"
        assert received["channel"] == "C123"
