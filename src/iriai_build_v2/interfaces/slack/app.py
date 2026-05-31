"""Slack bridge entry point — long-lived process connecting to Slack via Socket Mode."""

from __future__ import annotations

import asyncio
import faulthandler
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
    # Install an early SIGUSR1 guard BEFORE the multi-second startup
    # (control-plane checks, orchestrator.start, adapter.connect / Slack auth).
    # SIGUSR1 is the operator-free resume trigger (the dashboard sends it via
    # os.kill); until the real async handler is installed at the bottom of this
    # function, SIGUSR1's default disposition is 'terminate', so a resume that
    # races startup would KILL the bridge before it ever serves work (observed:
    # restart + immediate resume -> exit code -30). Record an early signal in a
    # flag and honor it once the real handler is live, so a racing resume is
    # queued rather than fatal.
    _early_resume_pending = {"flag": False}
    _early_resume_signal = getattr(signal, "SIGUSR1", None)
    if _early_resume_signal is not None:
        def _early_resume_guard(_signum, _frame) -> None:
            _early_resume_pending["flag"] = True

        try:
            signal.signal(_early_resume_signal, _early_resume_guard)
        except (ValueError, OSError):
            # Not the main thread / unsupported platform — the real async handler
            # installed below still covers the common (post-startup) case.
            pass

    from ...execution_control.startup import (
        EnvFlagState,
        assert_control_plane_ready_for_workflow_launch,
        read_control_plane_env_flag,
    )
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

    # Slice 12c — IRIAI_EXEC_CONTROL_PLANE_ENABLED env-flag + startup guard.
    # The Slack bridge is a long-lived process that admits new workflow
    # starts; the env flag must be evaluated ONCE at process startup. When
    # the flag is unset/disabled, the bridge runs on the legacy executor
    # (backward-compat during rollout). When enabled, the Slice-10f
    # assert_control_plane_ready fires BEFORE the bridge connects — a
    # malformed flag, missing component, or forbidden partial control
    # refuses startup (NOT silent fallback). Per-feature control-plane
    # state (deploy_artifact_commit / candidate_commit / required
    # migrations) is supplied by the Slice-12d adoption record + Slice-12e
    # final-landing wiring; Slice 12c lands the env-flag + outermost guard.
    flag_state = read_control_plane_env_flag()
    if flag_state is EnvFlagState.ENABLED:
        await assert_control_plane_ready_for_workflow_launch(
            require_enabled=True,
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
    resume_signal = getattr(signal, "SIGUSR1", None)
    if resume_signal is not None:
        def _request_recoverable_resume() -> None:
            async def _resume() -> None:
                try:
                    result = await orchestrator.trigger_recoverable_resumes(
                        trigger="signal:SIGUSR1",
                    )
                    logger.info("Recoverable resume signal handled: %s", result)
                except Exception:
                    logger.exception("Recoverable resume signal failed")

            asyncio.create_task(_resume())

        loop.add_signal_handler(resume_signal, _request_recoverable_resume)
        # Honor a resume SIGUSR1 that arrived during startup (caught by the early
        # guard above, before this real handler existed) now that we can act on
        # it — the operator-free resume must not be silently dropped just because
        # it raced the bridge coming up.
        if _early_resume_pending["flag"]:
            logger.info("Honoring SIGUSR1 resume that arrived during startup")
            _request_recoverable_resume()

    # Hang diagnosis: SIGUSR2 dumps every thread's Python stack to a file. This
    # is a C-level faulthandler (not an asyncio callback), so it fires even when
    # the event loop is frozen in an uncancellable await — the one case where a
    # native `sample` shows only `kevent` and py-spy needs root. `chain=False`
    # keeps the default terminate action from running, so the process survives
    # the dump. Trigger with `kill -USR2 <bridge_pid>`.
    dump_signal = getattr(signal, "SIGUSR2", None)
    if dump_signal is not None:
        _fault_path = os.environ.get(
            "IRIAI_FAULTHANDLER_PATH", "/tmp/iriai_bridge_faulthandler.log"
        )
        try:
            _fault_file = open(_fault_path, "a", buffering=1)  # noqa: SIM115 - kept open for process lifetime
            faulthandler.register(
                dump_signal, file=_fault_file, all_threads=True, chain=False
            )
            logger.info(
                "Stack-dump handler armed: kill -USR2 <pid> -> %s", _fault_path
            )
        except Exception:
            logger.exception("Failed to arm SIGUSR2 stack-dump handler")

    # asyncio TASK-stack dump — shows SUSPENDED coroutines (the orphaned dispatch
    # await), which faulthandler's thread-only dump cannot. kill -INFO <pid>.
    task_dump_signal = getattr(signal, "SIGINFO", None)
    if task_dump_signal is not None:
        _tasks_path = os.environ.get("IRIAI_TASKDUMP_PATH", "/tmp/iriai_bridge_tasks.log")

        def _dump_asyncio_tasks() -> None:
            import io

            buf = io.StringIO()
            try:
                tasks = asyncio.all_tasks(loop)
                buf.write(f"\n===== asyncio task dump ({len(tasks)} tasks) =====\n")
                for t in tasks:
                    buf.write(f"\n--- Task {t.get_name()} done={t.done()} ---\n")
                    try:
                        t.print_stack(file=buf)
                    except Exception as exc:  # pragma: no cover - best-effort diag
                        buf.write(f"(print_stack failed: {exc})\n")
                    # print_stack collapses nested coroutine awaits (observed: it
                    # shows only the OUTERMOST frame for a deeply-suspended
                    # resume_workflow, hiding the exact orphaned await). Walk the
                    # cr_await chain by hand to surface every frame down to the
                    # bare Future the coroutine is parked on.
                    try:
                        buf.write("  [await chain]\n")
                        node = t.get_coro()
                        seen: set[int] = set()
                        depth = 0
                        while node is not None and id(node) not in seen and depth < 300:
                            seen.add(id(node))
                            depth += 1
                            frame = getattr(node, "cr_frame", None) or getattr(
                                node, "gi_frame", None
                            )
                            if frame is not None:
                                code = frame.f_code
                                buf.write(
                                    f"    {code.co_filename}:{frame.f_lineno} "
                                    f"in {code.co_name}\n"
                                )
                            nxt = getattr(node, "cr_await", None)
                            if nxt is None:
                                nxt = getattr(node, "gi_yieldfrom", None)
                            if nxt is not None and not (
                                hasattr(nxt, "cr_frame") or hasattr(nxt, "gi_frame")
                            ):
                                buf.write(f"    -> awaiting non-coro: {nxt!r}\n")
                                nxt = None
                            node = nxt
                    except Exception as exc:  # pragma: no cover - best-effort diag
                        buf.write(f"  (await-chain walk failed: {exc})\n")
            except Exception as exc:  # pragma: no cover - best-effort diag
                buf.write(f"(all_tasks failed: {exc})\n")
            try:
                with open(_tasks_path, "a") as fh:
                    fh.write(buf.getvalue())
            except Exception:
                logger.exception("Failed to write asyncio task dump")

        try:
            loop.add_signal_handler(task_dump_signal, _dump_asyncio_tasks)
            logger.info("Task-dump handler armed: kill -INFO <pid> -> %s", _tasks_path)
        except Exception:
            logger.exception("Failed to arm SIGINFO task-dump handler")

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
