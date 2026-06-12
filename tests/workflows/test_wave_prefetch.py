"""Wave-startup prefetch (IRIAI_WAVE_PREFETCH, W-S).

Pins the W-S change: when wave N dispatches, a background task kicks wave
N+1's full startup (sandbox allocation via the SAME `_bind_task_sandbox`
spec construction + `SandboxRunner.allocate()` template/clonefile path, plus
dispatch-prompt assembly via the SAME `_ImplementationPromptBuilder`), so
the next wave's sandboxes are waiting when the seal clears. Covered:

- flag reader: default ON, "0"/"false"/"no" off;
- `SandboxSpec.content_idempotency_key`: attempt-free (two specs differing
  only in attempt_no share it — the REUSE_ON_RETRY=1 seed) and
  drift-sensitive (base-commit change => different key), while the default
  per-attempt `idempotency_key` semantics stay byte-identical;
- REUSE: a prefetch-registered sandbox is ADOPTED by the real dispatch
  (same lease, runtime-bound, NO second sandbox directory provisioned);
- STALENESS: a base-commit drift between prefetch and dispatch misses the
  registry (fresh provision at the real attempt path) and the stale entry
  is released by the controller (root removed);
- flag-off byte-identical: the registry is never consumed and the legacy
  fresh-provision path runs;
- quiesce/CHK boundary (IRIAI_QUIESCE_GROUP_INDEXES, the same
  `_dag_quiesce_group_indexes()` derivation dispatch uses) and an
  unresolved regroup offset are never prefetched;
- prefetch failure is WARN-only: `settle()` completes and dispatch is
  unaffected; `shutdown()` cancels an in-flight prefetch and releases
  unadopted sandboxes;
- the prefetched `dag-dispatch-prompt:*` artifact is digest-keyed: an
  unchanged rebuild adds no new key, a task amendment changes the prompt
  sha and therefore the key (automatic rebuild — no staleness).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.execution_control.models import IdempotencyConflict
from iriai_build_v2.models.outputs import ImplementationTask
from iriai_build_v2.workflows.develop.execution.sandbox import SandboxSpec
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module

_FLAG_ENV = implementation_module._WAVE_PREFETCH_ENV


# ── Fixtures ─────────────────────────────────────────────────────────────────


class _Artifacts:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str, *, feature):
        del feature
        return self.store.get(key, "")

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.store[key] = value


def _feature(feature_id: str = "ws-feat"):
    return SimpleNamespace(id=feature_id, slug=feature_id, metadata={})


def _runner():
    return SimpleNamespace(artifacts=_Artifacts(), services={})


def _task(task_id: str = "T-1") -> ImplementationTask:
    return ImplementationTask(
        id=task_id, name=f"Task {task_id}", description=f"Do {task_id}."
    )


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.decode("utf-8").strip()


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "ws@example.test")
    _git(path, "config", "user.name", "WS Test")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return _git(path, "rev-parse", "HEAD")


def _workspace(tmp_path: Path, feature_id: str = "ws-feat") -> tuple[Path, Path, Path]:
    """(workspace_root, feature_root, repo_path) with a real git repo."""
    workspace_root = tmp_path / "ws"
    feature_root = workspace_root / ".iriai" / "features" / feature_id / "repos"
    repo = feature_root / "app"
    _init_repo(repo)
    return workspace_root, feature_root, repo


async def _bind(
    runner,
    feature,
    *,
    workspace_root: Path,
    feature_root: Path,
    repo: Path,
    attempt_no: int,
    prefetch: bool = False,
    group_idx: int = 3,
    task_id: str = "T-1",
):
    return await implementation_module._bind_task_sandbox(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
        group_idx=group_idx,
        task_idx=0,
        attempt=0,
        task=_task(task_id),
        task_contract=None,
        ws_path=str(repo),
        snapshots=[],
        runtime="claude",
        repo_id_hint="app",
        sandbox_mode="task",
        sandbox_attempt_no=attempt_no,
        prefetch_register_only=prefetch,
    )


def _sandbox_group_dir(workspace_root: Path, feature_id: str, group_idx: int) -> Path:
    return (
        workspace_root / ".iriai" / "features" / feature_id / "sandboxes" / f"g{group_idx}"
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    implementation_module._WAVE_PREFETCH_REGISTRY.clear()
    yield
    implementation_module._WAVE_PREFETCH_REGISTRY.clear()


# ── Flag reader ──────────────────────────────────────────────────────────────


def test_flag_default_on(monkeypatch):
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    assert implementation_module._wave_prefetch_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "False", "no", "NO"])
def test_flag_off_values(monkeypatch, raw):
    monkeypatch.setenv(_FLAG_ENV, raw)
    assert implementation_module._wave_prefetch_enabled() is False


@pytest.mark.parametrize("raw", ["1", "on", "yes", ""])
def test_flag_other_values_on(monkeypatch, raw):
    monkeypatch.setenv(_FLAG_ENV, raw)
    assert implementation_module._wave_prefetch_enabled() is True


# ── Content idempotency key ──────────────────────────────────────────────────


def _spec(*, attempt_no: int = 5, base_commit: str = "c1") -> SandboxSpec:
    return SandboxSpec(
        feature_id="f",
        dag_sha256="d",
        group_idx=1,
        attempt_no=attempt_no,
        task_ids=["t"],
        repo_ids=["r"],
        base_snapshot_ids=[1],
        base_commits={"r": base_commit},
        mode="task",
        writable_roots=[],
        readonly_roots=[],
        contract_ids=[7],
    )


def test_content_key_is_attempt_free(monkeypatch):
    monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
    a = _spec(attempt_no=5)
    b = _spec(attempt_no=987_000_123)
    # Real-dispatch attempt ids are unpredictable; the content key must match.
    assert a.content_idempotency_key == b.content_idempotency_key
    # Default per-attempt allocation-key semantics are unchanged.
    assert a.idempotency_key != b.idempotency_key


def test_content_key_changes_on_base_commit_drift():
    a = _spec(base_commit="c1")
    b = _spec(base_commit="c2")
    assert a.content_idempotency_key != b.content_idempotency_key


def test_content_key_matches_reuse_on_retry_seed(monkeypatch):
    # With IRIAI_SANDBOX_REUSE_ON_RETRY=1 the allocation key is the same
    # attempt-free seed — the existing semantics the prefetch matching reuses.
    monkeypatch.setenv("IRIAI_SANDBOX_REUSE_ON_RETRY", "1")
    a = _spec(attempt_no=5)
    b = _spec(attempt_no=6)
    assert a.idempotency_key == b.idempotency_key
    assert a.content_idempotency_key == b.content_idempotency_key


# ── Reuse at real dispatch (no double provision) ─────────────────────────────


@pytest.mark.asyncio
async def test_prefetched_sandbox_adopted_no_double_provision(tmp_path, monkeypatch):
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    feature = _feature()
    runner = _runner()
    workspace_root, feature_root, repo = _workspace(tmp_path)

    prefetched = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=987_000_000,
        prefetch=True,
    )
    assert prefetched is not None
    assert prefetched.binding is None  # preparation only — never runtime-bound
    assert str(prefetched.lease.status) == "allocated"
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 1

    group_dir = _sandbox_group_dir(workspace_root, feature.id, 3)
    dirs_after_prefetch = sorted(p.name for p in group_dir.iterdir())
    assert dirs_after_prefetch == ["attempt-987000000"]

    adopted = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=42,  # simulates the durable dispatcher attempt id
    )
    assert adopted is not None
    # Same lease, now runtime-bound, on the prefetch runner.
    assert adopted.lease.sandbox_id == prefetched.lease.sandbox_id
    assert adopted.runner is prefetched.runner
    assert adopted.binding is not None
    # The entry is consumed and NO second sandbox directory was provisioned.
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 0
    dirs_after_dispatch = sorted(p.name for p in group_dir.iterdir())
    assert dirs_after_dispatch == ["attempt-987000000"]


@pytest.mark.asyncio
async def test_stale_prefetch_not_adopted_and_released(tmp_path, monkeypatch):
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    feature = _feature()
    runner = _runner()
    workspace_root, feature_root, repo = _workspace(tmp_path)

    prefetched = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=987_000_000,
        prefetch=True,
    )
    assert prefetched is not None

    # Wave N seals: the repo HEAD moves between prefetch and real dispatch.
    (repo / "tracked.txt").write_text("seal\n", encoding="utf-8")
    _git(repo, "commit", "-aqm", "seal")

    adopted = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=42,
    )
    assert adopted is not None
    # Fresh provision — staleness can never be adopted.
    assert adopted.lease.sandbox_id != prefetched.lease.sandbox_id
    group_dir = _sandbox_group_dir(workspace_root, feature.id, 3)
    assert sorted(p.name for p in group_dir.iterdir()) == [
        "attempt-42",
        "attempt-987000000",
    ]
    # The stale entry is still registered; the controller releases it.
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 1
    controller = implementation_module._WavePrefetchController(
        runner, feature, enabled=True,
    )
    await controller.settle(group_idx=4)  # next wave: g3 entries are stale
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 0
    assert sorted(p.name for p in group_dir.iterdir()) == ["attempt-42"]


@pytest.mark.asyncio
async def test_flag_off_registry_not_consumed(tmp_path, monkeypatch):
    feature = _feature()
    runner = _runner()
    workspace_root, feature_root, repo = _workspace(tmp_path)

    # Register a perfectly matching prefetched sandbox while the flag is ON.
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    prefetched = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=987_000_000,
        prefetch=True,
    )
    assert prefetched is not None

    # Flag off: the real dispatch never reads the registry — legacy path.
    monkeypatch.setenv(_FLAG_ENV, "0")
    adopted = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=42,
    )
    assert adopted is not None
    assert adopted.lease.sandbox_id != prefetched.lease.sandbox_id
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 1
    group_dir = _sandbox_group_dir(workspace_root, feature.id, 3)
    assert sorted(p.name for p in group_dir.iterdir()) == [
        "attempt-42",
        "attempt-987000000",
    ]


# ── Controller guards ────────────────────────────────────────────────────────


def _dag_stub(groups: int = 4):
    return SimpleNamespace(
        execution_order=[[f"T-{g}"] for g in range(groups)],
        tasks=[_task(f"T-{g}") for g in range(groups)],
    )


def _tasks_by_id(dag) -> dict:
    return {t.id: t for t in dag.tasks}


def _spawn(controller, *, group_idx: int = 1, dag=None, **overrides) -> bool:
    dag = dag or _dag_stub()
    kwargs = dict(
        dag=dag,
        group_idx=group_idx,
        tasks_by_id=_tasks_by_id(dag),
        dag_sha256="dag-sha",
        workspace_mgr=SimpleNamespace(_base="/tmp/ws"),
        feature_root=Path("/tmp/ws/.iriai/features/ws-feat/repos"),
        handover=implementation_module.HandoverDoc(),
        regroup_in_play=False,
        regroup_offset=999,
        regroup_overlay_applied=False,
    )
    kwargs.update(overrides)
    return controller.spawn(**kwargs)


@pytest.mark.asyncio
async def test_quiesce_boundary_wave_not_prefetched(monkeypatch):
    # Dispatch quiesces before group G iff G-1 is listed
    # (_maybe_quiesce_before_group_dispatch); prefetch from wave N skips
    # wave N+1 exactly when N is listed — the same derivation.
    monkeypatch.setenv("IRIAI_QUIESCE_GROUP_INDEXES", "1")
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    controller = implementation_module._WavePrefetchController(
        _runner(), _feature(), enabled=True,
    )
    assert _spawn(controller, group_idx=1) is False
    assert controller._task is None


@pytest.mark.asyncio
async def test_unresolved_regroup_offset_not_prefetched(monkeypatch):
    monkeypatch.delenv("IRIAI_QUIESCE_GROUP_INDEXES", raising=False)
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    controller = implementation_module._WavePrefetchController(
        _runner(), _feature(), enabled=True,
    )
    assert (
        _spawn(
            controller,
            group_idx=1,
            regroup_in_play=True,
            regroup_offset=2,
            regroup_overlay_applied=False,
        )
        is False
    )
    # Overlay already applied => the loop dag IS the effective order; prefetch
    # proceeds (stubbed wave body so nothing heavy runs).
    async def _noop_wave(**kwargs):
        return None

    monkeypatch.setattr(controller, "_prefetch_wave", _noop_wave)
    assert (
        _spawn(
            controller,
            group_idx=1,
            regroup_in_play=True,
            regroup_offset=2,
            regroup_overlay_applied=True,
        )
        is True
    )
    await controller.settle(group_idx=2)


@pytest.mark.asyncio
async def test_flag_off_controller_never_spawns(monkeypatch):
    monkeypatch.delenv("IRIAI_QUIESCE_GROUP_INDEXES", raising=False)
    controller = implementation_module._WavePrefetchController(
        _runner(), _feature(), enabled=False,
    )
    assert _spawn(controller, group_idx=1) is False
    assert controller._task is None
    await controller.settle(group_idx=2)  # no-op, never raises


@pytest.mark.asyncio
async def test_last_wave_not_prefetched(monkeypatch):
    monkeypatch.delenv("IRIAI_QUIESCE_GROUP_INDEXES", raising=False)
    controller = implementation_module._WavePrefetchController(
        _runner(), _feature(), enabled=True,
    )
    dag = _dag_stub(groups=2)
    assert _spawn(controller, group_idx=1, dag=dag) is False


# ── Failure isolation + shutdown ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prefetch_failure_never_affects_wave_dispatch(monkeypatch):
    monkeypatch.delenv("IRIAI_QUIESCE_GROUP_INDEXES", raising=False)
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    controller = implementation_module._WavePrefetchController(
        _runner(), _feature(), enabled=True,
    )

    async def _exploding_wave(**kwargs):
        raise RuntimeError("prefetch exploded")

    monkeypatch.setattr(controller, "_prefetch_wave", _exploding_wave)
    assert _spawn(controller, group_idx=1) is True

    # Wave N's own work proceeds concurrently and is unaffected.
    wave_results = await asyncio.gather(*[asyncio.sleep(0, result=i) for i in range(3)])
    assert wave_results == [0, 1, 2]

    # settle() swallows the prefetch failure (WARN-only) and never raises.
    await controller.settle(group_idx=2)
    assert controller._task is None


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_and_releases_unadopted(monkeypatch):
    monkeypatch.delenv("IRIAI_QUIESCE_GROUP_INDEXES", raising=False)
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    feature = _feature()
    controller = implementation_module._WavePrefetchController(
        _runner(), feature, enabled=True,
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hanging_wave(**kwargs):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(controller, "_prefetch_wave", _hanging_wave)
    assert _spawn(controller, group_idx=1) is True
    await asyncio.wait_for(started.wait(), timeout=5)

    released: list[str] = []

    class _Runner:
        async def release(self, lease, disposition):
            released.append(f"{lease.sandbox_id}:{disposition}")

    entry = implementation_module._WavePrefetchEntry(
        feature_id=feature.id,
        group_idx=2,
        task_id="T-2",
        content_key="idem:sandbox-content:test",
        spec=None,
        runner=_Runner(),
        lease=SimpleNamespace(sandbox_id="sb-1", status="allocated"),
    )
    implementation_module._wave_prefetch_register(entry)

    await controller.shutdown()
    assert cancelled.is_set()
    assert controller._task is None
    assert released == ["sb-1:release"]
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 0


# ── Prompt artifact digest keying ────────────────────────────────────────────


def _prompt_builder(runner, feature, *, inline_prompt: str = "Implement T-1."):
    return implementation_module._ImplementationPromptBuilder(
        runner=runner,
        feature=feature,
        task=_task("T-1"),
        repo_prefix="",
        task_contract=None,
        handover_context="",
        inline_prompt=inline_prompt,
        log_label="W-S T-1",
    )


def _prompt_keys(runner) -> list[str]:
    return sorted(
        key for key in runner.artifacts.store if key.startswith("dag-dispatch-prompt:")
    )


@pytest.mark.asyncio
async def test_prompt_artifact_reused_when_digests_unchanged():
    runner = _runner()
    feature = _feature()
    request = SimpleNamespace(group_idx=4, request_digest="e" * 64)

    first = await _prompt_builder(runner, feature).build_prompt_context(request)
    keys_after_prefetch = _prompt_keys(runner)
    assert len(keys_after_prefetch) == 1

    # Real dispatch with unchanged task content + context digests: the SAME
    # digest-keyed artifact is written (idempotent) — no new key appears.
    second = await _prompt_builder(runner, feature).build_prompt_context(request)
    assert _prompt_keys(runner) == keys_after_prefetch
    assert second.bundle.prompt_sha256 == first.bundle.prompt_sha256


@pytest.mark.asyncio
async def test_prompt_artifact_rebuilt_when_amendment_lands(monkeypatch):
    monkeypatch.delenv("IRIAI_TASK_AMENDMENTS", raising=False)
    runner = _runner()
    feature = _feature()
    request = SimpleNamespace(group_idx=4, request_digest="e" * 64)

    first = await _prompt_builder(runner, feature).build_prompt_context(request)
    keys_after_prefetch = _prompt_keys(runner)
    assert len(keys_after_prefetch) == 1

    # An amendment lands between prefetch and dispatch: it is read FRESH into
    # the prompt (P-14), so prompt_sha — and the artifact key — change and the
    # dispatcher rebuilds. No staleness risk.
    amendment_key = implementation_module._task_amendments_artifact_key("T-1")
    runner.artifacts.store[amendment_key] = "Per DEC-7: also update the docs."

    second = await _prompt_builder(runner, feature).build_prompt_context(request)
    assert second.bundle.prompt_sha256 != first.bundle.prompt_sha256
    new_keys = _prompt_keys(runner)
    assert len(new_keys) == 2
    assert keys_after_prefetch[0] in new_keys


# ── N-25: process-scoped prefetch lease identity ─────────────────────────────
#
# The durable sandbox-lease idempotency key mixes in attempt_no (default
# IRIAI_SANDBOX_REUSE_ON_RETRY=off). With deterministic prefetch numbering
# (BASE + task_idx) a successor process rebuilt byte-identical keys; once a
# dead run's prefetch leases went terminal, store._insert_or_reuse_sandbox_
# lease raised IdempotencyConflict("terminal sandbox lease cannot be reused
# for active allocation") forever (observed live: develop16 g3 attempts
# 987000000-987000003). The fix salts attempt_no with a per-boot nonce.

_TERMINAL_LEASE_STATUSES = {"captured", "released", "retained", "failed", "poisoned"}


class _FakeDurableLeaseStore:
    """Mimics ExecutionControlStore._insert_or_reuse_sandbox_lease's
    terminal-reuse rule (execution_control/store.py: a row found by
    (feature_id, idempotency_key) whose status is terminal cannot be
    re-allocated as active)."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    def allocate(self, idempotency_key: str) -> None:
        status = self.rows.get(idempotency_key)
        if status in _TERMINAL_LEASE_STATUSES:
            raise IdempotencyConflict(
                "terminal sandbox lease cannot be reused for active allocation"
            )
        self.rows[idempotency_key] = "allocated"

    def release(self, idempotency_key: str) -> None:
        self.rows[idempotency_key] = "released"


