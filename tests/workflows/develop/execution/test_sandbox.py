from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.execution_control.models import SandboxLease as StoredSandboxLease
from iriai_build_v2.workflows.develop.execution import sandbox as sandbox_module
from iriai_build_v2.workflows.develop.execution.sandbox import (
    SandboxAllocationError,
    SandboxIsolationError,
    SandboxLease,
    SandboxReleaseError,
    SandboxRunner,
    SandboxSpec,
    _stable_digest,
)


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


class FakeArtifactWriter:
    def __init__(self) -> None:
        self.records: list[tuple[str, bytes, dict[str, object]]] = []

    async def write_artifact_bytes(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, object],
    ) -> int:
        self.records.append((key, data, metadata))
        return len(self.records)


class FakeStore:
    def __init__(self) -> None:
        self.leases: list[object] = []
        self.patch_summaries: list[object] = []

    async def record_sandbox_lease(self, lease: object, *_args: object) -> None:
        self.leases.append(lease)

    async def record_patch_summary(self, summary: object) -> object:
        self.patch_summaries.append(summary)
        return SimpleNamespace(evidence=SimpleNamespace(id=1000 + len(self.patch_summaries)))


class DurableFakeStore:
    def __init__(self) -> None:
        self.allocated_leases: list[object] = []
        self.repo_bindings: list[object] = []
        self.runtime_bindings: list[object] = []
        self.updated_leases: list[object] = []
        self.active_leases: list[object] = []
        self.active_lease_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def allocate_sandbox_lease(
        self,
        lease: object,
        *,
        repo_bindings: tuple[object, ...] = (),
    ) -> object:
        for existing in [*self.active_leases, *self.updated_leases, *self.allocated_leases]:
            if getattr(existing, "idempotency_key", None) != getattr(lease, "idempotency_key", None):
                continue
            if (
                str(getattr(existing, "status", "")) in {"captured", "released", "retained", "failed", "poisoned"}
                and str(getattr(lease, "status", "")) not in {"captured", "released", "retained", "failed", "poisoned"}
            ):
                raise RuntimeError("terminal sandbox lease cannot be reused for active allocation")
        self.allocated_leases.append(lease)
        self.repo_bindings.extend(repo_bindings)
        return SimpleNamespace(
            lease=SimpleNamespace(id=321, lease_version=3),
            repo_bindings=repo_bindings,
        )

    async def record_runtime_workspace_binding(self, binding: object) -> object:
        if not getattr(binding, "sandbox_lease_id", None):
            raise RuntimeError("runtime binding missing sandbox lease id")
        for existing in self.runtime_bindings:
            if (
                getattr(existing, "sandbox_lease_id", None)
                == getattr(binding, "sandbox_lease_id", None)
                and getattr(existing, "runtime_name", None)
                == getattr(binding, "runtime_name", None)
            ):
                return SimpleNamespace(binding=SimpleNamespace(id=654))
        self.runtime_bindings.append(binding)
        return SimpleNamespace(binding=SimpleNamespace(id=654))

    async def update_sandbox_lease(self, lease: object, *_args: object) -> object:
        lease_id = getattr(lease, "sandbox_lease_id", None) or getattr(lease, "id", None)
        idempotency_key = getattr(lease, "idempotency_key", None)
        existing = [
            *self.active_leases,
            *self.updated_leases,
            *self.allocated_leases,
        ]
        if not any(
            (
                lease_id is not None
                and (
                    getattr(item, "sandbox_lease_id", None)
                    or getattr(item, "id", None)
                )
                == lease_id
            )
            or (
                idempotency_key
                and getattr(item, "idempotency_key", None) == idempotency_key
            )
            for item in existing
        ):
            raise RuntimeError("sandbox lease update target not found")
        self.updated_leases.append(lease)
        return SimpleNamespace(
            lease=SimpleNamespace(
                id=getattr(lease, "sandbox_lease_id", None) or getattr(lease, "id", 321),
                lease_version=(getattr(lease, "lease_version", 0) or 0) + 1,
            )
        )

    async def list_active_sandbox_leases(self, *args: object, **kwargs: object) -> list[object]:
        self.active_lease_calls.append((args, kwargs))
        return list(self.active_leases)

    async def get_sandbox_lease_by_idempotency_key(
        self,
        _feature_id: str,
        idempotency_key: str,
    ) -> object | None:
        candidates = [
            *self.active_leases,
            *self.updated_leases,
            *self.allocated_leases,
        ]
        for lease in candidates:
            if getattr(lease, "idempotency_key", None) == idempotency_key:
                return lease
        return None


class DurableAllocationFailsStore(DurableFakeStore):
    async def allocate_sandbox_lease(
        self,
        lease: object,
        *,
        repo_bindings: tuple[object, ...] = (),
    ) -> object:
        del lease, repo_bindings
        raise TimeoutError()


