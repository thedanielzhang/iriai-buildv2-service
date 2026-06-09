from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import json
import logging
import math
import os
import plistlib
import pwd
import secrets
import shlex
import shutil
import sys
import time
import uuid
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from iriai_compose.runner import AgentRuntime
from iriai_compose.storage import AgentSession, SessionStore

from .claude import _inline_defs, _resolve_model_and_effort, _validate_runtime_workspace_binding

if TYPE_CHECKING:
    from collections.abc import Callable

    from iriai_compose.actors import Role
    from iriai_compose.workflow import Workspace

logger = logging.getLogger(__name__)
_RUNTIME_WORKSPACE_BINDING_KEY = "runtime_workspace_binding"
_RUNTIME_SCRATCH_ROOTS_KEY = "runtime_scratch_roots"
_BOUND_WRITE_AUTHORIZED_KEY = "runtime_workspace_write_authorized"
_BOUND_WRITE_AUTHORIZATION_KEY = "runtime_workspace_write_authorization"
_BOUND_WRITE_AUTH_SECRET_FILE = "runtime-write-auth.secret"
_BOUND_WRITE_AUTH_SECRET_MODE = 0o640
_BOUND_WRITE_GUARD_KEY = "runtime_workspace_write_guard"
_BOUND_WRITE_GUARD_SANDBOX_EXEC = "sandbox_exec"
_AUTHORITY_GRANT_SCHEMA_VERSION = "runtime-workspace-authority-grant-v1"
_WRITE_PRODUCING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}

DEFAULT_POOL_ROOT = Path(
    os.environ.get("IRIAI_CLAUDE_POOL_ROOT", "/Users/Shared/iriai/claude-pool")
)
DEFAULT_PROFILE_NAMES = ("iriai-claude-1",)
DEFAULT_PROFILE_WEIGHTS = {
    "iriai-claude-1": 1.0,
}
DEPRECATED_DEFAULT_PROFILE_NAMES = ("iriai-claude-1", "iriai-claude-2")
DEPRECATED_DEFAULT_PROFILE_WEIGHT_SETS = (
    {"iriai-claude-1": 1.0, "iriai-claude-2": 9.0},
)
LEGACY_DEFAULT_PROFILE_NAMES = ("iriai-claude-1", "iriai-claude-2", "iriai-claude-3")
LEGACY_DEFAULT_PROFILE_WEIGHT_SETS = (
    {"iriai-claude-1": 5.0, "iriai-claude-2": 1.0, "iriai-claude-3": 12.0},
    {"iriai-claude-1": 5.0, "iriai-claude-2": 1.0, "iriai-claude-3": 9.0},
)
DEFAULT_CLAUDE_COMMAND = os.environ.get("IRIAI_CLAUDE_COMMAND", "/opt/homebrew/bin/claude")
DEFAULT_POLL_INTERVAL_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_POLL_INTERVAL_SECONDS", "1") or "1"
)
DEFAULT_HEARTBEAT_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_HEARTBEAT_SECONDS", "10") or "10"
)
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_HEARTBEAT_TIMEOUT_SECONDS", "180") or "180"
)
DEFAULT_JOB_STALE_TIMEOUT_SECONDS = float(
    os.environ.get(
        "IRIAI_CLAUDE_POOL_JOB_STALE_TIMEOUT_SECONDS",
        str(DEFAULT_HEARTBEAT_TIMEOUT_SECONDS),
    )
    or str(DEFAULT_HEARTBEAT_TIMEOUT_SECONDS)
)
DEFAULT_JOB_ABSOLUTE_TIMEOUT_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_JOB_ABSOLUTE_TIMEOUT_SECONDS", "21600")
    or "21600"
)
DEFAULT_HEALTH_TIMEOUT_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_HEALTH_TIMEOUT_SECONDS", "60") or "60"
)
DEFAULT_RUNNER_UMASK = os.environ.get("IRIAI_CLAUDE_POOL_RUNNER_UMASK", "0002")
DEFAULT_LIMIT_PROBE_AFTER_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_LIMIT_PROBE_AFTER_SECONDS", "60") or "60"
)
DEFAULT_OVERLOAD_PROBE_AFTER_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_OVERLOAD_PROBE_AFTER_SECONDS", "30") or "30"
)
DEFAULT_AUTH_PROBE_AFTER_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_AUTH_PROBE_AFTER_SECONDS", "300") or "300"
)
DEFAULT_PROBE_FAILED_AFTER_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_PROBE_FAILED_AFTER_SECONDS", "60") or "60"
)
DEFAULT_RECENT_USAGE_WINDOW_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_RECENT_USAGE_WINDOW_SECONDS", "21600") or "21600"
)
DEFAULT_AVAILABILITY_TIMEOUT_SECONDS = float(
    os.environ.get("IRIAI_CLAUDE_POOL_AVAILABILITY_TIMEOUT_SECONDS", "45") or "45"
)
DEFAULT_AVAILABILITY_PROBE_MODEL = os.environ.get(
    "IRIAI_CLAUDE_POOL_PROBE_MODEL",
    "claude-haiku-4-5-20251001",
)
DEFAULT_AVAILABILITY_PROBE_PROMPT = os.environ.get(
    "IRIAI_CLAUDE_POOL_PROBE_PROMPT",
    "Reply with exactly OK.",
)
# OPTIONAL / OFF-BY-DEFAULT capability probe model for CLAUDE members. When
# empty (the default) behavior is unchanged: the availability probe uses the
# cheap Haiku model. When set to an Opus model id, the readiness probe runs on
# that model so a Claude account that has lost Opus capability (but can still
# serve Haiku) fails the probe and is cooled down. Strictly opt-in.
DEFAULT_CAPABILITY_PROBE_MODEL = os.environ.get(
    "IRIAI_CLAUDE_POOL_CAPABILITY_PROBE_MODEL",
    "",
)

_current_invocation_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "claude_pool_runtime_invocation_id", default=None,
)


@dataclass(frozen=True)
class ClaudePoolProfile:
    name: str
    user: str
    claude_command: str = DEFAULT_CLAUDE_COMMAND
    weight: float = 1.0
    # Pool members are heterogeneous: ``"claude"`` accounts dispatched via the
    # LaunchAgent job queue, or ``"codex"`` dispatched in-process via the
    # embedded CodexAgentRuntime. Defaults to ``"claude"`` so existing pools
    # stay byte-identical.
    kind: str = "claude"


@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list[Any]
    id: str | None = None


@dataclass
class ResultMessage:
    structured_output: Any = None


@dataclass(frozen=True)
class ClaudePoolLateCompletion:
    job_id: str
    profile: str
    done_path: str
    result_path: str
    stdout_path: str | None
    stderr_path: str | None
    manifest: dict[str, Any]
    result: dict[str, Any]
    result_text: str
    structured_output: dict[str, Any]
    raw: Any
    schema_digest_validation: str

    def recovery_metadata(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "profile": self.profile,
            "done_path": self.done_path,
            "result_path": self.result_path,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "schema_digest_validation": self.schema_digest_validation,
            "job_created_at": self.manifest.get("created_at"),
            "job_claimed_at": self.manifest.get("claimed_at"),
            "job_finished_at": self.manifest.get("finished_at"),
        }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _stable_json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _jsonable_deep(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable_deep(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable_deep(item) for item in value]
    return _jsonable(value)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _runtime_workspace_binding(role: Any) -> dict[str, Any] | None:
    raw = (getattr(role, "metadata", None) or {}).get(_RUNTIME_WORKSPACE_BINDING_KEY)
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)


def _profile_runtime_scratch_roots(profile: ClaudePoolProfile) -> list[Path]:
    try:
        home = Path(pwd.getpwnam(profile.user).pw_dir)
    except KeyError:
        home = Path("/Users") / profile.user
    return [home / ".claude" / "session-env"]


def _role_is_write_producing(role: Any) -> bool:
    tools = {str(tool) for tool in (getattr(role, "tools", None) or [])}
    return bool(
        tools & _WRITE_PRODUCING_TOOLS
        or (getattr(role, "metadata", None) or {}).get("write_producing")
    )


def _manifest_role_is_write_producing(role: Any) -> bool:
    if not isinstance(role, Mapping):
        return False
    tools = {str(tool) for tool in (role.get("tools") or [])}
    metadata = role.get("metadata") or {}
    return bool(
        tools & _WRITE_PRODUCING_TOOLS
        or (isinstance(metadata, Mapping) and metadata.get("write_producing"))
        or role.get("write_producing")
    )


def _pool_write_auth_secret(root: Path) -> str:
    secret_path = root / _BOUND_WRITE_AUTH_SECRET_FILE
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        if not secret_path.exists():
            fd = os.open(
                secret_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                _BOUND_WRITE_AUTH_SECRET_MODE,
            )
            try:
                os.write(fd, secrets.token_hex(32).encode("utf-8"))
            finally:
                os.close(fd)
        try:
            stat_result = secret_path.stat()
        except OSError:
            stat_result = None
        if stat_result is None or stat_result.st_uid == os.geteuid():
            try:
                secret_path.chmod(_BOUND_WRITE_AUTH_SECRET_MODE)
            except OSError:
                logger.warning(
                    "Unable to chmod Claude pool write auth secret %s",
                    secret_path,
                )
        secret = secret_path.read_text(encoding="utf-8").strip()
    except FileExistsError:
        secret = secret_path.read_text(encoding="utf-8").strip()
    if not secret:
        raise RuntimeError("Claude pool write authorization secret is empty")
    return secret


