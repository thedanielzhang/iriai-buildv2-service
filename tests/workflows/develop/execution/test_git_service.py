from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.execution.git_service import (
    GitError,
    apply_check,
    apply_patch,
    build_commit_message,
    changed_path_set,
    clean_untracked,
    commit,
    head_commit,
    head_tree,
    is_ancestor,
    patch_path_set,
    porcelain_status,
    reset_hard,
    resolve_commit,
    run_git,
    stage_paths,
    staged_paths,
    unstaged_paths,
    untracked_paths,
    working_tree_clean,
)


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _git(repo_path, "init", "-q")
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Test User")
    _git(repo_path, "config", "commit.gpgsign", "false")
    (repo_path / "README.md").write_text("init\n")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "-q", "-m", "initial")
    return repo_path


@pytest.mark.asyncio
async def test_run_git_returns_result_and_raises_on_failure(repo: Path) -> None:
    result = await run_git(repo, "rev-parse", "--git-dir")
    assert result.ok
    assert result.stdout.strip()

    with pytest.raises(GitError):
        await run_git(repo, "rev-parse", "--verify", "does-not-exist")

    soft = await run_git(
        repo, "rev-parse", "--verify", "does-not-exist", check=False
    )
    assert not soft.ok
    assert soft.returncode != 0


@pytest.mark.asyncio
async def test_head_commit_and_tree(repo: Path) -> None:
    commit_sha = await head_commit(repo)
    tree_sha = await head_tree(repo)
    assert len(commit_sha) == 40
    assert len(tree_sha) == 40
    assert commit_sha != tree_sha
    assert await resolve_commit(repo, "HEAD") == commit_sha


@pytest.mark.asyncio
async def test_is_ancestor(repo: Path) -> None:
    base = await head_commit(repo)
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "second")
    head = await head_commit(repo)

    assert await is_ancestor(repo, base, head) is True
    assert await is_ancestor(repo, head, base) is False


@pytest.mark.asyncio
async def test_working_tree_clean_detects_dirty_states(repo: Path) -> None:
    assert await working_tree_clean(repo) is True

    target = repo / "README.md"
    target.write_text("init\nmodified\n")
    assert await working_tree_clean(repo) is False

    _git(repo, "checkout", "--", "README.md")
    assert await working_tree_clean(repo) is True

    (repo / "untracked.txt").write_text("new\n")
    assert await working_tree_clean(repo) is False


@pytest.mark.asyncio
async def test_path_sets_staged_unstaged_untracked(repo: Path) -> None:
    (repo / "staged.txt").write_text("s\n")
    _git(repo, "add", "staged.txt")
    (repo / "README.md").write_text("init\nedit\n")
    (repo / "untracked.txt").write_text("u\n")

    assert "staged.txt" in await staged_paths(repo)
    assert "README.md" in await unstaged_paths(repo)
    assert "untracked.txt" in await untracked_paths(repo)

    combined = await changed_path_set(repo)
    assert {"staged.txt", "README.md", "untracked.txt"} <= combined


@pytest.mark.asyncio
async def test_apply_patch_applies_captured_diff(repo: Path) -> None:
    target = repo / "README.md"
    target.write_text("init\nappended\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")
    assert target.read_text() == "init\n"

    check = await apply_check(repo, patch)
    assert check.applied is True

    result = await apply_patch(repo, patch)
    assert result.applied is True
    assert target.read_text() == "init\nappended\n"


@pytest.mark.asyncio
async def test_apply_check_rejects_nonapplying_patch(repo: Path) -> None:
    bad = (
        "diff --git a/missing.txt b/missing.txt\n"
        "--- a/missing.txt\n"
        "+++ b/missing.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    check = await apply_check(repo, bad)
    assert check.applied is False
    assert check.returncode != 0


@pytest.mark.asyncio
async def test_reset_hard_restores_recorded_head(repo: Path) -> None:
    base = await head_commit(repo)
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "second")
    assert await head_commit(repo) != base

    await reset_hard(repo, base)
    assert await head_commit(repo) == base
    assert not (repo / "f.txt").exists()


