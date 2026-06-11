"""Sandbox allocation, runtime binding, patch capture, and cleanup.

The runner in this module is deliberately local-filesystem first.  Persistence
and artifact storage can be injected by production wiring, but tests and
recovery code can exercise the isolation rules without depending on the store
slice landing first.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Sequence

try:
    import grp
except ImportError:  # pragma: no cover - non-Unix fallback.
    grp = None  # type: ignore[assignment]

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


logger = logging.getLogger(__name__)


SandboxMode = Literal["wave", "task", "repair", "canonicalization", "diagnostic"]
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
_AUTHORITY_GRANT_SCHEMA_VERSION = "runtime-workspace-authority-grant-v1"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_TERMINAL_STATUSES = {"captured", "released", "retained", "failed", "poisoned"}
_AGENT_SHARED_GROUP_ENV = "IRIAI_AGENT_SHARED_GROUP"
_DEFAULT_AGENT_SHARED_GROUP = "iriai-agents"
_RELEASE_DISPOSITIONS = {
    "release",
    "released",
    "delete",
    "cleanup",
    "retention-expired",
    "retention_expired",
}


def _agent_shared_group() -> tuple[str, int | None]:
    group_name = os.environ.get(_AGENT_SHARED_GROUP_ENV, _DEFAULT_AGENT_SHARED_GROUP)
    group_name = str(group_name or "").strip() or _DEFAULT_AGENT_SHARED_GROUP
    if grp is None:
        return group_name, None
    try:
        return group_name, grp.getgrnam(group_name).gr_gid
    except KeyError:
        return group_name, None


_ALLOCATION_LOCKS_GUARD = threading.Lock()
# Per-feature allocation lock. asyncio.Lock (not threading.RLock) so it can be
# held across an `await` (the off-loop clone) while still serializing concurrent
# same-feature allocations: a reentrant RLock is owned per-thread, so a second
# allocate on the same event-loop thread would slip straight through.
_ALLOCATION_LOCKS: dict[str, asyncio.Lock] = {}


def _allocation_lock_for_feature(feature_slug: str) -> asyncio.Lock:
    with _ALLOCATION_LOCKS_GUARD:
        return _ALLOCATION_LOCKS.setdefault(feature_slug, asyncio.Lock())


# Parallel sandbox provisioning (IRIAI_SANDBOX_PARALLEL_PROVISION, default ON):
# narrow the allocation lock from per-feature to per-sandbox-root so a wave of
# N tasks provisions concurrently (wave prep = max(single task), not sum).
#
# Lock-scope audit (what the wide per-feature lock actually guarded):
#   1. SAME-ROOT atomicity — the manifest-exists reconciliation, mkdir, clone,
#      and manifest write for one sandbox_root must not interleave with a
#      concurrent allocate of the SAME root. PRESERVED: the per-root lock keeps
#      the whole allocate body serialized per root.
#   2. Cross-root shared ancestors (.../sandboxes/gN) — created with
#      mkdir(parents=True, exist_ok=True), which is race-safe; no lock needed.
#   3. SandboxRunner in-memory registries (_leases_by_key/_specs_by_sandbox) —
#      per-instance (the wave path builds one SandboxRunner per task), mutated
#      atomically between awaits on a single event loop; no lock needed.
#   4. Durable store rows — distinct idempotency keys write distinct rows; the
#      store provides its own transactionality. (Even today the
#      _existing_lease_for_key check runs BEFORE the lock, so the wide lock
#      never provided cross-key dedup atomicity.)
#   5. Source-repo reads (rev-parse, clone --local/--no-local, node_modules CoW
#      copy) — read-only on the source; concurrent-safe.
#   6. Package-manager installs — pnpm store / npm cacache / pip cache carry
#      their own concurrency control.
# Setting IRIAI_SANDBOX_PARALLEL_PROVISION=0 restores the wide per-feature
# lock verbatim.
_SANDBOX_PARALLEL_PROVISION_ENV = "IRIAI_SANDBOX_PARALLEL_PROVISION"

# Optional bound on concurrently-provisioning sandboxes (clone + dependency
# install). Unset (default) means no extra bound — effective concurrency is
# the wave width, since each wave task issues exactly one allocate. Set a
# positive integer to cap host load (disk/network/package-manager pressure).
_SANDBOX_PROVISION_CONCURRENCY_ENV = "IRIAI_SANDBOX_PROVISION_CONCURRENCY"

_PROVISION_SEMAPHORES_GUARD = threading.Lock()
# Per-event-loop (asyncio primitives must not cross loops) and per-limit so a
# mid-flight env change can't strand waiters on a differently-sized semaphore.
_PROVISION_SEMAPHORES: "weakref.WeakKeyDictionary[Any, dict[int, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary()
)


def _sandbox_parallel_provision_enabled() -> bool:
    """True when per-sandbox-root allocation locking is enabled (default on).

    Set ``IRIAI_SANDBOX_PARALLEL_PROVISION=0`` to restore the wide per-feature
    allocation lock verbatim (serial wave provisioning)."""
    raw = os.environ.get(_SANDBOX_PARALLEL_PROVISION_ENV, "1")
    return str(raw).strip() not in ("0", "false", "False", "FALSE", "no", "No", "NO")


def _allocation_lock_for_sandbox_root(
    feature_slug: str, sandbox_root: Path
) -> asyncio.Lock:
    # NUL-joined key cannot collide with a bare feature_slug key (slugs are
    # alphanumeric-dash), so per-root and per-feature locks share the registry
    # without aliasing.
    key = f"{feature_slug}\x00{sandbox_root}"
    with _ALLOCATION_LOCKS_GUARD:
        return _ALLOCATION_LOCKS.setdefault(key, asyncio.Lock())


def _allocation_lock_for_allocate(
    feature_slug: str, sandbox_root: Path
) -> asyncio.Lock:
    """Select the allocate() serialization lock per the parallel-provision flag."""
    if _sandbox_parallel_provision_enabled():
        return _allocation_lock_for_sandbox_root(feature_slug, sandbox_root)
    return _allocation_lock_for_feature(feature_slug)


def _provision_concurrency_limit() -> int | None:
    raw = os.environ.get(_SANDBOX_PROVISION_CONCURRENCY_ENV)
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _provision_concurrency_gate() -> Any:
    """Async context manager bounding concurrent clone+install sections.

    Returns a no-op context when ``IRIAI_SANDBOX_PROVISION_CONCURRENCY`` is
    unset/invalid (default: bounded only by the wave width). Must be called
    from a running event loop."""
    limit = _provision_concurrency_limit()
    if limit is None:
        return contextlib.nullcontext()
    loop = asyncio.get_running_loop()
    with _PROVISION_SEMAPHORES_GUARD:
        per_loop = _PROVISION_SEMAPHORES.get(loop)
        if per_loop is None:
            per_loop = {}
            _PROVISION_SEMAPHORES[loop] = per_loop
        semaphore = per_loop.get(limit)
        if semaphore is None:
            semaphore = asyncio.Semaphore(limit)
            per_loop[limit] = semaphore
    return semaphore


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


_SANDBOX_COMMAND_TIMEOUT_ENV = "IRIAI_SANDBOX_COMMAND_TIMEOUT_S"
_DEFAULT_SANDBOX_COMMAND_TIMEOUT_S = 1200.0  # 20 min — generous for large --no-local clones

# Fast local clone: when source and destination are on the same filesystem,
# use `git clone --local` (hardlinked objects, near-instant) instead of
# `git clone --no-local` (full object copy). Disabled by setting
# IRIAI_SANDBOX_LOCAL_CLONE=0.  Automatically falls back to --no-local when
# the cross-filesystem guard triggers (different st_dev values).
_SANDBOX_LOCAL_CLONE_ENV = "IRIAI_SANDBOX_LOCAL_CLONE"

# Sandbox reuse on retry: when enabled, the sandbox idempotency key omits the
# attempt number so that retries of the same task (same DAG sha, group, repos,
# commits, and contracts) reattach to the existing sandbox instead of cloning
# anew.  Off by default; enable with IRIAI_SANDBOX_REUSE_ON_RETRY=1.
_SANDBOX_REUSE_ON_RETRY_ENV = "IRIAI_SANDBOX_REUSE_ON_RETRY"

# Sandbox template + APFS clonefile provisioning: build ONE fully-provisioned
# template (git clone + dependency install) per (feature, repo, digest) and
# provision each task sandbox from it via `cp -c -R` (APFS clonefile(2):
# seconds + CoW metadata instead of ~minutes + gigabytes per task).  The digest
# keys the template by base commit sha + the content of every package lockfile
# present in the source worktree's package roots, so a mid-run lockfile change
# (implementer tasks CAN edit package.json/lockfiles) yields a new digest and
# exactly one template rebuild at the next allocation.  Default ON; set
# IRIAI_SANDBOX_TEMPLATE_COW=0 to restore the legacy full-provisioning path
# verbatim.  ANY template/clonefile failure logs a loud WARNING and falls back
# to the legacy path — never a corrupted sandbox.
_SANDBOX_TEMPLATE_COW_ENV = "IRIAI_SANDBOX_TEMPLATE_COW"

# How long a concurrent allocator waits (seconds) for another builder's
# in-flight template build of the same digest before giving up and falling
# back to legacy provisioning.  A builder lockdir older than twice this value
# is treated as stale (crashed builder) and reclaimed.
_SANDBOX_TEMPLATE_WAIT_ENV = "IRIAI_SANDBOX_TEMPLATE_BUILD_WAIT_S"
_DEFAULT_SANDBOX_TEMPLATE_WAIT_S = 900.0

_TEMPLATE_DIRNAME = "sandbox-template"
_TEMPLATE_REPO_DIRNAME = "repo"
_TEMPLATE_MANIFEST_NAME = "template-manifest.json"
_TEMPLATE_SCHEMA_VERSION = "sandbox-template-v1"
# Lockfiles hashed into the template digest, checked at the repo root and at
# every profile package root (the exact set of roots provisioning touches).
_TEMPLATE_LOCKFILE_NAMES = (
    "pnpm-lock.yaml",
    "package-lock.json",
    "poetry.lock",
    "uv.lock",
)
_TEMPLATE_LOCKFILE_GLOBS = ("requirements*.txt",)
# Templates older than this (and not the one just built) are pruned after a
# successful build.  Generous vs the seconds-scale clonefile so an in-flight
# `cp -c -R` from an older template can never lose its source mid-copy.
_TEMPLATE_PRUNE_GRACE_S = 3600.0

# Template-time permission normalization (IRIAI_SANDBOX_TEMPLATE_PERMS,
# default ON).  APFS clonefile(2) preserves ownership and modes, so running
# the group/mode normalization sweep ONCE on the template at build time means
# every clonefile copy already carries correct permissions; the per-clone full
# tree walk (lstat/chown/chmod over an ~8GB tree, minutes per sandbox)
# downgrades to a cheap bounded spot-verify.  ANY spot-verify mismatch falls
# back to the FULL sweep with a loud WARNING — never a silent skip into wrong
# permissions.  Set =0 to restore today's full per-clone sweep byte-identically
# (no template-time normalization, no marker, full sweep on every clone).
_SANDBOX_TEMPLATE_PERMS_ENV = "IRIAI_SANDBOX_TEMPLATE_PERMS"
# Marker stamped NEXT TO the template repo dir (sibling of the template
# manifest, never inside the repo working tree) before the atomic publish
# rename; records the normalization params for provenance.  Absent marker
# (template built before this change, or with the flag off) => the clone runs
# the full per-clone sweep exactly as before.
_TEMPLATE_PERMS_MARKER_NAME = ".iriai-template-permissions-normalized"
_TEMPLATE_PERMS_SCHEMA_VERSION = "sandbox-template-perms-v1"
# Spot-verify sampling bounds: repo root + .git/.venv/node_modules (when
# present) + up to this many sorted top-level dirs/files, plus a shallow
# sample inside each sampled dir.  O(dozens) of lstats vs the full walk.
_TEMPLATE_PERMS_SPOT_DIR_SAMPLES = 8
_TEMPLATE_PERMS_SPOT_FILE_SAMPLES = 4
_TEMPLATE_PERMS_SPOT_CHILD_SAMPLES = 2


def _sandbox_command_timeout_s() -> float:
    """Hard timeout for a single sandbox subprocess (git) command.

    Without it, subprocess.run blocks forever on a wedged git command. Because
    allocate() runs the clone synchronously, a hung git froze the whole asyncio
    event loop (no watchdog/Slack/other workflow could run). Env-overridable;
    non-finite/non-positive values fall back to the default."""
    raw = os.environ.get(_SANDBOX_COMMAND_TIMEOUT_ENV)
    if raw is None:
        return _DEFAULT_SANDBOX_COMMAND_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SANDBOX_COMMAND_TIMEOUT_S
    if 0 < value < 1e9:
        return value
    return _DEFAULT_SANDBOX_COMMAND_TIMEOUT_S


def _sandbox_local_clone_enabled() -> bool:
    """Return True when fast same-filesystem cloning is allowed (default on).

    Set ``IRIAI_SANDBOX_LOCAL_CLONE=0`` to force ``--no-local`` unconditionally
    (e.g. when the source is a worktree on a different volume)."""
    raw = os.environ.get(_SANDBOX_LOCAL_CLONE_ENV, "1")
    return str(raw).strip() not in ("0", "false", "False", "FALSE", "no", "No", "NO")


def _sandbox_reuse_on_retry_enabled() -> bool:
    """Return True when retry sandbox reuse is enabled (default off).

    Set ``IRIAI_SANDBOX_REUSE_ON_RETRY=1`` to allow same-content retries to
    reattach to the existing sandbox instead of paying a full clone."""
    raw = os.environ.get(_SANDBOX_REUSE_ON_RETRY_ENV, "0")
    return str(raw).strip() in ("1", "true", "True", "TRUE", "yes", "Yes", "YES")


def _same_filesystem(path_a: Path, path_b: Path) -> bool:
    """Return True when *path_a* and *path_b* reside on the same filesystem.

    Uses ``os.stat().st_dev`` on the nearest existing ancestor of each path so
    that the check works even before sandbox directories are created.  A stat
    failure (permission error, race) is treated conservatively as False so the
    caller falls back to the safe ``--no-local`` clone."""
    def _dev(p: Path) -> int | None:
        candidate = p
        for _ in range(64):  # bound the ascent
            try:
                return os.stat(candidate).st_dev
            except (OSError, PermissionError):
                parent = candidate.parent
                if parent == candidate:
                    return None
                candidate = parent
        return None  # pragma: no cover

    dev_a = _dev(path_a)
    dev_b = _dev(path_b)
    if dev_a is None or dev_b is None:
        return False
    return dev_a == dev_b


def _git_clone_args(source: Path, dest: Path) -> list[str]:
    """Return ``git clone`` argument list for *source* → *dest*.

    Selects ``--local`` (hardlinked objects, near-instant on same-volume APFS)
    when ``IRIAI_SANDBOX_LOCAL_CLONE`` is enabled AND source and dest are on
    the same filesystem; otherwise falls back to the original ``--no-local``
    (safe, full object copy across any filesystem boundary)."""
    if _sandbox_local_clone_enabled() and _same_filesystem(source, dest):
        clone_flag = "--local"
    else:
        clone_flag = "--no-local"
    return ["clone", clone_flag, str(source), str(dest)]


def _sandbox_template_cow_enabled() -> bool:
    """Return True when template + APFS clonefile provisioning is on (default).

    Set ``IRIAI_SANDBOX_TEMPLATE_COW=0`` to restore the legacy per-task full
    clone + dependency-install provisioning path verbatim."""
    raw = os.environ.get(_SANDBOX_TEMPLATE_COW_ENV, "1")
    return str(raw).strip() not in ("0", "false", "False", "FALSE", "no", "No", "NO")


def _sandbox_template_perms_enabled() -> bool:
    """Return True when template-time permission normalization is on (default).

    Set ``IRIAI_SANDBOX_TEMPLATE_PERMS=0`` to restore the legacy full
    per-clone permission sweep byte-identically (no template-time
    normalization, no marker, full sweep on every clone)."""
    raw = os.environ.get(_SANDBOX_TEMPLATE_PERMS_ENV, "1")
    return str(raw).strip() not in ("0", "false", "False", "FALSE", "no", "No", "NO")


def _sandbox_template_wait_s() -> float:
    """Seconds to wait on another builder's in-flight template build."""
    raw = os.environ.get(_SANDBOX_TEMPLATE_WAIT_ENV)
    if raw is None:
        return _DEFAULT_SANDBOX_TEMPLATE_WAIT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SANDBOX_TEMPLATE_WAIT_S
    if 0 < value < 1e9:
        return value
    return _DEFAULT_SANDBOX_TEMPLATE_WAIT_S


