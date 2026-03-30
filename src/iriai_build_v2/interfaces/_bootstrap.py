"""Shared initialization for all interfaces (CLI, Slack, etc.)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import asyncpg
    from iriai_compose import DefaultContextProvider, Feature, Workspace
    from iriai_compose.runner import InteractionRuntime

    from ..services.artifacts import ArtifactMirror
    from ..services.reviews import ReviewSessionManager
    from ..services.workspace import WorkspaceManager
    from ..storage import PostgresArtifactStore, PostgresFeatureStore, PostgresSessionStore
    from ..tasks.feedback import FeedbackService
    from ..tasks.playwright import PlaywrightService
    from ..tasks.preview import PreviewService
    from ..workflows import TrackedWorkflowRunner


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


@dataclass
class BootstrappedEnv:
    pool: asyncpg.Pool
    artifacts: PostgresArtifactStore
    sessions: PostgresSessionStore
    feature_store: PostgresFeatureStore
    context_provider: DefaultContextProvider
    review_manager: ReviewSessionManager
    feedback_service: FeedbackService
    preview_service: PreviewService
    playwright_service: PlaywrightService
    artifact_mirror: ArtifactMirror
    workspace: Workspace | None
    workspace_path: Path | None
    workspace_manager: WorkspaceManager | None


async def bootstrap(workspace_path: Path | None = None) -> BootstrappedEnv:
    """Initialize database, stores, and services.

    When *workspace_path* is ``None`` (Slack bridge mode), the workspace is
    selected per-feature via a scoping card.  Artifact storage uses
    ``~/.iriai/artifacts/`` so it doesn't depend on a workspace.
    """
    import asyncpg  # noqa: F811

    from iriai_compose import DefaultContextProvider, Workspace
    from preview.api import PreviewClient

    from ..config import DATABASE_URL
    from ..db import create_pool, ensure_schema
    from ..services.artifacts import ArtifactMirror
    from ..services.reviews import ReviewSessionManager
    from ..storage import PostgresArtifactStore, PostgresFeatureStore, PostgresSessionStore
    from ..tasks.feedback import FeedbackService
    from ..tasks.playwright import PlaywrightService
    from ..tasks.preview import PreviewService

    pool = await create_pool(DATABASE_URL)
    await ensure_schema(pool)

    artifacts = PostgresArtifactStore(pool)
    sessions = PostgresSessionStore(pool)
    feature_store = PostgresFeatureStore(pool)
    context_provider = DefaultContextProvider(artifacts=artifacts)

    ws = Workspace(id="main", path=workspace_path) if workspace_path else None

    review_manager = ReviewSessionManager()
    feedback_service = FeedbackService(review_manager)
    preview_client = PreviewClient()
    preview_service = PreviewService(preview_client)
    playwright_service = PlaywrightService()
    await playwright_service.ensure_browsers()

    artifact_dir = (
        workspace_path / ".iriai" / "artifacts"
        if workspace_path
        else Path.home() / ".iriai" / "artifacts"
    )
    artifact_mirror = ArtifactMirror(artifact_dir)

    from ..services.workspace import WorkspaceManager

    workspace_manager = (
        WorkspaceManager(base_path=workspace_path)
        if workspace_path
        else None
    )

    return BootstrappedEnv(
        pool=pool,
        artifacts=artifacts,
        sessions=sessions,
        feature_store=feature_store,
        context_provider=context_provider,
        review_manager=review_manager,
        feedback_service=feedback_service,
        preview_service=preview_service,
        playwright_service=playwright_service,
        artifact_mirror=artifact_mirror,
        workspace=ws,
        workspace_path=workspace_path,
        workspace_manager=workspace_manager,
    )


async def teardown(env: BootstrappedEnv) -> None:
    """Clean up services and connections."""
    await env.playwright_service.close()
    await env.preview_service.close()
    await env.review_manager.stop_all()
    await env.pool.close()


def build_runner(
    env: BootstrappedEnv,
    *,
    interaction_runtimes: dict[str, Any],
    on_message: Callable[..., Any] | None = None,
    agent_runtime_name: str = "claude",
) -> TrackedWorkflowRunner:
    """Construct a TrackedWorkflowRunner with the given interaction runtimes.

    Creates both a primary and secondary agent runtime for adversarial
    multi-model execution.  The primary is determined by *agent_runtime_name*;
    the secondary is the other supported runtime.
    """
    from ..runtimes import create_agent_runtime
    from ..workflows import TrackedWorkflowRunner

    agent_runtime = create_agent_runtime(
        agent_runtime_name,
        session_store=env.sessions,
        on_message=on_message,
    )

    # Secondary runtime: the other one (for adversarial execution)
    secondary_name = "codex" if agent_runtime_name != "codex" else "claude"
    secondary_runtime = create_agent_runtime(
        secondary_name,
        session_store=env.sessions,
        on_message=on_message,
    )

    return TrackedWorkflowRunner(
        feature_store=env.feature_store,
        agent_runtime=agent_runtime,
        secondary_runtime=secondary_runtime,
        interaction_runtimes=interaction_runtimes,
        artifacts=env.artifacts,
        sessions=env.sessions,
        context_provider=env.context_provider,
        workspaces={"main": env.workspace},
        services={
            "feedback": env.feedback_service,
            "preview": env.preview_service,
            "playwright": env.playwright_service,
            "artifact_mirror": env.artifact_mirror,
            "workspace_manager": env.workspace_manager,
        },
    )


async def create_feature(
    feature_store: PostgresFeatureStore,
    name: str,
    workflow_name: str,
) -> Feature:
    """Create a feature with retry on unique constraint collision."""
    import asyncpg as apg

    from iriai_compose import Feature

    for _attempt in range(5):
        feature_id = str(uuid.uuid4())[:8]
        slug = f"{slugify(name)}-{feature_id}"
        feature = Feature(
            id=feature_id,
            name=name,
            slug=slug,
            workflow_name=workflow_name,
            workspace_id="main",
        )
        try:
            await feature_store.create(feature)
            return feature
        except apg.UniqueViolationError:
            continue

    raise RuntimeError(
        "Failed to create feature after 5 attempts due to ID collisions. "
        "This is extremely unlikely — check the database for stale entries."
    )


async def rebuild_state(
    workflow_name: str,
    artifacts: PostgresArtifactStore,
    feature: Feature,
) -> BuildState | BugFixState:
    """Reconstruct workflow state from persisted artifacts for resume."""
    from ..models.state import BugFixState, BuildState

    if workflow_name == "bugfix":
        state = BugFixState()
        mapping = {
            "bug_report": "bug_report",
            "reproduction": "reproduction",
            "baseline": "baseline",
            "root_cause_a": "root_cause_a",
            "root_cause_b": "root_cause_b",
            "fix": "fix",
            "verification": "verification",
            "regression": "regression",
            "project": "project",
        }
    else:
        state = BuildState()
        mapping = {
            "scope": "scope",
            "prd": "prd",
            "design": "design",
            "plan": "plan",
            "system-design": "system_design",
            "dag": "dag",
            "implementation": "implementation",
        }

    for artifact_key, field_name in mapping.items():
        val = await artifacts.get(artifact_key, feature=feature)
        if val:
            setattr(state, field_name, val)

    return state


def select_workflow(workflow_name: str):
    """Return the appropriate workflow instance."""
    from ..workflows import BugFixWorkflow, FullDevelopWorkflow, PlanningWorkflow

    if workflow_name == "planning":
        return PlanningWorkflow()
    elif workflow_name == "bugfix":
        return BugFixWorkflow()
    else:
        return FullDevelopWorkflow()


def build_state(
    workflow_name: str,
    *,
    project: str = "",
    bug_report: str = "",
):
    """Construct the initial state for the given workflow."""
    from ..models.state import BugFixState, BuildState

    if workflow_name == "bugfix":
        return BugFixState(project=project, bug_report=bug_report)
    return BuildState()