@pytest.mark.asyncio
async def test_stage_paths_and_commit_records_commit_and_tree(repo: Path) -> None:
    (repo / "feature.py").write_text("print('hi')\n")
    await stage_paths(repo, ["feature.py"])
    assert "feature.py" in await staged_paths(repo)

    result = await commit(
        repo,
        build_commit_message(3, ["task one"], {"Feature-ID": "feat-x"}),
    )
    assert result.committed is True
    assert result.commit == await head_commit(repo)
    assert result.tree == await head_tree(repo)
    assert result.hook_failure is None
    assert await working_tree_clean(repo) is True


@pytest.mark.asyncio
async def test_commit_returns_hook_failure_without_raising(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'lint error: bad style' >&2\nexit 1\n")
    hook.chmod(0o755)

    (repo / "feature.py").write_text("x\n")
    await stage_paths(repo, ["feature.py"])
    before = await head_commit(repo)

    result = await commit(repo, "feat: should be blocked")
    assert result.committed is False
    assert result.commit == ""
    assert result.hook_failure is not None
    assert result.hook_failure.returncode != 0
    assert "lint error" in result.hook_failure.stderr
    assert await head_commit(repo) == before


@pytest.mark.asyncio
async def test_clean_untracked_removes_only_named_paths(repo: Path) -> None:
    (repo / "residue.txt").write_text("r\n")
    (repo / "keep.txt").write_text("k\n")

    await clean_untracked(repo, ["residue.txt"])
    assert not (repo / "residue.txt").exists()
    assert (repo / "keep.txt").exists()


def test_build_commit_message_includes_title_and_trailers() -> None:
    msg = build_commit_message(
        7, ["alpha", "beta"], {"Feature-ID": "f1", "DAG-SHA256": "abc"}
    )
    assert msg.splitlines()[0] == "feat: group 7 - alpha, beta"
    assert "Feature-ID: f1" in msg
    assert "DAG-SHA256: abc" in msg

    assert build_commit_message(2, []) == "feat: group 2"


def test_patch_path_set_handles_modify_add_delete_rename() -> None:
    modify = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 111..222 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    assert patch_path_set(modify) == ["src/app.py"]

    add = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "index 000..222\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n+x\n"
    )
    assert patch_path_set(add) == ["new.txt"]

    delete = (
        "diff --git a/gone.txt b/gone.txt\n"
        "deleted file mode 100644\n"
        "--- a/gone.txt\n"
        "+++ /dev/null\n"
    )
    assert patch_path_set(delete) == ["gone.txt"]

    rename = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    assert patch_path_set(rename) == ["new.py", "old.py"]


@pytest.mark.asyncio
async def test_three_way_conflict_passes_check_but_fails_apply(repo: Path) -> None:
    target = repo / "README.md"
    target.write_text("line1\nline2\nline3\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "three lines")

    # Capture a patch that rewrites line2, then revert the worktree.
    target.write_text("line1\nPATCH-CHANGE\nline3\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    # Advance HEAD with a conflicting change to the same line.
    target.write_text("line1\nHEAD-CHANGE\nline3\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "conflicting change")

    # apply --check --3way reports success (git *can* 3-way merge it) ...
    check = await apply_check(repo, patch)
    assert check.applied is True
    # ... but the real apply produces conflict markers and exits non-zero,
    # which is the authoritative merge_conflict signal for the queue.
    result = await apply_patch(repo, patch)
    assert result.applied is False
    assert result.returncode != 0


@pytest.mark.asyncio
async def test_porcelain_status_handles_staged_rename(repo: Path) -> None:
    _git(repo, "mv", "README.md", "DOC.md")
    records = await porcelain_status(repo)
    # A rename is exactly one porcelain v2 record; the -z form would mis-split
    # it into two NUL-separated path fields.
    assert len(records) == 1
    assert await working_tree_clean(repo) is False

    result = await commit(repo, "rename readme")
    assert result.committed is True
    assert await working_tree_clean(repo) is True


@pytest.mark.asyncio
async def test_stage_paths_handles_deleted_tracked_path(repo: Path) -> None:
    (repo / "README.md").unlink()
    await stage_paths(repo, ["README.md"])  # must not raise on a deletion
    assert "README.md" in await staged_paths(repo)