class RuntimeBindingFailsOnceStore(DurableFakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_runtime_binding = True

    async def record_runtime_workspace_binding(self, binding: object) -> object:
        if self.fail_next_runtime_binding:
            self.fail_next_runtime_binding = False
            raise RuntimeError("runtime binding store down")
        return await super().record_runtime_workspace_binding(binding)


class LeaseStatusFailsOnceStore(DurableFakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_lease_update = True

    async def update_sandbox_lease(self, lease: object, *_args: object) -> object:
        if self.fail_next_lease_update:
            self.fail_next_lease_update = False
            raise RuntimeError("lease status store down")
        return await super().update_sandbox_lease(lease)


class RehydratingFakeStore:
    def __init__(self, lease: StoredSandboxLease) -> None:
        self.lease = lease

    async def get_sandbox_lease_by_idempotency_key(
        self,
        _feature_id: str,
        _idempotency_key: str,
    ) -> StoredSandboxLease:
        return self.lease


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
    (path / "delete.txt").write_text("delete me\n", encoding="utf-8")
    (path / "oldname.txt").write_text("rename me\n", encoding="utf-8")
    (path / "script.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (path / "blob.bin").write_bytes(b"\0\1\2" + bytes(range(64)))
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return git(path, "rev-parse", "HEAD")


def spec_for(base_commit: str) -> SandboxSpec:
    return SandboxSpec(
        feature_id="Feature/One",
        dag_sha256="dag-sha",
        group_idx=4,
        attempt_no=2,
        task_ids=["task-a"],
        repo_ids=["app"],
        base_snapshot_ids=[11],
        base_commits={"app": base_commit},
        mode="task",
        writable_roots=[],
        readonly_roots=[],
        contract_ids=[7],
    )


def runner_for(
    tmp_path: Path,
    source: Path,
    *,
    store: object | None = None,
    artifact_writer: object | None = None,
) -> SandboxRunner:
    return SandboxRunner(
        workspace_root=tmp_path,
        repo_sources={"app": source},
        allowed_source_roots=[tmp_path],
        store=store,
        artifact_writer=artifact_writer,
    )


def test_allocation_pins_head_writes_manifest_and_binds_runtime(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)

    lease = run(runner.allocate(spec_for(base)))

    sandbox_root = Path(lease.root)
    manifest_path = sandbox_root / "sandbox-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert sandbox_root == tmp_path / ".iriai/features/feature-one/sandboxes/g4/attempt-2"
    assert manifest["sandbox_id"] == lease.sandbox_id
    assert manifest["base_commits"] == {"app": base}
    assert Path(lease.repo_roots["app"]).is_dir()
    assert not Path(lease.repo_roots["app"]).is_symlink()
    assert git(Path(lease.repo_roots["app"]), "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain=v1") == ""

    binding = run(runner.bind_runtime(lease, "codex"))

    assert binding.cwd == lease.repo_roots["app"]
    assert binding.workspace_override == lease.repo_roots["app"]
    assert binding.repo_roots == lease.repo_roots
    assert str(source.resolve()) in binding.blocked_roots
    assert binding.env["IRIAI_SANDBOX_MANIFEST"] == str(manifest_path)
    assert binding.role_metadata["sandbox"] is True
    assert binding.authority_schema_version == "runtime-workspace-authority-grant-v1"
    assert binding.runtime_workspace_authority_grant_digest
    assert binding.runtime_workspace_authority_grants[0]["grant_type"] == "product"
    assert binding.runtime_workspace_authority_grants[0]["promotable"] is True


def test_allocate_materializes_create_parent_and_preserves_leaf_contract_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base).model_copy(update={
        "writable_roots": [
            "app:src/vs/workbench/contrib/workflowTab/views/implementation/index.ts",
        ],
        "writable_root_specs": [
            {
                "repo_id": "app",
                "path": "src/vs/workbench/contrib/workflowTab/views/implementation/index.ts",
                "match_kind": "file",
                "allow_create": True,
            }
        ],
    })

    lease = run(runner_for(tmp_path, source).allocate(spec))
    repo_root = Path(lease.repo_roots["app"])
    leaf = repo_root / "src/vs/workbench/contrib/workflowTab/views/implementation/index.ts"
    manifest = json.loads((Path(lease.root) / "sandbox-manifest.json").read_text())

    assert leaf.parent.is_dir()
    assert not leaf.exists()
    assert not (source / "src").exists()
    assert manifest["writable_roots"] == [str(leaf)]
    assert manifest["write_guard_roots"] == [str(leaf.parent)]
    assert manifest["materialized_create_parents"][0]["target"] == str(leaf.parent)
    assert manifest["authority_schema_version"] == "runtime-workspace-authority-grant-v1"
    grant = manifest["runtime_workspace_authority_grants"][0]
    assert grant["schema_version"] == "runtime-workspace-authority-grant-v1"
    assert grant["grant_type"] == "product"
    assert grant["contract_roots"] == [str(leaf)]
    assert grant["create_parent_roots"] == [str(leaf.parent)]
    assert grant["write_guard_roots"] == [str(leaf.parent)]
    assert grant["grant_digest"]
    assert git(repo_root, "status", "--porcelain=v1") == ""


def test_allocate_rejects_pre_grant_manifest_for_fresh_dispatch(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec))
    manifest_path = Path(lease.root) / "sandbox-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in (
        "authority_schema_version",
        "runtime_workspace_authority_grants",
        "runtime_workspace_authority_grant_digest",
        "promotable",
    ):
        manifest.pop(key, None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SandboxAllocationError, match="authority grant"):
        run(runner_for(tmp_path, source).allocate(spec))


def test_allocation_normalizes_clone_permissions_for_agent_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        sandbox_module,
        "_agent_shared_group",
        lambda: ("test-agents", os.getgid()),
    )
    source = tmp_path / "canonical" / "app"
    init_repo(source)
    (source / "src" / "nested").mkdir(parents=True)
    (source / "src" / "nested" / "created.py").write_text("value = 1\n", encoding="utf-8")
    (source / "script.sh").chmod(0o755)
    git(source, "add", ".")
    git(source, "commit", "-qm", "add nested executable")
    base = git(source, "rev-parse", "HEAD")
    runner = runner_for(tmp_path, source)

    lease = run(runner.allocate(spec_for(base)))

    repo = Path(lease.repo_roots["app"])
    manifest = json.loads((Path(lease.root) / "sandbox-manifest.json").read_text())
    for directory in [repo, repo / ".git", repo / "src", repo / "src" / "nested"]:
        mode = directory.stat().st_mode
        assert mode & stat.S_IWGRP
        assert mode & stat.S_IXGRP
        assert mode & stat.S_ISGID
        assert directory.stat().st_gid == os.getgid()
    for regular_file in [repo / "tracked.txt", repo / "src" / "nested" / "created.py"]:
        assert regular_file.stat().st_mode & stat.S_IWGRP
        assert regular_file.stat().st_gid == os.getgid()
    assert repo.joinpath("script.sh").stat().st_mode & stat.S_IXUSR
    assert git(repo, "status", "--porcelain=v1") == ""
    assert manifest["permission_normalization"]["scope"] == "sandbox_repo_roots"
    assert manifest["permission_normalization"]["repos"]["app"]["paths_changed"] > 0


