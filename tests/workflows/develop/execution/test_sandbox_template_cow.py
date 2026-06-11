"""Tests for sandbox template + APFS clonefile provisioning (we/sandbox-template-cow).

Covers:
1. ``IRIAI_SANDBOX_TEMPLATE_COW`` flag reader (default ON, =0 legacy verbatim)
2. Template digest computation (lockfiles + base commit) and rebuild-on-change
3. Clonefile path selection + same-volume / non-darwin guards
4. Loud fallback to legacy full provisioning on ANY template/clonefile failure
5. Single-flight template build (one builder per digest; waiters reattach or
   fall back; stale lockdir reclaim)
6. Mid-run lockfile change -> new digest -> exactly one template rebuild
7. Real end-to-end allocate() via clonefile against a tmp git repo (skips
   cleanly when the volume does not support APFS clonefile)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import iriai_build_v2.workflows.develop.execution.sandbox as sandbox_module
from iriai_build_v2.workflows.develop.execution.sandbox import (
    SandboxRunner,
    SandboxSpec,
    _sandbox_template_cow_enabled,
    _sandbox_template_perms_enabled,
    _sandbox_template_wait_s,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.decode("utf-8").strip()


def init_repo(path: Path) -> str:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "sandbox@example.test")
    git(path, "config", "user.name", "Sandbox Test")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    (path / "package-lock.json").write_text('{"v": 1}\n', encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return git(path, "rev-parse", "HEAD")


def spec_for(base_commit: str, *, group_idx: int = 4, attempt_no: int = 2) -> SandboxSpec:
    return SandboxSpec(
        feature_id="Feature/One",
        dag_sha256="dag-sha",
        group_idx=group_idx,
        attempt_no=attempt_no,
        task_ids=["task-a"],
        repo_ids=["app"],
        base_snapshot_ids=[11],
        base_commits={"app": base_commit},
        mode="task",
        writable_roots=[],
        readonly_roots=[],
        contract_ids=[7],
    )


def runner_for(tmp_path: Path, source: Path, **kwargs) -> SandboxRunner:
    return SandboxRunner(
        workspace_root=tmp_path,
        repo_sources={"app": source},
        allowed_source_roots=[tmp_path],
        **kwargs,
    )


def template_feature_dir(tmp_path: Path) -> Path:
    return tmp_path / ".iriai" / "features" / "feature-one" / "sandbox-template"


def _clonefile_supported(tmp_path: Path) -> bool:
    """True when `cp -c` (APFS clonefile) works on this volume."""
    probe_src = tmp_path / "clonefile-probe-src"
    probe_dst = tmp_path / "clonefile-probe-dst"
    try:
        probe_src.write_text("probe\n", encoding="utf-8")
        result = subprocess.run(
            ["cp", "-c", str(probe_src), str(probe_dst)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.returncode == 0
    except (OSError, ValueError):
        return False
    finally:
        for probe in (probe_src, probe_dst):
            try:
                probe.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 1. Flag reader
# ---------------------------------------------------------------------------

class TestTemplateCowFlag:
    def test_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IRIAI_SANDBOX_TEMPLATE_COW", raising=False)
        assert _sandbox_template_cow_enabled() is True

    def test_disabled_by_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "0")
        assert _sandbox_template_cow_enabled() is False

    def test_disabled_by_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "false")
        assert _sandbox_template_cow_enabled() is False

    def test_enabled_by_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        assert _sandbox_template_cow_enabled() is True

    def test_wait_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S", raising=False)
        assert _sandbox_template_wait_s() == 900.0
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S", "5")
        assert _sandbox_template_wait_s() == 5.0
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S", "garbage")
        assert _sandbox_template_wait_s() == 900.0


# ---------------------------------------------------------------------------
# 2. Digest computation
# ---------------------------------------------------------------------------

class TestTemplateDigest:
    def test_deterministic(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        (source / "package-lock.json").write_text("{}", encoding="utf-8")
        runner = runner_for(tmp_path, source)
        d1 = runner._template_digest("app", source, "commit-a")
        d2 = runner._template_digest("app", source, "commit-a")
        assert d1 == d2
        assert len(d1) == 16

    def test_changes_on_lockfile_content_change(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        lock = source / "pnpm-lock.yaml"
        lock.write_text("a: 1\n", encoding="utf-8")
        runner = runner_for(tmp_path, source)
        before = runner._template_digest("app", source, "commit-a")
        lock.write_text("a: 2\n", encoding="utf-8")
        after = runner._template_digest("app", source, "commit-a")
        assert before != after

    def test_changes_on_new_lockfile(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        runner = runner_for(tmp_path, source)
        before = runner._template_digest("app", source, "commit-a")
        (source / "uv.lock").write_text("lock\n", encoding="utf-8")
        after = runner._template_digest("app", source, "commit-a")
        assert before != after

    def test_changes_on_base_commit_change(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        runner = runner_for(tmp_path, source)
        assert runner._template_digest("app", source, "commit-a") != (
            runner._template_digest("app", source, "commit-b")
        )

    def test_includes_repo_id(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        runner = runner_for(tmp_path, source)
        assert runner._template_digest("app", source, "commit-a") != (
            runner._template_digest("web", source, "commit-a")
        )

    def test_requirements_glob_is_hashed(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        (source / "requirements.txt").write_text("flask\n", encoding="utf-8")
        (source / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
        runner = runner_for(tmp_path, source)
        lockfiles = runner._template_lockfiles(source)
        assert set(lockfiles) == {"requirements.txt", "requirements-dev.txt"}
        before = runner._template_digest("app", source, "c")
        (source / "requirements-dev.txt").write_text("pytest==9\n", encoding="utf-8")
        assert runner._template_digest("app", source, "c") != before

    def test_profile_package_roots_are_scanned(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        (source / "services" / "api").mkdir(parents=True)
        (source / "services" / "api" / "poetry.lock").write_text("x\n", encoding="utf-8")
        profile = SimpleNamespace(
            package_roots=["services/api"], package_managers=["poetry"]
        )
        runner = runner_for(tmp_path, source, project_profile=profile)
        lockfiles = runner._template_lockfiles(source)
        assert "services/api/poetry.lock" in lockfiles
        # No profile -> nested lockfile invisible (root-only scan)
        runner_no_profile = runner_for(tmp_path, source)
        assert "services/api/poetry.lock" not in (
            runner_no_profile._template_lockfiles(source)
        )


# ---------------------------------------------------------------------------
# 3+7. End-to-end clonefile allocation (real git repo on the test volume)
# ---------------------------------------------------------------------------

class TestClonefileAllocation:
    @pytest.fixture(autouse=True)
    def _require_clonefile(self, tmp_path: Path) -> None:
        if not _clonefile_supported(tmp_path):
            pytest.skip("APFS clonefile (cp -c) unsupported on this volume")

    def test_allocate_provisions_via_template_clonefile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        init_repo(source)
        # Gitignored dependency dir to prove provisioning is baked in.
        (source / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        git(source, "add", ".gitignore")
        git(source, "commit", "-qm", "ignore node_modules")
        base = git(source, "rev-parse", "HEAD")
        (source / "node_modules" / "left-pad").mkdir(parents=True)
        (source / "node_modules" / "left-pad" / "index.js").write_text(
            "module.exports = 1;\n", encoding="utf-8"
        )
        runner = runner_for(tmp_path, source)

        legacy_provision_calls: list[Path] = []
        original = SandboxRunner._provision_sandbox_dependencies

        def counting(self, repo_root, source_root):
            legacy_provision_calls.append(Path(repo_root))
            return original(self, repo_root, source_root)

        monkeypatch.setattr(
            SandboxRunner, "_provision_sandbox_dependencies", counting
        )

        lease = run(runner.allocate(spec_for(base)))

        # Template published with manifest, on the same volume as sandboxes.
        feature_templates = template_feature_dir(tmp_path)
        digests = [p for p in feature_templates.iterdir() if p.is_dir()]
        assert len(digests) == 1
        manifest = json.loads(
            (digests[0] / "template-manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["base_commit"] == base
        assert manifest["repo_id"] == "app"
        assert "package-lock.json" in manifest["lockfiles"]

        # Dependency provisioning ran exactly ONCE (template build, into the
        # staging dir that was atomically renamed to the digest dir), never in
        # the per-task sandbox.
        assert len(legacy_provision_calls) == 1
        assert legacy_provision_calls[0].is_relative_to(feature_templates)
        assert not legacy_provision_calls[0].is_relative_to(Path(lease.root))

        # The sandbox repo is a valid INDEPENDENT git worktree at base.
        repo_root = Path(lease.repo_roots["app"])
        assert git(repo_root, "rev-parse", "HEAD") == base
        assert git(repo_root, "status", "--porcelain=v1") == ""
        assert (repo_root / "node_modules" / "left-pad" / "index.js").is_file()
        # Independence: mutating the sandbox does not touch the template.
        (repo_root / "tracked.txt").write_text("mutated\n", encoding="utf-8")
        assert (
            (digests[0] / "repo" / "tracked.txt").read_text(encoding="utf-8")
            == "base\n"
        )

    def test_second_allocation_reuses_template_without_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        build_calls: list[str] = []
        original = SandboxRunner._build_sandbox_template

        def counting(self, **kwargs):
            build_calls.append(kwargs["digest"])
            return original(self, **kwargs)

        monkeypatch.setattr(SandboxRunner, "_build_sandbox_template", counting)

        lease_a = run(runner.allocate(spec_for(base, group_idx=1, attempt_no=0)))
        lease_b = run(runner.allocate(spec_for(base, group_idx=2, attempt_no=0)))

        assert len(build_calls) == 1  # one template build, two sandboxes
        assert lease_a.sandbox_id != lease_b.sandbox_id
        for lease in (lease_a, lease_b):
            assert git(Path(lease.repo_roots["app"]), "rev-parse", "HEAD") == base

    def test_mid_run_lockfile_change_triggers_one_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Implementer tasks CAN modify lockfiles: the digest key handles it."""
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        build_calls: list[str] = []
        original = SandboxRunner._build_sandbox_template

        def counting(self, **kwargs):
            build_calls.append(kwargs["digest"])
            return original(self, **kwargs)

        monkeypatch.setattr(SandboxRunner, "_build_sandbox_template", counting)

        run(runner.allocate(spec_for(base, group_idx=1, attempt_no=0)))

        # A merged implementer task bumps the lockfile -> new base commit too.
        (source / "package-lock.json").write_text('{"v": 2}\n', encoding="utf-8")
        git(source, "add", "package-lock.json")
        git(source, "commit", "-qm", "bump lockfile")
        new_base = git(source, "rev-parse", "HEAD")

        lease = run(runner.allocate(spec_for(new_base, group_idx=2, attempt_no=0)))

        assert len(build_calls) == 2
        assert build_calls[0] != build_calls[1]
        repo_root = Path(lease.repo_roots["app"])
        assert git(repo_root, "rev-parse", "HEAD") == new_base
        assert (
            json.loads((repo_root / "package-lock.json").read_text(encoding="utf-8"))
            == {"v": 2}
        )
        # Both templates exist (prune grace keeps recent digests).
        digests = [p for p in template_feature_dir(tmp_path).iterdir() if p.is_dir()]
        assert len(digests) == 2

    def test_flag_off_restores_legacy_path_verbatim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "0")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        lease = run(runner.allocate(spec_for(base)))

        assert not template_feature_dir(tmp_path).exists()
        repo_root = Path(lease.repo_roots["app"])
        assert git(repo_root, "rev-parse", "HEAD") == base

    def test_prune_removes_old_digests_after_grace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        feature_templates = template_feature_dir(tmp_path)
        stale = feature_templates / "0123456789abcdef"
        (stale / "repo").mkdir(parents=True)
        old = time.time() - 7200
        os.utime(stale, (old, old))

        run(runner.allocate(spec_for(base)))

        assert not stale.exists()
        live = [p for p in feature_templates.iterdir() if p.is_dir()]
        assert len(live) == 1