def _bound_write_authorization(manifest: Mapping[str, Any], secret: str) -> str:
    binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
    binding_map = dict(binding) if isinstance(binding, Mapping) else {}
    role = manifest.get("role")
    role_map = dict(role) if isinstance(role, Mapping) else {}
    paths = manifest.get("paths")
    paths_map = dict(paths) if isinstance(paths, Mapping) else {}
    payload = {
        "id": manifest.get("id"),
        "created_at": manifest.get("created_at"),
        "cwd": manifest.get("cwd"),
        "prompt_path": paths_map.get("prompt"),
        "role_name": role_map.get("name"),
        "role_tools": sorted(str(tool) for tool in (role_map.get("tools") or [])),
        "sandbox_id": binding_map.get("sandbox_id") or manifest.get("sandbox_id"),
        "runtime": binding_map.get("runtime"),
        "workspace_override": binding_map.get("workspace_override"),
        "manifest_path": binding_map.get("manifest_path") or manifest.get("manifest_path"),
        "expires_at": binding_map.get("expires_at") or manifest.get("expires_at"),
        "sandbox_profile": paths_map.get("sandbox_profile"),
        "write_guard": manifest.get(_BOUND_WRITE_GUARD_KEY),
        "repo_roots": binding_map.get("repo_roots") or manifest.get("repo_roots") or {},
        "writable_roots": binding_map.get("writable_roots") or manifest.get("writable_roots") or [],
        "write_guard_roots": binding_map.get("write_guard_roots") or manifest.get("write_guard_roots") or [],
        "write_guard_scope": binding_map.get("write_guard_scope") or manifest.get("write_guard_scope"),
        "authority_schema_version": (
            binding_map.get("authority_schema_version")
            or manifest.get("authority_schema_version")
        ),
        "runtime_workspace_authority_grants": (
            binding_map.get("runtime_workspace_authority_grants")
            or manifest.get("runtime_workspace_authority_grants")
            or []
        ),
        "runtime_workspace_authority_grant_digest": (
            binding_map.get("runtime_workspace_authority_grant_digest")
            or manifest.get("runtime_workspace_authority_grant_digest")
            or ""
        ),
        "promotable": binding_map.get("promotable", manifest.get("promotable")),
        "blocked_roots": binding_map.get("blocked_roots") or manifest.get("blocked_roots") or [],
        "contract_ids": binding_map.get("contract_ids") or manifest.get("contract_ids") or [],
        "runtime_scratch_roots": manifest.get(_RUNTIME_SCRATCH_ROOTS_KEY) or [],
    }
    return hmac.new(
        secret.encode("utf-8"),
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _sandbox_profile_quote(value: Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _write_guard_roots(manifest: Mapping[str, Any]) -> list[Path]:
    binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
    binding_map = dict(binding) if isinstance(binding, Mapping) else {}
    paths = manifest.get("paths")
    paths_map = dict(paths) if isinstance(paths, Mapping) else {}
    write_guard_scope = str(
        binding_map.get("write_guard_scope")
        or manifest.get("write_guard_scope")
        or "contract"
    )
    manifest_write_guard_roots = (
        binding_map.get("write_guard_roots")
        or manifest.get("write_guard_roots")
        or []
    )
    binding_writable_roots = binding_map.get("writable_roots") or []
    roots: list[Path] = []
    raw_roots: list[Any] = [
        paths_map.get("prompt"),
        paths_map.get("system_prompt"),
        paths_map.get("schema"),
        paths_map.get("result"),
        paths_map.get("stdout"),
        paths_map.get("stderr"),
        paths_map.get("sandbox_profile"),
        *(manifest.get(_RUNTIME_SCRATCH_ROOTS_KEY) or []),
        *manifest_write_guard_roots,
    ]
    if write_guard_scope == "diagnostic":
        raw_roots.append(manifest.get("cwd"))
        raw_roots.extend(binding_writable_roots)
    elif not manifest_write_guard_roots:
        raw_roots.extend(binding_writable_roots)
    for raw in raw_roots:
        if not str(raw or "").strip():
            continue
        path = Path(str(raw)).expanduser()
        if path.suffix and not path.exists():
            path = path.parent
        elif path.exists() and path.is_file():
            path = path.parent
        roots.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve(strict=False)
        except OSError:
            resolved = root.absolute()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _authority_grants_from_manifest(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
    binding_map = dict(binding) if isinstance(binding, Mapping) else {}
    raw_grants = (
        binding_map.get("runtime_workspace_authority_grants")
        or manifest.get("runtime_workspace_authority_grants")
        or []
    )
    if not isinstance(raw_grants, list):
        return []
    return [dict(item) for item in raw_grants if isinstance(item, Mapping)]


def _authority_schema_version(manifest: Mapping[str, Any]) -> str:
    binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
    binding_map = dict(binding) if isinstance(binding, Mapping) else {}
    return str(
        binding_map.get("authority_schema_version")
        or manifest.get("authority_schema_version")
        or ""
    )


def _authority_grant_digest(manifest: Mapping[str, Any]) -> str:
    binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
    binding_map = dict(binding) if isinstance(binding, Mapping) else {}
    return str(
        binding_map.get("runtime_workspace_authority_grant_digest")
        or manifest.get("runtime_workspace_authority_grant_digest")
        or ""
    )


def _grant_payload_digest(grant: Mapping[str, Any]) -> str:
    payload = dict(grant)
    payload.pop("grant_digest", None)
    return _stable_json_digest(payload)


def _validate_workspace_authority_grants(
    manifest: Mapping[str, Any],
    binding: Mapping[str, Any],
    *,
    role_name: str,
    cwd: Path,
) -> None:
    if _authority_schema_version(manifest) != _AUTHORITY_GRANT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Bound Claude write role {role_name} is missing runtime workspace authority grant"
        )
    grants = _authority_grants_from_manifest(manifest)
    if not grants:
        raise RuntimeError(
            f"Bound Claude write role {role_name} is missing runtime workspace authority grant"
        )
    expected_digest = _authority_grant_digest(manifest)
    actual_digest = _stable_json_digest(grants)
    if expected_digest and expected_digest != actual_digest:
        raise RuntimeError(
            f"Bound Claude write role {role_name} has invalid runtime workspace authority grant digest"
        )
    binding_write_guard_roots = {
        str(Path(str(path)).expanduser().resolve(strict=False))
        for path in binding.get("write_guard_roots", [])
        if str(path).strip()
    }
    grant_write_guard_roots: set[str] = set()
    grant_repo_roots: set[str] = set()
    grant_types: set[str] = set()
    for grant in grants:
        if str(grant.get("schema_version") or "") != _AUTHORITY_GRANT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Bound Claude write role {role_name} has unsupported runtime workspace authority grant"
            )
        if str(grant.get("grant_digest") or "") != _grant_payload_digest(grant):
            raise RuntimeError(
                f"Bound Claude write role {role_name} has tampered runtime workspace authority grant"
            )
        grant_type = str(grant.get("grant_type") or "")
        grant_types.add(grant_type)
        promotable = bool(grant.get("promotable"))
        if grant_type == "diagnostic" and promotable:
            raise RuntimeError(
                f"Bound Claude write role {role_name} diagnostic grant cannot be promotable"
            )
        if grant_type in {"product", "repair"} and not promotable:
            raise RuntimeError(
                f"Bound Claude write role {role_name} product grant must be promotable"
            )
        repo_root = Path(str(grant.get("repo_root") or "")).expanduser()
        if not repo_root.is_absolute() or not repo_root.exists() or repo_root.is_symlink():
            raise RuntimeError(
                f"Bound Claude write role {role_name} has invalid authority repo root"
            )
        repo_root = repo_root.resolve(strict=True)
        grant_repo_roots.add(str(repo_root))
        if not _is_relative_to(cwd.resolve(strict=True), repo_root):
            continue
        for raw_root in grant.get("write_guard_roots", []) or []:
            root = Path(str(raw_root)).expanduser().resolve(strict=False)
            if not _is_relative_to(root, repo_root):
                raise RuntimeError(
                    f"Bound Claude write role {role_name} authority write root escapes repo"
                )
            grant_write_guard_roots.add(str(root))
    if not any(
        _is_relative_to(cwd.resolve(strict=True), Path(root))
        for root in grant_repo_roots
    ):
        raise RuntimeError(
            f"Bound Claude write role {role_name} cwd is outside authority repo roots"
        )
    if not grant_write_guard_roots:
        raise RuntimeError(
            f"Bound Claude write role {role_name} authority grant has no write roots"
        )
    if binding_write_guard_roots and binding_write_guard_roots != grant_write_guard_roots:
        raise RuntimeError(
            f"Bound Claude write role {role_name} write guard roots do not match authority grant"
        )
    scope = str(binding.get("write_guard_scope") or manifest.get("write_guard_scope") or "contract")
    if scope == "diagnostic":
        if grant_types != {"diagnostic"}:
            raise RuntimeError(
                f"Bound Claude write role {role_name} diagnostic scope requires diagnostic grant"
            )
    elif not (grant_types & {"product", "repair"}):
        raise RuntimeError(
            f"Bound Claude write role {role_name} product scope requires promotable grant"
        )


def _write_sandbox_exec_profile(manifest: Mapping[str, Any]) -> Path:
    paths = manifest.get("paths")
    paths_map = dict(paths) if isinstance(paths, Mapping) else {}
    profile_text = str(paths_map.get("sandbox_profile") or "").strip()
    if not profile_text:
        raise RuntimeError("Bound Claude pool job is missing sandbox-exec profile path")
    profile_path = Path(profile_text).expanduser()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    allowed_roots = _write_guard_roots(manifest)
    allow_lines = "\n".join(
        f'  (subpath "{_sandbox_profile_quote(root)}")'
        for root in allowed_roots
    )
    profile = (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        "(allow file-write*\n"
        f"{allow_lines}\n"
        ")\n"
    )
    profile_path.write_text(profile, encoding="utf-8")
    return profile_path


def _coerce_aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = _parse_iso(value.strip())
    else:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)
    return cleaned.strip("-") or "default"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid JSON in %s", path)
        return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    with suppress(OSError):
        os.chmod(tmp, 0o664)
    os.replace(tmp, path)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        os.chmod(path, 0o775)


def _apply_runner_umask(raw_umask: str = DEFAULT_RUNNER_UMASK) -> int:
    """Make files created by Claude child processes group-writable.

    Claude pool jobs run as different macOS users inside one shared feature
    worktree. A default shell-style umask of 022 creates 755 directories, which
    blocks sibling Claude accounts from writing follow-up files in the same
    tree. Setting the runner process umask to 0002 makes child-created
    directories 775 and files 664 while still avoiding world-writable output.
    """
    try:
        umask = int(str(raw_umask), 8)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid IRIAI_CLAUDE_POOL_RUNNER_UMASK={raw_umask!r}; expected octal like 0002"
        ) from exc
    os.umask(umask)
    return umask


def _profile_entries_from_env() -> list[str]:
    raw = os.environ.get("IRIAI_CLAUDE_POOL_PROFILES", "")
    if raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(DEFAULT_PROFILE_NAMES)


def _default_profile_weight(name: str) -> float:
    return DEFAULT_PROFILE_WEIGHTS.get(name, 1.0)


def _coerce_profile_weight(raw: Any, *, name: str) -> float:
    if raw is None or raw == "":
        return _default_profile_weight(name)
    try:
        weight = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Claude pool weight for {name}: {raw!r}") from exc
    if not math.isfinite(weight) or weight <= 0:
        raise ValueError(f"Invalid Claude pool weight for {name}: {raw!r}")
    return weight


def _coerce_profile(entry: Any) -> ClaudePoolProfile:
    if isinstance(entry, str):
        return ClaudePoolProfile(name=entry, user=entry, weight=_default_profile_weight(entry))
    if isinstance(entry, dict):
        name = str(entry.get("name") or entry.get("user") or "").strip()
        if not name:
            raise ValueError(f"Invalid Claude pool profile entry: {entry!r}")
        # Heterogeneous pool members. ``kind`` defaults to ``"claude"`` so
        # existing entries are unchanged; ``"codex"`` members are dispatched
        # in-process and need no real OS ``user`` (default it to the name).
        kind = str(entry.get("kind") or "claude").strip().lower()
        return ClaudePoolProfile(
            name=name,
            user=str(entry.get("user") or name),
            claude_command=str(entry.get("claude_command") or DEFAULT_CLAUDE_COMMAND),
            weight=_coerce_profile_weight(entry.get("weight"), name=name),
            kind=kind,
        )
    raise ValueError(f"Invalid Claude pool profile entry: {entry!r}")


def _profile_entry_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("user") or "").strip()
    return ""


