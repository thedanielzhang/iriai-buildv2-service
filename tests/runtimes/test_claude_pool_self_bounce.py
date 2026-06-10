"""Runner self-bounce-on-stale-code (W-8).

Covers the staleness detector (commit-sha based, plain file reads), the
drain gate (never exit while a claimed job runs), the per-profile stagger,
and the env opt-out.  The launchd KeepAlive relaunch itself is
manual-verify only — these tests stop at the SystemExit(0) boundary.
"""

from __future__ import annotations

import asyncio
import shutil
import zlib
from contextlib import suppress
from pathlib import Path

import pytest

import iriai_build_v2.runtimes.claude_pool as claude_pool
from iriai_build_v2.runtimes.claude_pool import (
    SELF_BOUNCE_ENV_VAR,
    SELF_BOUNCE_SOURCE_REPO_ENV_VAR,
    SELF_BOUNCE_STAGGER_MODULO_SECONDS,
    ClaudePoolRunner,
    _job_state_path,
    _read_git_head_sha,
    _RunnerSelfBounceMonitor,
    _self_bounce_stagger_seconds,
    _write_json_atomic,
)

_SHA_A = "a" * 40
_SHA_B = "b" * 40


@pytest.fixture(autouse=True)
def _clean_self_bounce_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SELF_BOUNCE_ENV_VAR, raising=False)
    monkeypatch.delenv(SELF_BOUNCE_SOURCE_REPO_ENV_VAR, raising=False)


def _init_symbolic_repo(repo: Path, sha: str, *, ref: str = "refs/heads/main") -> Path:
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text(f"ref: {ref}\n", encoding="utf-8")
    ref_path = git_dir / ref
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(f"{sha}\n", encoding="utf-8")
    return repo


def _init_detached_repo(repo: Path, sha: str) -> Path:
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text(f"{sha}\n", encoding="utf-8")
    return repo


def _set_symbolic_sha(repo: Path, sha: str, *, ref: str = "refs/heads/main") -> None:
    (repo / ".git" / ref).write_text(f"{sha}\n", encoding="utf-8")


def _monitor_for(
    monkeypatch: pytest.MonkeyPatch, repo: Path, profile: str = "iriai-claude-1"
) -> _RunnerSelfBounceMonitor:
    monkeypatch.setenv(SELF_BOUNCE_SOURCE_REPO_ENV_VAR, str(repo))
    return _RunnerSelfBounceMonitor(profile)


def _runner_for(
    monkeypatch: pytest.MonkeyPatch, repo: Path, pool_root: Path
) -> ClaudePoolRunner:
    monkeypatch.setenv(SELF_BOUNCE_SOURCE_REPO_ENV_VAR, str(repo))
    return ClaudePoolRunner(profile="iriai-claude-1", root=pool_root)


# --- staleness detector -----------------------------------------------------


def test_same_sha_never_flags_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    monitor = _monitor_for(monkeypatch, repo)
    assert monitor.enabled
    assert monitor.startup_sha == _SHA_A
    for _ in range(3):
        assert monitor.check_stale() is False
    assert monitor.stale is False


def test_changed_sha_flags_stale_and_latches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    monitor = _monitor_for(monkeypatch, repo)
    assert monitor.check_stale() is False
    _set_symbolic_sha(repo, _SHA_B)
    assert monitor.check_stale() is True
    assert monitor.current_sha == _SHA_B
    # Latches: even if the sha flips back, the loaded code is still stale.
    _set_symbolic_sha(repo, _SHA_A)
    assert monitor.check_stale() is True


def test_detached_head_plain_sha_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_detached_repo(tmp_path / "repo", _SHA_A)
    monitor = _monitor_for(monkeypatch, repo)
    assert monitor.enabled
    assert monitor.startup_sha == _SHA_A
    assert monitor.check_stale() is False
    (repo / ".git" / "HEAD").write_text(f"{_SHA_B}\n", encoding="utf-8")
    assert monitor.check_stale() is True


def test_packed_ref_resolved_without_loose_ref_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{_SHA_A} refs/heads/main\n"
        f"^{_SHA_B}\n",
        encoding="utf-8",
    )
    assert _read_git_head_sha(repo) == _SHA_A


def test_gitdir_file_checkout_resolved(tmp_path: Path) -> None:
    # Worktree-style checkout: .git is a file pointing at the real git dir.
    real = tmp_path / "real-gitdir"
    real.mkdir(parents=True)
    (real / "HEAD").write_text(f"{_SHA_A}\n", encoding="utf-8")
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    assert _read_git_head_sha(repo) == _SHA_A


