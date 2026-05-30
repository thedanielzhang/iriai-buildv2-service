from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from iriai_compose.runner import AgentRuntime
from iriai_compose.storage import AgentSession, SessionStore

if TYPE_CHECKING:
    from iriai_compose.actors import Role
    from iriai_compose.workflow import Workspace

logger = logging.getLogger(__name__)
_current_invocation_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "claude_runtime_invocation_id", default=None,
)


# ── Write-isolation callback ───────────────────────────────────────────
# Tools that can create or modify files on disk.
_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_WRITE_PRODUCING_TOOLS = _WRITE_TOOLS | {"Bash"}
# File-path parameter name per tool.
_PATH_PARAMS: dict[str, str] = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "file_path",
}
_RUNTIME_WORKSPACE_BINDING_KEY = "runtime_workspace_binding"
_DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
_DEFAULT_CLAUDE_EFFORT = "high"
_OPUS_4_8_EFFORT = "high"
_CLAUDE_CLI_EFFORT_ALIASES = {
    "xhigh": "high",
}

_CLAUDE_STREAM_INACTIVITY_TIMEOUT_ENV = "IRIAI_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S"
_DEFAULT_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S = 600.0
# How often the watchdog regains control to check for a stall. The dispatch runs
# in a child task polled with asyncio.wait(timeout=...): unlike asyncio.timeout
# (which fires by cancelling the task and therefore cannot interrupt an
# uncancellable await — a blocking subprocess wait in an executor thread, or an
# anyio-shielded scope), asyncio.wait always returns on schedule, so the watchdog
# can never be starved by a wedged dispatch.
_WATCHDOG_POLL_SECONDS = 15.0


def _stream_inactivity_timeout_s() -> float:
    """Default seconds the Claude CLI response stream may be silent before the
    adapter treats the subprocess as wedged. The deadline resets on every
    streamed message, so this bounds inactivity, not total runtime — a healthy
    long-running job that keeps streaming is never interrupted. Env-overridable
    for restart-free tuning. Non-finite/non-positive values fall back to the
    default so a typo'd 'inf' can't silently disable the watchdog."""
    raw = os.environ.get(_CLAUDE_STREAM_INACTIVITY_TIMEOUT_ENV)
    if raw is None:
        return _DEFAULT_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S
    if value > 0 and math.isfinite(value):
        return value
    return _DEFAULT_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S


def _resolve_stream_inactivity_timeout(role: Any) -> float | None:
    """Effective inactivity-watchdog window for a role, or None to disable it.

    Mirrors the per-role contract the liveness watchdog enforces in
    workflows/_runner.py: role.metadata['liveness_timeout'] == 0 disables the
    watchdog (roles running long, deliberately-silent suites opt out), a
    positive value overrides, and absence falls back to the env default. Without
    this, the adapter watchdog would kill the very roles that disabled the outer
    one."""
    metadata = getattr(role, "metadata", None) or {}
    raw = metadata.get("liveness_timeout")
    if raw is None:
        return _stream_inactivity_timeout_s()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _stream_inactivity_timeout_s()
    if value == 0:
        return None
    if value < 0 or not math.isfinite(value):
        return _stream_inactivity_timeout_s()
    return value


class ClaudeStreamWatchdogStall(RuntimeError):
    """Claude CLI response stream produced no output within the inactivity
    window. The name and message carry 'watchdog'/'produced no output' so
    runtime_client._classify_exception maps it to the 'watchdog_stall' terminal
    reason (infra-retryable) rather than letting a wedged or silently-exited
    subprocess block the dispatch await forever."""


def _default_effort_for_model(model: Any) -> str:
    if str(model or "").strip() == _DEFAULT_CLAUDE_MODEL:
        return _OPUS_4_8_EFFORT
    return _DEFAULT_CLAUDE_EFFORT


def _resolve_model_and_effort(role: Any) -> tuple[str, str]:
    model = str(getattr(role, "model", None) or "").strip() or _DEFAULT_CLAUDE_MODEL
    effort = getattr(role, "effort", None)
    raw_effort = str(effort) if effort is not None else _default_effort_for_model(model)
    normalized_effort = raw_effort.strip().lower()
    return model, _CLAUDE_CLI_EFFORT_ALIASES.get(normalized_effort, normalized_effort)


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _runtime_workspace_binding(role: Any) -> dict[str, Any] | None:
    raw = (getattr(role, "metadata", None) or {}).get(_RUNTIME_WORKSPACE_BINDING_KEY)
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)


def _role_is_write_producing(role: Any) -> bool:
    tools = {str(tool) for tool in (getattr(role, "tools", None) or [])}
    return bool(
        tools & _WRITE_PRODUCING_TOOLS
        or (getattr(role, "metadata", None) or {}).get("write_producing")
    )


