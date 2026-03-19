"""ChooseCard: Selection card with one button per option.

Used for ``kind="choose"`` interactions (Choose tasks).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChooseCard:
    """Option selection card — buttons only, no text input.

    Layout::

        ┌──────────────────────────────────────────┐
        │ *Selection Required*                      │
        │ {question}                                 │
        ├────────────────────────────────────────────┤
        │ [Option A] [Option B] [Option C]           │
        └────────────────────────────────────────────┘
    """

    pending_id: str
    title: str
    question: str
    options: list[str] = field(default_factory=list)

    def build_blocks(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [self._header_section()]
        if self.options:
            blocks.append(self._option_buttons())
        return blocks

    # ── Private block builders ─────────────────────────────────────────

    def _header_section(self) -> dict[str, Any]:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{self.title}*\n{self.question}",
            },
        }

    def _option_buttons(self) -> dict[str, Any]:
        pid = self.pending_id
        return {
            "type": "actions",
            "block_id": f"choose_{pid}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": opt[:75],
                        "emoji": True,
                    },
                    "value": str(idx),
                    "action_id": f"choose_{pid}_opt_{idx}",
                    **({"style": "primary"} if idx == 0 else {}),
                }
                for idx, opt in enumerate(self.options)
            ],
        }
