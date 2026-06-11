"""Unit tests for sandbox clone-cost optimisations (wc/sandbox-clone-cost).

Covers:
1. Fast local clone — ``_git_clone_args`` / ``_same_filesystem`` /
   ``_sandbox_local_clone_enabled``
2. Sandbox reuse on retry — ``SandboxSpec.idempotency_key`` with
   ``IRIAI_SANDBOX_REUSE_ON_RETRY`` flag
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.execution.sandbox import (
    SandboxSpec,
    _git_clone_args,
    _same_filesystem,
    _sandbox_local_clone_enabled,
    _sandbox_reuse_on_retry_enabled,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec(**overrides: object) -> SandboxSpec:
    """Minimal valid SandboxSpec for testing idempotency_key variants."""
    base: dict[str, object] = {
        "feature_id": "Feature/Test",
        "dag_sha256": "aaaa" * 16,
        "group_idx": 0,
        "attempt_no": 0,
        "task_ids": ["task-x"],
        "repo_ids": ["repo"],
        "base_snapshot_ids": [1],
        "base_commits": {"repo": "abc123"},
        "mode": "task",
        "writable_roots": [],
        "readonly_roots": [],
        "contract_ids": [1],
    }
    base.update(overrides)
    return SandboxSpec(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1a. _sandbox_local_clone_enabled()
# ---------------------------------------------------------------------------

class TestSandboxLocalCloneEnabled:
    def test_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Local clone is enabled by default (env var absent)."""
        monkeypatch.delenv("IRIAI_SANDBOX_LOCAL_CLONE", raising=False)
        assert _sandbox_local_clone_enabled() is True

    def test_disabled_by_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "0")
        assert _sandbox_local_clone_enabled() is False

    def test_disabled_by_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "false")
        assert _sandbox_local_clone_enabled() is False

    def test_enabled_by_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "1")
        assert _sandbox_local_clone_enabled() is True

    def test_enabled_by_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "true")
        assert _sandbox_local_clone_enabled() is True


# ---------------------------------------------------------------------------
# 1b. _sandbox_reuse_on_retry_enabled()
# ---------------------------------------------------------------------------

