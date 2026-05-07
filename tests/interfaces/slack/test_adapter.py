from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.interfaces.slack import adapter as adapter_module
from iriai_build_v2.interfaces.slack.adapter import SlackAdapter


class _FakeSocketClient:
    def __init__(self) -> None:
        self.responses: list[object] = []

    async def send_socket_mode_response(self, response: object) -> None:
        self.responses.append(response)


@pytest.mark.asyncio
async def test_dispatch_routes_app_mention_as_inbound_message(monkeypatch):
    seen: list[dict] = []

    async def _capture_message(_adapter: SlackAdapter, event: dict) -> None:
        seen.append(event)

    monkeypatch.setattr(adapter_module, "handle_message", _capture_message)
    adapter = SlackAdapter(
        app_token="xapp-test",
        bot_token="xoxb-test",
        planning_channel="",
        mode="multiplayer",
    )
    client = _FakeSocketClient()
    request = SimpleNamespace(
        type="events_api",
        envelope_id="env-1",
        payload={
            "event": {
                "type": "app_mention",
                "channel": "CSUP",
                "user": "U1",
                "text": "<@B001> how is it looking?",
            }
        },
    )

    await adapter._dispatch(client, request)

    assert len(client.responses) == 1
    assert seen == [request.payload["event"]]