def test_allocation_permission_normalization_skips_symlink_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        sandbox_module,
        "_agent_shared_group",
        lambda: ("test-agents", os.getgid()),
    )
    source = tmp_path / "canonical" / "app"
    init_repo(source)
    outside = tmp_path / "outside-target.txt"
    outside.write_text("outside\n", encoding="utf-8")
    outside.chmod(0o600)
    try:
        os.symlink(outside, source / "outside-link")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    git(source, "add", "outside-link")
    git(source, "commit", "-qm", "add symlink")
    base = git(source, "rev-parse", "HEAD")
    outside_mode_before = stat.S_IMODE(outside.stat().st_mode)

    lease = run(runner_for(tmp_path, source).allocate(spec_for(base)))

    repo = Path(lease.repo_roots["app"])
    manifest = json.loads((Path(lease.root) / "sandbox-manifest.json").read_text())
    assert (repo / "outside-link").is_symlink()
    assert stat.S_IMODE(outside.stat().st_mode) == outside_mode_before
    assert manifest["permission_normalization"]["repos"]["app"]["symlinks_skipped"] >= 1


def test_allocation_blocks_when_permission_normalization_cannot_chmod(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        sandbox_module,
        "_agent_shared_group",
        lambda: ("test-agents", os.getgid()),
    )
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    real_chmod = sandbox_module.os.chmod

    def _deny_repo_chmod(path: os.PathLike[str] | str, mode: int) -> None:
        if ".iriai" in str(path):
            raise PermissionError("permission denied")
        real_chmod(path, mode)

    monkeypatch.setattr(sandbox_module.os, "chmod", _deny_repo_chmod)

    with pytest.raises(SandboxAllocationError, match="permission normalization failed"):
        run(runner_for(tmp_path, source).allocate(spec_for(base)))


def test_durable_store_bridge_persists_lease_id_and_runtime_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    store = DurableFakeStore()
    runner = runner_for(tmp_path, source, store=store)

    lease = run(runner.allocate(spec_for(base)))
    binding = run(runner.bind_runtime(lease, "codex"))

    assert lease.sandbox_lease_id == 321
    assert len(store.allocated_leases) == 1
    assert len(store.repo_bindings) == 1
    stored_lease = store.allocated_leases[0]
    assert stored_lease.feature_id == "Feature/One"
    assert stored_lease.idempotency_key == spec_for(base).idempotency_key
    assert stored_lease.repo_roots == lease.repo_roots
    assert store.repo_bindings[0].sandbox_repo_root == lease.repo_roots["app"]
    assert store.repo_bindings[0].canonical_repo_root == str(source.resolve())
    assert len(store.runtime_bindings) == 1
    stored_binding = store.runtime_bindings[0]
    assert stored_binding.sandbox_lease_id == 321
    assert stored_binding.cwd == lease.repo_roots["app"]
    assert stored_binding.workspace_override == lease.repo_roots["app"]
    assert stored_binding.payload["expires_at"] == lease.expires_at
    runtime_metadata = binding.role_metadata["runtime_workspace_binding"]
    assert runtime_metadata["sandbox_lease_id"] == 321
    assert runtime_metadata["cwd"] == lease.repo_roots["app"]
    assert runtime_metadata["expires_at"] == lease.expires_at
    assert store.updated_leases[-1].status == "running"
    manifest = json.loads((Path(lease.root) / "sandbox-manifest.json").read_text(encoding="utf-8"))
    assert manifest["sandbox_lease_id"] == 321


