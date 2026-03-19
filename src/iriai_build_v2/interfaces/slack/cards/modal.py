"""Shared modal view builder used by all card types."""

from __future__ import annotations

from typing import Any


def build_modal_view(
    pending_id: str,
    title: str,
    label: str = "Your response",
) -> dict[str, Any]:
    """Build a Slack modal view with a multi-line text input."""
    return {
        "type": "modal",
        "private_metadata": pending_id,
        "title": {"type": "plain_text", "text": title[:24]},  # Slack max 24 chars
        "submit": {"type": "plain_text", "text": "Submit"},
        "blocks": [
            {
                "type": "input",
                "block_id": "reply_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reply_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "Type your response..."},
                },
                "label": {"type": "plain_text", "text": label},
            }
        ],
    }
