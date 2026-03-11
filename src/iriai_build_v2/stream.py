from __future__ import annotations

from typing import Any

# Track the last message ID to avoid printing duplicates.
# The Interview loop re-sends messages through resolve(), and the on_message
# callback fires for every SDK message including ones we've already seen.
_seen_ids: set[str] = set()


def print_stream(msg: Any) -> None:
    """Print agent messages to the terminal as they stream in."""
    try:
        from claude_agent_sdk.types import AssistantMessage
    except ImportError:
        return

    if not isinstance(msg, AssistantMessage):
        return

    # Deduplicate by message ID if available
    msg_id = getattr(msg, "id", None)
    if msg_id:
        if msg_id in _seen_ids:
            return
        _seen_ids.add(msg_id)

    for block in msg.content:
        typ = type(block).__name__
        if typ == "TextBlock":
            print(block.text, end="", flush=True)
        elif typ == "ToolUseBlock":
            name = block.name
            inp = block.input
            target = ""
            if isinstance(inp, dict):
                target = inp.get("file_path") or inp.get("command") or inp.get("pattern") or ""
            print(f"\n[tool] {name} {target}", flush=True)