def test_durable_store_allocation_failure_records_diagnostic_context(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    store = DurableAllocationFailsStore()
    runner = runner_for(tmp_path, source, store=store)

    with pytest.raises(SandboxAllocationError) as exc_info:
        run(runner.allocate(spec_for(base)))

    message = str(exc_info.value)
    assert "durable sandbox lease allocation failed" in message
    assert "phase=store.allocate_sandbox_lease" in message
    assert "exception_type=TimeoutError" in message
    assert "exception_repr=TimeoutError()" in message
    assert "feature_id=Feature/One" in message
    assert "group_idx=4" in message
    assert "attempt_no=2" in message
    assert "task_ids=task-a" in message
    assert "idempotency_key=" in message
    assert store.allocated_leases == []
    assert not list((tmp_path / ".iriai").glob("features/*/sandboxes/g4/attempt-2"))


def test_runtime_binding_failure_does_not_cache_non_durable_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    store = RuntimeBindingFailsOnceStore()
    runner = runner_for(tmp_path, source, store=store)
    lease = run(runner.allocate(spec_for(base)))

    with pytest.raises(RuntimeError, match="runtime binding store down"):
        run(runner.bind_runtime(lease, "codex"))

    assert lease.status == "allocated"
    assert lease.sandbox_id not in runner._runtime_bindings

    binding = run(runner.bind_runtime(lease, "codex"))

    assert binding.runtime == "codex"
    assert lease.status == "running"
    assert len(store.runtime_bindings) == 1


def test_lease_status_failure_does_not_cache_partially_durable_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    store = LeaseStatusFailsOnceStore()
    runner = runner_for(tmp_path, source, store=store)
    lease = run(runner.allocate(spec_for(base)))

    with pytest.raises(RuntimeError, match="lease status store down"):
        run(runner.bind_runtime(lease, "codex"))

    assert lease.status == "allocated"
    assert lease.sandbox_id not in runner._runtime_bindings
    assert len(store.runtime_bindings) == 1

    binding = run(runner.bind_runtime(lease, "codex"))

    assert binding.runtime == "codex"
    assert lease.status == "running"
    assert len(store.runtime_bindings) == 1


def test_store_lease_rehydration_accepts_datetime_expiration(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))

    stored = StoredSandboxLease(
        id=999,
        feature_id=lease.feature_id,
        dag_sha256=lease.dag_sha256,
        group_idx=lease.group_idx,
        attempt_no=lease.attempt_no,
        mode=lease.mode,
        status="allocated",
        lease_owner="store",
        leased_until=datetime(2026, 5, 20, 12, tzinfo=timezone.utc),
        sandbox_root=lease.root,
        sandbox_id=lease.sandbox_id,
        manifest_path=str(Path(lease.root) / "sandbox-manifest.json"),
        repo_ids=["app"],
        base_commits={"app": base},
        task_ids=["task-a"],
        contract_ids=[7],
        idempotency_key=spec_for(base).idempotency_key,
    )

    rehydrated = runner._coerce_lease(stored)

    assert rehydrated.sandbox_lease_id == 999
    assert rehydrated.expires_at == "2026-05-20T12:00:00Z"


def test_allocate_replay_preserves_durable_expiry_for_runtime_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    first_runner = runner_for(tmp_path, source)
    first = run(first_runner.allocate(spec))
    stored = StoredSandboxLease(
        id=999,
        feature_id=first.feature_id,
        dag_sha256=first.dag_sha256,
        group_idx=first.group_idx,
        attempt_no=first.attempt_no,
        mode=first.mode,
        status="allocated",
        lease_owner=first.owner,
        leased_until=datetime(2026, 5, 20, 12, tzinfo=timezone.utc),
        sandbox_root=first.root,
        sandbox_id=first.sandbox_id,
        manifest_path=str(Path(first.root) / "sandbox-manifest.json"),
        repo_ids=["app"],
        base_commits={"app": base},
        task_ids=["task-a"],
        contract_ids=[7],
        idempotency_key=spec.idempotency_key,
    )
    runner = runner_for(tmp_path, source, store=RehydratingFakeStore(stored))

    replay = run(runner.allocate(spec))
    binding = run(runner.bind_runtime(replay, "codex"))

    assert replay.sandbox_lease_id == 999
    assert replay.expires_at == "2026-05-20T12:00:00Z"
    assert binding.expires_at == "2026-05-20T12:00:00Z"


def test_writable_root_escape_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base).model_copy(update={"writable_roots": ["app:../../escape"]})
    runner = runner_for(tmp_path, source)

    with pytest.raises(SandboxAllocationError, match="escapes sandbox"):
        run(runner.allocate(spec))


@pytest.mark.parametrize("redirected_component", ["sandboxes", "g4"])
def test_allocate_rejects_symlinked_sandbox_ancestor_before_outside_mutation(
    tmp_path: Path,
    redirected_component: str,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    outside = tmp_path.parent / f"{tmp_path.name}-{redirected_component}-outside"
    outside.mkdir()
    feature_root = tmp_path / ".iriai" / "features" / "feature-one"
    if redirected_component == "sandboxes":
        feature_root.mkdir(parents=True)
        os.symlink(outside, feature_root / "sandboxes")
    else:
        sandboxes_root = feature_root / "sandboxes"
        sandboxes_root.mkdir(parents=True)
        os.symlink(outside, sandboxes_root / "g4")
    store = DurableFakeStore()
    runner = runner_for(tmp_path, source, store=store)

    with pytest.raises(SandboxAllocationError, match="symlink"):
        run(runner.allocate(spec_for(base)))

    assert not (outside / "attempt-2").exists()
    assert list(outside.rglob("sandbox-manifest.json")) == []
    assert store.allocated_leases == []


def test_repeated_allocate_is_idempotent_at_path_and_manifest_level(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    runner = runner_for(tmp_path, source)

    first = run(runner.allocate(spec))
    manifest_path = Path(first.root) / "sandbox-manifest.json"
    manifest_before = manifest_path.read_text(encoding="utf-8")
    second_runner = runner_for(tmp_path, source)
    second = run(second_runner.allocate(spec))

    assert second.sandbox_id == first.sandbox_id
    assert second.root == first.root
    assert second.repo_roots == first.repo_roots
    assert manifest_path.read_text(encoding="utf-8") == manifest_before


def test_terminal_manifest_is_not_resurrected(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec))
    manifest_path = Path(lease.root) / "sandbox-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "poisoned"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SandboxAllocationError, match="poisoned lease"):
        run(runner_for(tmp_path, source).allocate(spec))


def test_released_terminal_manifest_is_cleaned_before_reallocation(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec))
    root = Path(lease.root)
    manifest_path = root / "sandbox-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "released"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    reallocated = run(runner_for(tmp_path, source).allocate(spec))

    assert reallocated.sandbox_id == lease.sandbox_id
    assert root.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "allocated"


