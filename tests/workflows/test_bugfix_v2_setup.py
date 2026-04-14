from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.state import BugFixV2State
from iriai_build_v2.workflows.bugfix_v2.phases.setup import BugflowSetupPhase


class _Artifacts:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    async def get(self, key: str, *, feature) -> str | None:
        return self.values.get((feature.id, key))

    async def put(self, key: str, value: str, *, feature) -> None:
        self.values[(feature.id, key)] = value


class _FeatureStore:
    def __init__(self, features: dict[str, SimpleNamespace]) -> None:
        self.features = features

    async def get_feature(self, feature_id: str):
        return self.features.get(feature_id)


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_bugflow_setup_clones_source_feature_repos(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source_repo = workspace / ".iriai" / "features" / "source-feature-beced7b1" / "repos" / "app"
    _init_git_repo(source_repo)

    source_feature = SimpleNamespace(
        id="beced7b1",
        name="Source feature",
        slug="source-feature-beced7b1",
        metadata={"workspace_path": str(workspace)},
    )
    bugflow_feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow: Source feature",
        slug="bugflow-source-feature-bf123456",
        metadata={"source_feature_id": "beced7b1", "dashboard_url": "https://dash.example/feature/bf123456"},
    )

    runner = SimpleNamespace(
        feature_store=_FeatureStore({source_feature.id: source_feature}),
        services={"workspace_manager": SimpleNamespace(_base=workspace)},
        artifacts=_Artifacts(),
    )
    state = BugFixV2State(
        source_feature_id="beced7b1",
        source_workspace_path=str(workspace),
    )

    result = await BugflowSetupPhase().execute(runner, bugflow_feature, state)

    feature_root = workspace / ".iriai" / "features" / bugflow_feature.slug / "repos"
    assert (feature_root / "app" / ".git").exists()
    assert (bugflow_feature.id, "project") in runner.artifacts.values
    assert (bugflow_feature.id, "bugflow-queue") in runner.artifacts.values
    assert result.source_feature_name == "Source feature"
    assert runner.services["worktree_root"] == feature_root


@pytest.mark.asyncio
async def test_bugflow_setup_requires_existing_source_feature_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source_feature = SimpleNamespace(
        id="beced7b1",
        name="Source feature",
        slug="source-feature-beced7b1",
        metadata={"workspace_path": str(workspace)},
    )
    bugflow_feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow: Source feature",
        slug="bugflow-source-feature-bf123456",
        metadata={"source_feature_id": "beced7b1"},
    )

    runner = SimpleNamespace(
        feature_store=_FeatureStore({source_feature.id: source_feature}),
        services={"workspace_manager": SimpleNamespace(_base=workspace)},
        artifacts=_Artifacts(),
    )
    state = BugFixV2State(source_feature_id="beced7b1", source_workspace_path=str(workspace))

    with pytest.raises(RuntimeError, match="Source feature repo root does not exist"):
        await BugflowSetupPhase().execute(runner, bugflow_feature, state)


@pytest.mark.asyncio
async def test_bugflow_setup_replaces_partial_destination_clone(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source_repo = workspace / ".iriai" / "features" / "source-feature-beced7b1" / "repos" / "app"
    _init_git_repo(source_repo)

    source_feature = SimpleNamespace(
        id="beced7b1",
        name="Source feature",
        slug="source-feature-beced7b1",
        metadata={"workspace_path": str(workspace)},
    )
    bugflow_feature = SimpleNamespace(
        id="bf123456",
        name="Bugflow: Source feature",
        slug="bugflow-source-feature-bf123456",
        metadata={"source_feature_id": "beced7b1"},
    )

    broken_dest = workspace / ".iriai" / "features" / bugflow_feature.slug / "repos" / "app"
    broken_dest.mkdir(parents=True, exist_ok=True)
    (broken_dest / "README.md").write_text("partial\n", encoding="utf-8")

    runner = SimpleNamespace(
        feature_store=_FeatureStore({source_feature.id: source_feature}),
        services={"workspace_manager": SimpleNamespace(_base=workspace)},
        artifacts=_Artifacts(),
    )
    state = BugFixV2State(
        source_feature_id="beced7b1",
        source_workspace_path=str(workspace),
    )

    await BugflowSetupPhase().execute(runner, bugflow_feature, state)

    assert (broken_dest / ".git").exists()
