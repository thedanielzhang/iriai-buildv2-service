"""Keep orchestrator-written planning artifacts (<repo>/.iriai/) out of git so
they never enter a PR, while remaining on disk. Pure git over a temp repo."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.execution.git_service import (
    changed_path_set,
    exclude_iriai_from_git,
)

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git is required"
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_excludes_iriai_from_untracked_and_changed_set(tmp_path):
    repo = _repo(tmp_path)
    # orchestrator planning docs + a real code change, both untracked
    (repo / ".iriai" / "artifacts" / "features" / "f1").mkdir(parents=True)
    (repo / ".iriai" / "artifacts" / "features" / "f1" / "prd.md").write_text("x")
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text("export const x = 1")

    exclude_iriai_from_git(repo)

    others = _git(repo, "ls-files", "--others", "--exclude-standard")
    assert "src/app.ts" in others
    assert ".iriai" not in others  # planning docs hidden from git

    changed = asyncio.run(changed_path_set(repo))
    assert "src/app.ts" in changed
    assert not any(p.startswith(".iriai/") for p in changed)

    exclude_file = (repo / ".git" / "info" / "exclude").read_text()
    assert "/.iriai/" in exclude_file


def test_idempotent(tmp_path):
    repo = _repo(tmp_path)
    exclude_iriai_from_git(repo)
    exclude_iriai_from_git(repo)
    exclude_file = (repo / ".git" / "info" / "exclude").read_text()
    assert exclude_file.count("/.iriai/") == 1


def test_anchored_does_not_match_nested_iriai(tmp_path):
    # Anchored "/.iriai/" must only match the repo-root .iriai, not a vendored
    # nested path of the same name.
    repo = _repo(tmp_path)
    (repo / "vendor" / "pkg" / ".iriai").mkdir(parents=True)
    (repo / "vendor" / "pkg" / ".iriai" / "keep.txt").write_text("vendored")
    exclude_iriai_from_git(repo)
    others = _git(repo, "ls-files", "--others", "--exclude-standard")
    assert "vendor/pkg/.iriai/keep.txt" in others  # nested one NOT excluded


def test_noop_when_not_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    exclude_iriai_from_git(plain)  # must not raise
    assert not (plain / ".git").exists()


def test_preserves_existing_exclude_entries(tmp_path):
    repo = _repo(tmp_path)
    exclude_path = repo / ".git" / "info" / "exclude"
    exclude_path.write_text("# custom\n*.log\n", encoding="utf-8")
    exclude_iriai_from_git(repo)
    text = exclude_path.read_text()
    assert "*.log" in text and "/.iriai/" in text