def test_release_refuses_unowned_roots_deletes_manifest_owned_root_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))
    sibling = Path(lease.root).parent / "attempt-keep"
    sibling.mkdir()

    fake_root = tmp_path / ".iriai/features/feature-one/sandboxes/g4/fake"
    fake_root.mkdir(parents=True)
    fake_lease = SandboxLease(
        sandbox_id="fake",
        root=str(fake_root),
        repo_roots={},
        base_commits={},
        expires_at=lease.expires_at,
        owner="test",
        status="captured",
        patch_summary_ids=[],
    )
    with pytest.raises(SandboxReleaseError):
        run(runner.release(fake_lease, "release"))
    assert fake_root.exists()

    run(runner.release(lease, "retain"))
    assert Path(lease.root).exists()
    assert lease.status == "retained"

    run(runner.release(lease, "release"))
    assert not Path(lease.root).exists()
    assert sibling.exists()
    assert lease.status == "released"

    run(runner.release(lease, "release"))
    assert lease.status == "released"


def test_release_requires_owner_match(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))
    lease.owner = "other-owner"

    with pytest.raises(SandboxReleaseError, match="owner"):
        run(runner.release(lease, "release"))


def test_release_persists_before_deleting_sandbox_root(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    store = LeaseStatusFailsOnceStore()
    runner = runner_for(tmp_path, source, store=store)
    lease = run(runner.allocate(spec_for(base)))
    root = Path(lease.root)

    with pytest.raises(RuntimeError, match="lease status store down"):
        run(runner.release(lease, "release"))

    assert root.exists()
    run(runner.release(lease, "release"))
    assert not root.exists()
    assert store.updated_leases[-1].status == "released"


def test_recover_retains_capturing_crash_evidence(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))
    manifest_path = Path(lease.root) / "sandbox-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "capturing"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    recovered = run(runner.recover())

    assert recovered[0].status == "retained"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "retained"


def test_recover_retains_running_crash_evidence_and_skips_captured(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    running = run(runner.allocate(spec_for(base)))
    running_manifest_path = Path(running.root) / "sandbox-manifest.json"
    running_manifest = json.loads(running_manifest_path.read_text(encoding="utf-8"))
    running_manifest["status"] = "running"
    running_manifest_path.write_text(json.dumps(running_manifest), encoding="utf-8")

    captured_spec = spec_for(base).model_copy(update={"attempt_no": 9})
    captured = run(runner.allocate(captured_spec))
    captured_manifest_path = Path(captured.root) / "sandbox-manifest.json"
    captured_manifest = json.loads(captured_manifest_path.read_text(encoding="utf-8"))
    captured_manifest["status"] = "captured"
    captured_manifest_path.write_text(json.dumps(captured_manifest), encoding="utf-8")

    recovered = run(runner.recover())

    assert [lease.sandbox_id for lease in recovered] == [running.sandbox_id]
    assert recovered[0].status == "retained"
    assert json.loads(running_manifest_path.read_text(encoding="utf-8"))["status"] == "retained"


def test_recover_marks_durable_only_active_lease_failed(tmp_path: Path) -> None:
    store = DurableFakeStore()
    store.active_leases.append(
        SandboxLease(
            id=999,
            sandbox_lease_id=999,
            feature_id="Feature/One",
            dag_sha256="dag-sha",
            group_idx=4,
            attempt_no=8,
            mode="task",
            idempotency_key="idem:missing",
            sandbox_id="missing-lease",
            root=str(tmp_path / ".iriai/features/feature-one/sandboxes/g4/attempt-8"),
            manifest_path=str(
                tmp_path
                / ".iriai/features/feature-one/sandboxes/g4/attempt-8/sandbox-manifest.json"
            ),
            repo_roots={"app": str(tmp_path / "missing-repo")},
            base_commits={"app": "abc"},
            expires_at=datetime.now(timezone.utc).isoformat(),
            owner="test",
            status="running",
            patch_summary_ids=[],
        )
    )
    store.active_leases.append(
        SandboxLease(
            id=1000,
            sandbox_lease_id=1000,
            feature_id="Feature/Other",
            dag_sha256="dag-sha",
            group_idx=4,
            attempt_no=9,
            mode="task",
            idempotency_key="idem:other",
            sandbox_id="other-lease",
            root=str(tmp_path / ".iriai/features/feature-other/sandboxes/g4/attempt-9"),
            manifest_path=str(
                tmp_path
                / ".iriai/features/feature-other/sandboxes/g4/attempt-9/sandbox-manifest.json"
            ),
            repo_roots={"app": str(tmp_path / "other-repo")},
            base_commits={"app": "abc"},
            expires_at=datetime.now(timezone.utc).isoformat(),
            owner="workflow:other:g4:t1:a9",
            status="running",
            patch_summary_ids=[],
        )
    )
    runner = SandboxRunner(
        workspace_root=tmp_path,
        repo_sources={},
        allowed_source_roots=[tmp_path],
        store=store,
        owner="test",
    )

    recovered = run(runner.recover())

    assert [lease.sandbox_id for lease in recovered] == ["missing-lease"]
    assert recovered[0].status == "failed"
    assert store.updated_leases[-1].status == "failed"
    assert store.active_lease_calls == [((), {"owner": "test"})]


def test_running_durable_lease_is_retained_and_same_key_reallocation_blocks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    store = DurableFakeStore()
    first_runner = runner_for(tmp_path, source, store=store)
    lease = run(first_runner.allocate(spec))
    run(first_runner.bind_runtime(lease, "codex"))
    store.active_leases = [lease]

    root = Path(lease.root)
    manifest_path = root / "sandbox-manifest.json"

    with pytest.raises(SandboxAllocationError, match="retained crashed sandbox evidence"):
        run(runner_for(tmp_path, source, store=store).allocate(spec))

    assert root.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "retained"
    assert any(item.status == "retained" for item in store.updated_leases)


def test_terminal_durable_lease_blocks_stale_nonterminal_manifest_reallocation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    store = DurableFakeStore()
    first_runner = runner_for(tmp_path, source, store=store)
    lease = run(first_runner.allocate(spec))
    root = Path(lease.root)
    manifest_path = root / "sandbox-manifest.json"
    stale_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stale_manifest["status"] = "running"
    manifest_path.write_text(json.dumps(stale_manifest), encoding="utf-8")
    lease.status = "released"
    store.active_leases = [lease]

    with pytest.raises(SandboxAllocationError, match="terminal sandbox lease"):
        run(runner_for(tmp_path, source, store=store).allocate(spec))

    assert not manifest_path.exists()
    assert store.allocated_leases[-1].idempotency_key == spec.idempotency_key


def test_retained_durable_lease_preserves_evidence_on_same_key_retry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    store = DurableFakeStore()
    first_runner = runner_for(tmp_path, source, store=store)
    lease = run(first_runner.allocate(spec))
    root = Path(lease.root)
    manifest_path = root / "sandbox-manifest.json"
    run(first_runner.release(lease, "retain"))

    with pytest.raises(SandboxAllocationError, match="retained sandbox evidence"):
        run(runner_for(tmp_path, source, store=store).allocate(spec))

    assert root.exists()
    assert manifest_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "retained"


def test_retained_mismatched_manifest_requires_new_attempt_without_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec))
    root = Path(lease.root)
    manifest_path = root / "sandbox-manifest.json"
    run(runner.release(lease, "retain"))
    retained_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    next_spec = spec.model_copy(update={"contract_ids": [99]})

    with pytest.raises(
        SandboxAllocationError,
        match="terminal sandbox lease requires a new attempt",
    ):
        run(runner_for(tmp_path, source).allocate(next_spec))

    assert root.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == retained_manifest


