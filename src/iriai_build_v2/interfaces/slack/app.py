"""Slack bridge entry point — long-lived process connecting to Slack via Socket Mode."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Literal

from ...runtime_policy import DEFAULT_RUNTIME_POLICY, RuntimePolicy

logger = logging.getLogger(__name__)


async def run_slack_bridge(
    *,
    planning_channel: str,
    workspace: str | None = None,
    mode: Literal["multiplayer", "singleplayer"] = "multiplayer",
    agent_runtime: str = "claude",
    agent_runtime_override: bool = False,
    runtime_policy: RuntimePolicy = DEFAULT_RUNTIME_POLICY,
    runtime_policy_override: bool = False,
    single_agent_runtime: bool = False,
    budget: bool = False,
    concurrency_max: int | None = None,
    autonomous_remainder: bool = False,
    slack_verbosity: Literal["normal", "quiet"] = "normal",
    ignored_mention_user_ids: set[str] | None = None,
) -> None:
    """Start the long-lived Slack bridge.

    Connects via Socket Mode (outbound WebSocket — no HTTP server needed).
    Routes [FEATURE] messages in #planning to create channels and run workflows.

    *workspace* is an optional default suggestion — the actual workspace is
    selected per-feature via a scoping card in the workflow channel.
    Runs until SIGINT or SIGTERM.
    """
    from .adapter import SlackAdapter
    from .interaction import SlackInteractionRuntime
    from .orchestrator import SlackWorkflowOrchestrator

    # 1. Validate required env vars (fail fast)
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")

    if not app_token:
        raise RuntimeError(
            "SLACK_APP_TOKEN environment variable is required. "
            "Get it from your Slack app's Socket Mode settings (xapp-...)."
        )
    if not bot_token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN environment variable is required. "
            "Get it from your Slack app's OAuth settings (xoxb-...)."
        )
    ignored_mentions = await _load_ignored_mention_user_ids(ignored_mention_user_ids)

    # 2. Create adapter
    adapter = SlackAdapter(
        app_token=app_token,
        bot_token=bot_token,
        planning_channel=planning_channel,
        mode=mode,
        ignored_mention_user_ids=ignored_mentions,
    )

    # 3. Create interaction runtime and orchestrator
    workspace_path = Path(workspace).resolve() if workspace else None
    interaction_runtime = SlackInteractionRuntime(adapter)
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter,
        interaction_runtime=interaction_runtime,
        workspace_path=workspace_path,
        agent_runtime_name=agent_runtime,
        agent_runtime_override=agent_runtime_override,
        runtime_policy=runtime_policy,
        runtime_policy_override=runtime_policy_override,
        single_agent_runtime=single_agent_runtime,
        budget=budget,
        concurrency_max=concurrency_max,
        autonomous_remainder=autonomous_remainder,
        slack_verbosity=slack_verbosity,
    )
    await orchestrator.start()

    # 4. Connect (auth test + Socket Mode start)
    await adapter.connect()

    # 5. Log successful connection
    print(f"\niriai-build-v2 Slack bridge")
    print(f"  Default mode: {mode}")
    print(f"  Agent runtime: {agent_runtime}")
    print(f"  Runtime policy: {runtime_policy}")
    print(f"  Concurrency max: {concurrency_max if concurrency_max is not None else 'unlimited'}")
    print(f"  Autonomous remainder: {autonomous_remainder}")
    print(f"  Slack verbosity: {slack_verbosity}")
    if ignored_mentions:
        print(f"  Ignored mentions: {', '.join(sorted(ignored_mentions))}")
    print(f"  Channel: {planning_channel}")
    print(f"  Bot: @{adapter.bot_user_id}")
    print(f"  Listening for [FEATURE] and [BUG] messages...\n")

    # 6. Keep alive until signal
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        # 7. Clean shutdown
        print("\nShutting down Slack bridge...")
        await orchestrator.shutdown()
        await adapter.disconnect()


async def _load_ignored_mention_user_ids(
    configured: set[str] | None = None,
) -> set[str]:
    """Load user ids that belong to other bots owning the same channel."""

    user_ids = set(configured or set())
    for env_name in (
        "IRIAI_SLACK_IGNORED_MENTION_USER_IDS",
        "SUPERVISOR_SLACK_BOT_USER_ID",
        "IRIAI_SUPERVISOR_SLACK_BOT_USER_ID",
    ):
        raw = os.environ.get(env_name, "")
        if raw:
            user_ids.update(_parse_user_id_list(raw))

    supervisor_token = os.environ.get("SUPERVISOR_SLACK_BOT_TOKEN", "")
    if supervisor_token and not (
        os.environ.get("SUPERVISOR_SLACK_BOT_USER_ID")
        or os.environ.get("IRIAI_SUPERVISOR_SLACK_BOT_USER_ID")
    ):
        try:
            from slack_sdk.web.async_client import AsyncWebClient

            auth = await AsyncWebClient(token=supervisor_token).auth_test()
            supervisor_user_id = auth.get("user_id")
            if supervisor_user_id:
                user_ids.add(str(supervisor_user_id))
        except Exception:
            logger.warning(
                "Could not resolve supervisor Slack bot user id; set "
                "SUPERVISOR_SLACK_BOT_USER_ID or IRIAI_SLACK_IGNORED_MENTION_USER_IDS "
                "to keep supervisor mentions out of the workflow bridge.",
                exc_info=True,
            )
    return user_ids


def _parse_user_id_list(raw: str) -> set[str]:
    return {part.strip() for part in raw.replace(",", " ").split() if part.strip()}