def _path_is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_is_relative_to(left, right) or _path_is_relative_to(right, left)


def _path_has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _as_path_list(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        text = str(value).strip()
        return [Path(text).expanduser()] if text else []
    if isinstance(value, (list, tuple, set)):
        return [Path(str(item)).expanduser() for item in value if str(item).strip()]
    return []


@dataclass(frozen=True)
class _RuntimeWorkspaceAuthority:
    cwd: Path
    sandbox_root: Path
    writable_roots: tuple[Path, ...]
    blocked_roots: tuple[Path, ...]


def _existing_absolute_path(
    value: Any,
    *,
    role_name: str,
    label: str,
    directory: bool,
    reject_symlinks: bool,
) -> Path:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"Bound Claude write role {role_name} is missing {label}")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"Bound Claude write role {role_name} {label} must be absolute")
    if not path.exists():
        raise RuntimeError(f"Bound Claude write role {role_name} {label} does not exist")
    if directory and not path.is_dir():
        raise RuntimeError(f"Bound Claude write role {role_name} {label} is not a directory")
    if not directory and not path.is_file():
        raise RuntimeError(f"Bound Claude write role {role_name} {label} is not a file")
    if reject_symlinks and _path_has_symlink_component(path):
        raise RuntimeError(f"Bound Claude write role {role_name} {label} is symlinked")
    return path


def _resolve_root_path(
    value: Any,
    *,
    role_name: str,
    label: str,
    sandbox_root: Path | None,
    require_existing: bool,
    allow_external: bool = False,
) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"Bound Claude write role {role_name} {label} must be absolute")
    if require_existing and not path.exists():
        raise RuntimeError(f"Bound Claude write role {role_name} {label} does not exist")
    if path.exists() and _path_has_symlink_component(path):
        raise RuntimeError(f"Bound Claude write role {role_name} {label} is symlinked")
    resolved = path.resolve(strict=False)
    if sandbox_root is not None and not allow_external and not _path_is_relative_to(resolved, sandbox_root):
        raise RuntimeError(f"Bound Claude write role {role_name} {label} is outside sandbox root")
    return resolved


def _resolve_root_list(
    value: Any,
    *,
    role_name: str,
    label: str,
    sandbox_root: Path | None,
    require_existing: bool,
    allow_external: bool = False,
) -> list[Path]:
    return [
        _resolve_root_path(
            item,
            role_name=role_name,
            label=label,
            sandbox_root=sandbox_root,
            require_existing=require_existing,
            allow_external=allow_external,
        )
        for item in _as_path_list(value)
    ]


def _resolve_root_mapping(
    value: Any,
    *,
    role_name: str,
    label: str,
    sandbox_root: Path,
    require_existing: bool,
) -> dict[str, Path]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _resolve_root_path(
            raw_path,
            role_name=role_name,
            label=label,
            sandbox_root=sandbox_root,
            require_existing=require_existing,
        )
        for key, raw_path in value.items()
        if str(key).strip() and str(raw_path).strip()
    }


