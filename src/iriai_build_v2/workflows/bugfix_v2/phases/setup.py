from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.state import BugFixV2State
from ...develop.phases.implementation import _clone_repo
from ..models import (
    BugflowPromotionQueueSnapshot,
    BugflowQueueSnapshot,
    BugflowRepoEntry,
    BugflowRepoStatus,
    default_counts,
)

logger = logging.getLogger(__name__)


class BugflowSetupPhase(Phase):
    name = "bugflow-setup"

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        state: BugFixV2State,
    ) -> BugFixV2State:
        source_feature_id = (
            state.source_feature_id
            or str(feature.metadata.get("source_feature_id", "") or "")
        )
        if not source_feature_id:
            raise RuntimeError("bugfix-v2 requires source_feature_id metadata")

        source_feature = await runner.feature_store.get_feature(source_feature_id)
        if not source_feature:
            raise RuntimeError(f"Source feature `{source_feature_id}` not found")

        source_workspace = (
            state.source_workspace_path
            or str(source_feature.metadata.get("workspace_path", "") or "")
        )
        if not source_workspace:
            raise RuntimeError(
                f"Source feature `{source_feature_id}` is missing workspace_path metadata"
            )

        source_root = (
            Path(source_workspace)
            / ".iriai"
            / "features"
            / source_feature.slug
            / "repos"
        )
        if not source_root.exists():
            raise RuntimeError(
                f"Source feature repo root does not exist: `{source_root}`"
            )

        workspace_mgr = runner.services.get("workspace_manager")
        if not workspace_mgr:
            raise RuntimeError("bugfix-v2 requires a workspace_manager service")
        workspace_root = Path(workspace_mgr._base)
        feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
        feature_root.mkdir(parents=True, exist_ok=True)

        repo_dirs = _discover_repo_dirs(source_root)
        if not repo_dirs:
            raise RuntimeError(f"No git repos found under `{source_root}`")

        branch_name = f"feature/{feature.slug}"
        for repo_dir in repo_dirs:
            rel_path = repo_dir.relative_to(source_root)
            dest = feature_root / rel_path
            if dest.exists():
                if await _is_healthy_bugflow_clone(dest, branch_name):
                    continue
                shutil.rmtree(dest, ignore_errors=True)
            await _clone_repo(repo_dir, dest, branch=branch_name)

        runner.services["worktree_root"] = feature_root
        runner.services["bugflow_source_feature"] = source_feature

        project_text = (
            f"Project workspace: {workspace_root}\n\n"
            f"Bugflow feature: {feature.name} ({feature.id})\n"
            f"Source feature: {source_feature.name} ({source_feature.id})\n"
            f"Feature repos: {feature_root}"
        )
        await runner.artifacts.put("project", project_text, feature=feature)

        source_context = {
            "source_feature_id": source_feature.id,
            "source_feature_name": source_feature.name,
            "source_feature_slug": source_feature.slug,
            "source_workspace_path": source_workspace,
            "implementation": await runner.artifacts.get("implementation", feature=source_feature),
            "handover": await runner.artifacts.get("handover", feature=source_feature),
            "observations": await runner.artifacts.get("observations", feature=source_feature),
            "observation_decisions": await runner.artifacts.get(
                "observation-decisions",
                feature=source_feature,
            ),
        }
        await runner.artifacts.put(
            "bugflow-source-context",
            json.dumps(source_context),
            feature=feature,
        )

        repo_status = await _collect_repo_status(feature_root, branch_name=branch_name)
        await runner.artifacts.put(
            "bugflow-repo-status",
            repo_status.model_dump_json(),
            feature=feature,
        )

        queue = BugflowQueueSnapshot(
            source_feature_id=source_feature.id,
            dashboard_url=str(feature.metadata.get("dashboard_url", "") or ""),
            counts=default_counts(),
            status_text="Queue idle",
            active_step="Waiting for reports",
        )
        await runner.artifacts.put("bugflow-queue", queue.model_dump_json(), feature=feature)
        await runner.artifacts.put(
            "bugflow-promotion-queue",
            BugflowPromotionQueueSnapshot(status_text="Promotion idle").model_dump_json(),
            feature=feature,
        )
        await runner.artifacts.put("bugflow-decisions", json.dumps([]), feature=feature)

        state.source_feature_id = source_feature.id
        state.source_feature_name = source_feature.name
        state.source_workspace_path = source_workspace
        state.project = project_text
        state.queue_summary = queue.model_dump_json()
        state.history_summary = json.dumps(source_context)
        state.phase = "bugflow-queue"
        return state


def _discover_repo_dirs(root: Path) -> list[Path]:
    repos: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        current = Path(dirpath)
        if (current / ".git").is_dir():
            repos.append(current)
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if not name.startswith(".iriai")]
    return sorted(repos)


async def _git_stdout(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {stderr.decode().strip()}"
        )
    return stdout.decode().strip()


async def _collect_repo_status(
    feature_root: Path,
    *,
    branch_name: str,
) -> BugflowRepoStatus:
    repos: list[BugflowRepoEntry] = []
    for repo_dir in _discover_repo_dirs(feature_root):
        rel_path = str(repo_dir.relative_to(feature_root))
        try:
            head_commit = await _git_stdout(repo_dir, "rev-parse", "HEAD")
            current_branch = await _git_stdout(repo_dir, "branch", "--show-current")
        except Exception:
            logger.warning("Failed to read repo status for %s", repo_dir, exc_info=True)
            head_commit = ""
            current_branch = branch_name
        repos.append(
            BugflowRepoEntry(
                repo_name=repo_dir.name,
                repo_path=rel_path,
                branch_name=current_branch or branch_name,
                head_commit=head_commit,
                touched=False,
            )
        )
    return BugflowRepoStatus(branch_name=branch_name, repos=repos)


async def _is_healthy_bugflow_clone(repo_dir: Path, branch_name: str) -> bool:
    if not (repo_dir / ".git").exists():
        return False
    try:
        current_branch = await _git_stdout(repo_dir, "branch", "--show-current")
    except Exception:
        return False
    return current_branch == branch_name
