"""CLI entry point for iriai-build-v2."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()


async def _run(
    workflow_name: str,
    name: str,
    workspace: str,
    auto: bool,
    *,
    repos: list[str] | None = None,
    project: str = "",
    bug_report: str = "",
) -> None:
    from iriai_compose.runtimes import AutoApproveRuntime, TerminalInteractionRuntime

    from ...stream import print_stream
    from .._bootstrap import (
        bootstrap,
        build_runner,
        build_state,
        create_feature,
        select_workflow,
        teardown,
    )

    workspace_path = Path(workspace).resolve()
    env = await bootstrap(workspace_path)

    try:
        # Runtimes
        if auto:
            interaction_runtime = AutoApproveRuntime()
        else:
            interaction_runtime = TerminalInteractionRuntime()

        runner = build_runner(
            env,
            interaction_runtimes={"terminal": interaction_runtime, "auto": interaction_runtime},
            on_message=print_stream,
        )

        # Feature
        feature = await create_feature(env.feature_store, name, workflow_name)

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
def plan(name: str, workspace: str, repo: tuple[str, ...], auto: bool) -> None:
    """Run the planning workflow (Scoping → PM → Design → Architecture → Plan Review)."""
    asyncio.run(_run("planning", name, workspace, auto, repos=list(repo) or None))


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--repo", multiple=True, help="GitHub repo (org/repo) or local path. Repeatable.")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
def develop(name: str, workspace: str, repo: tuple[str, ...], auto: bool) -> None:
    """Run the full develop workflow (Planning + Implementation)."""
    asyncio.run(_run("full-develop", name, workspace, auto, repos=list(repo) or None))


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
def bugfix(
    name: str, project: str, workspace: str, auto: bool, bug_report_path: str | None
) -> None:
    """Run the bug fix workflow (Intake → Reproduce → Diagnose → Fix → Verify)."""
    bug_report = ""
    if bug_report_path:
        bug_report = Path(bug_report_path).read_text()
    asyncio.run(
        _run("bugfix", name, workspace, auto, project=project, bug_report=bug_report)
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
    help="Agent runtime to use for workflow agents (claude or codex).",
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
def slack_cmd(
    channel: str,
    workspace: str | None,
    mode: str,
    agent_runtime: str | None,
    claude_only: bool,
    budget: bool,
) -> None:
    """Start the Slack bridge (long-lived process)."""
    import logging as _logging

    from ...runtimes import normalize_agent_runtime

    try:
        resolved_runtime = normalize_agent_runtime(agent_runtime)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--agent-runtime") from exc
    if claude_only and resolved_runtime != "claude":
        raise click.BadParameter(
            "--claude-only can only be used with Claude as the primary runtime.",
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
            agent_runtime_override=agent_runtime is not None,
            single_agent_runtime=claude_only,
            budget=budget,
        )
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