@pytest.fixture()
def fresh_boot_salt():
    """Reset the per-boot salt; returns a callable simulating a new boot."""

    def _reset(value: int | None = None) -> None:
        implementation_module._WAVE_PREFETCH_BOOT_SALT = value

    _reset()
    yield _reset
    _reset()


def _prefetch_spec(task_idx: int = 0) -> SandboxSpec:
    return _spec(
        attempt_no=implementation_module._wave_prefetch_attempt_no(task_idx)
    )


def test_two_boots_prefetch_same_wave_no_idempotency_conflict(
    monkeypatch, fresh_boot_salt
):
    monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
    store = _FakeDurableLeaseStore()

    # Boot 1 prefetches the wave; the run dies and its lease goes terminal.
    fresh_boot_salt(11)
    boot1 = _prefetch_spec()
    store.allocate(boot1.idempotency_key)
    store.release(boot1.idempotency_key)

    # Boot 2 (successor process, same wave, same content): fresh salt means a
    # fresh durable key — allocation succeeds, no IdempotencyConflict.
    fresh_boot_salt(12)
    boot2 = _prefetch_spec()
    assert boot2.attempt_no != boot1.attempt_no
    assert boot2.idempotency_key != boot1.idempotency_key
    store.allocate(boot2.idempotency_key)  # must not raise

    # The defect class this fixes: a deterministic attempt_no reproduces the
    # dead boot's key exactly and is permanently rejected.
    legacy = _spec(
        attempt_no=implementation_module._WAVE_PREFETCH_ATTEMPT_NO_BASE + 0
    )
    store.allocate(legacy.idempotency_key)
    store.release(legacy.idempotency_key)
    with pytest.raises(IdempotencyConflict):
        store.allocate(legacy.idempotency_key)


