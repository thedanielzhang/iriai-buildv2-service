from __future__ import annotations

"""Deprecated CLI interaction runtime.

The planning bridge is Slack-first, and threaded planning UX should be treated
as canonical there. This module remains as a local/debug fallback only.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from iriai_compose.pending import Pending
from iriai_compose.runner import InteractionRuntime
from iriai_compose.tasks import Ask

from ...planning_signals import BACKGROUND_RESPONSE, GateRejection

try:
    from iriai_compose.prompts import Confirm
except Exception:  # pragma: no cover - older iriai_compose versions
    class Confirm:  # type: ignore[no-redef]
        pass

_APPROVE_APPROVE = "Approve"
_APPROVE_REJECT = "Reject"
_APPROVE_FEEDBACK = "Give feedback"
_BACKGROUND_COMMAND = "/background"
_BACKGROUND_NOTE = "Type /background to finish this step in the background."


@dataclass
class _PromptPayload:
    question: str
    options: list[str]
    allow_background: bool = False
    thread_label: str = ""


def _parse_prompt(prompt: str) -> _PromptPayload:
    try:
        data = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return _PromptPayload(question=prompt, options=[])

    if not isinstance(data, dict):
        return _PromptPayload(question=prompt, options=[])

    question = data.get("question")
    if not isinstance(question, str) or not question:
        return _PromptPayload(
            question="The agent is processing. Reply with feedback or guidance.",
            options=[],
            allow_background=bool(data.get("allow_background")),
            thread_label=str(data.get("thread_label", "") or ""),
        )

    options = data.get("options", [])
    return _PromptPayload(
        question=question,
        options=options if isinstance(options, list) else [],
        allow_background=bool(data.get("allow_background")),
        thread_label=str(data.get("thread_label", "") or ""),
    )


def _format_header(phase_name: str, thread_label: str) -> str:
    header = phase_name or "interaction"
    if thread_label:
        return f"[{thread_label}] {header}"
    return header


def _display_prompt(prompt: str, *, phase_name: str, thread_label: str) -> _PromptPayload:
    payload = _parse_prompt(prompt)
    print(f"\n{_format_header(phase_name, payload.thread_label or thread_label)}")
    print(payload.question)
    if payload.options:
        print()
        for i, opt in enumerate(payload.options):
            print(f"  {i + 1}. {opt}")
    if payload.allow_background:
        print()
        print(_BACKGROUND_NOTE)
    return payload


def _ask_approve(prompt: str, *, phase_name: str, thread_label: str) -> bool | str | GateRejection:
    import questionary

    _display_prompt(prompt, phase_name=phase_name, thread_label=thread_label)
    print()
    choice = questionary.select(
        "",
        choices=[_APPROVE_APPROVE, _APPROVE_REJECT, _APPROVE_FEEDBACK],
    ).ask()
    if choice == _APPROVE_APPROVE:
        return True
    if choice == _APPROVE_REJECT:
        return GateRejection()
    return GateRejection(feedback=questionary.text("Feedback:").ask() or "")


def _ask_choose(prompt: str, options: list[str], *, phase_name: str, thread_label: str) -> str:
    import questionary

    _display_prompt(prompt, phase_name=phase_name, thread_label=thread_label)
    print()
    return questionary.select("", choices=options).ask()


def _ask_respond(prompt: str, *, phase_name: str, thread_label: str) -> str:
    import questionary

    payload = _display_prompt(prompt, phase_name=phase_name, thread_label=thread_label)
    print()
    response = questionary.text("").ask()
    if payload.allow_background and (response or "").strip() == _BACKGROUND_COMMAND:
        return BACKGROUND_RESPONSE
    return response


def _pending_from_task(
    task: Ask,
    *,
    feature_id: str,
    phase_name: str,
    kind_hint: str | None = None,
    options_hint: list[str] | None = None,
) -> Pending:
    kind = kind_hint or "respond"
    options: list[str] | None = list(options_hint) if options_hint else None
    if kind == "approve" and options is None:
        options = ["Approve", "Reject", "Give feedback"]
    task_input = getattr(task, "input", None)
    task_options = getattr(task_input, "options", None)
    if task_options is not None and not options:
        task_options = list(task_options)
        if task_options == ["Approve", "Reject", "Give feedback"]:
            kind = "approve"
        else:
            kind = "choose"
            options = task_options
    elif isinstance(task_input, Confirm) and kind_hint is None:
        kind = "approve"
        options = ["Approve", "Reject"]
    return Pending(
        id=f"cli-{feature_id}-{phase_name}",
        feature_id=feature_id,
        phase_name=phase_name,
        kind=kind,
        prompt=task.prompt,
        options=options,
        created_at=datetime.now(),
    )


class ThreadAwareTerminalInteractionRuntime(InteractionRuntime):
    name = "terminal"

    def __init__(self, *, thread_label: str = "") -> None:
        self._thread_label = thread_label

    def make_thread_runtime(
        self,
        *,
        feature_id: str,
        channel: str = "",
        thread_ts: str = "",
        persist_turns: bool = False,
        agent_runtime: Any = None,
        label: str | None = None,
        thread_id: str | None = None,
    ) -> ThreadAwareTerminalInteractionRuntime:
        del feature_id, channel, thread_ts, persist_turns, agent_runtime
        thread_label = label or thread_id or self._thread_label
        return ThreadAwareTerminalInteractionRuntime(thread_label=thread_label)

    async def ask(self, task: Ask, **kwargs: Any) -> str | bool | GateRejection:
        pending = _pending_from_task(
            task,
            feature_id=str(kwargs.get("feature_id", "") or ""),
            phase_name=str(kwargs.get("phase_name", "") or ""),
            kind_hint=str(kwargs.get("kind", "") or "") or None,
            options_hint=list(kwargs.get("options", []) or []),
        )
        return await self.resolve(pending)

    async def notify(
        self,
        *,
        feature_id: str,
        phase_name: str,
        message: str,
    ) -> None:
        del feature_id
        header = _format_header(phase_name or "notification", self._thread_label)
        print(f"\n{header}")
        print(message)

    async def resolve(self, pending: Pending) -> str | bool | GateRejection:
        if pending.kind == "approve":
            return await asyncio.to_thread(
                _ask_approve,
                pending.prompt,
                phase_name=pending.phase_name,
                thread_label=self._thread_label,
            )
        if pending.kind == "choose":
            options = pending.options or []
            return await asyncio.to_thread(
                _ask_choose,
                pending.prompt,
                options,
                phase_name=pending.phase_name,
                thread_label=self._thread_label,
            )
        return await asyncio.to_thread(
            _ask_respond,
            pending.prompt,
            phase_name=pending.phase_name,
            thread_label=self._thread_label,
        )
