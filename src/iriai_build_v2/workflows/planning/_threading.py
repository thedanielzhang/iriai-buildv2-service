from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iriai_compose import AgentActor

from ...interfaces.cli.interaction import ThreadAwareTerminalInteractionRuntime
from ...runtimes import create_agent_runtime
from ...stream import make_thread_stream


@dataclass
class PlanningThreadHandle:
    thread_id: str
    label: str
    resolver: str
    thread_ts: str = ""
    primary_runtime: Any | None = None
    secondary_runtime: Any | None = None


def _cache_key(feature_id: str, thread_id: str) -> str:
    return f"{feature_id}:{thread_id}"


async def ensure_planning_thread(
    runner: Any,
    feature: Any,
    *,
    thread_id: str,
    label: str,
    existing_thread_ts: str = "",
) -> PlanningThreadHandle:
    services = getattr(runner, "services", None)
    if not isinstance(services, dict):
        return PlanningThreadHandle(thread_id=thread_id, label=label, resolver="terminal")

    key = _cache_key(feature.id, thread_id)
    cache: dict[str, PlanningThreadHandle] = services.setdefault("planning_thread_handles", {})
    cached = cache.get(key)
    if cached:
        return cached

    resolver = f"terminal.thread.{thread_id}"
    thread_ts = existing_thread_ts
    root_runtime = runner.interaction_runtimes.get("terminal")

    primary_runtime = None
    secondary_runtime = None
    adapter = services.get("slack_adapter")
    channel_id = str(getattr(feature, "metadata", {}).get("channel_id", "") or "")
    session_store = getattr(runner, "sessions", None)

    if session_store is not None:
        if adapter and channel_id:
            from ...interfaces.slack.streamer import make_slack_on_message

            if not thread_ts:
                thread_ts = await adapter.post_message(
                    channel_id,
                    f"*Planning Thread*\n{label}",
                )
            primary_runtime = create_agent_runtime(
                getattr(runner.agent_runtime, "name", "claude"),
                session_store=session_store,
                on_message=make_slack_on_message(adapter, channel_id, thread_ts),
                interactive_roles=getattr(runner.agent_runtime, "_interactive_roles", None),
            )
            secondary_runtime = create_agent_runtime(
                getattr(runner.secondary_runtime, "name", getattr(runner.agent_runtime, "name", "claude")),
                session_store=session_store,
                on_message=make_slack_on_message(adapter, channel_id, thread_ts),
                interactive_roles=getattr(runner.secondary_runtime, "_interactive_roles", None),
            )
        else:
            primary_runtime = create_agent_runtime(
                getattr(runner.agent_runtime, "name", "claude"),
                session_store=session_store,
                on_message=make_thread_stream(label),
                interactive_roles=getattr(runner.agent_runtime, "_interactive_roles", None),
            )
            secondary_runtime = create_agent_runtime(
                getattr(runner.secondary_runtime, "name", getattr(runner.agent_runtime, "name", "claude")),
                session_store=session_store,
                on_message=make_thread_stream(label),
                interactive_roles=getattr(runner.secondary_runtime, "_interactive_roles", None),
            )

    if root_runtime and hasattr(root_runtime, "make_thread_runtime"):
        if isinstance(root_runtime, ThreadAwareTerminalInteractionRuntime):
            runner.interaction_runtimes[resolver] = root_runtime.make_thread_runtime(
                feature_id=feature.id,
                label=label,
                thread_id=thread_id,
            )
        else:
            runner.interaction_runtimes[resolver] = root_runtime.make_thread_runtime(
                feature_id=feature.id,
                channel=channel_id,
                thread_ts=thread_ts,
                persist_turns=True,
                agent_runtime=primary_runtime,
            )

    handle = PlanningThreadHandle(
        thread_id=thread_id,
        label=label,
        resolver=resolver,
        thread_ts=thread_ts,
        primary_runtime=primary_runtime,
        secondary_runtime=secondary_runtime,
    )
    cache[key] = handle
    return handle


def make_thread_user(base_user: Any, *, resolver: str) -> Any:
    return base_user.model_copy(update={"resolver": resolver})


def make_thread_actor(
    base: AgentActor,
    *,
    handle: PlanningThreadHandle,
    suffix: str,
    runtime: str = "primary",
    context_keys: list[str] | None = None,
) -> AgentActor:
    actor = base.model_copy(update={"name": f"{base.name}-{handle.thread_id}-{suffix}"})
    metadata = dict(actor.role.metadata)
    runtime_instance = handle.secondary_runtime if runtime == "secondary" else handle.primary_runtime
    if runtime_instance is not None:
        metadata["runtime_instance"] = runtime_instance
    role = actor.role.model_copy(update={"metadata": metadata})
    updates: dict[str, Any] = {"role": role}
    if context_keys is not None:
        updates["context_keys"] = context_keys
    return actor.model_copy(update=updates)


def write_thread_file(
    runner: Any,
    feature: Any,
    *,
    thread_id: str,
    file_name: str,
    content: str,
) -> str:
    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        raise RuntimeError("Artifact mirror required for planning thread context files")
    safe_thread = thread_id.replace(":", "-").replace("/", "-")
    path = Path(mirror.feature_dir(feature.id)) / "threads" / safe_thread / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_thread_context_file(
    runner: Any,
    feature: Any,
    *,
    thread_id: str,
    step: str,
    content: str,
) -> str:
    return write_thread_file(
        runner,
        feature,
        thread_id=thread_id,
        file_name=f"{step}-context.md",
        content=content,
    )


def build_agent_fill_prompt(*, label: str, response_text: str) -> str:
    return (
        f"You are the shadow stakeholder for the planning thread '{label}'. "
        "The human chose to finish this step in the background. Answer the planner's "
        "latest question as a decisive, high-fidelity stakeholder grounded in the "
        "existing code, artifacts, and current feature context. Make reasonable "
        "assumptions when needed, and state them directly in your answer.\n\n"
        f"Planner prompt:\n{response_text}"
    )
