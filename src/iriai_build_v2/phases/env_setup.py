from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from iriai_compose import Feature, Phase, WorkflowRunner

from ..models.state import BugFixState
from ..tasks.preview import LaunchPreviewServerTask

logger = logging.getLogger(__name__)


async def _run_cmd(*args: str, cwd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {stderr.decode()}")
    return stdout.decode().strip()


def _find_git_repos(workspace: str) -> list[str]:
    """Find all git repositories under the workspace."""
    repos: list[str] = []
    ws = Path(workspace)
    if (ws / ".git").exists():
        return [str(ws)]
    for git_dir in ws.glob("**/.git"):
        if git_dir.is_dir() and len(git_dir.relative_to(ws).parts) <= 4:
            repos.append(str(git_dir.parent))
    return sorted(repos)


async def _create_branch(repo: str, branch_name: str) -> None:
    """Create and push a bugfix branch in a repo."""
    logger.info("Creating branch %s in %s", branch_name, repo)
    await _run_cmd("git", "checkout", "main", cwd=repo)
    await _run_cmd("git", "pull", "--ff-only", cwd=repo)
    try:
        await _run_cmd("git", "checkout", "-b", branch_name, cwd=repo)
    except RuntimeError:
        await _run_cmd("git", "checkout", branch_name, cwd=repo)
    await _run_cmd("git", "push", "-u", "origin", branch_name, cwd=repo)


def _build_env_overrides() -> dict[str, str]:
    """Build env var overrides for excluded secrets that the preview needs."""
    overrides: dict[str, str] = {}

    # The deploy-service needs a Railway token to create/manage projects
    railway_token = os.environ.get("RAILWAY_TOKEN", "")
    if railway_token:
        overrides["RAILWAY_API_TOKEN"] = railway_token

    # GitHub PAT for robot account (deploy-service + iriai-engine)
    github_token = os.environ.get("ROBOT_ACCOUNT_GITHUB_TOKEN", "")
    if not github_token:
        github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token:
        overrides["ROBOT_ACCOUNT_GITHUB_TOKEN"] = github_token

    # Anthropic API key for iriai-engine
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        overrides["ANTHROPIC_API_KEY"] = anthropic_key

    return overrides


class EnvironmentSetupPhase(Phase):
    name = "env-setup"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        workspace = str(runner._workspaces["main"].path)
        branch_name = f"bugfix/{feature.slug}"

        # Create bugfix branch in all repos under the workspace
        repos = _find_git_repos(workspace)
        if not repos:
            raise RuntimeError(f"No git repositories found under {workspace}")

        for repo in repos:
            await _create_branch(repo, branch_name)

        state.metadata["branch"] = branch_name
        state.metadata["repos"] = repos

        # Deploy preview with integration test mode and env overrides
        env_overrides = _build_env_overrides()
        logger.info(
            "Deploying preview for project=%s branch=%s (integration_test=True, %d env overrides)",
            state.project, branch_name, len(env_overrides),
        )

        info = await runner.run(
            LaunchPreviewServerTask(
                project=state.project,
                branch=branch_name,
                integration_test=True,
                env_overrides=env_overrides,
            ),
            feature,
            phase_name=self.name,
        )

        preview_url = next(iter(info.urls.values()), "") if info.urls else ""
        state.preview_url = preview_url
        await runner.artifacts.put("preview_url", preview_url, feature=feature)

        logger.info("Preview deployed: %s", preview_url)
        return state
