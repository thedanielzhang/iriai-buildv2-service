"""Parse workflow trigger messages from #planning channel.

Detects [TAG] patterns at the start of messages to determine which workflow to run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TAG_MAP: dict[str, str] = {
    "feature": "full-develop",
    "bug": "bugfix-v2",
    "plan": "planning",
}

_TAG_RE = re.compile(
    r"^\[(" + "|".join(TAG_MAP) + r")\]\s*(.+)",
    re.IGNORECASE,
)
_FEATURE_ID_RE = re.compile(r"(?i)\b([0-9a-f]{8})\b")


@dataclass
class ParsedRequest:
    workflow_name: str
    feature_name: str
    source_feature_id: str | None = None


def parse_workflow_request(text: str) -> ParsedRequest | None:
    """Extract workflow tag and feature name from a message.

    >>> parse_workflow_request("[FEATURE] Add dark mode")
    ParsedRequest(workflow_name='full-develop', feature_name='Add dark mode')
    >>> parse_workflow_request("just chatting") is None
    True
    """
    if not text:
        return None
    m = _TAG_RE.match(text.strip())
    if not m:
        return None
    tag = m.group(1).lower()
    name = m.group(2).strip()
    if not name:
        return None
    workflow_name = TAG_MAP.get(tag)
    if not workflow_name:
        return None
    source_feature_id = _extract_source_feature_id(name) if workflow_name == "bugfix-v2" else None
    return ParsedRequest(
        workflow_name=workflow_name,
        feature_name=name,
        source_feature_id=source_feature_id,
    )


def _extract_source_feature_id(text: str) -> str | None:
    """Extract a bare feature id from Slack-formatted bugflow launch text."""
    cleaned = text.strip().strip("*_`~ ")
    match = _FEATURE_ID_RE.search(cleaned)
    if match:
        return match.group(1).lower()
    return cleaned or None