# ---------------------------------------------------------------------------
# 4. Fallback on failure (loud WARN, legacy path, sandbox never corrupted)
# ---------------------------------------------------------------------------

class TestFallbackToLegacy:
    def test_clonefile_failure_falls_back_to_legacy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        def boom(self, src, dst):
            raise sandbox_module.SandboxError("clonefile exploded")

        monkeypatch.setattr(SandboxRunner, "_clonefile_tree", boom)

        with caplog.at_level("WARNING"):
            lease = run(runner.allocate(spec_for(base)))

        assert any(
            "falling back to legacy full provisioning" in rec.getMessage()
            for rec in caplog.records
        )
        repo_root = Path(lease.repo_roots["app"])
        assert git(repo_root, "rev-parse", "HEAD") == base
        assert git(repo_root, "status", "--porcelain=v1") == ""

    def test_template_build_failure_falls_back_to_legacy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        def failing_build(self, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(SandboxRunner, "_build_sandbox_template", failing_build)

        with caplog.at_level("WARNING"):
            lease = run(runner.allocate(spec_for(base)))

        assert any(
            "falling back to legacy" in rec.getMessage() for rec in caplog.records
        )
        assert git(Path(lease.repo_roots["app"]), "rev-parse", "HEAD") == base

    def test_fatal_provisioning_failure_is_not_cached_as_template(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A template with fatal provision failures must never be published;
        the legacy path records the failure on the manifest exactly as today."""
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        profile = SimpleNamespace(package_roots=["."], package_managers=["bogus-mgr"])
        runner = runner_for(tmp_path, source, project_profile=profile)

        with caplog.at_level("WARNING"):
            lease = run(runner.allocate(spec_for(base)))

        assert any(
            "not caching the template" in rec.getMessage() for rec in caplog.records
        )
        feature_templates = template_feature_dir(tmp_path)
        published = (
            [p for p in feature_templates.iterdir() if p.is_dir()]
            if feature_templates.exists()
            else []
        )
        assert published == []
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["provisioning"]["repos"]["app"], (
            "fatal provisioning failure must surface on the manifest"
        )

    def test_non_darwin_platform_raises_and_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "src"
        source.mkdir()
        runner = runner_for(tmp_path, source)
        monkeypatch.setattr(sandbox_module.sys, "platform", "linux")
        with pytest.raises(sandbox_module.SandboxError, match="requires macOS"):
            runner._clonefile_tree(source, tmp_path / "dest")

    def test_cross_volume_guard_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "src"
        source.mkdir()
        runner = runner_for(tmp_path, source)
        monkeypatch.setattr(sandbox_module.sys, "platform", "darwin")
        monkeypatch.setattr(
            sandbox_module, "_same_filesystem", lambda a, b: False
        )
        with pytest.raises(sandbox_module.SandboxError, match="different"):
            runner._clonefile_tree(source, tmp_path / "dest")

    def test_cp_nonzero_exit_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "src"
        source.mkdir()

        def failing_cp(cwd, argv, env):
            assert argv[:3] == ["cp", "-c", "-R"]
            return sandbox_module.CommandResult(
                returncode=1, stdout=b"", stderr=b"clonefile not supported"
            )

        runner = runner_for(tmp_path, source, command_runner=failing_cp)
        monkeypatch.setattr(sandbox_module.sys, "platform", "darwin")
        with pytest.raises(sandbox_module.SandboxError, match="cp -c -R"):
            runner._clonefile_tree(source, tmp_path / "dest")


# ---------------------------------------------------------------------------
# 5. Single-flight template build
# ---------------------------------------------------------------------------

class TestSingleFlightTemplateBuild:
    def test_concurrent_builders_build_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if not _clonefile_supported(tmp_path):
            pytest.skip("APFS clonefile (cp -c) unsupported on this volume")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        build_count = threading.Semaphore(0)
        builds: list[str] = []
        original = SandboxRunner._build_sandbox_template

        def slow_build(self, **kwargs):
            builds.append(kwargs["digest"])
            time.sleep(0.5)  # widen the race window
            result = original(self, **kwargs)
            build_count.release()
            return result

        monkeypatch.setattr(SandboxRunner, "_build_sandbox_template", slow_build)

        results: list[Path | None] = []

        def ensure() -> None:
            results.append(
                runner._ensure_sandbox_template(
                    feature_slug="feature-one",
                    repo_id="app",
                    source_resolved=source.resolve(),
                    base_commit=base,
                )
            )

        threads = [threading.Thread(target=ensure) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert len(builds) == 1, "exactly one builder per digest"
        assert len(results) == 4
        assert all(r is not None for r in results)
        assert len({str(r) for r in results}) == 1

    def test_foreign_lockdir_times_out_to_legacy_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A fresh lockdir held by another process: wait, then fall back."""
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        digest = runner._template_digest("app", source.resolve(), base)
        lock_dir = template_feature_dir(tmp_path) / f"{digest}.building"
        lock_dir.mkdir(parents=True)

        with caplog.at_level("WARNING"):
            result = runner._ensure_sandbox_template(
                feature_slug="feature-one",
                repo_id="app",
                source_resolved=source.resolve(),
                base_commit=base,
            )

        assert result is None
        assert any("timed out" in rec.getMessage() for rec in caplog.records)
        assert lock_dir.exists()  # never steal a live builder's lock

    def test_stale_lockdir_is_reclaimed_and_build_proceeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        if not _clonefile_supported(tmp_path):
            pytest.skip("APFS clonefile (cp -c) unsupported on this volume")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        digest = runner._template_digest("app", source.resolve(), base)
        lock_dir = template_feature_dir(tmp_path) / f"{digest}.building"
        lock_dir.mkdir(parents=True)
        old = time.time() - 600  # >> 2 * wait
        os.utime(lock_dir, (old, old))

        with caplog.at_level("WARNING"):
            result = runner._ensure_sandbox_template(
                feature_slug="feature-one",
                repo_id="app",
                source_resolved=source.resolve(),
                base_commit=base,
            )

        assert result is not None
        assert result.is_dir()
        assert any("stale" in rec.getMessage() for rec in caplog.records)
        assert not lock_dir.exists()

    def test_waiter_reattaches_when_template_appears(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S", "10")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        digest = runner._template_digest("app", source.resolve(), base)
        template_dir = template_feature_dir(tmp_path) / digest
        lock_dir = template_feature_dir(tmp_path) / f"{digest}.building"
        lock_dir.mkdir(parents=True)

        def publish_then_unlock() -> None:
            time.sleep(0.5)
            (template_dir / "repo" / ".git").mkdir(parents=True)
            (template_dir / "template-manifest.json").write_text(
                "{}", encoding="utf-8"
            )
            os.rmdir(lock_dir)

        publisher = threading.Thread(target=publish_then_unlock)
        publisher.start()
        try:
            result = runner._ensure_sandbox_template(
                feature_slug="feature-one",
                repo_id="app",
                source_resolved=source.resolve(),
                base_commit=base,
            )
        finally:
            publisher.join(timeout=10)

        assert result == template_dir / "repo"


# ---------------------------------------------------------------------------
# 8. Template-time permission normalization
#    (wm/template-permission-normalization, IRIAI_SANDBOX_TEMPLATE_PERMS)
# ---------------------------------------------------------------------------

_PERMS_MARKER_NAME = ".iriai-template-permissions-normalized"


def _count_full_sweeps(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Wrap the full permission sweep so tests can count tree walks."""
    calls: list[Path] = []
    original = SandboxRunner._normalize_sandbox_repo_permissions

    def counting(self, repo_root, *, sandbox_root):
        calls.append(Path(repo_root))
        return original(self, repo_root, sandbox_root=sandbox_root)

    monkeypatch.setattr(
        SandboxRunner, "_normalize_sandbox_repo_permissions", counting
    )
    return calls


class TestTemplatePermsFlag:
    def test_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IRIAI_SANDBOX_TEMPLATE_PERMS", raising=False)
        assert _sandbox_template_perms_enabled() is True

    def test_disabled_by_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "0")
        assert _sandbox_template_perms_enabled() is False

    def test_disabled_by_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "false")
        assert _sandbox_template_perms_enabled() is False

    def test_enabled_by_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        assert _sandbox_template_perms_enabled() is True


class TestTemplatePermissionNormalization:
    @pytest.fixture(autouse=True)
    def _require_clonefile(self, tmp_path: Path) -> None:
        if not _clonefile_supported(tmp_path):
            pytest.skip("APFS clonefile (cp -c) unsupported on this volume")

    @pytest.fixture(autouse=True)
    def _own_gid_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sandbox_module,
            "_agent_shared_group",
            lambda: ("test-agents", os.getgid()),
        )

    def test_template_build_stamps_marker_and_normalized_perms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        source = tmp_path / "canonical" / "app"
        init_repo(source)
        (source / "src" / "nested").mkdir(parents=True)
        (source / "src" / "nested" / "mod.py").write_text(
            "value = 1\n", encoding="utf-8"
        )
        git(source, "add", ".")
        git(source, "commit", "-qm", "nested")
        base = git(source, "rev-parse", "HEAD")
        runner = runner_for(tmp_path, source)

        lease = run(runner.allocate(spec_for(base)))

        digests = [
            p for p in template_feature_dir(tmp_path).iterdir() if p.is_dir()
        ]
        assert len(digests) == 1
        marker_path = digests[0] / _PERMS_MARKER_NAME
        assert marker_path.is_file()
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        assert marker["schema_version"] == "sandbox-template-perms-v1"
        assert marker["repo_id"] == "app"
        assert marker["agent_shared_group"] == "test-agents"
        assert marker["agent_shared_gid"] == os.getgid()
        assert marker["summary"]["paths_changed"] > 0

        # The TEMPLATE tree itself is normalized (group + g+ws dirs, g+w files).
        template_repo = digests[0] / "repo"
        for directory in [template_repo, template_repo / "src", template_repo / ".git"]:
            mode = directory.stat().st_mode
            assert mode & stat.S_IWGRP
            assert mode & stat.S_IXGRP
            assert mode & stat.S_ISGID
            assert directory.stat().st_gid == os.getgid()
        tracked = template_repo / "tracked.txt"
        assert tracked.stat().st_mode & stat.S_IWGRP

        # The clonefile copy inherits the normalized perms end-to-end.
        repo = Path(lease.repo_roots["app"])
        for directory in [repo, repo / "src", repo / "src" / "nested", repo / ".git"]:
            mode = directory.stat().st_mode
            assert mode & stat.S_IWGRP
            assert mode & stat.S_IXGRP
            assert mode & stat.S_ISGID
            assert directory.stat().st_gid == os.getgid()
        assert (repo / "tracked.txt").stat().st_mode & stat.S_IWGRP
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text()
        )
        summary = manifest["permission_normalization"]["repos"]["app"]
        assert summary["mode"] == "template_spot_verify"

    def test_clone_from_marked_template_spot_verifies_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        sweep_calls = _count_full_sweeps(monkeypatch)

        lease_a = run(runner.allocate(spec_for(base, group_idx=1, attempt_no=0)))
        lease_b = run(runner.allocate(spec_for(base, group_idx=2, attempt_no=0)))

        # Exactly ONE full tree walk total — at template build time, inside the
        # template staging dir.  NEITHER per-task clone walked its tree.
        assert len(sweep_calls) == 1
        assert sweep_calls[0].is_relative_to(template_feature_dir(tmp_path))
        for lease in (lease_a, lease_b):
            assert not sweep_calls[0].is_relative_to(Path(lease.root))
            manifest = json.loads(
                (Path(lease.root) / "sandbox-manifest.json").read_text()
            )
            summary = manifest["permission_normalization"]["repos"]["app"]
            assert summary["mode"] == "template_spot_verify"
            assert summary["agent_shared_gid"] == os.getgid()
            assert 0 < summary["paths_verified"] <= 80

    def test_spot_verify_mismatch_falls_back_to_full_sweep_with_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        original_clone = SandboxRunner._clonefile_tree

        def breaking_clone(self, src, dest):
            original_clone(self, src, dest)
            # Simulate perms lost between template and clone: a top-level
            # file the spot-verify samples loses its group bits.
            (dest / "tracked.txt").chmod(0o600)

        monkeypatch.setattr(SandboxRunner, "_clonefile_tree", breaking_clone)

        with caplog.at_level(logging.WARNING):
            lease = run(runner.allocate(spec_for(base)))

        assert "spot-verify FAILED" in caplog.text
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text()
        )
        summary = manifest["permission_normalization"]["repos"]["app"]
        assert "tracked.txt" in summary["spot_verify_fallback"]
        assert summary["paths_changed"] >= 1  # full sweep ran and repaired it
        repo = Path(lease.repo_roots["app"])
        assert (repo / "tracked.txt").stat().st_mode & stat.S_IWGRP

    def test_marker_params_mismatch_runs_full_sweep(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        run(runner.allocate(spec_for(base, group_idx=1, attempt_no=0)))

        # The shared group changes after the template was built: the marker no
        # longer proves the right normalization — full sweep, loudly.
        monkeypatch.setattr(
            sandbox_module,
            "_agent_shared_group",
            lambda: ("other-agents", os.getgid()),
        )
        sweep_calls = _count_full_sweeps(monkeypatch)
        with caplog.at_level(logging.WARNING):
            lease = run(runner.allocate(spec_for(base, group_idx=2, attempt_no=0)))

        assert "does not match current normalization params" in caplog.text
        assert len(sweep_calls) == 1
        assert sweep_calls[0] == Path(lease.repo_roots["app"])
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text()
        )
        summary = manifest["permission_normalization"]["repos"]["app"]
        assert summary["spot_verify_fallback"] == "template marker params mismatch"

    def test_missing_marker_runs_full_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A template built before this change (no marker) keeps legacy sweeps."""
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        run(runner.allocate(spec_for(base, group_idx=1, attempt_no=0)))
        for digest in template_feature_dir(tmp_path).iterdir():
            marker = digest / _PERMS_MARKER_NAME
            if marker.is_file():
                marker.unlink()

        sweep_calls = _count_full_sweeps(monkeypatch)
        lease = run(runner.allocate(spec_for(base, group_idx=2, attempt_no=0)))

        assert len(sweep_calls) == 1
        assert sweep_calls[0] == Path(lease.repo_roots["app"])
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text()
        )
        summary = manifest["permission_normalization"]["repos"]["app"]
        assert "mode" not in summary
        assert "spot_verify_fallback" not in summary
        assert "paths_changed" in summary

    def test_flag_off_restores_full_per_clone_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "0")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        sweep_calls = _count_full_sweeps(monkeypatch)

        lease = run(runner.allocate(spec_for(base)))

        # No template-time normalization, no marker; the one and only sweep ran
        # on the per-task clone — today's behaviour byte-identically.
        digests = [
            p for p in template_feature_dir(tmp_path).iterdir() if p.is_dir()
        ]
        assert len(digests) == 1
        assert not (digests[0] / _PERMS_MARKER_NAME).exists()
        assert len(sweep_calls) == 1
        assert sweep_calls[0] == Path(lease.repo_roots["app"])
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text()
        )
        summary = manifest["permission_normalization"]["repos"]["app"]
        assert "mode" not in summary
        assert "spot_verify_fallback" not in summary
        assert "paths_changed" in summary

    def test_legacy_provisioning_keeps_full_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "0")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        sweep_calls = _count_full_sweeps(monkeypatch)

        lease = run(runner.allocate(spec_for(base)))

        assert not template_feature_dir(tmp_path).exists()
        assert len(sweep_calls) == 1
        assert sweep_calls[0] == Path(lease.repo_roots["app"])
        manifest = json.loads(
            (Path(lease.root) / "sandbox-manifest.json").read_text()
        )
        summary = manifest["permission_normalization"]["repos"]["app"]
        assert "mode" not in summary
        assert "paths_changed" in summary


