"""Pinning tests — depth-1 nested-repo boundary hole (BUG-6, 2026-06-12).

A missing single-segment path directly inside a search root that is itself a
git repo (monorepo workspace / '.'-rooted feature worktree) must be treated as
a NESTED request into the root repo ('.'), never as a new repo boundary — the
registry would otherwise scaffold an embedded .git over a populated tracked
subdirectory (live incident: spend-client wiped to scaffold at g5 prefetch).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from iriai_build_v2.workflows.develop.phases.implementation import (
    _normalize_workspace_repo_path,
)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_depth1_missing_path_in_repo_workspace_is_nested(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _init_repo(workspace)
    (workspace / "spend-client").mkdir()
    safe, nested = _normalize_workspace_repo_path("spend-client", workspace)
    assert safe == "."
    assert nested == "spend-client"


def test_depth1_missing_path_in_repo_feature_root_is_nested(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_root = tmp_path / "feature" / "repos"
    _init_repo(feature_root)
    safe, nested = _normalize_workspace_repo_path(
        "spend-client", workspace, feature_root=feature_root
    )
    assert safe == "."
    assert nested == "spend-client"


def test_new_repo_in_non_repo_workspace_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    safe, nested = _normalize_workspace_repo_path("brand-new-service", workspace)
    assert safe == "brand-new-service"
    assert nested is None


def test_exact_existing_repo_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _init_repo(workspace)  # workspace itself a repo — exact check must win
    _init_repo(workspace / "existing-service")
    safe, nested = _normalize_workspace_repo_path("existing-service", workspace)
    assert safe == "existing-service"
    assert nested is None


def test_multisegment_prefix_detection_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _init_repo(workspace / "existing-service")
    safe, nested = _normalize_workspace_repo_path(
        "existing-service/sub/dir", workspace
    )
    assert safe == "existing-service"
    assert nested == str(Path("existing-service/sub/dir"))
