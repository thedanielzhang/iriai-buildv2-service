from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, RepoSpec, TaskFileScope
from iriai_build_v2.models.outputs import ScopeOutput
from iriai_build_v2.services.workspace import DirectoryMap, RepoEntry, WorkspaceManager
from iriai_build_v2.workflows.develop.phases.implementation import (
    WorktreeRegistry,
    _discover_repo_roots_under,
    _ensure_task_worktrees,
    _remove_repo_path,
    _workflow_repos_root_guard_problems,
)


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
async def test_ensure_task_worktrees_rejects_symlinked_source_repo_before_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "outside-root"
    outside_repo = outside_root / "app"
    (outside_repo / ".git").mkdir(parents=True)
    (workspace_root / "link").symlink_to(outside_root, target_is_directory=True)

    git_calls: list[tuple[Path, tuple[str, ...]]] = []
    scaffold_calls: list[Path] = []

    async def _unexpected_run_git(cwd: Path, *args: str) -> str:
        git_calls.append((cwd, args))
        raise AssertionError(f"must not clone symlinked source repo: {cwd} {args}")

    async def _unexpected_scaffold_repo(path: Path) -> None:
        scaffold_calls.append(path)
        raise AssertionError(f"must not scaffold from symlinked source repo: {path}")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._run_git",
        _unexpected_run_git,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._scaffold_repo",
        _unexpected_scaffold_repo,
    )

    task = ImplementationTask(
        id="task-source-symlink",
        name="Reject symlinked source",
        description="Do not trust source repos behind workspace symlinks.",
        repo_path="link/app",
        file_scope=[TaskFileScope(path="link/app/README.md", action="modify")],
    )
    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    with pytest.raises(RuntimeError, match="source repo.*symlink ancestor"):
        await _ensure_task_worktrees(runner, feature, [task])

    assert git_calls == []
    assert scaffold_calls == []
    assert (outside_repo / ".git").exists()
    assert not (
        workspace_root / ".iriai" / "features" / "feat" / "repos" / "link" / "app"
    ).exists()


@pytest.mark.asyncio
async def test_ensure_task_worktrees_rejects_symlink_ancestor_before_delete(
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    source_repo = workspace_root / "link" / "app"
    (source_repo / ".git").mkdir(parents=True)
    feature_root = workspace_root / ".iriai" / "features" / "feat" / "repos"
    feature_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "app" / ".git").mkdir(parents=True)
    (feature_root / "link").symlink_to(outside, target_is_directory=True)

    task = ImplementationTask(
        id="task-symlink",
        name="Reject symlink ancestor",
        description="Reject symlink ancestor",
        repo_path="link/app",
        file_scope=[TaskFileScope(path="link/app/README.md", action="modify")],
    )
    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    with pytest.raises(RuntimeError, match="symlink ancestor"):
        await _ensure_task_worktrees(runner, feature, [task])

    assert (outside / "app" / ".git").exists()


@pytest.mark.asyncio
async def test_ensure_task_worktrees_rejects_symlink_repos_root_before_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    source_repo = workspace_root / "app"
    (source_repo / ".git").mkdir(parents=True)
    feature_parent = workspace_root / ".iriai" / "features" / "feat"
    feature_parent.mkdir(parents=True)
    outside = tmp_path / "outside-repos"
    outside.mkdir()
    (feature_parent / "repos").symlink_to(outside, target_is_directory=True)

    async def _unexpected_run_git(cwd: Path, *args: str) -> str:
        raise AssertionError(f"must not clone through symlinked repos root: {cwd} {args}")

    async def _unexpected_scaffold_repo(path: Path) -> None:
        raise AssertionError(f"must not scaffold through symlinked repos root: {path}")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._run_git",
        _unexpected_run_git,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._scaffold_repo",
        _unexpected_scaffold_repo,
    )

    task = ImplementationTask(
        id="task-root-symlink",
        name="Reject root symlink",
        description="Reject symlinked feature repos root.",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/README.md", action="modify")],
    )
    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    with pytest.raises(RuntimeError, match="symlink.*repos root|repos root.*symlink"):
        await _ensure_task_worktrees(runner, feature, [task])

    assert not (outside / "app").exists()