# ---------------------------------------------------------------------------
# 8. Venv console-script path rewrite (N-23: staging-path shebangs dangle)
# ---------------------------------------------------------------------------

def _make_fake_venv(package_root: Path, embedded_prefix: str) -> None:
    """Fake ``python3 -m venv`` output at ``<package_root>/.venv``.

    Text console scripts (pip/pytest) whose shebang embeds *embedded_prefix*
    (the venv's absolute location at creation time, exactly like real venv
    console scripts), a NUL-containing binary launcher, an interpreter
    symlink, and a ``pyvenv.cfg`` that also embeds the path.
    """
    bin_dir = package_root / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    shebang = f"#!{embedded_prefix}/.venv/bin/python3\n"
    for script in ("pip", "pytest"):
        (bin_dir / script).write_text(
            shebang + "# -*- coding: utf-8 -*-\nimport sys\n", encoding="utf-8"
        )
        (bin_dir / script).chmod(0o755)
    (bin_dir / "launcher.bin").write_bytes(
        b"\x7fELF\x00\x01" + embedded_prefix.encode("utf-8") + b"\x00"
    )
    (bin_dir / "python3").symlink_to("/usr/bin/true")
    (package_root / ".venv" / "pyvenv.cfg").write_text(
        "home = /opt/pyenv/versions/3.12.0/bin\n"
        f"command = /opt/pyenv/versions/3.12.0/bin/python3 -m venv "
        f"{embedded_prefix}/.venv\n",
        encoding="utf-8",
    )


