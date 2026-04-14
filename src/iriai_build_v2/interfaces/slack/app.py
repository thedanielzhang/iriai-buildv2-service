"""Slack bridge entry point — long-lived process connecting to Slack via Socket Mode."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


async def run_slack_bridge(
    *,
    planning_channel: str,
    workspace: str | None = None,
    mode: Literal["multiplayer", "singleplayer"] = "multiplayer",
    agent_runtime: str = "claude",
    agent_runtime_override: bool = False,
    single_agent_runtime: bool = False,
    budget: bool = False,
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

    # 2. Create adapter
    adapter = SlackAdapter(
        app_token=app_token,
        bot_token=bot_token,
        planning_channel=planning_channel,
        mode=mode,
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
        single_agent_runtime=single_agent_runtime,
        budget=budget,
    )
    await orchestrator.start()

    # 4. Connect (auth test + Socket Mode start)
    await adapter.connect()

    # 5. Log successful connection
    print(f"\niriai-build-v2 Slack bridge")
    print(f"  Default mode: {mode}")
    print(f"  Agent runtime: {agent_runtime}")
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
