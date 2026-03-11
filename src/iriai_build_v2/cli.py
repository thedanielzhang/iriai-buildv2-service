from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from .config import DATABASE_URL


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


async def _run(
    workflow_name: str,
    name: str,
    workspace: str,
    auto: bool,
    *,
    project: str = "",
    bug_report: str = "",
) -> None:
    import asyncpg

    from iriai_compose import (
        DefaultContextProvider,
        Feature,
        Workspace,
    )
    from iriai_compose.runtimes import AutoApproveRuntime, TerminalInteractionRuntime
    from .runtimes.claude import ClaudeAgentRuntime

    from .db import create_pool, ensure_schema
    from preview.api import PreviewClient

    from .services.reviews import ReviewSessionManager
    from .storage import PostgresArtifactStore, PostgresFeatureStore, PostgresSessionStore
    from .stream import print_stream
    from .tasks.feedback import FeedbackService
    from .tasks.playwright import PlaywrightService
    from .tasks.preview import PreviewService
    from .workflows import BugFixWorkflow, FullBuildWorkflow, PlanningWorkflow, TrackedWorkflowRunner
    from .models.state import BugFixState, BuildState

    # 1. Database
    pool = await create_pool(DATABASE_URL)
    await ensure_schema(pool)

    try:
        # 2. Stores
        artifacts = PostgresArtifactStore(pool)
        sessions = PostgresSessionStore(pool)
        feature_store = PostgresFeatureStore(pool)
        context_provider = DefaultContextProvider(artifacts=artifacts)

        # 3. Workspace
        workspace_path = Path(workspace).resolve()
        ws = Workspace(id="main", path=workspace_path)

        # 4. Runtimes
        interaction_runtime: TerminalInteractionRuntime | AutoApproveRuntime
        if auto:
            interaction_runtime = AutoApproveRuntime()
        else:
            interaction_runtime = TerminalInteractionRuntime()

        agent_runtime = ClaudeAgentRuntime(
            session_store=sessions,
            on_message=print_stream,
        )

        # 5. Services
        review_manager = ReviewSessionManager()
        feedback_service = FeedbackService(review_manager)
        preview_client = PreviewClient()
        preview_service = PreviewService(preview_client)
        playwright_service = PlaywrightService()
        await playwright_service.ensure_browsers()

        # 6. Runner
        runner = TrackedWorkflowRunner(
            feature_store=feature_store,
            agent_runtime=agent_runtime,
            interaction_runtimes={"terminal": interaction_runtime, "auto": interaction_runtime},
            artifacts=artifacts,
            sessions=sessions,
            context_provider=context_provider,
            workspaces={"main": ws},
            services={"feedback": feedback_service, "preview": preview_service, "playwright": playwright_service},
        )

        # 7. Feature
        feature_id = str(uuid.uuid4())[:8]
        slug = _slugify(name)
        feature = Feature(
            id=feature_id,
            name=name,
            slug=slug,
            workflow_name=workflow_name,
            workspace_id="main",
        )
        await feature_store.create(feature)

        # 8. Workflow
        if workflow_name == "planning":
            workflow = PlanningWorkflow()
        elif workflow_name == "bugfix":
            workflow = BugFixWorkflow()
        else:
            workflow = FullBuildWorkflow()

        state: BuildState | BugFixState
        if workflow_name == "bugfix":
            state = BugFixState(project=project, bug_report=bug_report)
            if bug_report:
                # Pre-load bug report artifact so intake phase can be skipped
                await artifacts.put("bug_report", bug_report, feature=feature)
        else:
            state = BuildState()

        # Store project context (workspace path for agents)
        await artifacts.put(
            "project",
            f"Project workspace: {workspace_path}\n\nFeature: {name}",
            feature=feature,
        )

        # 9. Execute
        print(f"\n{'='*60}")
        print(f"  iriai-build-v2 — {workflow_name}")
        print(f"  Feature: {name} ({feature_id})")
        print(f"  Workspace: {workspace_path}")
        print(f"{'='*60}\n")

        result = await runner.execute_workflow(workflow, feature, state)

        print(f"\n{'='*60}")
        print(f"  Workflow complete!")
        print(f"{'='*60}\n")

    finally:
        await playwright_service.close()
        await preview_service.close()
        await review_manager.stop_all()
        await pool.close()


@click.group()
def cli() -> None:
    """iriai-build-v2 — Agent orchestration build system."""
    pass


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
def plan(name: str, workspace: str, auto: bool) -> None:
    """Run the planning workflow (PM → Design → Architecture → Plan Review)."""
    asyncio.run(_run("planning", name, workspace, auto))


@cli.command()
@click.option("--name", required=True, help="Feature name")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
def build(name: str, workspace: str, auto: bool) -> None:
    """Run the full build workflow (Planning + Implementation)."""
    asyncio.run(_run("full-build", name, workspace, auto))


@cli.command()
@click.option("--name", required=True, help="Bug description (short)")
@click.option("--project", required=True, help="Project to deploy preview for")
@click.option("--workspace", default=".", help="Project workspace path")
@click.option("--auto", is_flag=True, help="Auto-approve all gates")
@click.option("--bug-report", "bug_report_path", default=None, type=click.Path(exists=True), help="Path to bug report JSON (skips intake interview)")
def bugfix(name: str, project: str, workspace: str, auto: bool, bug_report_path: str | None) -> None:
    """Run the bug fix workflow (Intake → Reproduce → Diagnose → Fix → Verify)."""
    bug_report = ""
    if bug_report_path:
        bug_report = Path(bug_report_path).read_text()
    asyncio.run(_run("bugfix", name, workspace, auto, project=project, bug_report=bug_report))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
