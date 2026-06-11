"""Parallel sandbox provisioning (IRIAI_SANDBOX_PARALLEL_PROVISION).

Pins the W-D change: the allocation lock is narrowed from per-feature to
per-sandbox-root (default ON) so a wave of N tasks provisions concurrently
(wave prep = max(single task), not sum). Setting the flag to 0 restores the
wide per-feature lock verbatim. IRIAI_SANDBOX_PROVISION_CONCURRENCY optionally
bounds concurrent clone+install sections; a provisioning failure must release
the gate and never poison a sibling allocate.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.execution.sandbox import (
    CommandResult,
    SandboxAllocationError,
    SandboxRunner,
    SandboxSpec,
    _allocation_lock_for_allocate,
    _allocation_lock_for_feature,
    _allocation_lock_for_sandbox_root,
    _provision_concurrency_limit,
    _sandbox_parallel_provision_enabled,
)

_FLAG_ENV = "IRIAI_SANDBOX_PARALLEL_PROVISION"
_CONCURRENCY_ENV = "IRIAI_SANDBOX_PROVISION_CONCURRENCY"
# Template-CoW (default ON) satisfies the first per-task provisioning from a
# single-flight template build and falls back to legacy on any failure. The
# tests below intercept the legacy per-task `git clone` to pin W-D lock/gate
# semantics, so they pin the template layer OFF; template behaviour itself is
# covered by test_sandbox_template_cow.py.
_TEMPLATE_ENV = "IRIAI_SANDBOX_TEMPLATE_COW"


# ── Helpers (test_sandbox.py conventions) ────────────────────────────────


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
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return git(path, "rev-parse", "HEAD")


def spec_for(
    base_commit: str, *, feature_id: str, attempt_no: int
) -> SandboxSpec:
    return SandboxSpec(
        feature_id=feature_id,
        dag_sha256="dag-sha",
        group_idx=4,
        attempt_no=attempt_no,
        task_ids=[f"task-{attempt_no}"],
        repo_ids=["app"],
        base_snapshot_ids=[11],
        base_commits={"app": base_commit},
        mode="task",
        writable_roots=[],
        readonly_roots=[],
        contract_ids=[7],
    )


def _subprocess_command_runner(cwd: Path, argv: list, env) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=dict(env) if env else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _is_clone(argv: list) -> bool:
    return len(argv) >= 2 and argv[0] == "git" and argv[1] == "clone"


def runner_for(
    tmp_path: Path, source: Path, *, command_runner=None
) -> SandboxRunner:
    return SandboxRunner(
        workspace_root=tmp_path,
        repo_sources={"app": source},
        allowed_source_roots=[tmp_path],
        command_runner=command_runner,
    )


# ── Flag readers ──────────────────────────────────────────────────────────


def test_parallel_provision_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    assert _sandbox_parallel_provision_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "FALSE", "no", "No", "NO"])
def test_parallel_provision_opt_out(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_FLAG_ENV, value)
    assert _sandbox_parallel_provision_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "", "on"])
def test_parallel_provision_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_FLAG_ENV, value)
    assert _sandbox_parallel_provision_enabled() is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("0", None),
        ("-3", None),
        ("junk", None),
        ("1", 1),
        ("6", 6),
        (" 4 ", 4),
    ],
)
def test_provision_concurrency_limit_parsing(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int | None
) -> None:
    if value is None:
        monkeypatch.delenv(_CONCURRENCY_ENV, raising=False)
    else:
        monkeypatch.setenv(_CONCURRENCY_ENV, value)
    assert _provision_concurrency_limit() == expected


# ── Lock selection ────────────────────────────────────────────────────────


def test_lock_is_per_sandbox_root_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    slug = "lock-scope-feature-on"
    root_a = Path("/tmp/ws/.iriai/features") / slug / "sandboxes/g4/attempt-1"
    root_b = Path("/tmp/ws/.iriai/features") / slug / "sandboxes/g4/attempt-2"
    lock_a = _allocation_lock_for_allocate(slug, root_a)
    lock_b = _allocation_lock_for_allocate(slug, root_b)
    assert lock_a is not lock_b, "distinct sandbox roots must not share a lock"
    assert _allocation_lock_for_allocate(slug, root_a) is lock_a
    # Per-root locks must never alias the wide per-feature lock.
    assert lock_a is not _allocation_lock_for_feature(slug)
    assert lock_b is not _allocation_lock_for_feature(slug)


def test_lock_is_wide_per_feature_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLAG_ENV, "0")
    slug = "lock-scope-feature-off"
    root_a = Path("/tmp/ws/.iriai/features") / slug / "sandboxes/g4/attempt-1"
    root_b = Path("/tmp/ws/.iriai/features") / slug / "sandboxes/g4/attempt-2"
    lock_a = _allocation_lock_for_allocate(slug, root_a)
    lock_b = _allocation_lock_for_allocate(slug, root_b)
    assert lock_a is lock_b, "flag=0 must restore the single wide lock"
    assert lock_a is _allocation_lock_for_feature(slug)


def test_per_root_lock_registry_key_does_not_alias_feature_key() -> None:
    slug = "alias-check-feature"
    root = Path(slug)  # pathological: root text equal to the slug
    assert _allocation_lock_for_sandbox_root(slug, root) is not (
        _allocation_lock_for_feature(slug)
    )


# ── Behavioral: parallel vs serial allocate ───────────────────────────────


class _BlockingCloneRunner:
    """Delegates to subprocess; blocks the FIRST clone until released.

    The clone runs inside asyncio.to_thread, so blocking it parks a worker
    thread without freezing the event loop — mirroring a slow real clone.
    """

    def __init__(self) -> None:
        self.first_clone_started = threading.Event()
        self.release_first_clone = threading.Event()
        self._lock = threading.Lock()
        self._blocked_once = False

    def __call__(self, cwd: Path, argv: list, env) -> CommandResult:
        if _is_clone(argv):
            with self._lock:
                should_block = not self._blocked_once
                self._blocked_once = True
            if should_block:
                self.first_clone_started.set()
                assert self.release_first_clone.wait(timeout=60), (
                    "test never released the blocked clone"
                )
        return _subprocess_command_runner(cwd, argv, env)


def test_sibling_allocate_proceeds_while_clone_in_flight_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    monkeypatch.delenv(_CONCURRENCY_ENV, raising=False)
    monkeypatch.setenv(_TEMPLATE_ENV, "0")
    feature_id = "parallel-on-feature"
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    blocking = _BlockingCloneRunner()
    runner_a = runner_for(tmp_path, source, command_runner=blocking)
    runner_b = runner_for(tmp_path, source)

    async def main() -> None:
        task_a = asyncio.create_task(
            runner_a.allocate(spec_for(base, feature_id=feature_id, attempt_no=1))
        )
        # Wait (off-loop event) until A is provably inside its clone — i.e.
        # holding its per-root lock across the await.
        await asyncio.to_thread(blocking.first_clone_started.wait, 30)
        assert blocking.first_clone_started.is_set()
        try:
            lease_b = await asyncio.wait_for(
                runner_b.allocate(
                    spec_for(base, feature_id=feature_id, attempt_no=2)
                ),
                timeout=30,
            )
        finally:
            blocking.release_first_clone.set()
        lease_a = await task_a
        assert lease_a.sandbox_id != lease_b.sandbox_id
        assert Path(lease_a.root) != Path(lease_b.root)

    asyncio.run(main())


def test_sibling_allocate_waits_for_wide_lock_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FLAG_ENV, "0")
    monkeypatch.delenv(_CONCURRENCY_ENV, raising=False)
    feature_id = "parallel-off-feature"
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    blocking = _BlockingCloneRunner()
    runner_a = runner_for(tmp_path, source, command_runner=blocking)
    runner_b = runner_for(tmp_path, source)

    async def main() -> None:
        task_a = asyncio.create_task(
            runner_a.allocate(spec_for(base, feature_id=feature_id, attempt_no=1))
        )
        await asyncio.to_thread(blocking.first_clone_started.wait, 30)
        task_b = asyncio.create_task(
            runner_b.allocate(spec_for(base, feature_id=feature_id, attempt_no=2))
        )
        # A holds the wide feature lock across its blocked clone, so B must
        # still be pending after a real delay.
        await asyncio.sleep(0.5)
        assert not task_b.done(), (
            "flag=0 must serialize same-feature allocates under the wide lock"
        )
        blocking.release_first_clone.set()
        lease_a = await task_a
        lease_b = await asyncio.wait_for(task_b, timeout=60)
        assert lease_a.sandbox_id != lease_b.sandbox_id

    asyncio.run(main())


# ── Bounded provisioning concurrency + failure isolation ─────────────────


class _CountingCloneRunner:
    """Tracks the maximum number of concurrently-running clones."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def __call__(self, cwd: Path, argv: list, env) -> CommandResult:
        if _is_clone(argv):
            with self._lock:
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            try:
                # Hold the clone long enough that an unbounded sibling WOULD
                # overlap; the bounded gate must prevent that overlap.
                threading.Event().wait(0.3)
                return _subprocess_command_runner(cwd, argv, env)
            finally:
                with self._lock:
                    self._active -= 1
        return _subprocess_command_runner(cwd, argv, env)