def _profile_entry_weight(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    raw = entry.get("weight")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _migrate_legacy_default_profile_weights(entries: Any) -> tuple[Any, bool]:
    """Move generated old default configs to the current active-profile set.

    Existing pool installs have explicit entries in profiles.json, so changing
    only DEFAULT_PROFILE_NAMES / DEFAULT_PROFILE_WEIGHTS would not affect them.
    Only known generated defaults are rewritten; custom profiles continue to
    win.
    """
    if not isinstance(entries, list):
        return entries, False

    names = [_profile_entry_name(entry) for entry in entries]
    generated_defaults = (
        (DEPRECATED_DEFAULT_PROFILE_NAMES, DEPRECATED_DEFAULT_PROFILE_WEIGHT_SETS),
        (LEGACY_DEFAULT_PROFILE_NAMES, LEGACY_DEFAULT_PROFILE_WEIGHT_SETS),
    )

    matched_names: tuple[str, ...] | None = None
    matched_weight_sets: tuple[dict[str, float], ...] = ()
    for candidate_names, candidate_weight_sets in generated_defaults:
        if names == list(candidate_names):
            matched_names = candidate_names
            matched_weight_sets = candidate_weight_sets
            break
    if matched_names is None:
        return entries, False

    by_name = dict(zip(names, entries, strict=False))
    for name in matched_names:
        entry = by_name.get(name)
        if isinstance(entry, str):
            continue
        if not isinstance(entry, dict):
            return entries, False
        user = str(entry.get("user") or name)
        command = str(entry.get("claude_command") or DEFAULT_CLAUDE_COMMAND)
        if user != name or command != DEFAULT_CLAUDE_COMMAND:
            return entries, False
        weight = _profile_entry_weight(entry)
        if weight is None:
            continue
        if not any(math.isclose(weight, weights[name]) for weights in matched_weight_sets):
            return entries, False

    migrated: list[Any] = []
    for name in DEFAULT_PROFILE_NAMES:
        entry = by_name.get(name)
        command = (
            str(entry.get("claude_command"))
            if isinstance(entry, dict) and entry.get("claude_command")
            else DEFAULT_CLAUDE_COMMAND
        )
        migrated.append(
            {
                "name": name,
                "user": name,
                "claude_command": command,
                "weight": DEFAULT_PROFILE_WEIGHTS[name],
            }
        )
    return migrated, True


def load_profiles(root: Path = DEFAULT_POOL_ROOT) -> list[ClaudePoolProfile]:
    config_path = root / "profiles.json"
    loaded_config = config_path.exists()
    if config_path.exists():
        data = _read_json(config_path, {})
        entries = data.get("profiles", data) if isinstance(data, dict) else data
    else:
        entries = _profile_entries_from_env()
    entries, migrated_defaults = _migrate_legacy_default_profile_weights(entries)
    profiles = [_coerce_profile(entry) for entry in entries]
    if not profiles:
        raise RuntimeError("Claude pool has no configured profiles")
    if loaded_config and migrated_defaults:
        _write_json_atomic(
            config_path,
            {
                "profiles": [
                    {
                        "name": profile.name,
                        "user": profile.user,
                        "claude_command": profile.claude_command,
                        "weight": profile.weight,
                        "kind": profile.kind,
                    }
                    for profile in profiles
                ]
            },
        )
    return profiles


def _prune_unconfigured_profile_state(
    root: Path,
    profiles: list[ClaudePoolProfile],
) -> None:
    state_path = _profile_state_path(root)
    if not state_path.exists():
        return
    state = _read_json(state_path, {})
    profile_state = state.get("profiles")
    if not isinstance(profile_state, dict):
        return
    configured = {profile.name for profile in profiles}
    removed = False
    for profile_name in list(profile_state):
        if profile_name not in configured:
            profile_state.pop(profile_name, None)
            removed = True
    if removed:
        _write_json_atomic(state_path, state)


def ensure_pool_layout(
    root: Path = DEFAULT_POOL_ROOT,
    profiles: list[ClaudePoolProfile] | None = None,
) -> list[ClaudePoolProfile]:
    profiles = profiles or load_profiles(root)
    _ensure_dir(root)
    for relative in ("jobs", "payloads", "heartbeats", "logs", "launchagents"):
        _ensure_dir(root / relative)
    for profile in profiles:
        for state in ("queued", "running", "done", "failed"):
            _ensure_dir(root / "jobs" / state / profile.name)
    profiles_path = root / "profiles.json"
    if not profiles_path.exists():
        _write_json_atomic(
            profiles_path,
            {
                "profiles": [
                    {
                        "name": profile.name,
                        "user": profile.user,
                        "claude_command": profile.claude_command,
                        "weight": profile.weight,
                        "kind": profile.kind,
                    }
                    for profile in profiles
                ]
            },
        )
    _prune_unconfigured_profile_state(root, profiles)
    return profiles


def _job_filename(job_id: str) -> str:
    return f"{job_id}.json"


def _job_state_path(root: Path, state: str, profile: str, job_id: str) -> Path:
    return root / "jobs" / state / profile / _job_filename(job_id)


def _profile_state_path(root: Path) -> Path:
    return root / "profile_state.json"


def _payload_dir(root: Path, job_id: str) -> Path:
    return root / "payloads" / job_id[:2] / job_id


def find_late_completed_claude_pool_job(
    *,
    root: Path | str = DEFAULT_POOL_ROOT,
    feature_id: str,
    group_idx: int,
    task_id: str,
    attempt_id: int,
    sandbox_id: str = "",
    sandbox_ids: Iterable[str] | None = None,
    invocation_id: str | None = None,
    idempotency_key: str | None = None,
    output_schema_digest: str | None = None,
    output_type_name: str | None = None,
) -> ClaudePoolLateCompletion | None:
    """Find one completed pool job that strictly matches a timed-out attempt."""

    root_path = Path(root)
    expected_sandbox_ids: list[str] = []
    for raw in (sandbox_id, *(sandbox_ids or ())):
        value = str(raw or "").strip()
        if value and value not in expected_sandbox_ids:
            expected_sandbox_ids.append(value)
    if not expected_sandbox_ids:
        return None
    matches: list[ClaudePoolLateCompletion] = []
    for done_path in sorted(
        (root_path / "jobs" / "done").glob("*/*.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    ):
        manifest = _read_json(done_path, {})
        candidate = _late_completion_from_manifest(
            manifest,
            done_path=done_path,
            feature_id=feature_id,
            group_idx=group_idx,
            task_id=task_id,
            attempt_id=attempt_id,
            sandbox_ids=expected_sandbox_ids,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            output_schema_digest=output_schema_digest,
            output_type_name=output_type_name,
        )
        if candidate is not None:
            matches.append(candidate)
            if len(matches) > 1:
                return None
    return matches[0] if matches else None


def _late_completion_from_manifest(
    manifest: Mapping[str, Any],
    *,
    done_path: Path,
    feature_id: str,
    group_idx: int,
    task_id: str,
    attempt_id: int,
    sandbox_ids: Iterable[str],
    invocation_id: str | None,
    idempotency_key: str | None,
    output_schema_digest: str | None,
    output_type_name: str | None,
) -> ClaudePoolLateCompletion | None:
    if manifest.get("kind") != "claude" or manifest.get("status") != "done":
        return None
    if str(manifest.get("feature_id") or "") != str(feature_id):
        return None
    binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
    if not isinstance(binding, Mapping):
        return None
    if _optional_int(binding.get("attempt_id")) != int(attempt_id):
        return None
    actual_sandbox_id = str(binding.get("sandbox_id") or manifest.get("sandbox_id") or "")
    if actual_sandbox_id not in {str(item) for item in sandbox_ids}:
        return None
    role_metadata = binding.get("role_metadata")
    if not isinstance(role_metadata, Mapping):
        return None
    if _optional_int(role_metadata.get("group_idx")) != int(group_idx):
        return None
    task_ids = {str(item) for item in (role_metadata.get("task_ids") or [])}
    if str(task_id) not in task_ids:
        return None
    if invocation_id and _manifest_value_present(manifest, "invocation_id"):
        if str(manifest.get("invocation_id") or "") != str(invocation_id):
            return None
    if idempotency_key and _manifest_value_present(manifest, "dispatch_idempotency_key"):
        if str(manifest.get("dispatch_idempotency_key") or "") != str(idempotency_key):
            return None

    schema_validation = _schema_digest_validation(
        manifest,
        expected_digest=output_schema_digest,
        output_type_name=output_type_name,
    )
    if schema_validation is None:
        return None

    paths = manifest.get("paths")
    if not isinstance(paths, Mapping):
        return None
    result_path_text = str(paths.get("result") or "")
    if not result_path_text:
        return None
    result_path = Path(result_path_text)
    result = _read_json(result_path, {})
    if not bool(result.get("ok")):
        return None
    structured = result.get("structured_output")
    if not isinstance(structured, Mapping):
        return None
    structured_output = dict(structured)
    if str(structured_output.get("task_id") or "") != str(task_id):
        return None
    return ClaudePoolLateCompletion(
        job_id=str(manifest.get("id") or done_path.stem),
        profile=str(manifest.get("profile") or done_path.parent.name),
        done_path=str(done_path),
        result_path=str(result_path),
        stdout_path=str(paths.get("stdout") or "") or None,
        stderr_path=str(paths.get("stderr") or "") or None,
        manifest=dict(manifest),
        result=dict(result),
        result_text=str(result.get("result_text") or ""),
        structured_output=structured_output,
        raw=result.get("raw"),
        schema_digest_validation=schema_validation,
    )


def _manifest_value_present(manifest: Mapping[str, Any], key: str) -> bool:
    value = manifest.get(key)
    return value is not None and str(value) != ""


def _schema_digest_validation(
    manifest: Mapping[str, Any],
    *,
    expected_digest: str | None,
    output_type_name: str | None,
) -> str | None:
    expected = str(expected_digest or "")
    manifest_digest = str(manifest.get("output_schema_digest") or "")
    if manifest_digest:
        return "matched_manifest" if not expected or manifest_digest == expected else None
    if not expected:
        return "not_required"
    paths = manifest.get("paths")
    schema_path_text = str(paths.get("schema") or "") if isinstance(paths, Mapping) else ""
    if not schema_path_text:
        return None
    schema_path = Path(schema_path_text)
    try:
        schema_text = schema_path.read_text(encoding="utf-8")
    except OSError:
        return None
    digests = {hashlib.sha256(schema_text.encode("utf-8")).hexdigest()}
    try:
        digests.add(_stable_json_digest(json.loads(schema_text)))
    except json.JSONDecodeError:
        pass
    if expected in digests:
        return "matched_schema_file"
    if output_type_name and expected == _stable_json_digest(str(output_type_name)):
        return "matched_legacy_type_name"
    return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classify_claude_pool_error(error: str) -> str | None:
    lowered = error.lower()
    if "overloaded_error" in lowered or '"message":"overloaded"' in lowered or "overloaded" in lowered:
        return "overloaded"
    if (
        "internal server error" in lowered
        or '"type":"api_error"' in lowered
        or '"type": "api_error"' in lowered
        or "'type': 'api_error'" in lowered
        # Codex/OpenAI transient surfaces (additive; never matched by Claude
        # accounts' own error text in the cases above).
        or "rate limit" in lowered
        or "too many requests" in lowered
    ):
        return "transient_api_error"
    if (
        "monthly usage limit" in lowered
        or "usage limit" in lowered
        or "out of extra usage" in lowered
        or "rate_limit_error" in lowered
        or "api_error_status=429" in lowered
        or '"api_error_status":429' in lowered
        or "api_error_status': 429" in lowered
        # Codex/OpenAI usage-cap surfaces (additive). A usage-limited codex
        # member is cooled down + failed-over exactly like a claude account.
        or "usage limit reached" in lowered
        or "you've reached your usage limit" in lowered
        or "quota" in lowered
        or "insufficient_quota" in lowered
        or "plan limit" in lowered
        or "resets at" in lowered
    ):
        return "usage_limited"
    if "login" in lowered or "not logged" in lowered or "auth" in lowered:
        return "auth_failed"
    return None


def _probe_delay_seconds_for_error(kind: str) -> float:
    if kind in {"overloaded", "transient_api_error"}:
        return DEFAULT_OVERLOAD_PROBE_AFTER_SECONDS
    if kind == "auth_failed":
        return DEFAULT_AUTH_PROBE_AFTER_SECONDS
    if kind == "probe_failed":
        return DEFAULT_PROBE_FAILED_AFTER_SECONDS
    return DEFAULT_LIMIT_PROBE_AFTER_SECONDS


def _profile_probe_after(record: dict[str, Any]) -> datetime | None:
    # Accept the old cooldown field so live state from previous bridge attempts
    # is treated as unavailable until it is probed and cleared.
    return _parse_iso(str(record.get("probe_after") or record.get("cooldown_until") or ""))


def _profile_unavailable_active(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(record.get("status") or "") not in {"cooldown", "unavailable", "limited"}:
        return False
    until = _profile_probe_after(record)
    if until is None:
        return False
    return until > (now or datetime.now(UTC))


def _profile_probe_due(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(record.get("status") or "") not in {"cooldown", "unavailable", "limited"}:
        return False
    until = _profile_probe_after(record)
    if until is None:
        return True
    return until <= (now or datetime.now(UTC))


def _extract_cost_and_tokens(result: dict[str, Any]) -> tuple[float, int]:
    raw = result.get("raw")
    payload = raw if isinstance(raw, dict) else result
    cost = 0.0
    tokens = 0
    try:
        cost = float(payload.get("total_cost_usd") or result.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if isinstance(usage, dict):
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            try:
                tokens += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                pass
    model_usage = payload.get("modelUsage") if isinstance(payload, dict) else None
    if isinstance(model_usage, dict):
        for item in model_usage.values():
            if not isinstance(item, dict):
                continue
            if not cost:
                try:
                    cost += float(item.get("costUSD") or 0.0)
                except (TypeError, ValueError):
                    pass
            for key in (
                "inputTokens",
                "outputTokens",
                "cacheCreationInputTokens",
                "cacheReadInputTokens",
            ):
                try:
                    tokens += int(item.get(key) or 0)
                except (TypeError, ValueError):
                    pass
    return cost, tokens


def _extract_result_text(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("result", "text", "message", "content"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts: list[str] = []
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item, str):
                        parts.append(item)
                if parts:
                    return "\n".join(parts)
        if raw:
            return json.dumps(raw)
    if isinstance(raw, str):
        return raw
    return "" if raw is None else str(raw)


def _extract_structured_output(raw: Any, result_text: str) -> Any:
    if isinstance(raw, dict):
        for key in ("structured_output", "output", "json"):
            value = raw.get(key)
            if value is not None:
                return value
        result = raw.get("result")
        if isinstance(result, dict):
            return result
    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed


def _looks_logged_in(text: str) -> bool:
    lowered = text.lower()
    if "loggedin" in lowered and "true" in lowered:
        return True
    if "logged in" in lowered and "not logged" not in lowered:
        return True
    return False


class ClaudePoolRuntime(AgentRuntime):
    """Claude CLI runtime that dispatches jobs to per-user GUI-session runners."""

    name = "claude_pool"

    def __init__(
        self,
        session_store: SessionStore | None = None,
        on_message: Callable[[Any], None] | None = None,
        *,
        interactive_roles: set[str] | None = None,
        root: Path | str | None = None,
        profiles: list[ClaudePoolProfile] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        job_stale_timeout: float = DEFAULT_JOB_STALE_TIMEOUT_SECONDS,
        job_absolute_timeout: float = DEFAULT_JOB_ABSOLUTE_TIMEOUT_SECONDS,
    ) -> None:
        self.root = Path(root) if root else DEFAULT_POOL_ROOT
        self.profiles = ensure_pool_layout(self.root, profiles)
        self.session_store = session_store
        self.on_message = on_message
        self._interactive_roles = interactive_roles or set()
        self._poll_interval = poll_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._job_stale_timeout = job_stale_timeout
        self._job_absolute_timeout = job_absolute_timeout
        self._affinity_lock = asyncio.Lock()
        self._invocation_jobs: dict[str, set[str]] = {}
        self._feature_sessions: dict[str, str] = {}
        self._queued_user_notes: dict[str, list[str]] = {}
        # In-memory concurrent-load counter for codex members (no job-queue dir).
        self._codex_active: dict[str, int] = {}
        # Lazily build the embedded codex runtime ONLY when a codex member is
        # configured, so a pure-claude pool keeps zero Codex-CLI dependency and
        # stays byte-identical to claude_pool.
        self._codex_runtime: Any | None = None
        if any(profile.kind == "codex" for profile in self.profiles):
            from .codex import CodexAgentRuntime

            self._codex_runtime = CodexAgentRuntime(
                session_store=session_store,
                on_message=on_message,
                interactive_roles=self._interactive_roles,
            )

    @asynccontextmanager
    async def bind_invocation(self, invocation_id: str, activity_sink: Any | None):
        del activity_sink
        token = _current_invocation_var.set(invocation_id)
        self._invocation_jobs.setdefault(invocation_id, set())
        try:
            yield
        finally:
            _current_invocation_var.reset(token)
            self._invocation_jobs.pop(invocation_id, None)

    def invocation_has_live_work(self, invocation_id: str) -> bool:
        job_ids = self._invocation_jobs.get(invocation_id, set())
        return any(self._job_is_live(job_id) for job_id in job_ids)

    def _job_is_live(self, job_id: str) -> bool:
        for profile in self.profiles:
            queued = _job_state_path(self.root, "queued", profile.name, job_id)
            if queued.exists():
                return True
            running = _job_state_path(self.root, "running", profile.name, job_id)
            if running.exists():
                manifest = _read_json(running, {})
                heartbeat = _parse_iso(manifest.get("heartbeat_at") or manifest.get("updated_at"))
                if heartbeat is None:
                    return True
                age = (datetime.now(UTC) - heartbeat).total_seconds()
                return age <= self._heartbeat_timeout
        return False

    async def invoke(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None = None,
        workspace: Workspace | None = None,
        session_key: str | None = None,
    ) -> str | BaseModel:
        feature_id = session_key.rsplit(":", 1)[-1] if session_key else None
        max_chars = int(role.metadata.get("max_session_chars", 0) or 0)
        persistent = bool(session_key and max_chars)

        if feature_id and session_key and (persistent or role.name in self._interactive_roles):
            self._feature_sessions[feature_id] = session_key

        session: AgentSession | None = None
        if session_key and self.session_store:
            if not persistent:
                await self.session_store.delete(session_key)
            else:
                session = await self.session_store.load(session_key)

        profile = await self._select_profile(
            session_key=session_key,
            persistent=persistent,
            exclude_kinds=self._excluded_kinds_for_role(role),
        )
        effective_prompt = self._compose_prompt(
            role,
            prompt,
            feature_id=feature_id,
            session=session,
            output_type=output_type,
        )

        if not output_type:
            final_text, structured_output, raw = await self._submit_and_wait_with_failover(
                role,
                effective_prompt,
                output_type=None,
                workspace=workspace,
                session_key=session_key,
                profile=profile,
                persistent=persistent,
            )
            await self._save_session_turn(session_key, session, final_text)
            self._emit_completion(final_text, structured_output)
            return final_text

        final_text = ""
        structured_output: Any = None
        raw: Any = None
        last_error: Exception | None = None
        attempt_prompt = effective_prompt
        for attempt in range(3):
            final_text, structured_output, raw = await self._submit_and_wait_with_failover(
                role,
                attempt_prompt,
                output_type=output_type,
                workspace=workspace,
                session_key=session_key,
                profile=profile,
                persistent=persistent,
            )
            try:
                payload = structured_output
                if payload is None:
                    payload = json.loads(final_text)
                result = output_type.model_validate(payload)
                await self._save_session_turn(session_key, session, final_text)
                self._emit_completion(final_text, payload)
                return result
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    break
                attempt_prompt = (
                    f"Your previous response was not valid JSON for {output_type.__name__}. "
                    f"Error: {exc}\n\n"
                    "Please output ONLY valid JSON matching the schema.\n\n"
                    f"Previous response:\n{final_text}"
                )

        await self._save_session_turn(session_key, session, final_text)
        self._emit_completion(final_text, structured_output)
        fallback = self._structured_fallback(output_type, session_key, final_text, last_error)
        if fallback is not None:
            return fallback
        raise RuntimeError(
            f"Claude pool failed to return valid JSON for {output_type.__name__} "
            f"after 3 attempts: {last_error}"
        )

    def _compose_prompt(
        self,
        role: Role,
        prompt: str,
        *,
        feature_id: str | None,
        session: AgentSession | None,
        output_type: type[BaseModel] | None,
    ) -> str:
        sections = [
            "You are running as an agent inside the iriai-build-v2 workflow engine.",
        ]
        notes = self._consume_user_notes(feature_id)
        if notes:
            sections.append(
                "## User Notes Since The Last Agent Turn\n"
                + "\n".join(f"- {note}" for note in notes)
            )
        prior = self._fallback_session_context(role, session)
        if prior:
            sections.append(prior)
        if output_type:
            sections.append(
                f"## Output Contract\nReturn JSON matching the {output_type.__name__} schema."
            )
        sections.append(f"## Current Task\n{prompt}")
        return "\n\n".join(section for section in sections if section.strip())

    def _fallback_session_context(self, role: Role, session: AgentSession | None) -> str:
        if not session:
            return ""
        turns = session.metadata.get("turns", [])
        if not turns:
            return ""
        keep_recent = max(int(role.metadata.get("keep_recent_messages", 6) or 6) * 2, 8)
        rendered: list[str] = []
        for turn in turns[-keep_recent:]:
            who = str(turn.get("role", "assistant")).title()
            text = str(turn.get("text", "")).strip()
            if text:
                rendered.append(f"{who}: {text}")
        if not rendered:
            return ""
        return "## Prior Conversation\n" + "\n\n".join(rendered)

    def _consume_user_notes(self, feature_id: str | None) -> list[str]:
        if not feature_id:
            return []
        return self._queued_user_notes.pop(feature_id, [])

    def _excluded_kinds_for_role(self, role: Role) -> frozenset[str]:
        """Kinds that cannot serve *role* and must be skipped during selection.

        A BOUND write-producing role (one carrying a runtime workspace binding)
        is excluded from codex members: the binding was minted with
        runtime == "claude_pool" and CodexAgentRuntime rejects bound write roles
        whose binding runtime != "codex". Everything else (read roles,
        structured-output roles, and binding-less write roles such as planning
        artifact authors) can rotate to / spill to codex co-equally.
        """
        binding = _runtime_workspace_binding(role)
        if binding and _role_is_write_producing(role):
            return frozenset({"codex"})
        return frozenset()

    async def _select_profile(
        self,
        *,
        session_key: str | None,
        persistent: bool,
        exclude_kinds: frozenset[str] = frozenset(),
    ) -> ClaudePoolProfile:
        async with self._affinity_lock:
            path = self.root / "affinity.json"
            data = _read_json(path, {"next_index": 0, "session_profiles": {}})
            self._sync_affinity_weights(data)
            session_profiles = data.setdefault("session_profiles", {})
            names = [profile.name for profile in self.profiles]
            state = _read_json(_profile_state_path(self.root), {})

            if persistent and session_key:
                existing = session_profiles.get(session_key)
                if existing in names:
                    state = await self._refresh_due_profile_state(state, [existing])
                existing_kind = (
                    self._profile_by_name(existing).kind if existing in names else None
                )
                if (
                    existing in names
                    and (existing_kind not in exclude_kinds)
                    and not self._profile_is_unavailable(existing, state)
                ):
                    profile = self._profile_by_name(existing)
                    self._record_profile_dispatch(data, profile)
                    _write_json_atomic(path, data)
                    return profile

            state = await self._refresh_due_profile_state(state, names)
            profile = await self._select_best_available_profile(
                data, state, exclude_kinds=exclude_kinds
            )
            selected_index = names.index(profile.name)
            data["next_index"] = (selected_index + 1) % len(self.profiles)
            if persistent and session_key:
                session_profiles[session_key] = profile.name
            self._record_profile_dispatch(data, profile)
            _write_json_atomic(path, data)
            return profile

    def _sync_affinity_weights(self, affinity_data: dict[str, Any]) -> None:
        weights = {profile.name: profile.weight for profile in self.profiles}
        if affinity_data.get("profile_weights") != weights:
            affinity_data["profile_weights"] = weights
            affinity_data["profile_dispatch_counts"] = {}
        else:
            counts = affinity_data.get("profile_dispatch_counts")
            if isinstance(counts, dict):
                for profile_name in list(counts):
                    if profile_name not in weights:
                        counts.pop(profile_name, None)
        session_profiles = affinity_data.get("session_profiles")
        if isinstance(session_profiles, dict):
            for session_key, profile_name in list(session_profiles.items()):
                if profile_name not in weights:
                    session_profiles.pop(session_key, None)

    def _record_profile_dispatch(
        self,
        affinity_data: dict[str, Any],
        profile: ClaudePoolProfile,
    ) -> None:
        counts = affinity_data.setdefault("profile_dispatch_counts", {})
        if not isinstance(counts, dict):
            counts = {}
            affinity_data["profile_dispatch_counts"] = counts
        try:
            current = int(counts.get(profile.name) or 0)
        except (TypeError, ValueError):
            current = 0
        counts[profile.name] = current + 1

    def _profile_by_name(self, name: str) -> ClaudePoolProfile:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        raise KeyError(name)

    def _profile_is_unavailable(self, profile_name: str, state: dict[str, Any] | None = None) -> bool:
        state = state if state is not None else _read_json(_profile_state_path(self.root), {})
        record = (state.get("profiles") or {}).get(profile_name)
        return isinstance(record, dict) and _profile_unavailable_active(record)

    async def _select_best_available_profile(
        self,
        affinity_data: dict[str, Any],
        state: dict[str, Any],
        *,
        exclude_kinds: frozenset[str] = frozenset(),
    ) -> ClaudePoolProfile:
        names = [profile.name for profile in self.profiles]
        next_index = int(affinity_data.get("next_index", 0) or 0)
        profile_state = state.get("profiles") if isinstance(state, dict) else {}
        now = datetime.now(UTC)
        available: list[ClaudePoolProfile] = []
        unavailable: list[tuple[datetime, ClaudePoolProfile]] = []
        for profile in self.profiles:
            # Bound-write authority filter (additive). A codex member's embedded
            # CodexAgentRuntime validates that a write-producing binding carries
            # runtime == "codex"; the pool mints runtime == "claude_pool", so a
            # BOUND write-producing role cannot run on codex. Exclude codex from
            # selection for those roles only. Read roles, structured-output
            # roles, and binding-less write roles still rotate/spill to codex.
            if profile.kind in exclude_kinds:
                continue
            record = profile_state.get(profile.name) if isinstance(profile_state, dict) else None
            if isinstance(record, dict) and _profile_unavailable_active(record, now=now):
                until = _profile_probe_after(record) or now
                unavailable.append((until, profile))
            else:
                available.append(profile)

        if not available and unavailable:
            # If every account is currently marked limited, cycle cheap probes
            # before burning a real implementation/verification request.
            for _until, profile in sorted(unavailable, key=lambda item: item[0]):
                if await self._probe_profile_available(profile):
                    state = _read_json(_profile_state_path(self.root), {})
                    profile_state = state.get("profiles") if isinstance(state, dict) else {}
                    record = (
                        profile_state.get(profile.name)
                        if isinstance(profile_state, dict) else None
                    )
                    if not (isinstance(record, dict) and _profile_unavailable_active(record)):
                        available.append(profile)
                        break

        if not available:
            next_probe = min((until for until, _profile in unavailable), default=now)
            raise RuntimeError(
                "No Claude pool profile is currently available; "
                f"next readiness probe after {next_probe.isoformat()}"
            )

        def _tie_index(profile: ClaudePoolProfile) -> int:
            idx = names.index(profile.name)
            return (idx - next_index) % len(names)

        counts = affinity_data.get("profile_dispatch_counts")
        dispatch_counts = counts if isinstance(counts, dict) else {}

        def _weighted_score(profile: ClaudePoolProfile) -> float:
            try:
                dispatch_count = float(dispatch_counts.get(profile.name) or 0.0)
            except (TypeError, ValueError):
                dispatch_count = 0.0
            return (self._profile_load_score(profile.name) + dispatch_count) / profile.weight

        return min(available, key=lambda profile: (_weighted_score(profile), _tie_index(profile)))

    async def _refresh_due_profile_state(
        self,
        state: dict[str, Any],
        profile_names: list[str],
    ) -> dict[str, Any]:
        profile_state = state.get("profiles") if isinstance(state, dict) else {}
        if not isinstance(profile_state, dict):
            return state
        now = datetime.now(UTC)
        for profile_name in profile_names:
            record = profile_state.get(profile_name)
            if not isinstance(record, dict) or not _profile_probe_due(record, now=now):
                continue
            try:
                await self._probe_profile_available(self._profile_by_name(profile_name))
            finally:
                state = _read_json(_profile_state_path(self.root), {})
                profile_state = state.get("profiles") if isinstance(state, dict) else {}
                if not isinstance(profile_state, dict):
                    break
        return state

    async def _probe_codex_available(self, profile: ClaudePoolProfile) -> bool:
        """Dynamic, restart-free recovery probe for a codex member.

        Runs a tiny throwaway in-process codex turn. On success the member's
        unavailable record is cleared (it rejoins the pool); on failure it is
        re-marked with the classified reason so cooldown/re-probe continues.
        """
        if self._codex_runtime is None:
            return False
        from iriai_compose.actors import Role as _Role

        probe_role = _Role(
            name=f"{profile.name}-availability-probe",
            prompt="Reply with exactly OK.",
            metadata={},
        )
        try:
            await asyncio.wait_for(
                self._codex_runtime.invoke(
                    probe_role,
                    DEFAULT_AVAILABILITY_PROBE_PROMPT,
                    output_type=None,
                    workspace=None,
                    session_key=None,
                ),
                timeout=DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            kind = _classify_claude_pool_error(str(exc)) or "probe_failed"
            self._mark_profile_unavailable(profile, kind, str(exc))
            logger.info(
                "Claude pool codex member %s readiness probe failed: %s",
                profile.name,
                exc,
            )
            return False
        self._clear_profile_unavailable(profile.name)
        logger.info(
            "Claude pool codex member %s readiness probe succeeded", profile.name
        )
        return True

    async def _probe_profile_available(self, profile: ClaudePoolProfile) -> bool:
        if profile.kind == "codex":
            return await self._probe_codex_available(profile)
        # OPTIONAL opt-in: probe claude members on the capability model (e.g.
        # Opus) so an account that lost the higher-tier capability is cooled
        # down. Empty (default) -> None -> cheap Haiku probe, behavior unchanged.
        capability_model = DEFAULT_CAPABILITY_PROBE_MODEL or None
        try:
            result = await submit_availability_check(
                root=self.root,
                profile=profile,
                timeout=DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
                model=capability_model,
            )
        except Exception as exc:
            kind = _classify_claude_pool_error(str(exc)) or "probe_failed"
            self._mark_profile_unavailable(profile, kind, str(exc))
            logger.info("Claude pool profile %s readiness probe failed: %s", profile.name, exc)
            return False
        if result.get("ok", False):
            self._clear_profile_unavailable(profile.name)
            logger.info("Claude pool profile %s readiness probe succeeded", profile.name)
            return True
        error = str(result.get("error") or result)
        self._mark_profile_failure(profile, error)
        return False

    def _record_codex_dispatch_active(self, name: str, delta: int) -> None:
        """Track in-flight in-process codex turns for selector neutrality.

        Codex members have no job-queue directory, so their concurrent-load
        signal lives in memory. Clamped at >= 0.
        """
        current = self._codex_active.get(name, 0) + delta
        self._codex_active[name] = current if current > 0 else 0

    def _profile_load_score(self, profile_name: str) -> float:
        # Codex members have no job-queue dir and would otherwise score 0.0
        # forever (starve-winning every selection). Mirror claude's
        # active*1000.0 units using the in-memory active counter so the
        # weighted-least-loaded selector treats codex co-equally.
        profile = next(
            (p for p in self.profiles if p.name == profile_name), None
        )
        if profile is not None and profile.kind == "codex":
            return self._codex_active.get(profile_name, 0) * 1000.0
        active = 0
        for state_name in ("queued", "running"):
            active += len(list((self.root / "jobs" / state_name / profile_name).glob("*.json")))

        recent_cost = 0.0
        recent_tokens = 0
        cutoff = time.time() - DEFAULT_RECENT_USAGE_WINDOW_SECONDS
        for state_name in ("done", "failed"):
            for job_path in (self.root / "jobs" / state_name / profile_name).glob("*.json"):
                try:
                    if job_path.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                manifest = _read_json(job_path, {})
                result_path = ((manifest.get("paths") or {}).get("result"))
                if not result_path:
                    continue
                result = _read_json(Path(result_path), {})
                if not isinstance(result, dict):
                    continue
                cost, tokens = _extract_cost_and_tokens(result)
                recent_cost += cost
                recent_tokens += tokens
        return (active * 1000.0) + (recent_cost * 100.0) + (recent_tokens / 100_000.0)

    def _clear_profile_unavailable(self, profile_name: str) -> None:
        state_path = _profile_state_path(self.root)
        state = _read_json(state_path, {})
        profiles = state.get("profiles")
        if isinstance(profiles, dict) and profile_name in profiles:
            profiles.pop(profile_name, None)
            _write_json_atomic(state_path, state)

    def _mark_profile_unavailable(
        self,
        profile: ClaudePoolProfile,
        kind: str,
        error: str,
    ) -> None:
        probe_delay_seconds = _probe_delay_seconds_for_error(kind)
        now = datetime.now(UTC)
        state_path = _profile_state_path(self.root)
        state = _read_json(state_path, {})
        profiles = state.setdefault("profiles", {})
        previous = profiles.get(profile.name) if isinstance(profiles, dict) else {}
        failure_count = 0
        if isinstance(previous, dict):
            try:
                failure_count = int(previous.get("failure_count") or 0)
            except (TypeError, ValueError):
                failure_count = 0
        profiles[profile.name] = {
            "status": "unavailable",
            "reason": kind,
            "probe_after": (now + timedelta(seconds=probe_delay_seconds)).isoformat(),
            "last_error": error[-2000:],
            "updated_at": now.isoformat(),
            "failure_count": failure_count + 1,
        }
        _write_json_atomic(state_path, state)
        logger.warning(
            "Marked Claude pool profile %s unavailable after %s; next readiness probe in %.0fs",
            profile.name,
            kind,
            probe_delay_seconds,
        )

    def _mark_profile_failure(self, profile: ClaudePoolProfile, error: str) -> None:
        kind = _classify_claude_pool_error(error)
        if not kind:
            return
        self._mark_profile_unavailable(profile, kind, error)

    async def _submit_and_wait_with_failover(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None,
        workspace: Workspace | None,
        session_key: str | None,
        profile: ClaudePoolProfile,
        persistent: bool,
    ) -> tuple[str, Any, Any]:
        attempted: set[str] = set()
        last_kind: str | None = None
        last_error: RuntimeError | None = None
        exclude_kinds = self._excluded_kinds_for_role(role)

        for _ in range(max(len(self.profiles), 1)):
            if self._profile_is_unavailable(profile.name):
                profile = await self._select_profile(
                    session_key=session_key,
                    persistent=persistent,
                    exclude_kinds=exclude_kinds,
                )
            attempted.add(profile.name)
            try:
                return await self._submit_and_wait(
                    role,
                    prompt,
                    output_type=output_type,
                    workspace=workspace,
                    session_key=session_key,
                    profile=profile,
                )
            except RuntimeError as exc:
                kind = _classify_claude_pool_error(str(exc))
                if not kind:
                    raise
                last_kind = kind
                last_error = exc
                self._mark_profile_unavailable(profile, kind, str(exc))
                logger.warning(
                    "Claude pool profile %s hit %s; retrying on another available profile",
                    profile.name,
                    kind,
                )
                try:
                    next_profile = await self._select_profile(
                        session_key=session_key,
                        persistent=persistent,
                        exclude_kinds=exclude_kinds,
                    )
                except RuntimeError as select_exc:
                    raise RuntimeError(
                        f"Claude pool exhausted after {kind} on {profile.name}: {select_exc}"
                    ) from exc
                if next_profile.name in attempted:
                    break
                profile = next_profile

        detail = f" after {last_kind}" if last_kind else ""
        attempted_text = ", ".join(sorted(attempted)) or "none"
        raise RuntimeError(
            f"Claude pool exhausted{detail}; attempted profiles: {attempted_text}"
        ) from last_error

    async def _submit_and_wait_codex(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None,
        workspace: Workspace | None,
        session_key: str | None,
        profile: ClaudePoolProfile,
    ) -> tuple[str, Any, Any]:
        """Dispatch a single turn to the embedded codex runtime in-process.

        Increments/decrements the in-memory active counter (selector load) and
        adapts CodexAgentRuntime.invoke's ``str | BaseModel`` return to the
        (text, structured, raw) triple the failover loop expects.
        """
        if self._codex_runtime is None:
            raise RuntimeError(
                f"Claude pool codex member {profile.name} has no embedded codex runtime"
            )
        self._record_codex_dispatch_active(profile.name, 1)
        try:
            result = await self._codex_runtime.invoke(
                role,
                prompt,
                output_type=output_type,
                workspace=workspace,
                session_key=session_key,
            )
        finally:
            self._record_codex_dispatch_active(profile.name, -1)
        if output_type is not None and isinstance(result, BaseModel):
            return (result.model_dump_json(), result.model_dump(mode="json"), None)
        return (str(result), None, None)

    async def _submit_and_wait(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None,
        workspace: Workspace | None,
        session_key: str | None,
        profile: ClaudePoolProfile,
    ) -> tuple[str, Any, Any]:
        # Codex members dispatch IN-PROCESS. This branch sits at the very top of
        # _submit_and_wait — before any job-id/payload/manifest/heartbeat/stale
        # machinery — so the codex path never touches the job queue, and so it
        # stays inside _submit_and_wait_with_failover's loop (cooldown / failover
        # / reselect apply to codex uniformly). Liveness == the in-process await.
        if profile.kind == "codex":
            return await self._submit_and_wait_codex(
                role,
                prompt,
                output_type=output_type,
                workspace=workspace,
                session_key=session_key,
                profile=profile,
            )
        job_id = uuid.uuid4().hex
        invocation_id = _current_invocation_var.get()
        if invocation_id:
            self._invocation_jobs.setdefault(invocation_id, set()).add(job_id)
        payload_dir = _payload_dir(self.root, job_id)
        _ensure_dir(payload_dir)

        prompt_path = payload_dir / "prompt.md"
        system_prompt_path = payload_dir / "system_prompt.md"
        schema_path = payload_dir / "schema.json"
        result_path = payload_dir / "result.json"
        stdout_path = payload_dir / "stdout.json"
        stderr_path = payload_dir / "stderr.log"
        sandbox_profile_path = payload_dir / "sandbox-exec.sb"
        binding = _runtime_workspace_binding(role)
        bound_authority = (
            _validate_runtime_workspace_binding(
                binding or {},
                role_name=role.name,
                expected_runtime="claude_pool",
                workspace_path=workspace.path if workspace and workspace.path else None,
            )
            if binding and _role_is_write_producing(role)
            else None
        )
        manifest_cwd = (
            str(bound_authority.cwd)
            if bound_authority is not None
            else str(workspace.path) if workspace and workspace.path else None
        )

        prompt_path.write_text(prompt, encoding="utf-8")
        system_prompt_path.write_text(role.prompt or "", encoding="utf-8")
        if output_type:
            schema = _inline_defs(output_type.model_json_schema())
            schema_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")

        model, effort = _resolve_model_and_effort(role)
        role_metadata = getattr(role, "metadata", None) or {}
        manifest = {
            "id": job_id,
            "kind": "claude",
            "profile": profile.name,
            "status": "queued",
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "session_key": session_key,
            "feature_id": session_key.rsplit(":", 1)[-1] if session_key and ":" in session_key else None,
            "invocation_id": role_metadata.get("runtime_invocation_id")
            or role_metadata.get("invocation_id"),
            "dispatch_attempt_id": role_metadata.get("dispatch_attempt_id")
            or role_metadata.get("attempt_id"),
            "dispatch_idempotency_key": role_metadata.get("dispatch_idempotency_key")
            or role_metadata.get("idempotency_key"),
            "dispatch_request_digest": role_metadata.get("dispatch_request_digest")
            or role_metadata.get("request_digest"),
            "output_schema_digest": role_metadata.get("output_schema_digest"),
            "output_type_name": role_metadata.get("output_type_name"),
            "timeout_seconds": role_metadata.get("timeout_seconds"),
            "cwd": manifest_cwd,
            _RUNTIME_SCRATCH_ROOTS_KEY: [
                str(path) for path in _profile_runtime_scratch_roots(profile)
            ],
            "role": {
                "name": role.name,
                "model": model,
                "effort": effort,
                "tools": [str(tool) for tool in (role.tools or [])],
                "metadata": {str(k): _jsonable(v) for k, v in (role.metadata or {}).items()},
            },
            "paths": {
                "prompt": str(prompt_path),
                "system_prompt": str(system_prompt_path),
                "schema": str(schema_path) if output_type else None,
                "result": str(result_path),
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "sandbox_profile": str(sandbox_profile_path),
            },
            "claude": {
                "command": profile.claude_command,
                "permission_mode": "bypassPermissions",
                "add_dirs": [os.path.expanduser("~/.npm")],
            },
        }
        if binding:
            manifest[_RUNTIME_WORKSPACE_BINDING_KEY] = _jsonable_deep(binding)
            for key in (
                "sandbox_id",
                "repo_roots",
                "contract_ids",
                "writable_roots",
                "write_guard_roots",
                "write_guard_scope",
                "authority_schema_version",
                "runtime_workspace_authority_grants",
                "runtime_workspace_authority_grant_digest",
                "promotable",
                "blocked_roots",
                "manifest_path",
                "expires_at",
            ):
                if key in binding:
                    manifest[key] = _jsonable_deep(binding.get(key))
            if _role_is_write_producing(role):
                _validate_workspace_authority_grants(
                    manifest,
                    binding,
                    role_name=role.name,
                    cwd=Path(str(manifest_cwd or "")),
                )
                manifest[_BOUND_WRITE_AUTHORIZED_KEY] = True
                manifest[_BOUND_WRITE_GUARD_KEY] = _BOUND_WRITE_GUARD_SANDBOX_EXEC
                manifest[_BOUND_WRITE_AUTHORIZATION_KEY] = _bound_write_authorization(
                    manifest,
                    _pool_write_auth_secret(self.root),
                )
        queued_path = _job_state_path(self.root, "queued", profile.name, job_id)
        _write_json_atomic(queued_path, manifest)
        logger.info("Queued Claude pool job %s on profile %s", job_id, profile.name)

        done_path = _job_state_path(self.root, "done", profile.name, job_id)
        failed_path = _job_state_path(self.root, "failed", profile.name, job_id)
        running_path = _job_state_path(self.root, "running", profile.name, job_id)
        wait_started = time.monotonic()
        while True:
            if done_path.exists():
                result = _read_json(result_path, {})
                if not result.get("ok", False):
                    error = result.get("error") or f"Claude pool job {job_id} failed"
                    raise RuntimeError(error)
                return (
                    str(result.get("result_text") or ""),
                    result.get("structured_output"),
                    result.get("raw"),
                )
            if failed_path.exists():
                result = _read_json(result_path, {})
                error = result.get("error") or _read_json(failed_path, {}).get("error")
                raise RuntimeError(error or f"Claude pool job {job_id} failed")
            self._raise_if_job_not_progressing(
                job_id=job_id,
                profile=profile.name,
                queued_path=queued_path,
                running_path=running_path,
                wait_started=wait_started,
            )
            await asyncio.sleep(self._poll_interval)

    def _raise_if_job_not_progressing(
        self,
        *,
        job_id: str,
        profile: str,
        queued_path: Path,
        running_path: Path,
        wait_started: float,
    ) -> None:
        elapsed = time.monotonic() - wait_started
        if self._job_absolute_timeout > 0 and elapsed > self._job_absolute_timeout:
            raise TimeoutError(
                "Claude pool job exceeded absolute runtime cap "
                f"(job_id={job_id}, profile={profile}, elapsed={elapsed:.1f}s, "
                f"cap={self._job_absolute_timeout:.1f}s)"
            )

        if running_path.exists():
            manifest = _read_json(running_path, {})
            heartbeat = _parse_iso(manifest.get("heartbeat_at") or manifest.get("updated_at"))
            if heartbeat is None:
                return
            heartbeat_age = (datetime.now(UTC) - heartbeat).total_seconds()
            if self._job_stale_timeout > 0 and heartbeat_age > self._job_stale_timeout:
                raise TimeoutError(
                    "Claude pool job heartbeat is stale "
                    f"(job_id={job_id}, profile={profile}, "
                    f"heartbeat_age={heartbeat_age:.1f}s, "
                    f"stale_timeout={self._job_stale_timeout:.1f}s, "
                    f"running_path={running_path})"
                )
            return

        if queued_path.exists():
            manifest = _read_json(queued_path, {})
            updated = _parse_iso(manifest.get("updated_at") or manifest.get("created_at"))
            if updated is None:
                return
            queued_age = (datetime.now(UTC) - updated).total_seconds()
            if self._job_stale_timeout > 0 and queued_age > self._job_stale_timeout:
                raise TimeoutError(
                    "Claude pool job remained queued without progress "
                    f"(job_id={job_id}, profile={profile}, queued_age={queued_age:.1f}s, "
                    f"stale_timeout={self._job_stale_timeout:.1f}s, "
                    f"queued_path={queued_path})"
                )
            return

        raise RuntimeError(
            "Claude pool job disappeared before completion "
            f"(job_id={job_id}, profile={profile})"
        )

    async def _save_session_turn(
        self,
        session_key: str | None,
        existing: AgentSession | None,
        final_text: str,
    ) -> None:
        if not session_key or not self.session_store:
            return
        current = existing or await self.session_store.load(session_key)
        if current is None:
            current = AgentSession(session_key=session_key)
        current.session_id = None
        turns = current.metadata.get("turns", [])
        turns.append({"role": "assistant", "text": final_text, "turn": len(turns) + 1})
        current.metadata["turns"] = turns
        await self.session_store.save(current)

    def _emit_completion(self, final_text: str, structured_output: Any) -> None:
        if self.on_message is None:
            return
        if final_text:
            self.on_message(AssistantMessage(content=[TextBlock(text=final_text)]))
        self.on_message(ResultMessage(structured_output=structured_output))

    def _structured_fallback(
        self,
        output_type: type[BaseModel],
        session_key: str | None,
        final_text: str,
        error: Exception | None,
    ) -> BaseModel | None:
        from ..models.outputs import ImplementationResult, Issue, Verdict

        if output_type is ImplementationResult:
            return ImplementationResult(
                task_id=session_key.split(":")[0] if session_key else "unknown",
                summary=final_text or "Agent completed work but could not produce structured summary",
            )
        if output_type is Verdict:
            return Verdict(
                approved=False,
                summary="Verdict could not be produced (structured output failed)",
                concerns=[
                    Issue(
                        severity="blocker",
                        description=(
                            "Agent failed to produce structured Verdict. "
                            f"Error: {error}. Last result: {final_text or 'empty'}"
                        ),
                    )
                ],
            )
        return None

    async def inject_user_message(self, feature_id: str, text: str) -> bool:
        del feature_id, text
        return False

    def has_active_agent(self, feature_id: str) -> bool:
        del feature_id
        return False

    def get_active_session_key(self, feature_id: str) -> str | None:
        return self._feature_sessions.get(feature_id)

    def queue_user_note(self, feature_id: str, text: str) -> None:
        self._queued_user_notes.setdefault(feature_id, []).append(text)


class ClaudePoolRunner:
    """Per-profile runner intended to run as the matching macOS user."""

    def __init__(
        self,
        *,
        profile: str,
        root: Path | str = DEFAULT_POOL_ROOT,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self.root = Path(root)
        self.profile = profile
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.profiles = ensure_pool_layout(self.root)
        self.profile_config = next((item for item in self.profiles if item.name == profile), None)
        if self.profile_config is None:
            raise RuntimeError(f"Unknown Claude pool profile: {profile}")
        self._active: dict[str, asyncio.Task[None]] = {}

    async def run_forever(self) -> None:
        logger.info("Claude pool runner started for profile %s", self.profile)
        while True:
            self._reap_active()
            await self.run_once(wait=False)
            self._write_profile_heartbeat()
            await asyncio.sleep(self.poll_interval)

    async def run_once(self, *, wait: bool = True) -> None:
        if not self._profile_is_enabled():
            logger.warning(
                "Claude pool runner profile %s is disabled in profiles.json; refusing to claim work",
                self.profile,
            )
            return
        for queued_path in sorted((self.root / "jobs" / "queued" / self.profile).glob("*.json")):
            claimed = self._claim(queued_path)
            if claimed is None:
                continue
            manifest = _read_json(claimed, {})
            job_id = str(manifest.get("id") or claimed.stem)
            self._active[job_id] = asyncio.create_task(self._execute_claimed(claimed))
        if wait and self._active:
            await asyncio.gather(*list(self._active.values()))
            self._reap_active()

    def _reap_active(self) -> None:
        for job_id, task in list(self._active.items()):
            if task.done():
                self._active.pop(job_id, None)

    def _profile_is_enabled(self) -> bool:
        try:
            configured = {profile.name for profile in load_profiles(self.root)}
        except Exception:
            logger.warning(
                "Claude pool runner profile %s could not reload profiles.json",
                self.profile,
                exc_info=True,
            )
            return False
        return self.profile in configured

    def _write_profile_heartbeat(self) -> None:
        _write_json_atomic(
            self.root / "heartbeats" / f"{self.profile}.json",
            {
                "profile": self.profile,
                "pid": os.getpid(),
                "updated_at": _utc_now_iso(),
                "active_jobs": sorted(self._active),
            },
        )

    def _claim(self, queued_path: Path) -> Path | None:
        running_path = self.root / "jobs" / "running" / self.profile / queued_path.name
        try:
            os.replace(queued_path, running_path)
        except FileNotFoundError:
            return None
        manifest = _read_json(running_path, {})
        manifest.update({
            "status": "running",
            "runner_pid": os.getpid(),
            "claimed_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "heartbeat_at": _utc_now_iso(),
        })
        _write_json_atomic(running_path, manifest)
        return running_path

    def _validate_bound_job_manifest(self, manifest: dict[str, Any]) -> None:
        binding = manifest.get(_RUNTIME_WORKSPACE_BINDING_KEY)
        if binding is None:
            if _manifest_role_is_write_producing(manifest.get("role")):
                raise RuntimeError(
                    "Claude pool write-producing job requires runtime workspace binding"
                )
            return
        if not isinstance(binding, Mapping):
            raise RuntimeError("Bound Claude pool job has invalid runtime workspace binding")
        role_map = manifest.get("role")
        role_name = str(role_map.get("name") if isinstance(role_map, Mapping) else "unknown")
        _validate_runtime_workspace_binding(
            binding,
            role_name=role_name or "unknown",
            expected_runtime="claude_pool",
            workspace_path=manifest.get("cwd"),
        )
        if (
            _manifest_role_is_write_producing(manifest.get("role"))
        ):
            if not bool(manifest.get(_BOUND_WRITE_AUTHORIZED_KEY)):
                raise RuntimeError(
                    "Bound Claude pool job cannot run write-producing tools under "
                    "runtime workspace binding"
                )
            if manifest.get(_BOUND_WRITE_GUARD_KEY) != _BOUND_WRITE_GUARD_SANDBOX_EXEC:
                raise RuntimeError(
                    "Bound Claude pool job requires sandbox-exec write guard for "
                    "runtime workspace binding"
                )
            expected_authorization = _bound_write_authorization(
                manifest,
                _pool_write_auth_secret(self.root),
            )
            if manifest.get(_BOUND_WRITE_AUTHORIZATION_KEY) != expected_authorization:
                raise RuntimeError(
                    "Bound Claude pool job has invalid write authorization for "
                    "runtime workspace binding"
                )

        cwd_text = str(manifest.get("cwd") or "").strip()
        if not cwd_text:
            raise RuntimeError("Bound Claude pool job is missing cwd")
        cwd = Path(cwd_text).expanduser()
        if not cwd.is_absolute():
            raise RuntimeError(f"Bound Claude pool job cwd must be absolute: {cwd_text}")
        if cwd.is_symlink():
            raise RuntimeError(f"Bound Claude pool job cwd is symlinked: {cwd_text}")
        if not cwd.exists():
            raise RuntimeError(f"Bound Claude pool job cwd does not exist: {cwd_text}")
        if not cwd.is_dir():
            raise RuntimeError(f"Bound Claude pool job cwd is not a directory: {cwd_text}")

        binding_cwd = str(binding.get("cwd") or "").strip()
        if binding_cwd and Path(binding_cwd).expanduser().resolve() != cwd.resolve():
            raise RuntimeError("Bound Claude pool job cwd does not match binding cwd")
        blocked_roots = [
            Path(str(path)).expanduser().resolve()
            for path in binding.get("blocked_roots", [])
            if str(path).strip()
        ]
        if any(_is_relative_to(cwd.resolve(), blocked) for blocked in blocked_roots):
            raise RuntimeError("Bound Claude pool job cwd resolves into a blocked root")
        writable_roots = [
            Path(str(path)).expanduser().resolve()
            for path in binding.get("writable_roots", [])
            if str(path).strip()
        ]
        if writable_roots and not any(
            _is_relative_to(cwd.resolve(), root) or _is_relative_to(root, cwd.resolve())
            for root in writable_roots
        ):
            raise RuntimeError("Bound Claude pool job cwd is outside writable roots")
        if _manifest_role_is_write_producing(manifest.get("role")):
            _validate_workspace_authority_grants(
                manifest,
                binding,
                role_name=role_name or "unknown",
                cwd=cwd,
            )

        expires_at = _coerce_aware_datetime(binding.get("expires_at") or manifest.get("expires_at"))
        if expires_at is None:
            raise RuntimeError("Bound Claude pool job is missing or has invalid expires_at")
        if expires_at <= datetime.now(UTC):
            raise RuntimeError("Bound Claude pool job binding is expired")

    async def _execute_claimed(self, running_path: Path) -> None:
        manifest = _read_json(running_path, {})
        job_id = str(manifest.get("id") or running_path.stem)
        heartbeat_task = asyncio.create_task(self._heartbeat_job(running_path))
        try:
            if manifest.get("kind") == "health":
                await self._execute_health(manifest)
            elif manifest.get("kind") == "availability":
                await self._execute_availability(manifest)
            else:
                self._validate_bound_job_manifest(manifest)
                await self._execute_claude(manifest)
            status = "done"
            error = None
        except Exception as exc:
            logger.warning("Claude pool job %s failed", job_id, exc_info=True)
            status = "failed"
            error = repr(exc)
            self._write_result(manifest, {"ok": False, "error": error})
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            latest = _read_json(running_path, manifest)
            latest.update({
                "status": status,
                "updated_at": _utc_now_iso(),
                "finished_at": _utc_now_iso(),
            })
            if error:
                latest["error"] = error
            destination = self.root / "jobs" / status / self.profile / running_path.name
            _write_json_atomic(running_path, latest)
            os.replace(running_path, destination)

    async def _heartbeat_job(self, running_path: Path) -> None:
        while True:
            manifest = _read_json(running_path, {})
            manifest.update({"heartbeat_at": _utc_now_iso(), "updated_at": _utc_now_iso()})
            _write_json_atomic(running_path, manifest)
            self._write_profile_heartbeat()
            await asyncio.sleep(self.heartbeat_interval)

    async def _execute_health(self, manifest: dict[str, Any]) -> None:
        username, _ = await self._run_small_command(["/usr/bin/whoami"], timeout=10)
        auth_stdout, auth_stderr = await self._run_small_command(
            [self.profile_config.claude_command, "auth", "status"],
            timeout=20,
        )
        combined_auth = f"{auth_stdout}\n{auth_stderr}".strip()
        self._write_result(
            manifest,
            {
                "ok": True,
                "kind": "health",
                "username": username.strip(),
                "claude_auth_stdout": auth_stdout,
                "claude_auth_stderr": auth_stderr,
                "claude_auth_logged_in": _looks_logged_in(combined_auth),
                "result_text": username.strip(),
                "structured_output": {
                    "username": username.strip(),
                    "claude_auth_logged_in": _looks_logged_in(combined_auth),
                },
            },
        )

    async def _execute_availability(self, manifest: dict[str, Any]) -> None:
        paths = manifest.get("paths") or {}
        prompt = Path(paths["prompt"]).read_text(encoding="utf-8")
        command = [
            str(self.profile_config.claude_command),
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--no-session-persistence",
        ]
        model = str(manifest.get("model") or DEFAULT_AVAILABILITY_PROBE_MODEL or "").strip()
        if model:
            command.extend(["--model", model])
        effort = str(manifest.get("effort") or "low").strip()
        if effort:
            command.extend(["--effort", effort])

        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=float(manifest.get("timeout") or DEFAULT_AVAILABILITY_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            raise

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        Path(paths["stderr"]).write_text(stderr_text, encoding="utf-8")
        try:
            raw: Any = json.loads(stdout_text) if stdout_text else {}
        except json.JSONDecodeError:
            raw = {"result": stdout_text}
        Path(paths["stdout"]).write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
        if proc.returncode != 0:
            details = stderr_text or stdout_text or "unknown error"
            raise RuntimeError(f"Claude availability probe failed with exit code {proc.returncode}: {details}")
        self._write_result(
            manifest,
            {
                "ok": True,
                "kind": "availability",
                "return_code": proc.returncode,
                "result_text": _extract_result_text(raw),
                "raw": raw,
            },
        )

    async def _run_small_command(self, command: list[str], *, timeout: float) -> tuple[str, str]:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            raise
        return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")

    async def _execute_claude(self, manifest: dict[str, Any]) -> None:
        self._validate_bound_job_manifest(manifest)
        paths = manifest.get("paths") or {}
        prompt = Path(paths["prompt"]).read_text(encoding="utf-8")
        system_prompt = Path(paths["system_prompt"]).read_text(encoding="utf-8")
        schema_path = paths.get("schema")
        schema = None
        if schema_path:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))

        command = self._build_claude_command(manifest, system_prompt=system_prompt, schema=schema)
        cwd = manifest.get("cwd") or None
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        stdout_path = Path(paths["stdout"])
        stderr_path = Path(paths["stderr"])
        stderr_path.write_text(stderr_text, encoding="utf-8")

        try:
            raw: Any = json.loads(stdout_text) if stdout_text else {}
        except json.JSONDecodeError:
            raw = {"result": stdout_text}
        stdout_path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

        if proc.returncode != 0:
            details = stderr_text or stdout_text or "unknown error"
            if "login" in details.lower() or "auth" in details.lower():
                details += " Ensure the Claude GUI user is logged in and the LaunchAgent runs in that user session."
            raise RuntimeError(f"Claude CLI failed with exit code {proc.returncode}: {details}")

        result_text = _extract_result_text(raw)
        structured_output = _extract_structured_output(raw, result_text) if schema else None
        self._write_result(
            manifest,
            {
                "ok": True,
                "kind": "claude",
                "return_code": proc.returncode,
                "result_text": result_text,
                "structured_output": structured_output,
                "raw": raw,
                "session_id": raw.get("session_id") if isinstance(raw, dict) else None,
            },
        )

    def _build_claude_command(
        self,
        manifest: dict[str, Any],
        *,
        system_prompt: str,
        schema: dict[str, Any] | None,
    ) -> list[str]:
        role = manifest.get("role") or {}
        claude = manifest.get("claude") or {}
        command = [
            str(claude.get("command") or self.profile_config.claude_command),
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--permission-mode",
            str(claude.get("permission_mode") or "bypassPermissions"),
            "--no-session-persistence",
        ]
        if schema is not None:
            command.extend(["--json-schema", json.dumps(schema, sort_keys=True)])
        if system_prompt.strip():
            command.extend(["--system-prompt", system_prompt])
        model = role.get("model")
        if model:
            command.extend(["--model", str(model)])
        effort = role.get("effort")
        if effort:
            command.extend(["--effort", str(effort)])
        tools = role.get("tools") or []
        if tools:
            command.extend(["--allowedTools", ",".join(str(tool) for tool in tools)])
        for add_dir in claude.get("add_dirs") or []:
            command.extend(["--add-dir", os.path.expanduser(str(add_dir))])
        if (
            manifest.get(_BOUND_WRITE_GUARD_KEY) == _BOUND_WRITE_GUARD_SANDBOX_EXEC
            and _manifest_role_is_write_producing(role)
        ):
            sandbox_exec = shutil.which("sandbox-exec")
            if not sandbox_exec:
                raise RuntimeError("Claude pool write guard requires sandbox-exec")
            profile_path = _write_sandbox_exec_profile(manifest)
            return [sandbox_exec, "-f", str(profile_path), *command]
        return command

    def _write_result(self, manifest: dict[str, Any], result: dict[str, Any]) -> None:
        result_path = Path((manifest.get("paths") or {})["result"])
        result.update({"job_id": manifest.get("id"), "profile": self.profile, "updated_at": _utc_now_iso()})
        _write_json_atomic(result_path, result)


async def submit_health_check(
    *,
    root: Path,
    profile: ClaudePoolProfile,
    timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    ensure_pool_layout(root)
    job_id = uuid.uuid4().hex
    payload_dir = _payload_dir(root, job_id)
    _ensure_dir(payload_dir)
    result_path = payload_dir / "result.json"
    manifest = {
        "id": job_id,
        "kind": "health",
        "profile": profile.name,
        "status": "queued",
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "paths": {"result": str(result_path)},
    }
    _write_json_atomic(_job_state_path(root, "queued", profile.name, job_id), manifest)

    done_path = _job_state_path(root, "done", profile.name, job_id)
    failed_path = _job_state_path(root, "failed", profile.name, job_id)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if done_path.exists():
            return _read_json(result_path, {})
        if failed_path.exists():
            result = _read_json(result_path, {})
            raise RuntimeError(result.get("error") or f"Health check failed for {profile.name}")
        await asyncio.sleep(1)
    raise TimeoutError(f"Timed out waiting for Claude pool health check on {profile.name}")


async def submit_availability_check(
    *,
    root: Path,
    profile: ClaudePoolProfile,
    timeout: float = DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
    model: str | None = None,
) -> dict[str, Any]:
    """Queue a tiny real Claude turn for one profile.

    ``claude auth status`` only proves login state. This probe proves the
    account can currently accept a model request, which is what session-limit
    recovery needs.
    """
    ensure_pool_layout(root)
    job_id = uuid.uuid4().hex
    payload_dir = _payload_dir(root, job_id)
    _ensure_dir(payload_dir)
    prompt_path = payload_dir / "prompt.md"
    result_path = payload_dir / "result.json"
    stdout_path = payload_dir / "stdout.json"
    stderr_path = payload_dir / "stderr.log"
    prompt_path.write_text(DEFAULT_AVAILABILITY_PROBE_PROMPT, encoding="utf-8")
    manifest = {
        "id": job_id,
        "kind": "availability",
        "profile": profile.name,
        "status": "queued",
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "model": str(model or DEFAULT_AVAILABILITY_PROBE_MODEL),
        "effort": "low",
        "timeout": timeout,
        "paths": {
            "prompt": str(prompt_path),
            "result": str(result_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        },
    }
    _write_json_atomic(_job_state_path(root, "queued", profile.name, job_id), manifest)

    done_path = _job_state_path(root, "done", profile.name, job_id)
    failed_path = _job_state_path(root, "failed", profile.name, job_id)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if done_path.exists():
            return _read_json(result_path, {})
        if failed_path.exists():
            result = _read_json(result_path, {})
            raise RuntimeError(
                result.get("error") or f"Availability probe failed for {profile.name}"
            )
        await asyncio.sleep(1)
    raise TimeoutError(f"Timed out waiting for Claude pool availability probe on {profile.name}")


async def doctor(
    *,
    root: Path = DEFAULT_POOL_ROOT,
    run_health_checks: bool = True,
    timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> list[str]:
    profiles = ensure_pool_layout(root)
    lines: list[str] = [f"Claude pool root: {root}"]
    for profile in profiles:
        if profile.kind == "codex":
            lines.append(f"{profile.name}: in-process member (codex; no runner/heartbeat)")
            continue
        heartbeat_path = root / "heartbeats" / f"{profile.name}.json"
        heartbeat = _read_json(heartbeat_path, {})
        updated_at = _parse_iso(heartbeat.get("updated_at"))
        if updated_at:
            age = (datetime.now(UTC) - updated_at).total_seconds()
            lines.append(f"{profile.name}: heartbeat age {age:.1f}s")
        else:
            lines.append(f"{profile.name}: no heartbeat found")

        if run_health_checks:
            try:
                result = await submit_health_check(root=root, profile=profile, timeout=timeout)
                username = str(result.get("username") or "").strip()
                auth = "logged in" if result.get("claude_auth_logged_in") else "auth unknown/not logged in"
                status = "ok" if username == profile.user else f"wrong user {username!r}"
                lines.append(f"{profile.name}: health {status}; claude {auth}")
            except Exception as exc:
                lines.append(f"{profile.name}: health failed: {exc}")
    return lines


def install_launchagent_templates(
    *,
    root: Path = DEFAULT_POOL_ROOT,
    runner_command: str | None = None,
) -> list[str]:
    profiles = ensure_pool_layout(root)
    runner_command = runner_command or _default_runner_command()
    command_parts = shlex.split(runner_command)
    if not command_parts:
        command_parts = ["claude-pool-runner"]

    output_lines: list[str] = []
    template_dir = root / "launchagents"
    _ensure_dir(template_dir)

    for profile in profiles:
        if profile.kind == "codex":
            output_lines.append(f"Skipping {profile.name}: in-process member (no LaunchAgent)")
            continue
        label = f"com.iriai.claude-pool.{profile.name}"
        plist_path = template_dir / f"{label}.plist"
        program_args = [
            *command_parts,
            "--profile",
            profile.name,
            "--root",
            str(root),
        ]
        plist = {
            "Label": label,
            "ProgramArguments": program_args,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(root / "logs" / f"{profile.name}.out.log"),
            "StandardErrorPath": str(root / "logs" / f"{profile.name}.err.log"),
            "WorkingDirectory": str(root),
            "EnvironmentVariables": {
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
        }
        plist_path.write_bytes(plistlib.dumps(plist, sort_keys=True))
        with suppress(OSError):
            os.chmod(plist_path, 0o664)

        try:
            uid = pwd.getpwnam(profile.user).pw_uid
        except KeyError:
            uid = "<uid>"
        user_plist = f"/Users/{profile.user}/Library/LaunchAgents/{label}.plist"
        output_lines.extend(
            [
                f"Wrote {plist_path}",
                f"Install for {profile.user}:",
                f"  sudo mkdir -p /Users/{profile.user}/Library/LaunchAgents",
                f"  sudo cp {plist_path} {user_plist}",
                f"  sudo chown {profile.user}:staff {user_plist}",
                f"  launchctl bootstrap gui/{uid} {user_plist}",
                f"  launchctl kickstart -k gui/{uid}/{label}",
            ]
        )
    return output_lines


def _default_runner_command() -> str:
    found = shutil.which("claude-pool-runner")
    if found:
        return found
    shared = Path("/Users/Shared/iriai/.venv/bin/claude-pool-runner")
    if shared.exists():
        return str(shared)
    return f"{sys.executable} -m iriai_build_v2.runtimes.claude_pool_runner"


def runner_main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run a Claude pool profile worker")
    parser.add_argument("--profile", required=True, help="Claude pool profile name")
    parser.add_argument("--root", default=str(DEFAULT_POOL_ROOT), help="Claude pool root directory")
    parser.add_argument("--once", action="store_true", help="Claim and run queued jobs once, then exit")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    umask = _apply_runner_umask()
    logger.info("Claude pool runner using umask %04o", umask)

    runner = ClaudePoolRunner(profile=args.profile, root=Path(args.root))
    if args.once:
        asyncio.run(runner.run_once(wait=True))
    else:
        asyncio.run(runner.run_forever())
