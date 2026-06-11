"""Unit tests for N-18 monorepo dispatch fixes 1 and 2.

Fix 1 (implementation.py:_dag_direct_workflow_repo_roots): monorepo-aware ACL
candidate roots — when repos_root/.git exists, return repos_root itself.

Fix 2 (implementation.py:_task_repo_prefixed_path): ignore task.repo_path
when it is absolute, to avoid garbage relative ACL targets like
"Users/danielzhang/.../repos/supply-chain/src".
"""
from __future__ import annotations

import subprocess
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, TaskFileScope
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_dir(path: Path) -> None:
    """Create a minimal .git directory (no real repo needed for these tests)."""
    (path / ".git").mkdir(parents=True, exist_ok=True)


def _task(
    task_id: str,
    repo_path: str,
    *file_scope_paths: str,
) -> ImplementationTask:
    scopes = [TaskFileScope(path=p, action="modify") for p in file_scope_paths]
    return ImplementationTask(
        id=task_id,
        name=task_id,
        description=task_id,
        repo_path=repo_path,
        files=list(file_scope_paths),
        file_scope=scopes,
    )


# ---------------------------------------------------------------------------
# Fix 1: _dag_direct_workflow_repo_roots — monorepo shape
# ---------------------------------------------------------------------------


def test_dag_direct_workflow_repo_roots_monorepo_returns_repos_root(tmp_path: Path) -> None:
    """When repos_root/.git exists (monorepo), repos_root is returned as the single candidate."""
    repos_root = tmp_path / "feature" / "repos"
    _make_git_dir(repos_root)

    roots = implementation_module._dag_direct_workflow_repo_roots(repos_root)

    assert roots == [repos_root], (
        "Expected the repos root itself to be returned for a monorepo layout"
    )


def test_dag_direct_workflow_repo_roots_monorepo_wins_over_children(tmp_path: Path) -> None:
    """Monorepo check fires before the child-enumeration loop; children with .git are irrelevant."""
    repos_root = tmp_path / "feature" / "repos"
    # Both repos_root AND a child have .git — monorepo wins.
    _make_git_dir(repos_root)
    child = repos_root / "sub-repo"
    _make_git_dir(child)

    roots = implementation_module._dag_direct_workflow_repo_roots(repos_root)

    assert roots == [repos_root]


def test_dag_direct_workflow_repo_roots_polyrepo_enumerates_children(tmp_path: Path) -> None:
    """Legacy polyrepo: child directories with .git are returned; root without .git is not."""
    repos_root = tmp_path / "feature" / "repos"
    repos_root.mkdir(parents=True)
    child_a = repos_root / "app"
    child_b = repos_root / "backend"
    _make_git_dir(child_a)
    _make_git_dir(child_b)
    # repos_root itself has no .git

    roots = implementation_module._dag_direct_workflow_repo_roots(repos_root)

    assert set(roots) == {child_a, child_b}
    assert repos_root not in roots


def test_dag_direct_workflow_repo_roots_none_input() -> None:
    """None repos_root returns empty list without error."""
    assert implementation_module._dag_direct_workflow_repo_roots(None) == []


def test_dag_direct_workflow_repo_roots_nonexistent_path(tmp_path: Path) -> None:
    """Non-existent repos_root returns empty list."""
    repos_root = tmp_path / "does-not-exist" / "repos"
    assert implementation_module._dag_direct_workflow_repo_roots(repos_root) == []


def test_dag_workspace_permission_repo_roots_monorepo(tmp_path: Path) -> None:
    """_dag_workspace_permission_repo_roots returns repos_root for monorepo via fix 1."""
    feature_root = tmp_path / "feature" / "repos"
    _make_git_dir(feature_root)

    roots = implementation_module._dag_workspace_permission_repo_roots(feature_root)

    assert roots == [feature_root]


# ---------------------------------------------------------------------------
# Fix 2: _task_repo_prefixed_path — ignore absolute repo_path
# ---------------------------------------------------------------------------


def test_task_repo_prefixed_path_ignores_absolute_repo_path(tmp_path: Path) -> None:
    """An absolute repo_path must not be strip()-prefixed onto file_scope paths."""
    abs_repo = str(tmp_path / "home" / "user" / "repos")
    task = _task("TASK-1", abs_repo, "supply-chain/src/service.py")

    result = implementation_module._task_repo_prefixed_path(
        task, "supply-chain/src/service.py"
    )

    # Must NOT produce garbage like "home/user/repos/supply-chain/src/service.py"
    assert not result.startswith("home/"), (
        f"Absolute repo_path leaked into prefixed target: {result!r}"
    )
    assert result == "supply-chain/src/service.py", (
        f"Expected path unchanged; got {result!r}"
    )


