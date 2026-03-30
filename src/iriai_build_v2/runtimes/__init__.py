from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from iriai_compose.runner import AgentRuntime
    from iriai_compose.storage import SessionStore

AgentRuntimeName = Literal["claude", "codex"]
SUPPORTED_AGENT_RUNTIMES: tuple[AgentRuntimeName, ...] = ("claude", "codex")

_RUNTIME_ALIASES = {
    "anthropic": "claude",
    "claude": "claude",
    "openai": "codex",
    "codex": "codex",
}


def normalize_agent_runtime(name: str | None = None) -> AgentRuntimeName:
    raw = (name or os.environ.get("IRIAI_AGENT_RUNTIME") or "claude").strip().lower()
    resolved = _RUNTIME_ALIASES.get(raw)
    if resolved is None:
        supported = ", ".join(SUPPORTED_AGENT_RUNTIMES)
        raise ValueError(
            f"Unsupported agent runtime '{raw}'. Supported values: {supported}"
        )
    return cast(AgentRuntimeName, resolved)


def create_agent_runtime(
    name: str | None,
    *,
    session_store: SessionStore | None = None,
    on_message: Callable[..., None] | None = None,
    interactive_roles: set[str] | None = None,
) -> AgentRuntime:
    runtime_name = normalize_agent_runtime(name)
    if runtime_name == "claude":
        from .claude import ClaudeAgentRuntime

        return ClaudeAgentRuntime(
            session_store=session_store,
            on_message=on_message,
            interactive_roles=interactive_roles,
        )

    from .codex import CodexAgentRuntime

    return CodexAgentRuntime(
        session_store=session_store,
        on_message=on_message,
        interactive_roles=interactive_roles,
    )


__all__ = [
    "AgentRuntimeName",
    "SUPPORTED_AGENT_RUNTIMES",
    "create_agent_runtime",
    "normalize_agent_runtime",
]