def _validate_runtime_workspace_binding(
    binding: Mapping[str, Any],
    *,
    role_name: str,
    expected_runtime: str,
    workspace_path: Any | None = None,
) -> _RuntimeWorkspaceAuthority:
    if str(binding.get("runtime") or "") != expected_runtime:
        raise RuntimeError(
            f"Bound Claude write role {role_name} binding runtime must be {expected_runtime}"
        )

    cwd = _existing_absolute_path(
        binding.get("cwd"),
        role_name=role_name,
        label="binding cwd",
        directory=True,
        reject_symlinks=True,
    )
    if workspace_path is not None:
        workspace = _existing_absolute_path(
            workspace_path,
            role_name=role_name,
            label="workspace cwd",
            directory=True,
            reject_symlinks=True,
        )
        if workspace.resolve(strict=True) != cwd.resolve(strict=True):
            raise RuntimeError(
                f"Bound Claude write role {role_name} workspace does not match binding cwd"
            )

    workspace_override = str(binding.get("workspace_override") or "").strip()
    if workspace_override:
        override = _existing_absolute_path(
            workspace_override,
            role_name=role_name,
            label="workspace_override",
            directory=True,
            reject_symlinks=True,
        )
        if override.resolve(strict=True) != cwd.resolve(strict=True):
            raise RuntimeError(
                f"Bound Claude write role {role_name} workspace override does not match binding cwd"
            )

    manifest_path = _existing_absolute_path(
        binding.get("manifest_path"),
        role_name=role_name,
        label="sandbox manifest",
        directory=False,
        reject_symlinks=True,
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Bound Claude write role {role_name} has unreadable sandbox manifest"
        ) from exc
    if not isinstance(manifest, Mapping):
        raise RuntimeError(f"Bound Claude write role {role_name} has invalid sandbox manifest")

    binding_sandbox_id = str(binding.get("sandbox_id") or "")
    manifest_sandbox_id = str(manifest.get("sandbox_id") or "")
    if binding_sandbox_id and manifest_sandbox_id and binding_sandbox_id != manifest_sandbox_id:
        raise RuntimeError(
            f"Bound Claude write role {role_name} binding sandbox_id does not match manifest"
        )

    sandbox_root = _existing_absolute_path(
        manifest.get("root"),
        role_name=role_name,
        label="sandbox root",
        directory=True,
        reject_symlinks=True,
    )
    if not _path_is_relative_to(manifest_path, sandbox_root):
        raise RuntimeError(f"Bound Claude write role {role_name} manifest is outside sandbox root")
    if not _path_is_relative_to(cwd, sandbox_root):
        raise RuntimeError(f"Bound Claude write role {role_name} cwd is outside sandbox root")

    manifest_repo_roots = _resolve_root_mapping(
        manifest.get("repo_roots"),
        role_name=role_name,
        label="manifest repo root",
        sandbox_root=sandbox_root,
        require_existing=True,
    )
    binding_repo_roots = _resolve_root_mapping(
        binding.get("repo_roots"),
        role_name=role_name,
        label="binding repo root",
        sandbox_root=sandbox_root,
        require_existing=True,
    )
    if binding_repo_roots and manifest_repo_roots and binding_repo_roots != manifest_repo_roots:
        raise RuntimeError(
            f"Bound Claude write role {role_name} binding repo roots do not match manifest"
        )
    repo_roots = tuple((manifest_repo_roots or binding_repo_roots).values())
    if repo_roots and not any(_path_is_relative_to(cwd, root) for root in repo_roots):
        raise RuntimeError(f"Bound Claude write role {role_name} cwd is outside bound repo roots")

    manifest_writable_roots = _resolve_root_list(
        manifest.get("writable_roots"),
        role_name=role_name,
        label="writable root",
        sandbox_root=sandbox_root,
        require_existing=False,
    )
    binding_writable_roots = _resolve_root_list(
        binding.get("writable_roots"),
        role_name=role_name,
        label="binding writable root",
        sandbox_root=sandbox_root,
        require_existing=False,
    )
    writable_roots = manifest_writable_roots or binding_writable_roots
    if not writable_roots:
        raise RuntimeError(f"Bound Claude write role {role_name} requires writable roots")
    if binding_writable_roots and set(binding_writable_roots) != set(writable_roots):
        raise RuntimeError(
            f"Bound Claude write role {role_name} binding writable roots do not match manifest"
        )
    if repo_roots and any(
        not any(_path_is_relative_to(root, repo_root) for repo_root in repo_roots)
        for root in writable_roots
    ):
        raise RuntimeError(f"Bound Claude write role {role_name} writable root is outside bound repo roots")
    if not any(_paths_overlap(cwd, root) for root in writable_roots):
        raise RuntimeError(f"Bound Claude write role {role_name} cwd is outside writable roots")

    blocked_roots = tuple(
        dict.fromkeys(
            [
                *_resolve_root_list(
                    manifest.get("blocked_roots"),
                    role_name=role_name,
                    label="blocked root",
                    sandbox_root=None,
                    require_existing=False,
                    allow_external=True,
                ),
                *_resolve_root_list(
                    binding.get("blocked_roots"),
                    role_name=role_name,
                    label="binding blocked root",
                    sandbox_root=None,
                    require_existing=False,
                    allow_external=True,
                ),
            ]
        )
    )
    if any(_path_is_relative_to(cwd, blocked) for blocked in blocked_roots):
        raise RuntimeError(f"Bound Claude write role {role_name} cwd is under a blocked binding root")

    return _RuntimeWorkspaceAuthority(
        cwd=cwd.resolve(strict=True),
        sandbox_root=sandbox_root.resolve(strict=True),
        writable_roots=tuple(writable_roots),
        blocked_roots=blocked_roots,
    )


