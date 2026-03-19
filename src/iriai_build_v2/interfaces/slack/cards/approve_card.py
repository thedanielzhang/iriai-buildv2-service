"""ApproveCard: Gate approval card with approve/reject and feedback via modal.

Used for ``kind="approve"`` interactions (Gate decisions in gate_and_revise loops).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ApproveCard:
    """Gate approval card.

    Layout::

        ┌──────────────────────────────────────────┐
        │ *Approval Required*                       │
        │ {context}                                  │
        │ Review in browser: {url}                   │
        ├────────────────────────────────────────────┤
        │ [Approve]  [Reject]  [Feedback]            │
        └────────────────────────────────────────────┘

    Note: Slack Block Kit does not support ``plain_text_input`` in ``actions``
    blocks, so feedback uses a modal opened by the Feedback button.
    """

    pending_id: str
    title: str
    context: str
    review_urls: list[str] | None = None

    def build_blocks(self) -> list[dict[str, Any]]:
        return [
            self._header_section(),
            self._decision_buttons(),
        ]

    # ── Private block builders ─────────────────────────────────────────

    def _header_section(self) -> dict[str, Any]:
        text = f"*{self.title}*"
        if self.context:
            text += f"\n{self.context}"
        if self.review_urls:
            text += "\n"
            for url in self.review_urls:
                text += f"\n<{url}|Review in browser>"
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }

    def _decision_buttons(self) -> dict[str, Any]:
        pid = self.pending_id
        return {
            "type": "actions",
            "block_id": f"gate_{pid}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "value": "approve",
                    "action_id": f"gate_{pid}_approve",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                    "value": "reject",
                    "action_id": f"gate_{pid}_reject",
                    "style": "danger",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Feedback", "emoji": True},
                    "action_id": f"gate_{pid}_feedback",
                },
            ],
        }
