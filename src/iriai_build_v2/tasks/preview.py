"""Preview deployment tasks — deploy and manage Railway preview environments."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from iriai_compose import Task

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

    from preview.api import PreviewClient
    from preview.models import DeploymentState


@dataclass
class PreviewInfo:
    """Returned by PreviewService.deploy()."""

    project: str
    feature: str
    urls: dict[str, str]
    status: str


class CleanupMode(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    TEARDOWN = "teardown"


class PreviewService:
    """Manages preview deployments via PreviewClient."""

    def __init__(self, client: PreviewClient) -> None:
        self._client = client

    async def ensure_project(self, project: str) -> None:
        """Register the project with preview if not already registered."""
        existing = self._client.get_project(project)
        if existing:
            return

        # Find the Railway project ID by name
        railway_client = self._client._get_railway_client()
        projects = await railway_client.list_projects()
        match = next((p for p in projects if p["name"] == project), None)
        if not match:
            raise RuntimeError(
                f"Railway project '{project}' not found in your account. "
                f"Available: {[p['name'] for p in projects]}"
            )

        await self._client.add_project(project, match["id"])

    async def deploy(
        self,
        project: str,
        *,
        feature: str | None = None,
        branch: str | None = None,
        clone_db: bool = True,
        force: bool = True,
        env_overrides: dict[str, str] | None = None,
        integration_test: bool = False,
        timeout_s: int = 600,
    ) -> PreviewInfo:
        """Deploy and poll until AWAITING_REVIEW. Returns PreviewInfo."""
        from preview.models import DeploymentStatus

        await self.ensure_project(project)

        state = await self._client.deploy(
            project,
            feature=feature,
            branch=branch,
            clone_db=clone_db,
            force=force,
            env_overrides=env_overrides,
            integration_test=integration_test,
        )

        if state.status == DeploymentStatus.AWAITING_REVIEW:
            return self._to_info(state)

        # Poll with exponential backoff
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_s
        interval = 3.0
        max_interval = 15.0
        terminal = {
            DeploymentStatus.APPROVED,
            DeploymentStatus.REJECTED,
            DeploymentStatus.TORN_DOWN,
        }

        while True:
            now = loop.time()
            if now >= deadline:
                raise TimeoutError(
                    f"Preview deploy for {project} timed out after {timeout_s}s"
                )

            await asyncio.sleep(min(interval, deadline - now))

            state = await self._client.status(project)
            if state is None:
                raise RuntimeError(f"Deployment state for {project} disappeared")

            if state.status == DeploymentStatus.AWAITING_REVIEW:
                return self._to_info(state)

            if state.status in terminal:
                raise RuntimeError(
                    f"Preview for {project} reached terminal state: {state.status.value}"
                )

            interval = min(interval * 2, max_interval)

    async def status(self, project: str) -> DeploymentState | None:
        """Passthrough to client.status()."""
        return await self._client.status(project)

    async def cleanup(
        self,
        project: str,
        *,
        mode: CleanupMode = CleanupMode.TEARDOWN,
        reason: str | None = None,
        keep: bool = False,
    ) -> None:
        """Dispatch to approve/reject/teardown based on mode."""
        if mode == CleanupMode.APPROVE:
            await self._client.approve(project, keep=keep)
        elif mode == CleanupMode.REJECT:
            await self._client.reject(project, reason=reason)
        elif mode == CleanupMode.TEARDOWN:
            await self._client.teardown_preview(project)

    async def close(self) -> None:
        """Close the underlying PreviewClient."""
        if hasattr(self._client, "close"):
            await self._client.close()

    @staticmethod
    def _to_info(state: DeploymentState) -> PreviewInfo:
        return PreviewInfo(
            project=state.project,
            feature=state.feature or "",
            urls=state.urls,
            status=state.status.value,
        )


class LaunchPreviewServerTask(Task):
    """Deploy a preview environment and wait until it is ready for review."""

    project: str
    feature: str | None = None
    branch: str | None = None
    clone_db: bool = True
    force: bool = True
    env_overrides: dict[str, str] | None = None
    integration_test: bool = False
    timeout_s: int = 600

    async def execute(self, runner: WorkflowRunner, feature: Feature) -> PreviewInfo:
        service: PreviewService = runner.services["preview"]
        return await service.deploy(
            self.project,
            feature=self.feature or feature.slug,
            branch=self.branch,
            clone_db=self.clone_db,
            force=self.force,
            env_overrides=self.env_overrides,
            integration_test=self.integration_test,
            timeout_s=self.timeout_s,
        )


class CleanupPreviewTask(Task):
    """Clean up a preview environment (approve, reject, or teardown)."""

    project: str
    mode: CleanupMode = CleanupMode.TEARDOWN
    reason: str | None = None
    keep: bool = False

    async def execute(self, runner: WorkflowRunner, feature: Feature) -> None:
        service: PreviewService = runner.services["preview"]
        await service.cleanup(
            self.project,
            mode=self.mode,
            reason=self.reason,
            keep=self.keep,
        )
