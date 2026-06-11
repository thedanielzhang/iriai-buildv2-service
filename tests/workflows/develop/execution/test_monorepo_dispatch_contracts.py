"""Unit tests for N-18 monorepo dispatch fixes 3 and 4.

Fix 3 (task_contracts.py:compile_task): contract's repo_path field must be the
resolved repo's workspace_relative_path, never the raw absolute task.repo_path.

Fix 4 (workspace_authority.py:_candidate_root_from_file_path): when repo_parent
itself is a git repo (monorepo), it is returned as the candidate root for
relative file paths (instead of only checking per-child .git dirs).
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, TaskFileScope
from iriai_build_v2.workflows.develop.execution.task_contracts import (
    ContractCompileRequest,
    ContractCompiler,
)
from iriai_build_v2.workflows.develop.execution.workspace_authority import (
    CanonicalRepoRegistry,
    RepoIdentity,
    WorkspaceAuthority,
)


FEATURE_ID = "feature-monorepo-n18"
DAG_SHA = "dag-sha-n18"
SOURCE_DAG_SHA = "source-dag-sha-n18"
MODULE_NAME = "iriai_build_v2.workflows.develop.execution.workspace_authority"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_dir(path: Path) -> None:
    """Create a minimal .git directory (no real repo needed)."""
    (path / ".git").mkdir(parents=True, exist_ok=True)


def _monorepo_registry(feature_root: Path) -> CanonicalRepoRegistry:
    """Return a one-repo registry where workspace_relative_path='.'  (monorepo)."""
    canonical_path = str(feature_root)
    repo = RepoIdentity(
        repo_id="mono-repo-id",
        repo_name="repos",
        role="primary",
        workspace_relative_path=".",
        canonical_path=canonical_path,
        identity_kind="source_path",
        identity_value=canonical_path,
        safety_status="ok",
        identity_evidence_digest="identity:mono-repo-id",
    )
    return CanonicalRepoRegistry(
        feature_id=FEATURE_ID,
        feature_slug="mono-n18",
        feature_root=str(feature_root),
        repos=[repo],
        aliases={},
        registry_digest="registry:digest:mono",
    )


def _task(
    task_id: str,
    repo_path: str,
    *file_scope_paths: str,
    action: str = "modify",
) -> ImplementationTask:
    scopes = [TaskFileScope(path=p, action=action) for p in file_scope_paths]
    return ImplementationTask(
        id=task_id,
        name=task_id,
        description=task_id,
        repo_path=repo_path,
        files=list(file_scope_paths),
        file_scope=scopes,
    )


def _compile_request(
    registry: CanonicalRepoRegistry,
    task: ImplementationTask,
) -> ContractCompileRequest:
    return ContractCompileRequest(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA,
        source_dag_artifact_id=1,
        source_dag_sha256=SOURCE_DAG_SHA,
        group_idx=0,
        task=task,
        all_task_ids=[task.id],
        workspace_registry=registry,
    )


# ---------------------------------------------------------------------------
# Fix 3: contract repo_path is resolved workspace_relative_path, not raw abs
# ---------------------------------------------------------------------------


def test_compile_task_absolute_repo_path_uses_resolved_workspace_relative_path(
    tmp_path: Path,
) -> None:
    """Fix 3: contract.repo_path must be '.' not the raw absolute path from planning."""
    feature_root = tmp_path / "feature" / "repos"
    feature_root.mkdir(parents=True)
    registry = _monorepo_registry(feature_root)
    abs_repo_path = str(feature_root)  # absolute, as planning emits for monorepo

    task = _task(
        "TASK-FDS-S1-01",
        abs_repo_path,
        "supply-chain/src/service.py",
    )
    compiler = ContractCompiler()
    request = _compile_request(registry, task)
    contract = compiler.compile_task(request)

    # The contract repo_path must be the registry's workspace_relative_path ("."),
    # NOT the raw absolute path that planning put on the task.
    assert contract.repo_path == ".", (
        f"Expected contract.repo_path='.' but got {contract.repo_path!r}; "
        "absolute repo_path leaked from task into contract (dispatch would mismatch)"
    )
    assert not Path(contract.repo_path).is_absolute(), (
        f"contract.repo_path must not be absolute; got {contract.repo_path!r}"
    )


def test_compile_task_relative_repo_path_preserved(tmp_path: Path) -> None:
    """Fix 3 guard: a normal relative repo_path is still used in the contract."""
    feature_root = tmp_path / "feature" / "repos"
    child = feature_root / "app"
    child.mkdir(parents=True)
    repo = RepoIdentity(
        repo_id="app-repo-id",
        repo_name="app",
        role="primary",
        workspace_relative_path="app",
        canonical_path=str(child),
        identity_kind="source_path",
        identity_value=str(child),
        safety_status="ok",
        identity_evidence_digest="identity:app-repo-id",
    )
    registry = CanonicalRepoRegistry(
        feature_id=FEATURE_ID,
        feature_slug="mono-n18",
        feature_root=str(feature_root),
        repos=[repo],
        aliases={},
        registry_digest="registry:digest:polyrepo",
    )
    task = _task("TASK-APP", "app", "app/src/main.py")
    compiler = ContractCompiler()
    request = _compile_request(registry, task)
    contract = compiler.compile_task(request)

    assert contract.repo_path == "app", (
        f"Relative repo_path should be preserved; got {contract.repo_path!r}"
    )


def test_compile_task_empty_repo_path_uses_workspace_relative_path(tmp_path: Path) -> None:
    """Fix 3 guard: empty repo_path falls through to workspace_relative_path from registry."""
    feature_root = tmp_path / "feature" / "repos"
    feature_root.mkdir(parents=True)
    registry = _monorepo_registry(feature_root)

    # repo_path is empty — as planning emits for share-link-defaults / E2E-HARNESS
    task = _task("TASK-EMPTY-REPO", "", "spend-client/e2e/test.spec.ts", action="create")
    compiler = ContractCompiler()
    request = _compile_request(registry, task)
    contract = compiler.compile_task(request)

    assert contract.repo_path == ".", (
        f"Empty repo_path should fall back to registry workspace_relative_path '.'; "
        f"got {contract.repo_path!r}"
    )


# ---------------------------------------------------------------------------
# Fix 4: _candidate_root_from_file_path — monorepo candidate discovery
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_authority_module():
    return importlib.import_module(MODULE_NAME)


async def _call(authority, method_name: str, *args, **kwargs):
    result = getattr(authority, method_name)(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def test_candidate_root_from_file_path_monorepo_relative_path(
    workspace_authority_module, tmp_path: Path
) -> None:
    """Fix 4: relative file path returns repo_parent when repo_parent/.git exists."""
    repo_parent = tmp_path / "feature" / "repos"
    _make_git_dir(repo_parent)

    authority = WorkspaceAuthority(feature_root=repo_parent)
    result = authority._candidate_root_from_file_path(
        "supply-chain/src/service.py",
        repo_parent,
    )

    assert result is not None, (
        "_candidate_root_from_file_path must return repo_parent for monorepo relative path"
    )
    assert result.resolve() == repo_parent.resolve(), (
        f"Expected repo_parent {repo_parent!r}; got {result!r}"
    )


def test_candidate_root_from_file_path_polyrepo_child_git(tmp_path: Path) -> None:
    """Fix 4 guard: relative path with first-segment child .git still works (polyrepo)."""
    repo_parent = tmp_path / "feature" / "repos"
    child = repo_parent / "app"
    _make_git_dir(child)
    # repo_parent itself has no .git

    authority = WorkspaceAuthority(feature_root=repo_parent)
    result = authority._candidate_root_from_file_path("app/src/main.py", repo_parent)

    assert result is not None
    assert result.resolve() == child.resolve()


def test_candidate_root_from_file_path_monorepo_no_match_outside(tmp_path: Path) -> None:
    """Fix 4 guard: absolute path outside repo_parent still returns None."""
    repo_parent = tmp_path / "feature" / "repos"
    _make_git_dir(repo_parent)
    outside = tmp_path / "other"

    authority = WorkspaceAuthority(feature_root=repo_parent)
    result = authority._candidate_root_from_file_path(
        str(outside / "src" / "foo.py"),
        repo_parent,
    )

    assert result is None, (
        "Path outside repo_parent must not return a candidate"
    )


@pytest.mark.asyncio
async def test_workspace_authority_build_registry_monorepo_empty_task_repo_path(
    workspace_authority_module,
    tmp_path: Path,
) -> None:
    """Fix 4 integration: build_registry assigns empty-repo_path tasks to monorepo root.

    In a monorepo layout (.git at repos/), a task with no repo_path but with
    a relative file_scope path (e.g. "spend-client/e2e/test.spec.ts") must
    have its candidate resolved to the repos root — not left unresolved, which
    would result in the task being absent from writable_task_ids and causing a
    'registry repo does not claim task' dispatch error.
    """
    repo_parent = tmp_path / "feature" / "repos"
    _make_git_dir(repo_parent)
    # Create the file path that the task touches (so candidate matching works)
    target = repo_parent / "spend-client" / "e2e" / "test.spec.ts"
    target.parent.mkdir(parents=True)
    target.write_text("// stub\n", encoding="utf-8")

    # Task with empty repo_path but a relative file path
    task = ImplementationTask(
        id="TASK-E2E",
        name="TASK-E2E",
        description="E2E harness task with no repo_path",
        repo_path="",
        files=["spend-client/e2e/test.spec.ts"],
        file_scope=[TaskFileScope(path="spend-client/e2e/test.spec.ts", action="create")],
    )
    registry_row = {
        "repo_id": "mono-repo-id",
        "repo_name": "repos",
        "workspace_relative_path": ".",
        "canonical_path": str(repo_parent),
    }
    authority = WorkspaceAuthority(
        feature_root=repo_parent,
        registry_repos=[registry_row],
    )
    registry = await authority.build_registry(
        feature_id=FEATURE_ID,
        tasks=[task],
        feature_root=repo_parent,
    )

    # The task must appear in writable_task_ids so dispatch does not fail with
    # "registry repo does not claim task".
    writable_ids = getattr(registry, "writable_task_ids", None)
    if writable_ids is None:
        # Fallback: check via repos
        all_text = str(registry.model_dump(mode="json") if hasattr(registry, "model_dump") else registry)
        assert "TASK-E2E" in all_text, (
            f"Expected TASK-E2E in registry; got {all_text[:500]}"
        )
    else:
        assert "TASK-E2E" in writable_ids, (
            f"TASK-E2E not in writable_task_ids={writable_ids}; "
            "monorepo fix 4 did not wire empty-repo_path tasks to repo root"
        )
