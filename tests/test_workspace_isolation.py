from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, RepoSpec, TaskFileScope
from iriai_build_v2.services.workspace import WorkspaceManager
from iriai_build_v2.workflows.develop.phases.implementation import _ensure_task_worktrees


@pytest.mark.asyncio
async def test_workspace_manager_uses_isolated_clone_instead_of_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    base = tmp_path / "workspace"
    source_repo = base / "app"
    source_repo_git = source_repo / ".git"
    source_repo_git.mkdir(parents=True)
    feature_root = base / ".iriai" / "features" / "feat" / "repos"
    feature_root.mkdir(parents=True)

    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args and args[0] == "clone":
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
        return ""

    monkeypatch.setattr("iriai_build_v2.services.workspace._run_git", _fake_run_git)

    manager = WorkspaceManager(base)
    spec = RepoSpec(name="app", local_path=str(source_repo), action="extend")

    resolved = await manager._resolve_and_worktree(spec, feature_root, "feat")

    assert resolved.local_path == str(feature_root / "app")
    assert any(args[0] == "clone" for _cwd, args in calls)
    assert all("worktree" not in args for _cwd, args in calls)
    assert any(
        cwd == feature_root / "app" and args[:2] == ("checkout", "-B")
        for cwd, args in calls
    )


@pytest.mark.asyncio
async def test_ensure_task_worktrees_clones_read_only_repo_instead_of_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    source_repo = workspace_root / "app"
    (source_repo / ".git").mkdir(parents=True)

    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args and args[0] == "clone":
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
        return ""

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._run_git",
        _fake_run_git,
    )

    task = ImplementationTask(
        id="task-1",
        name="Inspect app",
        description="Read from the app repo.",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/README.md", action="read_only")],
    )

    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    await _ensure_task_worktrees(runner, feature, [task])

    dest = workspace_root / ".iriai" / "features" / "feat" / "repos" / "app"
    assert dest.exists()
    assert not dest.is_symlink()
    assert any(args[0] == "clone" for _cwd, args in calls)
    assert all("branch" not in args for _cwd, args in calls)


@pytest.mark.asyncio
async def test_ensure_task_worktrees_scaffolds_new_repo_inside_feature_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    scaffolded: list[Path] = []

    async def _fake_scaffold_repo(path: Path) -> None:
        scaffolded.append(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / ".git").mkdir(exist_ok=True)

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._scaffold_repo",
        _fake_scaffold_repo,
    )

    task = ImplementationTask(
        id="task-2",
        name="Create new service",
        description="Create a new repo.",
        repo_path="services/newsvc",
        file_scope=[TaskFileScope(path="services/newsvc/app.py", action="create")],
    )

    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    await _ensure_task_worktrees(runner, feature, [task])

    dest = workspace_root / ".iriai" / "features" / "feat" / "repos" / "services" / "newsvc"
    assert scaffolded == [dest]
    assert not (workspace_root / "services" / "newsvc").exists()
