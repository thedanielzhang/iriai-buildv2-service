from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
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


class AgentStalled(RuntimeError):
    """Raised when an agent invocation produces no output for too long."""


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
        services: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(services=services, **kwargs)  # type: ignore[arg-type]
        self.agent_runtime = kwargs.get("agent_runtime")
        self.interaction_runtimes = self._runtimes
        self.feature_store = feature_store
        self.secondary_runtime = secondary_runtime
        self.budget = budget
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

        offload_base = ws_path
        if not offload_base:
            # Fallback for phases without worktree_root (e.g. planning):
            # use the artifact mirror's feature directory.
            mirror = self.services.get("artifact_mirror")
            if mirror:
                offload_base = str(mirror.feature_dir(feature.id))

        if offload_base and len(prompt) > PROMPT_FILE_THRESHOLD:
            prompt = _offload_if_large(prompt, Path(offload_base), f"prompt-{actor.name}")

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
                feature.id, "agent_start", actor.name,
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
                invocation_id = uuid4().hex
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
                        metadata={"phase_name": _phase_name_var.get()},
                    )
                    return result
                finally:
                    self._record_invocation_finished(invocation_id)

            effective_timeout = (
                role_timeout if role_timeout is not None else LIVENESS_TIMEOUT
            )

            last_err: Exception | None = None

            for attempt in range(RESOLVE_MAX_RETRIES + 1):
                invocation_id = uuid4().hex
                self._record_invocation_started(
                    invocation_id,
                    runtime=target_runtime,
                    actor_name=actor_name,
                    timeout_seconds=effective_timeout,
                )
                if attempt > 0:
                    await asyncio.sleep(RESOLVE_RETRY_BACKOFF * attempt)
                    logger.info(
                        "Retrying %s (attempt %d/%d) after stall",
                        actor_name, attempt + 1, RESOLVE_MAX_RETRIES + 1,
                    )
                    await self.feature_store.log_event(
                        feature.id, "agent_start", actor.name,
                        content=f"retry {attempt} after stall",
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
                        metadata={"phase_name": _phase_name_var.get()},
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
                    try:
                        await resolve_task
                    except asyncio.CancelledError:
                        pass
                    raise AgentStalled(
                        f"{actor_name} produced no output for {idle:.0f}s"
                    )

            return resolve_task.result()
        except asyncio.CancelledError:
            resolve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await resolve_task
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
        await workflow.on_start(self, feature, state)
        try:
            for phase_cls in workflow.build_phases():
                phase = phase_cls()
                _phase_name_var.set(phase.name)
                await self.feature_store.transition_phase(feature.id, phase.name)
                await phase.on_start(self, feature, state)
                try:
                    state = await phase.execute(self, feature, state)
                except Exception:
                    await phase.on_done(self, feature, state)
                    raise
                await phase.on_done(self, feature, state)
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
        phases = workflow.build_phases()
        phase_names = [cls().name for cls in phases]

        if resume_from_phase not in phase_names:
            raise RuntimeError(
                f"Cannot resume: phase '{resume_from_phase}' not found. "
                f"Valid phases: {phase_names}"
            )

        resume_idx = phase_names.index(resume_from_phase)

        await workflow.on_start(self, feature, state)

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
                try:
                    state = await phase.execute(self, feature, state)
                except Exception:
                    await phase.on_done(self, feature, state)
                    raise
                await phase.on_done(self, feature, state)
            await self.feature_store.transition_phase(feature.id, "complete")
        except Exception:
            await workflow.on_done(self, feature, state)
            raise
        await workflow.on_done(self, feature, state)
        return state