class TestSandboxReuseOnRetryEnabled:
    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry reuse is off by default (env var absent)."""
        monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
        assert _sandbox_reuse_on_retry_enabled() is False

    def test_disabled_by_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "0")
        assert _sandbox_reuse_on_retry_enabled() is False

    def test_enabled_by_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "1")
        assert _sandbox_reuse_on_retry_enabled() is True

    def test_enabled_by_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "true")
        assert _sandbox_reuse_on_retry_enabled() is True

    def test_enabled_by_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "yes")
        assert _sandbox_reuse_on_retry_enabled() is True


# ---------------------------------------------------------------------------
# 1c. _same_filesystem()
# ---------------------------------------------------------------------------

class TestSameFilesystem:
    def test_same_dir_is_same(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        assert _same_filesystem(a, b) is True

    def test_same_as_parent(self, tmp_path: Path) -> None:
        child = tmp_path / "sub" / "dir"
        assert _same_filesystem(tmp_path, child) is True

    def test_nonexistent_path_ascends(self, tmp_path: Path) -> None:
        # Neither path exists yet; should ascend to tmp_path itself
        a = tmp_path / "deep" / "nonexistent" / "a"
        b = tmp_path / "deep" / "nonexistent" / "b"
        assert _same_filesystem(a, b) is True

    def test_dev_null_is_same_as_itself(self) -> None:
        # /dev/null always exists — sanity check on a concrete real path
        dev = Path("/dev/null")
        if dev.exists():
            assert _same_filesystem(dev, dev) is True


# ---------------------------------------------------------------------------
# 1d. _git_clone_args() — clone mode selection
# ---------------------------------------------------------------------------

class TestGitCloneArgs:
    def test_local_flag_when_same_filesystem_and_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--local is selected when source and dest share the same filesystem."""
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "1")
        src = tmp_path / "source_repo"
        dst = tmp_path / "dest_repo"
        args = _git_clone_args(src, dst)
        assert args[0] == "clone"
        assert "--local" in args
        assert "--no-local" not in args
        assert str(src) in args
        assert str(dst) in args

    def test_no_local_flag_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--no-local is used when IRIAI_SANDBOX_LOCAL_CLONE=0."""
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "0")
        src = tmp_path / "source_repo"
        dst = tmp_path / "dest_repo"
        args = _git_clone_args(src, dst)
        assert "--no-local" in args
        assert "--local" not in args

    def test_default_selects_local_on_same_volume(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default (env absent) picks --local for same-volume paths."""
        monkeypatch.delenv("IRIAI_SANDBOX_LOCAL_CLONE", raising=False)
        src = tmp_path / "source_repo"
        dst = tmp_path / "dest_repo"
        args = _git_clone_args(src, dst)
        # Both paths share tmp_path volume → --local
        assert "--local" in args

    def test_arg_shape_is_passable_to_git(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is a plain list[str] starting with 'clone'."""
        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "1")
        src = tmp_path / "s"
        dst = tmp_path / "d"
        args = _git_clone_args(src, dst)
        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)
        assert args[0] == "clone"
        # four elements: clone, <flag>, <src>, <dst>
        assert len(args) == 4


# ---------------------------------------------------------------------------
# 2. SandboxSpec.idempotency_key — reuse-on-retry behaviour
# ---------------------------------------------------------------------------

class TestSandboxSpecIdempotencyKey:
    def test_default_different_attempt_gives_different_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without IRIAI_SANDBOX_REUSE_ON_RETRY, attempt_no is in the key."""
        monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
        spec0 = _spec(attempt_no=0)
        spec1 = _spec(attempt_no=1)
        assert spec0.idempotency_key != spec1.idempotency_key

    def test_reuse_enabled_same_attempt_same_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With reuse on, both attempt_no values produce the same key."""
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "1")
        spec0 = _spec(attempt_no=0)
        spec1 = _spec(attempt_no=1)
        assert spec0.idempotency_key == spec1.idempotency_key

    def test_reuse_enabled_different_dag_sha_gives_different_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Content-distinct tasks still get distinct keys even with reuse on."""
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "1")
        spec_a = _spec(attempt_no=0, dag_sha256="aaaa" * 16)
        spec_b = _spec(attempt_no=0, dag_sha256="bbbb" * 16)
        assert spec_a.idempotency_key != spec_b.idempotency_key

    def test_reuse_disabled_by_env_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IRIAI_SANDBOX_REUSE_ON_RETRY=0 keeps attempt_no in the key."""
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "0")
        spec0 = _spec(attempt_no=0)
        spec1 = _spec(attempt_no=1)
        assert spec0.idempotency_key != spec1.idempotency_key

    def test_key_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Key always starts with 'idem:sandbox:'."""
        monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
        spec = _spec(attempt_no=0)
        assert spec.idempotency_key.startswith("idem:sandbox:")

    def test_reuse_same_attempt_no_same_key_both_modes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Identical specs always produce the same key regardless of reuse flag."""
        for flag in ("0", "1"):
            monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", flag)
            spec_a = _spec(attempt_no=3)
            spec_b = _spec(attempt_no=3)
            assert spec_a.idempotency_key == spec_b.idempotency_key, f"flag={flag}"

    def test_reuse_different_contract_ids_different_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with reuse on, different contract_ids produce different keys."""
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "1")
        spec_a = _spec(attempt_no=1, contract_ids=[1])
        spec_b = _spec(attempt_no=1, contract_ids=[2])
        assert spec_a.idempotency_key != spec_b.idempotency_key

    def test_reuse_different_base_commit_different_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different base commits are still distinct under reuse mode."""
        monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "1")
        spec_a = _spec(attempt_no=0, base_commits={"repo": "commit-aaa"})
        spec_b = _spec(attempt_no=0, base_commits={"repo": "commit-bbb"})
        assert spec_a.idempotency_key != spec_b.idempotency_key


# ---------------------------------------------------------------------------
# Integration: _git_clone_args is used in the real allocate() flow
# (smoke-test: ensure the allocation path produces a --local clone on
# same-volume tmp dirs when git is available).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
class TestLocalCloneIntegration:
    def test_local_clone_produces_valid_working_tree(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """git clone --local actually works and produces a valid repo."""
        import subprocess

        monkeypatch.setenv("IRIAI_SANDBOX_LOCAL_CLONE", "1")
        src = tmp_path / "source"
        src.mkdir()
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=src, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=src, check=True, capture_output=True,
        )
        (src / "file.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=src, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "init"], cwd=src, check=True, capture_output=True
        )

        dst = tmp_path / "clone"
        args = _git_clone_args(src, dst)
        assert "--local" in args

        subprocess.run(["git", *args], check=True, capture_output=True)
        assert (dst / "file.txt").exists()
        result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=dst, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