def test_task_repo_prefixed_path_relative_repo_path_still_prefixes(tmp_path: Path) -> None:
    """Relative repo_path (normal case) is still prefixed when not already present."""
    task = _task("TASK-2", "supply-chain", "src/service.py")

    result = implementation_module._task_repo_prefixed_path(task, "src/service.py")

    assert result == "supply-chain/src/service.py"


def test_task_repo_prefixed_path_no_double_prefix(tmp_path: Path) -> None:
    """If the path already starts with the relative repo_path prefix, it is not doubled."""
    task = _task("TASK-3", "supply-chain", "supply-chain/src/service.py")

    result = implementation_module._task_repo_prefixed_path(
        task, "supply-chain/src/service.py"
    )

    assert result == "supply-chain/src/service.py"


def test_task_repo_prefixed_path_absolute_raw_path_not_mangled_by_abs_repo_path() -> None:
    """An absolute raw_path that is stripped by _strip_direct_route_line_suffix should still
    not get a garbage prefix from an absolute repo_path."""
    # _strip_direct_route_line_suffix strips leading "/" so the normalized form
    # of "/abs/repos/src/foo.py" is "abs/repos/src/foo.py".  Crucially, even
    # that stripped form must NOT get further prefixed by a mangled abs repo_path.
    abs_repo = "/Users/builder/repos"
    task = _task("TASK-4", abs_repo, "src/foo.py")

    result = implementation_module._task_repo_prefixed_path(task, "src/foo.py")

    # The abs repo_path must be ignored; the bare relative path is returned as-is.
    assert result == "src/foo.py", (
        f"Absolute repo_path must be ignored; got {result!r}"
    )
    assert not result.startswith("Users/") and not result.startswith("builder/"), (
        f"Absolute repo_path leaked into result: {result!r}"
    )


def test_task_repo_prefixed_path_empty_repo_path_no_prefix() -> None:
    """Empty repo_path results in the raw path being returned without prefix."""
    task = _task("TASK-5", "", "src/foo.py")

    result = implementation_module._task_repo_prefixed_path(task, "src/foo.py")

    assert result == "src/foo.py"


# ---------------------------------------------------------------------------
# Fix 1 + Fix 2 integration: ACL targets in monorepo layout produce valid paths
# ---------------------------------------------------------------------------


def test_dag_task_permission_targets_monorepo_absolute_repo_path_is_clean(tmp_path: Path) -> None:
    """In monorepo layout with absolute repo_path, permission targets are repos-root-relative."""
    abs_repo = str(tmp_path / "feature" / "repos")
    tasks = [
        _task(
            "TASK-A",
            abs_repo,
            "supply-chain/src/service.py",
            "shared_libs/kaya_db/kaya_db/models.py",
        )
    ]

    targets = implementation_module._dag_task_permission_targets(tasks)

    for t in targets:
        assert not Path(t).is_absolute(), f"Target must not be absolute: {t!r}"
        assert not t.startswith("home/") and not t.startswith("Users/"), (
            f"Absolute repo_path leaked into target: {t!r}"
        )
    assert "supply-chain/src/service.py" in targets
    assert "shared_libs/kaya_db/kaya_db/models.py" in targets


def test_normalize_feature_workspace_cleanup_permissions_monorepo_not_operator_required(
    tmp_path: Path,
) -> None:
    """Fix 1+2 together: monorepo ACL normalization succeeds and is not operator_required."""
    repos_root = tmp_path / "feature" / "repos"
    # Set up monorepo: .git at repos root, not in children.
    _make_git_dir(repos_root)
    # Create the target file.
    target_dir = repos_root / "supply-chain" / "src"
    target_dir.mkdir(parents=True)
    (target_dir / "service.py").write_text("# stub\n", encoding="utf-8")

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        ["supply-chain/src/service.py"],
        reason="n18-test",
    )

    # The repair lane should find the monorepo root as a candidate and not force operator_required.
    assert report["operator_required"] is False, (
        f"Expected no operator_required for monorepo; operator_reasons={report.get('operator_reasons')}"
    )