def test_provision_concurrency_one_serializes_heavy_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    monkeypatch.setenv(_CONCURRENCY_ENV, "1")
    monkeypatch.setenv(_TEMPLATE_ENV, "0")
    feature_id = "bounded-feature"
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    counting = _CountingCloneRunner()
    runner_a = runner_for(tmp_path, source, command_runner=counting)
    runner_b = runner_for(tmp_path, source, command_runner=counting)

    async def main() -> None:
        lease_a, lease_b = await asyncio.gather(
            runner_a.allocate(spec_for(base, feature_id=feature_id, attempt_no=1)),
            runner_b.allocate(spec_for(base, feature_id=feature_id, attempt_no=2)),
        )
        assert lease_a.sandbox_id != lease_b.sandbox_id

    asyncio.run(main())
    assert counting.max_active == 1, (
        "IRIAI_SANDBOX_PROVISION_CONCURRENCY=1 must never overlap clones; "
        f"observed max concurrency {counting.max_active}"
    )


class _FailFirstCloneRunner:
    """First clone fails (rc=1); everything else delegates to subprocess."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failed_once = False

    def __call__(self, cwd: Path, argv: list, env) -> CommandResult:
        if _is_clone(argv):
            with self._lock:
                should_fail = not self._failed_once
                self._failed_once = True
            if should_fail:
                return CommandResult(
                    returncode=1, stdout=b"", stderr=b"simulated clone failure"
                )
        return _subprocess_command_runner(cwd, argv, env)


def test_provision_failure_releases_gate_and_spares_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # limit=1 makes the assertion sharp: if the failing allocate leaked the
    # gate (or its lock), the sibling could never finish.
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    monkeypatch.setenv(_CONCURRENCY_ENV, "1")
    monkeypatch.setenv(_TEMPLATE_ENV, "0")
    feature_id = "isolation-feature"
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    failing = _FailFirstCloneRunner()
    runner_a = runner_for(tmp_path, source, command_runner=failing)
    runner_b = runner_for(tmp_path, source, command_runner=failing)

    async def main() -> tuple[object, object]:
        result_a = None
        try:
            result_a = await runner_a.allocate(
                spec_for(base, feature_id=feature_id, attempt_no=1)
            )
        except SandboxAllocationError as exc:
            result_a = exc
        result_b = await asyncio.wait_for(
            runner_b.allocate(spec_for(base, feature_id=feature_id, attempt_no=2)),
            timeout=60,
        )
        return result_a, result_b

    result_a, result_b = asyncio.run(main())
    assert isinstance(result_a, SandboxAllocationError)
    assert getattr(result_b, "sandbox_id", "")
    # The failed allocate must have cleaned its partial sandbox root.
    assert not Path(
        tmp_path / ".iriai/features" / feature_id / "sandboxes/g4/attempt-1"
    ).exists()


def test_parallel_failure_isolation_under_gather(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One task's provisioning failure must not cancel a parallel sibling."""
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    monkeypatch.delenv(_CONCURRENCY_ENV, raising=False)
    monkeypatch.setenv(_TEMPLATE_ENV, "0")
    feature_id = "gather-isolation-feature"
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    failing = _FailFirstCloneRunner()
    runner_a = runner_for(tmp_path, source, command_runner=failing)
    runner_b = runner_for(tmp_path, source, command_runner=failing)

    async def main() -> list[object]:
        return await asyncio.gather(
            runner_a.allocate(spec_for(base, feature_id=feature_id, attempt_no=1)),
            runner_b.allocate(spec_for(base, feature_id=feature_id, attempt_no=2)),
            return_exceptions=True,
        )

    results = asyncio.run(main())
    failures = [r for r in results if isinstance(r, BaseException)]
    leases = [r for r in results if not isinstance(r, BaseException)]
    assert len(failures) == 1 and isinstance(failures[0], SandboxAllocationError)
    assert len(leases) == 1 and getattr(leases[0], "sandbox_id", "")
