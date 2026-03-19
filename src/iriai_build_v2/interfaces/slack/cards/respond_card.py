"""RespondCard: Interview question card with selectable options and reply modal.

Used for ``kind="respond"`` interactions (Interview turns, free-form Respond tasks).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Section blocks support up to 3000 chars of text — plenty for any option.
# When options exceed this count, fall back to a static_select dropdown.
_MAX_SECTION_OPTIONS = 10


@dataclass
class RespondCard:
    """Interview/Respond question card.

    Layout (with options)::

        ┌───────────────────────────────────────────────────────┐
        │ *PM*                                                   │
        │ In one sentence, what does this feature do?           │
        ├───────────────────────────────────────────────────────┤
        │ CLI command that prints Charmander        [Select]     │
        │ ASCII art to the terminal                              │
        ├───────────────────────────────────────────────────────┤
        │ Web page that displays Charmander         [Select]     │
        │ ASCII art                                              │
        ├───────────────────────────────────────────────────────┤
        │ An ASCII art generator that can create    [Select]     │
        │ Charmander (and potentially other                      │
        │ Pokémon)                                               │
        ├───────────────────────────────────────────────────────┤
        │                                           [Reply]      │
        └───────────────────────────────────────────────────────┘

    Each option is a section block with the full option text and a "Select"
    button accessory — this gives vertical layout with no text truncation
    (section text supports up to 3000 chars).

    For >10 options, falls back to a ``static_select`` dropdown.
    """

    pending_id: str
    phase_name: str
    question: str
    options: list[str] = field(default_factory=list)

    def build_blocks(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [self._question_section()]
        if self.options:
            blocks.extend(self._option_blocks())
        blocks.append(self._reply_block())
        return blocks

    # ── Private block builders ─────────────────────────────────────────

    def _question_section(self) -> dict[str, Any]:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{self.phase_name}*\n{self.question}",
            },
        }

    def _option_blocks(self) -> list[dict[str, Any]]:
        """One section-with-accessory per option (vertical), or dropdown for many."""
        pid = self.pending_id

        if len(self.options) <= _MAX_SECTION_OPTIONS:
            return [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{idx + 1}. {opt}",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Select", "emoji": True},
                        "value": str(idx),
                        "action_id": f"respond_{pid}_opt_{idx}",
                    },
                }
                for idx, opt in enumerate(self.options)
            ]

        # >10 options → dropdown (static_select in an actions block)
        return [
            {
                "type": "actions",
                "block_id": f"respond_opts_{pid}",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": f"respond_{pid}_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Choose an option...",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": opt[:75],  # Slack dropdown option limit
                                },
                                "value": str(idx),
                            }
                            for idx, opt in enumerate(self.options)
                        ],
                    }
                ],
            }
        ]

    def _reply_block(self) -> dict[str, Any]:
        """Single Reply button that opens a modal for free-form text input."""
        pid = self.pending_id
        return {
            "type": "actions",
            "block_id": f"respond_input_{pid}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reply", "emoji": True},
                    "action_id": f"respond_{pid}_reply",
                    "style": "primary",
                },
            ],
        }
