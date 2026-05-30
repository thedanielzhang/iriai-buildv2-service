from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from iriai_compose import (
    DefaultWorkflowRunner,
    Feature,
    Workflow,
)
from iriai_compose.actors import AgentActor, InteractionActor
from iriai_compose.pending import Pending
from pydantic import BaseModel
from iriai_compose.prompts import Select
from iriai_compose.tasks import Ask

from ..agent_concurrency import AgentConcurrencyLimiter

if TYPE_CHECKING:
    from iriai_compose.runner import AgentRuntime

    from ..storage.features import PostgresFeatureStore

logger = logging.getLogger(__name__)

# Per-coroutine workspace override. Each asyncio.Task gets its own copy,
# so parallel tasks don't race on the shared _workspaces dict.
_workspace_override_var: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "_workspace_override", default=None,
)
_phase_name_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_phase_name", default="",
)
_invocation_observer_var: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "_invocation_observer", default=None,
)

# ── Liveness watchdog ──────────────────────────────────────────────────
# Both runtimes emit on_message callbacks whenever the agent does anything
# (tool calls, thinking, text). A stuck agent emits nothing. The watchdog
# kills invocations that go silent for too long — without affecting
# legitimately long-running agents that are actively working.

LIVENESS_TIMEOUT = 10 * 60  # 10 min of silence → agent is stuck
LIVENESS_POLL_INTERVAL = 30  # check every 30s
RESOLVE_MAX_RETRIES = 2  # retry stuck invocations up to 2 times
RESOLVE_RETRY_BACKOFF = 15  # seconds between retries
# When the liveness watchdog cancels a stalled invocation, the task may be parked
# in an UNCANCELLABLE await — e.g. the Claude SDK's anyio-shielded teardown or a
# blocking process.wait() on an already-exited CLI swallows the cancellation.
# Awaiting it unconditionally re-hangs the watchdog and starves the very liveness
# check meant to break the stall. Join only for a bounded interval, then abandon
# the orphaned task; asyncio.wait always returns on schedule. Mirrors the
# poll-and-abandon dispatch watchdog in runtimes/claude.py.
_WATCHDOG_ABANDON_JOIN_SECONDS = 15
RUNTIME_WORKSPACE_BINDING_KEY = "runtime_workspace_binding"
_WRITE_PRODUCING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}


class AgentStalled(RuntimeError):
    """Raised when an agent invocation produces no output for too long."""