def test_workflow_repo_guard_rejects_symlinked_feature_ancestor_before_discovery(
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    feature_parent = workspace_root / ".iriai" / "features"
    feature_parent.mkdir(parents=True)
    outside_feature = tmp_path / "outside-feature"
    outside_repo = outside_feature / "repos" / "app"
    (outside_repo / ".git").mkdir(parents=True)
    (feature_parent / "feat").symlink_to(outside_feature, target_is_directory=True)
    repos_root = feature_parent / "feat" / "repos"

    problems = _workflow_repos_root_guard_problems(repos_root)

    assert problems
    assert problems[0]["reason"] == "workflow_repos_root_symlink_ancestor"
    assert str(feature_parent / "feat") == problems[0]["path"]
    assert _discover_repo_roots_under(repos_root) == []


def test_discover_repo_roots_skips_package_manager_subtrees(tmp_path: Path):
    repos_root = tmp_path / ".iriai" / "features" / "feat" / "repos"
    app_repo = repos_root / "app"
    node_modules_repo = repos_root / "app" / "node_modules" / "dep"
    pnpm_repo = repos_root / "app" / ".pnpm" / "dep"
    for repo in (app_repo, node_modules_repo, pnpm_repo):
        (repo / ".git").mkdir(parents=True)

    assert _discover_repo_roots_under(repos_root) == [app_repo]


@pytest.mark.asyncio
async def test_ensure_task_worktrees_writes_registry_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    source_repo = workspace_root / "app"
    (source_repo / ".git").mkdir(parents=True)

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        if args and args[0] == "clone":
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
        return ""

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._run_git",
        _fake_run_git,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    task = ImplementationTask(
        id="task-registry",
        name="Modify app",
        description="Touch app code.",
        repo_path="app/src",
        file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
    )

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )
    feature = SimpleNamespace(id="feat-id", slug="feat")

    await _ensure_task_worktrees(runner, feature, [task], group_idx=45)

    registry = WorktreeRegistry.model_validate_json(
        artifacts.store["worktree-registry:g45"]
    )
    assert registry.complete is True
    assert registry.repos[0].repo_path == "app"
    assert registry.repos[0].action == "extend"
    assert registry.repos[0].task_ids == ["task-registry"]
    assert registry.repos[0].nested_requests == ["app/src"]
    assert registry.repos[0].preflight_status == "cloned"

    authority_registry = json.loads(artifacts.store["workspace-authority-registry:g45"])
    assert authority_registry["authoritative_mode"] == "compatibility_projection"
    assert authority_registry["registry"]["repos"][0]["workspace_relative_path"] == "app"


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


@pytest.mark.asyncio
async def test_ensure_task_worktrees_normalizes_nested_repo_path_to_existing_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    source_repo = workspace_root / "iriai-studio"
    (source_repo / ".git").mkdir(parents=True)

    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args and args[0] == "clone":
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
        return ""

    async def _unexpected_scaffold_repo(path: Path) -> None:
        raise AssertionError(f"must not scaffold nested repo at {path}")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._run_git",
        _fake_run_git,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._scaffold_repo",
        _unexpected_scaffold_repo,
    )

    task = ImplementationTask(
        id="task-nested",
        name="Repair retired dashboard",
        description="Task metadata incorrectly points at a nested repo.",
        repo_path="iriai-studio/src/webviews/dashboard",
        file_scope=[
            TaskFileScope(
                path="iriai-studio/src/webviews/dashboard/README.md",
                action="modify",
            )
        ],
    )

    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    await _ensure_task_worktrees(runner, feature, [task])

    dest = workspace_root / ".iriai" / "features" / "feat" / "repos" / "iriai-studio"
    nested_dest = dest / "src" / "webviews" / "dashboard"
    assert task.repo_path == "iriai-studio"
    assert dest.exists()
    assert (dest / ".git").exists()
    assert not (nested_dest / ".git").exists()
    assert any(args[0] == "clone" and Path(args[-1]) == dest for _cwd, args in calls)


