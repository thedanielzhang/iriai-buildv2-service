from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from iriai_compose import Feature, Phase, Workflow

from ...services.hosting import DocHostingService
from ...services.workspace import WorkspaceManager
from .phases import (
    BroadPhase,
    PlanReviewPhase,
    ScopingPhase,
    SubfeaturePhase,
    TaskPlanningPhase,
)

if TYPE_CHECKING:
    from iriai_compose import WorkflowRunner


class PlanningWorkflow(Workflow):
    name = "planning"

    async def on_start(self, runner: WorkflowRunner, feature: Feature, state: BaseModel) -> None:
        mirror = runner.services.get("artifact_mirror")
        if not mirror:
            raise RuntimeError(
                "Planning workflow requires 'artifact_mirror' service. "
                "Ensure ArtifactMirror is registered."
            )
        tunnel = runner.services.get("tunnel")  # None for CLI, CloudflareTunnel for Slack
        feedback = runner.services.get("feedback")  # kept for backward compat
        hosting = DocHostingService(mirror, feedback, tunnel=tunnel)
        runner.services["hosting"] = hosting
        mirror.write_manifest(feature.id, title=feature.name, phase="scoping")

        # Build/refresh the directory map before scoping begins
        workspace_mgr = runner.services.get("workspace_manager")
        if isinstance(workspace_mgr, WorkspaceManager):
            workspace_mgr.build_directory_map()

    async def on_done(self, runner: WorkflowRunner, feature: Feature, state: BaseModel) -> None:
        # Refresh directory map to capture any repos added during the workflow
        workspace_mgr = runner.services.get("workspace_manager")
        if isinstance(workspace_mgr, WorkspaceManager):
            workspace_mgr.build_directory_map()

        hosting = runner.services.get("hosting")
        if hosting:
            await hosting.stop_all()

    def build_phases(self) -> list[type[Phase]]:
        return [ScopingPhase, BroadPhase, SubfeaturePhase, PlanReviewPhase, TaskPlanningPhase]