def test_read_error_disables_self_bounce_for_process_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    monitor = _monitor_for(monkeypatch, repo)
    assert monitor.enabled
    # Break the sha read entirely: must NOT flag stale, must disable instead.
    shutil.rmtree(repo / ".git")
    assert monitor.check_stale() is False
    assert monitor.enabled is False
    # Even after the repo comes back with a new sha, stays disabled (no
    # bounce-loop risk for the rest of this process).
    _init_symbolic_repo(repo, _SHA_B)
    assert monitor.check_stale() is False


def test_unresolvable_startup_sha_disables_monitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv(SELF_BOUNCE_SOURCE_REPO_ENV_VAR, str(repo))  # no .git
    monitor = _RunnerSelfBounceMonitor("iriai-claude-1")
    assert monitor.enabled is False
    assert monitor.check_stale() is False


def test_stagger_is_deterministic_bounded_and_distinct_per_profile() -> None:
    names = ["iriai-claude-1", "iriai-claude-2", "iriai-claude-3"]
    staggers = [_self_bounce_stagger_seconds(name) for name in names]
    assert staggers == [_self_bounce_stagger_seconds(name) for name in names]
    assert all(0 <= s < SELF_BOUNCE_STAGGER_MODULO_SECONDS for s in staggers)
    assert len(set(staggers)) == len(names)
    assert staggers == [zlib.crc32(n.encode("utf-8")) % 120 for n in names]


# --- drain gate + exit ------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_gate_blocks_exit_until_claimed_job_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    runner = _runner_for(monkeypatch, repo, tmp_path / "pool")
    runner._self_bounce.stagger_seconds = 0
    _set_symbolic_sha(repo, _SHA_B)

    job = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    runner._active["job-1"] = job
    # Stale + claimed job running: must NOT exit.
    await runner._maybe_self_bounce()
    assert runner._self_bounce.stale is True

    # Job finishes -> next tick exits 0.
    job.cancel()
    with suppress(asyncio.CancelledError):
        await job
    runner._active.pop("job-1")
    with pytest.raises(SystemExit) as excinfo:
        await runner._maybe_self_bounce()
    assert excinfo.value.code == 0


@pytest.mark.asyncio
async def test_stale_idle_runner_exits_after_stagger_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    runner = _runner_for(monkeypatch, repo, tmp_path / "pool")
    _set_symbolic_sha(repo, _SHA_B)

    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(claude_pool.asyncio, "sleep", _fake_sleep)
    with pytest.raises(SystemExit) as excinfo:
        await runner._maybe_self_bounce()
    assert excinfo.value.code == 0
    assert delays == [runner._self_bounce.stagger_seconds]
    assert delays == [_self_bounce_stagger_seconds("iriai-claude-1")]


@pytest.mark.asyncio
async def test_stale_runner_declines_to_claim_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    pool_root = tmp_path / "pool"
    runner = _runner_for(monkeypatch, repo, pool_root)
    _set_symbolic_sha(repo, _SHA_B)
    assert runner._self_bounce.check_stale() is True

    job_id = "stale-decline-job"
    queued_path = _job_state_path(pool_root, "queued", "iriai-claude-1", job_id)
    _write_json_atomic(
        queued_path,
        {"id": job_id, "kind": "health", "profile": "iriai-claude-1", "status": "queued"},
    )

    await runner.run_once(wait=False)

    assert queued_path.exists()
    assert not _job_state_path(pool_root, "running", "iriai-claude-1", job_id).exists()
    assert runner._active == {}


@pytest.mark.asyncio
async def test_fresh_runner_does_not_bounce_or_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    pool_root = tmp_path / "pool"
    runner = _runner_for(monkeypatch, repo, pool_root)
    # Same sha: no exit, no decline.
    await runner._maybe_self_bounce()
    assert runner._self_bounce.stale is False


# --- env opt-out ------------------------------------------------------------


def test_opt_out_env_disables_monitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    monkeypatch.setenv(SELF_BOUNCE_ENV_VAR, "0")
    monitor = _monitor_for(monkeypatch, repo)
    assert monitor.enabled is False
    _set_symbolic_sha(repo, _SHA_B)
    assert monitor.check_stale() is False
    assert monitor.stale is False


@pytest.mark.asyncio
async def test_opt_out_env_runner_never_bounces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_symbolic_repo(tmp_path / "repo", _SHA_A)
    monkeypatch.setenv(SELF_BOUNCE_ENV_VAR, "0")
    runner = _runner_for(monkeypatch, repo, tmp_path / "pool")
    _set_symbolic_sha(repo, _SHA_B)
    await runner._maybe_self_bounce()  # must not raise SystemExit
    assert runner._self_bounce.stale is False