def _make_write_guard(
    allowed_dir: str,
    *,
    allowed_roots: list[str] | None = None,
    blocked_roots: list[str] | None = None,
) -> Any:
    """Return an async ``can_use_tool`` callback that denies writes outside *allowed_dir*.

    Reads (Glob, Grep, Read, Bash without mutations) are unrestricted.
    Writes via Edit/Write/MultiEdit/NotebookEdit are checked: the target
    ``file_path`` must resolve to a location under *allowed_dir*.
    """
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    resolved_root = os.path.realpath(os.path.expanduser(allowed_dir))
    raw_allowed_roots = [root for root in (allowed_roots or []) if str(root).strip()]
    resolved_allowed_roots = {
        os.path.realpath(os.path.expanduser(root))
        for root in (raw_allowed_roots or [allowed_dir])
        if str(root).strip()
    }
    resolved_blocked_roots = {
        os.path.realpath(os.path.expanduser(root))
        for root in (blocked_roots or [])
        if str(root).strip()
    }

    def _resolve_target(target: str) -> str:
        expanded = os.path.expanduser(str(target))
        if os.path.isabs(expanded):
            candidate = expanded
        else:
            candidate = os.path.join(resolved_root, expanded)
        return os.path.realpath(candidate)

    async def _guard(
        tool_name: str,
        tool_input: dict[str, Any],
        _context: Any,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name not in _WRITE_TOOLS:
            return PermissionResultAllow()

        path_key = _PATH_PARAMS.get(tool_name)
        if not path_key:
            return PermissionResultAllow()

        target = tool_input.get(path_key, "")
        if not target:
            return PermissionResultDeny(
                message=f"Write denied: no {path_key} provided",
            )

        resolved = _resolve_target(str(target))
        if any(_path_is_under(resolved, root) for root in resolved_blocked_roots):
            logger.warning(
                "Write guard: blocked %s to %s (blocked root)",
                tool_name, resolved,
            )
            return PermissionResultDeny(
                message=(
                    f"Write denied: {target} resolves to a blocked workspace path. "
                    "All file writes must stay within writable roots."
                ),
            )

        if any(_path_is_under(resolved, root) for root in resolved_allowed_roots):
            return PermissionResultAllow()

        logger.warning(
            "Write guard: blocked %s to %s (outside %s)",
            tool_name, resolved, resolved_root,
        )
        return PermissionResultDeny(
            message=(
                f"Write denied: {target} is outside the allowed workspace "
                f"({allowed_dir}). All file writes must stay within the workspace."
            ),
        )

    return _guard


def _inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``$ref`` references by inlining ``$defs``.

    Pydantic generates JSON schemas with ``$defs`` + ``$ref`` for nested
    models.  The Claude API's constrained decoding does not support
    ``$ref``, so we inline all definitions to make the full structure
    visible at every nesting level.
    """
    defs = schema.pop("$defs", None)
    if not defs:
        return schema

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if ref and isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name in defs:
                    return _resolve(defs[name])
                return obj
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(item) for item in obj]
        return obj

    return _resolve(schema)


def _neutralize_nested_claude_session_env() -> bool:
    """Strip the ``CLAUDECODE`` nested-session marker from this process's env.

    The bundled Claude Code CLI that the SDK spawns aborts with "Claude Code
    cannot be launched inside another Claude Code session" whenever it sees
    ``CLAUDECODE == "1"``. The SDK builds every subprocess environment as
    ``{**os.environ, **options.env, ...}``, so an inherited ``CLAUDECODE=1``
    propagates to every spawned CLI and crashes all dispatches. That marker is
    present whenever this runtime is launched from inside a Claude Code agent
    session — the documented operating model for the workflow runner — so strip
    it here at the single env chokepoint to cover every spawn site at once.
    Nothing in this codebase reads ``CLAUDECODE``.

    Returns ``True`` if a ``CLAUDECODE == "1"`` marker was cleared.
    """
    if os.environ.get("CLAUDECODE") == "1":
        del os.environ["CLAUDECODE"]
        return True
    return False


class ClaudeAgentRuntime(AgentRuntime):
    """Agent runtime using ClaudeSDKClient for reliable structured output.

    Two modes controlled by ``interactive_roles`` constructor param:

    - **Default** (no interactive_roles): Ephemeral client per invoke, uses
      ``receive_response()``. Same as the original implementation.
    - **Interactive** (role.name in interactive_roles): Persistent client during
      invoke, uses ``receive_messages()`` to support mid-stream user message
      injection via ``inject_user_message()``.
    """

    name = "claude"

    def __init__(
        self,
        session_store: SessionStore | None = None,
        on_message: Callable[[Any], None] | None = None,
        *,
        interactive_roles: set[str] | None = None,
    ) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            raise ImportError(
                "ClaudeAgentRuntime requires the 'claude-agent-sdk' package. "
                "Install it with: pip install claude-agent-sdk"
            )
        # The SDK splays os.environ into every CLI subprocess it spawns; an
        # inherited CLAUDECODE=1 (set when this runtime runs inside a Claude Code
        # session) trips the CLI's nested-session guard and aborts every
        # dispatch. Clear it once here so all spawn paths are covered.
        if _neutralize_nested_claude_session_env():
            logger.info(
                "Cleared inherited CLAUDECODE=1 so Claude Code CLI subprocesses "
                "can launch (nested-session guard would abort every dispatch)"
            )
        self.session_store = session_store
        self.on_message = on_message
        self._interactive_roles = interactive_roles or set()

        # Interactive mode state
        self._active_clients: dict[str, Any] = {}  # session_key → ClaudeSDKClient
        self._pending_counts: dict[str, int] = {}  # session_key → unresolved turns
        self._feature_sessions: dict[str, str] = {}  # feature_id → active session_key

        # Context management: message accumulation for session cycling
        self._session_messages: dict[str, list[str]] = {}  # session_key → full message texts (summarized only on cycle)
        self._session_sizes: dict[str, int] = {}  # session_key → actual byte count of full prompts/responses
        self._session_context: dict[str, str] = {}  # session_key → compressed context after cycle
        self._retry_depth: int = 0  # prevent infinite retry loops
        self._invocation_activity: dict[str, Callable[[], None] | None] = {}
        self._active_invocations: set[str] = set()

    @asynccontextmanager
    async def bind_invocation(self, invocation_id: str, activity_sink: Callable[[], None] | None):
        token = _current_invocation_var.set(invocation_id)
        self._invocation_activity[invocation_id] = activity_sink
        try:
            yield
        finally:
            _current_invocation_var.reset(token)
            self._invocation_activity.pop(invocation_id, None)
            self._active_invocations.discard(invocation_id)

    def invocation_has_live_work(self, invocation_id: str) -> bool:
        return invocation_id in self._active_invocations

    async def invoke(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None = None,
        workspace: Workspace | None = None,
        session_key: str | None = None,
    ) -> str | BaseModel:
        from claude_agent_sdk.types import ResultMessage

        # ── Determine session mode ──
        # Roles with max_session_chars are multi-turn (interviews) and
        # accumulate sessions.  Roles without it are one-shot (Ask tasks)
        # and get a fresh session every invocation.
        max_chars = role.metadata.get("max_session_chars", 0)
        ephemeral = not max_chars

        # ── One-shot tasks: clear prior session state ──
        if session_key and ephemeral:
            self._session_messages.pop(session_key, None)
            self._session_sizes.pop(session_key, 0)
            self._session_context.pop(session_key, None)
            if self.session_store:
                await self.session_store.delete(session_key)

        # ── Context management: proactive session cycling ──
        if session_key and max_chars:
            actual_size = self._session_sizes.get(session_key, 0)
            if actual_size >= max_chars:
                logger.info(
                    "Session %s reached %d chars (limit %d) — cycling",
                    session_key, actual_size, max_chars,
                )
                await self._cycle_session(session_key, role)

        if session_key:
            self._session_messages.setdefault(session_key, []).append(
                f"User: {prompt}"
            )
            # Track actual size for threshold checks
            self._session_sizes[session_key] = (
                self._session_sizes.get(session_key, 0) + len(prompt)
            )

        # ── Build options + inject compressed context if session was cycled ──
        options = self._build_options(role, workspace, output_type)

        effective_prompt = prompt
        prior_context = self._session_context.pop(session_key, None) if session_key else None
        if prior_context:
            effective_prompt = f"{prior_context}\n\n## Current Task\n{prompt}"

        # Resume existing session only if we have local message history.
        # On a fresh runtime (e.g. after bridge restart), _session_messages
        # is empty — the old SDK session may have accumulated a conversation
        # that exceeds the context window.  We can't summarize it (we don't
        # have the messages locally), so we start a fresh session.  Context
        # continuity is preserved via the artifact store + context provider
        # which inject prior artifacts into each prompt.
        if session_key and self.session_store:
            has_local_history = len(self._session_messages.get(session_key, [])) > 1
            session = await self.session_store.load(session_key)
            if session and session.session_id:
                if has_local_history:
                    options.resume = session.session_id
                else:
                    logger.info(
                        "Fresh runtime for %s — starting new session (prior context via artifacts)",
                        session_key,
                    )
                    await self.session_store.delete(session_key)

        use_interactive = bool(
            self._interactive_roles and role.name in self._interactive_roles
        )

        inactivity_timeout = _resolve_stream_inactivity_timeout(role)
        if use_interactive:
            result_msg = await self._invoke_interactive(
                options, effective_prompt, session_key, ResultMessage, inactivity_timeout
            )
        else:
            result_msg = await self._invoke_default(
                options, effective_prompt, ResultMessage, inactivity_timeout
            )

        if result_msg is None:
            raise RuntimeError("Claude query completed without a result message")

        result_text = getattr(result_msg, "result", "") or ""
        if session_key:
            self._session_messages.setdefault(session_key, []).append(
                f"Assistant: {result_text}"
            )
            self._session_sizes[session_key] = (
                self._session_sizes.get(session_key, 0) + len(result_text)
            )

        # Save session for future invocations + persist assistant turn
        session_id = getattr(result_msg, "session_id", None)
        if session_key and self.session_store and session_id:
            session = await self.session_store.load(session_key)
            if session:
                session.session_id = session_id
            else:
                session = AgentSession(
                    session_key=session_key, session_id=session_id
                )
            turns = session.metadata.get("turns", [])
            turns.append({
                "role": "assistant",
                "text": result_text,
                "turn": len(turns) + 1,
            })
            session.metadata["turns"] = turns
            await self.session_store.save(session)

        if not output_type:
            return result_msg.result

        # SDK guarantees structured output when output_format is set
        if result_msg.subtype == "error_max_structured_output_retries":
            logger.error(
                "Structured output failed for %s. subtype=%s, result=%s, structured_output=%s",
                output_type.__name__,
                result_msg.subtype,
                repr(result_msg.result) if result_msg.result else None,
                repr(getattr(result_msg, "structured_output", None)),
            )
            # For ImplementationResult, synthesize a minimal result instead of
            # crashing — the agent likely did the work but ran out of budget
            # before producing the structured output.
            from ..models.outputs import ImplementationResult

            if output_type is ImplementationResult:
                logger.warning(
                    "Synthesizing minimal ImplementationResult for %s — "
                    "agent exhausted budget before producing structured output",
                    session_key,
                )
                return ImplementationResult(
                    task_id=session_key.split(":")[0] if session_key else "unknown",
                    summary=(
                        result_msg.result
                        if result_msg.result
                        else "Agent completed work but could not produce structured summary"
                    ),
                )

            from ..models.outputs import Verdict, Issue

            if output_type is Verdict:
                logger.warning(
                    "Synthesizing rejected Verdict for %s — "
                    "agent could not produce structured output",
                    session_key,
                )
                return Verdict(
                    approved=False,
                    summary="Verdict could not be produced (structured output failed)",
                    concerns=[Issue(
                        severity="blocker",
                        description=(
                            f"Agent failed to produce structured Verdict after "
                            f"multiple attempts. Last result: "
                            f"{result_msg.result or 'empty'}"
                        ),
                    )],
                )

            raise RuntimeError(
                f"Claude could not produce valid {output_type.__name__} "
                f"after multiple attempts. Last result: {result_msg.result}"
            )

        # ── Error fallback: structured_output is None (context overflow) ──
        if result_msg.structured_output is None and session_key and self._retry_depth == 0:
            logger.warning(
                "structured_output is None for %s (session %s) — cycling and retrying",
                output_type.__name__, session_key,
            )
            await self._cycle_session(session_key, role)
            self._retry_depth += 1
            try:
                return await self.invoke(
                    role, prompt,
                    output_type=output_type, workspace=workspace, session_key=session_key,
                )
            finally:
                self._retry_depth -= 1

        if result_msg.structured_output is None:
            raise RuntimeError(
                f"structured_output is None for {output_type.__name__} "
                f"(session {session_key}) after retry. "
                f"Result text: {repr(result_msg.result) if result_msg.result else 'empty'}"
            )

        return output_type.model_validate(result_msg.structured_output)

    async def _run_dispatch_bounded(
        self,
        make_client: Callable[[], Any],
        prompt: str,
        ResultMessage: type,
        inactivity_timeout: float | None,
        *,
        emit: bool = True,
        on_connected: Callable[[Any], Callable[[], None] | None] | None = None,
    ) -> Any:
        """Connect, query, and drain a Claude CLI client under an inactivity
        watchdog covering the WHOLE lifecycle — connect (``__aenter__``), query,
        and receive.

        A wedge in ANY phase becomes a typed watchdog stall instead of an
        infinite hang. Bounding only the receive loop is not enough: dispatches
        whose options the SDK negotiates over the control protocol (structured
        output, can_use_tool, sandbox, MCP servers) can wedge during connect
        before the first message ever arrives — observed as the bridge sitting
        idle for 18min+ with no further log line.

        The dispatch runs in a CHILD task polled with asyncio.wait(timeout=...)
        rather than under asyncio.timeout. asyncio.timeout fires by cancelling
        the running task, which a wedged dispatch can swallow: the observed hang
        sat in an uncancellable await (a blocking subprocess wait4 in an executor
        thread / an anyio-shielded scope), so the cancellation never landed and
        the watchdog never tripped. asyncio.wait always returns on schedule, so
        on a genuine inactivity stall we abandon the child task (cancel without
        awaiting) and raise — the watchdog can never be starved. The deadline
        resets on every streamed message, so a healthy streaming job is never
        interrupted. ``inactivity_timeout`` of None disables the watchdog
        (per-role opt-out for long, deliberately-silent suites).

        ``on_connected(client)`` runs after connect for callers that must
        register the live client (interactive injection); it may return a
        cleanup callable run when the dispatch ends.
        """
        invocation_id = _current_invocation_var.get()
        loop = asyncio.get_running_loop()
        last_activity = loop.time()

        async def _run() -> Any:
            nonlocal last_activity
            result_msg = None
            async with make_client() as client:
                if invocation_id:
                    self._active_invocations.add(invocation_id)
                cleanup = on_connected(client) if on_connected is not None else None
                try:
                    await client.query(prompt)
                    async for msg in client.receive_response():
                        last_activity = loop.time()
                        if emit:
                            self._emit_message(msg)
                        if isinstance(msg, ResultMessage):
                            result_msg = msg
                finally:
                    if invocation_id:
                        self._active_invocations.discard(invocation_id)
                    if cleanup is not None:
                        cleanup()
            return result_msg

        if inactivity_timeout is None:
            return await _run()

        task = asyncio.create_task(_run())
        poll = min(inactivity_timeout, _WATCHDOG_POLL_SECONDS)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=poll)
                if task in done:
                    return task.result()
                if loop.time() - last_activity >= inactivity_timeout:
                    raise ClaudeStreamWatchdogStall(
                        f"Claude CLI dispatch watchdog stalled: produced no output "
                        f"for {inactivity_timeout:.0f}s (connect/query/stream)"
                    )
        finally:
            if not task.done():
                # Abandon: request cancellation but do not await it — the wedged
                # await may be uncancellable, and we must not re-hang here.
                task.cancel()

    async def _invoke_default(
        self,
        options: Any,
        prompt: str,
        ResultMessage: type,
        inactivity_timeout: float | None,
    ) -> Any:
        """Ephemeral client, receive_response(). Original code path."""
        from claude_agent_sdk import ClaudeSDKClient

        return await self._run_dispatch_bounded(
            lambda: ClaudeSDKClient(options=options),
            prompt,
            ResultMessage,
            inactivity_timeout,
        )

    async def _invoke_interactive(
        self,
        options: Any,
        prompt: str,
        session_key: str | None,
        ResultMessage: type,
        inactivity_timeout: float | None,
    ) -> Any:
        """Like default path but registers client for mid-stream injection."""
        from claude_agent_sdk import ClaudeSDKClient

        feature_id = session_key.rsplit(":", 1)[-1] if session_key else None

        def on_connected(client: Any) -> Callable[[], None]:
            if session_key:
                self._active_clients[session_key] = client
                self._pending_counts[session_key] = 1
            if feature_id:
                self._feature_sessions[feature_id] = session_key

            def cleanup() -> None:
                if session_key:
                    self._active_clients.pop(session_key, None)
                    self._pending_counts.pop(session_key, None)
                if feature_id:
                    self._feature_sessions.pop(feature_id, None)

            return cleanup

        return await self._run_dispatch_bounded(
            lambda: ClaudeSDKClient(options=options),
            prompt,
            ResultMessage,
            inactivity_timeout,
            on_connected=on_connected,
        )

    def _emit_message(self, msg: Any) -> None:
        invocation_id = _current_invocation_var.get()
        if invocation_id:
            sink = self._invocation_activity.get(invocation_id)
            if callable(sink):
                sink()
        if self.on_message is not None:
            self.on_message(msg)

    async def inject_user_message(self, feature_id: str, text: str) -> bool:
        """Inject a user message into the active agent for a feature.

        Returns True if injected, False if no active agent for this feature.
        """
        session_key = self._feature_sessions.get(feature_id)
        if not session_key or session_key not in self._active_clients:
            return False
        self._pending_counts[session_key] = self._pending_counts.get(session_key, 0) + 1
        await self._active_clients[session_key].query(f"[User message]: {text}")
        return True

    def has_active_agent(self, feature_id: str) -> bool:
        """Check if there is an active agent invocation for a feature."""
        session_key = self._feature_sessions.get(feature_id)
        return session_key is not None and session_key in self._active_clients

    def get_active_session_key(self, feature_id: str) -> str | None:
        """Return the active session key for a feature, if any."""
        return self._feature_sessions.get(feature_id)

    # ── Session cycling ────────────────────────────────────────────────

    async def _cycle_session(self, session_key: str, role: Role) -> None:
        """Summarize old messages, keep recent ones, clear the session."""
        messages = self._session_messages.get(session_key, [])
        keep_recent = role.metadata.get("keep_recent_messages", 6)

        if len(messages) <= keep_recent:
            # Nothing old to summarize — just clear the session
            if self.session_store:
                await self.session_store.delete(session_key)
            return

        old = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        # Summarize old messages via Haiku
        summary = ""
        try:
            summary = await self._summarize(old)
        except Exception:
            logger.warning("Summarization failed — proceeding without summary", exc_info=True)

        # Build compressed context
        parts: list[str] = []
        if summary:
            parts.append(f"## Prior Conversation Summary\n\n{summary}")
        if recent:
            parts.append("## Recent Messages\n\n" + "\n\n".join(recent))
        self._session_context[session_key] = "\n\n".join(parts)

        # Reset message buffer and size counter to recent only
        self._session_messages[session_key] = list(recent)
        self._session_sizes[session_key] = sum(len(m) for m in recent)

        # Clear the SDK session so the next invoke starts fresh
        if self.session_store:
            await self.session_store.delete(session_key)

        logger.info(
            "Cycled session %s: summarized %d old messages, kept %d recent",
            session_key, len(old), len(recent),
        )

    async def _summarize(self, messages: list[str]) -> str:
        """Use Haiku to summarize conversation history."""
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        from claude_agent_sdk.types import ResultMessage

        text = "\n\n---\n\n".join(messages)
        prompt = (
            "Summarize this conversation between an AI agent and a user. "
            "Capture: key decisions made, user preferences expressed, "
            "current state of the work, and any constraints established. "
            "For any files or artifacts mentioned, preserve their file paths "
            "so the agent can re-read them. "
            "Do NOT reproduce artifact content — just reference the paths. "
            "Be concise but preserve all decision-relevant information.\n\n"
            f"{text}"
        )
        options = ClaudeAgentOptions(
            model="claude-haiku-4-5-20251001",
            system_prompt="You are a conversation summarizer. Output only the summary.",
        )
        result = await self._run_dispatch_bounded(
            lambda: ClaudeSDKClient(options=options),
            prompt,
            ResultMessage,
            _stream_inactivity_timeout_s(),
            emit=False,
        )
        return result.result if result else ""

    def _build_options(
        self,
        role: Role,
        workspace: Workspace | None,
        output_type: type[BaseModel] | None = None,
    ) -> Any:
        """Construct ClaudeAgentOptions from a role."""
        from claude_agent_sdk import ClaudeAgentOptions

        binding = _runtime_workspace_binding(role)
        bound_write_role = bool(binding and _role_is_write_producing(role))
        authority = (
            _validate_runtime_workspace_binding(
                binding or {},
                role_name=role.name,
                expected_runtime="claude",
            )
            if bound_write_role
            else None
        )
        binding_cwd = str(authority.cwd if authority else (binding or {}).get("cwd") or "").strip()
        cwd = binding_cwd or (str(workspace.path) if workspace else None)
        if bound_write_role and not cwd:
            raise RuntimeError(f"Bound Claude write role {role.name} is missing binding cwd")

        # Write isolation: use can_use_tool callback to deny Edit/Write
        # outside the workspace.  The sandbox setting only restricts Bash
        # commands (Seatbelt/bubblewrap); Edit/Write bypass it entirely.
        write_guard = None
        sandbox = None
        sandbox_requested = bool(role.metadata.get("sandbox", True))
        if cwd and (sandbox_requested or bound_write_role):
            writable_roots = (
                [str(root) for root in authority.writable_roots]
                if authority is not None
                else _as_string_list((binding or {}).get("writable_roots"))
            )
            blocked_roots = (
                [str(root) for root in authority.blocked_roots]
                if authority is not None
                else _as_string_list((binding or {}).get("blocked_roots"))
            )
            write_guard = _make_write_guard(
                cwd,
                allowed_roots=writable_roots,
                blocked_roots=blocked_roots,
            )
            sandbox = {
                "enabled": True,
            }

        model, effort = _resolve_model_and_effort(role)
        options = ClaudeAgentOptions(
            system_prompt=role.prompt,
            allowed_tools=role.tools,
            model=model,
            cwd=cwd,
            permission_mode="bypassPermissions",
            effort=effort,
            max_buffer_size=50 * 1024 * 1024,  # 50MB — agents may glob large dirs
            sandbox=sandbox,
            can_use_tool=write_guard,
        )

        if cwd and not bound_write_role:
            options.add_dirs = [os.path.expanduser("~/.npm")]

        if "setting_sources" in role.metadata:
            options.setting_sources = role.metadata["setting_sources"]

        if "mcp_servers" in role.metadata:
            options.mcp_servers = role.metadata["mcp_servers"]

        if output_type:
            options.output_format = {
                "type": "json_schema",
                "schema": _inline_defs(output_type.model_json_schema()),
            }

        return options
