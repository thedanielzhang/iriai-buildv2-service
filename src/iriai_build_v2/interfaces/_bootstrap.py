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
    from ..public_dashboard import PublicDashboardOutbox
    from ..storage import PostgresArtifactStore, PostgresFeatureStore, PostgresSessionStore
    from ..tasks.feedback import FeedbackService
    from ..tasks.playwright import PlaywrightService
    from ..tasks.preview import PreviewService
    from ..models.state import BugFixState, BuildState
from ..workflows import TrackedWorkflowRunner
from ..runtime_policy import DEFAULT_RUNTIME_POLICY, RuntimePolicy, normalize_runtime_policy


# Bound the human-readable slug portion so `{slug}-{id}` stays a valid path
# component (filesystem limit ~255B) and Slack channel name (≤80 chars). The
# id suffix carries uniqueness, so truncating the name portion is safe.
_FEATURE_SLUG_NAME_MAX = 80
_SLACK_CHANNEL_SLUG_MAX = 50


def slugify(name: str, max_length: int | None = None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if max_length is not None and len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
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
    public_dashboard: PublicDashboardOutbox
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
    from ..public_dashboard import PublicDashboardOutbox
    from ..services.artifacts import ArtifactMirror
    from ..services.reviews import ReviewSessionManager
    from ..storage import PostgresArtifactStore, PostgresFeatureStore, PostgresSessionStore
    from ..tasks.feedback import FeedbackService
    from ..tasks.playwright import PlaywrightService
    from ..tasks.preview import PreviewService

    pool = await create_pool(DATABASE_URL)
    await ensure_schema(pool)

    public_dashboard = PublicDashboardOutbox(pool)
    artifacts = PostgresArtifactStore(pool, public_dashboard=public_dashboard)
    sessions = PostgresSessionStore(pool)
    feature_store = PostgresFeatureStore(pool, public_dashboard=public_dashboard)
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
        public_dashboard=public_dashboard,
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
    runtime_policy: RuntimePolicy = DEFAULT_RUNTIME_POLICY,
    single_agent_runtime: bool = False,
) -> TrackedWorkflowRunner:
    """Construct a TrackedWorkflowRunner with the given interaction runtimes.

    Creates both a primary and secondary agent runtime. Claude primary still
    pairs with Codex for adversarial review; Codex primary pairs with Codex
    again so no Claude runtime is instantiated behind the scenes. When
    ``single_agent_runtime`` is true, the secondary runtime matches the
    primary runtime exactly.
    """
    from ..runtimes import (
        create_agent_runtime,
        normalize_agent_runtime,
        secondary_agent_runtime_name,
    )
    from ..workflows import TrackedWorkflowRunner

    agent_runtime = create_agent_runtime(
        agent_runtime_name,
        session_store=env.sessions,
        on_message=on_message,
    )

    # Secondary runtime: Codex for Claude-primary runs, Codex again for
    # Codex-primary runs so "codex" means fully Codex-only.
    secondary_name = secondary_agent_runtime_name(
        agent_runtime_name,
        single_runtime=single_agent_runtime,
    )
    if normalize_agent_runtime(agent_runtime_name) == "agent_pool":
        # agent_pool is a single FLAT pool whose codex member is co-equal with
        # the claude accounts. Reuse the SAME pool instance as the secondary so
        # the in-memory codex-load counter, profile affinity, and embedded codex
        # runtime are not duplicated across two pool objects.
        secondary_runtime = agent_runtime
    else:
        secondary_runtime = create_agent_runtime(
            secondary_name,
            session_store=env.sessions,
            on_message=on_message,
        )
    resolved_runtime_policy = normalize_runtime_policy(runtime_policy)

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
            "public_dashboard": env.public_dashboard,
            "workspace_manager": env.workspace_manager,
            "runtime_policy": resolved_runtime_policy,
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
        slug = f"{slugify(name, max_length=_FEATURE_SLUG_NAME_MAX)}-{feature_id}"
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
    from ..models.state import BugFixState, BugFixV2State, BuildState

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
    elif workflow_name == "bugfix-v2":
        metadata = feature.metadata or {}
        state = BugFixV2State(
            source_feature_id=str(metadata.get("source_feature_id", "") or ""),
            source_feature_name=str(metadata.get("source_feature_name", "") or ""),
            source_workspace_path=str(metadata.get("workspace_path", "") or ""),
            project=str(await artifacts.get("project", feature=feature) or ""),
        )
        mapping = {
            "project": "project",
            "bugflow-queue": "queue_summary",
            "bugflow-decisions": "decision_summary",
            "bugflow-source-context": "history_summary",
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
            "observations": "observations",
        }

    state.metadata = dict(feature.metadata or {})
    current_phase = str(state.metadata.get("_db_phase", "") or "")

    for artifact_key, field_name in mapping.items():
        if (
            workflow_name in {"planning", "develop", "full-develop"}
            and artifact_key == "scope"
            and current_phase == "scoping"
        ):
            approved = await artifacts.get("scope:approved", feature=feature)
            if not approved:
                continue
        val = await artifacts.get(artifact_key, feature=feature)
        if val:
            setattr(state, field_name, val)

    return state


