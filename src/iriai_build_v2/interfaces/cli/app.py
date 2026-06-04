"""CLI entry point for iriai-build-v2."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

from ...runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    SUPPORTED_RUNTIME_POLICIES,
    normalize_runtime_policy,
)

load_dotenv()


async def _run(
    workflow_name: str,
    name: str,
    workspace: str,
    auto: bool,
    *,
    agent_runtime: str = "claude",
    repos: list[str] | None = None,
    project: str = "",
    bug_report: str = "",
) -> None:
    from iriai_compose.runtimes import AutoApproveRuntime

    from ...execution_control.startup import (
        EnvFlagState,
        assert_control_plane_ready_for_workflow_launch,
        read_control_plane_env_flag,
    )
    from ...stream import print_stream
    from .interaction import ThreadAwareTerminalInteractionRuntime
    from .._bootstrap import (
        bootstrap,
        build_runner,
        build_state,
        create_feature,
        maybe_assert_adopted_or_legacy_for_resume,
        select_workflow,
        teardown,
    )

    workspace_path = Path(workspace).resolve()
    env = await bootstrap(workspace_path)

    try:
        # Slice 12c — IRIAI_EXEC_CONTROL_PLANE_ENABLED env-flag + startup
        # guard. The env flag is the SINGLE product-authoritative switch for
        # the typed execution control plane per doc 12 § "Atomic Landing
        # Contract". When the flag is unset/disabled the CLI continues with
        # the legacy executor (preserves backward compatibility during the
        # rollout). When enabled, the Slice-10f assert_control_plane_ready
        # fires BEFORE any workflow runs; missing dependencies / mismatched
        # deploy artifact / missing migrations / forbidden partial controls
        # raise ControlPlaneStartupError (NOT silent fallback to legacy).
        # Malformed env values raise ControlPlaneEnvFlagError from
        # read_control_plane_env_flag (fail closed; never silently default).
        flag_state = read_control_plane_env_flag()
        if flag_state is EnvFlagState.ENABLED:
            await assert_control_plane_ready_for_workflow_launch(
                pool=env.pool,
                require_enabled=True,
            )

        # Runtimes
        if auto:
            interaction_runtime = AutoApproveRuntime()
        else:
            interaction_runtime = ThreadAwareTerminalInteractionRuntime()

        runner = build_runner(
            env,
            interaction_runtimes={"terminal": interaction_runtime, "auto": interaction_runtime},
            on_message=print_stream,
            agent_runtime_name=agent_runtime,
        )

        # Feature
        feature = await create_feature(env.feature_store, name, workflow_name)

        # Slice 12e -- PR 11.13 final atomic landing: consult the Slice-12d
        # resume guard at the workflow seam. The CLI today always CREATES a
        # fresh feature in `_run` (no CLI resume path exists -- the only
        # production resume seam is Slack's `_resume_workflow` at
        # `interfaces/slack/orchestrator.py:925`), so `is_resume=False` is
        # the existing distinction. Under `IRIAI_EXEC_CONTROL_PLANE_ENABLED=
        # ENABLED` a fresh feature is implicitly under the new control plane
        # -- the adoption marker is only required at the in-flight RESUME
        # boundary per doc 12 § "In-Flight Cutover Policy" lines 73-78.
        # The helper centralizes the env-flag short-circuit + the
        # fresh-vs-resume distinction so the CLI and Slack seams stay in
        # lockstep.
        await maybe_assert_adopted_or_legacy_for_resume(
            feature=feature,
            artifacts=env.artifacts,
            is_resume=False,
        )

        # Workflow + state
        workflow = select_workflow(workflow_name)
        state = build_state(workflow_name, project=project, bug_report=bug_report)

        if workflow_name == "bugfix" and bug_report:
            await env.artifacts.put("bug_report", bug_report, feature=feature)

        if repos:
            from ...models.outputs import ProjectContext, RepoSpec

            repo_specs = []
            for r in repos:
                # Detect if it's a GitHub ref (contains /) or a local path
                if "/" in r and not Path(r).exists():
                    repo_specs.append(RepoSpec(
                        name=r.split("/")[-1],
                        github_url=r if r.startswith("http") else f"https://github.com/{r}",
                    ))
                else:
                    repo_specs.append(RepoSpec(
                        name=Path(r).name,
                        local_path=str(Path(r).resolve()),
                    ))

            project_ctx = ProjectContext(
                feature_name=name,
                repos=repo_specs,
                workspace_path=str(workspace_path),
            )
            await env.artifacts.put(
                "project",
                project_ctx.model_dump_json(indent=2),
                feature=feature,
            )
        else:
            await env.artifacts.put(
                "project",
                f"Project workspace: {workspace_path}\n\nFeature: {name}",
                feature=feature,
            )

        # Execute
        print(f"\n{'='*60}")
        print(f"  iriai-build-v2 — {workflow_name}")
        print(f"  Feature: {name} ({feature.id})")
        print(f"  Workspace: {workspace_path}")
        print(f"{'='*60}\n")

        await runner.execute_workflow(workflow, feature, state)

        print(f"\n{'='*60}")
        print(f"  Workflow complete!")
        print(f"{'='*60}\n")

    finally:
        await teardown(env)


@click.group()
def cli() -> None:
    """iriai-build-v2 — Agent orchestration build system."""
    pass


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--repo", multiple=True, help="GitHub repo (org/repo) or local path. Repeatable.")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option(
    "--agent-runtime",
    default=None,
    help="Agent runtime to use for workflow agents (claude, claude_pool, or codex).",
)
def plan(
    name: str,
    workspace: str,
    repo: tuple[str, ...],
    auto: bool,
    agent_runtime: str | None,
) -> None:
    """Run the planning workflow (Scoping → PM → Design → Architecture → Plan Review)."""
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    asyncio.run(
        _run(
            "planning",
            name,
            workspace,
            auto,
            agent_runtime=resolved_runtime,
            repos=list(repo) or None,
        )
    )


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--repo", multiple=True, help="GitHub repo (org/repo) or local path. Repeatable.")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option(
    "--agent-runtime",
    default=None,
    help="Agent runtime to use for workflow agents (claude, claude_pool, or codex).",
)
def develop(
    name: str,
    workspace: str,
    repo: tuple[str, ...],
    auto: bool,
    agent_runtime: str | None,
) -> None:
    """Run the full develop workflow (Planning + Implementation)."""
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    asyncio.run(
        _run(
            "full-develop",
            name,
            workspace,
            auto,
            agent_runtime=resolved_runtime,
            repos=list(repo) or None,
        )
    )


@cli.command()
@click.option("--name", required=True, help="Bug description (short)")
@click.option("--project", required=True, help="Project to deploy preview for")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option(
    "--bug-report",
    "bug_report_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to bug report JSON (skips intake interview)",
)
@click.option(
    "--agent-runtime",
    default=None,
    help="Agent runtime to use for workflow agents (claude, claude_pool, or codex).",
)
def bugfix(
    name: str,
    project: str,
    workspace: str,
    auto: bool,
    bug_report_path: str | None,
    agent_runtime: str | None,
) -> None:
    """Run the bug fix workflow (Intake → Reproduce → Diagnose → Fix → Verify)."""
    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    bug_report = ""
    if bug_report_path:
        bug_report = Path(bug_report_path).read_text()
    asyncio.run(
        _run(
            "bugfix",
            name,
            workspace,
            auto,
            agent_runtime=resolved_runtime,
            project=project,
            bug_report=bug_report,
        )
    )


@cli.command("slack")
@click.option("--channel", required=True, help="Slack planning channel ID")
@click.option(
    "--workspace",
    default=None,
    help="Default project workspace path (optional — user selects per-feature via card)",
)
@click.option(
    "--mode",
    type=click.Choice(["multiplayer", "singleplayer"]),
    default="multiplayer",
    help="multiplayer: bot responds only to @mentions. singleplayer: bot responds to all messages.",
)
@click.option(
    "--agent-runtime",
    default=None,
    help="Agent runtime to use for workflow agents (claude, claude_pool, or codex).",
)
@click.option(
    "--runtime-policy",
    type=click.Choice(list(SUPPORTED_RUNTIME_POLICIES)),
    default=DEFAULT_RUNTIME_POLICY,
    help="Runtime routing policy for workflow roles.",
)
@click.option(
    "--claude-pool-codex-review",
    is_flag=True,
    help=(
        "Use claude_pool primary plus Codex secondary for "
        "review/verification roles."
    ),
)
@click.option(
    "--claude-only",
    is_flag=True,
    help="When using Claude primary, also use Claude as the secondary runtime.",
)
@click.option(
    "--budget",
    is_flag=True,
    help="Use Sonnet for implementers, Opus for verifiers.",
)
@click.option(
    "--concurrency-max",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum active agent invocations across the Slack bridge.",
)
@click.option(
    "--autonomous-remainder",
    is_flag=True,
    help="Delegate later-phase human prompts (plan-review through implementation) to an agent.",
)
@click.option(
    "--slack-verbosity",
    type=click.Choice(["normal", "quiet"]),
    default="normal",
    show_default=True,
    help="Slack bridge message verbosity.",
)
@click.option(
    "--ignore-mention-user-id",
    "ignored_mention_user_ids",
    multiple=True,
    help=(
        "Slack user id whose mentions should be ignored by this bridge, "
        "for colocated bots such as the supervisor."
    ),
)
def slack_cmd(
    channel: str,
    workspace: str | None,
    mode: str,
    agent_runtime: str | None,
    runtime_policy: str,
    claude_pool_codex_review: bool,
    claude_only: bool,
    budget: bool,
    concurrency_max: int | None,
    autonomous_remainder: bool,
    slack_verbosity: str,
    ignored_mention_user_ids: tuple[str, ...],
) -> None:
    """Start the Slack bridge (long-lived process)."""
    import logging as _logging

    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    try:
        resolved_runtime_policy = normalize_runtime_policy(runtime_policy)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--runtime-policy") from exc

    runtime_policy_override = resolved_runtime_policy != DEFAULT_RUNTIME_POLICY
    if claude_pool_codex_review:
        if agent_runtime is not None and resolved_runtime != "claude_pool":
            raise click.BadParameter(
                "--claude-pool-codex-review cannot be combined with a "
                "non-Claude-pool --agent-runtime.",
                param_hint="--claude-pool-codex-review",
            )
        resolved_runtime = "claude_pool"
        resolved_runtime_policy = PRIMARY_IMPL_SECONDARY_REVIEW_POLICY
        runtime_policy_override = True

    if claude_only and resolved_runtime not in {"claude", "claude_pool"}:
        raise click.BadParameter(
            "--claude-only can only be used with Claude or Claude pool as the primary runtime.",
            param_hint="--claude-only",
        )

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from ..slack.app import run_slack_bridge

    asyncio.run(
        run_slack_bridge(
            planning_channel=channel,
            workspace=workspace,
            mode=mode,
            agent_runtime=resolved_runtime,
            agent_runtime_override=agent_runtime is not None or claude_pool_codex_review,
            runtime_policy=resolved_runtime_policy,
            runtime_policy_override=runtime_policy_override,
            single_agent_runtime=claude_only,
            budget=budget,
            concurrency_max=concurrency_max,
            autonomous_remainder=autonomous_remainder,
            slack_verbosity=slack_verbosity,
            ignored_mention_user_ids=set(ignored_mention_user_ids),
        )
    )


@cli.command("supervisor")
@click.option("--channel", required=True, help="Slack channel ID for the supervisor bot.")
@click.option("--feature", default=None, help="Feature ID the supervisor should focus on.")
@click.option("--dashboard-url", default=None, help="Dashboard URL for the supervised feature.")
@click.option(
    "--runtime",
    default="codex",
    show_default=True,
    help="Supervisor service runtime name.",
)
@click.option(
    "--mode",
    type=click.Choice(["multiplayer", "singleplayer"]),
    default="singleplayer",
    show_default=True,
    help="multiplayer: respond to @mentions. singleplayer: respond to all channel messages.",
)
@click.option(
    "--supervisor-mode",
    type=click.Choice(["read_only", "guarded"]),
    default="read_only",
    show_default=True,
    help="Supervisor action authority.",
)
@click.option(
    "--poll-interval",
    type=float,
    default=30.0,
    show_default=True,
    help="Seconds between supervisor evidence polls.",
)
@click.option(
    "--digest-interval",
    type=float,
    default=120.0,
    show_default=True,
    help="Minimum seconds between identical supervisor digests.",
)
@click.option(
    "--worktree-root",
    multiple=True,
    help="Repo/worktree root to probe for hygiene blockers. May be repeated.",
)
@click.option(
    "--forbidden-path",
    multiple=True,
    help="Manifest-forbidden path or prefix to probe in worktree roots. May be repeated.",
)
@click.option(
    "--app-token-env",
    default="SUPERVISOR_SLACK_APP_TOKEN",
    show_default=True,
    help="Environment variable containing the supervisor Socket Mode app token.",
)
@click.option(
    "--bot-token-env",
    default="SUPERVISOR_SLACK_BOT_TOKEN",
    show_default=True,
    help="Environment variable containing the supervisor bot token.",
)
def supervisor_cmd(
    channel: str,
    feature: str | None,
    dashboard_url: str | None,
    runtime: str,
    mode: str,
    supervisor_mode: str,
    poll_interval: float,
    digest_interval: float,
    worktree_root: tuple[str, ...],
    forbidden_path: tuple[str, ...],
    app_token_env: str,
    bot_token_env: str,
) -> None:
    """Start the supervisor Slack bot (long-lived process)."""
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from ...supervisor.slack import run_supervisor_slack_app

    asyncio.run(
        run_supervisor_slack_app(
            channel=channel,
            feature_id=feature,
            dashboard_url=dashboard_url,
            runtime=runtime,
            mode=mode,
            supervisor_mode=supervisor_mode,
            poll_interval_seconds=poll_interval,
            min_digest_interval_seconds=digest_interval,
            worktree_roots=list(worktree_root),
            forbidden_paths=list(forbidden_path),
            app_token_env=app_token_env,
            bot_token_env=bot_token_env,
        )
    )


@cli.group("claude-pool")
def claude_pool_cmd() -> None:
    """Manage the local Claude account pool runtime."""
    pass


@claude_pool_cmd.command("doctor")
@click.option(
    "--root",
    default=None,
    help="Claude pool root directory (defaults to /Users/Shared/iriai/claude-pool).",
)
@click.option(
    "--skip-health-checks",
    is_flag=True,
    help="Only inspect config and heartbeats; do not submit runner health jobs.",
)
@click.option("--timeout", default=60.0, help="Seconds to wait for each health-check job.")
def claude_pool_doctor(root: str | None, skip_health_checks: bool, timeout: float) -> None:
    """Check shared spool, runner heartbeats, and per-profile user context."""
    from ...runtimes.claude_pool import DEFAULT_POOL_ROOT, doctor

    root_path = Path(root) if root else DEFAULT_POOL_ROOT
    lines = asyncio.run(
        doctor(
            root=root_path,
            run_health_checks=not skip_health_checks,
            timeout=timeout,
        )
    )
    for line in lines:
        click.echo(line)


@claude_pool_cmd.command("install-launchagents")
@click.option(
    "--root",
    default=None,
    help="Claude pool root directory (defaults to /Users/Shared/iriai/claude-pool).",
)
@click.option(
    "--runner-command",
    default=None,
    help="Command used by LaunchAgents to start claude-pool-runner.",
)
def claude_pool_install_launchagents(root: str | None, runner_command: str | None) -> None:
    """Write LaunchAgent plist templates and print install commands."""
    from ...runtimes.claude_pool import DEFAULT_POOL_ROOT, install_launchagent_templates

    root_path = Path(root) if root else DEFAULT_POOL_ROOT
    for line in install_launchagent_templates(root=root_path, runner_command=runner_command):
        click.echo(line)


def main() -> None:
    from .e2e_cmd import register_e2e_commands

    register_e2e_commands(cli)
    cli()


if __name__ == "__main__":
    main()
