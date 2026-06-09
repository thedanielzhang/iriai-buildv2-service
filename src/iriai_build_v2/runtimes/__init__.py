from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from iriai_compose.runner import AgentRuntime
    from iriai_compose.storage import SessionStore

AgentRuntimeName = Literal["claude", "codex", "claude_pool", "agent_pool"]
SUPPORTED_AGENT_RUNTIMES: tuple[AgentRuntimeName, ...] = (
    "claude",
    "codex",
    "claude_pool",
    "agent_pool",
)

_RUNTIME_ALIASES = {
    "anthropic": "claude",
    "claude": "claude",
    "claude-pool": "claude_pool",
    "claude_pool": "claude_pool",
    "openai": "codex",
    "codex": "codex",
    "pool": "claude_pool",
    "agent_pool": "agent_pool",
    "agent-pool": "agent_pool",
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


def secondary_agent_runtime_name(
    name: str | None = None,
    *,
    single_runtime: bool = False,
) -> AgentRuntimeName:
    """Return the secondary runtime paired with the selected primary runtime.

    Claude primary still pairs with Codex for adversarial review.
    Codex primary stays fully Codex-only by pairing with Codex again,
    so no Claude runtime is instantiated behind the scenes.
    """
    primary = normalize_agent_runtime(name)
    if single_runtime:
        return primary
    # agent_pool is a FLAT heterogeneous pool that already contains codex as a
    # co-equal member, so it is its own secondary (no separate Codex-as-secondary
    # runtime). Returning the same name makes secondary_alternation_enabled False
    # (primary == secondary), so planning fan-out never tags "secondary" and the
    # alternation path is bypassed -- in-pool selection handles distribution.
    if primary == "agent_pool":
        return "agent_pool"
    return "codex" if primary in {"claude", "claude_pool"} else primary


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

    if runtime_name in {"claude_pool", "agent_pool"}:
        # Both names resolve to the same ClaudePoolRuntime. It loads the
        # codex-inclusive profiles.json and only builds an embedded codex
        # runtime when a kind=="codex" member is configured; for a pure-claude
        # profiles.json it is byte-identical to claude_pool.
        from .claude_pool import ClaudePoolRuntime

        return ClaudePoolRuntime(
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
    "secondary_agent_runtime_name",
]