def select_workflow(workflow_name: str):
    """Return the appropriate workflow instance."""
    from ..workflows import (
        BugFixV2Workflow,
        BugFixWorkflow,
        FullDevelopWorkflow,
        PlanningWorkflow,
    )

    if workflow_name == "planning":
        return PlanningWorkflow()
    elif workflow_name == "bugfix":
        return BugFixWorkflow()
    elif workflow_name == "bugfix-v2":
        return BugFixV2Workflow()
    else:
        return FullDevelopWorkflow()


def build_state(
    workflow_name: str,
    *,
    project: str = "",
    bug_report: str = "",
):
    """Construct the initial state for the given workflow."""
    from ..models.state import BugFixState, BugFixV2State, BuildState

    if workflow_name == "bugfix":
        return BugFixState(project=project, bug_report=bug_report)
    if workflow_name == "bugfix-v2":
        return BugFixV2State()
    return BuildState()


async def maybe_assert_adopted_or_legacy_for_resume(
    *,
    feature: Feature,
    artifacts: PostgresArtifactStore,
    is_resume: bool,
) -> Any | None:
    """Slice 12e -- consult the resume guard at the CLI/Slack workflow seam.

    Per doc 11 § "PR 11.13" + doc 12 § "In-Flight Cutover Policy" line 73-78:
    the resume guard
    :func:`iriai_build_v2.execution_control.adoption.assert_feature_adopted_or_legacy`
    is the SINGLE arbiter of whether an in-flight feature may enter the typed
    control-plane resume path. The guard:

    * pass-through when ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` is UNSET or
      DISABLED (the env flag from Slice 12c is the global product switch;
      legacy mode is bit-exact identical to pre-12e behavior).
    * pass-through when ``is_resume=False`` (a fresh feature has nothing to
      adopt; no marker is required).
    * under ``ENABLED`` + ``is_resume=True``: require an explicit adoption
      marker. If absent, the guard raises
      :class:`~iriai_build_v2.execution_control.adoption.ControlPlaneAdoptionError`
      and the workflow MUST NOT enter the control-plane resume path. The
      caller (CLI ``_run`` / Slack ``_resume_workflow``) propagates the
      error to the operator -- NO silent fallback to legacy per doc 12.

    The ``is_resume`` flag is supplied EXPLICITLY by the caller -- the CLI
    today always creates a fresh feature in ``_run`` (so callers pass
    ``is_resume=False``); the Slack ``_resume_workflow`` is the actual
    resume seam (so it passes ``is_resume=True``). This keeps the existing
    fresh-vs-resume distinction at each entrypoint and avoids new state
    machinery per the Slice-12e brief's hard rule.

    Returns:
        ``None`` when the guard is a pass-through (env flag UNSET/DISABLED,
        or ``is_resume=False``); the :class:`~iriai_build_v2.
        execution_control.atomic_landing.InFlightAdoptionRecord` when the
        flag is ENABLED and the marker is present.

    Raises:
        ControlPlaneAdoptionError: when the env flag is ENABLED and
            ``is_resume=True`` but no adoption marker exists for ``feature``.
        AdoptionMarkerCorruptError: when the marker exists but cannot be
            parsed.
        ControlPlaneEnvFlagError: when the env flag value is malformed
            (propagated from the Slice-12c env-flag reader).
    """

    from ..execution_control.adoption import assert_feature_adopted_or_legacy
    from ..execution_control.startup import (
        EnvFlagState,
        read_control_plane_env_flag,
    )

    # Slice 12c env-flag short-circuit. We re-read here (not consume a
    # caller-supplied state) so the guard reflects the LIVE env at the
    # workflow seam -- the CLI's process-startup read can race a long-lived
    # Slack bridge whose env was set on launch but whose features resume
    # later. The guard's own internal env read (in
    # `assert_feature_adopted_or_legacy`) is redundant under is_resume=True,
    # but we keep this explicit double-check so the "fresh start under
    # ENABLED skips the adoption check" contract is plain at the call site.
    flag_state = read_control_plane_env_flag()
    if flag_state is not EnvFlagState.ENABLED:
        # Legacy mode (UNSET / DISABLED). NO automatic migration per doc 12.
        return None

    if not is_resume:
        # Fresh feature -- nothing to adopt. The adoption marker is only
        # required at the in-flight RESUME boundary (doc 12 line 73-74:
        # "Resume sees the adoption marker, verifies it ... and only then
        # enters the control-plane resume path"). A freshly-created feature
        # is implicitly under the new control plane already.
        return None

    return await assert_feature_adopted_or_legacy(
        feature=feature,
        artifact_store=artifacts,
    )