def _fake_venv_provision(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Monkeypatch dependency provisioning to lay down one fake venv.

    Mirrors the real pip path: the venv is created IN the repo root the
    provisioner is handed (the template build's staging repo), so its
    embedded absolute path is the staging path.
    """
    provisioned_roots: list[Path] = []

    def fake(self, repo_root, source_root):
        pkg = Path(repo_root) / "ai-service"
        pkg.mkdir(exist_ok=True)
        _make_fake_venv(pkg, str(pkg))
        provisioned_roots.append(Path(repo_root))
        return [
            sandbox_module.ProvisionResult(
                rel_path="ai-service", manager="pip", ok=True
            )
        ]

    monkeypatch.setattr(SandboxRunner, "_provision_sandbox_dependencies", fake)
    return provisioned_roots


class TestVenvPathRewrite:
    def test_helper_rewrites_text_scripts_and_pyvenv_cfg_only(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        pkg = repo / "ai-service"
        _make_fake_venv(pkg, str(pkg))
        binary_before = (pkg / ".venv" / "bin" / "launcher.bin").read_bytes()
        runner = runner_for(tmp_path, repo)

        new_repo = "/sandboxes/attempt-654/repo"
        count = runner._rewrite_venv_path_references(
            repo, old_prefix=str(repo), new_prefix=new_repo
        )

        assert count == 3  # pip + pytest + pyvenv.cfg
        for script in ("pip", "pytest"):
            content = (pkg / ".venv" / "bin" / script).read_text(encoding="utf-8")
            assert content.startswith(
                f"#!{new_repo}/ai-service/.venv/bin/python3\n"
            )
            assert str(repo) not in content
            mode = (pkg / ".venv" / "bin" / script).stat().st_mode
            assert mode & stat.S_IXUSR  # executable bit preserved
        cfg = (pkg / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
        assert f"{new_repo}/ai-service/.venv" in cfg
        assert str(repo) not in cfg
        # Binary and symlink are never touched.
        assert (pkg / ".venv" / "bin" / "launcher.bin").read_bytes() == binary_before
        python3 = pkg / ".venv" / "bin" / "python3"
        assert python3.is_symlink()
        assert os.readlink(python3) == "/usr/bin/true"

    def test_helper_noop_when_prefixes_match(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _make_fake_venv(repo / "ai-service", str(repo / "ai-service"))
        runner = runner_for(tmp_path, repo)
        assert (
            runner._rewrite_venv_path_references(
                repo, old_prefix=str(repo), new_prefix=str(repo)
            )
            == 0
        )

    def test_template_publish_rewrites_staging_to_final_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass 1: at publish, staging-path shebangs become final-path."""
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "0")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        provisioned_roots = _fake_venv_provision(monkeypatch)
        template_dir = template_feature_dir(tmp_path) / "cafe0123deadbeef"
        template_dir.parent.mkdir(parents=True)

        repo_dir = runner._build_sandbox_template(
            template_dir=template_dir,
            digest="cafe0123deadbeef",
            repo_id="app",
            source_resolved=source.resolve(),
            base_commit=base,
        )

        assert repo_dir == template_dir / "repo"
        assert len(provisioned_roots) == 1
        venv_bin = repo_dir / "ai-service" / ".venv" / "bin"
        for script in ("pip", "pytest"):
            content = (venv_bin / script).read_text(encoding="utf-8")
            assert content.startswith(
                f"#!{template_dir}/repo/ai-service/.venv/bin/python3\n"
            )
            assert ".staging-" not in content
        cfg = (repo_dir / "ai-service" / ".venv" / "pyvenv.cfg").read_text(
            encoding="utf-8"
        )
        assert ".staging-" not in cfg
        # Binary keeps its (now-stale) staging bytes: proof it was untouched.
        assert b".staging-" in (venv_bin / "launcher.bin").read_bytes()
        assert (venv_bin / "python3").is_symlink()

    def test_clone_rewrites_template_path_to_attempt_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass 2: each clone's scripts target the clone, never the template."""
        if not _clonefile_supported(tmp_path):
            pytest.skip("APFS clonefile (cp -c) unsupported on this volume")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_PERMS", "0")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)
        _fake_venv_provision(monkeypatch)

        lease = run(runner.allocate(spec_for(base)))

        repo_root = Path(lease.repo_roots["app"])
        digests = [p for p in template_feature_dir(tmp_path).iterdir() if p.is_dir()]
        assert len(digests) == 1
        template_repo = digests[0] / "repo"
        for script in ("pip", "pytest"):
            content = (
                repo_root / "ai-service" / ".venv" / "bin" / script
            ).read_text(encoding="utf-8")
            assert content.startswith(
                f"#!{repo_root}/ai-service/.venv/bin/python3\n"
            )
            # Cross-contamination guard: never points at the template's venv.
            assert str(template_repo) not in content
            assert ".staging-" not in content
            # The TEMPLATE's own script still targets the template.
            template_content = (
                template_repo / "ai-service" / ".venv" / "bin" / script
            ).read_text(encoding="utf-8")
            assert template_content.startswith(
                f"#!{template_repo}/ai-service/.venv/bin/python3\n"
            )
        assert (repo_root / "ai-service" / ".venv" / "bin" / "python3").is_symlink()

    def test_publish_rewrite_failure_falls_back_to_legacy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A rewrite failure must never publish the template; loud fallback."""
        monkeypatch.setenv("IRIAI_SANDBOX_TEMPLATE_COW", "1")
        source = tmp_path / "canonical" / "app"
        base = init_repo(source)
        runner = runner_for(tmp_path, source)

        def boom(self, repo_root, *, old_prefix, new_prefix):
            raise sandbox_module.SandboxError("venv path rewrite exploded")

        monkeypatch.setattr(SandboxRunner, "_rewrite_venv_path_references", boom)

        with caplog.at_level("WARNING"):
            lease = run(runner.allocate(spec_for(base)))

        assert any(
            "sandbox template build FAILED" in rec.getMessage()
            and "falling back to legacy provisioning" in rec.getMessage()
            for rec in caplog.records
        )
        feature_templates = template_feature_dir(tmp_path)
        published = (
            [p for p in feature_templates.iterdir() if p.is_dir()]
            if feature_templates.exists()
            else []
        )
        assert published == []  # never publish a template with dangling tooling
        repo_root = Path(lease.repo_roots["app"])
        assert git(repo_root, "rev-parse", "HEAD") == base
