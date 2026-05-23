"""Sandbox allocation, runtime binding, patch capture, and cleanup.

The runner in this module is deliberately local-filesystem first.  Persistence
and artifact storage can be injected by production wiring, but tests and
recovery code can exercise the isolation rules without depending on the store
slice landing first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:  # Store integration is optional for this slice.
    from iriai_build_v2.execution_control.models import (
        PatchSummary as StoredPatchSummary,
        RuntimeWorkspaceBinding as StoredRuntimeWorkspaceBinding,
        SandboxLease as StoredSandboxLease,
        SandboxRepoBinding as StoredSandboxRepoBinding,
    )
except Exception:  # pragma: no cover - import guard for partial deployments.
    StoredPatchSummary = None  # type: ignore[assignment]
    StoredRuntimeWorkspaceBinding = None  # type: ignore[assignment]
    StoredSandboxLease = None  # type: ignore[assignment]
    StoredSandboxRepoBinding = None  # type: ignore[assignment]

# Slice-11d cluster imports -- the 6 pure sandbox-lifecycle helpers moved from
# ``workflows/develop/phases/implementation.py`` depend on the typed
# RuntimeSandboxTaskBinding + SandboxWorkflowBlocker classes already declared
# in the sibling ``types`` module (Slice 11a), on ImplementationTask from
# ``models.outputs``, and on the sibling-package ``_write_context_text``
# helper. NONE of these are phase-level; the imports are kept here at the head
# of the module so the appended Slice-11d helpers at the file tail resolve
# cleanly without circular dependency.
from .types import RuntimeSandboxTaskBinding, SandboxWorkflowBlocker
from ....models.outputs import ImplementationTask
from ..._common._helpers import _write_context_text


SandboxMode = Literal["wave", "task", "repair", "canonicalization"]
SandboxStatus = Literal[
    "allocating",
    "allocated",
    "binding",
    "running",
    "capturing",
    "captured",
    "released",
    "retained",
    "failed",
    "poisoned",
]
RuntimeName = Literal["claude", "codex", "claude_pool"]

_MANIFEST_NAME = "sandbox-manifest.json"
_MANIFEST_VERSION = "sandbox-runner-v1"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_TERMINAL_STATUSES = {"captured", "released", "retained", "failed", "poisoned"}
_RELEASE_DISPOSITIONS = {
    "release",
    "released",
    "delete",
    "cleanup",
    "retention-expired",
    "retention_expired",
}


class SandboxError(RuntimeError):
    """Base class for sandbox lifecycle failures."""


class SandboxAllocationError(SandboxError):
    """Allocation failed before a runtime could bind."""


class SandboxBindingError(SandboxError):
    """Runtime binding would violate the sandbox contract."""


class SandboxCaptureError(SandboxError):
    """Patch capture failed."""


class SandboxIsolationError(SandboxCaptureError):
    """Sandbox contents attempted to escape the declared repo roots."""


class SandboxReleaseError(SandboxError):
    """Release refused to delete untrusted filesystem state."""


class _SandboxModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class SandboxSpec(_SandboxModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    attempt_no: int
    task_ids: list[str]
    repo_ids: list[str]
    base_snapshot_ids: list[int]
    base_commits: dict[str, str]
    mode: SandboxMode
    writable_roots: list[str]
    readonly_roots: list[str]
    contract_ids: list[int]
    ttl_seconds: int = 86_400

    @field_validator("feature_id", "dag_sha256", "mode")
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("value cannot be empty")
        return value

    @field_validator("ttl_seconds")
    @classmethod
    def _positive_ttl(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ttl_seconds must be positive")
        return value

    @model_validator(mode="after")
    def _stable_lists(self) -> "SandboxSpec":
        if not self.repo_ids:
            raise ValueError("repo_ids cannot be empty")
        if len(set(self.repo_ids)) != len(self.repo_ids):
            raise ValueError("repo_ids must be unique")
        if len(set(self.contract_ids)) != len(self.contract_ids):
            raise ValueError("contract_ids must be unique")
        if self.group_idx < 0:
            raise ValueError("group_idx cannot be negative")
        if self.attempt_no < 0:
            raise ValueError("attempt_no cannot be negative")
        return self

    @property
    def idempotency_key(self) -> str:
        seed = {
            "feature_id": self.feature_id,
            "dag_sha256": self.dag_sha256,
            "group_idx": self.group_idx,
            "attempt_no": self.attempt_no,
            "mode": self.mode,
            "repo_ids": sorted(self.repo_ids),
            "base_commits": {
                repo_id: self.base_commits.get(repo_id, "")
                for repo_id in sorted(self.repo_ids)
            },
            "contract_ids": sorted(self.contract_ids),
        }
        return f"idem:sandbox:{_stable_digest(seed)}"


class SandboxLease(_SandboxModel):
    id: int | None = None
    sandbox_lease_id: int | None = None
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: int = 0
    attempt_no: int = 0
    mode: SandboxMode = "task"
    idempotency_key: str = ""
    sandbox_id: str
    root: str
    manifest_path: str = ""
    repo_roots: dict[str, str]
    base_commits: dict[str, str]
    writable_roots: list[str] = Field(default_factory=list)
    readonly_roots: list[str] = Field(default_factory=list)
    blocked_roots: list[str] = Field(default_factory=list)
    expires_at: str
    owner: str
    status: SandboxStatus
    patch_summary_ids: list[int]
    lease_version: int = 0


class RuntimeWorkspaceBinding(_SandboxModel):
    id: int | None = None
    feature_id: str = ""
    sandbox_lease_id: int | None = None
    sandbox_id: str
    attempt_id: int = 0
    runtime: RuntimeName
    cwd: str
    workspace_override: str
    repo_roots: dict[str, str]
    writable_roots: list[str]
    readonly_roots: list[str]
    blocked_roots: list[str]
    expires_at: str
    env: dict[str, str]
    role_metadata: dict[str, Any]
    manifest_path: str | None = None


class SandboxRepoPatch(_SandboxModel):
    repo_id: str
    base_commit: str
    head_commit: str | None
    changed_paths: list[str]
    created_paths: list[str]
    modified_paths: list[str]
    deleted_paths: list[str]
    renamed_paths: list[tuple[str, str]]
    binary_paths: list[str]
    mode_changed_paths: list[str]
    executable_bit_changed_paths: list[str]
    outside_contract_paths: list[str]
    diff_sha256: str
    diff_artifact_id: int


class PatchCaptureResult(_SandboxModel):
    sandbox_id: str
    patch_summary_ids: list[int]
    repo_patches: list[SandboxRepoPatch]
    empty: bool
    clean_after_capture: bool


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes = b""


CommandRunner = Callable[
    [Path, Sequence[str], Mapping[str, str] | None],
    CommandResult | subprocess.CompletedProcess[bytes] | Awaitable[Any],
]


class SandboxRunner:
    """Owns sandbox lifecycle actions without mutating canonical repos."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        repo_sources: Mapping[str, str | Path] | None = None,
        store: Any | None = None,
        artifact_writer: Any | None = None,
        command_runner: CommandRunner | None = None,
        owner: str | None = None,
        allowed_source_roots: Sequence[str | Path] | None = None,
        blocked_roots: Sequence[str | Path] | None = None,
        alias_roots: Sequence[str | Path] | None = None,
        recovery_owner_prefix: str | None = None,
        recovery_feature_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.repo_sources = {
            repo_id: Path(path)
            for repo_id, path in dict(repo_sources or {}).items()
        }
        self.store = store
        self.artifact_writer = artifact_writer
        self.command_runner = command_runner
        self.owner = owner or f"pid:{os.getpid()}"
        self.recovery_owner_prefix = recovery_owner_prefix
        self.recovery_feature_id = recovery_feature_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._leases_by_key: dict[str, SandboxLease] = {}
        self._specs_by_sandbox: dict[str, SandboxSpec] = {}
        self._runtime_bindings: dict[str, RuntimeWorkspaceBinding] = {}
        self._patch_summary_counter = 0
        self._artifact_counter = 0
        self._locks: dict[str, threading.RLock] = {}
        if allowed_source_roots is None:
            allowed_source_roots = [self.workspace_root]
        self.allowed_source_roots = [
            Path(path).resolve(strict=False) for path in allowed_source_roots
        ]
        self.blocked_roots = [
            Path(path).resolve(strict=False)
            for path in [*(blocked_roots or ()), *(alias_roots or ())]
        ]

    async def allocate(self, spec: SandboxSpec) -> SandboxLease:
        idempotency_key = spec.idempotency_key
        existing = await self._existing_lease_for_key(idempotency_key, spec)
        if existing is not None:
            return existing

        feature_slug = _slugify(spec.feature_id)
        sandbox_id = self._sandbox_id(spec)
        sandbox_root = self._sandbox_root(spec)
        lock = self._locks.setdefault(feature_slug, threading.RLock())

        with lock:
            self._validate_sandbox_allocation_path(sandbox_root)
            manifest_path = sandbox_root / _MANIFEST_NAME
            if manifest_path.exists():
                manifest = self._read_manifest(manifest_path)
                if manifest.get("idempotency_key") != idempotency_key:
                    raise SandboxAllocationError(
                        f"sandbox path already belongs to a different lease: {sandbox_root}"
                    )
                terminal_status = str(manifest.get("status") or "")
                if terminal_status in _TERMINAL_STATUSES:
                    if terminal_status == "poisoned":
                        raise SandboxAllocationError(
                            f"sandbox path belongs to poisoned lease: {sandbox_root}"
                        )
                    if terminal_status == "retained":
                        raise SandboxAllocationError(
                            "retained sandbox evidence requires a new attempt idempotency key: "
                            f"{sandbox_root}"
                    )
                    terminal_lease = self._lease_from_manifest(manifest)
                    self._validate_release_ownership(sandbox_root, manifest, terminal_lease)
                    shutil.rmtree(sandbox_root)
                    self._validate_sandbox_allocation_path(sandbox_root)
                    sandbox_root.mkdir(parents=True, exist_ok=True)
                    manifest_path = sandbox_root / _MANIFEST_NAME
                else:
                    lease = self._lease_from_manifest(manifest)
                    self._validate_manifest(manifest, lease, verify_heads=True)
                    lease = await self._persist_allocated_lease(lease, spec, manifest)
                    self._leases_by_key[idempotency_key] = lease
                    self._specs_by_sandbox[lease.sandbox_id] = spec
                    return lease

            self._validate_sandbox_allocation_path(sandbox_root)
            sandbox_root.mkdir(parents=True, exist_ok=True)
            repo_roots: dict[str, str] = {}
            source_roots: dict[str, str] = {}
            base_commits: dict[str, str] = {}
            blocked_roots: list[str] = []

            try:
                for repo_id in spec.repo_ids:
                    source_root = await self._source_root_for_repo(repo_id)
                    self._validate_source_root(repo_id, source_root)
                    source_resolved = source_root.resolve(strict=True)
                    source_roots[repo_id] = str(source_resolved)
                    blocked_roots.append(str(source_resolved))
                    base_commit = spec.base_commits.get(repo_id) or self._git_text(
                        source_resolved,
                        ["rev-parse", "HEAD"],
                    ).strip()
                    base_commits[repo_id] = base_commit

                    repo_root = sandbox_root / "repos" / _slugify(repo_id)
                    if repo_root.exists():
                        raise SandboxAllocationError(
                            f"repo destination already exists before manifest: {repo_root}"
                        )
                    repo_root.parent.mkdir(parents=True, exist_ok=True)
                    self._git_text(
                        sandbox_root,
                        ["clone", "--no-local", str(source_resolved), str(repo_root)],
                    )
                    self._git_text(repo_root, ["checkout", "--detach", base_commit])
                    self._validate_repo_root(
                        repo_root,
                        sandbox_root=sandbox_root,
                        expected_commit=base_commit,
                    )
                    repo_roots[repo_id] = str(repo_root.resolve(strict=True))
            except Exception as exc:
                if not manifest_path.exists():
                    shutil.rmtree(sandbox_root, ignore_errors=True)
                if isinstance(exc, SandboxAllocationError):
                    raise
                if isinstance(exc, SandboxError):
                    raise SandboxAllocationError(str(exc)) from exc
                raise

            all_blocked = _sorted_unique(
                [*blocked_roots, *(str(path) for path in self.blocked_roots)]
            )
            writable_roots = self._runtime_roots_from_spec(
                spec.writable_roots,
                repo_roots=repo_roots,
                source_roots=source_roots,
                sandbox_root=sandbox_root,
                default_roots=list(repo_roots.values()),
                allow_external=False,
            )
            readonly_roots = self._runtime_roots_from_spec(
                spec.readonly_roots,
                repo_roots=repo_roots,
                source_roots=source_roots,
                sandbox_root=sandbox_root,
                default_roots=[],
                allow_external=False,
            )
            now = _utc_now(self._clock)
            expires_at = now + timedelta(seconds=spec.ttl_seconds)
            manifest = {
                "manifest_version": _MANIFEST_VERSION,
                "sandbox_id": sandbox_id,
                "idempotency_key": idempotency_key,
                "root": str(sandbox_root.resolve(strict=True)),
                "repo_roots": repo_roots,
                "repo_sources": source_roots,
                "repo_ids": list(spec.repo_ids),
                "base_commits": base_commits,
                "base_snapshot_ids": list(spec.base_snapshot_ids),
                "base_snapshot_by_repo": _base_snapshot_by_repo(
                    repo_ids=spec.repo_ids,
                    base_snapshot_ids=spec.base_snapshot_ids,
                ),
                "contract_ids": sorted(spec.contract_ids),
                "blocked_roots": all_blocked,
                "writable_roots": writable_roots,
                "readonly_roots": readonly_roots,
                "expires_at": _isoformat(expires_at),
                "owner": self.owner,
                "status": "allocated",
                "mode": spec.mode,
                "feature_id": spec.feature_id,
                "feature_slug": feature_slug,
                "dag_sha256": spec.dag_sha256,
                "group_idx": spec.group_idx,
                "attempt_no": spec.attempt_no,
                "task_ids": list(spec.task_ids),
                "created_at": _isoformat(now),
            }
            self._write_manifest(manifest_path, manifest)
            manifest = self._read_manifest(manifest_path)
            lease = self._lease_from_manifest(manifest)
            self._validate_manifest(manifest, lease, verify_heads=True)
            try:
                lease = await self._persist_allocated_lease(lease, spec, manifest)
            except Exception:
                shutil.rmtree(sandbox_root, ignore_errors=True)
                raise
            self._leases_by_key[idempotency_key] = lease
            self._specs_by_sandbox[sandbox_id] = spec
            return lease

    async def bind_runtime(
        self,
        lease: SandboxLease,
        runtime: str,
    ) -> RuntimeWorkspaceBinding:
        if runtime not in {"claude", "codex", "claude_pool"}:
            raise SandboxBindingError(f"unsupported runtime: {runtime}")
        manifest = self._load_manifest_for_lease(lease)
        self._validate_manifest(manifest, lease, verify_heads=True)
        if lease.status not in {"allocated", "binding"}:
            existing = self._runtime_bindings.get(lease.sandbox_id)
            if existing is not None and existing.runtime == runtime:
                return existing
            raise SandboxBindingError(
                f"lease {lease.sandbox_id} cannot bind runtime from status {lease.status}"
            )
        existing = self._runtime_bindings.get(lease.sandbox_id)
        if existing is not None:
            raise SandboxBindingError(f"lease {lease.sandbox_id} already has a binding")

        root = str(Path(manifest["root"]).resolve(strict=True))
        manifest_path = str(Path(root) / _MANIFEST_NAME)
        repo_roots = {
            str(repo_id): str(Path(path).resolve(strict=True))
            for repo_id, path in dict(manifest.get("repo_roots", {})).items()
        }
        cwd = self._runtime_cwd_from_manifest(manifest, repo_roots=repo_roots)
        effective_expires_at = str(lease.expires_at or manifest["expires_at"])
        blocked_roots = _sorted_unique(
            [*list(manifest.get("blocked_roots") or []), manifest_path]
        )
        binding = RuntimeWorkspaceBinding(
            feature_id=str(manifest.get("feature_id") or ""),
            sandbox_lease_id=lease.sandbox_lease_id or lease.id,
            sandbox_id=lease.sandbox_id,
            attempt_id=int(manifest.get("attempt_no") or 0),
            runtime=runtime,  # type: ignore[arg-type]
            cwd=cwd,
            workspace_override=cwd,
            repo_roots=repo_roots,
            writable_roots=list(manifest.get("writable_roots") or repo_roots.values()),
            readonly_roots=list(manifest.get("readonly_roots") or []),
            blocked_roots=blocked_roots,
            expires_at=effective_expires_at,
            env={
                "IRIAI_SANDBOX_ID": lease.sandbox_id,
                "IRIAI_SANDBOX_ROOT": root,
                "IRIAI_SANDBOX_MANIFEST": manifest_path,
                "IRIAI_SANDBOX_EXPIRES_AT": effective_expires_at,
                "IRIAI_SANDBOX_REPO_ROOTS_JSON": json.dumps(
                    repo_roots,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
            role_metadata={
                "sandbox": True,
                "sandbox_id": lease.sandbox_id,
                "feature_id": manifest.get("feature_id", ""),
                "dag_sha256": manifest.get("dag_sha256", ""),
                "group_idx": manifest.get("group_idx"),
                "attempt_no": manifest.get("attempt_no"),
                "mode": manifest.get("mode", ""),
                "task_ids": list(manifest.get("task_ids") or []),
                "contract_ids": list(manifest.get("contract_ids") or []),
                "base_snapshot_ids": list(manifest.get("base_snapshot_ids") or []),
                "base_snapshot_by_repo": dict(
                    manifest.get("base_snapshot_by_repo") or {}
                ),
            },
            manifest_path=manifest_path,
        )
        binding.role_metadata["runtime_workspace_binding"] = _runtime_binding_metadata(
            binding
        )
        previous_status = lease.status
        try:
            await self._persist_runtime_binding(binding, lease, manifest)
            lease.status = "running"
            await self._persist_lease_status(lease)
        except Exception:
            lease.status = previous_status
            raise
        self._runtime_bindings[lease.sandbox_id] = binding
        return binding

    async def capture_patch(self, lease: SandboxLease) -> PatchCaptureResult:
        manifest = self._load_manifest_for_lease(lease)
        self._validate_manifest(manifest, lease, verify_heads=False)
        lease.status = "capturing"

        repo_patches: list[SandboxRepoPatch] = []
        patch_summary_ids: list[int] = []
        clean_after_capture = True
        try:
            repo_roots = dict(manifest.get("repo_roots", {}))
            base_commits = dict(manifest.get("base_commits", {}))
            for repo_id in sorted(repo_roots):
                repo_patch, repo_clean, diff_bytes = await self._capture_repo_patch(
                    repo_id=repo_id,
                    repo_root=Path(repo_roots[repo_id]),
                    base_commit=str(base_commits.get(repo_id) or ""),
                    manifest=manifest,
                    lease=lease,
                )
                repo_patches.append(repo_patch)
                clean_after_capture = clean_after_capture and repo_clean
                patch_summary_id = await self._record_patch_summary(
                    manifest=manifest,
                    lease=lease,
                    repo_patch=repo_patch,
                    diff_bytes=diff_bytes,
                )
                patch_summary_ids.append(patch_summary_id)
                lease.patch_summary_ids = list(patch_summary_ids)
        except SandboxIsolationError:
            lease.status = "poisoned"
            await self._persist_lease_status(lease)
            raise
        except Exception as exc:
            lease.status = "failed"
            await self._persist_lease_status(lease)
            if isinstance(exc, SandboxCaptureError):
                raise
            raise SandboxCaptureError(str(exc)) from exc

        lease.patch_summary_ids = patch_summary_ids
        lease.status = "captured"
        result = PatchCaptureResult(
            sandbox_id=lease.sandbox_id,
            patch_summary_ids=patch_summary_ids,
            repo_patches=repo_patches,
            empty=all(not patch.changed_paths for patch in repo_patches),
            clean_after_capture=clean_after_capture,
        )
        await self._persist_lease_status(lease, result)
        return result

    async def release(self, lease: SandboxLease, disposition: str) -> None:
        should_delete = str(disposition).strip().lower() in _RELEASE_DISPOSITIONS
        if not should_delete:
            if lease.status == "failed":
                lease.status = "failed"
            else:
                lease.status = "retained"
            await self._persist_lease_status(lease)
            return

        root = Path(lease.root)
        if not root.exists():
            lease.status = "released"
            await self._persist_lease_status(lease)
            return
        manifest_path = root / _MANIFEST_NAME
        if not manifest_path.exists():
            raise SandboxReleaseError(f"refusing to delete sandbox without manifest: {root}")
        manifest = self._read_manifest(manifest_path)
        self._validate_manifest_identity(manifest, lease)
        self._validate_release_ownership(root, manifest, lease)
        lease.status = "released"
        await self._persist_lease_status(lease)
        shutil.rmtree(root)

    async def recover(self) -> list[SandboxLease]:
        """Rehydrate non-terminal manifest-owned leases for restart recovery."""
        recovered: list[SandboxLease] = []
        recovered_keys: set[tuple[int | None, str]] = set()
        recovered_idempotency_keys: set[str] = set()
        durable_leases = await self._store_active_leases()
        if self.store is not None:
            for stored in durable_leases:
                lease = self._coerce_lease(stored)
                if not self._lease_matches_recovery_scope(lease):
                    continue
                key = (lease.sandbox_lease_id or lease.id, lease.sandbox_id)
                if key in recovered_keys or lease.status in _TERMINAL_STATUSES:
                    continue
                manifest_path = Path(lease.root) / _MANIFEST_NAME
                if manifest_path.exists():
                    try:
                        manifest = self._read_manifest(manifest_path)
                        self._validate_manifest(manifest, lease, verify_heads=False)
                    except SandboxError:
                        lease.status = "failed"
                        await self._persist_lease_status(lease)
                    else:
                        if lease.status in {"binding", "running", "capturing"}:
                            await self._retain_crashed_lease(lease)
                else:
                    lease.status = "failed"
                    await self._persist_lease_status(lease)
                recovered.append(lease)
                recovered_keys.add(key)
                if lease.idempotency_key:
                    recovered_idempotency_keys.add(lease.idempotency_key)
                    self._leases_by_key[lease.idempotency_key] = lease

        manifest_root = self.workspace_root / ".iriai" / "features"
        if manifest_root.exists():
            for manifest_path in sorted(manifest_root.glob("*/sandboxes/*/*/sandbox-manifest.json")):
                try:
                    manifest = self._read_manifest(manifest_path)
                    self._validate_manifest_only_recovery_root(manifest_path, manifest)
                    lease = self._lease_from_manifest(manifest)
                    self._validate_manifest(manifest, lease, verify_heads=False)
                except (SandboxError, KeyError, TypeError, ValueError):
                    continue
                key = (lease.sandbox_lease_id or lease.id, lease.sandbox_id)
                if key in recovered_keys:
                    continue
                if lease.idempotency_key and lease.idempotency_key in recovered_idempotency_keys:
                    continue
                if lease.status in _TERMINAL_STATUSES:
                    continue
                if not self._lease_matches_recovery_scope(lease):
                    continue
                if self.store is not None:
                    try:
                        lease = await self._ensure_durable_lease_for_manifest(lease, manifest)
                    except (SandboxError, KeyError, TypeError, ValueError):
                        continue
                    key = (lease.sandbox_lease_id or lease.id, lease.sandbox_id)
                    if key in recovered_keys:
                        continue
                if lease.status in {"binding", "running", "capturing"}:
                    await self._retain_crashed_lease(lease)
                recovered.append(lease)
                recovered_keys.add(key)
                if lease.idempotency_key:
                    recovered_idempotency_keys.add(lease.idempotency_key)
                    self._leases_by_key[lease.idempotency_key] = lease
        return recovered

    async def _existing_lease_for_key(
        self,
        idempotency_key: str,
        spec: SandboxSpec,
    ) -> SandboxLease | None:
        store_lease = await self._store_get_lease(idempotency_key, spec.feature_id)
        if store_lease is not None:
            lease = self._coerce_lease(store_lease)
            if lease.status in _TERMINAL_STATUSES:
                root = Path(lease.root)
                if lease.status == "retained":
                    if root.exists():
                        manifest_path = root / _MANIFEST_NAME
                        if manifest_path.exists():
                            manifest = self._read_manifest(manifest_path)
                            self._validate_manifest_identity(manifest, lease)
                            self._validate_release_ownership(root, manifest, lease)
                    raise SandboxAllocationError(
                        "retained sandbox evidence cannot be reused with the same "
                        f"idempotency key: {idempotency_key}"
                    )
                if root.exists():
                    if lease.status == "poisoned":
                        raise SandboxAllocationError(
                            f"durable lease is poisoned for idempotency key: {idempotency_key}"
                        )
                    manifest_path = root / _MANIFEST_NAME
                    if manifest_path.exists():
                        manifest = self._read_manifest(manifest_path)
                        self._validate_manifest_identity(manifest, lease)
                        self._validate_release_ownership(root, manifest, lease)
                    shutil.rmtree(root)
                return None
            if lease.status not in _TERMINAL_STATUSES:
                if lease.status in {"binding", "running", "capturing"}:
                    await self._retain_crashed_lease(lease)
                    raise SandboxAllocationError(
                        "retained crashed sandbox evidence cannot be reused with "
                        f"the same idempotency key: {idempotency_key}"
                    )
                manifest = self._load_manifest_for_lease(lease)
                if manifest.get("idempotency_key") != idempotency_key:
                    raise SandboxAllocationError("stored lease and manifest disagree")
                self._validate_manifest(manifest, lease, verify_heads=True)
                manifest_lease = self._lease_from_manifest(manifest)
                manifest_lease.id = lease.id
                manifest_lease.sandbox_lease_id = lease.sandbox_lease_id or lease.id
                manifest_lease.status = lease.status
                manifest_lease.patch_summary_ids = list(lease.patch_summary_ids)
                manifest_lease.lease_version = lease.lease_version
                manifest_lease.expires_at = lease.expires_at
                self._leases_by_key[idempotency_key] = manifest_lease
                self._specs_by_sandbox[manifest_lease.sandbox_id] = spec
                return manifest_lease

        lease = self._leases_by_key.get(idempotency_key)
        if lease is None or lease.status in _TERMINAL_STATUSES:
            return None
        manifest_path = Path(lease.root) / _MANIFEST_NAME
        if not manifest_path.exists():
            return None
        manifest = self._read_manifest(manifest_path)
        self._validate_manifest(manifest, lease, verify_heads=True)
        self._specs_by_sandbox[lease.sandbox_id] = spec
        return lease

    async def _retain_crashed_lease(self, lease: SandboxLease) -> None:
        lease.status = "retained"
        await self._persist_lease_status(lease)

    async def _ensure_durable_lease_for_manifest(
        self,
        lease: SandboxLease,
        manifest: Mapping[str, Any],
    ) -> SandboxLease:
        if self.store is None:
            return lease
        if getattr(self.store, "allocate_sandbox_lease", None) is None:
            return lease
        spec = self._spec_from_manifest(manifest)
        return await self._persist_allocated_lease(lease, spec, manifest)

    async def _store_get_lease(
        self,
        idempotency_key: str,
        feature_id: str | None = None,
    ) -> Any | None:
        if self.store is None:
            return None
        for name in (
            "get_sandbox_lease_by_idempotency_key",
            "fetch_sandbox_lease_by_idempotency_key",
            "find_sandbox_lease",
        ):
            method = getattr(self.store, name, None)
            if method is None:
                continue
            try:
                if feature_id:
                    result = await _maybe_await(method(feature_id, idempotency_key))
                else:
                    result = await _maybe_await(method(idempotency_key))
            except TypeError:
                result = await _maybe_await(method(idempotency_key))
            if result is not None:
                return result
        return None

    async def _store_call(self, method_names: Sequence[str], *args: Any) -> Any | None:
        if self.store is None:
            return None
        for name in method_names:
            method = getattr(self.store, name, None)
            if method is None:
                continue
            try:
                return await _maybe_await(method(*args))
            except TypeError:
                if args and isinstance(args[0], BaseModel):
                    return await _maybe_await(method(args[0].model_dump(mode="json")))
                raise
        return None

    async def _store_active_leases(self) -> list[Any]:
        if self.store is None:
            return []
        call_scopes: list[dict[str, str]] = []
        if self.recovery_feature_id or self.recovery_owner_prefix:
            scoped: dict[str, str] = {}
            if self.recovery_feature_id:
                scoped["feature_id"] = self.recovery_feature_id
            if self.recovery_owner_prefix:
                scoped["owner_prefix"] = self.recovery_owner_prefix
            call_scopes.append(scoped)
        if self.recovery_owner_prefix:
            call_scopes.append({"owner_prefix": self.recovery_owner_prefix})
        if self.owner:
            call_scopes.append({"owner": self.owner})
        if not call_scopes:
            call_scopes.append({})
        for name in (
            "list_active_sandbox_leases",
            "list_nonterminal_sandbox_leases",
            "recover_active_sandbox_leases",
            "iter_active_sandbox_leases",
        ):
            method = getattr(self.store, name, None)
            if method is None:
                continue
            for kwargs in call_scopes:
                try:
                    result = await _maybe_await(method(**kwargs))
                except TypeError:
                    if set(kwargs) == {"owner"}:
                        try:
                            result = await _maybe_await(method(kwargs["owner"]))
                        except TypeError:
                            continue
                    elif not kwargs:
                        try:
                            result = await _maybe_await(method())
                        except TypeError:
                            continue
                    else:
                        continue
                leases = self._active_lease_result_list(result)
                return [
                    lease
                    for lease in leases
                    if self._lease_matches_recovery_scope(self._coerce_lease(lease))
                ]
        return []

    @staticmethod
    def _active_lease_result_list(result: Any) -> list[Any]:
        if result is None:
            return []
        if isinstance(result, Mapping):
            for key in ("leases", "items", "rows"):
                value = result.get(key)
                if value is not None:
                    return list(value)
            return []
        return list(result)

    def _lease_matches_recovery_scope(self, lease: SandboxLease) -> bool:
        feature_id = str(getattr(lease, "feature_id", "") or "")
        if self.recovery_feature_id and feature_id != self.recovery_feature_id:
            return False
        owner = str(getattr(lease, "owner", "") or "")
        if self.recovery_owner_prefix:
            return owner.startswith(self.recovery_owner_prefix)
        if self.owner:
            return owner == self.owner
        return True

    async def _persist_allocated_lease(
        self,
        lease: SandboxLease,
        spec: SandboxSpec,
        manifest: Mapping[str, Any],
    ) -> SandboxLease:
        if self.store is None:
            return lease
        method = getattr(self.store, "allocate_sandbox_lease", None)
        if method is None or StoredSandboxLease is None or StoredSandboxRepoBinding is None:
            await self._store_call(
                ("record_sandbox_lease", "upsert_sandbox_lease", "save_sandbox_lease"),
                lease,
                spec,
                manifest,
            )
            return lease

        manifest_path = str(Path(str(manifest["root"])) / _MANIFEST_NAME)
        repo_roots = {
            str(repo_id): str(path)
            for repo_id, path in dict(manifest.get("repo_roots", {})).items()
        }
        repo_sources = {
            str(repo_id): str(path)
            for repo_id, path in dict(manifest.get("repo_sources", {})).items()
        }
        base_commits = {
            str(repo_id): str(commit)
            for repo_id, commit in dict(manifest.get("base_commits", {})).items()
        }
        base_snapshot_by_repo = {
            repo_id: (
                int(spec.base_snapshot_ids[idx])
                if idx < len(spec.base_snapshot_ids)
                else 0
            )
            for idx, repo_id in enumerate(spec.repo_ids)
        }
        missing_snapshot_ids = [
            repo_id
            for repo_id, snapshot_id in base_snapshot_by_repo.items()
            if snapshot_id <= 0
        ]
        if missing_snapshot_ids:
            raise SandboxAllocationError(
                "sandbox repo bindings require durable workspace snapshot ids "
                f"for repos: {', '.join(sorted(missing_snapshot_ids))}"
            )
        stored_lease = StoredSandboxLease(
            feature_id=spec.feature_id,
            dag_sha256=spec.dag_sha256,
            group_idx=spec.group_idx,
            attempt_no=spec.attempt_no,
            mode=spec.mode,
            lease_owner=self.owner,
            owner=self.owner,
            expires_at=str(manifest["expires_at"]),
            sandbox_root=str(manifest["root"]),
            root=str(manifest["root"]),
            sandbox_id=lease.sandbox_id,
            manifest_path=manifest_path,
            base_snapshot_ids=list(spec.base_snapshot_ids),
            repo_ids=list(spec.repo_ids),
            repo_roots=repo_roots,
            base_commits=base_commits,
            task_ids=list(spec.task_ids),
            contract_ids=list(spec.contract_ids),
            writable_roots=list(manifest.get("writable_roots") or []),
            readonly_roots=list(manifest.get("readonly_roots") or []),
            blocked_roots=list(manifest.get("blocked_roots") or []),
            patch_summary_ids=list(lease.patch_summary_ids),
            status=lease.status,
            idempotency_key=spec.idempotency_key,
            payload=dict(manifest),
        )
        repo_bindings = tuple(
            StoredSandboxRepoBinding(
                feature_id=spec.feature_id,
                repo_id=str(repo_id),
                sandbox_repo_root=repo_roots[str(repo_id)],
                canonical_repo_root=repo_sources[str(repo_id)],
                base_snapshot_id=base_snapshot_by_repo.get(repo_id, 0),
                base_commit=base_commits.get(str(repo_id), ""),
                writable=True,
                writable_roots=list(manifest.get("writable_roots") or []),
                readonly_roots=list(manifest.get("readonly_roots") or []),
                blocked_canonical_roots=list(manifest.get("blocked_roots") or []),
                payload={
                    "sandbox_id": lease.sandbox_id,
                    "manifest_path": manifest_path,
                    "mode": spec.mode,
                    "task_ids": list(spec.task_ids),
                    "contract_ids": list(spec.contract_ids),
                },
            )
            for repo_id in spec.repo_ids
        )
        try:
            try:
                result = await _maybe_await(method(stored_lease, repo_bindings=repo_bindings))
            except TypeError:
                result = await _maybe_await(method(stored_lease))
        except Exception as exc:
            if isinstance(exc, SandboxError):
                raise
            raise SandboxAllocationError(
                f"durable sandbox lease allocation failed: {exc}"
            ) from exc
        stored_result_lease = getattr(result, "lease", None) if result is not None else None
        if stored_result_lease is not None:
            stored_id = getattr(stored_result_lease, "id", None)
            if stored_id is not None:
                lease.id = int(stored_id)
                lease.sandbox_lease_id = int(stored_id)
            lease.lease_version = int(getattr(stored_result_lease, "lease_version", 0) or 0)
        manifest_path = Path(lease.root) / _MANIFEST_NAME
        if lease.sandbox_lease_id and manifest_path.exists():
            persisted_manifest = self._read_manifest(manifest_path)
            self._validate_manifest_identity(persisted_manifest, lease)
            persisted_manifest["sandbox_lease_id"] = lease.sandbox_lease_id
            self._write_manifest(manifest_path, persisted_manifest)
        return lease

    async def _persist_runtime_binding(
        self,
        binding: RuntimeWorkspaceBinding,
        lease: SandboxLease,
        manifest: Mapping[str, Any],
    ) -> None:
        if self.store is None:
            return
        method = getattr(self.store, "record_runtime_workspace_binding", None)
        if (
            method is not None
            and StoredRuntimeWorkspaceBinding is not None
            and not binding.sandbox_lease_id
        ):
            raise SandboxBindingError(
                "durable runtime workspace binding requires a sandbox lease id"
            )
        if (
            method is None
            or StoredRuntimeWorkspaceBinding is None
            or not binding.sandbox_lease_id
        ):
            await self._store_call(
                (
                    "record_runtime_workspace_binding",
                    "save_runtime_workspace_binding",
                    "upsert_runtime_workspace_binding",
                ),
                binding,
                lease,
                manifest,
            )
            return
        stored_binding = StoredRuntimeWorkspaceBinding(
            feature_id=binding.feature_id or str(manifest.get("feature_id") or ""),
            sandbox_lease_id=int(binding.sandbox_lease_id),
            sandbox_id=lease.sandbox_id,
            attempt_id=binding.attempt_id,
            runtime_name=binding.runtime,
            runtime=binding.runtime,
            cwd=binding.cwd,
            workspace_override=binding.workspace_override,
            manifest_path=binding.manifest_path or "",
            repo_roots=dict(binding.repo_roots),
            writable_roots=list(binding.writable_roots),
            readonly_roots=list(binding.readonly_roots),
            blocked_roots=list(binding.blocked_roots),
            env=dict(binding.env),
            role_metadata=dict(binding.role_metadata),
            status="bound",
            payload={
                **binding.model_dump(mode="json"),
                "expires_at": binding.expires_at,
            },
        )
        result = await _maybe_await(method(stored_binding))
        stored_row = getattr(result, "binding", None) if result is not None else None
        if stored_row is not None and getattr(stored_row, "id", None) is not None:
            binding.id = int(getattr(stored_row, "id"))

    def _spec_from_manifest(self, manifest: Mapping[str, Any]) -> SandboxSpec:
        repo_ids = [str(item) for item in manifest.get("repo_ids") or []]
        if not repo_ids:
            repo_ids = [str(repo_id) for repo_id in dict(manifest.get("repo_roots") or {})]
        return SandboxSpec(
            feature_id=str(manifest.get("feature_id") or ""),
            dag_sha256=str(manifest.get("dag_sha256") or ""),
            group_idx=int(manifest.get("group_idx") or 0),
            attempt_no=int(manifest.get("attempt_no") or 0),
            task_ids=[str(item) for item in manifest.get("task_ids") or []],
            repo_ids=repo_ids,
            base_snapshot_ids=[
                int(item) for item in manifest.get("base_snapshot_ids") or []
            ],
            base_commits={
                str(repo_id): str(commit)
                for repo_id, commit in dict(manifest.get("base_commits") or {}).items()
            },
            mode=str(manifest.get("mode") or "task"),  # type: ignore[arg-type]
            writable_roots=[str(item) for item in manifest.get("writable_roots") or []],
            readonly_roots=[str(item) for item in manifest.get("readonly_roots") or []],
            contract_ids=[int(item) for item in manifest.get("contract_ids") or []],
        )

    async def _persist_lease_status(
        self,
        lease: SandboxLease,
        result: PatchCaptureResult | None = None,
    ) -> None:
        stored = await self._store_call(("update_sandbox_lease", "save_sandbox_lease"), lease, result)
        if stored is not None:
            stored_lease = getattr(stored, "lease", stored)
            if getattr(stored_lease, "lease_version", None) is not None:
                lease.lease_version = int(getattr(stored_lease, "lease_version") or 0)
        manifest_path = Path(lease.root) / _MANIFEST_NAME
        if not manifest_path.exists():
            return
        manifest = self._read_manifest(manifest_path)
        self._validate_manifest_identity(manifest, lease)
        manifest["status"] = lease.status
        manifest["patch_summary_ids"] = list(lease.patch_summary_ids)
        if lease.sandbox_lease_id or lease.id:
            manifest["sandbox_lease_id"] = lease.sandbox_lease_id or lease.id
        manifest["expires_at"] = str(lease.expires_at)
        manifest["updated_at"] = _isoformat(_utc_now(self._clock))
        self._write_manifest(manifest_path, manifest)

    async def _source_root_for_repo(self, repo_id: str) -> Path:
        if repo_id in self.repo_sources:
            return self.repo_sources[repo_id]
        if self.store is not None:
            for name in (
                "get_repo_root",
                "repo_root_for_id",
                "get_canonical_repo_root",
                "canonical_repo_root_for_id",
            ):
                method = getattr(self.store, name, None)
                if method is None:
                    continue
                result = await _maybe_await(method(repo_id))
                if result:
                    return Path(str(result))
        raise SandboxAllocationError(f"no repo source registered for {repo_id}")

    async def _capture_repo_patch(
        self,
        *,
        repo_id: str,
        repo_root: Path,
        base_commit: str,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
    ) -> tuple[SandboxRepoPatch, bool, bytes]:
        sandbox_root = Path(str(manifest["root"]))
        self._validate_repo_root(
            repo_root,
            sandbox_root=sandbox_root,
            expected_commit=None,
        )
        if not base_commit:
            raise SandboxCaptureError(f"missing base commit for repo {repo_id}")
        blocked_roots = [
            Path(path).resolve(strict=False)
            for path in manifest.get("blocked_roots", [])
        ]
        self._reject_symlink_escapes(repo_root, blocked_roots)
        before_index = self._normal_index_digest(repo_root)
        head_commit = self._git_text(repo_root, ["rev-parse", "HEAD"]).strip() or None

        with tempfile.TemporaryDirectory(prefix="iriai-sandbox-index-") as temp_dir:
            index_path = Path(temp_dir) / "index"
            env = {"GIT_INDEX_FILE": str(index_path)}
            self._git_text(repo_root, ["read-tree", base_commit], env=env)
            self._git_text(repo_root, ["add", "-A", "--", "."], env=env)
            diff_bytes = self._git_bytes(
                repo_root,
                [
                    "diff",
                    "--cached",
                    "--binary",
                    "--find-renames",
                    "--full-index",
                    base_commit,
                    "--",
                ],
                env=env,
            )
            name_status = self._git_bytes(
                repo_root,
                [
                    "diff",
                    "--cached",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    base_commit,
                    "--",
                ],
                env=env,
            )
            raw_diff = self._git_bytes(
                repo_root,
                [
                    "diff",
                    "--cached",
                    "--raw",
                    "-z",
                    "--find-renames",
                    base_commit,
                    "--",
                ],
                env=env,
            )
            numstat = self._git_bytes(
                repo_root,
                [
                    "diff",
                    "--cached",
                    "--numstat",
                    "-z",
                    "--find-renames",
                    base_commit,
                    "--",
                ],
                env=env,
            )

        created, modified, deleted, renamed = _parse_name_status(name_status)
        mode_changed, executable_changed = _parse_raw_modes(raw_diff)
        binary_paths = _parse_binary_paths(numstat)
        changed_paths = _sorted_unique(
            [
                *created,
                *modified,
                *deleted,
                *(old for old, _new in renamed),
                *(new for _old, new in renamed),
                *mode_changed,
                *binary_paths,
            ]
        )
        for changed_path in changed_paths:
            self._validate_changed_path(
                repo_root=repo_root,
                repo_path=changed_path,
                blocked_roots=blocked_roots,
            )
        outside_contract_paths = self._outside_contract_paths(
            repo_root=repo_root,
            changed_paths=changed_paths,
            manifest=manifest,
        )
        diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()
        diff_artifact_id = await self._write_diff_artifact(
            manifest=manifest,
            lease=lease,
            repo_id=repo_id,
            diff_bytes=diff_bytes,
            diff_sha256=diff_sha256,
        )
        after_index = self._normal_index_digest(repo_root)
        repo_patch = SandboxRepoPatch(
            repo_id=repo_id,
            base_commit=base_commit,
            head_commit=head_commit,
            changed_paths=changed_paths,
            created_paths=_sorted_unique(created),
            modified_paths=_sorted_unique(modified),
            deleted_paths=_sorted_unique(deleted),
            renamed_paths=sorted(set(renamed)),
            binary_paths=_sorted_unique(binary_paths),
            mode_changed_paths=_sorted_unique(mode_changed),
            executable_bit_changed_paths=_sorted_unique(executable_changed),
            outside_contract_paths=outside_contract_paths,
            diff_sha256=diff_sha256,
            diff_artifact_id=diff_artifact_id,
        )
        return repo_patch, before_index == after_index, diff_bytes

    async def _record_patch_summary(
        self,
        *,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
        repo_patch: SandboxRepoPatch,
        diff_bytes: bytes,
    ) -> int:
        workspace_snapshot_id = self._workspace_snapshot_id_for_repo(
            repo_patch.repo_id,
            manifest=manifest,
            lease=lease,
        )
        summary_hash_payload = {
            "feature_id": str(manifest.get("feature_id") or ""),
            "dag_sha256": str(manifest.get("dag_sha256") or ""),
            "group_idx": manifest.get("group_idx"),
            "attempt_no": manifest.get("attempt_no"),
            "sandbox_id": lease.sandbox_id,
            "repo_id": repo_patch.repo_id,
            "workspace_snapshot_id": workspace_snapshot_id,
            "base_commit": repo_patch.base_commit,
            "head_commit": repo_patch.head_commit,
            "contract_ids": list(manifest.get("contract_ids") or []),
            "changed_paths": repo_patch.changed_paths,
            "created_paths": repo_patch.created_paths,
            "modified_paths": repo_patch.modified_paths,
            "deleted_paths": repo_patch.deleted_paths,
            "renamed_paths": dict(repo_patch.renamed_paths),
            "binary_paths": repo_patch.binary_paths,
            "mode_changed_paths": repo_patch.mode_changed_paths,
            "executable_bit_changed_paths": repo_patch.executable_bit_changed_paths,
            "outside_contract_paths": repo_patch.outside_contract_paths,
            "diff_sha256": repo_patch.diff_sha256,
            "diff_bytes": len(diff_bytes),
        }
        summary_sha256 = _stable_digest(summary_hash_payload)
        base_snapshot_by_repo = self._base_snapshot_by_repo_for_manifest(
            manifest,
            lease=lease,
        )
        fields = {
            "feature_id": str(manifest.get("feature_id") or ""),
            "dag_sha256": str(manifest.get("dag_sha256") or ""),
            "group_idx": manifest.get("group_idx"),
            "attempt_no": manifest.get("attempt_no"),
            "sandbox_id": lease.sandbox_id,
            "task_id": ",".join(str(item) for item in manifest.get("task_ids") or []),
            "contract_ids": list(manifest.get("contract_ids") or []),
            "repo_id": repo_patch.repo_id,
            "base_commit": repo_patch.base_commit,
            "changed_paths": repo_patch.changed_paths,
            "created_paths": repo_patch.created_paths,
            "modified_paths": repo_patch.modified_paths,
            "deleted_paths": repo_patch.deleted_paths,
            "renamed_paths": dict(repo_patch.renamed_paths),
            "diff_sha256": repo_patch.diff_sha256,
            "diff_artifact_id": repo_patch.diff_artifact_id,
            "summary": (
                f"{len(repo_patch.changed_paths)} changed path(s), "
                f"{len(repo_patch.outside_contract_paths)} outside contract"
            ),
            "stage": "sandbox_capture",
            "metadata": {
                "workspace_snapshot_id": workspace_snapshot_id,
                "base_snapshot_id": workspace_snapshot_id,
                "base_snapshot_ids": list(manifest.get("base_snapshot_ids") or []),
                "base_snapshot_by_repo": base_snapshot_by_repo,
                "summary_hash": summary_sha256,
                "summary_sha256": summary_sha256,
                "computed_summary_sha256": summary_sha256,
                "summary_hash_payload": summary_hash_payload,
                "binary_paths": repo_patch.binary_paths,
                "mode_changed_paths": repo_patch.mode_changed_paths,
                "executable_bit_changed_paths": repo_patch.executable_bit_changed_paths,
                "outside_contract_paths": repo_patch.outside_contract_paths,
                "head_commit": repo_patch.head_commit,
                "empty": not repo_patch.changed_paths,
                "diff_bytes": len(diff_bytes),
            },
            "payload": {
                **repo_patch.model_dump(mode="json"),
                "workspace_snapshot_id": workspace_snapshot_id,
                "base_snapshot_id": workspace_snapshot_id,
                "base_snapshot_ids": list(manifest.get("base_snapshot_ids") or []),
                "base_snapshot_by_repo": base_snapshot_by_repo,
                "summary_hash": summary_sha256,
                "summary_sha256": summary_sha256,
                "computed_summary_sha256": summary_sha256,
                "summary_hash_payload": summary_hash_payload,
            },
            "idempotency_key": _stable_digest(
                {
                    "sandbox_id": lease.sandbox_id,
                    "repo_id": repo_patch.repo_id,
                    "diff_sha256": repo_patch.diff_sha256,
                    "workspace_snapshot_id": workspace_snapshot_id,
                    "contract_ids": list(manifest.get("contract_ids") or []),
                }
            ),
        }
        if self.store is not None:
            method = getattr(self.store, "record_patch_summary", None)
            if method is not None:
                try:
                    if StoredPatchSummary is not None:
                        summary = StoredPatchSummary(**fields)
                    else:
                        summary = fields
                    result = await _maybe_await(method(summary))
                except TypeError:
                    result = await _maybe_await(method(fields))
                evidence_id = _extract_evidence_id(result)
                if evidence_id is not None:
                    return evidence_id

        self._patch_summary_counter += 1
        return self._patch_summary_counter

    def _workspace_snapshot_id_for_repo(
        self,
        repo_id: str,
        *,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
    ) -> int | None:
        snapshot_by_repo = self._base_snapshot_by_repo_for_manifest(
            manifest,
            lease=lease,
        )
        snapshot_id = snapshot_by_repo.get(repo_id)
        if snapshot_id is None:
            return None
        try:
            value = int(snapshot_id)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _base_snapshot_by_repo_for_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        lease: SandboxLease,
    ) -> dict[str, int]:
        explicit = manifest.get("base_snapshot_by_repo")
        if isinstance(explicit, Mapping):
            return {
                str(repo_id): int(snapshot_id)
                for repo_id, snapshot_id in explicit.items()
                if _positive_int_or_none(snapshot_id) is not None
            }

        spec = self._specs_by_sandbox.get(lease.sandbox_id)
        if spec is not None:
            return _base_snapshot_by_repo(
                repo_ids=spec.repo_ids,
                base_snapshot_ids=spec.base_snapshot_ids,
            )

        repo_ids = [str(item) for item in manifest.get("repo_ids") or []]
        if not repo_ids:
            repo_ids = [str(repo_id) for repo_id in dict(manifest.get("repo_roots") or {})]
        return _base_snapshot_by_repo(
            repo_ids=repo_ids,
            base_snapshot_ids=list(manifest.get("base_snapshot_ids") or []),
        )

    async def _write_diff_artifact(
        self,
        *,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
        repo_id: str,
        diff_bytes: bytes,
        diff_sha256: str,
    ) -> int:
        key = (
            f"dag-sandbox-patch:g{manifest.get('group_idx')}:"
            f"attempt-{manifest.get('attempt_no')}:repo-{repo_id}.patch"
        )
        metadata = {
            "sandbox_id": lease.sandbox_id,
            "repo_id": repo_id,
            "diff_sha256": diff_sha256,
            "feature_id": manifest.get("feature_id"),
            "dag_sha256": manifest.get("dag_sha256"),
        }
        if self.artifact_writer is not None:
            for name in ("write_artifact_bytes", "write_bytes", "write_binary"):
                method = getattr(self.artifact_writer, name, None)
                if method is None:
                    continue
                try:
                    result = await _maybe_await(
                        method(
                            key,
                            diff_bytes,
                            metadata,
                            feature=SimpleNamespace(id=str(manifest.get("feature_id") or "")),
                        )
                    )
                except TypeError:
                    result = await _maybe_await(method(key, diff_bytes, metadata))
                artifact_id = _extract_artifact_id(result)
                if artifact_id is not None:
                    return artifact_id
            method = getattr(self.artifact_writer, "write_artifact", None)
            if method is not None:
                result = await _maybe_await(
                    method(str(manifest.get("feature_id") or ""), key, diff_bytes.decode("utf-8", "surrogateescape"))
                )
                artifact_id = _extract_artifact_id(result)
                if artifact_id is not None:
                    return artifact_id

        self._artifact_counter += 1
        artifact_id = self._artifact_counter
        artifact_dir = (
            self.workspace_root
            / ".iriai"
            / "artifacts"
            / "sandbox"
            / lease.sandbox_id
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / f"{artifact_id}-{repo_id}-{diff_sha256}.patch").write_bytes(
            diff_bytes
        )
        (artifact_dir / f"{artifact_id}-{repo_id}-{diff_sha256}.json").write_text(
            json.dumps(metadata, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return artifact_id

    def _runtime_roots_from_spec(
        self,
        entries: Sequence[str],
        *,
        repo_roots: Mapping[str, str],
        source_roots: Mapping[str, str],
        sandbox_root: Path,
        default_roots: Sequence[str],
        allow_external: bool,
    ) -> list[str]:
        if not entries:
            return _sorted_unique(str(Path(path).resolve(strict=False)) for path in default_roots)
        roots: list[str] = []
        for entry in entries:
            roots.extend(
                self._map_runtime_root(
                    entry,
                    repo_roots=repo_roots,
                    source_roots=source_roots,
                    sandbox_root=sandbox_root,
                    allow_external=allow_external,
                )
            )
        return _sorted_unique(roots)

    def _map_runtime_root(
        self,
        entry: str,
        *,
        repo_roots: Mapping[str, str],
        source_roots: Mapping[str, str],
        sandbox_root: Path,
        allow_external: bool,
    ) -> list[str]:
        text = str(entry).strip()
        if not text:
            return []
        if text in repo_roots:
            return [str(Path(repo_roots[text]).resolve(strict=False))]
        if ":" in text:
            repo_id, rel = text.split(":", 1)
            if repo_id in repo_roots:
                repo_root = Path(repo_roots[repo_id]).resolve(strict=False)
                mapped = repo_root / rel.lstrip("/")
                resolved = mapped.resolve(strict=False)
                if not _is_relative_to(resolved, repo_root):
                    raise SandboxAllocationError(f"runtime root escapes sandbox: {entry}")
                return [str(resolved)]
        raw = Path(text)
        if raw.is_absolute():
            resolved = raw.resolve(strict=False)
            for repo_id, source in source_roots.items():
                source_path = Path(source).resolve(strict=False)
                if _is_relative_to(resolved, source_path):
                    rel = resolved.relative_to(source_path)
                    return [str((Path(repo_roots[repo_id]) / rel).resolve(strict=False))]
            if _is_relative_to(resolved, sandbox_root.resolve(strict=False)):
                return [str(resolved)]
            if allow_external:
                return [str(resolved)]
            raise SandboxAllocationError(f"runtime root escapes sandbox: {entry}")
        if len(repo_roots) == 1:
            repo_root = Path(next(iter(repo_roots.values())))
            resolved = (repo_root / PurePosixPath(text)).resolve(strict=False)
            if not _is_relative_to(resolved, repo_root.resolve(strict=False)):
                raise SandboxAllocationError(f"runtime root escapes sandbox: {entry}")
            return [str(resolved)]
        roots = []
        for path in repo_roots.values():
            repo_root = Path(path).resolve(strict=False)
            resolved = (repo_root / PurePosixPath(text)).resolve(strict=False)
            if not _is_relative_to(resolved, repo_root):
                raise SandboxAllocationError(f"runtime root escapes sandbox: {entry}")
            roots.append(str(resolved))
        return roots

    def _runtime_cwd_from_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        repo_roots: Mapping[str, str],
    ) -> str:
        sandbox_root = Path(str(manifest["root"])).resolve(strict=True)
        writable_roots = [
            Path(str(path)).resolve(strict=False)
            for path in manifest.get("writable_roots", [])
            if str(path).strip()
        ]
        for root in writable_roots:
            if root.exists() and root.is_dir() and _is_relative_to(root, sandbox_root):
                return str(root)
            for repo_root in repo_roots.values():
                repo_path = Path(repo_root).resolve(strict=True)
                if _is_relative_to(root, repo_path):
                    return str(repo_path)
        if repo_roots:
            return str(Path(next(iter(repo_roots.values()))).resolve(strict=True))
        return str(sandbox_root)

    def _outside_contract_paths(
        self,
        *,
        repo_root: Path,
        changed_paths: Sequence[str],
        manifest: Mapping[str, Any],
    ) -> list[str]:
        writable_roots = [
            Path(path).resolve(strict=False)
            for path in manifest.get("writable_roots", [])
        ]
        if not writable_roots:
            return []
        outside: list[str] = []
        for changed_path in changed_paths:
            resolved = (repo_root / changed_path).resolve(strict=False)
            if not any(_is_relative_to(resolved, root) for root in writable_roots):
                outside.append(changed_path)
        return _sorted_unique(outside)

    def _load_manifest_for_lease(self, lease: SandboxLease) -> dict[str, Any]:
        manifest_path = Path(lease.root) / _MANIFEST_NAME
        if not manifest_path.exists():
            raise SandboxError(f"sandbox manifest missing for {lease.sandbox_id}")
        return self._read_manifest(manifest_path)

    def _validate_manifest_only_recovery_root(
        self,
        manifest_path: Path,
        manifest: Mapping[str, Any],
    ) -> None:
        if manifest_path.name != _MANIFEST_NAME or manifest_path.is_symlink():
            raise SandboxError("manifest-only recovery path is not a direct sandbox manifest")
        expected_root = manifest_path.parent.resolve(strict=False)
        sandbox_tree_root = (self.workspace_root / ".iriai" / "features").resolve(strict=False)
        if not _is_relative_to(expected_root, sandbox_tree_root):
            raise SandboxError("manifest-only recovery root is outside workspace sandbox tree")
        manifest_root = Path(str(manifest.get("root", ""))).resolve(strict=False)
        if manifest_root != expected_root:
            raise SandboxError("manifest-only recovery root does not match discovered manifest path")
        manifest_path_value = manifest.get("manifest_path")
        if manifest_path_value:
            declared_manifest = Path(str(manifest_path_value)).resolve(strict=False)
            if declared_manifest != manifest_path.resolve(strict=False):
                raise SandboxError(
                    "manifest-only recovery manifest_path does not match discovered manifest path"
                )

    def _read_manifest(self, manifest_path: Path) -> dict[str, Any]:
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SandboxError(f"invalid sandbox manifest: {manifest_path}") from exc

    def _write_manifest(self, manifest_path: Path, manifest: Mapping[str, Any]) -> None:
        tmp_path = manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(manifest_path)

    def _lease_from_manifest(self, manifest: Mapping[str, Any]) -> SandboxLease:
        return SandboxLease(
            id=manifest.get("sandbox_lease_id"),
            sandbox_lease_id=manifest.get("sandbox_lease_id"),
            feature_id=str(manifest.get("feature_id") or ""),
            dag_sha256=str(manifest.get("dag_sha256") or ""),
            group_idx=int(manifest.get("group_idx") or 0),
            attempt_no=int(manifest.get("attempt_no") or 0),
            mode=str(manifest.get("mode") or "task"),  # type: ignore[arg-type]
            idempotency_key=str(manifest.get("idempotency_key") or ""),
            sandbox_id=str(manifest["sandbox_id"]),
            root=str(manifest["root"]),
            manifest_path=str(Path(str(manifest["root"])) / _MANIFEST_NAME),
            repo_roots={
                str(repo_id): str(path)
                for repo_id, path in dict(manifest.get("repo_roots", {})).items()
            },
            base_commits={
                str(repo_id): str(commit)
                for repo_id, commit in dict(manifest.get("base_commits", {})).items()
            },
            writable_roots=[str(item) for item in manifest.get("writable_roots", [])],
            readonly_roots=[str(item) for item in manifest.get("readonly_roots", [])],
            blocked_roots=[str(item) for item in manifest.get("blocked_roots", [])],
            expires_at=str(manifest["expires_at"]),
            owner=str(manifest["owner"]),
            status=str(manifest.get("status") or "allocated"),  # type: ignore[arg-type]
            patch_summary_ids=[
                int(item) for item in manifest.get("patch_summary_ids", [])
            ],
        )

    def _coerce_lease(self, value: Any) -> SandboxLease:
        if isinstance(value, SandboxLease):
            return value
        if isinstance(value, Mapping):
            return SandboxLease.model_validate(value)
        data = {
            field: getattr(value, field)
            for field in SandboxLease.model_fields
            if hasattr(value, field)
        }
        if not data.get("root") and hasattr(value, "sandbox_root"):
            data["root"] = getattr(value, "sandbox_root")
        if not data.get("manifest_path") and data.get("root"):
            data["manifest_path"] = str(Path(str(data["root"])) / _MANIFEST_NAME)
        if not data.get("expires_at") and hasattr(value, "leased_until"):
            data["expires_at"] = getattr(value, "leased_until")
        if isinstance(data.get("expires_at"), datetime):
            data["expires_at"] = _isoformat(data["expires_at"])
        if not data.get("owner") and hasattr(value, "lease_owner"):
            data["owner"] = getattr(value, "lease_owner")
        if not data.get("sandbox_lease_id") and data.get("id"):
            data["sandbox_lease_id"] = data["id"]
        if not data.get("patch_summary_ids"):
            data["patch_summary_ids"] = []
        return SandboxLease.model_validate(data)

    def _validate_source_root(self, repo_id: str, source_root: Path) -> None:
        if not source_root.exists():
            raise SandboxAllocationError(f"repo source missing for {repo_id}: {source_root}")
        if not source_root.is_dir():
            raise SandboxAllocationError(f"repo source is not a directory: {source_root}")
        if source_root.is_symlink():
            raise SandboxAllocationError(f"repo source cannot be a symlink: {source_root}")
        symlink_component = _first_symlink_component_under_roots(
            source_root,
            self.allowed_source_roots,
        )
        if symlink_component is not None:
            raise SandboxAllocationError(
                f"repo source path ancestor cannot be a symlink: {symlink_component}"
            )
        resolved = source_root.resolve(strict=True)
        if self.allowed_source_roots and not any(
            _is_relative_to(resolved, root) for root in self.allowed_source_roots
        ):
            raise SandboxAllocationError(f"repo source outside allowed roots: {source_root}")
        git_marker = source_root / ".git"
        if not git_marker.exists():
            raise SandboxAllocationError(f"repo source is not a git checkout: {source_root}")
        if git_marker.is_symlink():
            raise SandboxAllocationError(f"repo source .git cannot be a symlink: {source_root}")
        try:
            git_dir = self._git_dir_from_marker(source_root, git_marker).resolve(strict=False)
        except SandboxError as exc:
            raise SandboxAllocationError(str(exc)) from exc
        common_dir_text = self._git_text(source_root, ["rev-parse", "--git-common-dir"]).strip()
        common_dir = Path(common_dir_text)
        if not common_dir.is_absolute():
            common_dir = source_root / common_dir
        allowed_metadata_roots = self.allowed_source_roots or [resolved]
        for metadata_path in (git_dir, common_dir.resolve(strict=False)):
            if not any(_is_relative_to(metadata_path, root) for root in allowed_metadata_roots):
                raise SandboxAllocationError(
                    f"repo source git metadata escapes allowed roots for {repo_id}: "
                    f"{metadata_path}"
                )

    def _validate_repo_root(
        self,
        repo_root: Path,
        *,
        sandbox_root: Path,
        expected_commit: str | None,
    ) -> None:
        if not repo_root.exists():
            raise SandboxError(f"sandbox repo root missing: {repo_root}")
        if not repo_root.is_dir():
            raise SandboxError(f"sandbox repo root is not a directory: {repo_root}")
        if repo_root.is_symlink():
            raise SandboxError(f"sandbox repo root cannot be a symlink: {repo_root}")
        repo_resolved = repo_root.resolve(strict=True)
        sandbox_resolved = sandbox_root.resolve(strict=False)
        if not _is_relative_to(repo_resolved, sandbox_resolved):
            raise SandboxError(f"sandbox repo root escapes sandbox: {repo_root}")
        git_marker = repo_root / ".git"
        if not git_marker.exists():
            raise SandboxError(f"sandbox repo root missing .git: {repo_root}")
        if git_marker.is_symlink():
            raise SandboxError(f"sandbox repo .git cannot be a symlink: {repo_root}")
        git_path = self._git_dir_from_marker(repo_root, git_marker)
        if not _is_relative_to(git_path.resolve(strict=False), sandbox_resolved):
            raise SandboxError(f"sandbox git dir escapes sandbox: {git_path}")
        try:
            common_dir_text = self._git_text(repo_root, ["rev-parse", "--git-common-dir"]).strip()
        except Exception as exc:
            raise SandboxError(
                f"sandbox git common dir could not be resolved inside sandbox: {repo_root}"
            ) from exc
        common_dir = Path(common_dir_text)
        if not common_dir.is_absolute():
            common_dir = repo_root / common_dir
        if not _is_relative_to(common_dir.resolve(strict=False), sandbox_resolved):
            raise SandboxError(f"sandbox git common dir escapes sandbox: {common_dir}")
        if expected_commit:
            head = self._git_text(repo_root, ["rev-parse", "HEAD"]).strip()
            if head != expected_commit:
                raise SandboxAllocationError(
                    f"sandbox repo {repo_root} at {head}, expected {expected_commit}"
                )

    def _git_dir_from_marker(self, repo_root: Path, git_marker: Path) -> Path:
        if git_marker.is_dir():
            return git_marker
        text = git_marker.read_text(encoding="utf-8", errors="replace").strip()
        if not text.startswith("gitdir:"):
            raise SandboxError(f"invalid .git file: {git_marker}")
        git_dir = Path(text.split(":", 1)[1].strip())
        if not git_dir.is_absolute():
            git_dir = repo_root / git_dir
        return git_dir

    def _validate_manifest(
        self,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
        *,
        verify_heads: bool,
    ) -> None:
        self._validate_manifest_identity(manifest, lease)
        manifest_root = Path(str(manifest.get("root", ""))).resolve(strict=False)
        for repo_id, repo_root in dict(manifest.get("repo_roots", {})).items():
            base_commit = dict(manifest.get("base_commits", {})).get(repo_id)
            self._validate_repo_root(
                Path(str(repo_root)),
                sandbox_root=manifest_root,
                expected_commit=str(base_commit) if verify_heads and base_commit else None,
            )

    def _validate_manifest_identity(
        self,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
    ) -> None:
        if manifest.get("manifest_version") != _MANIFEST_VERSION:
            raise SandboxError("unsupported sandbox manifest version")
        if str(manifest.get("sandbox_id")) != lease.sandbox_id:
            raise SandboxError("lease and manifest sandbox_id disagree")
        manifest_root = Path(str(manifest.get("root", ""))).resolve(strict=False)
        lease_root = Path(lease.root).resolve(strict=False)
        if manifest_root != lease_root:
            raise SandboxError("lease and manifest root disagree")

    def _validate_release_ownership(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        lease: SandboxLease,
    ) -> None:
        root_resolved = root.resolve(strict=True)
        workspace_iriai = (self.workspace_root / ".iriai").resolve(strict=False)
        if not _is_relative_to(root_resolved, workspace_iriai):
            raise SandboxReleaseError(f"refusing to delete sandbox outside .iriai: {root}")
        if str(manifest.get("sandbox_id")) != lease.sandbox_id:
            raise SandboxReleaseError("manifest id does not match lease")
        manifest_owner = str(manifest.get("owner") or "")
        if manifest_owner and manifest_owner != lease.owner:
            raise SandboxReleaseError("manifest owner does not match lease owner")
        if lease.owner and lease.owner != self.owner:
            raise SandboxReleaseError("refusing to release sandbox owned by another runner")
        for repo_root in dict(manifest.get("repo_roots", {})).values():
            repo_resolved = Path(str(repo_root)).resolve(strict=False)
            if not _is_relative_to(repo_resolved, root_resolved):
                raise SandboxReleaseError(
                    f"manifest repo root is outside sandbox root: {repo_root}"
                )
            for blocked in manifest.get("blocked_roots", []):
                blocked_resolved = Path(str(blocked)).resolve(strict=False)
                if _is_relative_to(repo_resolved, blocked_resolved):
                    raise SandboxReleaseError(
                        f"manifest repo root resolves into blocked root: {repo_root}"
                    )

    def _reject_symlink_escapes(
        self,
        repo_root: Path,
        blocked_roots: Sequence[Path],
    ) -> None:
        repo_resolved = repo_root.resolve(strict=True)
        for current, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
            dirnames[:] = [name for name in dirnames if name != ".git"]
            for name in [*dirnames, *filenames]:
                path = Path(current) / name
                if not path.is_symlink():
                    continue
                target = path.resolve(strict=False)
                if not _is_relative_to(target, repo_resolved):
                    raise SandboxIsolationError(
                        f"symlink escape in sandbox repo: {_repo_rel(repo_root, path)} -> {target}"
                    )
                if any(_is_relative_to(target, blocked) for blocked in blocked_roots):
                    raise SandboxIsolationError(
                        f"symlink resolves into blocked root: {_repo_rel(repo_root, path)}"
                    )

    def _validate_changed_path(
        self,
        *,
        repo_root: Path,
        repo_path: str,
        blocked_roots: Sequence[Path],
    ) -> None:
        normalized = _normalize_repo_path(repo_path)
        repo_resolved = repo_root.resolve(strict=True)
        candidate = repo_root / PurePosixPath(normalized)
        resolved = candidate.resolve(strict=False)
        if not _is_relative_to(resolved, repo_resolved):
            raise SandboxIsolationError(f"changed path escapes repo root: {repo_path}")
        if any(_is_relative_to(resolved, blocked) for blocked in blocked_roots):
            raise SandboxIsolationError(
                f"changed path resolves into blocked root: {repo_path}"
            )

    def _normal_index_digest(self, repo_root: Path) -> str:
        index_path_text = self._git_text(repo_root, ["rev-parse", "--git-path", "index"]).strip()
        index_path = Path(index_path_text)
        if not index_path.is_absolute():
            index_path = repo_root / index_path
        if not index_path.exists():
            return _EMPTY_SHA256
        return hashlib.sha256(index_path.read_bytes()).hexdigest()

    def _sandbox_root(self, spec: SandboxSpec) -> Path:
        return (
            self.workspace_root
            / ".iriai"
            / "features"
            / _slugify(spec.feature_id)
            / "sandboxes"
            / f"g{spec.group_idx}"
            / f"attempt-{spec.attempt_no}"
        )

    def _validate_sandbox_allocation_path(self, sandbox_root: Path) -> None:
        workspace_iriai = Path(os.path.abspath(self.workspace_root / ".iriai"))
        sandbox_lexical = Path(os.path.abspath(sandbox_root))
        try:
            sandbox_lexical.relative_to(workspace_iriai)
        except ValueError as exc:
            raise SandboxAllocationError(
                f"sandbox path escapes workspace .iriai: {sandbox_root}"
            ) from exc

        try:
            relative_parts = sandbox_lexical.relative_to(workspace_iriai).parts
        except ValueError as exc:  # pragma: no cover - guarded above.
            raise SandboxAllocationError(
                f"sandbox path escapes workspace .iriai: {sandbox_root}"
            ) from exc

        candidate = workspace_iriai
        for part in ("", *relative_parts):
            if part:
                candidate = candidate / part
            if candidate.is_symlink():
                raise SandboxAllocationError(
                    f"sandbox path ancestor cannot be a symlink: {candidate}"
                )
            if candidate.exists() and not candidate.is_dir():
                raise SandboxAllocationError(
                    f"sandbox path ancestor is not a directory: {candidate}"
                )

    def _sandbox_id(self, spec: SandboxSpec) -> str:
        return (
            f"sandbox-{_slugify(spec.feature_id)}-g{spec.group_idx}-"
            f"attempt-{spec.attempt_no}-{_stable_digest(spec.idempotency_key)[:12]}"
        )

    def _git_text(
        self,
        cwd: Path,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> str:
        return self._git_bytes(cwd, args, env=env).decode("utf-8", "replace")

    def _git_bytes(
        self,
        cwd: Path,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> bytes:
        result = self._run_command(cwd, ["git", *args], env=env)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise SandboxError(
                f"git {' '.join(args)} failed in {cwd}: {stderr or result.returncode}"
            )
        return result.stdout

    def _run_command(
        self,
        cwd: Path,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        merged_env = os.environ.copy()
        if env:
            merged_env.update({str(key): str(value) for key, value in env.items()})
        if self.command_runner is None:
            completed = subprocess.run(
                list(argv),
                cwd=str(cwd),
                env=merged_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            return CommandResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        result = self.command_runner(cwd, list(argv), merged_env)
        if asyncio.iscoroutine(result):
            raise SandboxError("async command_runner is not supported from sync git helper")
        return _coerce_command_result(result)


def _parse_name_status(data: bytes) -> tuple[list[str], list[str], list[str], list[tuple[str, str]]]:
    tokens = _zsplit(data)
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []
    idx = 0
    while idx < len(tokens):
        status = _decode_token(tokens[idx])
        idx += 1
        if not status:
            continue
        kind = status[0]
        if kind in {"R", "C"}:
            if idx + 1 >= len(tokens):
                break
            old_path = _normalize_repo_path(_decode_token(tokens[idx]))
            new_path = _normalize_repo_path(_decode_token(tokens[idx + 1]))
            idx += 2
            if kind == "R":
                renamed.append((old_path, new_path))
            else:
                created.append(new_path)
            continue
        if idx >= len(tokens):
            break
        path = _normalize_repo_path(_decode_token(tokens[idx]))
        idx += 1
        if kind == "A":
            created.append(path)
        elif kind == "D":
            deleted.append(path)
        elif kind in {"M", "T", "U"}:
            modified.append(path)
    return created, modified, deleted, renamed


def _parse_raw_modes(data: bytes) -> tuple[list[str], list[str]]:
    tokens = _zsplit(data)
    mode_changed: list[str] = []
    executable_changed: list[str] = []
    idx = 0
    while idx < len(tokens):
        header = _decode_token(tokens[idx])
        idx += 1
        if not header.startswith(":"):
            continue
        parts = header.split()
        if len(parts) < 5:
            continue
        old_mode = parts[0][1:]
        new_mode = parts[1]
        status = parts[4]
        if idx >= len(tokens):
            break
        path = _normalize_repo_path(_decode_token(tokens[idx]))
        idx += 1
        if status and status[0] in {"R", "C"}:
            if idx >= len(tokens):
                break
            path = _normalize_repo_path(_decode_token(tokens[idx]))
            idx += 1
        if old_mode == "000000" or new_mode == "000000" or old_mode == new_mode:
            continue
        mode_changed.append(path)
        if _mode_executable(old_mode) != _mode_executable(new_mode):
            executable_changed.append(path)
    return mode_changed, executable_changed


def _parse_binary_paths(data: bytes) -> list[str]:
    tokens = data.split(b"\0")
    binary_paths: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        idx += 1
        if not token:
            continue
        parts = token.split(b"\t")
        if len(parts) < 3 or parts[0] != b"-" or parts[1] != b"-":
            continue
        path_field = parts[2]
        if path_field:
            binary_paths.append(_normalize_repo_path(_decode_token(path_field)))
        elif idx + 1 < len(tokens):
            _old_path = tokens[idx]
            new_path = tokens[idx + 1]
            idx += 2
            if new_path:
                binary_paths.append(_normalize_repo_path(_decode_token(new_path)))
    return _sorted_unique(binary_paths)


def _mode_executable(mode: str) -> bool:
    return mode.endswith("755")


def _normalize_repo_path(path: str) -> str:
    if "\x00" in path:
        raise SandboxIsolationError("repo path contains NUL")
    posix = PurePosixPath(path)
    if posix.is_absolute():
        raise SandboxIsolationError(f"repo path cannot be absolute: {path}")
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SandboxIsolationError(f"repo path contains traversal: {path}")
    return posix.as_posix()


def _zsplit(data: bytes) -> list[bytes]:
    return [token for token in data.split(b"\0") if token]


def _decode_token(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _repo_rel(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _coerce_command_result(value: Any) -> CommandResult:
    if isinstance(value, CommandResult):
        return value
    if isinstance(value, subprocess.CompletedProcess):
        stdout = value.stdout or b""
        stderr = value.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if isinstance(stderr, str):
            stderr = stderr.encode()
        return CommandResult(value.returncode, stdout, stderr)
    if isinstance(value, tuple) and len(value) >= 2:
        returncode = int(value[0])
        stdout = value[1]
        stderr = value[2] if len(value) > 2 else b""
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if isinstance(stderr, str):
            stderr = stderr.encode()
        return CommandResult(returncode, stdout, stderr)
    if isinstance(value, bytes):
        return CommandResult(0, value)
    if isinstance(value, str):
        return CommandResult(0, value.encode())
    raise SandboxError(f"unsupported command runner result: {type(value)!r}")


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
        return await value
    return value


def _extract_evidence_id(value: Any) -> int | None:
    if value is None:
        return None
    for candidate in (
        getattr(getattr(value, "evidence", None), "id", None),
        getattr(value, "evidence_node_id", None),
        getattr(value, "id", None),
    ):
        if candidate is not None:
            return int(candidate)
    if isinstance(value, Mapping):
        evidence = value.get("evidence")
        if isinstance(evidence, Mapping) and evidence.get("id") is not None:
            return int(evidence["id"])
        for key in ("evidence_node_id", "id", "patch_summary_id"):
            if value.get(key) is not None:
                return int(value[key])
    return None


def _extract_artifact_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        for key in ("artifact_id", "id"):
            if value.get(key) is not None:
                return int(value[key])
        return None
    for key in ("artifact_id", "id"):
        candidate = getattr(value, key, None)
        if candidate is not None:
            return int(candidate)
    return None


def _runtime_binding_metadata(binding: RuntimeWorkspaceBinding) -> dict[str, Any]:
    return {
        "sandbox_id": binding.sandbox_id,
        "sandbox_lease_id": binding.sandbox_lease_id,
        "runtime": binding.runtime,
        "cwd": binding.cwd,
        "workspace_override": binding.workspace_override,
        "repo_roots": dict(binding.repo_roots),
        "writable_roots": list(binding.writable_roots),
        "readonly_roots": list(binding.readonly_roots),
        "blocked_roots": list(binding.blocked_roots),
        "base_snapshot_ids": list(binding.role_metadata.get("base_snapshot_ids") or []),
        "base_snapshot_by_repo": dict(
            binding.role_metadata.get("base_snapshot_by_repo") or {}
        ),
        "manifest_path": binding.manifest_path,
        "expires_at": binding.expires_at,
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _first_symlink_component_under_roots(
    path: Path,
    roots: Sequence[Path],
) -> Path | None:
    absolute = Path(path).absolute()
    for root in roots:
        root_abs = Path(root).absolute()
        try:
            relative = absolute.relative_to(root_abs)
        except ValueError:
            continue
        current = root_abs
        for part in relative.parts:
            current = current / part
            try:
                if current.is_symlink():
                    return current
            except OSError:
                return current
        return None
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return current
        except OSError:
            return current
    return None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug.lower() or "sandbox"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _base_snapshot_by_repo(
    *,
    repo_ids: Sequence[str],
    base_snapshot_ids: Sequence[Any],
) -> dict[str, int]:
    snapshots: dict[str, int] = {}
    for idx, repo_id in enumerate(repo_ids):
        if idx >= len(base_snapshot_ids):
            continue
        snapshot_id = _positive_int_or_none(base_snapshot_ids[idx])
        if snapshot_id is not None:
            snapshots[str(repo_id)] = snapshot_id
    return snapshots


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Slice 11d -- pure sandbox-lifecycle helpers moved byte-for-byte from
# ``workflows/develop/phases/implementation.py``. These complement the
# Slice-04 ``SandboxRunner`` lifecycle (`allocate`/`bind_runtime`/
# `capture_patch`/`release`/`recover`) above by providing the pure data-shape
# / format / IO helpers callers use to plumb sandbox lifecycle events.
# Re-exported from ``implementation.py`` via the Slice-11d shim block so every
# existing legacy import + monkeypatch target keeps resolving to the SAME
# object.
# ---------------------------------------------------------------------------


def _sandbox_blocker(message: str, *, task_id: str | None = None) -> SandboxWorkflowBlocker:
    return SandboxWorkflowBlocker(message, task_id=task_id)


def _is_terminal_sandbox_attempt_blocker(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "terminal sandbox lease" in text
        or "retained sandbox evidence requires a new attempt" in text
        or "retained sandbox evidence cannot be reused" in text
    )


def _sandbox_manifest_for_binding(binding: RuntimeSandboxTaskBinding) -> dict[str, Any]:
    manifest_path = Path(str(binding.lease.root)) / "sandbox-manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _sandbox_blocker(
            f"Sandbox manifest could not be read for {binding.lease.sandbox_id}: {exc}"
        ) from exc


def _repair_repo_id_for_sandbox(
    group_tasks: list[ImplementationTask],
    contracts_by_task_id: dict[str, Any] | None,
    *,
    feature_root: Path | None,
    ws_path: str | None,
) -> str:
    contracts_by_task_id = contracts_by_task_id or {}
    for task in group_tasks:
        contract = contracts_by_task_id.get(task.id)
        repo_id = str(getattr(contract, "repo_id", "") or task.repo_path or "").strip()
        if repo_id:
            return repo_id
    if feature_root is not None and ws_path:
        try:
            resolved_feature = feature_root.resolve()
            resolved_ws = Path(ws_path).resolve()
            if resolved_ws != resolved_feature:
                return resolved_ws.relative_to(resolved_feature).as_posix()
        except Exception:
            pass
    return "repo"


def _sandbox_prompt_context_dir(
    context_base: Path,
    *,
    task_id: str,
    context_segment: str,
) -> Path:
    base = context_base.resolve(strict=False)
    context_root = base / ".iriai-context"
    if context_root.is_symlink():
        raise _sandbox_blocker(
            f"Prompt context root is symlinked for task {task_id}: {context_root}",
            task_id=task_id,
        )
    context_dir = context_root / context_segment
    if context_dir.is_symlink():
        raise _sandbox_blocker(
            f"Prompt context directory is symlinked for task {task_id}: {context_dir}",
            task_id=task_id,
        )
    resolved = context_dir.resolve(strict=False)
    try:
        resolved.relative_to(context_root.resolve(strict=False))
    except ValueError as exc:
        raise _sandbox_blocker(
            f"Prompt context path escapes sandbox workspace for task {task_id}.",
            task_id=task_id,
        ) from exc
    return context_dir


def _exclude_sandbox_prompt_context_from_capture(
    context_base: Path,
    *,
    context_segment: str,
) -> None:
    git_dir = context_base / ".git"
    if not git_dir.is_dir():
        return
    exclude_path = git_dir / "info" / "exclude"
    pattern = f"/.iriai-context/{context_segment}/"
    try:
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    except OSError:
        existing = ""
    if pattern in {line.strip() for line in existing.splitlines()}:
        return
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"{pattern}\n"
    _write_context_text(exclude_path, text)


__all__ = [
    "CommandResult",
    "PatchCaptureResult",
    "RuntimeWorkspaceBinding",
    "SandboxAllocationError",
    "SandboxBindingError",
    "SandboxCaptureError",
    "SandboxError",
    "SandboxIsolationError",
    "SandboxLease",
    "SandboxMode",
    "SandboxReleaseError",
    "SandboxRepoPatch",
    "SandboxRunner",
    "SandboxSpec",
    "SandboxStatus",
    "_exclude_sandbox_prompt_context_from_capture",
    "_is_terminal_sandbox_attempt_blocker",
    "_repair_repo_id_for_sandbox",
    "_sandbox_blocker",
    "_sandbox_manifest_for_binding",
    "_sandbox_prompt_context_dir",
]