@pytest.mark.asyncio
async def test_ensure_task_worktrees_reuses_feature_repo_for_nested_resume_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    feature_repo = (
        workspace_root
        / ".iriai"
        / "features"
        / "feat"
        / "repos"
        / "iriai-studio"
    )
    (feature_repo / ".git").mkdir(parents=True)

    async def _unexpected_run_git(cwd: Path, *args: str) -> str:
        raise AssertionError(f"must not run git for existing feature repo: {cwd} {args}")

    async def _unexpected_scaffold_repo(path: Path) -> None:
        raise AssertionError(f"must not scaffold nested repo at {path}")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._run_git",
        _unexpected_run_git,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._scaffold_repo",
        _unexpected_scaffold_repo,
    )

    task = ImplementationTask(
        id="task-resume-nested",
        name="Repair retired dashboard",
        description="Resume metadata incorrectly points at a nested repo.",
        repo_path="iriai-studio/src/webviews/dashboard",
        file_scope=[
            TaskFileScope(
                path="iriai-studio/src/webviews/dashboard/README.md",
                action="modify",
            )
        ],
    )

    runner = SimpleNamespace(services={"workspace_manager": SimpleNamespace(_base=workspace_root)})
    feature = SimpleNamespace(slug="feat")

    await _ensure_task_worktrees(runner, feature, [task])

    assert task.repo_path == "iriai-studio"
    assert (feature_repo / ".git").exists()
    assert not (feature_repo / "src" / "webviews" / "dashboard" / ".git").exists()


def test_remove_repo_path_quarantines_busy_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    (repo_path / "nested" / "tests").mkdir(parents=True, exist_ok=True)

    original_rmtree = shutil.rmtree
    original_rename = Path.rename
    renamed_targets: list[Path] = []

    def _fake_rmtree(path: Path | str, *args, **kwargs) -> None:
        path = Path(path)
        ignore_errors = bool(kwargs.get("ignore_errors", False))
        if path == repo_path and not ignore_errors:
            raise OSError(66, "Directory not empty")
        return original_rmtree(path, *args, **kwargs)

    def _tracking_rename(self: Path, target: Path | str) -> Path:
        renamed_targets.append(Path(target))
        return original_rename(self, target)

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation.shutil.rmtree",
        _fake_rmtree,
    )
    monkeypatch.setattr(Path, "rename", _tracking_rename)

    _remove_repo_path(repo_path)

    assert not repo_path.exists()
    assert renamed_targets
    assert renamed_targets[0].name.startswith("repo-stale-")


@pytest.mark.asyncio
async def test_setup_feature_workspace_skips_external_adjacent_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    base = tmp_path / "workspace"
    source_repo = base / "iriai-build-v2"
    (source_repo / ".git").mkdir(parents=True)
    compose_repo = base / "iriai-compose"
    (compose_repo / ".git").mkdir(parents=True)

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        if args and args[0] == "clone":
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
        return ""

    monkeypatch.setattr("iriai_build_v2.services.workspace._run_git", _fake_run_git)

    manager = WorkspaceManager(base)
    manager.load_directory_map = lambda: DirectoryMap(
        repos={
            "iriai-build-v2": RepoEntry(
                name="iriai-build-v2",
                path="iriai-build-v2",
                github_url="https://github.com/thedanielzhang/iriai-build-v2",
            ),
            "iriai-compose": RepoEntry(
                name="iriai-compose",
                path="iriai-compose",
                github_url="https://github.com/thedanielzhang/iriai-compose",
            ),
        },
        dependencies={
            "iriai-build-v2": ["iriai-compose", "asyncpg (external)"],
        },
        raw_content="",
    )

    feature = SimpleNamespace(slug="feat", name="Feature")
    scope = ScopeOutput(
        repos=[RepoSpec(name="iriai-build-v2", local_path="iriai-build-v2", action="read_only")],
        complete=True,
    )

    project = await manager.setup_feature_workspace(feature, scope)

    repo_names = [repo.name for repo in project.repos]
    assert "iriai-build-v2" in repo_names
    assert "asyncpg (external)" not in repo_names