def test_prefetch_attempt_ids_stay_in_reserved_range(fresh_boot_salt):
    base = implementation_module._WAVE_PREFETCH_ATTEMPT_NO_BASE
    ceiling = implementation_module._WAVE_PREFETCH_ATTEMPT_NO_CEILING
    for salt in (0, 11, implementation_module._WAVE_PREFETCH_BOOT_SALT_SPAN - 1):
        fresh_boot_salt(salt)
        for task_idx in (0, 7, 999):
            attempt_no = implementation_module._wave_prefetch_attempt_no(task_idx)
            assert base <= attempt_no < ceiling
            assert implementation_module._is_wave_prefetch_attempt_no(attempt_no)
    # Real attempt ids — the legacy (attempt * 1000 + task_idx) shape and
    # durable dispatcher attempt row ids (a sequence from 1) — are NEVER in
    # the reserved prefetch namespace.
    for real_attempt_no in (0, 1, 42, 1_000, 5_001, 123_456, base - 1):
        assert not implementation_module._is_wave_prefetch_attempt_no(
            real_attempt_no
        )


def test_boot_salt_drawn_once_per_process_and_in_range(fresh_boot_salt):
    first = implementation_module._wave_prefetch_boot_salt()
    second = implementation_module._wave_prefetch_boot_salt()
    assert first == second  # stable within one boot
    assert 0 <= first < implementation_module._WAVE_PREFETCH_BOOT_SALT_SPAN
    # Distinct tasks in one wave get distinct ids inside the reserved range.
    attempt_nos = {
        implementation_module._wave_prefetch_attempt_no(i) for i in range(8)
    }
    assert len(attempt_nos) == 8
    assert all(
        implementation_module._is_wave_prefetch_attempt_no(no)
        for no in attempt_nos
    )