def test_active_mismatched_manifest_still_fails_as_different_lease(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    spec = spec_for(base)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec))
    root = Path(lease.root)
    manifest_path = root / "sandbox-manifest.json"
    active_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    next_spec = spec.model_copy(update={"contract_ids": [99]})

    with pytest.raises(SandboxAllocationError, match="sandbox path already belongs"):
        run(runner_for(tmp_path, source).allocate(next_spec))

    assert root.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == active_manifest


def test_recover_scopes_durable_leases_by_feature_and_owner_prefix(
    tmp_path: Path,
) -> None:
    store = DurableFakeStore()
    matching_root = tmp_path / ".iriai/features/feature-one/sandboxes/g4/attempt-8"
    matching_root.mkdir(parents=True)
    (matching_root / "sandbox-manifest.json").write_text(
        json.dumps(
                {
                    "manifest_version": "sandbox-runner-v1",
                    "sandbox_id": "matching-lease",
                "root": str(matching_root),
                "manifest_path": str(matching_root / "sandbox-manifest.json"),
                "feature_id": "Feature/One",
                "dag_sha256": "dag-sha",
                "group_idx": 4,
                "attempt_no": 8,
                "idempotency_key": "idem:matching",
                "repo_roots": {},
                "base_commits": {},
                "status": "running",
                "owner": "workflow:Feature/One:g4:t1:a8",
                "mode": "task",
                "patch_summary_ids": [],
            }
        ),
        encoding="utf-8",
    )
    store.active_leases.extend(
        [
            SandboxLease(
                feature_id="Feature/One",
                dag_sha256="dag-sha",
                group_idx=4,
                attempt_no=8,
                mode="task",
                idempotency_key="idem:matching",
                sandbox_id="matching-lease",
                root=str(matching_root),
                manifest_path=str(matching_root / "sandbox-manifest.json"),
                repo_roots={},
                base_commits={},
                expires_at=datetime.now(timezone.utc).isoformat(),
                owner="workflow:Feature/One:g4:t1:a8",
                status="running",
                patch_summary_ids=[],
            ),
            SandboxLease(
                feature_id="Feature/Other",
                dag_sha256="dag-sha",
                group_idx=4,
                attempt_no=8,
                mode="task",
                idempotency_key="idem:other",
                sandbox_id="other-lease",
                root=str(tmp_path / "other"),
                manifest_path=str(tmp_path / "other" / "sandbox-manifest.json"),
                repo_roots={},
                base_commits={},
                expires_at=datetime.now(timezone.utc).isoformat(),
                owner="workflow:Feature/Other:g4:t1:a8",
                status="running",
                patch_summary_ids=[],
            ),
        ]
    )
    runner = SandboxRunner(
        workspace_root=tmp_path,
        repo_sources={},
        allowed_source_roots=[tmp_path],
        store=store,
        owner="workflow:Feature/One",
        recovery_owner_prefix="workflow:Feature/One:",
        recovery_feature_id="Feature/One",
    )

    recovered = run(runner.recover())

    assert [lease.sandbox_id for lease in recovered] == ["matching-lease"]
    assert [lease.sandbox_id for lease in store.updated_leases] == ["matching-lease"]
    assert store.active_lease_calls == [
        (
            (),
            {
                "feature_id": "Feature/One",
                "owner_prefix": "workflow:Feature/One:",
            },
        )
    ]


