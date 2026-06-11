"""Canonical workspace authority for execution-control workspace decisions.

This module is intentionally fixture-friendly: it owns deterministic identity,
path, ACL, and snapshot helpers without requiring the workflow monolith,
persistence store, or runtime adapters. Public methods are async to match the
future integration boundary, but the core helpers are pure or bounded local
filesystem probes.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Sequence, Union
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field


PathKind = Literal["canonical", "alias", "outside_root", "unknown_repo"]
PathAction = Literal["read", "create", "modify", "delete", "stage"]
PathSource = Literal["task", "contract", "verifier", "repair", "commit", "merge"]
CaseSensitivity = Literal["case_sensitive", "case_insensitive", "unknown"]

REGISTRY_VERSION = "workspace-authority-v1"
AGENT_SHARED_GROUP_ENV = "IRIAI_AGENT_SHARED_GROUP"
DEFAULT_AGENT_SHARED_GROUP = "iriai-agents"
_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
_KNOWN_CASE_INSENSITIVE_GIT_HOSTS = {
    "bitbucket.org",
    "github.com",
    "gitlab.com",
}
_WRITABLE_ACTIONS = {"create", "modify", "delete", "stage"}


class _AuthorityModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class RepoIdentity(_AuthorityModel):
    repo_id: str = ""
    repo_name: str = ""
    role: str = "execution"
    workspace_relative_path: str = ""
    canonical_path: str = ""
    source_path: Optional[str] = None
    alias_paths: list[str] = Field(default_factory=list)
    remote_url: Optional[str] = None
    remote_fingerprint: Optional[str] = None
    branch: Optional[str] = None
    head_sha: Optional[str] = None
    git_common_dir: Optional[str] = None
    source_git_common_dir: Optional[str] = None
    identity_kind: str = ""
    identity_value: str = ""
    writable_task_ids: list[str] = Field(default_factory=list)
    read_only_task_ids: list[str] = Field(default_factory=list)
    safety_status: str = "ok"
    safety_reasons: list[str] = Field(default_factory=list)
    identity_evidence_digest: str = ""


class CanonicalRepoRegistry(_AuthorityModel):
    feature_id: str = ""
    feature_slug: str = ""
    feature_root: str = ""
    registry_version: str = REGISTRY_VERSION
    repos: list[RepoIdentity] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    collisions: list[dict[str, str]] = Field(default_factory=list)
    blocked: bool = False
    blockers: list[dict[str, str]] = Field(default_factory=list)
    registry_digest: str = ""


class CanonicalPathResolution(_AuthorityModel):
    original_path: str = ""
    canonical_path: str = ""
    repo_id: Optional[str] = None
    path_kind: PathKind = "unknown_repo"
    alias_path: Optional[str] = None
    alias_exists: bool = False
    canonical_exists: bool = False
    divergent: bool = False
    symlink_blocker: Optional[str] = None
    repair_route: str = "none"
    reasons: list[str] = Field(default_factory=list)


class PathTarget(_AuthorityModel):
    raw_path: str = ""
    action: PathAction = "read"
    task_id: Optional[str] = None
    contract_id: Optional[int] = None
    source: PathSource = "task"


class AclTarget(_AuthorityModel):
    repo_id: str = ""
    raw_path: str = ""
    canonical_path: str = ""
    action: str = ""
    nearest_existing_parent: Optional[str] = None
    repo_root: Optional[str] = None


class WorkspacePreflight(_AuthorityModel):
    approved: bool = False
    resolutions: list[CanonicalPathResolution] = Field(default_factory=list)
    acl_targets: list[AclTarget] = Field(default_factory=list)
    blockers: list[dict[str, str]] = Field(default_factory=list)
    repair_routes: list[str] = Field(default_factory=list)
    status_unavailable: list[dict[str, str]] = Field(default_factory=list)
    snapshot_required: bool = False
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: Optional[int] = None
    attempt_id: Optional[int] = None
    stage: str = ""
    feature_root: str = ""
    registry_digest: str = ""


class AclNormalizationResult(_AuthorityModel):
    approved: bool = False
    changed: list[dict[str, str]] = Field(default_factory=list)
    already_ok: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)
    denied_targets: list[AclTarget] = Field(default_factory=list)
    repair_route: Optional[str] = None


class WorkspaceSnapshot(_AuthorityModel):
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: Optional[int] = None
    attempt_id: Optional[int] = None
    stage: str = ""
    repo_id: str = ""
    role: str = ""
    canonical_path: str = ""
    workspace_relative_path: str = ""
    source_path: Optional[str] = None
    remote_url: Optional[str] = None
    remote_fingerprint: Optional[str] = None
    branch: Optional[str] = None
    head_sha: Optional[str] = None
    git_common_dir: Optional[str] = None
    source_git_common_dir: Optional[str] = None
    case_sensitivity: CaseSensitivity = "unknown"
    index_digest: str = _EMPTY_DIGEST
    worktree_status_digest: str = _EMPTY_DIGEST
    dirty_paths: list[str] = Field(default_factory=list)
    staged_paths: list[str] = Field(default_factory=list)
    untracked_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    symlink_paths: list[str] = Field(default_factory=list)
    outside_root_targets: list[str] = Field(default_factory=list)
    agent_writable_paths: list[str] = Field(default_factory=list)
    alias_paths: list[str] = Field(default_factory=list)
    registry_artifact_id: Optional[int] = None
    acl_artifact_id: Optional[int] = None
    compatibility_projection_artifact_ids: list[int] = Field(default_factory=list)
    no_dirty: bool = True
    status_unavailable: Optional[dict[str, str]] = None
    validated_at: str = ""
    captured_at: str = ""
    warnings: list[str] = Field(default_factory=list)
    safety_status: str = "ok"
    idempotency_key: str = ""


class FailureObservation(_AuthorityModel):
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: Optional[int] = None
    task_id: Optional[str] = None
    attempt_id: Optional[int] = None
    source: str = "workspace_authority"
    failure_class: str = ""
    failure_type: str = ""
    severity: str = "error"
    deterministic: bool = True
    retryable: bool = True
    operator_required: bool = False
    evidence_ids: list[int] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    route: str = ""
    signature_hash: str = ""
    idempotency_key: str = ""


@dataclass
class _GitResult:
    returncode: int
    stdout: str
    stderr: str = ""


GitRunner = Callable[[Path, Sequence[str]], Union[_GitResult, str]]


@dataclass
class _CandidateEvidence:
    root: Path
    repo_name: str
    role: str = "execution"
    action: str = ""
    registry_repo_id: str = ""
    registry_backed: bool = False
    declared_canonical_path: Path | None = None
    source_path: str | None = None
    remote_url: str | None = None
    remote_fingerprint: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    git_common_dir: str | None = None
    source_git_common_dir: str | None = None
    writable_task_ids: set[str] = field(default_factory=set)
    read_only_task_ids: set[str] = field(default_factory=set)
    safety_reasons: list[str] = field(default_factory=list)


def stable_json(value: Any) -> str:
    """Return deterministic compact JSON for hashes and tests."""

    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def normalize_remote_fingerprint(remote_url: str | None) -> str | None:
    """Normalize HTTPS/SSH Git remote URLs to one host/path fingerprint.

    Credentials and transport are stripped so common HTTPS and SSH forms of the
    same GitHub/GitLab/Bitbucket repository compare equal.
    """

    raw = str(remote_url or "").strip()
    if not raw:
        return None
    raw = raw.split("#", 1)[0].split("?", 1)[0].strip()

    scp_match = re.match(r"^(?:(?P<user>[^@/\s]+)@)?(?P<host>[^:/\s]+):(?P<path>.+)$", raw)
    if scp_match and "://" not in raw:
        host = scp_match.group("host")
        path = scp_match.group("path")
    else:
        candidate = raw
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlsplit(candidate)
        host = parsed.hostname or ""
        path = parsed.path or ""
        if parsed.scheme == "file":
            return _strip_trailing_git_suffix(unquote(path.strip("/"))) or None

    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    path = unquote(path).strip().strip("/")
    if path.startswith("~"):
        path = path[1:].lstrip("/")
    path = _strip_trailing_git_suffix(path)
    if not host or not path:
        return None
    if host in _KNOWN_CASE_INSENSITIVE_GIT_HOSTS:
        path = path.lower()
    return f"{host}/{path}"


def repo_id_for_identity(identity_kind: str, identity_value: str) -> str:
    material = f"repo-identity-v1\0{identity_kind}\0{identity_value}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def path_agent_writable(
    path: str | Path,
    *,
    repo_path: str | Path,
    shared_gid: int | None = None,
) -> bool:
    """Return whether a path is writable by the configured agent population.

    Inside feature workspaces, owner-write alone is not proof because the bridge
    user may own files that the runtime agent cannot mutate. Group-write is
    only proof when a configured shared gid matches; otherwise this falls back
    to other-write or trusted owner-write outside feature workspaces.
    """

    target = Path(path)
    repo = Path(repo_path)
    try:
        st = target.stat()
    except OSError:
        return False
    mode = st.st_mode
    if mode & stat.S_IWOTH:
        return True
    if mode & stat.S_IWGRP and shared_gid is not None and st.st_gid == shared_gid:
        return True
    if st.st_uid == os.getuid() and mode & stat.S_IWUSR and _owner_write_is_trustworthy(repo):
        return True
    return False


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _strip_trailing_git_suffix(path: str) -> str:
    return path[:-4] if path.lower().endswith(".git") else path


def _strip_line_suffix(value: str | Path | None) -> str:
    text = str(value or "").strip().strip("`'\"").replace("\\", "/")
    if ":" in text:
        prefix, suffix = text.rsplit(":", 1)
        if suffix.isdigit() and prefix and not re.match(r"^[A-Za-z]$", prefix):
            text = prefix
    while text.startswith("./"):
        text = text[2:]
    return text.strip()


def _has_traversal(value: str) -> bool:
    return any(part == ".." for part in Path(value).parts)


def _path_key(path: Path) -> str:
    return path.resolve(strict=False).as_posix()


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False
    except OSError:
        return False


def _contained_lexically(path: Path, root: Path) -> bool:
    try:
        path.expanduser().absolute().relative_to(root.expanduser().absolute())
        return True
    except ValueError:
        return False


def _relative_to(path: Path, root: Path) -> str:
    try:
        rel = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except Exception:
        return path.name
    text = rel.as_posix()
    return "." if text == "." else text


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _sorted_unique(items: Iterable[str]) -> list[str]:
    return sorted({item for item in items if item})


def _record_get(record: Any, name: str, default: Any = "") -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _task_id(task: Any) -> str:
    return str(_record_get(task, "id", "") or "").strip()


def _task_repo_path(task: Any) -> str:
    return str(_record_get(task, "repo_path", "") or "").strip()


def _task_write_kind(task: Any) -> str:
    scopes = list(_record_get(task, "file_scope", []) or [])
    if scopes:
        return "read_only" if all(str(_record_get(scope, "action", "")) == "read_only" for scope in scopes) else "writable"
    files = list(_record_get(task, "files", []) or [])
    return "writable" if files else "read_only"


def _task_file_paths(task: Any) -> list[str]:
    paths: list[str] = []
    for scope in list(_record_get(task, "file_scope", []) or []):
        path = str(_record_get(scope, "path", "") or "").strip()
        if path:
            paths.append(path)
    for path in list(_record_get(task, "files", []) or []):
        text = str(path or "").strip()
        if text:
            paths.append(text)
    return _dedupe(paths)


def _owner_write_is_trustworthy(repo_path: Path) -> bool:
    try:
        parts = repo_path.resolve(strict=False).parts
    except OSError:
        parts = repo_path.parts
    return ".iriai" not in parts or "features" not in parts


def _agent_shared_gid(group_name: str | None = None) -> int | None:
    name = (group_name if group_name is not None else os.environ.get(
        AGENT_SHARED_GROUP_ENV,
        DEFAULT_AGENT_SHARED_GROUP,
    )).strip()
    if not name:
        return None
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WorkspaceAuthority:
    """Facade for canonical registry, path, ACL, routing, and snapshot helpers."""

    def __init__(
        self,
        *,
        feature_root: str | Path | None = None,
        feature_slug: str | None = None,
        workspace_root: str | Path | None = None,
        registry_repos: Sequence[Any] | None = None,
        legacy_registry: Any | None = None,
        directory_map: Any | None = None,
        shared_group: str | None = None,
        shared_gid: int | None = None,
        git_runner: GitRunner | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.feature_root = Path(feature_root).expanduser() if feature_root is not None else None
        self.feature_slug = feature_slug or ""
        self.workspace_root = Path(workspace_root).expanduser() if workspace_root is not None else None
        self.registry_repos = list(registry_repos or [])
        self.legacy_registry = legacy_registry
        self.directory_map = directory_map
        self.shared_group = shared_group
        self.shared_gid = shared_gid
        self.git_runner = git_runner
        self.now = now or _utc_now

    async def build_registry(
        self=None,
        feature_id: str | Sequence[Any] = "",
        tasks: Sequence[Any] | None = None,
        *,
        feature_root: str | Path | None = None,
        feature_slug: str | None = None,
        workspace_root: str | Path | None = None,
        registry_repos: Sequence[Any] | None = None,
        legacy_registry: Any | None = None,
        directory_map: Any | None = None,
    ) -> CanonicalRepoRegistry:
        authority, real_feature_id, real_tasks = WorkspaceAuthority._parse_build_args(
            self,
            feature_id,
            tasks,
            feature_root=feature_root,
            feature_slug=feature_slug,
            workspace_root=workspace_root,
            registry_repos=registry_repos,
            legacy_registry=legacy_registry,
            directory_map=directory_map,
        )
        return authority._build_registry_impl(real_feature_id, list(real_tasks or []))

    async def resolve_path(
        self=None,
        path: str | Path | CanonicalRepoRegistry = "",
        registry: CanonicalRepoRegistry | None = None,
    ) -> CanonicalPathResolution:
        authority, real_path, real_registry = WorkspaceAuthority._parse_resolve_args(
            self,
            path,
            registry,
        )
        return authority._resolve_path_impl(str(real_path), real_registry)

    async def preflight_targets(
        self=None,
        targets: Sequence[PathTarget | dict[str, Any]] | CanonicalRepoRegistry | None = None,
        registry: CanonicalRepoRegistry | None = None,
        *,
        feature_id: str = "",
        dag_sha256: str = "",
        group_idx: int | None = None,
        attempt_id: int | None = None,
        stage: str = "",
    ) -> WorkspacePreflight:
        authority, real_targets, real_registry = WorkspaceAuthority._parse_preflight_args(
            self,
            targets,
            registry,
        )
        return authority._preflight_targets_impl(
            list(real_targets or []),
            real_registry,
            feature_id=feature_id,
            dag_sha256=dag_sha256,
            group_idx=group_idx,
            attempt_id=attempt_id,
            stage=stage,
        )

    async def normalize_acl(
        self=None,
        report: WorkspacePreflight | None = None,
    ) -> AclNormalizationResult:
        authority, real_report = WorkspaceAuthority._parse_report_args(self, report)
        return authority._normalize_acl_impl(real_report)

    async def route_preflight(
        self=None,
        report: WorkspacePreflight | None = None,
    ) -> list[FailureObservation]:
        authority, real_report = WorkspaceAuthority._parse_report_args(self, report)
        return authority._route_preflight_impl(real_report)

    async def snapshot(
        self=None,
        feature_id: str = "",
        dag_sha256: str = "",
        group_idx: int | None = None,
        stage: str = "",
        attempt_id: int | None = None,
        registry: CanonicalRepoRegistry | None = None,
        targets: Sequence[PathTarget | dict[str, Any]] | None = None,
        task_ids: Sequence[str] | None = None,
    ) -> list[WorkspaceSnapshot]:
        authority, args = WorkspaceAuthority._parse_snapshot_args(
            self,
            feature_id,
            dag_sha256,
            group_idx,
            stage,
            attempt_id,
            registry,
            targets,
            task_ids,
        )
        return authority._snapshot_impl(**args)

    def registry_digest(
        self=None,
        registry: Optional[CanonicalRepoRegistry] = None,
    ) -> str:
        real_registry = registry if isinstance(self, WorkspaceAuthority) else (
            self if isinstance(self, CanonicalRepoRegistry) else registry
        )
        if not isinstance(real_registry, CanonicalRepoRegistry):
            raise ValueError("registry is required")
        return _registry_digest(real_registry)

    def normalize_remote_fingerprint(
        self=None,
        remote_url: Optional[str] = None,
    ) -> Optional[str]:
        value = remote_url if isinstance(self, WorkspaceAuthority) else (
            self if self is not None else remote_url
        )
        return normalize_remote_fingerprint(value)

    def path_agent_writable(
        self=None,
        path: Union[str, Path, None] = None,
        *,
        repo_path: Union[str, Path],
        shared_gid: Optional[int] = None,
    ) -> bool:
        if isinstance(self, WorkspaceAuthority):
            if path is None:
                raise ValueError("path is required")
            return path_agent_writable(path, repo_path=repo_path, shared_gid=self._shared_gid())
        real_path = self if self is not None and path is None else path
        if real_path is None:
            raise ValueError("path is required")
        return path_agent_writable(real_path, repo_path=repo_path, shared_gid=shared_gid)

    @staticmethod
    def _parse_build_args(
        maybe_self: Any,
        feature_id: str | Sequence[Any],
        tasks: Sequence[Any] | None,
        **kwargs: Any,
    ) -> tuple["WorkspaceAuthority", str, Sequence[Any] | None]:
        if isinstance(maybe_self, WorkspaceAuthority):
            return maybe_self, str(feature_id or ""), tasks
        authority = WorkspaceAuthority(**{key: val for key, val in kwargs.items() if val is not None})
        if maybe_self is not None:
            return authority, str(maybe_self or ""), feature_id if tasks is None else tasks  # type: ignore[return-value]
        return authority, str(feature_id or ""), tasks

    @staticmethod
    def _parse_resolve_args(
        maybe_self: Any,
        path: str | Path | CanonicalRepoRegistry,
        registry: CanonicalRepoRegistry | None,
    ) -> tuple["WorkspaceAuthority", str | Path, CanonicalRepoRegistry]:
        if isinstance(maybe_self, WorkspaceAuthority):
            if registry is None:
                raise ValueError("registry is required")
            return maybe_self, path, registry
        authority = WorkspaceAuthority()
        if registry is None and isinstance(path, CanonicalRepoRegistry):
            return authority, maybe_self, path
        if registry is None:
            raise ValueError("registry is required")
        return authority, path, registry

    @staticmethod
    def _parse_preflight_args(
        maybe_self: Any,
        targets: Sequence[PathTarget | dict[str, Any]] | CanonicalRepoRegistry | None,
        registry: CanonicalRepoRegistry | None,
    ) -> tuple["WorkspaceAuthority", Sequence[PathTarget | dict[str, Any]] | None, CanonicalRepoRegistry]:
        if isinstance(maybe_self, WorkspaceAuthority):
            if registry is None:
                raise ValueError("registry is required")
            return maybe_self, targets if not isinstance(targets, CanonicalRepoRegistry) else [], registry
        authority = WorkspaceAuthority()
        if registry is None and isinstance(targets, CanonicalRepoRegistry):
            return authority, maybe_self, targets  # type: ignore[return-value]
        if registry is None:
            raise ValueError("registry is required")
        return authority, targets, registry

    @staticmethod
    def _parse_report_args(
        maybe_self: Any,
        report: WorkspacePreflight | None,
    ) -> tuple["WorkspaceAuthority", WorkspacePreflight]:
        if isinstance(maybe_self, WorkspaceAuthority):
            if report is None:
                raise ValueError("report is required")
            return maybe_self, report
        authority = WorkspaceAuthority()
        real_report = maybe_self if isinstance(maybe_self, WorkspacePreflight) else report
        if real_report is None:
            raise ValueError("report is required")
        return authority, real_report

    @staticmethod
    def _parse_snapshot_args(
        maybe_self: Any,
        feature_id: str,
        dag_sha256: str,
        group_idx: int | None,
        stage: str,
        attempt_id: int | None,
        registry: CanonicalRepoRegistry | None,
        targets: Sequence[PathTarget | dict[str, Any]] | None,
        task_ids: Sequence[str] | None,
    ) -> tuple["WorkspaceAuthority", dict[str, Any]]:
        if isinstance(maybe_self, WorkspaceAuthority):
            authority = maybe_self
            args = {
                "feature_id": feature_id,
                "dag_sha256": dag_sha256,
                "group_idx": group_idx,
                "stage": stage,
                "attempt_id": attempt_id,
                "registry": registry,
                "targets": list(targets or []),
                "task_ids": list(task_ids or []) if task_ids is not None else None,
            }
            if args["registry"] is None:
                raise ValueError("registry is required")
            return authority, args

        authority = WorkspaceAuthority()
        if isinstance(maybe_self, str) and isinstance(attempt_id, CanonicalRepoRegistry):
            args = {
                "feature_id": maybe_self,
                "dag_sha256": feature_id,
                "group_idx": dag_sha256,  # type: ignore[dict-item]
                "stage": group_idx,
                "attempt_id": stage,
                "registry": attempt_id,
                "targets": list(registry or []),  # type: ignore[arg-type]
                "task_ids": list(targets or []) if task_ids is None and targets else list(task_ids or []),
            }
            return authority, args
        if registry is None:
            raise ValueError("registry is required")
        return authority, {
            "feature_id": feature_id,
            "dag_sha256": dag_sha256,
            "group_idx": group_idx,
            "stage": stage,
            "attempt_id": attempt_id,
            "registry": registry,
            "targets": list(targets or []),
            "task_ids": list(task_ids or []) if task_ids is not None else None,
        }

    def _build_registry_impl(
        self,
        feature_id: str,
        tasks: Sequence[Any],
    ) -> CanonicalRepoRegistry:
        repo_parent, root_blockers = self._guarded_feature_repo_parent(tasks)
        feature_slug = self.feature_slug or _feature_slug_from_root(repo_parent) or feature_id
        blockers: list[dict[str, str]] = list(root_blockers)
        candidates: dict[str, _CandidateEvidence] = {}
        repos: list[RepoIdentity] = []
        aliases: dict[str, str] = {}
        collisions: list[dict[str, str]] = []
        if not blockers:
            candidates = self._discover_candidates(repo_parent, tasks, blockers)
            repos, aliases, collisions, identity_blockers = self._candidates_to_repos(
                feature_id,
                repo_parent,
                candidates,
            )
            blockers.extend(identity_blockers)
        blocked = bool(blockers or collisions)
        registry = CanonicalRepoRegistry(
            feature_id=feature_id,
            feature_slug=feature_slug,
            feature_root=(
                repo_parent.absolute().as_posix()
                if root_blockers
                else repo_parent.resolve(strict=False).as_posix()
            ),
            registry_version=REGISTRY_VERSION,
            repos=repos,
            aliases=aliases,
            collisions=collisions,
            blocked=blocked,
            blockers=sorted(blockers, key=lambda item: stable_json(item)),
            registry_digest="",
        )
        registry.repos = sorted(
            registry.repos,
            key=lambda repo: (repo.repo_id, repo.canonical_path),
        )
        registry.aliases = _sorted_aliases(registry.aliases)
        registry.registry_digest = _registry_digest(registry)
        return registry

    def _guarded_feature_repo_parent(
        self,
        tasks: Sequence[Any],
    ) -> tuple[Path, list[dict[str, str]]]:
        if self.feature_root is not None:
            return self._guarded_as_repo_parent(self.feature_root)
        env_root = os.environ.get("IRIAI_FEATURE_ROOT") or os.environ.get("IRIAI_WORKSPACE_FEATURE_ROOT")
        if env_root:
            return self._guarded_as_repo_parent(Path(env_root).expanduser())
        repo_parent = self._feature_repo_parent(tasks)
        return repo_parent, self._root_symlink_blockers(repo_parent, root_kind="repos_root")

    def _feature_repo_parent(self, tasks: Sequence[Any]) -> Path:
        if self.feature_root is not None:
            return _as_repo_parent(self.feature_root)
        env_root = os.environ.get("IRIAI_FEATURE_ROOT") or os.environ.get("IRIAI_WORKSPACE_FEATURE_ROOT")
        if env_root:
            return _as_repo_parent(Path(env_root).expanduser())
        absolute_task_roots = [
            Path(path).expanduser()
            for task in tasks
            for path in [_strip_line_suffix(_task_repo_path(task))]
            if path and Path(path).expanduser().is_absolute()
        ]
        if absolute_task_roots:
            parents = {path.parent.resolve(strict=False) for path in absolute_task_roots}
            if len(parents) == 1:
                return next(iter(parents))
            return Path(os.path.commonpath([path.as_posix() for path in absolute_task_roots])).resolve(strict=False)
        return Path.cwd().resolve(strict=False)

    def _guarded_as_repo_parent(self, feature_root: Path) -> tuple[Path, list[dict[str, str]]]:
        raw_root = feature_root.expanduser()
        raw_kind = "repos_root" if raw_root.name == "repos" else "feature_root"
        blockers = self._root_symlink_blockers(raw_root, root_kind=raw_kind)
        if blockers:
            return _blocked_repo_parent_for(raw_root), blockers

        repos_root = raw_root / "repos"
        if raw_root.name != "repos" and not (raw_root / ".git").exists() and _lexical_path_exists(repos_root):
            blockers = self._root_symlink_blockers(repos_root, root_kind="repos_root")
            if blockers:
                return repos_root.absolute(), blockers

        return _as_repo_parent(raw_root), []

    def _root_symlink_blockers(self, root: Path, *, root_kind: str) -> list[dict[str, str]]:
        symlink = _lexical_symlink_component(root, anchor=self._workspace_root_hint())
        if symlink is None:
            return []
        reason = f"{root_kind}_symlink"
        return [{
            "failure_class": "operator_required",
            "failure_type": "operator_clearance_required",
            "reason": reason,
            "path": root.expanduser().absolute().as_posix(),
            "root_kind": root_kind,
            "symlink_path": symlink.absolute().as_posix(),
            "symlink_target": _readlink_target(symlink),
            "route": "operator_required",
        }]

    def _workspace_root_hint(self) -> Path | None:
        if self.workspace_root is not None:
            return self.workspace_root
        env_root = os.environ.get("IRIAI_WORKSPACE_ROOT")
        if env_root:
            return Path(env_root).expanduser()
        test_root = globals().get("_TEST_WORKSPACE_ROOT")
        if test_root:
            return Path(test_root).expanduser()
        return None

    def _discover_candidates(
        self,
        repo_parent: Path,
        tasks: Sequence[Any],
        blockers: list[dict[str, str]],
    ) -> dict[str, _CandidateEvidence]:
        candidates: dict[str, _CandidateEvidence] = {}

        def add_candidate(
            path: Path,
            *,
            registry_row: Any | None = None,
            task: Any | None = None,
            declared_canonical: Path | None = None,
        ) -> None:
            root = path.expanduser()
            root_key = _path_key(root)
            if _has_symlink_component(repo_parent, root):
                blockers.append({
                    "failure_class": "operator_required",
                    "failure_type": "operator_clearance_required",
                    "reason": "symlink_repo_root",
                    "path": root.as_posix(),
                    "route": "operator_required",
                })
                return
            if not _contained(root, repo_parent):
                blockers.append({
                    "failure_class": "operator_required",
                    "failure_type": "operator_clearance_required",
                    "reason": "repo_root_outside_feature_root",
                    "path": root.as_posix(),
                    "route": "operator_required",
                })
                return
            if not _git_metadata_inside_repo_parent(repo_parent, root):
                blockers.append({
                    "failure_class": "operator_required",
                    "failure_type": "operator_clearance_required",
                    "reason": "repo_git_metadata_outside_feature_root",
                    "path": root.as_posix(),
                    "route": "operator_required",
                })
                return
            if not _is_valid_repo_root(repo_parent, root):
                blockers.append({
                    "failure_class": "operator_required",
                    "failure_type": "operator_clearance_required",
                    "reason": "repo_root_nested_in_existing_repo",
                    "path": root.as_posix(),
                    "route": "operator_required",
                })
                return

            candidate = candidates.get(root_key)
            if candidate is None:
                candidate = self._candidate_from_root(root, repo_parent)
                candidates[root_key] = candidate
            if registry_row is not None:
                self._merge_registry_evidence(candidate, registry_row, repo_parent, declared_canonical)
            if task is not None:
                self._merge_task_evidence(candidate, task)

        for root in self._direct_repo_roots(repo_parent):
            add_candidate(root)

        for row in self._legacy_rows():
            row_repo = self._candidate_path_from_value(_record_get(row, "repo_path", ""), repo_parent)
            row_canonical = self._candidate_path_from_value(_record_get(row, "canonical_path", ""), repo_parent)
            destination = self._candidate_path_from_value(_record_get(row, "destination_path", ""), repo_parent)
            declared = row_canonical if row_canonical is not None else None
            for path in _dedupe_paths([row_canonical, row_repo, destination]):
                if path is not None:
                    add_candidate(path, registry_row=row, declared_canonical=declared)

        for task in tasks:
            task_repo = self._candidate_path_from_value(_task_repo_path(task), repo_parent)
            if task_repo is not None:
                if task_repo.exists() or (task_repo / ".git").exists():
                    add_candidate(task_repo, task=task)
                else:
                    blockers.append({
                        "failure_class": "operator_required",
                        "failure_type": "operator_clearance_required",
                        "reason": "task_repo_path_missing",
                        "path": task_repo.as_posix(),
                        "task_id": _task_id(task),
                        "route": "operator_required",
                    })
                continue
            for path_text in _task_file_paths(task):
                inferred = self._candidate_root_from_file_path(path_text, repo_parent)
                if inferred is not None:
                    add_candidate(inferred, task=task)
                    break

        return candidates

    def _direct_repo_roots(self, repo_parent: Path) -> list[Path]:
        if (repo_parent / ".git").exists():
            return [repo_parent]
        if not repo_parent.exists():
            return []
        try:
            children = sorted(repo_parent.iterdir(), key=lambda path: path.name)
        except OSError:
            return []
        return [
            child
            for child in children
            if child.is_dir() and not child.is_symlink() and (child / ".git").exists()
        ]

    def _legacy_rows(self) -> list[Any]:
        rows: list[Any] = []
        if self.registry_repos:
            rows.extend(self.registry_repos)
        if self.legacy_registry is not None:
            rows.extend(list(_record_get(self.legacy_registry, "repos", []) or []))
        return rows

    def _candidate_path_from_value(self, value: Any, repo_parent: Path) -> Path | None:
        text = _strip_line_suffix(value)
        if not text:
            return None
        if _has_traversal(text):
            return None
        raw_path = Path(text).expanduser()
        if raw_path.is_absolute():
            return raw_path.resolve(strict=False)
        rel = Path(text.strip("/"))
        options = [repo_parent / rel]
        if rel.parts and rel.parts[0] == "repos" and repo_parent.name == "repos":
            options.append(repo_parent.parent / rel)
        if len(rel.parts) > 1 and (repo_parent / rel.parts[0] / ".git").exists():
            options.insert(0, repo_parent / rel.parts[0])
        for option in options:
            if option.exists() or (option / ".git").exists():
                return option.resolve(strict=False)
        return options[0].resolve(strict=False)

    def _candidate_root_from_file_path(self, value: str, repo_parent: Path) -> Path | None:
        text = _strip_line_suffix(value)
        if not text or _has_traversal(text):
            return None
        raw = Path(text).expanduser()
        if raw.is_absolute():
            if not _contained(raw, repo_parent):
                return None
            current = raw if raw.is_dir() else raw.parent
            while _contained(current, repo_parent):
                if (current / ".git").exists() and _is_valid_repo_root(repo_parent, current):
                    return current.resolve(strict=False)
                if current == current.parent:
                    return None
                current = current.parent
            return None
        # N-18 fix 4: monorepo shape — .git lives at repo_parent itself, not
        # inside a named child directory.  A relative file path like
        # "supply-chain/tests/foo.py" has first-segment "supply-chain", but
        # repo_parent/"supply-chain"/.git does not exist.  When the registry
        # has exactly one repo with workspace_relative_path "." (indicating the
        # entire repos root IS the repo), repo_parent is the candidate root.
        # Mirror the same check _direct_repo_roots already performs so that
        # file-path-derived candidates are consistent with directly-rooted ones.
        if (repo_parent / ".git").exists():
            return repo_parent.resolve(strict=False)
        parts = Path(text.strip("/")).parts
        if not parts:
            return None
        candidate = repo_parent / parts[0]
        if (candidate / ".git").exists():
            return candidate.resolve(strict=False)
        return None

    def _candidate_from_root(self, root: Path, repo_parent: Path) -> _CandidateEvidence:
        repo_name = root.name if root != repo_parent else repo_parent.name
        remote_url = self._git_value(root, ["config", "--get", "remote.origin.url"]) or None
        branch = self._git_value(root, ["branch", "--show-current"]) or None
        head_sha = self._git_value(root, ["rev-parse", "HEAD"]) or None
        git_common_dir = self._git_common_dir(root)
        return _CandidateEvidence(
            root=root.resolve(strict=False),
            repo_name=repo_name,
            remote_url=remote_url,
            remote_fingerprint=normalize_remote_fingerprint(remote_url),
            branch=branch,
            head_sha=head_sha,
            git_common_dir=git_common_dir,
            source_git_common_dir=git_common_dir,
        )

    def _merge_registry_evidence(
        self,
        candidate: _CandidateEvidence,
        row: Any,
        repo_parent: Path,
        declared_canonical: Path | None,
    ) -> None:
        candidate.registry_backed = True
        candidate.registry_repo_id = str(_record_get(row, "repo_id", candidate.registry_repo_id) or candidate.registry_repo_id)
        candidate.role = str(_record_get(row, "role", candidate.role) or candidate.role)
        candidate.action = str(_record_get(row, "action", candidate.action) or candidate.action)
        if declared_canonical is not None and _contained(declared_canonical, repo_parent):
            candidate.declared_canonical_path = declared_canonical.resolve(strict=False)

        source_path = _strip_line_suffix(_record_get(row, "source_path", "") or "")
        if source_path:
            path = Path(source_path).expanduser()
            candidate.source_path = path.resolve(strict=False).as_posix()
            source_git = self._git_common_dir(path)
            if source_git:
                candidate.source_git_common_dir = source_git
            else:
                candidate.source_git_common_dir = None

        row_source_git = _strip_line_suffix(_record_get(row, "source_git_common_dir", "") or "")
        if row_source_git:
            candidate.source_git_common_dir = Path(row_source_git).expanduser().resolve(strict=False).as_posix()
        row_git = _strip_line_suffix(_record_get(row, "git_common_dir", "") or "")
        if row_git:
            candidate.git_common_dir = Path(row_git).expanduser().resolve(strict=False).as_posix()
            if not candidate.source_git_common_dir:
                candidate.source_git_common_dir = candidate.git_common_dir

        remote_url = str(_record_get(row, "remote_url", "") or "").strip()
        if remote_url:
            candidate.remote_url = remote_url
            candidate.remote_fingerprint = normalize_remote_fingerprint(remote_url)
        branch = str(_record_get(row, "branch", "") or "").strip()
        head_sha = str(_record_get(row, "head_sha", "") or "").strip()
        if branch:
            candidate.branch = branch
        if head_sha:
            candidate.head_sha = head_sha
        for task_id in list(_record_get(row, "writable_task_ids", []) or []):
            if task_id:
                candidate.writable_task_ids.add(str(task_id))
        for task_id in list(_record_get(row, "read_only_task_ids", []) or []):
            if task_id:
                candidate.read_only_task_ids.add(str(task_id))
        for task_id in list(_record_get(row, "task_ids", []) or []):
            if task_id:
                if candidate.action == "read_only":
                    candidate.read_only_task_ids.add(str(task_id))
                else:
                    candidate.writable_task_ids.add(str(task_id))

    def _merge_task_evidence(self, candidate: _CandidateEvidence, task: Any) -> None:
        task_id = _task_id(task)
        if not task_id:
            return
        if _task_write_kind(task) == "read_only":
            candidate.read_only_task_ids.add(task_id)
        else:
            candidate.writable_task_ids.add(task_id)

    def _candidates_to_repos(
        self,
        feature_id: str,
        repo_parent: Path,
        candidates: dict[str, _CandidateEvidence],
    ) -> tuple[list[RepoIdentity], dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
        prepared: list[tuple[_CandidateEvidence, str, str, str]] = []
        for candidate in candidates.values():
            identity_kind, identity_value = _identity_for_candidate(feature_id, repo_parent, candidate)
            repo_id = repo_id_for_identity(identity_kind, identity_value)
            prepared.append((candidate, identity_kind, identity_value, repo_id))

        grouped: dict[str, list[tuple[_CandidateEvidence, str, str]]] = {}
        for candidate, kind, value, repo_id in prepared:
            grouped.setdefault(repo_id, []).append((candidate, kind, value))

        repos: list[RepoIdentity] = []
        aliases: dict[str, str] = {}
        collisions: list[dict[str, str]] = []
        blockers: list[dict[str, str]] = []

        for repo_id in sorted(grouped):
            group = grouped[repo_id]
            canonical = _select_canonical_candidate(group, repo_parent)
            alias_candidates: list[_CandidateEvidence] = []
            group_blockers: list[str] = []
            for candidate, _kind, _value in group:
                if _path_key(candidate.root) == _path_key(canonical.root):
                    continue
                match_reasons = _strong_identity_match_reasons(canonical, candidate)
                if match_reasons:
                    alias_candidates.append(candidate)
                    continue
                reason = "repo_identity_collision_without_source_evidence"
                group_blockers.append(reason)
                collisions.append({
                    "repo_id": repo_id,
                    "canonical_path": canonical.root.as_posix(),
                    "other_path": candidate.root.as_posix(),
                    "reason": reason,
                    "canonical_evidence": _candidate_evidence_digest(canonical),
                    "other_evidence": _candidate_evidence_digest(candidate),
                })

            identity_kind, identity_value = _identity_for_candidate(feature_id, repo_parent, canonical)
            safety_reasons = list(canonical.safety_reasons)
            safety_reasons.extend(group_blockers)
            repo = RepoIdentity(
                repo_id=repo_id,
                repo_name=canonical.repo_name,
                role=canonical.role or "execution",
                workspace_relative_path=_relative_to(canonical.root, repo_parent),
                canonical_path=canonical.root.resolve(strict=False).as_posix(),
                source_path=canonical.source_path,
                alias_paths=sorted(_path_key(candidate.root) for candidate in alias_candidates),
                remote_url=canonical.remote_url,
                remote_fingerprint=canonical.remote_fingerprint,
                branch=canonical.branch,
                head_sha=canonical.head_sha,
                git_common_dir=canonical.git_common_dir,
                source_git_common_dir=canonical.source_git_common_dir,
                identity_kind=identity_kind,
                identity_value=identity_value,
                writable_task_ids=sorted(canonical.writable_task_ids),
                read_only_task_ids=sorted(canonical.read_only_task_ids),
                safety_status="blocked" if safety_reasons else "ok",
                safety_reasons=sorted(set(safety_reasons)),
                identity_evidence_digest=_candidate_evidence_digest(canonical),
            )
            repos.append(repo)
            for alias in repo.alias_paths:
                aliases[alias] = repo.canonical_path
            if safety_reasons:
                blockers.append({
                    "failure_class": "operator_required",
                    "failure_type": "operator_clearance_required",
                    "reason": "repo_identity_collision",
                    "repo_id": repo_id,
                    "route": "operator_required",
                })

        repos = sorted(repos, key=lambda repo: (repo.repo_id, repo.canonical_path))
        return repos, _sorted_aliases(aliases), sorted(collisions, key=lambda item: stable_json(item)), blockers

    def _resolve_path_impl(
        self,
        path: str,
        registry: CanonicalRepoRegistry,
    ) -> CanonicalPathResolution:
        original = str(path or "")
        text = _strip_line_suffix(original)
        reasons: list[str] = []
        feature_root_raw = Path(registry.feature_root).expanduser()
        if not text:
            return CanonicalPathResolution(
                original_path=original,
                canonical_path="",
                path_kind="unknown_repo",
                repair_route="operator_required",
                reasons=["empty_path"],
            )
        root_blockers = self._root_symlink_blockers(feature_root_raw, root_kind="repos_root")
        if root_blockers:
            root_blocker = root_blockers[0]
            raw_path = Path(text).expanduser()
            lexical_absolute = raw_path if raw_path.is_absolute() else feature_root_raw / text.strip("/")
            return CanonicalPathResolution(
                original_path=original,
                canonical_path=lexical_absolute.absolute().as_posix(),
                path_kind="outside_root",
                symlink_blocker=root_blocker.get("symlink_path"),
                repair_route="operator_required",
                reasons=[root_blocker.get("reason", "repos_root_symlink")],
            )
        feature_root = feature_root_raw.resolve(strict=False)
        if _has_traversal(text):
            candidate = (Path(text).expanduser() if Path(text).expanduser().is_absolute() else feature_root / text)
            return CanonicalPathResolution(
                original_path=original,
                canonical_path=candidate.resolve(strict=False).as_posix(),
                path_kind="outside_root",
                repair_route="operator_required",
                reasons=["path_traversal"],
            )

        raw_path = Path(text).expanduser()
        lexical_absolute = raw_path if raw_path.is_absolute() else feature_root / text.strip("/")
        lexical_absolute = lexical_absolute.expanduser()
        if not _contained_lexically(lexical_absolute, feature_root):
            return CanonicalPathResolution(
                original_path=original,
                canonical_path=lexical_absolute.resolve(strict=False).as_posix(),
                path_kind="outside_root",
                repair_route="operator_required",
                reasons=["outside_feature_root"],
            )
        symlink_blocker = _has_symlink_component(feature_root, lexical_absolute)
        absolute = lexical_absolute if symlink_blocker else lexical_absolute.resolve(strict=False)
        if not symlink_blocker and not _contained(absolute, feature_root):
            return CanonicalPathResolution(
                original_path=original,
                canonical_path=absolute.as_posix(),
                path_kind="outside_root",
                repair_route="operator_required",
                reasons=["outside_feature_root"],
            )

        alias_edges = _alias_edges(registry)
        alias_match = _longest_prefix_match(
            absolute,
            alias_edges.keys(),
            follow_symlinks=not bool(symlink_blocker),
        )
        if alias_match is not None:
            alias_root = Path(alias_match) if symlink_blocker else Path(alias_match).resolve(strict=False)
            canonical_root = Path(alias_edges[alias_match]).resolve(strict=False)
            suffix = absolute.relative_to(alias_root)
            canonical = canonical_root / suffix
            if not symlink_blocker:
                canonical = canonical.resolve(strict=False)
            repo = _repo_for_path(canonical, registry, follow_symlinks=not bool(symlink_blocker))
            symlink_blocker = symlink_blocker or _has_symlink_component(feature_root, canonical)
            if symlink_blocker:
                reasons.append("symlink_blocker")
            alias_exists = absolute.exists()
            canonical_exists = canonical.exists()
            divergent = _paths_diverge(absolute, canonical)
            repair_route = "none"
            if symlink_blocker:
                repair_route = "operator_required"
            elif divergent or (alias_exists and not canonical_exists):
                repair_route = "run_canonicalization_repair"
                reasons.append("alias_canonical_divergent" if divergent else "alias_only_canonical_missing")
            elif canonical_exists or alias_exists:
                repair_route = "retry_verifier"
                reasons.append("verifier_context_stale")
            else:
                repair_route = "retry_verifier"
                reasons.append("stale_alias_metadata")
            return CanonicalPathResolution(
                original_path=original,
                canonical_path=canonical.as_posix(),
                repo_id=repo.repo_id if repo is not None else None,
                path_kind="alias",
                alias_path=alias_root.as_posix(),
                alias_exists=alias_exists,
                canonical_exists=canonical_exists,
                divergent=divergent,
                symlink_blocker=symlink_blocker,
                repair_route=repair_route,
                reasons=_dedupe(reasons),
            )

        repo = _repo_for_path(absolute, registry, follow_symlinks=not bool(symlink_blocker))
        if repo is not None:
            reasons = ["symlink_blocker"] if symlink_blocker else []
            return CanonicalPathResolution(
                original_path=original,
                canonical_path=absolute.as_posix(),
                repo_id=repo.repo_id,
                path_kind="canonical",
                alias_path=None,
                alias_exists=False,
                canonical_exists=absolute.exists(),
                divergent=False,
                symlink_blocker=symlink_blocker,
                repair_route="operator_required" if symlink_blocker else "none",
                reasons=reasons,
            )

        return CanonicalPathResolution(
            original_path=original,
            canonical_path=absolute.as_posix(),
            path_kind="unknown_repo",
            canonical_exists=absolute.exists(),
            repair_route="operator_required",
            reasons=[
                "ambiguous_relative_path"
                if _ambiguous_relative_path(text, registry)
                else "unknown_repo"
            ],
        )

    def _preflight_targets_impl(
        self,
        targets: Sequence[PathTarget | dict[str, Any]],
        registry: CanonicalRepoRegistry,
        *,
        feature_id: str = "",
        dag_sha256: str = "",
        group_idx: int | None = None,
        attempt_id: int | None = None,
        stage: str = "",
    ) -> WorkspacePreflight:
        resolutions: list[CanonicalPathResolution] = []
        acl_targets: list[AclTarget] = []
        blockers: list[dict[str, str]] = []
        repair_routes: list[str] = []

        if registry.blocked:
            for blocker in registry.blockers:
                blockers.append({key: str(value) for key, value in blocker.items()})
                route = str(blocker.get("route") or "operator_required")
                repair_routes.append(route)

        for raw_target in targets:
            target = raw_target if isinstance(raw_target, PathTarget) else PathTarget.model_validate(raw_target)
            resolution = self._resolve_path_impl(target.raw_path, registry)
            resolutions.append(resolution)
            route = resolution.repair_route
            target_allows_missing_alias_create = (
                target.action == "create"
                and resolution.path_kind == "alias"
                and not resolution.alias_exists
                and not resolution.canonical_exists
                and not resolution.symlink_blocker
            )
            if resolution.symlink_blocker:
                blockers.append(_blocker_from_resolution(
                    resolution,
                    target,
                    failure_class="operator_required",
                    failure_type="operator_clearance_required",
                    route="operator_required",
                    reason="symlink_blocker",
                ))
                repair_routes.append("operator_required")
            elif resolution.path_kind == "outside_root":
                blockers.append(_blocker_from_resolution(
                    resolution,
                    target,
                    failure_class="operator_required",
                    failure_type="operator_clearance_required",
                    route="operator_required",
                    reason="outside_root",
                ))
                repair_routes.append("operator_required")
            elif resolution.path_kind == "unknown_repo":
                reason = (
                    "ambiguous_relative_path"
                    if "ambiguous_relative_path" in resolution.reasons
                    else "unknown_repo"
                )
                blockers.append(_blocker_from_resolution(
                    resolution,
                    target,
                    failure_class="operator_required",
                    failure_type="operator_clearance_required",
                    route="operator_required",
                    reason=reason,
                ))
                repair_routes.append("operator_required")
            elif resolution.path_kind == "alias" and route != "none" and not target_allows_missing_alias_create:
                failure_type = "alias_canonical_divergent" if resolution.divergent else (
                    "alias_only_canonical_missing"
                    if resolution.alias_exists and not resolution.canonical_exists
                    else "verifier_context_stale"
                )
                failure_class = "worktree_alias" if failure_type.startswith("alias_") else "stale_projection"
                blockers.append(_blocker_from_resolution(
                    resolution,
                    target,
                    failure_class=failure_class,
                    failure_type=failure_type,
                    route=route,
                    reason=failure_type,
                ))
                repair_routes.append(route)

            if target.action in _WRITABLE_ACTIONS and resolution.repo_id and resolution.path_kind in {"canonical", "alias"}:
                repo = _repo_by_id(registry, resolution.repo_id)
                repo_root = repo.canonical_path if repo is not None else ""
                nearest = _nearest_existing_parent(Path(resolution.canonical_path), Path(repo_root)) if repo_root else None
                acl_target = AclTarget(
                    repo_id=resolution.repo_id,
                    raw_path=target.raw_path,
                    canonical_path=resolution.canonical_path,
                    action=target.action,
                    nearest_existing_parent=nearest.as_posix() if nearest is not None else None,
                    repo_root=repo_root or None,
                )
                acl_targets.append(acl_target)
                acl_problem = self._acl_preflight_problem(acl_target)
                if acl_problem is not None:
                    blockers.append(acl_problem)
                    repair_routes.append(acl_problem.get("route", "run_workspace_repair"))

        status_unavailable: list[dict[str, str]] = []
        for repo in self._preflight_status_repos(registry, resolutions, acl_targets):
            repo_path = Path(repo.canonical_path)
            if self.git_runner is None and not _repo_has_git_marker(repo_path, repo):
                continue
            _status_text, status_evidence = self._git_status(repo_path, repo_id=repo.repo_id)
            if status_evidence is None:
                continue
            blocker = _status_unavailable_blocker(repo, status_evidence)
            status_unavailable.append(status_evidence)
            blockers.append(blocker)
            repair_routes.append(blocker["route"])

        repair_routes = _dedupe(route for route in repair_routes if route and route != "none")
        approved = not blockers
        snapshot_required = bool(acl_targets or resolutions)
        return WorkspacePreflight(
            approved=approved,
            resolutions=resolutions,
            acl_targets=acl_targets,
            blockers=sorted(blockers, key=lambda item: stable_json(item)),
            repair_routes=repair_routes,
            status_unavailable=sorted(status_unavailable, key=lambda item: stable_json(item)),
            snapshot_required=snapshot_required,
            feature_id=feature_id or registry.feature_id,
            dag_sha256=dag_sha256,
            group_idx=group_idx,
            attempt_id=attempt_id,
            stage=stage,
            feature_root=registry.feature_root,
            registry_digest=registry.registry_digest,
        )

    def _acl_preflight_problem(self, target: AclTarget) -> dict[str, str] | None:
        repo = Path(target.repo_root or "")
        canonical = Path(target.canonical_path)
        if not target.repo_root or not _contained(canonical, repo):
            return {
                "failure_class": "operator_required",
                "failure_type": "operator_clearance_required",
                "reason": "acl_target_outside_repo",
                "path": target.canonical_path,
                "repo_id": target.repo_id,
                "route": "operator_required",
            }
        symlink = _has_symlink_component(repo, canonical)
        if symlink:
            return {
                "failure_class": "operator_required",
                "failure_type": "operator_clearance_required",
                "reason": "symlink_blocker",
                "path": target.canonical_path,
                "repo_id": target.repo_id,
                "route": "operator_required",
            }
        closure = _acl_closure(target)
        shared_gid = self._shared_gid()
        denied = [
            path
            for path in closure
            if path.exists() and not path_agent_writable(path, repo_path=repo, shared_gid=shared_gid)
        ]
        if denied:
            return {
                "failure_class": "acl_workability",
                "failure_type": "unwritable_runtime_path",
                "reason": "unwritable_runtime_path",
                "path": denied[0].as_posix(),
                "target": target.canonical_path,
                "repo_id": target.repo_id,
                "route": "run_workspace_repair",
            }
        if target.action == "create" and target.nearest_existing_parent:
            parent = Path(target.nearest_existing_parent)
            if parent.exists() and not path_agent_writable(parent, repo_path=repo, shared_gid=shared_gid):
                return {
                    "failure_class": "acl_workability",
                    "failure_type": "unwritable_runtime_path",
                    "reason": "unwritable_create_parent",
                    "path": parent.as_posix(),
                    "target": target.canonical_path,
                    "repo_id": target.repo_id,
                    "route": "run_workspace_repair",
                }
        return None

    def _normalize_acl_impl(self, report: WorkspacePreflight) -> AclNormalizationResult:
        changed: list[dict[str, str]] = []
        already_ok: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        denied_targets: list[AclTarget] = []
        unresolved_denied = False
        shared_gid = self._shared_gid()
        root_failures = self._acl_root_failures(report)
        if root_failures:
            return AclNormalizationResult(
                approved=False,
                changed=changed,
                already_ok=already_ok,
                warnings=warnings,
                failed=root_failures,
                denied_targets=list(report.acl_targets),
                repair_route="operator_required",
            )
        initially_denied = {
            str(blocker.get("target") or blocker.get("path") or "")
            for blocker in report.blockers
            if blocker.get("failure_class") == "acl_workability"
        }

        for target in report.acl_targets:
            repo = Path(target.repo_root or _find_git_root(Path(target.canonical_path)) or "")
            canonical = Path(target.canonical_path)
            if not repo or not _contained(canonical, repo):
                failed.append({
                    "path": target.canonical_path,
                    "repo_id": target.repo_id,
                    "reason": "acl_target_outside_repo",
                })
                _append_acl_target_once(denied_targets, target)
                unresolved_denied = True
                continue
            symlink = _has_symlink_component(repo, canonical)
            if symlink:
                failed.append({
                    "path": target.canonical_path,
                    "repo_id": target.repo_id,
                    "reason": "symlink_blocker",
                    "symlink_blocker": symlink,
                })
                _append_acl_target_once(denied_targets, target)
                unresolved_denied = True
                continue
            if target.canonical_path in initially_denied:
                _append_acl_target_once(denied_targets, target)
            for path in _acl_closure(target):
                self._normalize_acl_path(
                    path,
                    repo=repo,
                    target=target,
                    shared_gid=shared_gid,
                    changed=changed,
                    already_ok=already_ok,
                    warnings=warnings,
                    failed=failed,
                )
            if self._acl_preflight_problem(target) is not None:
                _append_acl_target_once(denied_targets, target)
                unresolved_denied = True

        approved = not failed and not unresolved_denied
        return AclNormalizationResult(
            approved=approved,
            changed=changed,
            already_ok=already_ok,
            warnings=warnings,
            failed=failed,
            denied_targets=denied_targets,
            repair_route=None if approved else "run_workspace_repair",
        )

    def _acl_root_failures(self, report: WorkspacePreflight) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []
        if self.feature_root is not None:
            _repo_parent, feature_blockers = self._guarded_as_repo_parent(self.feature_root)
            blockers.extend(feature_blockers)
        if report.feature_root:
            _repo_parent, report_blockers = self._guarded_as_repo_parent(Path(report.feature_root))
            blockers.extend(report_blockers)

        failures: list[dict[str, str]] = []
        seen: set[str] = set()
        for blocker in blockers:
            key = stable_json(blocker)
            if key in seen:
                continue
            seen.add(key)
            failures.append({
                "path": blocker.get("path", ""),
                "repo_id": "",
                "reason": blocker.get("reason", "repos_root_symlink"),
                "failure_class": blocker.get("failure_class", "operator_required"),
                "failure_type": blocker.get("failure_type", "operator_clearance_required"),
                "root_kind": blocker.get("root_kind", ""),
                "symlink_blocker": blocker.get("symlink_path", ""),
                "symlink_target": blocker.get("symlink_target", ""),
                "route": blocker.get("route", "operator_required"),
            })
        return sorted(failures, key=lambda item: stable_json(item))

    def _normalize_acl_path(
        self,
        path: Path,
        *,
        repo: Path,
        target: AclTarget,
        shared_gid: int | None,
        changed: list[dict[str, str]],
        already_ok: list[dict[str, str]],
        warnings: list[dict[str, str]],
        failed: list[dict[str, str]],
    ) -> None:
        if not path.exists():
            return
        try:
            st = path.lstat()
        except OSError as exc:
            failed.append({
                "path": path.as_posix(),
                "repo_id": target.repo_id,
                "reason": "stat_failed",
                "error": str(exc),
            })
            return
        if stat.S_ISLNK(st.st_mode):
            failed.append({
                "path": path.as_posix(),
                "repo_id": target.repo_id,
                "reason": "refusing_to_chmod_symlink",
            })
            return
        if path_agent_writable(path, repo_path=repo, shared_gid=shared_gid):
            already_ok.append({
                "path": path.as_posix(),
                "repo_id": target.repo_id,
                "reason": "already_agent_writable",
                "mode": stat.filemode(st.st_mode),
            })
            return

        mode = stat.S_IMODE(st.st_mode)
        desired = (
            mode | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_ISGID
            if stat.S_ISDIR(st.st_mode)
            else mode | stat.S_IRGRP | stat.S_IWGRP
        )
        before_mode = stat.filemode(st.st_mode)
        group_changed = False
        try:
            if shared_gid is not None and st.st_gid != shared_gid:
                os.chown(path, -1, shared_gid)
                group_changed = True
        except OSError as exc:
            warnings.append({
                "path": path.as_posix(),
                "repo_id": target.repo_id,
                "reason": "chgrp_failed",
                "error": str(exc),
            })
        try:
            os.chmod(path, desired)
        except OSError as exc:
            if path_agent_writable(path, repo_path=repo, shared_gid=shared_gid):
                warnings.append({
                    "path": path.as_posix(),
                    "repo_id": target.repo_id,
                    "reason": "chmod_failed_but_agent_writable",
                    "error": str(exc),
                })
                return
            failed.append({
                "path": path.as_posix(),
                "repo_id": target.repo_id,
                "reason": "chmod_failed",
                "error": str(exc),
                "mode": before_mode,
            })
            return
        if path_agent_writable(path, repo_path=repo, shared_gid=shared_gid):
            try:
                after_mode = stat.filemode(path.lstat().st_mode)
            except OSError:
                after_mode = oct(desired)
            changed.append({
                "path": path.as_posix(),
                "repo_id": target.repo_id,
                "reason": "agent_writable_normalized",
                "old_mode": before_mode,
                "new_mode": after_mode,
                "group_changed": str(group_changed).lower(),
            })
            return
        failed.append({
            "path": path.as_posix(),
            "repo_id": target.repo_id,
            "reason": "normalization_did_not_make_agent_writable",
            "mode": before_mode,
        })

    def _route_preflight_impl(self, report: WorkspacePreflight) -> list[FailureObservation]:
        observations: list[FailureObservation] = []
        seen: set[str] = set()
        for blocker in sorted(report.blockers, key=lambda item: (_route_priority(item), stable_json(item))):
            failure_class = str(blocker.get("failure_class") or "operator_required")
            failure_type = str(blocker.get("failure_type") or "operator_clearance_required")
            route = str(blocker.get("route") or _route_for_failure(failure_class, failure_type))
            repo_id = str(blocker.get("repo_id") or "-")
            payload = dict(blocker)
            payload["route"] = route
            target_digest = stable_digest({
                "blocker": blocker,
                "resolutions": [
                    resolution.model_dump(mode="json")
                    for resolution in report.resolutions
                    if not blocker.get("path") or blocker.get("path") in {resolution.canonical_path, resolution.alias_path, resolution.original_path}
                ],
            })
            key = f"{failure_class}:{failure_type}:{target_digest}"
            if key in seen:
                continue
            seen.add(key)
            operator_required = failure_class == "operator_required" or route == "operator_required"
            retryable = not operator_required
            idempotency_key = (
                f"workspace-route:{report.feature_id}:{report.dag_sha256}:"
                f"g{report.group_idx if report.group_idx is not None else '-'}:"
                f"{report.stage}:{repo_id}:{failure_class}:{target_digest}"
            )
            observations.append(FailureObservation(
                feature_id=report.feature_id,
                dag_sha256=report.dag_sha256,
                group_idx=report.group_idx,
                attempt_id=report.attempt_id,
                source="workspace_authority",
                failure_class=failure_class,
                failure_type=failure_type,
                severity="error",
                deterministic=True,
                retryable=retryable,
                operator_required=operator_required,
                evidence_ids=[],
                payload=payload,
                route=route,
                signature_hash=target_digest,
                idempotency_key=idempotency_key,
            ))
        return observations

    def _snapshot_impl(
        self,
        *,
        feature_id: str,
        dag_sha256: str,
        group_idx: int | None,
        stage: str,
        attempt_id: int | None,
        registry: CanonicalRepoRegistry,
        targets: Sequence[PathTarget | dict[str, Any]],
        task_ids: Sequence[str] | None,
    ) -> list[WorkspaceSnapshot]:
        preflight = self._preflight_targets_impl(
            list(targets),
            registry,
            feature_id=feature_id,
            dag_sha256=dag_sha256,
            group_idx=group_idx,
            attempt_id=attempt_id,
            stage=stage,
        )
        repos = self._snapshot_repos(registry, preflight, task_ids)
        captured_at = self.now()
        snapshots: list[WorkspaceSnapshot] = []
        for repo in repos:
            repo_path = Path(repo.canonical_path)
            git_common_dir = self._git_common_dir(repo_path) or repo.git_common_dir
            branch = self._git_value(repo_path, ["branch", "--show-current"]) or repo.branch
            head_sha = self._git_value(repo_path, ["rev-parse", "HEAD"]) or repo.head_sha or ""
            status_text, status_evidence = self._git_status(repo_path, repo_id=repo.repo_id)
            parsed_status = _parse_porcelain_status(status_text)
            index_digest = self._index_digest(repo_path, git_common_dir)
            worktree_status_digest = (
                hashlib.sha256(status_text.encode("utf-8")).hexdigest()
                if status_evidence is None
                else stable_digest(status_evidence)
            )
            repo_resolutions = [
                resolution
                for resolution in preflight.resolutions
                if resolution.repo_id == repo.repo_id
            ]
            denied_paths = sorted({
                str(blocker.get("target") or blocker.get("path") or "")
                for blocker in preflight.blockers
                if str(blocker.get("repo_id") or "") == repo.repo_id
            } - {""})
            symlink_paths = _sorted_unique(
                _repo_relative_symlink_paths(repo_path, [Path(resolution.canonical_path) for resolution in repo_resolutions])
            )
            outside_root_targets = sorted({
                resolution.original_path
                for resolution in preflight.resolutions
                if resolution.path_kind == "outside_root"
            })
            agent_writable_paths = _sorted_unique(
                _agent_writable_snapshot_paths(repo, preflight.acl_targets, self._shared_gid())
            )
            dirty_paths = _sorted_unique(parsed_status["dirty"] + parsed_status["staged"] + parsed_status["untracked"])
            warnings = ["status_unavailable"] if status_evidence is not None else []
            snapshot_safety_status = "status_unavailable" if status_evidence is not None else repo.safety_status
            idempotency_key = (
                f"snapshot:{feature_id}:{dag_sha256}:"
                f"g{group_idx if group_idx is not None else '-'}:"
                f"{stage}:{repo.repo_id}:{head_sha}:{index_digest}:{worktree_status_digest}"
            )
            snapshots.append(WorkspaceSnapshot(
                feature_id=feature_id,
                dag_sha256=dag_sha256,
                group_idx=group_idx,
                attempt_id=attempt_id,
                stage=stage,
                repo_id=repo.repo_id,
                role=repo.role,
                canonical_path=repo.canonical_path,
                workspace_relative_path=repo.workspace_relative_path,
                source_path=repo.source_path,
                remote_url=repo.remote_url,
                remote_fingerprint=repo.remote_fingerprint,
                branch=branch,
                head_sha=head_sha,
                git_common_dir=git_common_dir,
                source_git_common_dir=repo.source_git_common_dir,
                case_sensitivity=_case_sensitivity(repo_path),
                index_digest=index_digest,
                worktree_status_digest=worktree_status_digest,
                dirty_paths=dirty_paths,
                staged_paths=_sorted_unique(parsed_status["staged"]),
                untracked_paths=_sorted_unique(parsed_status["untracked"]),
                forbidden_paths=[],
                denied_paths=denied_paths,
                symlink_paths=symlink_paths,
                outside_root_targets=outside_root_targets,
                agent_writable_paths=agent_writable_paths,
                alias_paths=list(repo.alias_paths),
                registry_artifact_id=None,
                acl_artifact_id=None,
                compatibility_projection_artifact_ids=[],
                no_dirty=status_evidence is None and not dirty_paths,
                status_unavailable=status_evidence,
                validated_at=captured_at,
                captured_at=captured_at,
                warnings=warnings,
                safety_status=snapshot_safety_status,
                idempotency_key=idempotency_key,
            ))
        return sorted(snapshots, key=lambda snapshot: (snapshot.repo_id, snapshot.canonical_path))

    def _snapshot_repos(
        self,
        registry: CanonicalRepoRegistry,
        preflight: WorkspacePreflight,
        task_ids: Sequence[str] | None,
    ) -> list[RepoIdentity]:
        if task_ids:
            wanted = set(task_ids)
            repos = [
                repo
                for repo in registry.repos
                if wanted.intersection(repo.writable_task_ids or repo.read_only_task_ids)
            ]
            if repos:
                return repos
        target_repo_ids = {
            resolution.repo_id
            for resolution in preflight.resolutions
            if resolution.repo_id
        } | {
            target.repo_id
            for target in preflight.acl_targets
            if target.repo_id
        }
        if target_repo_ids:
            return [repo for repo in registry.repos if repo.repo_id in target_repo_ids]
        return list(registry.repos)

    def _preflight_status_repos(
        self,
        registry: CanonicalRepoRegistry,
        resolutions: Sequence[CanonicalPathResolution],
        acl_targets: Sequence[AclTarget],
    ) -> list[RepoIdentity]:
        target_repo_ids = {
            resolution.repo_id
            for resolution in resolutions
            if resolution.repo_id
        } | {
            target.repo_id
            for target in acl_targets
            if target.repo_id
        }
        if not target_repo_ids:
            return []
        return [repo for repo in registry.repos if repo.repo_id in target_repo_ids]

    def _shared_gid(self) -> int | None:
        if self.shared_gid is not None:
            return self.shared_gid
        return _agent_shared_gid(self.shared_group)

    def _git_value(self, cwd: Path, args: Sequence[str]) -> str:
        result = self._run_git(cwd, args)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _git_common_dir(self, cwd: Path) -> str | None:
        value = self._git_value(cwd, ["rev-parse", "--git-common-dir"])
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        return path.resolve(strict=False).as_posix()

    def _git_status(self, cwd: Path, *, repo_id: str = "") -> tuple[str, dict[str, str] | None]:
        args = ["status", "--porcelain=v1", "--untracked-files=all"]
        result = self._run_git(cwd, args)
        if result.returncode == 0:
            return result.stdout, None
        return "", _status_unavailable_evidence(cwd, args, result, repo_id=repo_id)

    def _index_digest(self, repo_path: Path, git_common_dir: str | None) -> str:
        index_candidates: list[Path] = []
        if git_common_dir:
            index_candidates.append(Path(git_common_dir) / "index")
        index_candidates.append(repo_path / ".git" / "index")
        for index in index_candidates:
            if index.exists() and index.is_file():
                try:
                    return hashlib.sha256(index.read_bytes()).hexdigest()
                except OSError:
                    return stable_digest({"index": index.as_posix(), "error": "read_failed"})
        staged = self._git_value(repo_path, ["diff", "--cached", "--name-status"])
        return hashlib.sha256(staged.encode("utf-8")).hexdigest()

    def _run_git(self, cwd: Path, args: Sequence[str]) -> _GitResult:
        if self.git_runner is not None:
            try:
                result = self.git_runner(cwd, list(args))
            except Exception as exc:
                return _git_exception_result(exc)
            if isinstance(result, _GitResult):
                return result
            return _GitResult(returncode=0, stdout=str(result))
        if not cwd.exists() or shutil.which("git") is None:
            return _GitResult(returncode=1, stdout="", stderr="git unavailable")
        try:
            proc = subprocess.run(
                ["git", "-C", str(cwd), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception as exc:
            return _git_exception_result(exc)
        return _GitResult(proc.returncode, proc.stdout, proc.stderr)


def _git_exception_result(exc: Exception) -> _GitResult:
    if isinstance(exc, subprocess.TimeoutExpired):
        timeout = "" if exc.timeout is None else f" after {exc.timeout} seconds"
        return _GitResult(returncode=124, stdout=_bytes_to_text(exc.output), stderr=f"git command timed out{timeout}")
    if isinstance(exc, PermissionError):
        return _GitResult(returncode=126, stdout="", stderr=str(exc))
    return _GitResult(returncode=1, stdout="", stderr=str(exc))


def _repo_has_git_marker(repo_path: Path, repo: RepoIdentity) -> bool:
    if (repo_path / ".git").exists():
        return True
    if repo.git_common_dir and Path(repo.git_common_dir).exists():
        return True
    return False


def _status_unavailable_evidence(
    repo_path: Path,
    args: Sequence[str],
    result: _GitResult,
    *,
    repo_id: str = "",
) -> dict[str, str]:
    stderr = result.stderr or ""
    stdout = result.stdout or ""
    evidence = {
        "reason": "status_unavailable",
        "repo_id": repo_id,
        "canonical_path": repo_path.resolve(strict=False).as_posix(),
        "command": "git " + " ".join(args),
        "exit_code": str(result.returncode),
        "status_failure_kind": _status_failure_kind(result),
        "stdout_digest": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_digest": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stderr_excerpt": _bounded_text(stderr),
    }
    evidence["evidence_digest"] = stable_digest(evidence)
    return evidence


def _status_unavailable_blocker(repo: RepoIdentity, evidence: dict[str, str]) -> dict[str, str]:
    return {
        "failure_class": "workspace_status",
        "failure_type": "status_unavailable",
        "reason": "status_unavailable",
        "route": "quiesce",
        "repo_id": repo.repo_id,
        "path": repo.canonical_path,
        "canonical_path": repo.canonical_path,
        "status_failure_kind": evidence.get("status_failure_kind", "git_status_failed"),
        "status_evidence_digest": evidence.get("evidence_digest", ""),
        "command": evidence.get("command", ""),
        "exit_code": evidence.get("exit_code", ""),
        "stderr_digest": evidence.get("stderr_digest", ""),
        "stderr_excerpt": evidence.get("stderr_excerpt", ""),
    }


def _status_failure_kind(result: _GitResult) -> str:
    stderr = (result.stderr or "").lower()
    if result.returncode == 124 or "timed out" in stderr or "timeout" in stderr:
        return "status_timeout"
    if result.returncode == 126 or "permission denied" in stderr or "operation not permitted" in stderr:
        return "permission_denied"
    if (
        "not a git repository" in stderr
        or "bad object" in stderr
        or "corrupt" in stderr
        or "unable to read" in stderr
        or "invalid object" in stderr
    ):
        return "corrupt_repo"
    return "git_status_failed"


def _bounded_text(value: str, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _bytes_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _lexical_path_exists(path: Path) -> bool:
    try:
        path.expanduser().lstat()
        return True
    except OSError:
        return False


def _blocked_repo_parent_for(feature_root: Path) -> Path:
    root = feature_root.expanduser().absolute()
    if root.name == "repos":
        return root
    parts = root.parts
    if ".iriai" in parts and "features" in parts:
        return root / "repos"
    return root


def _lexical_symlink_component(path: Path, *, anchor: Path | None = None) -> Path | None:
    absolute = path.expanduser().absolute()
    scan_anchor = _lexical_scan_anchor(absolute, anchor)
    try:
        relative = absolute.relative_to(scan_anchor)
    except ValueError:
        scan_anchor = absolute.parent
        relative = Path(absolute.name)

    current = scan_anchor
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return current
        except OSError:
            return current
    return None


def _lexical_scan_anchor(path: Path, anchor: Path | None) -> Path:
    if anchor is not None:
        absolute_anchor = anchor.expanduser().absolute()
        try:
            path.relative_to(absolute_anchor)
            if path != absolute_anchor:
                return absolute_anchor
        except ValueError:
            pass

    parts = path.parts
    if ".iriai" in parts:
        idx = parts.index(".iriai")
        if idx == 0:
            return Path(parts[0])
        return Path(*parts[:idx])
    return path.parent


def _readlink_target(path: Path) -> str:
    try:
        target = Path(os.readlink(path))
    except OSError:
        return ""
    if not target.is_absolute():
        target = path.parent / target
    return target.absolute().as_posix()


def _path_has_symlink_component_between(root: Path, path: Path) -> bool:
    root_resolved = root.expanduser().resolve(strict=False)
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    candidate = candidate.absolute()
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError:
        return True
    cursor = root_resolved
    for part in relative.parts:
        cursor = cursor / part
        try:
            if cursor.is_symlink():
                return True
        except OSError:
            return True
    return False


def _gitdir_from_file(marker: Path) -> Path | None:
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if not first.lower().startswith("gitdir:"):
        return None
    raw = first.split(":", 1)[1].strip()
    if not raw:
        return None
    git_dir = Path(raw).expanduser()
    if not git_dir.is_absolute():
        git_dir = marker.parent / git_dir
    return git_dir


def _git_common_dir_from_file(git_dir: Path) -> Path | None:
    common_dir_file = git_dir / "commondir"
    if not common_dir_file.exists():
        return None
    try:
        raw = common_dir_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    common_dir = Path(raw).expanduser()
    if not common_dir.is_absolute():
        common_dir = git_dir / common_dir
    return common_dir


def _git_metadata_inside_repo_parent(repo_parent: Path, root: Path) -> bool:
    repo_parent = repo_parent.expanduser().resolve(strict=False)
    root = root.expanduser().resolve(strict=False)
    marker = root / ".git"
    if marker.is_symlink():
        return False
    if marker.is_dir():
        git_dir_raw = marker
    elif marker.is_file():
        parsed = _gitdir_from_file(marker)
        if parsed is None:
            return False
        git_dir_raw = parsed
    else:
        return False

    git_dir = git_dir_raw.resolve(strict=False)
    if not _contained(git_dir, repo_parent):
        return False
    if _path_has_symlink_component_between(repo_parent, git_dir_raw):
        return False

    common_dir_raw = _git_common_dir_from_file(git_dir_raw)
    if common_dir_raw is None:
        return True
    common_dir = common_dir_raw.resolve(strict=False)
    if not _contained(common_dir, repo_parent):
        return False
    if _path_has_symlink_component_between(repo_parent, common_dir_raw):
        return False
    return True


def _as_repo_parent(feature_root: Path) -> Path:
    root = feature_root.expanduser().resolve(strict=False)
    if (root / "repos").is_dir() and not (root / ".git").exists():
        return (root / "repos").resolve(strict=False)
    return root


def _feature_slug_from_root(repo_parent: Path) -> str:
    try:
        if repo_parent.name == "repos":
            return repo_parent.parent.name
    except Exception:
        pass
    return ""


def _is_direct_repo_root(repo_parent: Path, root: Path) -> bool:
    repo_parent = repo_parent.resolve(strict=False)
    root = root.resolve(strict=False)
    if root == repo_parent:
        return (root / ".git").exists()
    try:
        rel = root.relative_to(repo_parent)
    except ValueError:
        return False
    return len(rel.parts) == 1


def _is_valid_repo_root(repo_parent: Path, root: Path) -> bool:
    repo_parent = repo_parent.resolve(strict=False)
    root = root.resolve(strict=False)
    if not (root / ".git").exists() or not _contained(root, repo_parent):
        return False
    if not _git_metadata_inside_repo_parent(repo_parent, root):
        return False
    if root == repo_parent:
        return True
    current = root.parent
    while current != repo_parent and _contained(current, repo_parent):
        if (current / ".git").exists():
            return False
        current = current.parent
    return True


def _dedupe_paths(paths: Iterable[Path | None]) -> list[Path | None]:
    seen: set[str] = set()
    result: list[Path | None] = []
    for path in paths:
        if path is None:
            continue
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _identity_for_candidate(
    feature_id: str,
    repo_parent: Path,
    candidate: _CandidateEvidence,
) -> tuple[str, str]:
    if candidate.registry_repo_id:
        return "registry_repo_id", candidate.registry_repo_id
    if candidate.source_git_common_dir:
        return "source_git_common_dir", candidate.source_git_common_dir
    if candidate.source_path:
        return "source_path", candidate.source_path
    if candidate.remote_fingerprint:
        return "remote_fingerprint", f"{candidate.remote_fingerprint}:{candidate.repo_name}"
    return "new_feature_repo", f"{feature_id}:{_relative_to(candidate.root, repo_parent)}"


def _candidate_evidence_digest(candidate: _CandidateEvidence) -> str:
    return stable_digest({
        "action": candidate.action,
        "branch": candidate.branch,
        "declared_canonical_path": candidate.declared_canonical_path,
        "git_common_dir": candidate.git_common_dir,
        "head_sha": candidate.head_sha,
        "registry_backed": candidate.registry_backed,
        "registry_repo_id": candidate.registry_repo_id,
        "remote_fingerprint": candidate.remote_fingerprint,
        "remote_url": candidate.remote_url,
        "repo_name": candidate.repo_name,
        "role": candidate.role,
        "root": candidate.root,
        "source_git_common_dir": candidate.source_git_common_dir,
        "source_path": candidate.source_path,
    })


def _select_canonical_candidate(
    group: list[tuple[_CandidateEvidence, str, str]],
    repo_parent: Path,
) -> _CandidateEvidence:
    candidates = [item[0] for item in group]
    declared_paths = {
        _path_key(candidate.declared_canonical_path)
        for candidate in candidates
        if candidate.declared_canonical_path is not None
        and _contained(candidate.declared_canonical_path, repo_parent)
        and candidate.declared_canonical_path.exists()
    }
    for candidate in sorted(candidates, key=lambda item: item.root.as_posix()):
        if _path_key(candidate.root) in declared_paths:
            return candidate
    registry_backed = [candidate for candidate in candidates if candidate.registry_backed and candidate.root.exists()]
    if registry_backed:
        return sorted(registry_backed, key=lambda item: item.root.as_posix())[0]
    return sorted(candidates, key=lambda item: (item.root.name.endswith("-wt"), item.root.as_posix()))[0]


def _strong_identity_match_reasons(
    left: _CandidateEvidence,
    right: _CandidateEvidence,
) -> list[str]:
    reasons: list[str] = []
    if left.source_git_common_dir and right.source_git_common_dir and left.source_git_common_dir == right.source_git_common_dir:
        reasons.append("source_git_common_dir_match")
    if left.git_common_dir and right.git_common_dir and left.git_common_dir == right.git_common_dir:
        reasons.append("git_common_dir_match")
    if left.source_path and right.source_path and left.source_path == right.source_path:
        reasons.append("source_path_match")
    return reasons


def _sorted_aliases(aliases: dict[str, str]) -> dict[str, str]:
    return {
        alias: aliases[alias]
        for alias in sorted(aliases, key=lambda value: (-len(Path(value).parts), value))
    }


def _alias_edges(registry: CanonicalRepoRegistry) -> dict[str, str]:
    feature_root = Path(registry.feature_root).resolve(strict=False)
    edges: dict[str, str] = {}
    for alias, canonical in dict(registry.aliases).items():
        alias_path = Path(alias).expanduser()
        canonical_path = Path(canonical).expanduser()
        if not alias_path.is_absolute():
            alias_path = feature_root / alias_path
        if not canonical_path.is_absolute():
            canonical_path = feature_root / canonical_path
        edges[alias_path.resolve(strict=False).as_posix()] = canonical_path.resolve(strict=False).as_posix()
    for repo in registry.repos:
        canonical_path = Path(repo.canonical_path).expanduser()
        if not canonical_path.is_absolute():
            canonical_path = feature_root / canonical_path
        for alias in repo.alias_paths:
            alias_path = Path(alias).expanduser()
            if not alias_path.is_absolute():
                alias_path = feature_root / alias_path
            edges.setdefault(
                alias_path.resolve(strict=False).as_posix(),
                canonical_path.resolve(strict=False).as_posix(),
            )
    return _sorted_aliases(edges)


def _registry_payload(registry: CanonicalRepoRegistry) -> dict[str, Any]:
    return {
        "aliases": _sorted_aliases(dict(registry.aliases)),
        "blocked": registry.blocked,
        "blockers": sorted(registry.blockers, key=lambda item: stable_json(item)),
        "collisions": sorted(registry.collisions, key=lambda item: stable_json(item)),
        "feature_id": registry.feature_id,
        "feature_root": registry.feature_root,
        "feature_slug": registry.feature_slug,
        "registry_version": registry.registry_version,
        "repos": [
            repo.model_dump(mode="json")
            for repo in sorted(registry.repos, key=lambda item: (item.repo_id, item.canonical_path))
        ],
    }


def _registry_digest(registry: CanonicalRepoRegistry) -> str:
    return stable_digest(_registry_payload(registry))


def _longest_prefix_match(
    path: Path,
    prefixes: Iterable[str],
    *,
    follow_symlinks: bool = True,
) -> str | None:
    resolved = path.resolve(strict=False) if follow_symlinks else path.absolute()
    matches: list[Path] = []
    for prefix in prefixes:
        prefix_path = Path(prefix).resolve(strict=False) if follow_symlinks else Path(prefix).absolute()
        try:
            resolved.relative_to(prefix_path)
        except ValueError:
            continue
        matches.append(prefix_path)
    if not matches:
        return None
    return sorted(matches, key=lambda item: (-len(item.parts), item.as_posix()))[0].as_posix()


def _repo_for_path(
    path: Path,
    registry: CanonicalRepoRegistry,
    *,
    follow_symlinks: bool = True,
) -> RepoIdentity | None:
    matches: list[RepoIdentity] = []
    resolved = path.resolve(strict=False) if follow_symlinks else path.absolute()
    for repo in registry.repos:
        root = Path(repo.canonical_path).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        matches.append(repo)
    if not matches:
        return None
    return sorted(matches, key=lambda item: (-len(Path(item.canonical_path).parts), item.canonical_path))[0]


def _repo_by_id(registry: CanonicalRepoRegistry, repo_id: str) -> RepoIdentity | None:
    return next((repo for repo in registry.repos if repo.repo_id == repo_id), None)


def _ambiguous_relative_path(path_text: str, registry: CanonicalRepoRegistry) -> bool:
    raw = Path(path_text).expanduser()
    if raw.is_absolute() or len(registry.repos) < 2:
        return False
    normalized = _strip_line_suffix(path_text).strip("/")
    if not normalized or _has_traversal(normalized):
        return False
    first = Path(normalized).parts[0] if Path(normalized).parts else ""
    repo_names = {
        Path(repo.workspace_relative_path or repo.canonical_path).name
        for repo in registry.repos
    }
    return first not in repo_names


def _has_symlink_component(root: Path, target: Path) -> str | None:
    try:
        root_resolved = root.resolve(strict=False)
        target_resolved = target.resolve(strict=False)
    except OSError:
        root_resolved = root
        target_resolved = target
    try:
        relative = target.relative_to(root)
        current = root
    except ValueError:
        try:
            relative = target_resolved.relative_to(root_resolved)
            current = root_resolved
        except ValueError:
            return None
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return current.as_posix()
        except OSError:
            return current.as_posix()
    return None


def _paths_diverge(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    if left.is_dir() or right.is_dir():
        return left.is_dir() != right.is_dir()
    try:
        return hashlib.sha256(left.read_bytes()).hexdigest() != hashlib.sha256(right.read_bytes()).hexdigest()
    except OSError:
        return True


def _blocker_from_resolution(
    resolution: CanonicalPathResolution,
    target: PathTarget,
    *,
    failure_class: str,
    failure_type: str,
    route: str,
    reason: str,
) -> dict[str, str]:
    return {
        "failure_class": failure_class,
        "failure_type": failure_type,
        "reason": reason,
        "route": route,
        "path": resolution.canonical_path,
        "raw_path": target.raw_path,
        "repo_id": resolution.repo_id or "",
        "task_id": target.task_id or "",
        "source": target.source,
    }


def _nearest_existing_parent(target: Path, repo: Path) -> Path | None:
    repo = repo.resolve(strict=False)
    current = target if target.exists() and target.is_dir() else target.parent
    while True:
        if _contained(current, repo) and current.exists():
            return current if current.is_dir() else current.parent
        if current == current.parent or not _contained(current, repo):
            return repo if repo.exists() else None
        current = current.parent


def _acl_closure(target: AclTarget) -> list[Path]:
    repo = Path(target.repo_root or _find_git_root(Path(target.canonical_path)) or "")
    if not repo:
        return []
    canonical = Path(target.canonical_path)
    paths: list[Path] = []
    if repo.exists():
        paths.append(repo)
    try:
        rel = canonical.resolve(strict=False).relative_to(repo.resolve(strict=False))
    except ValueError:
        return paths
    current = repo
    parent_parts = rel.parts if canonical.exists() and canonical.is_dir() else rel.parts[:-1]
    for part in parent_parts:
        current = current / part
        if current.exists():
            paths.append(current)
        else:
            break
    nearest = Path(target.nearest_existing_parent) if target.nearest_existing_parent else None
    if nearest is not None and nearest.exists():
        paths.append(nearest)
    if canonical.exists():
        paths.append(canonical)
    git_dir = repo / ".git"
    if git_dir.exists():
        paths.append(git_dir)
    git_index = git_dir / "index"
    if git_index.exists():
        paths.append(git_index)
    return _dedupe_existing_paths(paths)


def _append_acl_target_once(targets: list[AclTarget], target: AclTarget) -> None:
    key = (target.repo_id, target.canonical_path, target.action)
    for existing in targets:
        if (existing.repo_id, existing.canonical_path, existing.action) == key:
            return
    targets.append(target)


def _dedupe_existing_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _find_git_root(path: Path) -> str | None:
    current = path if path.is_dir() else path.parent
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent.resolve(strict=False).as_posix()
    return None


def _is_git_metadata_path(path: Path, repo: Path) -> bool:
    try:
        rel = path.resolve(strict=False).relative_to(repo.resolve(strict=False))
    except ValueError:
        return False
    return bool(rel.parts and rel.parts[0] == ".git")


def _route_for_failure(failure_class: str, failure_type: str) -> str:
    if failure_class == "worktree_alias":
        return "run_canonicalization_repair"
    if failure_class == "acl_workability":
        return "run_workspace_repair"
    if failure_class == "stale_projection":
        return "retry_verifier"
    if failure_class == "operator_required":
        return "operator_required"
    return "quiesce"


def _route_priority(blocker: dict[str, str]) -> int:
    failure_class = str(blocker.get("failure_class") or "")
    failure_type = str(blocker.get("failure_type") or "")
    reason = str(blocker.get("reason") or "")
    if failure_class == "operator_required" and ("outside" in reason or "symlink" in reason):
        return 0
    if failure_class == "operator_required":
        return 1
    if failure_class == "worktree_alias" and failure_type == "alias_canonical_divergent":
        return 2
    if failure_class == "worktree_alias":
        return 3
    if failure_class == "acl_workability":
        return 4
    if failure_class == "stale_projection":
        return 5
    return 9


def _parse_porcelain_status(status_text: str) -> dict[str, list[str]]:
    dirty: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []
    for raw_line in status_text.splitlines():
        if not raw_line:
            continue
        if len(raw_line) < 3:
            continue
        if raw_line.startswith("? "):
            xy = "??"
            path_text = raw_line[2:].strip().strip('"')
        elif raw_line.startswith("1 "):
            parts = raw_line.split(" ", 8)
            if len(parts) < 9:
                continue
            xy = parts[1]
            path_text = parts[8].strip().strip('"')
        elif raw_line.startswith("2 "):
            parts = raw_line.split(" ", 9)
            if len(parts) < 10:
                continue
            xy = parts[1]
            path_text = parts[9].split("\t", 1)[-1].strip().strip('"')
        else:
            xy = raw_line[:2]
            path_text = raw_line[2:].strip().strip('"')
        if " -> " in path_text:
            _, path_text = path_text.rsplit(" -> ", 1)
        if not path_text:
            continue
        if xy == "??":
            untracked.append(path_text)
            dirty.append(path_text)
            continue
        if xy[0] != " ":
            staged.append(path_text)
        if xy[1] != " " or xy[0] != " ":
            dirty.append(path_text)
    return {
        "dirty": _sorted_unique(dirty),
        "staged": _sorted_unique(staged),
        "untracked": _sorted_unique(untracked),
    }


def _repo_relative_symlink_paths(repo: Path, target_paths: Sequence[Path], *, limit: int = 200) -> list[str]:
    paths: list[str] = []
    for target in target_paths:
        symlink = _has_symlink_component(repo, target)
        if symlink:
            try:
                paths.append(Path(symlink).resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix())
            except ValueError:
                paths.append(symlink)
    if repo.exists():
        for dirpath, dirnames, filenames in os.walk(repo):
            current = Path(dirpath)
            names = list(dirnames) + list(filenames)
            for name in names:
                path = current / name
                if path.is_symlink():
                    try:
                        paths.append(path.relative_to(repo).as_posix())
                    except ValueError:
                        paths.append(path.as_posix())
                    if len(paths) >= limit:
                        return _dedupe(paths)
            dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
    return _dedupe(paths)


def _agent_writable_snapshot_paths(
    repo: RepoIdentity,
    acl_targets: Sequence[AclTarget],
    shared_gid: int | None,
) -> list[str]:
    repo_path = Path(repo.canonical_path)
    result: list[str] = []
    for target in acl_targets:
        if target.repo_id != repo.repo_id:
            continue
        candidates = [Path(target.canonical_path)]
        if target.nearest_existing_parent:
            candidates.append(Path(target.nearest_existing_parent))
        for path in candidates:
            if path.exists() and path_agent_writable(path, repo_path=repo_path, shared_gid=shared_gid):
                result.append(path.as_posix())
    return _dedupe(result)


def _case_sensitivity(repo_path: Path) -> CaseSensitivity:
    # Avoid mutating repos during snapshot capture. The platform is a useful hint
    # but not authoritative for mounted volumes, so unknown is the safe default.
    return "unknown"


__all__ = [
    "AclNormalizationResult",
    "AclTarget",
    "CanonicalPathResolution",
    "CanonicalRepoRegistry",
    "FailureObservation",
    "PathTarget",
    "RepoIdentity",
    "WorkspaceAuthority",
    "WorkspacePreflight",
    "WorkspaceSnapshot",
    "normalize_remote_fingerprint",
    "path_agent_writable",
    "repo_id_for_identity",
    "stable_digest",
    "stable_json",
]
