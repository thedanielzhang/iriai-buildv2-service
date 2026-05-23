from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import EvidencePacket, SupervisorMode

_MAX_CELL = 140
_MAX_CARD_BODY = 200
_MAX_CARD_TITLE = 150


@dataclass(frozen=True)
class SupervisorStaleInvocationCard:
    packet: EvidencePacket
    mode: SupervisorMode = SupervisorMode.READ_ONLY

    def build_blocks(self) -> list[dict[str, Any]]:
        stale = _stale_fact(self.packet)
        token = str(stale.get("evidence_token") or "")
        actor = str(stale.get("actor") or self.packet.facts.get("actor") or "Codex invocation")
        elapsed = _duration(float(stale.get("elapsed_seconds") or 0))
        title = f"Stale Codex invocation: {actor}"[:_MAX_CARD_TITLE]
        action_text = (
            "Kill stale Codex"
            if self.mode == SupervisorMode.GUARDED
            else "Needs guarded mode"
        )
        action_id = (
            f"stale_codex_kill_{token}"
            if self.mode == SupervisorMode.GUARDED
            else f"stale_codex_readonly_{token}"
        )
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "block_id": f"stale_header_{token}",
                "text": {
                    "type": "plain_text",
                    "text": "Stale Codex Invocation",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "block_id": f"stale_summary_{token}",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{title}*\n`{actor}` has been idle for {elapsed}; "
                        "reset only this validated process tree."
                    ),
                },
            },
            {
                "type": "context",
                "block_id": f"stale_context_{token}",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Feature `{self.packet.feature_id}` · G{self.packet.group_idx}",
                    },
                ],
            },
            _evidence_section(stale, token),
            _diagnostic_section(stale, token),
            _plan_section(stale, token),
            {
                "type": "actions",
                "block_id": f"stale_actions_{token}",
                "elements": [
                    _button(
                        action_text,
                        action_id,
                        token,
                        style="danger" if self.mode == SupervisorMode.GUARDED else "primary",
                        confirm=_kill_confirm(actor) if self.mode == SupervisorMode.GUARDED else None,
                    ),
                    _button("Ignore once", f"stale_codex_ignore_{token}", token),
                    _button("Open trace", f"stale_codex_trace_{token}", token),
                    _button("Ask why", f"stale_codex_why_{token}", token),
                ],
            },
            {
                "type": "actions",
                "block_id": f"stale_feedback_{token}",
                "elements": [
                    _button("Correct", f"stale_codex_feedback_{token}", f"correct:{token}"),
                    _button("Wrong", f"stale_codex_feedback_{token}", f"wrong:{token}"),
                    _button("Dismiss", f"stale_codex_dismiss_{token}", token),
                ],
            },
        ]
        return _validate_block_kit(blocks)

    def fallback_text(self) -> str:
        stale = _stale_fact(self.packet)
        actor = str(stale.get("actor") or "Codex invocation")
        return f"Stale Codex invocation detected: {actor}"


def build_status_blocks(message: str, *, title: str = "Supervisor status") -> list[dict[str, Any]]:
    return _validate_block_kit([
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title[:_MAX_CARD_TITLE], "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": message[:3_000]},
        },
    ])


def build_resolved_notice_blocks(title: str, body: str) -> list[dict[str, Any]]:
    return _validate_block_kit([
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title[:_MAX_CARD_TITLE], "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body[:3_000]},
        },
    ])


def _button(
    text: str,
    action_id: str,
    value: str,
    *,
    style: str | None = None,
    confirm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text[:75], "emoji": True},
        "action_id": action_id,
        "value": value,
    }
    if style:
        button["style"] = style
    if confirm:
        button.update(confirm)
    return button


def _validate_block_kit(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"header", "section", "context", "actions", "divider"}
    invalid = sorted({str(block.get("type")) for block in blocks if block.get("type") not in allowed})
    if invalid:
        raise ValueError(f"invalid Slack Block Kit block type(s): {', '.join(invalid)}")
    return blocks


def _stale_fact(packet: EvidencePacket) -> dict[str, Any]:
    value = packet.facts.get("stale_codex_invocation") if packet.facts else None
    return value if isinstance(value, dict) else {}


def _card_body(stale: dict[str, Any]) -> str:
    pid = stale.get("pid")
    children = stale.get("child_pids") or []
    trace = str(stale.get("trace_path") or "")
    trace_name = trace.rsplit("/", 1)[-1] if trace else "unknown trace"
    body = f"PID `{pid}`"
    if children:
        body += f" with children `{','.join(str(pid) for pid in children)}`"
    body += f"; trace `{trace_name}`."
    return body[:_MAX_CARD_BODY]


def _evidence_section(stale: dict[str, Any], token: str) -> dict[str, Any]:
    fields = [
        f"*PID*\n`{stale.get('pid') or ''}`",
        f"*Children*\n`{','.join(str(pid) for pid in stale.get('child_pids') or []) or 'none'}`",
        f"*Elapsed*\n{_duration(float(stale.get('elapsed_seconds') or 0))}",
        f"*Output bytes*\n{stale.get('output_bytes') or 0}",
        f"*Last event*\n`{stale.get('last_event') or ''}`",
        f"*CPU*\n{_percent(stale.get('cpu_percent'))}",
    ]
    return {
        "type": "section",
        "block_id": f"stale_evidence_{token}",
        "fields": [{"type": "mrkdwn", "text": field[:2_000]} for field in fields],
    }


def _diagnostic_section(stale: dict[str, Any], token: str) -> dict[str, Any]:
    trace = str(stale.get("trace_path") or "")
    output = str(stale.get("output_path") or "")
    command = str(stale.get("command") or "")
    lines = [
        "*Why this looks stale*",
        "- Heartbeats report unchanged stdout, stderr, and output bytes.",
        "- The last item is `command_execution`, so heartbeat-only liveness is extending the run.",
    ]
    if trace:
        lines.append(f"- Trace: `{trace}`")
    if output:
        lines.append(f"- Output file: `{output}`")
    if command:
        lines.append(f"- Command: `{command[:500]}`")
    return {
        "type": "section",
        "block_id": f"stale_diagnostic_{token}",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)[:3_000]},
    }


def _plan_section(stale: dict[str, Any], token: str) -> dict[str, Any]:
    text = (
        "*Reset plan*\n"
        f"1. Confirm parent PID `{stale.get('pid')}` and children `{stale.get('child_pids') or []}` still match.\n"
        "2. Terminate only the stale Codex process tree.\n"
        "3. Watch the bridge release the slot and record the partial invocation."
    )
    return {
        "type": "section",
        "block_id": f"stale_plan_{token}",
        "text": {"type": "mrkdwn", "text": text[:3_000]},
    }


def _rich_text(text: str) -> dict[str, Any]:
    return {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [{"type": "text", "text": text}],
            }
        ],
    }


def _raw(text: str) -> dict[str, Any]:
    return {"type": "raw_text", "text": text[:_MAX_CELL]}


def _duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _percent(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "unknown"


def _kill_confirm(actor: str) -> dict[str, Any]:
    return {
        "confirm": {
            "title": {"type": "plain_text", "text": "Kill stale Codex?"},
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"This will terminate only `{actor}` if the process tree still "
                    "matches the stored supervisor evidence."
                ),
            },
            "confirm": {"type": "plain_text", "text": "Kill Codex"},
            "deny": {"type": "plain_text", "text": "Cancel"},
        }
    }
