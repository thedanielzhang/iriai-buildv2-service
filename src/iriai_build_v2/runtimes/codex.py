from __future__ import annotations

import asyncio
import collections
import copy
import contextvars
import hashlib
import json
import logging
import os
import signal
import shutil
import tempfile
import tomllib
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from iriai_compose.runner import AgentRuntime
from iriai_compose.storage import AgentSession, SessionStore

from ..config import MCP_SERVERS

if TYPE_CHECKING:
    from iriai_compose.actors import Role
    from iriai_compose.workflow import Workspace

logger = logging.getLogger(__name__)
_STDOUT_READ_CHUNK = 64 * 1024
_RUNTIME_WORKSPACE_BINDING_KEY = "runtime_workspace_binding"
_WRITE_PRODUCING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}


def _bounded_int_env(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


_STDOUT_LINE_MAX_BYTES = _bounded_int_env(
    "IRIAI_CODEX_STDOUT_LINE_MAX_BYTES",
    4 * 1024 * 1024,
    maximum=32 * 1024 * 1024,
)
_STDERR_CAPTURE_LINES = _bounded_int_env(
    "IRIAI_CODEX_STDERR_CAPTURE_LINES",
    200,
    maximum=5_000,
)
_STDERR_CAPTURE_CHARS = _bounded_int_env(
    "IRIAI_CODEX_STDERR_CAPTURE_CHARS",
    64 * 1024,
    maximum=4 * 1024 * 1024,
)
_STDERR_LINE_CHARS = _bounded_int_env(
    "IRIAI_CODEX_STDERR_LINE_CHARS",
    4_000,
    maximum=64 * 1024,
)
_TRACE_HEARTBEAT_SECONDS = int(
    os.environ.get("IRIAI_CODEX_TRACE_HEARTBEAT_SECONDS", "60") or "60"
)
_PRE_WORK_STALL_SECONDS = int(
    os.environ.get("IRIAI_CODEX_PRE_WORK_STALL_SECONDS", "300") or "300"
)
_PRE_WORK_STALL_RETRIES = int(
    os.environ.get("IRIAI_CODEX_PRE_WORK_STALL_RETRIES", "1") or "1"
)
_TRACE_TEXT_LIMIT = 1_000
_current_invocation_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "codex_runtime_invocation_id", default=None,
)
_ROLE_E2E_MARKERS = (
    "gate-reviewer",
    "reviewer",
    "verifier",
    "tester",
    "reproducer",
    "deployer",
    "auditor",
)
_ROLE_E2E_NAMES = {
    "accessibility-auditor",
    "bug-reproducer",
    "code-reviewer",
    "deployer",
    "integration-tester",
    "lead-architect",
    "lead-designer",
    "lead-product-manager",
    "lead-task-planner",
    "regression-tester",
    "security-auditor",
    "smoke-tester",
    "verifier",
}
_E2E_MCP_SERVER_NAMES = ("playwright", "qa-feedback", "preview", "postgres")
_ENV_FILE_NAMES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.development.local",
    ".env.test",
    ".env.example",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _truncate_trace_text(value: Any, limit: int = _TRACE_TEXT_LIMIT) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _file_size(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


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


def _as_path_list(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        text = str(value).strip()
        return [Path(text).expanduser()] if text else []
    if isinstance(value, (list, tuple, set)):
        return [Path(str(item)).expanduser() for item in value if str(item).strip()]
    return []


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


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


def _runtime_artifact_root_for(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.name == ".iriai":
            return candidate / "runtime" / "codex"
    return path / ".iriai" / "runtime" / "codex"


def _lease_runtime_artifact_root_for(sandbox_root: Path) -> Path:
    return sandbox_root / ".iriai" / "runtime" / "codex"


class _CodexPreWorkStalled(RuntimeError):
    """Codex reached turn.started but produced no observable work."""


def _serialize_toml(config: dict[str, Any]) -> str:
    """Minimal TOML serializer for Codex config files."""
    lines: list[str] = []
    tables: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            tables[key] = value
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, bool):
            lines.append(f'{key} = {"true" if value else "false"}')
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
    for table_name, table_value in tables.items():
        if table_name == "mcp_servers":
            for server_name, server_config in table_value.items():
                lines.append(f"\n[mcp_servers.{server_name}]")
                for k, v in server_config.items():
                    if k == "type":
                        continue
                    if isinstance(v, list):
                        items = ", ".join(f'"{i}"' for i in v)
                        lines.append(f"{k} = [{items}]")
                    elif isinstance(v, dict):
                        lines.append(f"\n[mcp_servers.{server_name}.{k}]")
                        for ek, ev in v.items():
                            lines.append(f'{ek} = "{ev}"')
                    elif isinstance(v, str):
                        lines.append(f'{k} = "{v}"')
    return "\n".join(lines) + "\n"


def _read_global_codex_config() -> dict[str, Any]:
    """Read ~/.codex/config.toml, stripping MCP servers and projects."""
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        config.pop("mcp_servers", None)
        config.pop("projects", None)
        return config
    except Exception:
        logger.warning("Failed to read %s", config_path, exc_info=True)
        return {}


def _prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Pydantic schema for Codex structured output."""
    defs = schema.pop("$defs", None)

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if ref and isinstance(ref, str) and defs:
                name = ref.rsplit("/", 1)[-1]
                if name in defs:
                    return _resolve(defs[name])

            resolved = {key: _resolve(value) for key, value in obj.items()}
            if resolved.get("type") == "object" and "additionalProperties" not in resolved:
                resolved["additionalProperties"] = False
            properties = resolved.get("properties")
            if isinstance(properties, dict):
                resolved["required"] = list(properties.keys())
            return resolved

        if isinstance(obj, list):
            return [_resolve(item) for item in obj]

        return obj

    return _resolve(schema)


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    thinking: str


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    content: Any
    is_error: bool | None = None


@dataclass
class AssistantMessage:
    content: list[Any]
    id: str | None = None


@dataclass
class ResultMessage:
    structured_output: Any = None


@dataclass(frozen=True)
class _BoundRuntimeAuthority:
    cwd: Path
    sandbox_root: Path
    writable_roots: tuple[Path, ...]
    blocked_roots: tuple[Path, ...]
    artifact_roots: tuple[Path, ...]


class CodexAgentRuntime(AgentRuntime):
    """Agent runtime backed by the Codex CLI."""

    name = "codex"

    def __init__(
        self,
        session_store: SessionStore | None = None,
        on_message: Any | None = None,
        *,
        interactive_roles: set[str] | None = None,
        codex_command: str = "codex",
    ) -> None:
        if shutil.which(codex_command) is None:
            raise ImportError(
                "CodexAgentRuntime requires the Codex CLI on PATH. "
                "Install it with: npm install -g @openai/codex"
            )
        self.session_store = session_store
        self.on_message = on_message
        self._interactive_roles = interactive_roles or set()
        self._codex_command = codex_command
        self._feature_sessions: dict[str, str] = {}
        self._queued_user_notes: dict[str, list[str]] = {}
        self._warned_roles: set[tuple[str, ...]] = set()
        self._global_codex_config = _read_global_codex_config()
        self._invocation_activity: dict[str, Any] = {}
        self._invocation_processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def _bound_runtime_allowed_roots(authority: _BoundRuntimeAuthority) -> list[Path]:
        return [
            authority.sandbox_root,
            *authority.writable_roots,
            *authority.artifact_roots,
        ]

    def _assert_bound_runtime_path(
        self,
        path: Path,
        authority: _BoundRuntimeAuthority,
        *,
        label: str,
    ) -> None:
        if not path.is_absolute():
            raise RuntimeError(f"Codex runtime {label} {path} must be absolute")
        if _path_has_symlink_component(path):
            raise RuntimeError(f"Codex runtime {label} {path} is symlinked")
        if not any(
            _path_is_relative_to(path, root)
            for root in self._bound_runtime_allowed_roots(authority)
        ):
            raise RuntimeError(
                f"Codex runtime {label} {path} is outside the bound sandbox/artifact roots"
            )

    def _trace_dir(
        self,
        workspace: Workspace | None,
        *,
        authority: _BoundRuntimeAuthority | None = None,
        temp_dir: str | None = None,
    ) -> Path:
        temp_dir = temp_dir or self._runtime_temp_dir(workspace, authority=authority)
        base = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir()) / "iriai-codex"
        trace_dir = base / "traces"
        if authority is not None:
            self._assert_bound_runtime_path(trace_dir, authority, label="trace dir")
        trace_dir.mkdir(parents=True, exist_ok=True)
        if authority is not None:
            self._assert_bound_runtime_path(trace_dir, authority, label="trace dir")
        return trace_dir

    def _new_trace_path(
        self,
        *,
        workspace: Workspace | None,
        role: Role,
        session_key: str | None,
        authority: _BoundRuntimeAuthority | None = None,
        temp_dir: str | None = None,
    ) -> Path:
        actor_name = self._actor_name(session_key) or role.name
        safe_actor = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "_"
            for ch in actor_name
        )[:80]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        return self._trace_dir(
            workspace,
            authority=authority,
            temp_dir=temp_dir,
        ) / f"{stamp}-{safe_actor}-{uuid.uuid4().hex[:8]}.jsonl"

    def _write_trace(
        self,
        trace_path: Path | None,
        event: str,
        **fields: Any,
    ) -> None:
        if trace_path is None:
            return
        payload = {
            "ts": _utc_now_iso(),
            "event": event,
            **fields,
        }
        try:
            with trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, sort_keys=True, default=str))
                f.write("\n")
        except Exception:
            logger.debug("Failed to write Codex trace event %s", event, exc_info=True)

    @asynccontextmanager
    async def bind_invocation(self, invocation_id: str, activity_sink: Any | None):
        token = _current_invocation_var.set(invocation_id)
        self._invocation_activity[invocation_id] = activity_sink
        try:
            yield
        finally:
            _current_invocation_var.reset(token)
            self._invocation_activity.pop(invocation_id, None)
            self._invocation_processes.pop(invocation_id, None)

    def invocation_has_live_work(self, invocation_id: str) -> bool:
        proc = self._invocation_processes.get(invocation_id)
        if proc is None:
            return False
        if proc.returncode is not None:
            return False
        try:
            os.kill(proc.pid, 0)
        except OSError:
            return False
        return True

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

        self._log_runtime_differences(role)

        effective_prompt = self._compose_prompt(
            role,
            prompt,
            feature_id=feature_id,
            session_key=session_key,
            session=session,
            workspace=workspace,
            output_type=output_type,
        )

        final_text, thread_id = await self._run_codex(
            role,
            effective_prompt,
            workspace=workspace,
            output_type=output_type,
            resume_thread_id=None,
            ephemeral=not persistent,
            feature_id=feature_id,
            session_key=session_key,
        )

        if session_key and self.session_store:
            current = session or AgentSession(session_key=session_key)
            current.session_id = None
            turns = current.metadata.get("turns", [])
            turns.append(
                {
                    "role": "assistant",
                    "text": final_text,
                    "turn": len(turns) + 1,
                }
            )
            current.metadata["turns"] = turns
            await self.session_store.save(current)

        if not output_type:
            return final_text

        # Parse and validate structured output with retry.
        # Codex doesn't guarantee valid JSON on first attempt (unlike Claude SDK).
        max_retries = 2
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                payload = json.loads(final_text)
                return output_type.model_validate(payload)
            except (json.JSONDecodeError, Exception) as exc:
                last_error = exc
                if attempt < max_retries:
                    logger.warning(
                        "Codex structured output attempt %d failed for %s: %s — retrying",
                        attempt + 1, output_type.__name__, exc,
                    )
                    final_text, _ = await self._run_codex(
                        role=role,
                        prompt=(
                            f"Your previous response was not valid JSON for {output_type.__name__}. "
                            f"Error: {exc}\n\n"
                            f"Please output ONLY valid JSON matching the schema. "
                            f"Previous response:\n{final_text}"
                        ),
                        workspace=workspace,
                        output_type=output_type,
                        resume_thread_id=None,
                        ephemeral=True,
                        feature_id=feature_id,
                        session_key=session_key,
                    )
        # For ImplementationResult, synthesize a minimal result instead of
        # crashing — the agent likely did the work but couldn't produce JSON.
        from ..models.outputs import ImplementationResult

        if output_type is ImplementationResult:
            logger.warning(
                "Synthesizing minimal ImplementationResult for %s — "
                "Codex could not produce valid JSON after %d attempts",
                session_key, max_retries + 1,
            )
            return ImplementationResult(
                task_id=session_key.split(":")[0] if session_key else "unknown",
                summary=final_text if final_text else "Agent completed work but could not produce structured summary",
            )

        raise RuntimeError(
            f"Codex failed to return valid JSON for {output_type.__name__} "
            f"after {max_retries + 1} attempts: {last_error}"
        )

    async def inject_user_message(self, feature_id: str, text: str) -> bool:
        return False

    def has_active_agent(self, feature_id: str) -> bool:
        return False

    def get_active_session_key(self, feature_id: str) -> str | None:
        return self._feature_sessions.get(feature_id)

    def queue_user_note(self, feature_id: str, text: str) -> None:
        self._queued_user_notes.setdefault(feature_id, []).append(text)

    def _compose_prompt(
        self,
        role: Role,
        prompt: str,
        *,
        feature_id: str | None,
        session_key: str | None = None,
        session: AgentSession | None = None,
        workspace: Workspace | None = None,
        output_type: type[BaseModel] | None = None,
    ) -> str:
        sections = [
            "You are running as an agent inside the iriai-build-v2 workflow engine.",
            f"## Role\nName: {role.name}",
            f"## Role Instructions\n{role.prompt.strip()}",
        ]

        if role.tools:
            tools = ", ".join(role.tools)
            sections.append(
                "## Available Tooling Expectations\n"
                f"Use Codex tools to cover these intended capabilities when possible: {tools}."
            )

        if self._wants_e2e_access(role, session_key=session_key):
            sections.append(
                "## Runtime Capabilities\n"
                "This session may run local shell commands without Codex sandbox restrictions, "
                "connect to localhost services, use Playwright for browser testing, "
                "and access configured preview/database integrations when available."
            )

        mcp_servers = self._effective_mcp_servers(
            role,
            workspace=workspace,
            feature_id=feature_id,
            session_key=session_key,
        )
        if mcp_servers:
            names = ", ".join(mcp_servers.keys())
            sections.append(
                "## MCP Tools Available\n"
                f"The following MCP servers are configured and available for this session: {names}. "
                "Use the tools they provide when relevant to your task."
            )

        notes = self._consume_user_notes(feature_id)
        if notes:
            sections.append(
                "## User Notes Since The Last Agent Turn\n"
                + "\n".join(f"- {note}" for note in notes)
            )

        fallback_context = self._fallback_session_context(role, session)
        if fallback_context:
            sections.append(fallback_context)

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
        recent_turns = turns[-keep_recent:]
        rendered: list[str] = []
        for turn in recent_turns:
            who = str(turn.get("role", "assistant")).title()
            text = str(turn.get("text", "")).strip()
            if not text:
                continue
            rendered.append(f"{who}: {text}")
        if not rendered:
            return ""
        return "## Prior Conversation\n" + "\n\n".join(rendered)

    def _consume_user_notes(self, feature_id: str | None) -> list[str]:
        if not feature_id:
            return []
        return self._queued_user_notes.pop(feature_id, [])

    def _build_command(
        self,
        *,
        role: Role,
        workspace: Workspace | None,
        output_schema_path: str | None,
        output_path: str,
        resume_thread_id: str | None,
        ephemeral: bool,
        session_key: str | None = None,
    ) -> list[str]:
        args = [self._codex_command, "exec"]
        if resume_thread_id:
            args.append("resume")

        args.extend(["--json", "--skip-git-repo-check"])
        binding = _runtime_workspace_binding(role)
        bound_write_role = bool(binding and _role_is_write_producing(role))
        auto_approve_mcp_tools = bool(role.metadata.get("auto_approve_mcp_tools"))
        disable_shell_tools = bool(
            role.metadata.get("disable_shell_tools")
            or role.metadata.get("forbid_command_execution")
        )
        codex_read_only_sandbox = bool(role.metadata.get("codex_read_only_shell"))
        if disable_shell_tools and not codex_read_only_sandbox:
            raise RuntimeError(
                "Codex no-shell execution is not available for roles that disable "
                "shell tools; use an evidence-only supervisor path instead."
            )
        if bound_write_role:
            args.append("--full-auto")
        elif codex_read_only_sandbox:
            args.extend(["--full-auto", "--sandbox", "read-only"])
        elif self._wants_e2e_access(role, session_key=session_key) or (
            auto_approve_mcp_tools and not disable_shell_tools
        ):
            args.extend(
                [
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c",
                    "shell_environment_policy.inherit=all",
                ]
            )
        else:
            args.append("--full-auto")
        args.extend(["-o", output_path])

        if ephemeral:
            args.append("--ephemeral")

        model = self._resolve_model(role)
        if model:
            args.extend(["-m", model])

        if output_schema_path:
            args.extend(["--output-schema", output_schema_path])

        # MCP servers are now configured via per-invocation CODEX_HOME
        # (see _prepare_codex_home), not via -c flags.
        if not bound_write_role:
            args.extend(["--add-dir", os.path.expanduser("~/.npm")])

        if workspace and workspace.path:
            args.extend(["-C", str(workspace.path)])

        if resume_thread_id:
            args.append(resume_thread_id)

        args.append("-")
        return args

    def _runtime_temp_dir(
        self,
        workspace: Workspace | None,
        *,
        authority: _BoundRuntimeAuthority | None = None,
    ) -> str | None:
        """Choose a lease-local or workspace-local temp dir for Codex helper files."""
        if authority is not None:
            temp_root = authority.artifact_roots[0]
            self._assert_bound_runtime_path(temp_root, authority, label="temp dir")
            temp_root.mkdir(parents=True, exist_ok=True)
            self._assert_bound_runtime_path(temp_root, authority, label="temp dir")
            resolved = temp_root.resolve(strict=True)
            self._assert_bound_runtime_path(resolved, authority, label="temp dir")
            return str(resolved)

        if not workspace or not workspace.path:
            return None

        path = Path(workspace.path).resolve()
        for candidate in (path, *path.parents):
            if candidate.name == ".iriai":
                temp_root = candidate / "runtime" / "codex"
                temp_root.mkdir(parents=True, exist_ok=True)
                return str(temp_root)

        temp_root = path / ".iriai" / "runtime" / "codex"
        temp_root.mkdir(parents=True, exist_ok=True)
        return str(temp_root)

    def _assert_bound_runtime_paths(
        self,
        role: Role,
        workspace: Workspace | None,
        *,
        temp_dir: str | None,
        paths: list[str | Path | None],
    ) -> None:
        binding = _runtime_workspace_binding(role)
        if not binding or not _role_is_write_producing(role):
            return

        if not temp_dir:
            raise RuntimeError(f"Bound Codex write role {role.name} requires workspace-local runtime temp dir")

        authority = self._bound_runtime_authority(role, workspace, binding=binding)
        allowed_roots = self._bound_runtime_allowed_roots(authority)
        blocked_roots = list(authority.blocked_roots)

        runtime_temp = Path(temp_dir).expanduser()
        if not runtime_temp.is_absolute():
            raise RuntimeError(f"Codex runtime temp dir {runtime_temp} must be absolute")
        if _path_has_symlink_component(runtime_temp):
            raise RuntimeError(f"Codex runtime temp dir {runtime_temp} is symlinked")

        if not any(_path_is_relative_to(runtime_temp, root) for root in allowed_roots):
            raise RuntimeError(
                f"Codex runtime temp dir {runtime_temp} is outside the bound sandbox/artifact roots"
            )

        for raw_path in paths:
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                raise RuntimeError(f"Codex runtime path {path} must be absolute")
            if _path_has_symlink_component(path):
                raise RuntimeError(f"Codex runtime path {path} is symlinked")
            if any(_path_is_relative_to(path, root) for root in blocked_roots):
                raise RuntimeError(f"Codex runtime path {path} is under a blocked binding root")
            if not any(_path_is_relative_to(path, root) for root in allowed_roots):
                raise RuntimeError(
                    f"Codex runtime path {path} is outside the bound sandbox/artifact roots"
                )

    def _bound_runtime_authority(
        self,
        role: Role,
        workspace: Workspace | None,
        *,
        binding: Mapping[str, Any] | None = None,
    ) -> _BoundRuntimeAuthority:
        binding = dict(binding or _runtime_workspace_binding(role) or {})
        if not binding or not _role_is_write_producing(role):
            raise RuntimeError(f"Bound Codex write role {role.name} requires runtime workspace binding")
        if str(binding.get("runtime") or "") != "codex":
            raise RuntimeError(f"Bound Codex write role {role.name} binding runtime must be codex")
        if not workspace or not workspace.path:
            raise RuntimeError(f"Bound Codex write role {role.name} requires a workspace")

        cwd = self._existing_absolute_path(
            binding.get("cwd"),
            role=role,
            label="binding cwd",
            directory=True,
            reject_symlinks=True,
        )
        workspace_path = self._existing_absolute_path(
            workspace.path,
            role=role,
            label="workspace cwd",
            directory=True,
            reject_symlinks=True,
        )
        if workspace_path.resolve(strict=True) != cwd.resolve(strict=True):
            raise RuntimeError(f"Bound Codex write role {role.name} workspace does not match binding cwd")

        workspace_override = str(binding.get("workspace_override") or "").strip()
        if workspace_override:
            override_path = self._existing_absolute_path(
                workspace_override,
                role=role,
                label="workspace_override",
                directory=True,
                reject_symlinks=True,
            )
            if override_path.resolve(strict=True) != cwd.resolve(strict=True):
                raise RuntimeError(f"Bound Codex write role {role.name} workspace override does not match binding cwd")

        manifest_path = self._existing_absolute_path(
            binding.get("manifest_path"),
            role=role,
            label="sandbox manifest",
            directory=False,
            reject_symlinks=True,
        )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Bound Codex write role {role.name} has unreadable sandbox manifest") from exc
        if not isinstance(manifest, Mapping):
            raise RuntimeError(f"Bound Codex write role {role.name} has invalid sandbox manifest")

        binding_sandbox_id = str(binding.get("sandbox_id") or "")
        manifest_sandbox_id = str(manifest.get("sandbox_id") or "")
        if binding_sandbox_id and manifest_sandbox_id and binding_sandbox_id != manifest_sandbox_id:
            raise RuntimeError(f"Bound Codex write role {role.name} binding sandbox_id does not match manifest")

        sandbox_root = self._existing_absolute_path(
            manifest.get("root"),
            role=role,
            label="sandbox root",
            directory=True,
            reject_symlinks=True,
        )
        if not _path_is_relative_to(manifest_path, sandbox_root):
            raise RuntimeError(f"Bound Codex write role {role.name} manifest is outside sandbox root")
        if not _path_is_relative_to(cwd, sandbox_root):
            raise RuntimeError(f"Bound Codex write role {role.name} cwd is outside sandbox root")

        manifest_repo_roots = self._resolve_manifest_roots(
            manifest.get("repo_roots"),
            role=role,
            label="manifest repo root",
            sandbox_root=sandbox_root,
            require_existing=True,
        )
        binding_repo_roots = self._resolve_manifest_roots(
            binding.get("repo_roots"),
            role=role,
            label="binding repo root",
            sandbox_root=sandbox_root,
            require_existing=True,
        )
        if binding_repo_roots and binding_repo_roots != manifest_repo_roots:
            raise RuntimeError(f"Bound Codex write role {role.name} binding repo roots do not match manifest")
        if manifest_repo_roots and not any(_path_is_relative_to(cwd, root) for root in manifest_repo_roots.values()):
            raise RuntimeError(f"Bound Codex write role {role.name} cwd is outside bound repo roots")

        writable_roots = self._resolve_root_list(
            manifest.get("writable_roots") or binding.get("writable_roots"),
            role=role,
            label="writable root",
            sandbox_root=sandbox_root,
            require_existing=False,
        )
        binding_writable_roots = self._resolve_root_list(
            binding.get("writable_roots"),
            role=role,
            label="binding writable root",
            sandbox_root=sandbox_root,
            require_existing=False,
        )
        if not writable_roots:
            raise RuntimeError(f"Bound Codex write role {role.name} requires writable roots")
        if binding_writable_roots and set(binding_writable_roots) != set(writable_roots):
            raise RuntimeError(f"Bound Codex write role {role.name} binding writable roots do not match manifest")
        if writable_roots and not any(
            _path_is_relative_to(cwd, root)
            for root in writable_roots
        ):
            raise RuntimeError(f"Bound Codex write role {role.name} cwd is outside writable roots")

        blocked_roots = tuple(
            dict.fromkeys(
                [
                    *self._resolve_root_list(
                        manifest.get("blocked_roots"),
                        role=role,
                        label="blocked root",
                        sandbox_root=None,
                        require_existing=False,
                        allow_external=True,
                    ),
                    *self._resolve_root_list(
                        binding.get("blocked_roots"),
                        role=role,
                        label="binding blocked root",
                        sandbox_root=None,
                        require_existing=False,
                        allow_external=True,
                    ),
                ]
            )
        )
        if any(_path_is_relative_to(cwd, blocked) for blocked in blocked_roots):
            raise RuntimeError(f"Bound Codex write role {role.name} cwd is under a blocked binding root")

        runtime_artifact_root = _lease_runtime_artifact_root_for(sandbox_root)
        if _path_has_symlink_component(runtime_artifact_root):
            raise RuntimeError(f"Bound Codex write role {role.name} runtime artifact root is symlinked")
        runtime_artifact_root = runtime_artifact_root.resolve(strict=False)
        if not _path_is_relative_to(runtime_artifact_root, sandbox_root):
            raise RuntimeError(
                f"Bound Codex write role {role.name} runtime artifact root is outside sandbox root"
            )
        return _BoundRuntimeAuthority(
            cwd=cwd.resolve(strict=True),
            sandbox_root=sandbox_root.resolve(strict=True),
            writable_roots=tuple(writable_roots),
            blocked_roots=blocked_roots,
            artifact_roots=(runtime_artifact_root,),
        )

    def _existing_absolute_path(
        self,
        value: Any,
        *,
        role: Role,
        label: str,
        directory: bool,
        reject_symlinks: bool,
    ) -> Path:
        text = str(value or "").strip()
        if not text:
            raise RuntimeError(f"Bound Codex write role {role.name} is missing {label}")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"Bound Codex write role {role.name} {label} must be absolute")
        if not path.exists():
            raise RuntimeError(f"Bound Codex write role {role.name} {label} does not exist")
        if directory and not path.is_dir():
            raise RuntimeError(f"Bound Codex write role {role.name} {label} is not a directory")
        if not directory and not path.is_file():
            raise RuntimeError(f"Bound Codex write role {role.name} {label} is not a file")
        if reject_symlinks and _path_has_symlink_component(path):
            raise RuntimeError(f"Bound Codex write role {role.name} {label} is symlinked")
        return path

    def _resolve_manifest_roots(
        self,
        value: Any,
        *,
        role: Role,
        label: str,
        sandbox_root: Path,
        require_existing: bool,
    ) -> dict[str, Path]:
        if not isinstance(value, Mapping):
            return {}
        roots: dict[str, Path] = {}
        for key, raw_path in value.items():
            roots[str(key)] = self._resolve_root_path(
                raw_path,
                role=role,
                label=label,
                sandbox_root=sandbox_root,
                require_existing=require_existing,
            )
        return roots

    def _resolve_root_list(
        self,
        value: Any,
        *,
        role: Role,
        label: str,
        sandbox_root: Path | None,
        require_existing: bool,
        allow_external: bool = False,
    ) -> list[Path]:
        return [
            self._resolve_root_path(
                item,
                role=role,
                label=label,
                sandbox_root=sandbox_root,
                require_existing=require_existing,
                allow_external=allow_external,
            )
            for item in _as_path_list(value)
        ]

    def _resolve_root_path(
        self,
        value: Any,
        *,
        role: Role,
        label: str,
        sandbox_root: Path | None,
        require_existing: bool,
        allow_external: bool = False,
    ) -> Path:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"Bound Codex write role {role.name} {label} must be absolute")
        if require_existing and not path.exists():
            raise RuntimeError(f"Bound Codex write role {role.name} {label} does not exist")
        if path.exists() and _path_has_symlink_component(path):
            raise RuntimeError(f"Bound Codex write role {role.name} {label} is symlinked")
        resolved = path.resolve(strict=False)
        if sandbox_root is not None and not allow_external and not _path_is_relative_to(resolved, sandbox_root):
            raise RuntimeError(f"Bound Codex write role {role.name} {label} is outside sandbox root")
        return resolved

    def _prepare_codex_home(
        self,
        role: Role,
        workspace: Workspace | None,
        *,
        feature_id: str | None = None,
        session_key: str | None = None,
        authority: _BoundRuntimeAuthority | None = None,
        temp_dir: str | None = None,
    ) -> str:
        """Create a per-invocation CODEX_HOME with only the role's MCP servers.

        This prevents the global ``~/.codex/config.toml`` from loading all 7
        MCP servers for every invocation.  Each role declares which servers it
        needs via ``mcp_servers_for(...)``; only those are included.
        """
        temp_dir = temp_dir or self._runtime_temp_dir(workspace, authority=authority)
        base = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
        codex_home = base / "codex_homes" / str(uuid.uuid4())
        if authority is not None:
            self._assert_bound_runtime_path(codex_home, authority, label="CODEX_HOME")
        codex_home.mkdir(parents=True, exist_ok=True)
        if authority is not None:
            self._assert_bound_runtime_path(codex_home, authority, label="CODEX_HOME")

        # Global settings (model, etc.) + only the role's MCP servers
        config: dict[str, Any] = copy.deepcopy(self._global_codex_config)
        mcp_servers = self._effective_mcp_servers(
            role,
            workspace=workspace,
            feature_id=feature_id,
            session_key=session_key,
        )
        if mcp_servers:
            config["mcp_servers"] = mcp_servers

        (codex_home / "config.toml").write_text(
            _serialize_toml(config), encoding="utf-8",
        )

        # Symlink auth.json so Codex can authenticate
        real_codex_home = Path(
            os.environ.get("CODEX_HOME", Path.home() / ".codex")
        )
        auth_src = real_codex_home / "auth.json"
        if auth_src.exists():
            auth_dst = codex_home / "auth.json"
            if authority is not None:
                shutil.copy2(auth_src, auth_dst)
            else:
                try:
                    auth_dst.symlink_to(auth_src)
                except OSError:
                    shutil.copy2(auth_src, auth_dst)

        return str(codex_home)

    async def _run_codex(
        self,
        role: Role,
        prompt: str,
        *,
        workspace: Workspace | None,
        output_type: type[BaseModel] | None,
        resume_thread_id: str | None,
        ephemeral: bool,
        feature_id: str | None = None,
        session_key: str | None = None,
    ) -> tuple[str, str | None]:
        schema_path: str | None = None
        output_path: str | None = None
        codex_home: str | None = None
        trace_path: Path | None = None
        prompt_snapshot_path: Path | None = None
        schema_snapshot_path: Path | None = None
        succeeded = False
        binding = _runtime_workspace_binding(role)
        authority: _BoundRuntimeAuthority | None = None
        if binding and _role_is_write_producing(role):
            authority = self._bound_runtime_authority(role, workspace, binding=binding)
        temp_dir = self._runtime_temp_dir(workspace, authority=authority)
        if authority is not None:
            self._assert_bound_runtime_paths(
                role,
                workspace,
                temp_dir=temp_dir,
                paths=[temp_dir],
            )
        try:
            trace_path = self._new_trace_path(
                workspace=workspace,
                role=role,
                session_key=session_key,
                authority=authority,
                temp_dir=temp_dir,
            )
            prompt_snapshot_path = trace_path.with_suffix(".prompt.md")
            prompt_snapshot_path.write_text(prompt, encoding="utf-8")
            prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if output_type:
                prepared_schema = _prepare_schema(output_type.model_json_schema())
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".json", dir=temp_dir, delete=False
                ) as schema_file:
                    json.dump(prepared_schema, schema_file)
                    schema_path = schema_file.name
                schema_snapshot_path = trace_path.with_suffix(".schema.json")
                schema_snapshot_path.write_text(
                    json.dumps(prepared_schema, indent=2, sort_keys=True),
                    encoding="utf-8",
                )

            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".txt", dir=temp_dir, delete=False
            ) as output_file:
                output_path = output_file.name

            command = self._build_command(
                role=role,
                workspace=workspace,
                output_schema_path=schema_path,
                output_path=output_path,
                resume_thread_id=resume_thread_id,
                ephemeral=ephemeral,
                session_key=session_key,
            )

            # Isolate CODEX_HOME so only the role's MCP servers are loaded
            mcp_server_names = sorted(
                self._effective_mcp_servers(
                    role,
                    workspace=workspace,
                    feature_id=feature_id,
                    session_key=session_key,
                ).keys()
            )
            codex_home = self._prepare_codex_home(
                role,
                workspace,
                feature_id=feature_id,
                session_key=session_key,
                authority=authority,
                temp_dir=temp_dir,
            )
            self._assert_bound_runtime_paths(
                role,
                workspace,
                temp_dir=temp_dir,
                paths=[
                    trace_path,
                    prompt_snapshot_path,
                    schema_snapshot_path,
                    schema_path,
                    output_path,
                    codex_home,
                ],
            )
            env = {**os.environ, "CODEX_HOME": codex_home}

            self._write_trace(
                trace_path,
                "invocation.prepared",
                role=role.name,
                actor=self._actor_name(session_key),
                session_key=session_key,
                feature_id=feature_id,
                workspace=str(workspace.path) if workspace and workspace.path else None,
                output_type=output_type.__name__ if output_type else None,
                prompt_chars=len(prompt),
                prompt_bytes=len(prompt.encode("utf-8")),
                prompt_sha256=prompt_digest,
                prompt_snapshot_path=str(prompt_snapshot_path),
                command=command,
                codex_home=codex_home,
                mcp_servers=mcp_server_names,
                output_path=output_path,
                output_schema_path=schema_path,
                output_schema_snapshot_path=str(schema_snapshot_path) if schema_snapshot_path else None,
                output_file_size=_file_size(output_path),
                pre_work_stall_seconds=_PRE_WORK_STALL_SECONDS,
                pre_work_stall_retries=_PRE_WORK_STALL_RETRIES,
            )
            logger.info(
                "Codex invocation prepared role=%s actor=%s feature=%s trace=%s "
                "prompt_chars=%d mcp=%s output=%s schema=%s",
                role.name,
                self._actor_name(session_key) or "<unknown>",
                feature_id or "<unknown>",
                trace_path,
                len(prompt),
                ",".join(mcp_server_names) or "<none>",
                output_path,
                schema_path,
            )

            final_text = ""
            thread_id: str | None = None
            stderr_text = ""
            for process_attempt in range(_PRE_WORK_STALL_RETRIES + 1):
                if process_attempt > 0 and output_path:
                    Path(output_path).write_text("", encoding="utf-8")
                    self._write_trace(
                        trace_path,
                        "invocation.pre_work_retry",
                        process_attempt=process_attempt,
                        max_retries=_PRE_WORK_STALL_RETRIES,
                    )
                    logger.warning(
                        "Retrying Codex invocation after pre-work startup stall "
                        "attempt=%d/%d trace=%s",
                        process_attempt + 1,
                        _PRE_WORK_STALL_RETRIES + 1,
                        trace_path,
                    )
                try:
                    final_text, thread_id, stderr_text = await self._run_process(
                        command,
                        prompt,
                        output_type,
                        env=env,
                        trace_path=trace_path,
                        output_path=output_path,
                        process_attempt=process_attempt,
                        forbid_command_execution=bool(
                            role.metadata.get("forbid_command_execution")
                        ),
                    )
                    break
                except _CodexPreWorkStalled:
                    if process_attempt >= _PRE_WORK_STALL_RETRIES:
                        self._write_trace(
                            trace_path,
                            "invocation.pre_work_retries_exhausted",
                            process_attempt=process_attempt,
                            max_retries=_PRE_WORK_STALL_RETRIES,
                        )
                        raise
                    continue

            if not final_text and output_path:
                final_text = Path(output_path).read_text(encoding="utf-8").strip()

            if not final_text:
                details = stderr_text.strip() or "empty response"
                self._write_trace(
                    trace_path,
                    "invocation.empty_response",
                    stderr=_truncate_trace_text(stderr_text),
                    output_file_size=_file_size(output_path),
                )
                raise RuntimeError(f"Codex returned no final message: {details}")

            succeeded = True
            self._write_trace(
                trace_path,
                "invocation.completed",
                thread_id=thread_id,
                final_text_chars=len(final_text),
                output_file_size=_file_size(output_path),
            )
            return final_text, thread_id
        finally:
            if succeeded:
                for path in (schema_path, output_path):
                    if path and os.path.exists(path):
                        try:
                            os.unlink(path)
                        except OSError:
                            logger.debug("Failed to remove temporary file %s", path, exc_info=True)
            else:
                self._write_trace(
                    trace_path,
                    "invocation.diagnostics_preserved",
                    output_path=output_path if output_path and os.path.exists(output_path) else None,
                    output_file_size=_file_size(output_path),
                    output_schema_path=schema_path if schema_path and os.path.exists(schema_path) else None,
                    output_schema_snapshot_path=str(schema_snapshot_path) if schema_snapshot_path else None,
                    prompt_snapshot_path=str(prompt_snapshot_path) if prompt_snapshot_path else None,
                    trace_file=str(trace_path) if trace_path else None,
                )
                logger.warning(
                    "Codex invocation did not complete cleanly; diagnostics preserved "
                    "trace=%s output=%s schema=%s",
                    trace_path,
                    output_path if output_path and os.path.exists(output_path) else None,
                    schema_path if schema_path and os.path.exists(schema_path) else None,
                )
            if codex_home:
                shutil.rmtree(codex_home, ignore_errors=True)

    async def _run_process(
        self,
        command: list[str],
        prompt: str,
        output_type: type[BaseModel] | None,
        *,
        env: dict[str, str] | None = None,
        trace_path: Path | None = None,
        output_path: str | None = None,
        process_attempt: int = 0,
        forbid_command_execution: bool = False,
    ) -> tuple[str, str | None, str]:
        self._write_trace(
            trace_path,
            "process.launching",
            command=command,
            process_attempt=process_attempt,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            self._write_trace(
                trace_path,
                "process.launch_failed",
                error="codex command not found",
                process_attempt=process_attempt,
            )
            raise RuntimeError(
                "Could not start the Codex CLI. Ensure `codex` is installed and on PATH."
            ) from exc
        except Exception as exc:
            self._write_trace(
                trace_path,
                "process.launch_failed",
                error=repr(exc),
                process_attempt=process_attempt,
            )
            raise

        invocation_id = _current_invocation_var.get()
        if invocation_id:
            self._invocation_processes[invocation_id] = proc
        proc_pid = getattr(proc, "pid", None)
        self._write_trace(
            trace_path,
            "process.started",
            pid=proc_pid,
            invocation_id=invocation_id,
            process_attempt=process_attempt,
            output_file_size=_file_size(output_path),
        )
        logger.info(
            "Codex process started pid=%s invocation=%s trace=%s",
            proc_pid,
            invocation_id or "<none>",
            trace_path,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        self._write_trace(
            trace_path,
            "stdin.write_started",
            prompt_bytes=len(prompt.encode("utf-8")),
            process_attempt=process_attempt,
        )
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        self._write_trace(trace_path, "stdin.closed")

        state: dict[str, Any] = {
            "thread_id": None,
            "last_agent_message": "",
            "last_error": "",
            "stdout_lines": 0,
            "stderr_lines": 0,
            "stdout_events": 0,
            "last_event_type": "",
            "last_item_type": "",
            "last_command": "",
            "turn_started_monotonic": None,
            "turn_started_at": None,
            "substantive_event_seen": False,
            "pre_work_stall": False,
        }

        stdout_task = asyncio.create_task(
            self._read_stdout(
                proc.stdout,
                state=state,
                output_type=output_type,
                trace_path=trace_path,
                forbid_command_execution=forbid_command_execution,
            )
        )
        stderr_task = asyncio.create_task(
            self._read_stderr(proc.stderr, state=state, trace_path=trace_path)
        )
        wait_task = asyncio.create_task(proc.wait())
        monitor_task = asyncio.create_task(
            self._monitor_process(
                proc,
                state=state,
                trace_path=trace_path,
                output_path=output_path,
                process_attempt=process_attempt,
            )
        )

        stderr_text = ""
        return_code: int | None = None
        active_tasks: set[asyncio.Task[Any]] = {stdout_task, stderr_task, wait_task}

        try:
            while return_code is None:
                done, _pending = await asyncio.wait(
                    active_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stdout_task in done:
                    active_tasks.discard(stdout_task)
                    exc = stdout_task.exception()
                    if exc is not None:
                        self._write_trace(
                            trace_path,
                            "stdout.reader_failed",
                            error=repr(exc),
                        )
                        await self._abort_process(proc, wait_task, stderr_task, monitor_task)
                        raise RuntimeError(
                            "Codex stdout reader failed before process exit"
                        ) from exc
                if stderr_task in done:
                    active_tasks.discard(stderr_task)
                    exc = stderr_task.exception()
                    if exc is not None:
                        self._write_trace(
                            trace_path,
                            "stderr.reader_failed",
                            error=repr(exc),
                        )
                        await self._abort_process(proc, wait_task, stdout_task, monitor_task)
                        raise RuntimeError(
                            "Codex stderr reader failed before process exit"
                        ) from exc
                    stderr_text = stderr_task.result()
                if wait_task in done:
                    active_tasks.discard(wait_task)
                    return_code = wait_task.result()
        except asyncio.CancelledError:
            logger.warning("Codex invocation cancelled — killing subprocess %s", proc_pid)
            self._write_trace(
                trace_path,
                "process.cancelled",
                pid=proc_pid,
                output_file_size=_file_size(output_path),
                process_attempt=process_attempt,
                **{key: state.get(key) for key in (
                    "stdout_lines",
                    "stderr_lines",
                    "stdout_events",
                    "last_event_type",
                    "last_item_type",
                    "last_command",
                    "turn_started_at",
                    "substantive_event_seen",
                    "pre_work_stall",
                )},
            )
            await self._abort_process(proc, wait_task, stdout_task, stderr_task, monitor_task)
            raise

        if not stderr_task.done():
            stderr_text = await stderr_task
        if not stdout_task.done():
            await stdout_task
        if not monitor_task.done():
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task

        self._write_trace(
            trace_path,
            "process.exited",
            pid=proc_pid,
            return_code=return_code,
            output_file_size=_file_size(output_path),
            stderr_chars=len(stderr_text),
            process_attempt=process_attempt,
            **{key: state.get(key) for key in (
                "stdout_lines",
                "stderr_lines",
                "stdout_events",
                "last_event_type",
                "last_item_type",
                "last_command",
                "turn_started_at",
                "substantive_event_seen",
                "pre_work_stall",
            )},
        )

        if state.get("pre_work_stall"):
            raise _CodexPreWorkStalled(
                "Codex reached turn.started but emitted no reasoning, "
                "tool use, agent message, stderr, or output before the "
                f"{_PRE_WORK_STALL_SECONDS}s pre-work guard fired"
            )

        if return_code != 0:
            details = (
                state["last_error"].strip()
                or stderr_text.strip()
                or state["last_agent_message"]
                or "unknown error"
            )
            if "login" in details.lower():
                details += " Run `codex login` and sign in with ChatGPT or an API key."
            self._write_trace(
                trace_path,
                "process.failed",
                return_code=return_code,
                details=_truncate_trace_text(details),
                process_attempt=process_attempt,
            )
            raise RuntimeError(f"Codex CLI failed with exit code {return_code}: {details}")

        if self.on_message is not None:
            structured_payload = None
            if output_type and state["last_agent_message"]:
                try:
                    structured_payload = json.loads(state["last_agent_message"])
                except json.JSONDecodeError:
                    structured_payload = {}
            self._emit(ResultMessage(structured_output=structured_payload))

        return state["last_agent_message"], state["thread_id"], stderr_text

    async def _abort_process(
        self,
        proc: asyncio.subprocess.Process,
        wait_task: asyncio.Task[int],
        *reader_tasks: asyncio.Task[Any],
    ) -> None:
        if proc.returncode is None:
            pid = getattr(proc, "pid", None)
            with suppress(ProcessLookupError):
                if pid is not None:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    proc.kill()
        with suppress(asyncio.TimeoutError, ProcessLookupError):
            await asyncio.wait_for(wait_task, timeout=2)
        for task in reader_tasks:
            if not task.done():
                task.cancel()
        if reader_tasks:
            await asyncio.gather(*reader_tasks, return_exceptions=True)

    async def _monitor_process(
        self,
        proc: asyncio.subprocess.Process,
        *,
        state: dict[str, Any],
        trace_path: Path | None,
        output_path: str | None,
        process_attempt: int = 0,
    ) -> None:
        interval = _TRACE_HEARTBEAT_SECONDS
        if interval <= 0 and _PRE_WORK_STALL_SECONDS > 0:
            interval = min(60, max(1, _PRE_WORK_STALL_SECONDS))
        if interval <= 0:
            return

        started = asyncio.get_running_loop().time()
        while proc.returncode is None:
            await asyncio.sleep(interval)
            if proc.returncode is not None:
                break
            elapsed = asyncio.get_running_loop().time() - started
            heartbeat = {
                "pid": proc.pid,
                "elapsed_seconds": round(elapsed, 1),
                "output_file_size": _file_size(output_path),
                "return_code": proc.returncode,
                "stdout_lines": state.get("stdout_lines", 0),
                "stderr_lines": state.get("stderr_lines", 0),
                "stdout_events": state.get("stdout_events", 0),
                "last_event_type": state.get("last_event_type", ""),
                "last_item_type": state.get("last_item_type", ""),
                "last_command": _truncate_trace_text(state.get("last_command", ""), 300),
                "turn_started_at": state.get("turn_started_at"),
                "substantive_event_seen": state.get("substantive_event_seen", False),
                "pre_work_stall_seconds": _PRE_WORK_STALL_SECONDS,
                "process_attempt": process_attempt,
            }
            if _TRACE_HEARTBEAT_SECONDS > 0:
                self._write_trace(trace_path, "process.heartbeat", **heartbeat)
                logger.info(
                    "Codex heartbeat pid=%s elapsed=%.0fs trace=%s stdout_events=%s "
                    "stderr_lines=%s output_bytes=%s last_event=%s last_item=%s",
                    proc.pid,
                    elapsed,
                    trace_path,
                    heartbeat["stdout_events"],
                    heartbeat["stderr_lines"],
                    heartbeat["output_file_size"],
                    heartbeat["last_event_type"] or "<none>",
                    heartbeat["last_item_type"] or "<none>",
                )

            turn_started = state.get("turn_started_monotonic")
            if (
                _PRE_WORK_STALL_SECONDS > 0
                and turn_started is not None
                and not state.get("substantive_event_seen", False)
                and not state.get("pre_work_stall", False)
            ):
                pre_work_elapsed = asyncio.get_running_loop().time() - float(turn_started)
                if pre_work_elapsed >= _PRE_WORK_STALL_SECONDS:
                    state["pre_work_stall"] = True
                    state["last_error"] = (
                        "pre-work startup stall: turn.started emitted but no "
                        "reasoning/tool/message event followed"
                    )
                    self._write_trace(
                        trace_path,
                        "process.pre_work_stall",
                        pid=proc.pid,
                        process_attempt=process_attempt,
                        pre_work_elapsed_seconds=round(pre_work_elapsed, 1),
                        timeout_seconds=_PRE_WORK_STALL_SECONDS,
                        output_file_size=_file_size(output_path),
                        stdout_events=state.get("stdout_events", 0),
                        stderr_lines=state.get("stderr_lines", 0),
                        last_event_type=state.get("last_event_type", ""),
                        last_item_type=state.get("last_item_type", ""),
                    )
                    logger.warning(
                        "Codex pre-work startup stall pid=%s attempt=%d "
                        "elapsed=%.0fs trace=%s",
                        proc.pid,
                        process_attempt,
                        pre_work_elapsed,
                        trace_path,
                    )
                    with suppress(ProcessLookupError):
                        proc.terminate()
                    await asyncio.sleep(5)
                    if proc.returncode is None:
                        with suppress(ProcessLookupError):
                            proc.kill()
                    return

    async def _read_stdout(
        self,
        stdout: asyncio.StreamReader,
        *,
        state: dict[str, Any],
        output_type: type[BaseModel] | None,
        trace_path: Path | None = None,
        forbid_command_execution: bool = False,
    ) -> None:
        buffer = bytearray()

        def append_fragment(fragment: bytes) -> None:
            if not fragment:
                return
            if len(buffer) + len(fragment) > _STDOUT_LINE_MAX_BYTES:
                state["last_error"] = (
                    "Codex stdout line exceeded "
                    f"{_STDOUT_LINE_MAX_BYTES} bytes before newline"
                )
                state["stdout_line_too_large"] = True
                state["stdout_line_bytes"] = len(buffer) + len(fragment)
                self._write_trace(
                    trace_path,
                    "stdout.line_too_large",
                    pending_bytes=len(buffer),
                    fragment_bytes=len(fragment),
                    max_bytes=_STDOUT_LINE_MAX_BYTES,
                )
                raise RuntimeError(state["last_error"])
            buffer.extend(fragment)

        while True:
            chunk = await stdout.read(_STDOUT_READ_CHUNK)
            if not chunk:
                break
            start = 0
            while start < len(chunk):
                newline = chunk.find(b"\n", start)
                if newline < 0:
                    append_fragment(chunk[start:])
                    break
                append_fragment(chunk[start:newline])
                line = bytes(buffer).decode("utf-8", errors="replace")
                buffer.clear()
                self._handle_stdout_line(
                    line,
                    state=state,
                    output_type=output_type,
                    trace_path=trace_path,
                    forbid_command_execution=forbid_command_execution,
                )
                start = newline + 1

        if bytes(buffer).strip():
            line = bytes(buffer).decode("utf-8", errors="replace")
            self._handle_stdout_line(
                line,
                state=state,
                output_type=output_type,
                trace_path=trace_path,
                forbid_command_execution=forbid_command_execution,
            )
        self._write_trace(
            trace_path,
            "stdout.reader_done",
            stdout_lines=state.get("stdout_lines", 0),
            stdout_events=state.get("stdout_events", 0),
            last_event_type=state.get("last_event_type", ""),
            last_item_type=state.get("last_item_type", ""),
        )

    def _handle_stdout_line(
        self,
        line: str,
        *,
        state: dict[str, Any],
        output_type: type[BaseModel] | None,
        trace_path: Path | None,
        forbid_command_execution: bool = False,
    ) -> None:
        del output_type  # reserved for future event-specific handling

        text = line.strip()
        if not text:
            return
        state["stdout_lines"] = int(state.get("stdout_lines", 0) or 0) + 1
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON Codex stdout line: %s", text)
            self._write_trace(
                trace_path,
                "stdout.non_json",
                line=_truncate_trace_text(text),
            )
            return

        event_type = str(event.get("type") or "")
        state["stdout_events"] = int(state.get("stdout_events", 0) or 0) + 1
        state["last_event_type"] = event_type

        if event.get("type") == "thread.started":
            state["thread_id"] = event.get("thread_id")
            self._write_trace(
                trace_path,
                "stdout.thread_started",
                thread_id=state["thread_id"],
            )
            return

        if event.get("type") == "turn.started":
            state["turn_started_monotonic"] = asyncio.get_running_loop().time()
            state["turn_started_at"] = _utc_now_iso()
            self._write_trace(
                trace_path,
                "stdout.turn_started",
                turn_started_at=state["turn_started_at"],
            )
            return

        if event.get("type") == "error":
            state["last_error"] = event.get("message", "")
            state["substantive_event_seen"] = True
            self._write_trace(
                trace_path,
                "stdout.error",
                message=_truncate_trace_text(state["last_error"]),
            )
            return

        if event.get("type") == "turn.failed":
            error = event.get("error") or {}
            state["last_error"] = error.get("message", "") or state["last_error"]
            state["substantive_event_seen"] = True
            self._write_trace(
                trace_path,
                "stdout.turn_failed",
                message=_truncate_trace_text(state["last_error"]),
            )
            return

        item = event.get("item") or {}
        item_type = item.get("type")
        item_id = item.get("id")
        state["last_item_type"] = item_type or ""

        if item_type == "reasoning" and event.get("type") == "item.completed":
            state["substantive_event_seen"] = True
            self._write_trace(
                trace_path,
                "stdout.reasoning_completed",
                item_id=item_id,
                text_chars=len(str(item.get("text", ""))),
            )
            self._emit(AssistantMessage([ThinkingBlock(item.get("text", ""))], id=item_id))
        elif item_type == "command_execution":
            if event.get("type") == "item.started":
                state["substantive_event_seen"] = True
                state["last_command"] = item.get("command", "") or ""
                if forbid_command_execution:
                    state["last_error"] = (
                        "command execution is disabled for this role; use the "
                        "configured MCP evidence tools instead"
                    )
                    self._write_trace(
                        trace_path,
                        "stdout.command_forbidden",
                        item_id=item_id,
                        command=_truncate_trace_text(state["last_command"]),
                    )
                    raise RuntimeError(state["last_error"])
                self._write_trace(
                    trace_path,
                    "stdout.command_started",
                    item_id=item_id,
                    command=_truncate_trace_text(state["last_command"]),
                )
                self._emit(
                    AssistantMessage(
                        [
                            ToolUseBlock(
                                name="Bash",
                                input={"command": item.get("command", "")},
                            )
                        ],
                        id=item_id,
                    )
                )
            elif event.get("type") == "item.completed":
                state["substantive_event_seen"] = True
                self._write_trace(
                    trace_path,
                    "stdout.command_completed",
                    item_id=item_id,
                    exit_code=item.get("exit_code"),
                    output_chars=len(str(item.get("aggregated_output", ""))),
                    is_error=(item.get("exit_code") or 0) != 0,
                )
                self._emit(
                    AssistantMessage(
                        [
                            ToolResultBlock(
                                content=item.get("aggregated_output"),
                                is_error=(item.get("exit_code") or 0) != 0,
                            )
                        ],
                        id=f"{item_id}:result",
                    )
                )
        elif item_type == "agent_message" and event.get("type") == "item.completed":
            message = item.get("text", "")
            state["last_agent_message"] = message
            state["substantive_event_seen"] = True
            self._write_trace(
                trace_path,
                "stdout.agent_message_completed",
                item_id=item_id,
                text_chars=len(message),
            )
            self._emit(AssistantMessage([TextBlock(message)], id=item_id))
        elif item_type == "mcp_tool_call":
            state["substantive_event_seen"] = True
            self._write_trace(
                trace_path,
                "stdout.mcp_tool_call",
                event_type=event_type,
                item_id=item_id,
                item=_truncate_trace_text(json.dumps(item, sort_keys=True, default=str), 4000),
            )
        else:
            if item_type:
                state["substantive_event_seen"] = True
            self._write_trace(
                trace_path,
                "stdout.event",
                event_type=event_type,
                item_type=item_type,
                item_id=item_id,
            )

    async def _read_stderr(
        self,
        stderr: asyncio.StreamReader,
        *,
        state: dict[str, Any],
        trace_path: Path | None,
    ) -> str:
        lines: collections.deque[str] = collections.deque()
        total_chars = 0
        pending = bytearray()
        pending_truncated_bytes = 0

        def append_tail(text: str) -> None:
            nonlocal total_chars
            if len(text) > _STDERR_LINE_CHARS:
                omitted = len(text) - _STDERR_LINE_CHARS
                text = f"{text[:_STDERR_LINE_CHARS]}...<truncated {omitted} stderr chars>"
                state["stderr_truncated_lines"] = int(state.get("stderr_truncated_lines", 0) or 0) + 1
            while total_chars + len(text) > _STDERR_CAPTURE_CHARS and lines:
                removed = lines.popleft()
                total_chars -= len(removed)
                state["stderr_dropped_tail_lines"] = int(
                    state.get("stderr_dropped_tail_lines", 0) or 0
                ) + 1
            while len(lines) >= _STDERR_CAPTURE_LINES and lines:
                removed = lines.popleft()
                total_chars -= len(removed)
                state["stderr_dropped_tail_lines"] = int(
                    state.get("stderr_dropped_tail_lines", 0) or 0
                ) + 1
            lines.append(text)
            total_chars += len(text)

        def append_fragment(fragment: bytes) -> None:
            nonlocal pending_truncated_bytes
            if not fragment:
                return
            remaining = max(0, _STDERR_LINE_CHARS * 4 - len(pending))
            if remaining:
                pending.extend(fragment[:remaining])
            if len(fragment) > remaining:
                pending_truncated_bytes += len(fragment) - remaining

        def emit_pending() -> None:
            nonlocal pending_truncated_bytes
            text = bytes(pending).decode("utf-8", errors="replace").rstrip()
            if pending_truncated_bytes:
                text = (
                    f"{text}...<truncated {pending_truncated_bytes} stderr bytes before newline>"
                )
                state["stderr_truncated_lines"] = int(state.get("stderr_truncated_lines", 0) or 0) + 1
            pending.clear()
            pending_truncated_bytes = 0
            if not text:
                return
            state["stderr_lines"] = int(state.get("stderr_lines", 0) or 0) + 1
            append_tail(text)
            self._write_trace(
                trace_path,
                "stderr.line",
                line=_truncate_trace_text(text),
                stderr_lines=state["stderr_lines"],
            )

        while True:
            chunk = await stderr.read(_STDOUT_READ_CHUNK)
            if not chunk:
                break
            start = 0
            while start < len(chunk):
                newline = chunk.find(b"\n", start)
                if newline < 0:
                    append_fragment(chunk[start:])
                    break
                append_fragment(chunk[start:newline])
                emit_pending()
                start = newline + 1
        if pending or pending_truncated_bytes:
            emit_pending()
        self._write_trace(
            trace_path,
            "stderr.reader_done",
            stderr_lines=state.get("stderr_lines", 0),
            stderr_tail_lines=len(lines),
            stderr_tail_chars=total_chars,
            stderr_truncated_lines=state.get("stderr_truncated_lines", 0),
            stderr_dropped_tail_lines=state.get("stderr_dropped_tail_lines", 0),
        )
        return "\n".join(lines)

    def _emit(self, message: Any) -> None:
        invocation_id = _current_invocation_var.get()
        if invocation_id:
            sink = self._invocation_activity.get(invocation_id)
            if callable(sink):
                sink()
        if self.on_message is not None:
            self.on_message(message)

    def _resolve_model(self, role: Role) -> str | None:
        env_model = os.environ.get("IRIAI_CODEX_MODEL", "").strip()
        if env_model:
            return env_model

        model = (role.model or "").strip()
        if not model:
            return None

        normalized = model.lower()
        if normalized.startswith("gpt-") or normalized.startswith("o") or "codex" in normalized:
            return model

        warning_key = ("model", role.name)
        if warning_key not in self._warned_roles:
            logger.info(
                "Ignoring Claude-specific model '%s' for role %s when using Codex runtime",
                model,
                role.name,
            )
            self._warned_roles.add(warning_key)
        return None

    def _wants_e2e_access(
        self,
        role: Role,
        *,
        session_key: str | None = None,
    ) -> bool:
        identifiers = {role.name.lower()}
        actor_name = self._actor_name(session_key)
        if actor_name:
            identifiers.add(actor_name.lower())

        declared_servers = set((role.metadata.get("mcp_servers") or {}).keys())
        if declared_servers & set(_E2E_MCP_SERVER_NAMES):
            return True

        if role.name.lower() in _ROLE_E2E_NAMES:
            return True

        for identifier in identifiers:
            if any(marker in identifier for marker in _ROLE_E2E_MARKERS):
                return True

        return False

    def _actor_name(self, session_key: str | None) -> str | None:
        if not session_key:
            return None
        actor_name, _sep, _feature_id = session_key.partition(":")
        return actor_name or None

    def _effective_mcp_servers(
        self,
        role: Role,
        *,
        workspace: Workspace | None,
        feature_id: str | None,
        session_key: str | None,
    ) -> dict[str, dict[str, Any]]:
        servers = copy.deepcopy(role.metadata.get("mcp_servers") or {})

        if self._wants_e2e_access(role, session_key=session_key):
            for name in _E2E_MCP_SERVER_NAMES:
                if name not in servers and name in MCP_SERVERS:
                    servers[name] = copy.deepcopy(MCP_SERVERS[name])

        for name, config in list(servers.items()):
            config = copy.deepcopy(config)
            env = dict(config.get("env") or {})
            if name == "preview":
                token = os.environ.get("RAILWAY_TOKEN", "").strip()
                if token:
                    env["RAILWAY_TOKEN"] = token
            elif name == "github":
                token = os.environ.get("GITHUB_TOKEN", "").strip()
                if token:
                    env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
            if env:
                config["env"] = env
            elif "env" in config:
                config.pop("env")
            servers[name] = config

        if "postgres" in servers:
            database_url = self._discover_database_url(workspace=workspace, feature_id=feature_id)
            if database_url:
                postgres = copy.deepcopy(servers["postgres"])
                args = [
                    arg for arg in postgres.get("args", [])
                    if not self._looks_like_database_url(arg)
                ]
                args.append(database_url)
                postgres["args"] = args
                servers["postgres"] = postgres
            else:
                warning_key = ("postgres-missing", role.name, feature_id or "")
                if warning_key not in self._warned_roles:
                    logger.warning(
                        "Role %s requested Postgres MCP but no database URL was discovered for feature %s",
                        role.name,
                        feature_id or "<unknown>",
                    )
                    self._warned_roles.add(warning_key)
                servers.pop("postgres", None)

        return servers

    def _discover_database_url(
        self,
        *,
        workspace: Workspace | None,
        feature_id: str | None,
    ) -> str | None:
        for env_name in ("IRIAI_E2E_DATABASE_URL", "DATABASE_URL"):
            value = self._normalize_database_url(os.environ.get(env_name, ""))
            if value:
                return value

        for path in self._candidate_env_files(workspace=workspace, feature_id=feature_id):
            value = self._read_database_url_from_env_file(path)
            if value:
                return value

        return None

    def _candidate_env_files(
        self,
        *,
        workspace: Workspace | None,
        feature_id: str | None,
    ) -> list[Path]:
        candidates: list[Path] = []
        seen: set[Path] = set()

        def _add(path: Path) -> None:
            resolved = path.resolve()
            if resolved in seen or not resolved.exists() or not resolved.is_file():
                return
            seen.add(resolved)
            candidates.append(resolved)

        workspace_path = Path(workspace.path).resolve() if workspace and workspace.path else None
        workspace_root = self._workspace_root(workspace)

        if workspace_root and feature_id:
            features_root = workspace_root / ".iriai" / "features"
            if features_root.exists():
                for feature_dir in sorted(
                    path for path in features_root.iterdir()
                    if path.is_dir() and feature_id in path.name
                ):
                    for env_name in _ENV_FILE_NAMES:
                        for env_path in sorted(feature_dir.rglob(env_name)):
                            _add(env_path)

        if workspace_path:
            bases = [workspace_path, *workspace_path.parents[:4]]
            for base in bases:
                for env_name in _ENV_FILE_NAMES:
                    _add(base / env_name)

        def _priority(path: Path) -> tuple[int, int, str]:
            try:
                name_priority = _ENV_FILE_NAMES.index(path.name)
            except ValueError:
                name_priority = len(_ENV_FILE_NAMES)
            return (name_priority, len(path.parts), str(path))

        return sorted(candidates, key=_priority)

    def _workspace_root(self, workspace: Workspace | None) -> Path | None:
        if not workspace or not workspace.path:
            return None

        path = Path(workspace.path).resolve()
        for candidate in (path, *path.parents):
            if candidate.name == ".iriai":
                return candidate.parent
        return path

    def _read_database_url_from_env_file(self, path: Path) -> str | None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if not line.startswith("DATABASE_URL="):
                continue
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return self._normalize_database_url(value)
        return None

    def _normalize_database_url(self, value: str) -> str | None:
        url = value.strip()
        if not url or "$" in url:
            return None
        if url.startswith("postgresql+"):
            _scheme, _sep, remainder = url.partition("://")
            if remainder:
                return f"postgresql://{remainder}"
        return url

    def _looks_like_database_url(self, value: str) -> bool:
        lowered = value.lower()
        return lowered.startswith("postgres://") or lowered.startswith("postgresql://")

    def _mcp_config_flags(self, role: Role) -> list[str]:
        """Convert role MCP server config to Codex ``-c`` CLI flags."""
        servers: dict[str, dict[str, Any]] = role.metadata.get("mcp_servers") or {}
        flags: list[str] = []
        for name, config in servers.items():
            command = config.get("command")
            if command:
                flags.extend(["-c", f'mcp_servers.{name}.command="{command}"'])
            args = config.get("args")
            if args:
                toml_array = "[" + ", ".join(f'"{a}"' for a in args) + "]"
                flags.extend(["-c", f"mcp_servers.{name}.args={toml_array}"])
            env = config.get("env")
            if isinstance(env, dict):
                for env_key, env_val in env.items():
                    flags.extend(["-c", f'mcp_servers.{name}.env.{env_key}="{env_val}"'])
        return flags

    def _log_runtime_differences(self, role: Role) -> None:
        mcp = role.metadata.get("mcp_servers")
        if mcp:
            warning_key = ("mcp", role.name)
            if warning_key not in self._warned_roles:
                logger.debug(
                    "Role %s: loading %d declared MCP server(s) via per-invocation CODEX_HOME: %s",
                    role.name, len(mcp), ", ".join(mcp.keys()),
                )
                self._warned_roles.add(warning_key)

        if "WebSearch" in role.tools or "WebFetch" in role.tools:
            warning_key = ("web", role.name)
            if warning_key not in self._warned_roles:
                logger.info(
                    "Role %s expects Claude web tools; Codex exec will rely on Codex CLI defaults for network access",
                    role.name,
                )
                self._warned_roles.add(warning_key)