class WorkflowQuiesced(RuntimeError):
    """Raised when a workflow reaches an intentional pause boundary."""

    def __init__(
        self,
        *,
        phase_name: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.phase_name = phase_name
        self.reason = reason
        self.metadata = metadata or {}
        super().__init__(reason or f"Workflow quiesced in phase {phase_name}")


@dataclass
class WorkflowQuiesceResult:
    """Last intentional workflow pause observed by the runner."""

    phase_name: str
    reason: str
    metadata: dict[str, Any]


def _prompt_observability(prompt: str) -> dict[str, Any]:
    preview = " ".join(prompt.strip().split())
    if len(preview) > 800:
        preview = preview[:797].rstrip() + "..."
    return {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_preview": preview,
        "prompt_length": len(prompt),
    }


def _combined_ask_prompt(task: Ask, *, prompt: str, context: str) -> str:
    task_prompt = task.model_copy(update={"prompt": prompt}).to_prompt()
    return f"{context}\n\n## Task\n{task_prompt}" if context else task_prompt


def _offloaded_prompt_path(prompt: str) -> str:
    match = re.search(r"in `([^`]+)`", prompt)
    return match.group(1) if match else ""


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_is_under(left, right) or _path_is_under(right, left)


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


def _parse_binding_expires_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        expires = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            expires = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires.astimezone(timezone.utc)


def _role_metadata_for_binding(metadata: Mapping[str, Any]) -> dict[str, Any]:
    role_metadata: dict[str, Any] = {}
    for key, value in metadata.items():
        if key == RUNTIME_WORKSPACE_BINDING_KEY:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            role_metadata[str(key)] = value
        elif isinstance(value, (list, tuple, set)):
            role_metadata[str(key)] = [
                item if isinstance(item, (str, int, float, bool)) or item is None else str(item)
                for item in value
            ]
        elif isinstance(value, Mapping):
            role_metadata[str(key)] = {
                str(inner_key): (
                    inner_value
                    if isinstance(inner_value, (str, int, float, bool)) or inner_value is None
                    else str(inner_value)
                )
                for inner_key, inner_value in value.items()
            }
        else:
            role_metadata[str(key)] = str(value)
    return role_metadata


def _coerce_runtime_workspace_binding(
    role: Any,
    *,
    actor_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    metadata = role.metadata or {}
    if RUNTIME_WORKSPACE_BINDING_KEY not in metadata:
        return None, None

    raw = metadata.get(RUNTIME_WORKSPACE_BINDING_KEY)
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, Mapping):
        return None, "runtime workspace binding must be a mapping"

    binding = dict(raw)
    cwd = binding.get("cwd") or binding.get("workspace_override")
    if cwd is not None:
        binding["cwd"] = str(Path(str(cwd)).expanduser())
    if binding.get("cwd") and not binding.get("workspace_override"):
        binding["workspace_override"] = binding["cwd"]
    for key in ("writable_roots", "readonly_roots", "blocked_roots", "contract_ids"):
        binding[key] = _as_string_list(binding.get(key))
    repo_roots_raw = binding.get("repo_roots")
    if isinstance(repo_roots_raw, Mapping):
        binding["repo_roots"] = {
            str(key): str(value)
            for key, value in repo_roots_raw.items()
            if str(key).strip() and str(value).strip()
        }
    else:
        binding["repo_roots"] = _as_string_list(repo_roots_raw)
    binding.setdefault("runtime", metadata.get("runtime"))
    binding.setdefault(
        "role",
        {
            "actor_name": actor_name,
            "name": getattr(role, "name", ""),
            "tools": [str(tool) for tool in (getattr(role, "tools", None) or [])],
            "metadata": _role_metadata_for_binding(metadata),
        },
    )
    return binding, _runtime_workspace_binding_error(
        binding,
        validate_manifest=_role_is_write_producing(role),
    )


def _runtime_workspace_binding_error(
    binding: Mapping[str, Any],
    *,
    validate_manifest: bool = False,
) -> str | None:
    sandbox_id = str(binding.get("sandbox_id") or "").strip()
    if not sandbox_id:
        return "missing sandbox_id"

    cwd = str(binding.get("cwd") or "").strip()
    if not cwd:
        return "missing cwd"

    cwd_path = Path(cwd).expanduser()
    if not cwd_path.is_absolute():
        return "cwd must be absolute"
    if not cwd_path.exists():
        return f"cwd does not exist: {cwd}"
    if not cwd_path.is_dir():
        return f"cwd is not a directory: {cwd}"
    if cwd_path.is_symlink():
        return f"cwd is symlinked: {cwd}"

    workspace_override = str(binding.get("workspace_override") or "").strip()
    if workspace_override and Path(workspace_override).expanduser().resolve() != cwd_path.resolve():
        return "workspace_override must match cwd"

    cwd_resolved = cwd_path.resolve()
    blocked_roots = [
        Path(path).expanduser().resolve()
        for path in _as_string_list(binding.get("blocked_roots"))
        if str(path).strip()
    ]
    if any(_path_is_under(cwd_resolved, blocked) for blocked in blocked_roots):
        return "cwd resolves into a blocked root"
    repo_roots_value = binding.get("repo_roots")
    repo_root_paths = (
        list(repo_roots_value.values())
        if isinstance(repo_roots_value, Mapping)
        else _as_string_list(repo_roots_value)
    )
    repo_roots = [
        Path(path).expanduser().resolve()
        for path in repo_root_paths
        if str(path).strip()
    ]
    writable_roots = [
        Path(path).expanduser().resolve()
        for path in _as_string_list(binding.get("writable_roots"))
        if str(path).strip()
    ]
    if repo_roots and not any(_path_is_under(cwd_resolved, root) for root in repo_roots):
        return "cwd is outside runtime workspace roots"
    if repo_roots and writable_roots and any(
        not any(_path_is_under(root, repo_root) for repo_root in repo_roots)
        for root in writable_roots
    ):
        return "writable root is outside runtime workspace roots"
    if writable_roots and not any(_paths_overlap(cwd_resolved, root) for root in writable_roots):
        return "cwd is outside runtime workspace roots"

    if validate_manifest:
        manifest_path_text = str(binding.get("manifest_path") or "").strip()
        if not manifest_path_text:
            return "missing sandbox manifest"
        manifest_path = Path(manifest_path_text).expanduser()
        if not manifest_path.is_absolute():
            return "sandbox manifest must be absolute"
        if not manifest_path.exists():
            return "sandbox manifest does not exist"
        if not manifest_path.is_file():
            return "sandbox manifest is not a file"
        if _path_has_symlink_component(manifest_path):
            return "sandbox manifest is symlinked"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "sandbox manifest is unreadable"
        if not isinstance(manifest, Mapping):
            return "sandbox manifest is invalid"

        manifest_sandbox_id = str(manifest.get("sandbox_id") or "").strip()
        if manifest_sandbox_id and manifest_sandbox_id != sandbox_id:
            return "binding sandbox_id does not match manifest"

        sandbox_root_text = str(manifest.get("root") or "").strip()
        if not sandbox_root_text:
            return "sandbox manifest missing root"
        sandbox_root = Path(sandbox_root_text).expanduser()
        if not sandbox_root.is_absolute():
            return "sandbox root must be absolute"
        if not sandbox_root.exists():
            return "sandbox root does not exist"
        if not sandbox_root.is_dir():
            return "sandbox root is not a directory"
        if _path_has_symlink_component(sandbox_root):
            return "sandbox root is symlinked"
        sandbox_root_resolved = sandbox_root.resolve()
        if not _path_is_under(manifest_path.resolve(), sandbox_root_resolved):
            return "manifest is outside sandbox root"
        if not _path_is_under(cwd_resolved, sandbox_root_resolved):
            return "cwd is outside sandbox root"

        manifest_repo_roots_value = manifest.get("repo_roots")
        manifest_repo_root_paths = (
            list(manifest_repo_roots_value.values())
            if isinstance(manifest_repo_roots_value, Mapping)
            else _as_string_list(manifest_repo_roots_value)
        )
        manifest_repo_roots = [
            Path(path).expanduser().resolve(strict=False)
            for path in manifest_repo_root_paths
            if str(path).strip()
        ]
        for root in manifest_repo_roots:
            if root.exists() and _path_has_symlink_component(root):
                return "repo root is symlinked"
            if not _path_is_under(root, sandbox_root_resolved):
                return "repo root is outside sandbox root"
        binding_repo_roots = [
            Path(path).expanduser().resolve(strict=False)
            for path in repo_root_paths
            if str(path).strip()
        ]
        for root in binding_repo_roots:
            if root.exists() and _path_has_symlink_component(root):
                return "binding repo root is symlinked"
            if not _path_is_under(root, sandbox_root_resolved):
                return "binding repo root is outside sandbox root"
        manifest_bound_repo_roots = manifest_repo_roots or binding_repo_roots
        if binding_repo_roots and manifest_repo_roots and set(binding_repo_roots) != set(manifest_repo_roots):
            return "binding repo roots do not match manifest"
        if manifest_bound_repo_roots and not any(
            _path_is_under(cwd_resolved, root) for root in manifest_bound_repo_roots
        ):
            return "cwd is outside bound repo roots"

        manifest_writable_roots = [
            Path(path).expanduser().resolve(strict=False)
            for path in _as_string_list(manifest.get("writable_roots"))
            if str(path).strip()
        ]
        for root in manifest_writable_roots:
            if root.exists() and _path_has_symlink_component(root):
                return "writable root is symlinked"
            if not _path_is_under(root, sandbox_root_resolved):
                return "writable root is outside sandbox root"
        binding_writable_roots = [
            Path(path).expanduser().resolve(strict=False)
            for path in _as_string_list(binding.get("writable_roots"))
            if str(path).strip()
        ]
        for root in binding_writable_roots:
            if root.exists() and _path_has_symlink_component(root):
                return "binding writable root is symlinked"
            if not _path_is_under(root, sandbox_root_resolved):
                return "binding writable root is outside sandbox root"
        manifest_allowed_roots = manifest_writable_roots or binding_writable_roots
        if not manifest_allowed_roots:
            return "missing writable roots"
        if binding_writable_roots and set(binding_writable_roots) != set(manifest_allowed_roots):
            return "binding writable roots do not match manifest"
        if manifest_bound_repo_roots and any(
            not any(_path_is_under(root, repo_root) for repo_root in manifest_bound_repo_roots)
            for root in manifest_allowed_roots
        ):
            return "writable root is outside bound repo roots"
        if not any(_paths_overlap(cwd_resolved, root) for root in manifest_allowed_roots):
            return "cwd is outside writable roots"

    expires_raw = binding.get("expires_at")
    if not expires_raw:
        return "missing expires_at"
    expires_at = _parse_binding_expires_at(expires_raw)
    if expires_at is None:
        return "expires_at is invalid"
    if expires_at <= datetime.now(timezone.utc):
        return "binding is expired"

    return None


def _role_is_write_producing(role: Any) -> bool:
    tools = {str(tool) for tool in (getattr(role, "tools", None) or [])}
    metadata = role.metadata or {}
    return bool(
        tools & _WRITE_PRODUCING_TOOLS
        or metadata.get("write_producing")
        or metadata.get("produces_writes")
    )


def _role_requires_runtime_workspace_binding(role: Any, *, actor_name: str) -> bool:
    metadata = role.metadata or {}

    explicit = any(
        bool(metadata.get(key))
        for key in (
            "sandbox_required",
            "requires_sandbox",
            "runtime_workspace_binding_required",
            "workspace_binding_required",
            "require_runtime_workspace_binding",
        )
    )
    for policy_key in ("execution_policy", "sandbox_policy", "runtime_workspace_binding_policy"):
        policy = metadata.get(policy_key)
        if isinstance(policy, Mapping) and any(
            bool(policy.get(key))
            for key in (
                "sandbox_required",
                "requires_sandbox",
                "runtime_workspace_binding_required",
                "workspace_binding_required",
                "required",
            )
        ):
            explicit = True

    if not explicit or not _role_is_write_producing(role):
        return False

    return True


@dataclass
class _InvocationState:
    runtime: AgentRuntime
    actor_name: str
    started_at: float
    last_activity: float
    timeout_seconds: int


class _LivenessTracker:
    """Wraps a runtime's on_message callback to track last-activity time."""

    def __init__(
        self,
        runtime: AgentRuntime | None,
        *,
        activity_callback: Any | None = None,
    ) -> None:
        self._runtime = runtime
        self._activity_callback = activity_callback
        self._original_callback = getattr(runtime, "on_message", None) if runtime is not None else None
        self.last_activity = time.monotonic()

    def record_activity(self) -> None:
        self.last_activity = time.monotonic()
        if self._activity_callback is not None:
            self._activity_callback()

    def install(self) -> None:
        """Replace the runtime's on_message with our tracking wrapper."""
        if self._runtime is None:
            return
        original = self._original_callback

        def _tracking_callback(msg: Any) -> None:
            self.record_activity()
            if original is not None:
                original(msg)

        self._runtime.on_message = _tracking_callback  # type: ignore[attr-defined]

    def restore(self) -> None:
        """Restore the runtime's original on_message callback."""
        if self._runtime is None:
            return
        self._runtime.on_message = self._original_callback  # type: ignore[attr-defined]

    def seconds_idle(self) -> float:
        return time.monotonic() - self.last_activity


class TrackedWorkflowRunner(DefaultWorkflowRunner):
    """Extends DefaultWorkflowRunner to log phase transitions to Postgres.

    Supports an optional **secondary_runtime** for adversarial multi-model
    execution.  Roles with ``metadata["runtime"] == "secondary"`` are routed
    to the secondary runtime; all others use the primary (default) runtime.
    """

    def __init__(
        self,
        *,
        feature_store: PostgresFeatureStore,
        secondary_runtime: AgentRuntime | None = None,
        budget: bool = False,
        agent_concurrency_limiter: AgentConcurrencyLimiter | None = None,
        services: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(services=services, **kwargs)  # type: ignore[arg-type]
        self.agent_runtime = kwargs.get("agent_runtime")
        self.interaction_runtimes = self._runtimes
        self.feature_store = feature_store
        self.secondary_runtime = secondary_runtime
        self.budget = budget
        self.agent_concurrency_limiter = agent_concurrency_limiter
        self._active_invocations: dict[str, _InvocationState] = {}

    @contextmanager
    def bind_invocation_observer(self, observer: Any):
        token = _invocation_observer_var.set(observer)
        try:
            yield observer
        finally:
            _invocation_observer_var.reset(token)

    def _notify_invocation_observer(self, event: str, invocation_id: str, **payload: Any) -> None:
        observer = _invocation_observer_var.get()
        if observer is None:
            return
        handler = getattr(observer, event, None)
        if callable(handler):
            handler(invocation_id, **payload)

    def _record_invocation_started(
        self,
        invocation_id: str,
        *,
        runtime: AgentRuntime,
        actor_name: str,
        timeout_seconds: int,
    ) -> None:
        now = time.monotonic()
        self._active_invocations[invocation_id] = _InvocationState(
            runtime=runtime,
            actor_name=actor_name,
            started_at=now,
            last_activity=now,
            timeout_seconds=timeout_seconds,
        )
        self._notify_invocation_observer(
            "on_invocation_start",
            invocation_id,
            actor_name=actor_name,
            runtime_name=getattr(runtime, "name", "unknown"),
            timeout_seconds=timeout_seconds,
        )

    def _record_invocation_activity(self, invocation_id: str) -> None:
        state = self._active_invocations.get(invocation_id)
        if state is None:
            return
        state.last_activity = time.monotonic()
        self._notify_invocation_observer("on_invocation_activity", invocation_id)

    def _record_invocation_finished(self, invocation_id: str) -> None:
        state = self._active_invocations.pop(invocation_id, None)
        self._notify_invocation_observer(
            "on_invocation_finish",
            invocation_id,
            actor_name=state.actor_name if state is not None else "",
        )

    def invocation_has_live_work(self, invocation_id: str) -> bool:
        state = self._active_invocations.get(invocation_id)
        if state is None:
            return False
        checker = getattr(state.runtime, "invocation_has_live_work", None)
        if callable(checker):
            try:
                return bool(checker(invocation_id))
            except Exception:
                logger.debug("Failed to inspect live work for %s", invocation_id, exc_info=True)
                return False
        return False

    # ── Multi-runtime routing ───────────────────────────────────────

    async def resolve(
        self,
        task_or_actor: Any,
        prompt_or_feature: Any = None,
        *,
        feature: Feature | None = None,
        context_keys: list[str] | None = None,
        output_type: type[BaseModel] | None = None,
        kind: Literal["approve", "choose", "respond"] | None = None,
        options: list[str] | None = None,
        continuation: bool = False,
        **runtime_kwargs: Any,
    ) -> Any:
        task, feature = self._coerce_resolve_call(
            task_or_actor,
            prompt_or_feature,
            feature=feature,
            context_keys=context_keys,
            output_type=output_type,
            kind=kind,
            options=options,
            continuation=continuation,
        )
        actor = task.actor
        context = await self._resolve_context(task, feature)

        if isinstance(actor, InteractionActor):
            runtime = self._resolve_runtime(actor.resolver)
            ask = getattr(runtime, "ask", None)
            if callable(ask):
                return await ask(
                    task,
                    **{
                        **runtime_kwargs,
                        "context": context,
                        "feature_id": feature.id,
                        "phase_name": _phase_name_var.get(),
                        "kind": kind,
                        "options": options,
                    },
                )
            pending = Pending(
                id=str(uuid4()),
                feature_id=feature.id,
                phase_name=_phase_name_var.get(),
                kind=kind or "respond",
                prompt=task.prompt,
                options=options,
                created_at=datetime.now(),
            )
            return await runtime.resolve(pending)

        # ── Workspace resolution ─────────────────────────────────────
        # Priority:
        # 1. Per-actor metadata "workspace_override" (most specific — e.g., repos/iriai-compose/)
        # 2. Phase-level "worktree_root" service (set once by implementation phase — repos/)
        # 3. Default runner workspace (main workspace — only for non-implementation phases)
        #
        # Uses a ContextVar so parallel coroutines each get their own
        # workspace without racing on the shared _workspaces dict.
        ws_path = None
        from iriai_compose import Workspace

        binding, binding_error = _coerce_runtime_workspace_binding(
            actor.role,
            actor_name=actor.name,
        )
        if binding_error:
            raise RuntimeError(
                f"Invalid runtime workspace binding for {actor.name}: {binding_error}"
            )
        if binding:
            metadata = dict(actor.role.metadata or {})
            metadata[RUNTIME_WORKSPACE_BINDING_KEY] = binding
            role = actor.role.model_copy(update={"metadata": metadata})
            actor = actor.model_copy(update={"role": role})
            task = task.model_copy(update={"actor": actor})
            ws_path = binding["cwd"]
        elif _role_requires_runtime_workspace_binding(actor.role, actor_name=actor.name):
            raise RuntimeError(
                f"Runtime workspace binding required for sandbox-required write role {actor.name}"
            )
        else:
            ws_path = actor.role.metadata.get("workspace_override")

        if not ws_path:
            worktree_root = self.services.get("worktree_root")
            if worktree_root:
                ws_path = str(worktree_root)

        if ws_path:
            _workspace_override_var.set(
                Workspace(id=feature.workspace_id, path=Path(ws_path))
            )

        # Inject workspace write boundary into the prompt so the agent
        # knows where to write even if Seatbelt sandbox isn't enforcing.
        prompt = task.prompt
        if ws_path:
            prompt = (
                f"{prompt}\n\n"
                f"## Workspace Write Boundary\n"
                f"Your working directory is `{ws_path}`. "
                f"You may read files anywhere for research, but all file writes "
                f"(Edit, Write, Bash) MUST target paths within this directory. "
                f"Do NOT write to other copies of the same repo."
            )

        # Auto-offload oversized prompts to a file so agents don't
        # exceed their context window.  Agents with Read tool can read
        # the file; agents without it get the prompt as-is (truncation
        # is preferable to a crash).
        from ._common._helpers import PROMPT_FILE_THRESHOLD, _offload_if_large

        offload_base = None if binding else ws_path
        if not offload_base:
            # Fallback for phases without worktree_root (e.g. planning):
            # use the artifact mirror's feature directory.
            mirror = self.services.get("artifact_mirror")
            if mirror and not binding:
                offload_base = str(mirror.feature_dir(feature.id))

        original_context_length = len(context)
        combined_prompt = _combined_ask_prompt(task, prompt=prompt, context=context)
        combined_prompt_length = len(combined_prompt)
        prompt_offloaded = False
        prompt_offload_path = ""
        if offload_base and combined_prompt_length > PROMPT_FILE_THRESHOLD:
            prompt = _offload_if_large(
                combined_prompt,
                Path(offload_base),
                f"prompt-{actor.name}",
            )
            prompt_offloaded = prompt != combined_prompt
            prompt_offload_path = _offloaded_prompt_path(prompt)
            context = ""
            task = task.model_copy(update={"prompt": prompt, "input": None})
        else:
            task = task.model_copy(update={"prompt": prompt})

        try:
            # ── Budget mode: downgrade implementers to Sonnet ──────
            if self.budget and "Edit" in actor.role.tools:
                from ..config import BUDGET_TIERS

                budget_role = actor.role.model_copy(
                    update={"model": BUDGET_TIERS["sonnet"]},
                )
                actor = actor.model_copy(update={"role": budget_role})
                task = task.model_copy(update={"actor": actor})
                logger.info("Budget mode: downgraded %s to sonnet", actor.name)

            await self.feature_store.log_event(
                feature.id,
                "agent_start",
                actor.name,
                metadata={
                    "phase_name": _phase_name_var.get(),
                    "role_name": actor.role.name,
                    "runtime_hint": actor.role.metadata.get("runtime"),
                    "runtime_instance": getattr(actor.role.metadata.get("runtime_instance"), "name", None),
                    "tools": list(actor.role.tools or []),
                    "output_type": getattr(getattr(task, "output_type", None), "__name__", str(getattr(task, "output_type", "") or "")),
                    **_prompt_observability(prompt),
                    "context_length": original_context_length,
                    "combined_prompt_length": combined_prompt_length,
                    "runtime_prompt_length": len(prompt),
                    "prompt_offloaded": prompt_offloaded,
                    "prompt_offload_path": prompt_offload_path,
                },
            )

            # ── Pick the runtime this invocation will use ──────────
            runtime_override = (
                actor.role.metadata.get("runtime_instance")
            )
            use_secondary = (
                runtime_override is None
                and self.secondary_runtime
                and actor.role.metadata.get("runtime") == "secondary"
            )
            target_runtime = (
                runtime_override
                if runtime_override is not None
                else (self.secondary_runtime if use_secondary else self.agent_runtime)
            )
            runtime_name = getattr(target_runtime, "name", "unknown")

            if use_secondary:
                logger.info(
                    "Routing %s to secondary runtime (%s)",
                    actor.name, runtime_name,
                )
            elif runtime_override is not None:
                logger.info(
                    "Routing %s to runtime override (%s)",
                    actor.name, runtime_name,
                )

            # ── Resolve with liveness watchdog + retry ─────────────
            runtime_call_kwargs = {
                **runtime_kwargs,
                "context": context,
                "workspace": self.get_workspace(feature.workspace_id),
                "session_key": f"{actor.name}:{feature.id}",
            }

            actor_name = actor.name

            # ── Per-role liveness timeout override ────────────────
            # Roles that run long external processes (e.g. Playwright
            # test suites) can set liveness_timeout=0 to disable the
            # watchdog entirely, or a custom value in seconds.
            role_timeout = actor.role.metadata.get("liveness_timeout")
            if "Bash" in actor.role.tools:
                if role_timeout == 0:
                    prompt += (
                        "\n\n## Inactivity Timeout\n"
                        "The liveness watchdog is disabled for this role. "
                        "Still break long workflows into clear steps and report "
                        "intermediate progress so recovery remains observable."
                    )
                else:
                    effective_timeout_preview = (
                        role_timeout if role_timeout is not None else LIVENESS_TIMEOUT
                    )
                    timeout_minutes = max(1, int(effective_timeout_preview // 60))
                    prompt += (
                        "\n\n## Inactivity Timeout\n"
                        f"You will be killed if you produce no output for about "
                        f"{timeout_minutes} minutes. For long-running commands "
                        "(test suites, builds, installs), run in small batches and "
                        "report intermediate results between each. NEVER run a "
                        "single command that blocks for most of that window."
                    )
                task = task.model_copy(update={"prompt": prompt})
            if role_timeout == 0:
                logger.info(
                    "Watchdog disabled for %s (liveness_timeout=0)",
                    actor_name,
                )
                async with self._agent_concurrency_slot(
                    actor_name=actor_name,
                    feature_id=feature.id,
                    phase_name=_phase_name_var.get(),
                ):
                    invocation_id = uuid4().hex
                    await self.feature_store.log_event(
                        feature.id,
                        "agent_invocation_start",
                        actor.name,
                        content=runtime_name,
                        metadata={
                            "phase_name": _phase_name_var.get(),
                            "invocation_id": invocation_id,
                            "runtime_name": runtime_name,
                            "attempt": 0,
                            "liveness_timeout_seconds": 0,
                        },
                    )
                    self._record_invocation_started(
                        invocation_id,
                        runtime=target_runtime,
                        actor_name=actor_name,
                        timeout_seconds=0,
                    )
                    tracker = _LivenessTracker(
                        None,
                        activity_callback=lambda: self._record_invocation_activity(invocation_id),
                    )
                    try:
                        result = await self._resolve_with_runtime(
                            target_runtime,
                            task,
                            runtime_kwargs=runtime_call_kwargs,
                            tracker=tracker,
                            invocation_id=invocation_id,
                        )
                        await self.feature_store.log_event(
                            feature.id,
                            "agent_done",
                            actor.name,
                            content=runtime_name,
                            metadata={
                                "phase_name": _phase_name_var.get(),
                                "invocation_id": invocation_id,
                                "runtime_name": runtime_name,
                            },
                        )
                        return result
                    finally:
                        self._record_invocation_finished(invocation_id)

            effective_timeout = (
                role_timeout if role_timeout is not None else LIVENESS_TIMEOUT
            )

            last_err: Exception | None = None

            for attempt in range(RESOLVE_MAX_RETRIES + 1):
                if attempt > 0:
                    await asyncio.sleep(RESOLVE_RETRY_BACKOFF * attempt)
                    logger.info(
                        "Retrying %s (attempt %d/%d) after stall",
                        actor_name, attempt + 1, RESOLVE_MAX_RETRIES + 1,
                    )
                invocation_id = uuid4().hex
                async with self._agent_concurrency_slot(
                    actor_name=actor_name,
                    feature_id=feature.id,
                    phase_name=_phase_name_var.get(),
                ):
                    await self.feature_store.log_event(
                        feature.id,
                        "agent_invocation_start",
                        actor.name,
                        content=runtime_name,
                        metadata={
                            "phase_name": _phase_name_var.get(),
                            "invocation_id": invocation_id,
                            "runtime_name": runtime_name,
                            "attempt": attempt,
                            "liveness_timeout_seconds": effective_timeout,
                        },
                    )
                    self._record_invocation_started(
                        invocation_id,
                        runtime=target_runtime,
                        actor_name=actor_name,
                        timeout_seconds=effective_timeout,
                    )
                    if attempt > 0:
                        await self.feature_store.log_event(
                            feature.id, "agent_start", actor.name,
                            content=f"retry {attempt} after stall",
                            metadata={
                                "phase_name": _phase_name_var.get(),
                                "invocation_id": invocation_id,
                                "runtime_name": runtime_name,
                                "attempt": attempt,
                                "retry_reason": "stall",
                            },
                        )

                    tracker = _LivenessTracker(
                        None,
                        activity_callback=lambda: self._record_invocation_activity(invocation_id),
                    )
                    try:
                        result = await self._resolve_with_watchdog(
                            task,
                            tracker,
                            target_runtime,
                            invocation_id=invocation_id,
                            runtime_kwargs=runtime_call_kwargs,
                            actor_name=actor_name,
                            liveness_timeout=effective_timeout,
                        )
                        await self.feature_store.log_event(
                            feature.id,
                            "agent_done",
                            actor.name,
                            content=runtime_name,
                            metadata={
                                "phase_name": _phase_name_var.get(),
                                "invocation_id": invocation_id,
                                "runtime_name": runtime_name,
                                "attempt": attempt,
                            },
                        )
                        return result
                    except AgentStalled as exc:
                        last_err = exc
                        logger.warning(
                            "%s stalled after %ds of inactivity (attempt %d/%d)",
                            actor_name, effective_timeout,
                            attempt + 1, RESOLVE_MAX_RETRIES + 1,
                        )
                        await self.feature_store.log_event(
                            feature.id, "agent_stalled", actor.name,
                            content=f"no output for {effective_timeout}s",
                        )
                    finally:
                        self._record_invocation_finished(invocation_id)

            # All retries exhausted
            raise RuntimeError(
                f"{actor_name} stalled {RESOLVE_MAX_RETRIES + 1} times "
                f"({effective_timeout}s inactivity each) — giving up"
            ) from last_err
        except Exception as exc:
            await self.feature_store.log_event(
                feature.id,
                "agent_error",
                actor.name,
                content=str(exc)[:1000],
                metadata={
                    "phase_name": _phase_name_var.get(),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            # Clear the per-coroutine workspace override
            _workspace_override_var.set(None)

    @contextlib.asynccontextmanager
    async def _agent_concurrency_slot(
        self,
        *,
        actor_name: str,
        feature_id: str,
        phase_name: str,
    ):
        if self.agent_concurrency_limiter is None:
            yield
            return
        async with self.agent_concurrency_limiter.acquire(
            actor_name=actor_name,
            feature_id=feature_id,
            phase_name=phase_name,
        ):
            yield

    def _coerce_resolve_call(
        self,
        task_or_actor: Any,
        prompt_or_feature: Any,
        *,
        feature: Feature | None,
        context_keys: list[str] | None,
        output_type: type[BaseModel] | None,
        kind: Literal["approve", "choose", "respond"] | None,
        options: list[str] | None,
        continuation: bool,
    ) -> tuple[Ask, Feature]:
        if isinstance(task_or_actor, Ask):
            if feature is not None:
                return task_or_actor, feature
            if prompt_or_feature is None:
                raise TypeError("feature is required when resolving an Ask task")
            return task_or_actor, prompt_or_feature

        if feature is None:
            raise TypeError("feature is required when resolving a legacy actor/prompt call")

        task_kwargs: dict[str, Any] = {
            "actor": task_or_actor,
            "prompt": prompt_or_feature,
            "context_keys": context_keys or [],
            "output_type": output_type,
            "continuation": continuation,
        }
        if isinstance(task_or_actor, InteractionActor):
            if kind == "choose":
                task_kwargs["input"] = Select(options=options or [])
                task_kwargs["input_type"] = Select
            elif kind == "approve":
                task_kwargs["input"] = Select(
                    options=["Approve", "Reject", "Give feedback"]
                )
                task_kwargs["input_type"] = Select
        return Ask(**task_kwargs), feature

    async def _resolve_context(self, task: Ask, feature: Feature) -> str:
        context = ""
        if task.continuation:
            return context

        all_keys: list[str] = []
        if isinstance(task.actor, AgentActor):
            all_keys = list(
                dict.fromkeys(task.actor.context_keys + (task.context_keys or []))
            )
        elif task.context_keys:
            all_keys = list(task.context_keys)

        resolver = getattr(self.context_provider, "resolve", None)
        if all_keys and resolver is not None:
            context = await resolver(all_keys, feature=feature)
        return context

    async def _resolve_with_watchdog(
        self,
        task: Ask,
        tracker: _LivenessTracker,
        target_runtime: AgentRuntime,
        *,
        invocation_id: str,
        runtime_kwargs: dict[str, Any],
        actor_name: str,
        liveness_timeout: int = LIVENESS_TIMEOUT,
    ) -> Any:
        """Run a runtime ask() call with a liveness watchdog."""
        async def _do_resolve() -> Any:
            return await self._resolve_with_runtime(
                target_runtime,
                task,
                runtime_kwargs=runtime_kwargs,
                tracker=tracker,
                invocation_id=invocation_id,
            )

        resolve_task = asyncio.create_task(_do_resolve())

        try:
            while not resolve_task.done():
                await asyncio.sleep(LIVENESS_POLL_INTERVAL)
                if resolve_task.done():
                    break
                idle = tracker.seconds_idle()
                if idle >= liveness_timeout:
                    # Before killing, check if the agent is legitimately
                    # executing a long-running tool (e.g. Playwright tests).
                    if self.invocation_has_live_work(invocation_id):
                        logger.info(
                            "Watchdog: %s idle for %.0fs but has active "
                            "invocation-local live work — extending grace period",
                            actor_name, idle,
                        )
                        tracker.record_activity()
                        continue

                    logger.error(
                        "Watchdog: %s has been idle for %.0fs — cancelling",
                        actor_name, idle,
                    )
                    resolve_task.cancel()
                    # Bounded join, not an unconditional await: if the cancelled
                    # task is wedged in an uncancellable await it would re-hang
                    # this watchdog forever (the 8ac124d6 line-728 freeze).
                    # asyncio.wait returns on schedule; the orphan is abandoned.
                    await asyncio.wait(
                        {resolve_task}, timeout=_WATCHDOG_ABANDON_JOIN_SECONDS
                    )
                    raise AgentStalled(
                        f"{actor_name} produced no output for {idle:.0f}s"
                    )

            return resolve_task.result()
        except asyncio.CancelledError:
            resolve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait(
                    {resolve_task}, timeout=_WATCHDOG_ABANDON_JOIN_SECONDS
                )
            raise

    async def _resolve_with_runtime(
        self,
        target_runtime: AgentRuntime,
        task: Ask,
        *,
        runtime_kwargs: dict[str, Any],
        tracker: _LivenessTracker,
        invocation_id: str,
    ) -> Any:
        binder = getattr(target_runtime, "bind_invocation", None)
        if callable(binder):
            async with binder(invocation_id, tracker.record_activity):
                tracker.record_activity()
                return await target_runtime.ask(task, **runtime_kwargs)
        tracker._runtime = target_runtime
        tracker._original_callback = getattr(target_runtime, "on_message", None)
        tracker.install()
        try:
            tracker.record_activity()
            return await target_runtime.ask(task, **runtime_kwargs)
        finally:
            tracker.restore()

    def get_workspace(self, workspace_id: str | None) -> Any:
        """Return the per-coroutine workspace override if set, else default."""
        override = _workspace_override_var.get(None)
        if override is not None:
            return override
        return super().get_workspace(workspace_id)

    # ── Workflow execution with phase tracking ──────────────────────

    async def execute_workflow(
        self,
        workflow: Workflow,
        feature: Feature,
        state: BaseModel,
    ) -> BaseModel:
        self.last_workflow_quiesce = None
        await workflow.on_start(self, feature, state)
        try:
            from .public_exhibit import (
                enqueue_public_exhibit_refresh,
                ensure_public_summary_fallback,
            )

            await ensure_public_summary_fallback(
                self,
                feature,
                reason="workflow-start-missing-public-summary",
            )
            await enqueue_public_exhibit_refresh(
                self,
                feature,
                reason="workflow-start",
                priority=10,
            )
        except Exception:
            logger.warning("Failed to ensure public summary for %s", feature.id, exc_info=True)
        try:
            for phase_cls in workflow.build_phases():
                phase = phase_cls()
                _phase_name_var.set(phase.name)
                await self.feature_store.transition_phase(feature.id, phase.name)
                await phase.on_start(self, feature, state)
                await self.feature_store.log_event(
                    feature.id,
                    "phase_execute_start",
                    "workflow",
                    phase.name,
                    metadata={"workflow": workflow.__class__.__name__},
                )
                try:
                    state = await phase.execute(self, feature, state)
                except WorkflowQuiesced as exc:
                    self.last_workflow_quiesce = WorkflowQuiesceResult(
                        phase_name=exc.phase_name,
                        reason=exc.reason,
                        metadata=dict(exc.metadata),
                    )
                    await self.feature_store.log_event(
                        feature.id,
                        "phase_execute_quiesced",
                        "workflow",
                        phase.name,
                        metadata={
                            "workflow": workflow.__class__.__name__,
                            "phase_name": exc.phase_name,
                            "reason": exc.reason[:1000],
                            **exc.metadata,
                        },
                    )
                    await phase.on_done(self, feature, state)
                    await workflow.on_done(self, feature, state)
                    return state
                except Exception as exc:
                    await self.feature_store.log_event(
                        feature.id,
                        "phase_execute_error",
                        "workflow",
                        phase.name,
                        metadata={
                            "workflow": workflow.__class__.__name__,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1000],
                        },
                    )
                    await phase.on_done(self, feature, state)
                    raise
                await phase.on_done(self, feature, state)
                await self.feature_store.log_event(
                    feature.id,
                    "phase_execute_done",
                    "workflow",
                    phase.name,
                    metadata={"workflow": workflow.__class__.__name__},
                )
            await self.feature_store.transition_phase(feature.id, "complete")
        except Exception:
            await workflow.on_done(self, feature, state)
            raise
        await workflow.on_done(self, feature, state)
        return state

    async def resume_workflow(
        self,
        workflow: Workflow,
        feature: Feature,
        state: BaseModel,
        *,
        resume_from_phase: str,
    ) -> BaseModel:
        """Resume a workflow, skipping phases before *resume_from_phase*."""
        self.last_workflow_quiesce = None
        phases = workflow.build_phases()
        phase_names = [cls().name for cls in phases]

        if resume_from_phase not in phase_names:
            raise RuntimeError(
                f"Cannot resume: phase '{resume_from_phase}' not found. "
                f"Valid phases: {phase_names}"
            )

        resume_idx = phase_names.index(resume_from_phase)

        await workflow.on_start(self, feature, state)
        try:
            from .public_exhibit import (
                enqueue_public_exhibit_refresh,
                ensure_public_summary_fallback,
            )

            await ensure_public_summary_fallback(
                self,
                feature,
                reason="workflow-resume-missing-public-summary",
            )
            await enqueue_public_exhibit_refresh(
                self,
                feature,
                reason="workflow-resume",
                priority=10,
            )
        except Exception:
            logger.warning("Failed to ensure public summary for %s", feature.id, exc_info=True)

        # Re-host artifacts from prior phases so browser review URLs work
        hosting = self.services.get("hosting")
        if hosting and hasattr(hosting, "rehost_existing"):
            try:
                count = await hosting.rehost_existing(
                    feature.id, label_prefix=f"{feature.name} — ",
                )
                if count:
                    await self.feature_store.log_event(
                        feature.id, "artifacts_rehosted", "resume", str(count),
                    )
            except Exception:
                logger.warning(
                    "Failed to re-host artifacts for %s", feature.id, exc_info=True,
                )

        try:
            for i, phase_cls in enumerate(phases):
                phase = phase_cls()
                if i < resume_idx:
                    await self.feature_store.log_event(
                        feature.id, "phase_skipped", "resume", phase.name
                    )
                    continue

                _phase_name_var.set(phase.name)
                await self.feature_store.transition_phase(feature.id, phase.name)
                await phase.on_start(self, feature, state)
                await self.feature_store.log_event(
                    feature.id,
                    "phase_execute_start",
                    "resume",
                    phase.name,
                    metadata={"workflow": workflow.__class__.__name__},
                )
                try:
                    state = await phase.execute(self, feature, state)
                except WorkflowQuiesced as exc:
                    self.last_workflow_quiesce = WorkflowQuiesceResult(
                        phase_name=exc.phase_name,
                        reason=exc.reason,
                        metadata=dict(exc.metadata),
                    )
                    await self.feature_store.log_event(
                        feature.id,
                        "phase_execute_quiesced",
                        "resume",
                        phase.name,
                        metadata={
                            "workflow": workflow.__class__.__name__,
                            "phase_name": exc.phase_name,
                            "reason": exc.reason[:1000],
                            **exc.metadata,
                        },
                    )
                    await phase.on_done(self, feature, state)
                    await workflow.on_done(self, feature, state)
                    return state
                except Exception as exc:
                    await self.feature_store.log_event(
                        feature.id,
                        "phase_execute_error",
                        "resume",
                        phase.name,
                        metadata={
                            "workflow": workflow.__class__.__name__,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1000],
                        },
                    )
                    await phase.on_done(self, feature, state)
                    raise
                await phase.on_done(self, feature, state)
                await self.feature_store.log_event(
                    feature.id,
                    "phase_execute_done",
                    "resume",
                    phase.name,
                    metadata={"workflow": workflow.__class__.__name__},
                )
            await self.feature_store.transition_phase(feature.id, "complete")
        except Exception:
            await workflow.on_done(self, feature, state)
            raise
        await workflow.on_done(self, feature, state)
        return state
