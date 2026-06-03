"""Unit tests for CloneSubstrate over synthetic local git repos (fast)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.e2e.substrate import (
    CloneSubstrate,
    SubstrateError,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _make_repo(path: Path) -> tuple[str, str]:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "A.txt").write_text("a")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "A")
    sha_a = _git(path, "rev-parse", "HEAD")
    (path / "B.txt").write_text("b")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "B")
    sha_b = _git(path, "rev-parse", "HEAD")
    return sha_a, sha_b


@pytest.mark.asyncio
async def test_clone_checkout_detaches_at_commit(tmp_path):
    src = tmp_path / "src_repo"
    sha_a, sha_b = _make_repo(src)
    sub = CloneSubstrate(
        run_id="run1", base_dir=tmp_path / "scratch", nice=False
    )
    try:
        checkouts = await sub.clone_checkpoint(
            sources={"repo": str(src)}, commits={"repo": sha_a}
        )
        co = checkouts["repo"]
        # checked out at A: A.txt present, B.txt absent, detached HEAD == sha_a
        assert (co.checkout_dir / "A.txt").exists()
        assert not (co.checkout_dir / "B.txt").exists()
        head = _git(co.checkout_dir, "rev-parse", "HEAD")
        assert head == sha_a
        # independent object store: clone is out-of-tree, source .git untouched
        assert str(co.checkout_dir).startswith(str(tmp_path / "scratch"))
        assert not (src / ".git" / "worktrees").exists()
    finally:
        await sub.teardown()
    # teardown removed the run dir
    assert not sub.run_dir.exists()


@pytest.mark.asyncio
async def test_multi_repo_clone(tmp_path):
    s1 = tmp_path / "r1"
    s2 = tmp_path / "r2"
    a1, _ = _make_repo(s1)
    a2, b2 = _make_repo(s2)
    sub = CloneSubstrate(run_id="m", base_dir=tmp_path / "sc", nice=False)
    try:
        cos = await sub.clone_checkpoint(
            sources={"r1": str(s1), "r2": str(s2)},
            commits={"r1": a1, "r2": b2},
        )
        assert set(cos) == {"r1", "r2"}
        assert _git(cos["r1"].checkout_dir, "rev-parse", "HEAD") == a1
        assert _git(cos["r2"].checkout_dir, "rev-parse", "HEAD") == b2
    finally:
        await sub.teardown()


def test_alloc_port_is_free_int():
    p = CloneSubstrate.alloc_port()
    assert isinstance(p, int) and 1024 < p < 65536


@pytest.mark.asyncio
async def test_refuses_to_provision_inside_live_repo(tmp_path):
    src = tmp_path / "live"
    _make_repo(src)
    # run dir placed *inside* the source -> must refuse
    sub = CloneSubstrate(run_id="x", base_dir=src / "nested", nice=False)
    with pytest.raises(SubstrateError):
        await sub.clone_checkpoint(sources={"r": str(src)}, commits={"r": "HEAD"})


def test_gc_removes_only_stale_run_dirs(tmp_path):
    import os
    import time

    base = tmp_path / "scratch"
    stale = base / "track" / "old-run"
    stale.mkdir(parents=True)
    (stale / "pids.json").write_text("[]")
    # make it genuinely old (older than max_age)
    old = time.time() - 10 * 3600
    os.utime(stale, (old, old))
    recent = base / "track" / "recent-run"  # a recent sibling — must survive
    recent.mkdir(parents=True)
    keep = base / "track" / "keep-run"
    keep.mkdir(parents=True)
    removed = CloneSubstrate.gc_stale(
        role="track", base_dir=base, keep_run_id="keep-run"
    )
    assert any("old-run" in r for r in removed)
    assert not stale.exists()
    # the fix: recent siblings + the current run are NEVER deleted
    assert recent.exists()
    assert keep.exists()
