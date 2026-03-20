"""Slack-specific helpers: mrkdwn conversion, Block Kit builders, file upload.

Ported from v1's slack-helpers.js. Pure functions except for upload/reaction
which take a WebClient.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

# ── Markdown → Slack mrkdwn ────────────────────────────────────────────────


def markdown_to_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn format.

    Preserves code blocks and inline code, then converts:
      # Header  →  *Header*
      **bold**  →  *bold*
      [text](url)  →  <url|text>
      ![alt](path) →  [img:path]
    """
    if not text:
        return text

    # Protect code blocks
    code_blocks: list[str] = []
    def _save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(0))
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    result = re.sub(r"```[\s\S]*?```", _save_code_block, text)

    # Protect inline code
    inline_code: list[str] = []
    def _save_inline_code(m: re.Match) -> str:
        inline_code.append(m.group(0))
        return f"\x00INLINE{len(inline_code) - 1}\x00"

    result = re.sub(r"`[^`]+`", _save_inline_code, result)

    # Headers → bold
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

    # Bold: **text** → *text*
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)

    # Image refs: ![alt](path) → [img:path] (must be before links)
    result = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[img:\2]", result)

    # Links: [text](url) → <url|text>
    result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", result)

    # Restore inline code
    result = re.sub(
        r"\x00INLINE(\d+)\x00", lambda m: inline_code[int(m.group(1))], result
    )

    # Restore code blocks
    result = re.sub(
        r"\x00CODE(\d+)\x00", lambda m: code_blocks[int(m.group(1))], result
    )

    return result


# ── Block Kit Builders ─────────────────────────────────────────────────────


def build_decision_blocks(
    decision_id: str,
    title: str,
    context: str,
    options: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Build Block Kit blocks for a decision with action buttons.

    Each option dict should have: id, label, and optionally style ("primary"/"danger").
    action_id format: decision_{decision_id}_{option_id}
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{title}*" + (f"\n{context}" if context else ""),
            },
        },
        {
            "type": "actions",
            "block_id": f"decision_{decision_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": opt["label"], "emoji": True},
                    "value": opt["id"],
                    "action_id": f"decision_{decision_id}_{opt['id']}",
                    **({"style": opt["style"]} if opt.get("style") else {}),
                }
                for opt in options
            ],
        },
    ]
    return blocks


def build_resolved_blocks(
    title: str,
    selected_label: str,
    resolved_by: str,
    feedback: str = "",
) -> list[dict[str, Any]]:
    """Build replacement blocks showing a resolved decision."""
    feedback_line = ""
    if feedback:
        quoted = feedback.replace("\n", "\n> ")
        feedback_line = f"\n> {quoted}"

    by_text = f" by <@{resolved_by}>" if resolved_by else ""

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{title}*\n~Resolved~: *{selected_label}*{by_text}{feedback_line}",
            },
        }
    ]


# ── File Upload ────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


async def upload_file(
    web: AsyncWebClient,
    channel: str,
    file_path: str,
    title: str,
    *,
    thread_ts: str | None = None,
) -> bool:
    """Upload a file to a Slack channel. Returns True on success."""
    p = Path(file_path).resolve()
    if not p.exists():
        logger.warning("File not found: %s", file_path)
        return False

    if p.stat().st_size > _MAX_UPLOAD_BYTES:
        logger.warning(
            "File too large (%.1f MB): %s",
            p.stat().st_size / 1024 / 1024,
            file_path,
        )
        return False

    try:
        kwargs: dict[str, Any] = {
            "channel_id": channel,
            "file": str(p),
            "filename": p.name,
            "title": title,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await web.files_upload_v2(**kwargs)
        return True
    except Exception:
        logger.exception("File upload failed for %s", file_path)
        # Fallback: post truncated content as text
        try:
            content = p.read_text()
            if len(content) > 3000:
                content = content[:3000] + "\n\n_(truncated — full document in repo)_"
            post_kwargs: dict[str, Any] = {
                "channel": channel,
                "text": markdown_to_mrkdwn(f"*{title}:*\n\n{content}"),
                "mrkdwn": True,
            }
            if thread_ts:
                post_kwargs["thread_ts"] = thread_ts
            await web.chat_postMessage(**post_kwargs)
        except Exception:
            logger.exception("Fallback text post also failed for %s", title)
        return False


# ── Thread Posting ─────────────────────────────────────────────────────────


async def post_to_thread(
    web: AsyncWebClient,
    channel: str,
    thread_ts: str,
    text: str,
) -> str:
    """Post a mrkdwn-converted message to a thread. Returns message ts."""
    result = await web.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=markdown_to_mrkdwn(text),
        mrkdwn=True,
    )
    return result["ts"]


# ── Reactions ──────────────────────────────────────────────────────────────


async def add_reaction(
    web: AsyncWebClient, channel: str, timestamp: str, reaction: str
) -> None:
    """Add a reaction. Silently ignores errors (e.g. already_reacted)."""
    try:
        await web.reactions_add(channel=channel, name=reaction, timestamp=timestamp)
    except Exception:
        pass


async def remove_reaction(
    web: AsyncWebClient, channel: str, timestamp: str, reaction: str
) -> None:
    """Remove a reaction. Silently ignores errors (e.g. no_reaction)."""
    try:
        await web.reactions_remove(channel=channel, name=reaction, timestamp=timestamp)
    except Exception:
        pass


# ── Long text splitting ────────────────────────────────────────────────

_SECTION_TEXT_LIMIT = 2900  # Slack max is 3000, leave room for formatting


def split_mrkdwn_blocks(text: str, limit: int = _SECTION_TEXT_LIMIT) -> list[dict[str, Any]]:
    """Split long mrkdwn text into multiple section blocks.

    Breaks at double-newline (paragraph) boundaries first, then single
    newlines, to avoid mid-sentence cuts.  Returns a list of section
    blocks, each within the char limit.
    """
    if len(text) <= limit:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # If single paragraph exceeds limit, split by lines
            if len(paragraph) > limit:
                for line in paragraph.split("\n"):
                    line_candidate = f"{current}\n{line}" if current else line
                    if len(line_candidate) <= limit:
                        current = line_candidate
                    else:
                        if current:
                            chunks.append(current)
                        # Hard split if single line exceeds limit
                        while len(line) > limit:
                            chunks.append(line[:limit])
                            line = line[limit:]
                        current = line
            else:
                current = paragraph

    if current:
        chunks.append(current)

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in chunks
    ]


# ── Backward-compat re-exports (moved to cards/) ─────────────────────────

from .cards.modal import build_modal_view  # noqa: F401, E402