def test_salted_attempt_no_keeps_content_key_for_adoption(
    monkeypatch, fresh_boot_salt
):
    monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
    fresh_boot_salt(11)
    prefetch = _prefetch_spec()
    real_dispatch = _spec(attempt_no=42)  # durable dispatcher attempt id
    # Adoption matching is attempt-free: the content key is IDENTICAL across
    # the salted prefetch spec and the real dispatch spec...
    assert prefetch.content_idempotency_key == real_dispatch.content_idempotency_key
    # ...and across boots (content_idempotency_key never sees attempt_no), so
    # only the durable allocation key is process-scoped.
    fresh_boot_salt(12)
    assert (
        _prefetch_spec().content_idempotency_key
        == prefetch.content_idempotency_key
    )
    assert prefetch.idempotency_key != real_dispatch.idempotency_key


@pytest.mark.asyncio
async def test_adoption_end_to_end_with_salted_prefetch_attempt(
    tmp_path, monkeypatch, fresh_boot_salt
):
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    monkeypatch.delenv("IRIAI_SANDBOX_REUSE_ON_RETRY", raising=False)
    fresh_boot_salt(11)
    feature = _feature()
    runner = _runner()
    workspace_root, feature_root, repo = _workspace(tmp_path)

    salted_attempt_no = implementation_module._wave_prefetch_attempt_no(0)
    prefetched = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=salted_attempt_no,
        prefetch=True,
    )
    assert prefetched is not None
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 1

    adopted = await _bind(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        repo=repo,
        attempt_no=42,  # the durable dispatcher attempt id
    )
    assert adopted is not None
    assert adopted.lease.sandbox_id == prefetched.lease.sandbox_id
    assert adopted.binding is not None
    assert len(implementation_module._WAVE_PREFETCH_REGISTRY) == 0
    # No second provision: the only sandbox dir is the salted prefetch root.
    group_dir = _sandbox_group_dir(workspace_root, feature.id, 3)
    assert sorted(p.name for p in group_dir.iterdir()) == [
        f"attempt-{salted_attempt_no}"
    ]