def test_recover_retains_manifest_only_leases_when_durable_store_exists(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    lease = run(runner_for(tmp_path, source).allocate(spec_for(base)))
    manifest_path = Path(lease.root) / "sandbox-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = DurableFakeStore()

    recovered = run(runner_for(tmp_path, source, store=store).recover())

    assert [lease.sandbox_id for lease in recovered] == [lease.sandbox_id]
    assert [lease.sandbox_id for lease in store.allocated_leases] == [lease.sandbox_id]
    assert [lease.sandbox_id for lease in store.updated_leases] == [lease.sandbox_id]
    retained_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert retained_manifest["sandbox_lease_id"] == 321
    assert retained_manifest["status"] == "retained"


def test_recover_skips_corrupt_manifest_only_lease_and_retains_later_manifests(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    valid_lease = run(runner_for(tmp_path, source).allocate(spec_for(base)))
    valid_manifest_path = Path(valid_lease.root) / "sandbox-manifest.json"
    valid_manifest = json.loads(valid_manifest_path.read_text(encoding="utf-8"))
    valid_manifest["status"] = "running"
    valid_manifest_path.write_text(json.dumps(valid_manifest), encoding="utf-8")
    corrupt_manifest_path = (
        tmp_path
        / ".iriai"
        / "features"
        / "000-corrupt"
        / "sandboxes"
        / "g1"
        / "attempt-1"
        / "sandbox-manifest.json"
    )
    corrupt_manifest_path.parent.mkdir(parents=True)
    corrupt_manifest_path.write_text("{not valid json", encoding="utf-8")
    store = DurableFakeStore()

    recovered = run(runner_for(tmp_path, source, store=store).recover())

    assert [lease.sandbox_id for lease in recovered] == [valid_lease.sandbox_id]
    assert [lease.sandbox_id for lease in store.allocated_leases] == [valid_lease.sandbox_id]
    assert [lease.sandbox_id for lease in store.updated_leases] == [valid_lease.sandbox_id]
    assert json.loads(valid_manifest_path.read_text(encoding="utf-8"))["status"] == "retained"
    assert corrupt_manifest_path.exists()


def test_recover_skips_semantically_corrupt_manifest_only_lease(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    valid_lease = run(runner_for(tmp_path, source).allocate(spec_for(base)))
    valid_manifest_path = Path(valid_lease.root) / "sandbox-manifest.json"
    valid_manifest = json.loads(valid_manifest_path.read_text(encoding="utf-8"))
    valid_manifest["status"] = "running"
    valid_manifest_path.write_text(json.dumps(valid_manifest), encoding="utf-8")
    corrupt_manifest_path = (
        tmp_path
        / ".iriai"
        / "features"
        / "000-semantic-corrupt"
        / "sandboxes"
        / "g1"
        / "attempt-1"
        / "sandbox-manifest.json"
    )
    corrupt_manifest_path.parent.mkdir(parents=True)
    corrupt_manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "sandbox-runner-v1",
                "root": str(corrupt_manifest_path.parent),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    store = DurableFakeStore()

    recovered = run(runner_for(tmp_path, source, store=store).recover())

    assert [lease.sandbox_id for lease in recovered] == [valid_lease.sandbox_id]
    assert [lease.sandbox_id for lease in store.allocated_leases] == [valid_lease.sandbox_id]
    assert json.loads(valid_manifest_path.read_text(encoding="utf-8"))["status"] == "retained"


def test_recover_rejects_manifest_only_root_that_differs_from_discovered_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    valid_lease = run(runner_for(tmp_path, source).allocate(spec_for(base)))
    valid_manifest_path = Path(valid_lease.root) / "sandbox-manifest.json"
    valid_manifest = json.loads(valid_manifest_path.read_text(encoding="utf-8"))
    valid_manifest["status"] = "running"
    valid_manifest_path.write_text(json.dumps(valid_manifest), encoding="utf-8")
    escape_manifest_path = (
        tmp_path
        / ".iriai"
        / "features"
        / "000-escape"
        / "sandboxes"
        / "g1"
        / "attempt-1"
        / "sandbox-manifest.json"
    )
    escape_manifest_path.parent.mkdir(parents=True)
    escape_root = tmp_path / "outside-sandbox-root"
    escape_root.mkdir()
    escape_manifest_path.write_text(
        json.dumps(
            {
                **valid_manifest,
                "sandbox_id": "escape-lease",
                "root": str(escape_root),
                "manifest_path": str(escape_root / "sandbox-manifest.json"),
                "status": "running",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    store = DurableFakeStore()

    recovered = run(runner_for(tmp_path, source, store=store).recover())

    assert [lease.sandbox_id for lease in recovered] == [valid_lease.sandbox_id]
    assert [lease.sandbox_id for lease in store.allocated_leases] == [valid_lease.sandbox_id]
    assert all(lease.sandbox_id != "escape-lease" for lease in recovered)


def test_recover_backfills_allocated_manifest_before_runtime_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    lease = run(runner_for(tmp_path, source).allocate(spec_for(base)))
    manifest_path = Path(lease.root) / "sandbox-manifest.json"
    store = DurableFakeStore()
    runner = runner_for(tmp_path, source, store=store)

    recovered = run(runner.recover())

    assert [item.sandbox_id for item in recovered] == [lease.sandbox_id]
    assert recovered[0].status == "allocated"
    assert recovered[0].sandbox_lease_id == 321
    assert [item.sandbox_id for item in store.allocated_leases] == [lease.sandbox_id]
    assert store.updated_leases == []
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["sandbox_lease_id"] == 321

    binding = run(runner.bind_runtime(recovered[0], "codex"))

    assert binding.sandbox_lease_id == 321
    assert store.runtime_bindings[0].sandbox_lease_id == 321
    assert store.updated_leases[-1].status == "running"


def test_source_git_metadata_must_stay_inside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside-main"
    base = init_repo(outside)
    source = allowed / "app"
    source.parent.mkdir(parents=True)
    git(outside, "worktree", "add", "--detach", str(source), base)
    runner = SandboxRunner(
        workspace_root=tmp_path,
        repo_sources={"app": source},
        allowed_source_roots=[allowed],
    )

    with pytest.raises(SandboxAllocationError, match="git metadata escapes"):
        run(runner.allocate(spec_for(base)))


def test_source_root_symlink_ancestor_fails_closed(tmp_path: Path) -> None:
    real_parent = tmp_path / "canonical-real"
    source = real_parent / "app"
    base = init_repo(source)
    link_parent = tmp_path / "canonical-link"
    os.symlink(real_parent, link_parent)
    runner = runner_for(tmp_path, link_parent / "app")

    with pytest.raises(SandboxAllocationError, match="symlink"):
        run(runner.allocate(spec_for(base)))


def test_capture_includes_worktree_changes_without_mutating_normal_index(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    artifact_writer = FakeArtifactWriter()
    store = FakeStore()
    runner = runner_for(
        tmp_path,
        source,
        store=store,
        artifact_writer=artifact_writer,
    )
    lease = run(runner.allocate(spec_for(base)))
    repo = Path(lease.repo_roots["app"])

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "delete.txt").unlink()
    (repo / "oldname.txt").rename(repo / "newname.txt")
    (repo / "created.txt").write_text("created\n", encoding="utf-8")
    status_before = git(repo, "status", "--porcelain=v1")

    result = run(runner.capture_patch(lease))

    assert git(repo, "status", "--porcelain=v1") == status_before
    assert result.clean_after_capture is True
    assert result.empty is False
    assert result.patch_summary_ids == [1001]
    patch = result.repo_patches[0]
    assert patch.created_paths == ["created.txt"]
    assert patch.modified_paths == ["tracked.txt"]
    assert patch.deleted_paths == ["delete.txt"]
    assert patch.renamed_paths == [("oldname.txt", "newname.txt")]
    assert patch.changed_paths == [
        "created.txt",
        "delete.txt",
        "newname.txt",
        "oldname.txt",
        "tracked.txt",
    ]
    assert patch.outside_contract_paths == []
    assert artifact_writer.records
    assert b"diff --git" in artifact_writer.records[0][1]
    assert len(store.patch_summaries) == 1
    summary = store.patch_summaries[0]
    assert summary.metadata["workspace_snapshot_id"] == 11
    assert summary.metadata["base_snapshot_id"] == 11
    assert summary.metadata["base_snapshot_by_repo"] == {"app": 11}
    assert summary.payload["workspace_snapshot_id"] == 11
    assert summary.payload["summary_hash"] == _stable_digest(
        summary.payload["summary_hash_payload"]
    )
    assert summary.metadata["summary_hash"] == summary.payload["summary_hash"]
    assert summary.metadata["computed_summary_sha256"] == summary.payload["summary_hash"]
    assert git(source, "status", "--porcelain=v1") == ""


def test_capture_records_outside_contract_paths_by_contract_roots(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    store = FakeStore()
    runner = runner_for(tmp_path, source, store=store)
    spec = spec_for(base).model_copy(update={"writable_roots": ["app:tracked.txt"]})
    lease = run(runner.allocate(spec))
    repo = Path(lease.repo_roots["app"])

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")

    result = run(runner.capture_patch(lease))

    patch = result.repo_patches[0]
    assert patch.changed_paths == ["outside.txt", "tracked.txt"]
    assert patch.outside_contract_paths == ["outside.txt"]
    assert store.patch_summaries
    assert store.patch_summaries[0].metadata["outside_contract_paths"] == ["outside.txt"]


def test_capture_represents_binary_and_mode_only_executable_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))
    repo = Path(lease.repo_roots["app"])

    (repo / "blob.bin").write_bytes(b"\0binary-change" + bytes(range(120)))
    os.chmod(repo / "script.sh", 0o755)

    result = run(runner.capture_patch(lease))
    patch = result.repo_patches[0]

    assert "blob.bin" in patch.binary_paths
    assert "script.sh" in patch.mode_changed_paths
    assert "script.sh" in patch.executable_bit_changed_paths
    assert "script.sh" in patch.changed_paths
    assert patch.diff_sha256 != "e3b0c44298fc1c149afbf4c8996fb924"


def test_symlink_escape_and_outside_source_root_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    outside_source = outside / "app"
    outside_base = init_repo(outside_source)
    runner = runner_for(tmp_path, outside_source)
    with pytest.raises(SandboxAllocationError):
        run(runner.allocate(spec_for(outside_base)))

    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))
    repo = Path(lease.repo_roots["app"])
    os.symlink(source, repo / "escape")

    with pytest.raises(SandboxIsolationError):
        run(runner.capture_patch(lease))
    assert lease.status == "poisoned"
    assert git(source, "status", "--porcelain=v1") == ""


def test_capture_revalidates_sandbox_git_common_dir_before_git_operations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    runner = runner_for(tmp_path, source)
    lease = run(runner.allocate(spec_for(base)))
    repo = Path(lease.repo_roots["app"])
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / ".git" / "commondir").write_text(
        str((tmp_path / "outside-common-dir").resolve()),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="git common dir"):
        run(runner.capture_patch(lease))


def test_empty_patch_persists_empty_capture_result(tmp_path: Path) -> None:
    source = tmp_path / "canonical" / "app"
    base = init_repo(source)
    artifact_writer = FakeArtifactWriter()
    store = FakeStore()
    runner = runner_for(
        tmp_path,
        source,
        store=store,
        artifact_writer=artifact_writer,
    )
    lease = run(runner.allocate(spec_for(base)))

    result = run(runner.capture_patch(lease))

    assert result.empty is True
    assert result.clean_after_capture is True
    assert result.patch_summary_ids == [1001]
    patch = result.repo_patches[0]
    assert patch.changed_paths == []
    assert patch.diff_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert patch.diff_artifact_id == 1
    assert artifact_writer.records[0][1] == b""
    assert len(store.patch_summaries) == 1
    assert lease.status == "captured"
