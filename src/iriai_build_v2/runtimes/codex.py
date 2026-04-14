from __future__ import annotations

import asyncio
import copy
import contextvars
import json
import logging
import os
import shutil
import tempfile
import tomllib
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
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
        if self._wants_e2e_access(role, session_key=session_key):
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
        args.extend(["--add-dir", os.path.expanduser("~/.npm")])

        if workspace and workspace.path:
            args.extend(["-C", str(workspace.path)])

        if resume_thread_id:
            args.append(resume_thread_id)

        args.append("-")
        return args

    def _runtime_temp_dir(self, workspace: Workspace | None) -> str | None:
        """Choose a workspace-local temp dir so helper files stay inside `.iriai`."""
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

    def _prepare_codex_home(
        self,
        role: Role,
        workspace: Workspace | None,
        *,
        feature_id: str | None = None,
        session_key: str | None = None,
    ) -> str:
        """Create a per-invocation CODEX_HOME with only the role's MCP servers.

        This prevents the global ``~/.codex/config.toml`` from loading all 7
        MCP servers for every invocation.  Each role declares which servers it
        needs via ``mcp_servers_for(...)``; only those are included.
        """
        temp_dir = self._runtime_temp_dir(workspace)
        base = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
        codex_home = base / "codex_homes" / str(uuid.uuid4())
        codex_home.mkdir(parents=True, exist_ok=True)

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
        temp_dir = self._runtime_temp_dir(workspace)
        try:
            if output_type:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".json", dir=temp_dir, delete=False
                ) as schema_file:
                    json.dump(_prepare_schema(output_type.model_json_schema()), schema_file)
                    schema_path = schema_file.name

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
            codex_home = self._prepare_codex_home(
                role,
                workspace,
                feature_id=feature_id,
                session_key=session_key,
            )
            env = {**os.environ, "CODEX_HOME": codex_home}

            final_text, thread_id, stderr_text = await self._run_process(
                command, prompt, output_type, env=env,
            )

            if not final_text and output_path:
                final_text = Path(output_path).read_text(encoding="utf-8").strip()

            if not final_text:
                details = stderr_text.strip() or "empty response"
                raise RuntimeError(f"Codex returned no final message: {details}")

            return final_text, thread_id
        finally:
            for path in (schema_path, output_path):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        logger.debug("Failed to remove temporary file %s", path, exc_info=True)
            if codex_home:
                shutil.rmtree(codex_home, ignore_errors=True)

    async def _run_process(
        self,
        command: list[str],
        prompt: str,
        output_type: type[BaseModel] | None,
        *,
        env: dict[str, str] | None = None,
    ) -> tuple[str, str | None, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Could not start the Codex CLI. Ensure `codex` is installed and on PATH."
            ) from exc

        invocation_id = _current_invocation_var.get()
        if invocation_id:
            self._invocation_processes[invocation_id] = proc

        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        state: dict[str, Any] = {
            "thread_id": None,
            "last_agent_message": "",
            "last_error": "",
        }

        stdout_task = asyncio.create_task(
            self._read_stdout(proc.stdout, state=state, output_type=output_type)
        )
        stderr_task = asyncio.create_task(self._read_stderr(proc.stderr))
        wait_task = asyncio.create_task(proc.wait())

        stderr_text = ""
        return_code: int | None = None

        try:
            while return_code is None:
                done, _pending = await asyncio.wait(
                    {stdout_task, stderr_task, wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stdout_task in done:
                    exc = stdout_task.exception()
                    if exc is not None:
                        await self._abort_process(proc, wait_task, stderr_task)
                        raise RuntimeError(
                            "Codex stdout reader failed before process exit"
                        ) from exc
                if stderr_task in done:
                    exc = stderr_task.exception()
                    if exc is not None:
                        await self._abort_process(proc, wait_task, stdout_task)
                        raise RuntimeError(
                            "Codex stderr reader failed before process exit"
                        ) from exc
                    stderr_text = stderr_task.result()
                if wait_task in done:
                    return_code = wait_task.result()
        except asyncio.CancelledError:
            logger.warning("Codex invocation cancelled — killing subprocess %s", proc.pid)
            await self._abort_process(proc, wait_task, stdout_task, stderr_task)
            raise

        if not stderr_task.done():
            stderr_text = await stderr_task
        if not stdout_task.done():
            await stdout_task

        if return_code != 0:
            details = (
                state["last_error"].strip()
                or stderr_text.strip()
                or state["last_agent_message"]
                or "unknown error"
            )
            if "login" in details.lower():
                details += " Run `codex login` and sign in with ChatGPT or an API key."
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
            with suppress(ProcessLookupError):
                proc.kill()
        with suppress(asyncio.TimeoutError, ProcessLookupError):
            await asyncio.wait_for(wait_task, timeout=2)
        for task in reader_tasks:
            if not task.done():
                task.cancel()
        if reader_tasks:
            await asyncio.gather(*reader_tasks, return_exceptions=True)

    async def _read_stdout(
        self,
        stdout: asyncio.StreamReader,
        *,
        state: dict[str, Any],
        output_type: type[BaseModel] | None,
    ) -> None:
        buffer = ""
        while True:
            chunk = await stdout.read(_STDOUT_READ_CHUNK)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            while True:
                newline = buffer.find("\n")
                if newline < 0:
                    break
                line = buffer[:newline]
                buffer = buffer[newline + 1 :]
                self._handle_stdout_line(
                    line,
                    state=state,
                    output_type=output_type,
                )

        if buffer.strip():
            self._handle_stdout_line(
                buffer,
                state=state,
                output_type=output_type,
            )

    def _handle_stdout_line(
        self,
        line: str,
        *,
        state: dict[str, Any],
        output_type: type[BaseModel] | None,
    ) -> None:
        del output_type  # reserved for future event-specific handling

        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON Codex stdout line: %s", text)
            return

        if event.get("type") == "thread.started":
            state["thread_id"] = event.get("thread_id")
            return

        if event.get("type") == "error":
            state["last_error"] = event.get("message", "")
            return

        if event.get("type") == "turn.failed":
            error = event.get("error") or {}
            state["last_error"] = error.get("message", "") or state["last_error"]
            return

        item = event.get("item") or {}
        item_type = item.get("type")
        item_id = item.get("id")

        if item_type == "reasoning" and event.get("type") == "item.completed":
            self._emit(AssistantMessage([ThinkingBlock(item.get("text", ""))], id=item_id))
        elif item_type == "command_execution":
            if event.get("type") == "item.started":
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
            self._emit(AssistantMessage([TextBlock(message)], id=item_id))

    async def _read_stderr(self, stderr: asyncio.StreamReader) -> str:
        lines: list[str] = []
        while True:
            line = await stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                lines.append(text)
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