class SandboxReleaseError(SandboxError):
    """Release refused to delete untrusted filesystem state."""


class _SandboxModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class SandboxWritableRootSpec(_SandboxModel):
    repo_id: str = ""
    path: str
    match_kind: Literal["file", "directory"] = "file"
    allow_create: bool = False
    source: str = "contract"

    @field_validator("path")
    @classmethod
    def _non_empty_path(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("path cannot be empty")
        return text


class RuntimeWorkspaceAuthorityGrant(_SandboxModel):
    schema_version: str = _AUTHORITY_GRANT_SCHEMA_VERSION
    feature_id: str
    group_idx: int
    lane_id: str
    grant_type: Literal["product", "repair", "diagnostic"]
    repo_id: str
    repo_root: str
    contract_roots: list[str] = Field(default_factory=list)
    create_parent_roots: list[str] = Field(default_factory=list)
    write_guard_roots: list[str] = Field(default_factory=list)
    promotable: bool = True
    contract_ids: list[int] = Field(default_factory=list)
    expires_at: str

    @model_validator(mode="after")
    def _validate_grant(self) -> "RuntimeWorkspaceAuthorityGrant":
        if self.schema_version != _AUTHORITY_GRANT_SCHEMA_VERSION:
            raise ValueError("unsupported runtime workspace authority grant schema")
        if not str(self.feature_id).strip():
            raise ValueError("feature_id cannot be empty")
        if self.group_idx < 0:
            raise ValueError("group_idx cannot be negative")
        if not str(self.lane_id).strip():
            raise ValueError("lane_id cannot be empty")
        if not str(self.repo_id).strip():
            raise ValueError("repo_id cannot be empty")
        if not str(self.repo_root).strip():
            raise ValueError("repo_root cannot be empty")
        if self.grant_type == "diagnostic" and self.promotable:
            raise ValueError("diagnostic grants cannot be promotable")
        if self.grant_type in {"product", "repair"} and not self.promotable:
            raise ValueError("product and repair grants must be promotable")
        if not self.write_guard_roots:
            raise ValueError("write_guard_roots cannot be empty")
        if len(set(self.contract_ids)) != len(self.contract_ids):
            raise ValueError("contract_ids must be unique")
        return self

    @property
    def grant_digest(self) -> str:
        return _stable_digest(self.model_dump(mode="json", exclude={"grant_digest"}))


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
    writable_root_specs: list[SandboxWritableRootSpec] = Field(default_factory=list)
    readonly_roots: list[str]
    contract_ids: list[int]
    write_guard_scope: Literal["contract", "diagnostic"] = "contract"
    authority_lane_id: str | None = None
    authority_grant_type: Literal["product", "repair", "diagnostic"] | None = None
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
        seed: dict[str, Any] = {
            "feature_id": self.feature_id,
            "dag_sha256": self.dag_sha256,
            "group_idx": self.group_idx,
            "mode": self.mode,
            "repo_ids": sorted(self.repo_ids),
            "base_commits": {
                repo_id: self.base_commits.get(repo_id, "")
                for repo_id in sorted(self.repo_ids)
            },
            "contract_ids": sorted(self.contract_ids),
        }
        # When IRIAI_SANDBOX_REUSE_ON_RETRY is off (default) include attempt_no
        # in the key so each attempt gets a fresh sandbox (previous behaviour).
        # When enabled, the key is content-digest-only: same task/dag/repos/
        # commits/contracts on a retry reattaches to the existing sandbox
        # instead of paying a full clone + provisioning.
        if not _sandbox_reuse_on_retry_enabled():
            seed["attempt_no"] = self.attempt_no
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
    # Per-repo new-manager provisioning failures (pnpm/pip/poetry). Empty when all
    # roots provisioned (or the legacy best-effort npm path). Shape:
    # {"scope": ..., "repos": {repo_id: [{"root", "manager", "command", "detail"}]}}.
    provisioning: dict[str, Any] = Field(default_factory=dict)


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
    write_guard_roots: list[str] = Field(default_factory=list)
    write_guard_scope: str = "contract"
    authority_schema_version: str = ""
    runtime_workspace_authority_grants: list[dict[str, Any]] = Field(default_factory=list)
    runtime_workspace_authority_grant_digest: str = ""
    promotable: bool = True
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


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of provisioning one package root in a freshly-cloned sandbox.

    ``best_effort`` marks the legacy single-root npm path, whose failures only
    reproduce the pre-existing no-tooling state and so must NOT surface as task
    errors. The new manager paths (pnpm/pip/poetry) set ``best_effort=False`` so
    a failure is logged at ERROR with the exact command, recorded onto the lease,
    and surfaced to the task (AC-K-4) rather than buried in a ``logger.warning``
    that leaves pytest/mypy/tsc mysteriously absent.
    """

    rel_path: str
    manager: str
    ok: bool
    command: str = ""
    detail: str = ""
    best_effort: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.rel_path,
            "manager": self.manager,
            "ok": self.ok,
            "command": self.command,
            "detail": self.detail,
        }


def _command_failure_detail(result: CommandResult, *, limit: int = 600) -> str:
    """Precise, bounded failure string (rc + stderr tail) — never a log dump."""
    stderr = result.stderr.decode("utf-8", "replace").strip()
    tail = stderr[-limit:] if stderr else ""
    return f"rc={result.returncode}" + (f": {tail}" if tail else "")


def _pip_is_installable_package(dest: Path) -> bool:
    """True when ``dest`` is a pip-installable project (so ``pip install -e .`` is safe).

    A bare ``pyproject.toml`` is frequently just tool config (``[tool.black]``,
    ``[tool.pytest.ini_options]``, …) and is NOT installable — running ``-e .``
    on it would fail spuriously. Only treat it as a package when it declares a
    ``[project]`` or ``[build-system]`` table, or a ``setup.py`` exists.
    """
    if (dest / "setup.py").is_file():
        return True
    pyproject = dest / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "[project]" in text or "[build-system]" in text
    return False


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
        project_profile: Any | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.repo_sources = {
            repo_id: Path(path)
            for repo_id, path in dict(repo_sources or {}).items()
        }
        self.store = store
        self.artifact_writer = artifact_writer
        # Inferred ProjectProfile (typed Any to avoid an e2e.models import
        # cycle). None => legacy single-root npm provisioning (studio default).
        self.project_profile = project_profile
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
        # Per-sandbox-root lock by default (wave provisions in parallel);
        # IRIAI_SANDBOX_PARALLEL_PROVISION=0 restores the wide per-feature lock.
        lock = _allocation_lock_for_allocate(feature_slug, sandbox_root)

        async with lock:
            self._validate_sandbox_allocation_path(sandbox_root)
            manifest_path = sandbox_root / _MANIFEST_NAME
            if manifest_path.exists():
                manifest = self._read_manifest(manifest_path)
                if manifest.get("idempotency_key") != idempotency_key:
                    terminal_status = str(manifest.get("status") or "")
                    if terminal_status in _TERMINAL_STATUSES:
                        if terminal_status == "poisoned":
                            raise SandboxAllocationError(
                                f"sandbox path belongs to poisoned lease: {sandbox_root}"
                            )
                        raise SandboxAllocationError(
                            "terminal sandbox lease requires a new attempt idempotency key: "
                            f"{sandbox_root}"
                        )
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
                    self._require_authority_grants_for_fresh_dispatch(manifest)
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
            permission_normalization: dict[str, Any] = {}
            provisioning_failures: dict[str, list[dict[str, Any]]] = {}

            try:
                # Bound concurrent heavy provisioning (clone + dependency
                # install) across parallel allocates when
                # IRIAI_SANDBOX_PROVISION_CONCURRENCY is set; no-op otherwise.
                # Failure routing is unchanged: an exception inside the gate
                # propagates to the except below exactly as before.
                async with _provision_concurrency_gate():
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
                        # Fast path (IRIAI_SANDBOX_TEMPLATE_COW, default ON): one
                        # fully-provisioned template per (feature, repo, lockfile+
                        # base-commit digest), per-task provisioning is an APFS
                        # clonefile of it (~seconds, ~MB of CoW metadata).  Off the
                        # event loop: a digest miss builds the template inline
                        # (clone + dependency install).  ANY failure warns loudly
                        # and falls through to the legacy path below.
                        provisioned_from_template = await asyncio.to_thread(
                            self._provision_repo_from_template,
                            feature_slug=feature_slug,
                            repo_id=repo_id,
                            source_resolved=source_resolved,
                            base_commit=base_commit,
                            repo_root=repo_root,
                        )
                        if provisioned_from_template:
                            # Templates are only published when every provisioning
                            # result is failure-free, so the clone carries none.
                            prov_results: list[ProvisionResult] = []
                        else:
                            # Legacy path — byte-identical to pre-template behaviour.
                            # Run the clone off the event loop. `git clone --no-local` of
                            # a large repo takes minutes (or wedges); calling it inline
                            # froze the entire asyncio loop (no watchdog/Slack/other
                            # workflow could run) — the bridge "hang". to_thread keeps the
                            # loop responsive; the per-command timeout bounds a wedged git.
                            # When source and dest are on the same filesystem,
                            # _git_clone_args() selects --local (hardlinked objects,
                            # near-instant) instead of --no-local; falls back automatically
                            # across filesystem boundaries or when IRIAI_SANDBOX_LOCAL_CLONE=0.
                            await asyncio.to_thread(
                                self._git_text,
                                sandbox_root,
                                _git_clone_args(source_resolved, repo_root),
                            )
                            self._git_text(repo_root, ["checkout", "--detach", base_commit])
                            # Restore gitignored dependencies (e.g. node_modules) so
                            # in-sandbox tooling (tsc/tsgo/Playwright) can self-verify.
                            # Off the event loop like the clone above: an APFS CoW copy
                            # is fast but an npm-ci fallback can be slow.
                            prov_results = await asyncio.to_thread(
                                self._provision_sandbox_dependencies,
                                repo_root,
                                source_resolved,
                            )
                        repo_failures = [
                            r.as_dict()
                            for r in prov_results
                            if not r.ok and not r.best_effort
                        ]
                        if repo_failures:
                            provisioning_failures[repo_id] = repo_failures
                        self._validate_repo_root(
                            repo_root,
                            sandbox_root=sandbox_root,
                            expected_commit=base_commit,
                        )
                        permission_normalization[repo_id] = (
                            self._normalize_clone_permissions_post_provision(
                                repo_root,
                                sandbox_root=sandbox_root,
                                template_repo=provisioned_from_template,
                            )
                        )
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
            mapped_writable_root_specs = self._mapped_writable_root_specs(
                spec.writable_root_specs,
                repo_roots=repo_roots,
                source_roots=source_roots,
                sandbox_root=sandbox_root,
            )
            materialized_create_parents = self._materialize_create_parents(
                mapped_writable_root_specs,
                repo_roots=repo_roots,
                sandbox_root=sandbox_root,
            )
            write_guard_roots = self._write_guard_roots_for_manifest(
                writable_roots=writable_roots,
                writable_root_specs=mapped_writable_root_specs,
                diagnostic=spec.write_guard_scope == "diagnostic",
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
            authority_grants = self._runtime_workspace_authority_grants(
                spec,
                repo_roots=repo_roots,
                writable_roots=writable_roots,
                writable_root_specs=mapped_writable_root_specs,
                materialized_create_parents=materialized_create_parents,
                write_guard_roots=write_guard_roots,
                expires_at=_isoformat(expires_at),
            )
            authority_grant_payloads = [
                _authority_grant_payload(grant) for grant in authority_grants
            ]
            authority_grant_digest = _stable_digest(authority_grant_payloads)
            write_guard_roots = _sorted_unique(
                root
                for grant in authority_grant_payloads
                for root in list(grant.get("write_guard_roots") or [])
            )
            manifest = {
                "manifest_version": _MANIFEST_VERSION,
                "authority_schema_version": _AUTHORITY_GRANT_SCHEMA_VERSION,
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
                "writable_root_specs": mapped_writable_root_specs,
                "write_guard_roots": write_guard_roots,
                "write_guard_scope": spec.write_guard_scope,
                "runtime_workspace_authority_grants": authority_grant_payloads,
                "runtime_workspace_authority_grant_digest": authority_grant_digest,
                "promotable": any(
                    bool(grant.get("promotable")) for grant in authority_grant_payloads
                ),
                "materialized_create_parents": materialized_create_parents,
                "readonly_roots": readonly_roots,
                "permission_normalization": {
                    "scope": "sandbox_repo_roots",
                    "repos": permission_normalization,
                },
                "provisioning": {
                    "scope": "sandbox_package_roots",
                    "repos": provisioning_failures,
                },
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
        self._require_authority_grants_for_fresh_dispatch(manifest)
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
        authority_grants = [
            dict(item)
            for item in list(manifest.get("runtime_workspace_authority_grants") or [])
            if isinstance(item, Mapping)
        ]
        authority_grant_digest = str(
            manifest.get("runtime_workspace_authority_grant_digest") or ""
        )
        authority_schema_version = str(
            manifest.get("authority_schema_version") or ""
        )
        promotable = bool(manifest.get("promotable"))
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
            write_guard_roots=list(manifest.get("write_guard_roots") or []),
            write_guard_scope=str(manifest.get("write_guard_scope") or "contract"),
            authority_schema_version=authority_schema_version,
            runtime_workspace_authority_grants=authority_grants,
            runtime_workspace_authority_grant_digest=authority_grant_digest,
            promotable=promotable,
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
                "authority_schema_version": authority_schema_version,
                "runtime_workspace_authority_grants": authority_grants,
                "runtime_workspace_authority_grant_digest": authority_grant_digest,
                "promotable": promotable,
                "base_snapshot_ids": list(manifest.get("base_snapshot_ids") or []),
                "base_snapshot_by_repo": dict(
                    manifest.get("base_snapshot_by_repo") or {}
                ),
                "provisioning": dict(manifest.get("provisioning") or {}),
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
            try:
                await self._store_call(
                    (
                        "record_sandbox_lease",
                        "upsert_sandbox_lease",
                        "save_sandbox_lease",
                    ),
                    lease,
                    spec,
                    manifest,
                )
            except Exception as exc:
                if isinstance(exc, SandboxError):
                    raise
                raise SandboxAllocationError(
                    self._durable_allocation_failure_message(
                        exc,
                        lease=lease,
                        spec=spec,
                        manifest=manifest,
                        phase="store.record_sandbox_lease",
                    )
                ) from exc
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
                self._durable_allocation_failure_message(
                    exc,
                    lease=lease,
                    spec=spec,
                    manifest=manifest,
                    phase="store.allocate_sandbox_lease",
                )
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

    def _durable_allocation_failure_message(
        self,
        exc: BaseException,
        *,
        lease: SandboxLease,
        spec: SandboxSpec,
        manifest: Mapping[str, Any],
        phase: str,
    ) -> str:
        task_ids = ",".join(str(task_id) for task_id in spec.task_ids)
        return (
            "durable sandbox lease allocation failed: "
            f"phase={phase} "
            f"exception_type={type(exc).__name__} "
            f"exception_repr={exc!r} "
            f"feature_id={spec.feature_id} "
            f"group_idx={spec.group_idx} "
            f"attempt_no={spec.attempt_no} "
            f"mode={spec.mode} "
            f"sandbox_id={lease.sandbox_id} "
            f"sandbox_root={manifest.get('root')} "
            f"task_ids={task_ids} "
            f"idempotency_key={spec.idempotency_key}"
        )

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
            writable_root_specs=[
                SandboxWritableRootSpec.model_validate(item)
                for item in manifest.get("writable_root_specs") or []
                if isinstance(item, Mapping)
            ],
            readonly_roots=[str(item) for item in manifest.get("readonly_roots") or []],
            contract_ids=[int(item) for item in manifest.get("contract_ids") or []],
            write_guard_scope=str(
                manifest.get("write_guard_scope") or "contract"
            ),  # type: ignore[arg-type]
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

    def _capture_patch_git_section(
        self,
        repo_root: Path,
        base_commit: str,
    ) -> tuple[str, str | None, bytes, bytes, bytes, bytes]:
        """Synchronous git plumbing for patch capture: index digest, rev-parse,
        and the read-tree/add/diff sequence against a throwaway index. Every git
        command shells out via subprocess.run, which BLOCKS the calling thread,
        so the caller MUST run this off the event loop (asyncio.to_thread).
        Running it inline froze the entire asyncio loop and every watchdog while
        ``git add -A`` / ``git diff`` churned a large worktree — the same hang
        class as the inline clone, already fixed at allocate. Returns
        (before_index, head_commit, diff_bytes, name_status, raw_diff, numstat)."""
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
        return before_index, head_commit, diff_bytes, name_status, raw_diff, numstat

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
        # Run the git plumbing off the event loop. read-tree/add/diff shell out
        # via blocking subprocess.run; inline they froze the whole asyncio loop
        # (and every watchdog) while `git add -A`/`git diff` churned a large
        # worktree — the bridge "hang". to_thread keeps the loop responsive; the
        # per-command timeout still bounds a wedged git.
        (
            before_index,
            head_commit,
            diff_bytes,
            name_status,
            raw_diff,
            numstat,
        ) = await asyncio.to_thread(
            self._capture_patch_git_section, repo_root, base_commit
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
        after_index = await asyncio.to_thread(self._normal_index_digest, repo_root)
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

    def _mapped_writable_root_specs(
        self,
        specs: Sequence[SandboxWritableRootSpec | Mapping[str, Any]],
        *,
        repo_roots: Mapping[str, str],
        source_roots: Mapping[str, str],
        sandbox_root: Path,
    ) -> list[dict[str, Any]]:
        mapped_specs: list[dict[str, Any]] = []
        for raw_spec in specs:
            spec = (
                raw_spec
                if isinstance(raw_spec, SandboxWritableRootSpec)
                else SandboxWritableRootSpec.model_validate(raw_spec)
            )
            repo_id = str(spec.repo_id or "")
            if not repo_id and len(repo_roots) == 1:
                repo_id = next(iter(repo_roots))
            entry = f"{repo_id}:{spec.path}" if repo_id else spec.path
            runtime_roots = self._map_runtime_root(
                entry,
                repo_roots=repo_roots,
                source_roots=source_roots,
                sandbox_root=sandbox_root,
                allow_external=False,
            )
            for runtime_root in runtime_roots:
                mapped_specs.append({
                    "repo_id": repo_id,
                    "path": spec.path,
                    "match_kind": spec.match_kind,
                    "allow_create": bool(spec.allow_create),
                    "source": spec.source,
                    "runtime_root": runtime_root,
                })
        return mapped_specs

    def _materialize_create_parents(
        self,
        writable_root_specs: Sequence[Mapping[str, Any]],
        *,
        repo_roots: Mapping[str, str],
        sandbox_root: Path,
    ) -> list[dict[str, Any]]:
        materialized: list[dict[str, Any]] = []
        for spec in writable_root_specs:
            if not bool(spec.get("allow_create")):
                continue
            runtime_root = Path(str(spec.get("runtime_root") or "")).resolve(strict=False)
            repo_id = str(spec.get("repo_id") or "")
            repo_root_text = repo_roots.get(repo_id)
            if not repo_root_text and len(repo_roots) == 1:
                repo_root_text = next(iter(repo_roots.values()))
            if not repo_root_text:
                raise SandboxAllocationError(
                    f"create root is missing repo identity: {spec.get('path')}"
                )
            repo_root = Path(repo_root_text).resolve(strict=True)
            target = (
                runtime_root
                if str(spec.get("match_kind") or "file") == "directory"
                else runtime_root.parent
            )
            target = target.resolve(strict=False)
            if not _is_relative_to(target, repo_root):
                raise SandboxAllocationError(
                    f"create root escapes sandbox repo: {spec.get('path')}"
                )
            self._materialize_directory_chain(
                target,
                repo_root=repo_root,
                sandbox_root=sandbox_root,
            )
            materialized.append({
                "repo_id": repo_id,
                "path": str(spec.get("path") or ""),
                "match_kind": str(spec.get("match_kind") or "file"),
                "target": str(target),
            })
        return materialized

    def _materialize_directory_chain(
        self,
        target: Path,
        *,
        repo_root: Path,
        sandbox_root: Path,
    ) -> None:
        repo_resolved = repo_root.resolve(strict=True)
        sandbox_resolved = sandbox_root.resolve(strict=False)
        target_resolved = target.resolve(strict=False)
        if not _is_relative_to(target_resolved, repo_resolved):
            raise SandboxAllocationError(f"create parent escapes sandbox repo: {target}")
        if not _is_relative_to(repo_resolved, sandbox_resolved):
            raise SandboxAllocationError(f"repo root escapes sandbox: {repo_root}")
        relative = target_resolved.relative_to(repo_resolved)
        current = repo_resolved
        self._normalize_sandbox_directory_permissions(
            current,
            repo_root=repo_resolved,
            sandbox_root=sandbox_resolved,
        )
        for part in relative.parts:
            current = current / part
            try:
                st = current.lstat()
            except FileNotFoundError:
                current.mkdir()
                self._normalize_sandbox_directory_permissions(
                    current,
                    repo_root=repo_resolved,
                    sandbox_root=sandbox_resolved,
                )
                continue
            except OSError as exc:
                raise SandboxAllocationError(
                    f"create parent stat failed for {current}: {exc}"
                ) from exc
            if stat.S_ISLNK(st.st_mode):
                raise SandboxAllocationError(
                    f"create parent contains symlink component: {current}"
                )
            if not stat.S_ISDIR(st.st_mode):
                raise SandboxAllocationError(
                    f"create parent contains non-directory component: {current}"
                )
            self._normalize_sandbox_directory_permissions(
                current,
                repo_root=repo_resolved,
                sandbox_root=sandbox_resolved,
            )

    def _normalize_sandbox_directory_permissions(
        self,
        path: Path,
        *,
        repo_root: Path,
        sandbox_root: Path,
    ) -> None:
        try:
            st = path.lstat()
        except OSError as exc:
            raise SandboxAllocationError(
                f"sandbox create-parent permission normalization failed to stat {path}: {exc}"
            ) from exc
        if stat.S_ISLNK(st.st_mode):
            raise SandboxAllocationError(
                f"sandbox create-parent permission normalization encountered symlink: {path}"
            )
        if not stat.S_ISDIR(st.st_mode):
            raise SandboxAllocationError(
                f"sandbox create-parent permission normalization requires directory: {path}"
            )
        resolved = path.resolve(strict=False)
        if not _is_relative_to(resolved, repo_root) or not _is_relative_to(resolved, sandbox_root):
            raise SandboxAllocationError(
                f"sandbox create-parent permission normalization escapes sandbox: {path}"
            )
        group_name, shared_gid = _agent_shared_group()
        if shared_gid is not None and st.st_gid != shared_gid:
            try:
                os.chown(path, -1, shared_gid)
            except OSError as exc:
                raise SandboxAllocationError(
                    "sandbox create-parent permission normalization failed to chgrp "
                    f"{path} to {group_name}: {exc}"
                ) from exc
        desired_mode = stat.S_IMODE(st.st_mode) | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_ISGID
        try:
            os.chmod(path, desired_mode)
        except OSError as exc:
            raise SandboxAllocationError(
                "sandbox create-parent permission normalization failed to chmod "
                f"{path} to {oct(desired_mode)}: {exc}"
            ) from exc

    def _write_guard_roots_for_manifest(
        self,
        *,
        writable_roots: Sequence[str],
        writable_root_specs: Sequence[Mapping[str, Any]],
        diagnostic: bool,
    ) -> list[str]:
        if diagnostic:
            return _sorted_unique(str(Path(path).resolve(strict=False)) for path in writable_roots)
        if writable_root_specs:
            roots: list[str] = []
            for spec in writable_root_specs:
                runtime_root = Path(str(spec.get("runtime_root") or "")).resolve(strict=False)
                if str(spec.get("match_kind") or "file") == "directory":
                    roots.append(str(runtime_root))
                else:
                    roots.append(str(runtime_root.parent))
            return _sorted_unique(roots)
        roots = []
        for raw in writable_roots:
            path = Path(str(raw)).resolve(strict=False)
            roots.append(str(path if path.exists() and path.is_dir() else path.parent))
        return _sorted_unique(roots)

    def _runtime_workspace_authority_grants(
        self,
        spec: SandboxSpec,
        *,
        repo_roots: Mapping[str, str],
        writable_roots: Sequence[str],
        writable_root_specs: Sequence[Mapping[str, Any]],
        materialized_create_parents: Sequence[Mapping[str, Any]],
        write_guard_roots: Sequence[str],
        expires_at: str,
    ) -> list[RuntimeWorkspaceAuthorityGrant]:
        grant_type = _authority_grant_type_for_spec(spec)
        lane_id = (
            str(spec.authority_lane_id or "").strip()
            or f"{spec.mode}:g{spec.group_idx}:a{spec.attempt_no}:{','.join(spec.task_ids)}"
        )
        promotable = grant_type in {"product", "repair"}
        grants: list[RuntimeWorkspaceAuthorityGrant] = []
        for repo_id, repo_root_text in repo_roots.items():
            repo_root = Path(repo_root_text).resolve(strict=True)
            contract_roots = [
                str(Path(root).resolve(strict=False))
                for root in writable_roots
                if _is_relative_to(Path(root).resolve(strict=False), repo_root)
            ]
            spec_guard_roots = [
                str(Path(root).resolve(strict=False))
                for root in write_guard_roots
                if _is_relative_to(Path(root).resolve(strict=False), repo_root)
            ]
            create_parent_roots = [
                str(Path(str(item.get("target"))).resolve(strict=False))
                for item in materialized_create_parents
                if str(item.get("repo_id") or repo_id) == repo_id
            ]
            if grant_type == "diagnostic" and not contract_roots:
                contract_roots = [str(repo_root)]
            if grant_type == "diagnostic" and not spec_guard_roots:
                spec_guard_roots = [str(repo_root)]
            if not spec_guard_roots and contract_roots:
                spec_guard_roots = _sorted_unique(
                    str(
                        root
                        if Path(root).exists() and Path(root).is_dir()
                        else Path(root).parent
                    )
                    for root in contract_roots
                )
            grant = RuntimeWorkspaceAuthorityGrant(
                feature_id=spec.feature_id,
                group_idx=spec.group_idx,
                lane_id=lane_id,
                grant_type=grant_type,
                repo_id=str(repo_id),
                repo_root=str(repo_root),
                contract_roots=_sorted_unique(contract_roots),
                create_parent_roots=_sorted_unique(create_parent_roots),
                write_guard_roots=_sorted_unique(spec_guard_roots),
                promotable=promotable,
                contract_ids=sorted(int(item) for item in spec.contract_ids),
                expires_at=expires_at,
            )
            self._validate_authority_grant_paths(grant, repo_root=repo_root)
            grants.append(grant)
        if not grants:
            raise SandboxAllocationError("runtime workspace authority grant requires repo roots")
        return grants

    def _validate_authority_grant_paths(
        self,
        grant: RuntimeWorkspaceAuthorityGrant,
        *,
        repo_root: Path,
    ) -> None:
        for label, paths in (
            ("contract root", grant.contract_roots),
            ("create parent root", grant.create_parent_roots),
            ("write guard root", grant.write_guard_roots),
        ):
            for raw in paths:
                path = Path(raw).resolve(strict=False)
                if not _is_relative_to(path, repo_root):
                    raise SandboxAllocationError(
                        f"runtime workspace authority {label} escapes repo "
                        f"{grant.repo_id}: {raw}"
                    )

    def _require_authority_grants_for_fresh_dispatch(
        self,
        manifest: Mapping[str, Any],
    ) -> None:
        if manifest.get("authority_schema_version") != _AUTHORITY_GRANT_SCHEMA_VERSION:
            raise SandboxAllocationError(
                "sandbox manifest lacks runtime workspace authority grant metadata; "
                "fresh dispatch requires a new sandbox attempt"
            )
        grants = manifest.get("runtime_workspace_authority_grants")
        if not isinstance(grants, list) or not grants:
            raise SandboxAllocationError(
                "sandbox manifest has no runtime workspace authority grants; "
                "fresh dispatch requires a new sandbox attempt"
            )

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
            provisioning=dict(manifest.get("provisioning") or {}),
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

    def _normalize_sandbox_repo_permissions(
        self,
        repo_root: Path,
        *,
        sandbox_root: Path,
    ) -> dict[str, Any]:
        repo_resolved = repo_root.resolve(strict=True)
        sandbox_resolved = sandbox_root.resolve(strict=False)
        if not _is_relative_to(repo_resolved, sandbox_resolved):
            raise SandboxAllocationError(
                f"sandbox repo permission normalization escapes sandbox: {repo_root}"
            )

        group_name, shared_gid = _agent_shared_group()
        summary: dict[str, Any] = {
            "agent_shared_group": group_name,
            "agent_shared_gid": shared_gid,
            "paths_changed": 0,
            "paths_already_ok": 0,
            "directories_normalized": 0,
            "files_normalized": 0,
            "symlinks_skipped": 0,
            "unsupported_skipped": 0,
        }

        def _normalize_path(path: Path) -> None:
            try:
                st = path.lstat()
            except OSError as exc:
                raise SandboxAllocationError(
                    f"sandbox repo permission normalization failed to stat {path}: {exc}"
                ) from exc

            if stat.S_ISLNK(st.st_mode):
                summary["symlinks_skipped"] += 1
                return

            path_resolved = path.resolve(strict=False)
            if not _is_relative_to(path_resolved, repo_resolved):
                raise SandboxAllocationError(
                    f"sandbox repo permission normalization path escapes repo: {path}"
                )

            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISDIR(st.st_mode):
                desired_mode = (
                    mode
                    | stat.S_IRGRP
                    | stat.S_IWGRP
                    | stat.S_IXGRP
                    | stat.S_ISGID
                )
                normalized_counter = "directories_normalized"
            elif stat.S_ISREG(st.st_mode):
                desired_mode = mode | stat.S_IRGRP | stat.S_IWGRP
                normalized_counter = "files_normalized"
            else:
                summary["unsupported_skipped"] += 1
                return

            changed = False
            if shared_gid is not None and st.st_gid != shared_gid:
                try:
                    os.chown(path, -1, shared_gid)
                except OSError as exc:
                    raise SandboxAllocationError(
                        "sandbox repo permission normalization failed to chgrp "
                        f"{path} to {group_name}: {exc}"
                    ) from exc
                changed = True

            if mode != desired_mode:
                try:
                    os.chmod(path, desired_mode)
                except OSError as exc:
                    raise SandboxAllocationError(
                        "sandbox repo permission normalization failed to chmod "
                        f"{path} to {oct(desired_mode)}: {exc}"
                    ) from exc
                changed = True

            try:
                verified = path.lstat()
            except OSError as exc:
                raise SandboxAllocationError(
                    f"sandbox repo permission normalization failed to verify {path}: {exc}"
                ) from exc
            if stat.S_ISLNK(verified.st_mode):
                raise SandboxAllocationError(
                    f"sandbox repo permission normalization encountered symlink race: {path}"
                )
            verified_mode = stat.S_IMODE(verified.st_mode)
            if shared_gid is not None and verified.st_gid != shared_gid:
                raise SandboxAllocationError(
                    "sandbox repo permission normalization could not set group "
                    f"{group_name} on {path}"
                )
            if not (verified_mode & stat.S_IWGRP):
                raise SandboxAllocationError(
                    f"sandbox repo permission normalization left {path} without group write"
                )
            if stat.S_ISDIR(verified.st_mode) and not (
                verified_mode & stat.S_IXGRP and verified_mode & stat.S_ISGID
            ):
                raise SandboxAllocationError(
                    "sandbox repo permission normalization left directory without "
                    f"group execute/setgid: {path}"
                )

            if changed:
                summary["paths_changed"] += 1
                summary[normalized_counter] += 1
            else:
                summary["paths_already_ok"] += 1

        _normalize_path(repo_root)
        for current, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for dirname in dirnames:
                child = current_path / dirname
                try:
                    child_stat = child.lstat()
                except OSError as exc:
                    raise SandboxAllocationError(
                        "sandbox repo permission normalization failed to stat "
                        f"directory {child}: {exc}"
                    ) from exc
                if stat.S_ISLNK(child_stat.st_mode):
                    summary["symlinks_skipped"] += 1
                    continue
                kept_dirs.append(dirname)
                _normalize_path(child)
            dirnames[:] = kept_dirs
            for filename in filenames:
                _normalize_path(current_path / filename)

        return summary

    def _normalize_clone_permissions_post_provision(
        self,
        repo_root: Path,
        *,
        sandbox_root: Path,
        template_repo: Path | None,
    ) -> dict[str, Any]:
        """Dispatch post-provision permission handling for one sandbox repo.

        Clones provisioned from a template that was permission-normalized at
        build time (marker present, same group params, flag ON) get a cheap
        bounded spot-verify — clonefile(2) preserves ownership/modes, so the
        full lstat/chown/chmod tree walk is redundant.  EVERY other path
        (flag off, legacy provisioning, pre-marker template, marker unreadable,
        normalization params changed since the template was built) runs the
        full sweep byte-identically to today.
        """
        if template_repo is None or not _sandbox_template_perms_enabled():
            return self._normalize_sandbox_repo_permissions(
                repo_root,
                sandbox_root=sandbox_root,
            )
        marker_path = template_repo.parent / _TEMPLATE_PERMS_MARKER_NAME
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Template predates template-time normalization (or marker is
            # unreadable): the clone's permissions are unproven — full sweep.
            return self._normalize_sandbox_repo_permissions(
                repo_root,
                sandbox_root=sandbox_root,
            )
        group_name, shared_gid = _agent_shared_group()
        if (
            marker.get("schema_version") != _TEMPLATE_PERMS_SCHEMA_VERSION
            or marker.get("agent_shared_group") != group_name
            or marker.get("agent_shared_gid") != shared_gid
        ):
            logger.warning(
                "sandbox template permission marker %s does not match current "
                "normalization params (group=%s gid=%s); running the FULL "
                "permission sweep on %s",
                marker_path,
                group_name,
                shared_gid,
                repo_root,
            )
            summary = self._normalize_sandbox_repo_permissions(
                repo_root,
                sandbox_root=sandbox_root,
            )
            summary["spot_verify_fallback"] = "template marker params mismatch"
            return summary
        return self._spot_verify_sandbox_repo_permissions(
            repo_root,
            sandbox_root=sandbox_root,
            group_name=group_name,
            shared_gid=shared_gid,
        )

    def _spot_verify_sandbox_repo_permissions(
        self,
        repo_root: Path,
        *,
        sandbox_root: Path,
        group_name: str,
        shared_gid: int | None,
    ) -> dict[str, Any]:
        """Bounded spot-verify of a clone-from-normalized-template's perms.

        Stats the repo root, ``.git``/``.venv``/``node_modules`` (when
        present), a sorted sample of top-level dirs/files, and a shallow
        sample inside each sampled dir — O(dozens) of lstats instead of the
        full-tree walk.  ANY mismatch (wrong group, missing group write,
        directory missing group exec/setgid) or stat error logs a loud
        WARNING and falls back to the FULL normalization sweep; it never
        silently skips into wrong permissions.
        """
        repo_resolved = repo_root.resolve(strict=True)
        sandbox_resolved = sandbox_root.resolve(strict=False)
        if not _is_relative_to(repo_resolved, sandbox_resolved):
            raise SandboxAllocationError(
                f"sandbox repo permission spot-verify escapes sandbox: {repo_root}"
            )

        def _mismatch(path: Path) -> str | None:
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                return None
            mode = stat.S_IMODE(st.st_mode)
            if shared_gid is not None and st.st_gid != shared_gid:
                return (
                    f"{path} has gid {st.st_gid}, expected {group_name} "
                    f"({shared_gid})"
                )
            if stat.S_ISDIR(st.st_mode):
                wanted = stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_ISGID
                if (mode & wanted) != wanted:
                    return (
                        f"directory {path} mode {oct(mode)} is missing group "
                        "rwx/setgid"
                    )
            elif stat.S_ISREG(st.st_mode):
                wanted = stat.S_IRGRP | stat.S_IWGRP
                if (mode & wanted) != wanted:
                    return f"file {path} mode {oct(mode)} is missing group rw"
            return None

        def _sample_children(
            dir_path: Path, *, dir_limit: int, file_limit: int
        ) -> tuple[list[Path], list[Path]]:
            sampled_dirs: list[Path] = []
            sampled_files: list[Path] = []
            with os.scandir(dir_path) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if len(sampled_dirs) < dir_limit:
                            sampled_dirs.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        if len(sampled_files) < file_limit:
                            sampled_files.append(Path(entry.path))
                    if (
                        len(sampled_dirs) >= dir_limit
                        and len(sampled_files) >= file_limit
                    ):
                        break
            return sampled_dirs, sampled_files

        failure: str | None = None
        paths_verified = 0
        try:
            candidates: list[Path] = [repo_root]
            top_dirs, top_files = _sample_children(
                repo_root,
                dir_limit=_TEMPLATE_PERMS_SPOT_DIR_SAMPLES,
                file_limit=_TEMPLATE_PERMS_SPOT_FILE_SAMPLES,
            )
            # Always sample the dependency/.git subtrees: they dominate the
            # tree and are exactly where wrong perms would strand the agent.
            for special in (".git", ".venv", "node_modules"):
                special_path = repo_root / special
                if special_path.is_dir() and special_path not in top_dirs:
                    top_dirs.append(special_path)
            candidates.extend(top_dirs)
            candidates.extend(top_files)
            for top_dir in top_dirs:
                child_dirs, child_files = _sample_children(
                    top_dir,
                    dir_limit=_TEMPLATE_PERMS_SPOT_CHILD_SAMPLES,
                    file_limit=_TEMPLATE_PERMS_SPOT_CHILD_SAMPLES,
                )
                candidates.extend(child_dirs)
                candidates.extend(child_files)
            for candidate in candidates:
                failure = _mismatch(candidate)
                if failure is not None:
                    break
                paths_verified += 1
        except OSError as exc:
            failure = f"spot-verify stat failed: {exc}"

        if failure is None:
            return {
                "mode": "template_spot_verify",
                "agent_shared_group": group_name,
                "agent_shared_gid": shared_gid,
                "paths_verified": paths_verified,
            }
        logger.warning(
            "sandbox clone permission spot-verify FAILED for %s (%s); falling "
            "back to the FULL permission normalization sweep",
            repo_root,
            failure,
        )
        summary = self._normalize_sandbox_repo_permissions(
            repo_root,
            sandbox_root=sandbox_root,
        )
        summary["spot_verify_fallback"] = failure
        return summary

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
        suspects: list[Path] = []
        for current, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
            dirnames[:] = [name for name in dirnames if name != ".git"]
            for name in [*dirnames, *filenames]:
                path = Path(current) / name
                if not path.is_symlink():
                    continue
                target = path.resolve(strict=False)
                if not _is_relative_to(target, repo_resolved) or any(
                    _is_relative_to(target, blocked) for blocked in blocked_roots
                ):
                    suspects.append(path)
        if not suspects:
            return
        # Sandbox provisioning itself creates gitignored artifacts that
        # legitimately symlink outside the repo (per-service venvs:
        # .venv/bin/python -> the interpreter). A gitignored symlink can
        # never be part of a captured patch, so it is exempt; any
        # non-ignored escaping symlink (e.g. one an agent created in
        # tracked space) still fails loudly.
        ignored = self._gitignored_paths(repo_root, suspects)
        for path in suspects:
            if path in ignored:
                logger.warning(
                    "sandbox symlink escape exempted (gitignored provisioning "
                    "artifact): %s -> %s",
                    _repo_rel(repo_root, path),
                    path.resolve(strict=False),
                )
                continue
            target = path.resolve(strict=False)
            if not _is_relative_to(target, repo_resolved):
                raise SandboxIsolationError(
                    f"symlink escape in sandbox repo: {_repo_rel(repo_root, path)} -> {target}"
                )
            raise SandboxIsolationError(
                f"symlink resolves into blocked root: {_repo_rel(repo_root, path)}"
            )

    def _gitignored_paths(self, repo_root: Path, paths: Sequence[Path]) -> set[Path]:
        """Return the subset of *paths* that git ignores in *repo_root*.

        Conservative on failure: if git cannot answer (not a repo, git
        missing), NOTHING is exempt and the symlink-escape guard keeps its
        full strictness.
        """
        if not paths:
            return set()
        try:
            rels = [str(path.relative_to(repo_root)) for path in paths]
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "check-ignore", "--stdin", "-z"],
                input="\0".join(rels) + "\0",
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:
            logger.warning(
                "git check-ignore failed for symlink-escape exemption — "
                "keeping full guard strictness",
                exc_info=True,
            )
            return set()
        if proc.returncode not in (0, 1):  # 1 = no paths ignored
            return set()
        ignored_rels = {item for item in proc.stdout.split("\0") if item}
        return {repo_root / rel for rel in ignored_rels}

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

    def _profile_package_roots(self) -> list[tuple[str, str]] | None:
        """``(rel_path, manager)`` pairs from the profile, or ``None``.

        ``None`` selects the legacy single-root npm path (no profile, no/empty
        ``package_roots``, or a malformed non-list ``package_roots`` — the
        iriai-studio default, unchanged). A root is never dropped just because its
        manager is missing: if the index-aligned ``package_managers`` list is
        shorter, the unmatched roots get an empty manager that dispatches to a
        surfaced "unknown manager" failure (a path that doesn't exist in the repo
        is a separate case, handled in the caller).

        Tolerant of a duck-typed/AI-inferred profile: a non-list ``package_roots``
        falls back to legacy rather than raising (preserving the never-raise
        provisioning contract) or iterating a bare string character-by-character.
        """
        profile = self.project_profile
        if profile is None:
            return None
        roots_raw = getattr(profile, "package_roots", None)
        if not isinstance(roots_raw, (list, tuple)) or not roots_raw:
            return None
        managers_raw = getattr(profile, "package_managers", None)
        managers = (
            [str(m) for m in managers_raw]
            if isinstance(managers_raw, (list, tuple))
            else []
        )
        roots = [str(r) for r in roots_raw]
        return [
            (roots[i], managers[i] if i < len(managers) else "")
            for i in range(len(roots))
        ]

    # -- Sandbox template + APFS clonefile provisioning -----------------------

    def _template_lockfiles(self, source_root: Path) -> dict[str, str]:
        """``{relpath: sha256}`` of every package lockfile in the worktree.

        Scans the repo root plus every profile package root — exactly the set
        of directories :meth:`_provision_sandbox_dependencies` provisions — for
        ``pnpm-lock.yaml``/``package-lock.json``/``poetry.lock``/``uv.lock``
        and ``requirements*.txt``.  Content hashes (not mtimes) so a mid-run
        lockfile edit by an implementer task deterministically changes the
        template digest at the next allocation.
        """
        candidates: list[Path] = [source_root]
        for rel_path, _manager in self._profile_package_roots() or []:
            rel = "" if rel_path in {"", "."} else rel_path
            candidate = source_root if not rel else (source_root / rel)
            if candidate.is_dir() and candidate not in candidates:
                candidates.append(candidate)
        lockfiles: dict[str, str] = {}
        for directory in candidates:
            found: list[Path] = [
                directory / name
                for name in _TEMPLATE_LOCKFILE_NAMES
                if (directory / name).is_file()
            ]
            for pattern in _TEMPLATE_LOCKFILE_GLOBS:
                found.extend(
                    sorted(p for p in directory.glob(pattern) if p.is_file())
                )
            for path in found:
                rel_key = path.relative_to(source_root).as_posix()
                if rel_key in lockfiles:
                    continue
                lockfiles[rel_key] = hashlib.sha256(path.read_bytes()).hexdigest()
        return lockfiles

    def _template_digest(
        self, repo_id: str, source_root: Path, base_commit: str
    ) -> str:
        seed = {
            "schema": _TEMPLATE_SCHEMA_VERSION,
            "repo_id": repo_id,
            "base_commit": base_commit,
            "lockfiles": self._template_lockfiles(source_root),
        }
        return _stable_digest(seed)[:16]

    def _template_feature_dir(self, feature_slug: str) -> Path:
        # Same .iriai tree (and therefore the same APFS volume) as the
        # sandboxes — a hard requirement for clonefile(2).
        return (
            self.workspace_root
            / ".iriai"
            / "features"
            / feature_slug
            / _TEMPLATE_DIRNAME
        )

    def _template_is_complete(self, template_dir: Path) -> bool:
        return (
            (template_dir / _TEMPLATE_MANIFEST_NAME).is_file()
            and (template_dir / _TEMPLATE_REPO_DIRNAME / ".git").exists()
        )

    def _ensure_sandbox_template(
        self,
        *,
        feature_slug: str,
        repo_id: str,
        source_resolved: Path,
        base_commit: str,
    ) -> Path | None:
        """Return the provisioned template repo dir for the current digest.

        Builds it (single-flight: exactly one builder per digest, enforced by
        an atomic ``os.mkdir`` lockdir that works across threads AND processes
        — deliberately independent of the per-feature allocation lock, which a
        sibling change is narrowing) when absent.  Concurrent allocators wait
        for the in-flight build and reattach; on timeout/build failure they
        return ``None`` so the caller falls back to legacy provisioning.
        """
        digest = self._template_digest(repo_id, source_resolved, base_commit)
        template_dir = self._template_feature_dir(feature_slug) / digest
        template_repo = template_dir / _TEMPLATE_REPO_DIRNAME
        if self._template_is_complete(template_dir):
            return template_repo

        lock_dir = template_dir.with_name(template_dir.name + ".building")
        lock_dir.parent.mkdir(parents=True, exist_ok=True)
        stale_after_s = _sandbox_template_wait_s() * 2
        try:
            os.mkdir(lock_dir)
        except FileExistsError:
            try:
                lock_age = time.time() - os.stat(lock_dir).st_mtime
            except OSError:
                lock_age = 0.0
            if lock_age <= stale_after_s:
                return self._wait_for_template_build(template_dir, lock_dir)
            # A builder crashed mid-build: reclaim the (always-empty) lockdir
            # and retry the build exactly once; on a lost race, wait instead.
            logger.warning(
                "sandbox template lockdir %s is stale (%.0fs old); reclaiming",
                lock_dir,
                lock_age,
            )
            try:
                os.rmdir(lock_dir)
                os.mkdir(lock_dir)
            except OSError:
                return self._wait_for_template_build(template_dir, lock_dir)
        try:
            # Double-check after winning the lock: the previous holder may have
            # published the template between our exists-check and mkdir.
            if self._template_is_complete(template_dir):
                return template_repo
            return self._build_sandbox_template(
                template_dir=template_dir,
                digest=digest,
                repo_id=repo_id,
                source_resolved=source_resolved,
                base_commit=base_commit,
            )
        finally:
            try:
                os.rmdir(lock_dir)
            except OSError:  # pragma: no cover - already reclaimed/removed.
                pass

    def _wait_for_template_build(
        self, template_dir: Path, lock_dir: Path
    ) -> Path | None:
        """Wait for another builder's in-flight template build (or fall back)."""
        deadline = time.monotonic() + _sandbox_template_wait_s()
        while time.monotonic() < deadline:
            if self._template_is_complete(template_dir):
                return template_dir / _TEMPLATE_REPO_DIRNAME
            if not lock_dir.exists():
                # Builder finished: either the template was published or the
                # build failed (in which case we fall back to legacy).
                if self._template_is_complete(template_dir):
                    return template_dir / _TEMPLATE_REPO_DIRNAME
                logger.warning(
                    "concurrent sandbox template build for %s ended without a "
                    "usable template; falling back to legacy provisioning",
                    template_dir,
                )
                return None
            time.sleep(0.25)
        logger.warning(
            "timed out after %.0fs waiting for concurrent sandbox template "
            "build %s; falling back to legacy provisioning",
            _sandbox_template_wait_s(),
            template_dir,
        )
        return None

    def _build_sandbox_template(
        self,
        *,
        template_dir: Path,
        digest: str,
        repo_id: str,
        source_resolved: Path,
        base_commit: str,
    ) -> Path | None:
        """Build + atomically publish one template (caller holds the lockdir).

        Reuses the exact legacy provisioning code paths (``git clone`` via
        :func:`_git_clone_args`, ``checkout --detach``, then
        :meth:`_provision_sandbox_dependencies`) into a staging dir, then
        ``os.rename``s it into place (atomic on the shared APFS volume).  A
        template with any fatal (non-best-effort) provisioning failure is NOT
        cached — the task falls back to legacy provisioning, which surfaces
        the same failure onto the lease exactly as today.
        """
        staging = template_dir.with_name(template_dir.name + f".staging-{os.getpid()}")
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            staging_repo = staging / _TEMPLATE_REPO_DIRNAME
            self._git_text(staging, _git_clone_args(source_resolved, staging_repo))
            self._git_text(staging_repo, ["checkout", "--detach", base_commit])
            results = self._provision_sandbox_dependencies(
                staging_repo, source_resolved
            )
            fatal = [r.as_dict() for r in results if not r.ok and not r.best_effort]
            if fatal:
                logger.warning(
                    "sandbox template build for repo %s digest %s had fatal "
                    "provisioning failures (%s); not caching the template — "
                    "falling back to legacy provisioning",
                    repo_id,
                    digest,
                    fatal,
                )
                shutil.rmtree(staging, ignore_errors=True)
                return None
            if _sandbox_template_perms_enabled():
                # Normalize permissions ONCE here (clonefile preserves
                # ownership/modes, so every clone inherits them) and stamp the
                # provenance marker BEFORE the atomic publish rename.  A
                # normalization failure raises into the except below: the
                # template is not cached and the task falls back to legacy
                # provisioning, whose full per-clone sweep is unchanged.
                group_name, shared_gid = _agent_shared_group()
                perms_summary = self._normalize_sandbox_repo_permissions(
                    staging_repo,
                    sandbox_root=staging,
                )
                (staging / _TEMPLATE_PERMS_MARKER_NAME).write_text(
                    json.dumps(
                        {
                            "schema_version": _TEMPLATE_PERMS_SCHEMA_VERSION,
                            "digest": digest,
                            "repo_id": repo_id,
                            "agent_shared_group": group_name,
                            "agent_shared_gid": shared_gid,
                            "normalized_at": _isoformat(_utc_now(self._clock)),
                            "summary": perms_summary,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            template_manifest = {
                "schema_version": _TEMPLATE_SCHEMA_VERSION,
                "digest": digest,
                "repo_id": repo_id,
                "base_commit": base_commit,
                "source_root": str(source_resolved),
                "lockfiles": self._template_lockfiles(source_resolved),
                "provision_results": [r.as_dict() for r in results],
                "owner": self.owner,
                "built_at": _isoformat(_utc_now(self._clock)),
            }
            (staging / _TEMPLATE_MANIFEST_NAME).write_text(
                json.dumps(template_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if template_dir.exists():  # incomplete leftover from a crash
                shutil.rmtree(template_dir)
            os.rename(staging, template_dir)
        except Exception as exc:
            logger.warning(
                "sandbox template build FAILED for repo %s digest %s (%s); "
                "falling back to legacy provisioning",
                repo_id,
                digest,
                exc,
            )
            shutil.rmtree(staging, ignore_errors=True)
            return None
        self._prune_stale_templates(template_dir.parent, keep=template_dir)
        return template_dir / _TEMPLATE_REPO_DIRNAME

    def _prune_stale_templates(self, feature_template_dir: Path, *, keep: Path) -> None:
        """Best-effort removal of superseded template digests (disk hygiene).

        Only prunes published templates (and orphaned staging dirs) older than
        ``_TEMPLATE_PRUNE_GRACE_S`` — clonefile provisioning from a template
        completes in seconds, so anything an hour old cannot be a live source.
        Never raises.
        """
        try:
            now = time.time()
            for entry in feature_template_dir.iterdir():
                if entry == keep or not entry.is_dir():
                    continue
                if entry.name.endswith(".building"):
                    continue  # lockdirs are reclaimed by their own staleness path
                try:
                    age = now - entry.stat().st_mtime
                except OSError:
                    continue
                if age <= _TEMPLATE_PRUNE_GRACE_S:
                    continue
                logger.info("pruning stale sandbox template %s", entry)
                shutil.rmtree(entry, ignore_errors=True)
        except Exception as exc:  # pragma: no cover - hygiene must never fail.
            logger.warning(
                "stale sandbox template pruning under %s failed: %s",
                feature_template_dir,
                exc,
            )

    def _clonefile_tree(self, source: Path, dest: Path) -> None:
        """APFS clonefile copy of *source* dir to *dest* via ``cp -c -R``.

        ``cp -c`` uses clonefile(2) and FAILS (no silent degradation) when the
        target filesystem does not support cloning, so a non-APFS volume
        surfaces as a SandboxError here and the caller falls back to legacy
        provisioning.  Guards: macOS only, and source/dest must share a volume
        (clonefile cannot cross filesystems).
        """
        if sys.platform != "darwin":
            raise SandboxError(
                "clonefile provisioning requires macOS (cp -c); "
                f"platform is {sys.platform}"
            )
        if not _same_filesystem(source, dest.parent):
            raise SandboxError(
                f"template {source} and sandbox {dest} are on different "
                "filesystems; clonefile cannot cross volumes"
            )
        cp_args = ["cp", "-c", "-R"]
        if _sandbox_template_perms_enabled():
            # Template-time permission normalization relies on the clone
            # INHERITING the template's group + group-write bits; plain
            # `cp -R` masks created modes with the umask (022 strips g+w), so
            # preserve attributes explicitly.  Gated on the flag so
            # IRIAI_SANDBOX_TEMPLATE_PERMS=0 keeps today's clone byte-identical
            # (the full per-clone sweep re-normalizes either way).
            cp_args.append("-p")
        result = self._run_command(
            dest.parent, [*cp_args, str(source), str(dest)]
        )
        if result.returncode != 0:
            raise SandboxError(
                f"{' '.join(cp_args)} {source} -> {dest} failed: "
                f"{_command_failure_detail(result)}"
            )

    def _provision_repo_from_template(
        self,
        *,
        feature_slug: str,
        repo_id: str,
        source_resolved: Path,
        base_commit: str,
        repo_root: Path,
    ) -> Path | None:
        """Fast path: provision ``repo_root`` by clonefiling a feature template.

        Returns the template repo dir (truthy) when ``repo_root`` is a
        fully-provisioned, independent git worktree at ``base_commit`` — the
        caller uses it to locate the template's permission-normalization
        marker.  Returns None (falsy; after a loud WARNING and removing any
        partial ``repo_root``) on ANY failure so the caller runs the legacy
        full-provisioning path verbatim.
        """
        if not _sandbox_template_cow_enabled():
            return None
        try:
            template_repo = self._ensure_sandbox_template(
                feature_slug=feature_slug,
                repo_id=repo_id,
                source_resolved=source_resolved,
                base_commit=base_commit,
            )
            if template_repo is None:
                return None  # already warned at the failure site
            self._clonefile_tree(template_repo, repo_root)
            # The clone must be a valid INDEPENDENT git worktree (clonefile
            # copies .git wholesale): verify git works and pin the commit.
            self._git_text(repo_root, ["status", "--porcelain=v1"])
            self._git_text(repo_root, ["checkout", "--detach", base_commit])
            return template_repo
        except Exception as exc:
            logger.warning(
                "sandbox template clonefile provisioning FAILED for repo %s "
                "(feature %s): %s; falling back to legacy full provisioning",
                repo_id,
                feature_slug,
                exc,
            )
            shutil.rmtree(repo_root, ignore_errors=True)
            return None

    def _provision_sandbox_dependencies(
        self, repo_root: Path, source_root: Path
    ) -> list[ProvisionResult]:
        """Restore each package root's dependencies into a sandbox clone.

        The clone in :meth:`allocate` produces a working tree without gitignored
        dependency dirs (``node_modules``/``.venv``), so in-sandbox tooling
        (``tsc``/``tsgo``/Playwright/``pytest``/``mypy``) is missing and tasks
        fail-close. This restores them per package root described by
        ``self.project_profile``.

        When no profile (or no ``package_roots``) is present, falls back to the
        legacy single-root npm path verbatim (iriai-studio default, unchanged).

        Returns one :class:`ProvisionResult` per root. Legacy npm failures stay
        best-effort (a failure only reproduces the pre-existing no-tooling
        state); new-manager failures are logged at ERROR with the exact command
        and recorded onto the lease so the task surfaces a precise error instead
        of a buried warning (AC-K-4).
        """
        roots = self._profile_package_roots()
        if roots is None:
            return [self._provision_npm(repo_root, source_root)]

        results: list[ProvisionResult] = []
        for rel_path, manager in roots:
            rel = "" if rel_path in {"", "."} else rel_path
            dest = repo_root if not rel else (repo_root / rel)
            if not dest.is_dir():
                # The root belongs to a different repo (multi-repo feature) or is
                # misconfigured; skip rather than fail this repo's other roots.
                logger.debug(
                    "sandbox provisioning: skipping absent root %s (manager=%s) "
                    "under %s",
                    rel_path,
                    manager,
                    repo_root,
                )
                continue
            source = source_root if not rel else (source_root / rel)
            result = self._provision_root(dest, source, manager, rel_path or ".")
            if not result.ok and not result.best_effort:
                logger.error(
                    "sandbox dependency provisioning FAILED for root %s "
                    "(manager=%s): command %r: %s",
                    rel_path or ".",
                    manager,
                    result.command,
                    result.detail,
                )
            results.append(result)
        return results

    def _provision_root(
        self, dest: Path, source: Path, manager: str, rel_label: str
    ) -> ProvisionResult:
        mgr = (manager or "").strip().lower()
        if mgr == "npm":
            return self._provision_npm(dest, source, rel_label=rel_label)
        if mgr == "pnpm":
            return self._provision_pnpm(dest, rel_label=rel_label)
        if mgr == "pip":
            return self._provision_pip(dest, rel_label=rel_label)
        if mgr == "poetry":
            return self._provision_poetry(dest, rel_label=rel_label)
        return ProvisionResult(
            rel_path=rel_label,
            manager=manager,
            ok=False,
            detail=f"unknown package manager {manager!r} for root {rel_label}",
        )

    def _provision_npm(
        self, dest: Path, source: Path, *, rel_label: str = "."
    ) -> ProvisionResult:
        """APFS copy-on-write restore of ``node_modules`` (+ ``npm ci`` fallback).

        The canonical source repo already has dependencies installed; restore via
        an APFS copy-on-write clone (near-instant, no network) and only fall back
        to a slow ``npm ci`` when the source has nothing to copy. Best-effort: a
        failure only reproduces the pre-existing no-tooling state, so the legacy
        single-root npm path never surfaces as a task error.
        """
        cmd = ""
        try:
            dest_modules = dest / "node_modules"
            if dest_modules.exists():
                return ProvisionResult(rel_label, "npm", True, best_effort=True)

            source_modules = source / "node_modules"
            if source_modules.is_dir():
                cmd = f"cp -c -R {source_modules} {dest_modules}"
                result = self._run_command(
                    dest,
                    ["cp", "-c", "-R", str(source_modules), str(dest_modules)],
                )
                if result.returncode == 0:
                    return ProvisionResult(
                        rel_label, "npm", True, command=cmd, best_effort=True
                    )
                logger.warning(
                    "sandbox dependency clone failed (rc=%s) copying %s -> %s; "
                    "falling back",
                    result.returncode,
                    source_modules,
                    dest_modules,
                )

            if (dest / "package-lock.json").is_file():
                cmd = "npm ci --prefer-offline --no-audit"
                result = self._run_command(
                    dest,
                    ["npm", "ci", "--prefer-offline", "--no-audit"],
                )
                if result.returncode != 0:
                    logger.warning(
                        "sandbox dependency install failed (rc=%s) in %s",
                        result.returncode,
                        dest,
                    )
                    return ProvisionResult(
                        rel_label,
                        "npm",
                        False,
                        command=cmd,
                        detail=_command_failure_detail(result),
                        best_effort=True,
                    )
            return ProvisionResult(
                rel_label, "npm", True, command=cmd, best_effort=True
            )
        except Exception as exc:  # pragma: no cover - best-effort guard.
            logger.warning(
                "sandbox dependency provisioning errored for %s: %s",
                dest,
                exc,
            )
            return ProvisionResult(
                rel_label, "npm", False, command=cmd, detail=str(exc), best_effort=True
            )

    def _provision_pnpm(self, dest: Path, *, rel_label: str) -> ProvisionResult:
        """``pnpm install --frozen-lockfile`` in the package root.

        NOT a ``node_modules`` CoW copy: pnpm's ``node_modules`` is a symlink farm
        into a global content-addressed store outside the repo, so a copy yields
        dangling links. ``HOME``/``PNPM_HOME`` propagate via ``_run_command``'s
        ``merged_env`` (it starts from ``os.environ``), keeping the global store
        reachable. A failure surfaces (not best-effort) per AC-K-4.
        """
        cmd = "pnpm install --frozen-lockfile --prefer-offline"
        try:
            result = self._run_command(
                dest, ["pnpm", "install", "--frozen-lockfile", "--prefer-offline"]
            )
            if result.returncode == 0:
                return ProvisionResult(rel_label, "pnpm", True, command=cmd)
            return ProvisionResult(
                rel_label,
                "pnpm",
                False,
                command=cmd,
                detail=_command_failure_detail(result),
            )
        except Exception as exc:
            return ProvisionResult(
                rel_label, "pnpm", False, command=cmd, detail=str(exc)
            )

    def _provision_pip(self, dest: Path, *, rel_label: str) -> ProvisionResult:
        """``python -m venv .venv`` + install project deps + dev tools.

        Installs declared project dependencies (``requirements*.txt`` or an
        editable install of ``pyproject``/``setup.py``) plus the dev tools
        (pytest/mypy/black) so in-sandbox self-verify runs. A failure surfaces
        (not best-effort) per AC-K-4.
        """
        venv_dir = dest / ".venv"
        cmd = "python3 -m venv .venv"
        try:
            if not venv_dir.exists():
                result = self._run_command(dest, ["python3", "-m", "venv", ".venv"])
                if result.returncode != 0:
                    return ProvisionResult(
                        rel_label,
                        "pip",
                        False,
                        command=cmd,
                        detail=_command_failure_detail(result),
                    )
            pip = str(venv_dir / "bin" / "pip")
            # N-22: dev-requirements filename varies by repo convention —
            # kaya pins its test deps (pytest-asyncio, kaya_test, …) in
            # requirements_dev.txt (underscore); the hyphen-only list left
            # every sandbox venv unable to even COLLECT the service suites
            # (ModuleNotFoundError: pytest_asyncio at conftest import).
            for req in (
                "requirements.txt",
                "requirements-dev.txt",
                "requirements_dev.txt",
                "requirements-test.txt",
                "requirements_test.txt",
            ):
                if (dest / req).is_file():
                    cmd = f"{pip} install -r {req}"
                    result = self._run_command(dest, [pip, "install", "-r", req])
                    if result.returncode != 0:
                        return ProvisionResult(
                            rel_label,
                            "pip",
                            False,
                            command=cmd,
                            detail=_command_failure_detail(result),
                        )
            # Editable-install the project itself when it is a real installable
            # package — independent of requirements*.txt, which usually list only
            # third-party deps and would otherwise leave the project's own modules
            # unimportable for in-sandbox pytest/mypy.
            if _pip_is_installable_package(dest):
                cmd = f"{pip} install -e ."
                result = self._run_command(dest, [pip, "install", "-e", "."])
                if result.returncode != 0:
                    return ProvisionResult(
                        rel_label,
                        "pip",
                        False,
                        command=cmd,
                        detail=_command_failure_detail(result),
                    )
            cmd = f"{pip} install pytest mypy black"
            result = self._run_command(
                dest, [pip, "install", "pytest", "mypy", "black"]
            )
            if result.returncode != 0:
                return ProvisionResult(
                    rel_label,
                    "pip",
                    False,
                    command=cmd,
                    detail=_command_failure_detail(result),
                )
            return ProvisionResult(rel_label, "pip", True, command=cmd)
        except Exception as exc:
            return ProvisionResult(
                rel_label, "pip", False, command=cmd, detail=str(exc)
            )

    def _provision_poetry(self, dest: Path, *, rel_label: str) -> ProvisionResult:
        """``poetry install`` in the package root (surfaces failures, AC-K-4)."""
        cmd = "poetry install"
        try:
            result = self._run_command(dest, ["poetry", "install"])
            if result.returncode == 0:
                return ProvisionResult(rel_label, "poetry", True, command=cmd)
            return ProvisionResult(
                rel_label,
                "poetry",
                False,
                command=cmd,
                detail=_command_failure_detail(result),
            )
        except Exception as exc:
            return ProvisionResult(
                rel_label, "poetry", False, command=cmd, detail=str(exc)
            )

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
            try:
                completed = subprocess.run(
                    list(argv),
                    cwd=str(cwd),
                    env=merged_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=_sandbox_command_timeout_s(),
                )
            except subprocess.TimeoutExpired as exc:
                raise SandboxError(
                    f"command timed out after {_sandbox_command_timeout_s():.0f}s: "
                    f"{' '.join(map(str, argv))} in {cwd}"
                ) from exc
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
        "write_guard_roots": list(binding.write_guard_roots),
        "write_guard_scope": binding.write_guard_scope,
        "authority_schema_version": binding.authority_schema_version,
        "runtime_workspace_authority_grants": list(
            binding.runtime_workspace_authority_grants
        ),
        "runtime_workspace_authority_grant_digest": (
            binding.runtime_workspace_authority_grant_digest
        ),
        "promotable": bool(binding.promotable),
        "readonly_roots": list(binding.readonly_roots),
        "blocked_roots": list(binding.blocked_roots),
        "base_snapshot_ids": list(binding.role_metadata.get("base_snapshot_ids") or []),
        "base_snapshot_by_repo": dict(
            binding.role_metadata.get("base_snapshot_by_repo") or {}
        ),
        "provisioning": dict(binding.role_metadata.get("provisioning") or {}),
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


def _authority_grant_type_for_spec(
    spec: SandboxSpec,
) -> Literal["product", "repair", "diagnostic"]:
    if spec.authority_grant_type:
        return spec.authority_grant_type
    if spec.write_guard_scope == "diagnostic" or spec.mode == "diagnostic":
        return "diagnostic"
    if spec.mode in {"repair", "canonicalization"}:
        return "repair"
    return "product"


def _authority_grant_payload(
    grant: RuntimeWorkspaceAuthorityGrant | Mapping[str, Any],
) -> dict[str, Any]:
    payload = (
        grant.model_dump(mode="json")
        if isinstance(grant, RuntimeWorkspaceAuthorityGrant)
        else dict(grant)
    )
    payload.pop("grant_digest", None)
    payload["grant_digest"] = _stable_digest(payload)
    return payload


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
    "SandboxWritableRootSpec",
    "_exclude_sandbox_prompt_context_from_capture",
    "_is_terminal_sandbox_attempt_blocker",
    "_repair_repo_id_for_sandbox",
    "_sandbox_blocker",
    "_sandbox_manifest_for_binding",
    "_sandbox_prompt_context_dir",
]
