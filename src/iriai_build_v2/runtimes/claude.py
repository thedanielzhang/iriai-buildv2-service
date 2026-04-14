from __future__ import annotations

import contextvars
import logging
import os
from contextlib import asynccontextmanager
from collections.abc import Callable
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
# File-path parameter name per tool.
_PATH_PARAMS: dict[str, str] = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "file_path",
}


def _make_write_guard(allowed_dir: str) -> Any:
    """Return an async ``can_use_tool`` callback that denies writes outside *allowed_dir*.

    Reads (Glob, Grep, Read, Bash without mutations) are unrestricted.
    Writes via Edit/Write/MultiEdit/NotebookEdit are checked: the target
    ``file_path`` must resolve to a location under *allowed_dir*.
    """
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    resolved_root = os.path.realpath(allowed_dir)

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

        resolved = os.path.realpath(target)
        if resolved == resolved_root or resolved.startswith(resolved_root + os.sep):
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

        if use_interactive:
            result_msg = await self._invoke_interactive(
                options, effective_prompt, session_key, ResultMessage
            )
        else:
            result_msg = await self._invoke_default(options, effective_prompt, ResultMessage)

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

    async def _invoke_default(self, options: Any, prompt: str, ResultMessage: type) -> Any:
        """Ephemeral client, receive_response(). Original code path."""
        from claude_agent_sdk import ClaudeSDKClient

        invocation_id = _current_invocation_var.get()
        async with ClaudeSDKClient(options=options) as client:
                if invocation_id:
                    self._active_invocations.add(invocation_id)
                await client.query(prompt)
                result_msg = None
                try:
                    async for msg in client.receive_response():
                        self._emit_message(msg)
                        if isinstance(msg, ResultMessage):
                            result_msg = msg
                finally:
                    if invocation_id:
                        self._active_invocations.discard(invocation_id)
        return result_msg

    async def _invoke_interactive(
        self, options: Any, prompt: str, session_key: str | None, ResultMessage: type
    ) -> Any:
        """Like default path but registers client for mid-stream injection."""
        from claude_agent_sdk import ClaudeSDKClient

        feature_id = session_key.rsplit(":", 1)[-1] if session_key else None
        invocation_id = _current_invocation_var.get()

        async with ClaudeSDKClient(options=options) as client:
            if invocation_id:
                self._active_invocations.add(invocation_id)
            if session_key:
                self._active_clients[session_key] = client
                self._pending_counts[session_key] = 1
            if feature_id:
                self._feature_sessions[feature_id] = session_key

            try:
                await client.query(prompt)
                result_msg = None
                async for msg in client.receive_response():
                    self._emit_message(msg)
                    if isinstance(msg, ResultMessage):
                        result_msg = msg
            finally:
                if invocation_id:
                    self._active_invocations.discard(invocation_id)
                if session_key:
                    self._active_clients.pop(session_key, None)
                    self._pending_counts.pop(session_key, None)
                if feature_id:
                    self._feature_sessions.pop(feature_id, None)

        return result_msg

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
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            result = None
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    result = msg
            return result.result if result else ""

    def _build_options(
        self,
        role: Role,
        workspace: Workspace | None,
        output_type: type[BaseModel] | None = None,
    ) -> Any:
        """Construct ClaudeAgentOptions from a role."""
        from claude_agent_sdk import ClaudeAgentOptions

        cwd = str(workspace.path) if workspace else None

        # Write isolation: use can_use_tool callback to deny Edit/Write
        # outside the workspace.  The sandbox setting only restricts Bash
        # commands (Seatbelt/bubblewrap); Edit/Write bypass it entirely.
        write_guard = None
        sandbox = None
        if cwd and role.metadata.get("sandbox", True):
            write_guard = _make_write_guard(cwd)
            sandbox = {
                "enabled": True,
            }

        options = ClaudeAgentOptions(
            system_prompt=role.prompt,
            allowed_tools=role.tools,
            model=role.model or "claude-sonnet-4-6",
            cwd=cwd,
            permission_mode="bypassPermissions",
            effort=role.effort if role.effort is not None else "high",
            max_buffer_size=50 * 1024 * 1024,  # 50MB — agents may glob large dirs
            sandbox=sandbox,
            can_use_tool=write_guard,
        )

        if cwd:
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
