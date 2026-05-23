from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


RuntimeTerminalReason = Literal[
    "completed",
    "cancelled",
    "provider_error",
    "timeout",
    "watchdog_stall",
    "process_failed",
    "prompt_too_large",
    "context_materialization_failed",
    "structured_output_invalid",
    "sandbox_binding_failed",
    "patch_capture_failed",
]


class RuntimeInvocationResponse(BaseModel):
    invocation_id: str
    status: Literal["completed", "failed", "cancelled"]
    terminal_reason: RuntimeTerminalReason
    process_started: bool = False
    structured_output: dict[str, Any] | None = None
    raw_text: str | None = None
    raw_text_ref: int | None = None
    raw_artifact_id: int | None = None
    provider_request_id: str | None = None
    provider_error_code: str | None = None
    stdout_artifact_id: int | None = None
    stderr_artifact_id: int | None = None
    adapter_retry_ids: list[str] = Field(default_factory=list)
    adapter_retry_count: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int = 0


RunnerFactory = Callable[[Any], Any]
ActorFactory = Callable[[Any], Any]
AskFactory = Callable[[Any, Any], Any]

_MISSING = object()


class RuntimeClient:
    """Convert runner.run(Ask(...)) into an exception-free response contract."""

    def __init__(
        self,
        *,
        runner: Any | None = None,
        runner_factory: RunnerFactory | None = None,
        actor_factory: ActorFactory | None = None,
        ask_factory: AskFactory | None = None,
        cancellation_registry: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._runner_factory = runner_factory
        self._actor_factory = actor_factory
        self._ask_factory = ask_factory
        self._cancellation_registry = cancellation_registry
        self._clock = clock or time.monotonic

    async def invoke(self, request: Any) -> RuntimeInvocationResponse:
        started_at = self._clock()

        if await self._is_cancelled(request):
            return self._failure_response(
                request,
                started_at,
                "cancelled",
                status="cancelled",
                process_started=False,
            )

        observer = _InvocationObserver()
        try:
            runner = await self._resolve_runner(request)
            actor = await self._resolve_actor(request)
            ask = await self._resolve_ask(request, actor)
            result = await self._run_with_observer(runner, ask, request, observer)
            return self._completed_response(
                request,
                result,
                started_at,
                process_started=observer.process_started or True,
            )
        except asyncio.CancelledError as exc:
            return self._failure_response(
                request,
                started_at,
                "cancelled",
                status="cancelled",
                exc=exc,
                process_started=_process_started_from(exc, observer.process_started),
            )
        except TimeoutError as exc:
            reason: RuntimeTerminalReason = (
                "watchdog_stall" if _looks_like_watchdog_stall(exc) else "timeout"
            )
            return self._failure_response(
                request,
                started_at,
                reason,
                exc=exc,
                process_started=_process_started_from(exc, observer.process_started),
            )
        except Exception as exc:
            return self._failure_response(
                request,
                started_at,
                _classify_exception(exc),
                exc=exc,
                process_started=_process_started_from(exc, observer.process_started),
            )

    async def _resolve_runner(self, request: Any) -> Any:
        if self._runner_factory is not None:
            return await _maybe_await(self._runner_factory(request))
        metadata = _metadata(request)
        runner = _field(request, "runner", metadata.get("runner", _MISSING))
        if runner is not _MISSING:
            return runner
        if self._runner is not None:
            return self._runner
        raise RuntimeError("RuntimeClient requires a runner or runner_factory")

    async def _resolve_actor(self, request: Any) -> Any:
        if self._actor_factory is not None:
            return await _maybe_await(self._actor_factory(request))
        metadata = _metadata(request)
        actor = _field(request, "actor", metadata.get("actor", _MISSING))
        if actor is not _MISSING:
            return actor
        return _default_actor_from_request(request)

    async def _resolve_ask(self, request: Any, actor: Any) -> Any:
        if self._ask_factory is not None:
            return await _maybe_await(self._ask_factory(request, actor))
        return _default_ask_from_request(request, actor)

    async def _run_with_observer(
        self,
        runner: Any,
        ask: Any,
        request: Any,
        observer: "_InvocationObserver",
    ) -> Any:
        binder = getattr(runner, "bind_invocation_observer", None)
        if callable(binder):
            bound = binder(observer)
            if hasattr(bound, "__aenter__"):
                async with bound:
                    return await self._run_with_timeout(runner, ask, request)
            if hasattr(bound, "__enter__"):
                with bound:
                    return await self._run_with_timeout(runner, ask, request)
        return await self._run_with_timeout(runner, ask, request)

    async def _run_with_timeout(self, runner: Any, ask: Any, request: Any) -> Any:
        timeout_seconds = _positive_float(_field(request, "timeout_seconds", None))
        call = self._call_runner(runner, ask, request)
        if timeout_seconds is None:
            return await call
        return await asyncio.wait_for(call, timeout=timeout_seconds)

    async def _call_runner(self, runner: Any, ask: Any, request: Any) -> Any:
        run = getattr(runner, "run", None)
        if not callable(run):
            raise RuntimeError("RuntimeClient runner must expose run(...)")

        metadata = _metadata(request)
        kwargs = _plain_dict(metadata.get("runner_kwargs"))
        phase_name = metadata.get("phase_name")
        if phase_name and "phase_name" not in kwargs:
            kwargs["phase_name"] = phase_name

        feature = _field(request, "feature", metadata.get("feature", _MISSING))
        if feature is _MISSING:
            return await _maybe_await(run(ask, **kwargs))
        return await _maybe_await(run(ask, feature, **kwargs))

    async def _is_cancelled(self, request: Any) -> bool:
        metadata = _metadata(request)
        if bool(metadata.get("cancelled") or metadata.get("cancellation_requested")):
            return True
        if bool(_field(request, "cancelled", False) or _field(request, "cancellation_requested", False)):
            return True

        token = _field(request, "cancellation_token", None)
        if not token or self._cancellation_registry is None:
            return False
        registry = self._cancellation_registry
        for name in ("is_cancelled", "cancelled", "is_cancellation_requested"):
            checker = getattr(registry, name, None)
            if callable(checker):
                return bool(await _maybe_await(checker(token)))
        if callable(registry):
            return bool(await _maybe_await(registry(token)))
        if isinstance(registry, Mapping):
            return bool(registry.get(token))
        with contextlib.suppress(TypeError):
            return token in registry
        return False

    def _completed_response(
        self,
        request: Any,
        result: Any,
        started_at: float,
        *,
        process_started: bool,
    ) -> RuntimeInvocationResponse:
        extracted = _extract_result_payload(result)
        return RuntimeInvocationResponse(
            invocation_id=_text(_field(request, "invocation_id", "")),
            status="completed",
            terminal_reason="completed",
            process_started=process_started,
            structured_output=extracted["structured_output"],
            raw_text=extracted["raw_text"],
            raw_artifact_id=extracted["raw_artifact_id"],
            provider_request_id=extracted["provider_request_id"],
            provider_error_code=None,
            stdout_artifact_id=extracted["stdout_artifact_id"],
            stderr_artifact_id=extracted["stderr_artifact_id"],
            adapter_retry_ids=extracted["adapter_retry_ids"],
            adapter_retry_count=extracted["adapter_retry_count"],
            usage=extracted["usage"],
            elapsed_ms=self._elapsed_ms(started_at),
        )

    def _failure_response(
        self,
        request: Any,
        started_at: float,
        terminal_reason: RuntimeTerminalReason,
        *,
        status: Literal["failed", "cancelled"] = "failed",
        exc: BaseException | None = None,
        process_started: bool = False,
    ) -> RuntimeInvocationResponse:
        extracted = _extract_result_payload(exc)
        return RuntimeInvocationResponse(
            invocation_id=_text(_field(request, "invocation_id", "")),
            status=status,
            terminal_reason=terminal_reason,
            process_started=process_started,
            structured_output=None,
            raw_text=extracted["raw_text"] or (_exception_text(exc) if exc else None),
            raw_artifact_id=extracted["raw_artifact_id"],
            provider_request_id=extracted["provider_request_id"],
            provider_error_code=extracted["provider_error_code"],
            stdout_artifact_id=extracted["stdout_artifact_id"],
            stderr_artifact_id=extracted["stderr_artifact_id"],
            adapter_retry_ids=extracted["adapter_retry_ids"],
            adapter_retry_count=extracted["adapter_retry_count"],
            usage=extracted["usage"],
            elapsed_ms=self._elapsed_ms(started_at),
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, int(round((self._clock() - started_at) * 1000)))


class RunnerRuntimeClient(RuntimeClient):
    """RuntimeClient entrypoint for dispatcher integration with an existing runner."""

    async def invoke(
        self,
        request: Any,
        *,
        runner: Any | None = None,
        feature: Any | None = None,
        phase_name: str | None = None,
        runner_kwargs: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocationResponse:
        if runner is None and feature is None and phase_name is None and runner_kwargs is None:
            return await super().invoke(request)
        return await super().invoke(
            _with_runner_context(
                request,
                runner=runner,
                feature=feature,
                phase_name=phase_name,
                runner_kwargs=runner_kwargs,
            )
        )


class _InvocationObserver:
    def __init__(self) -> None:
        self.process_started = False

    def on_invocation_start(self, _invocation_id: str, **_payload: Any) -> None:
        self.process_started = True


def _default_actor_from_request(request: Any) -> Any:
    from iriai_compose.actors import AgentActor, Role

    actor_metadata = _plain_dict(_field(request, "actor_metadata", None))
    binding = _plain_dict(_field(request, "workspace_binding", None))
    metadata = _plain_dict(actor_metadata.get("role_metadata"))
    metadata.update(_plain_dict(binding.get("role_metadata")))
    metadata.update(_plain_dict(_metadata(request).get("role_metadata")))
    metadata.setdefault("runtime", _text(_field(request, "runtime", actor_metadata.get("runtime", ""))))
    if binding:
        metadata.setdefault("runtime_workspace_binding", binding)

    role = Role(
        name=_text(_field(request, "actor_role", actor_metadata.get("actor_role", "runtime"))),
        prompt=_text(actor_metadata.get("system_prompt") or metadata.get("system_prompt") or ""),
        tools=list(metadata.get("tools") or actor_metadata.get("tools") or []),
        model=actor_metadata.get("model"),
        metadata=metadata,
    )
    return AgentActor(
        name=_text(_field(request, "actor_name", actor_metadata.get("actor_name", "runtime"))),
        role=role,
    )


def _default_ask_from_request(request: Any, actor: Any) -> Any:
    from iriai_compose.tasks import Ask

    metadata = _metadata(request)
    output_type = _field(request, "output_type", metadata.get("output_type", None))
    return Ask(
        actor=actor,
        prompt=_text(_field(request, "prompt", "")),
        output_type=output_type,
    )


def _extract_result_payload(value: Any) -> dict[str, Any]:
    provider_metadata = _plain_dict(_first(value, "provider_metadata", "metadata"))
    retry_ids = _string_list(
        _first(value, "adapter_retry_ids", "adapter_retries", default=provider_metadata.get("adapter_retry_ids"))
    )
    retry_count = _int_or_zero(
        _first(value, "adapter_retry_count", default=provider_metadata.get("adapter_retry_count"))
    )
    if retry_count == 0 and retry_ids:
        retry_count = len(retry_ids)

    return {
        "structured_output": _extract_structured_output(value),
        "raw_text": _raw_text(value),
        "raw_artifact_id": _optional_int(_first(value, "raw_artifact_id", default=provider_metadata.get("raw_artifact_id"))),
        "provider_request_id": _optional_text(
            _first(
                value,
                "provider_request_id",
                "request_id",
                default=provider_metadata.get("provider_request_id") or provider_metadata.get("request_id"),
            )
        ),
        "provider_error_code": _optional_text(
            _first(
                value,
                "provider_error_code",
                "error_code",
                "code",
                "status_code",
                "return_code",
                "returncode",
                default=provider_metadata.get("provider_error_code") or provider_metadata.get("error_code"),
            )
        ),
        "stdout_artifact_id": _optional_int(
            _first(value, "stdout_artifact_id", default=provider_metadata.get("stdout_artifact_id"))
        ),
        "stderr_artifact_id": _optional_int(
            _first(value, "stderr_artifact_id", default=provider_metadata.get("stderr_artifact_id"))
        ),
        "adapter_retry_ids": retry_ids,
        "adapter_retry_count": retry_count,
        "usage": _plain_dict(_first(value, "usage", default=provider_metadata.get("usage"))),
    }


def _extract_structured_output(value: Any) -> dict[str, Any] | None:
    explicit = _field(value, "structured_output", _MISSING)
    if explicit is not _MISSING:
        return _structured_dict(explicit)
    if isinstance(value, Mapping) and "json" in value:
        return _structured_dict(value.get("json"))
    if isinstance(value, Mapping):
        wrapper_keys = {
            "raw_text",
            "raw_artifact_id",
            "provider_metadata",
            "provider_request_id",
            "provider_error_code",
            "stdout_artifact_id",
            "stderr_artifact_id",
            "adapter_retry_ids",
            "adapter_retry_count",
            "usage",
        }
        if wrapper_keys & set(value):
            return None
        return dict(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else None
    return None


def _structured_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else None
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return None


def _raw_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    candidate = _first(value, "raw_text", "result_text", "text", "content", "result", default=_MISSING)
    if candidate is _MISSING:
        return None
    return _optional_text(candidate)


def _classify_exception(exc: BaseException) -> RuntimeTerminalReason:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if _looks_like_watchdog_stall(exc):
        return "watchdog_stall"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "structured_output_invalid"

    text = _exception_text(exc).lower()
    class_name = type(exc).__name__.lower()
    if "cancel" in text or "cancel" in class_name:
        return "cancelled"
    if any(token in text for token in ("prompt too large", "context window", "maximum context", "input is too long")):
        return "prompt_too_large"
    if any(token in text for token in ("context materialization", "failed to resolve context", "context bundle")):
        return "context_materialization_failed"
    if any(token in text for token in ("structured_output", "structured output", "valid json", "schema", "model_validate")):
        return "structured_output_invalid"
    if any(
        token in text
        for token in (
            "sandbox binding",
            "workspace binding",
            "binding cwd",
            "runtime workspace",
            "blocked binding root",
            "outside bound repo roots",
            "runtime artifact root is symlinked",
            "outside the bound sandbox/artifact roots",
            "outside sandbox/artifact roots",
            "outside sandbox root",
            "outside writable roots",
        )
    ):
        return "sandbox_binding_failed"
    if any(token in text for token in ("patch capture", "capture patch", "captured patch")):
        return "patch_capture_failed"
    if _looks_like_process_failure(exc):
        return "process_failed"
    return "provider_error"


def _looks_like_watchdog_stall(exc: BaseException) -> bool:
    text = _exception_text(exc).lower()
    class_name = type(exc).__name__.lower()
    return any(
        token in text or token in class_name
        for token in ("watchdog", "stalled", "produced no output", "pre-work stall")
    )


def _looks_like_process_failure(exc: BaseException) -> bool:
    if _first(exc, "return_code", "returncode", "pid", default=_MISSING) is not _MISSING:
        return True
    text = _exception_text(exc).lower()
    return any(
        token in text
        for token in (
            "process.",
            "process ",
            "exit code",
            "return code",
            "cli failed",
            "command not found",
            "could not start",
            "subprocess",
        )
    )


def _process_started_from(exc: BaseException | None, observer_started: bool) -> bool:
    if observer_started:
        return True
    if exc is None:
        return False
    explicit = _first(exc, "process_started", "started", default=_MISSING)
    if explicit is not _MISSING:
        return bool(explicit)
    if _first(exc, "pid", default=_MISSING) is not _MISSING:
        return True
    return False


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _first(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        candidate = _field(value, name, _MISSING)
        if candidate is not _MISSING:
            return candidate
    return default


def _metadata(request: Any) -> dict[str, Any]:
    return _plain_dict(_field(request, "metadata", None))


def _plain_dict(value: Any) -> dict[str, Any]:
    if value is None or value is _MISSING:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {}


def _text(value: Any) -> str:
    if value is None or value is _MISSING:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _optional_text(value: Any) -> str | None:
    text = _text(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value is _MISSING:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


def _positive_float(value: Any) -> float | None:
    if value is None or value is _MISSING:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _string_list(value: Any) -> list[str]:
    if value is None or value is _MISSING:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _exception_text(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    message = str(exc).strip()
    return message or type(exc).__name__


def _with_runner_context(
    request: Any,
    *,
    runner: Any | None,
    feature: Any | None,
    phase_name: str | None,
    runner_kwargs: Mapping[str, Any] | None,
) -> Any:
    metadata = _metadata(request)
    if runner is not None:
        metadata["runner"] = runner
    if feature is not None:
        metadata["feature"] = feature
    if phase_name is not None:
        metadata["phase_name"] = phase_name
    if runner_kwargs is not None:
        merged_kwargs = _plain_dict(metadata.get("runner_kwargs"))
        merged_kwargs.update(dict(runner_kwargs))
        metadata["runner_kwargs"] = merged_kwargs

    if isinstance(request, Mapping):
        data = dict(request)
        data["metadata"] = metadata
        return data

    model_copy = getattr(request, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"metadata": metadata})

    namespace = _plain_dict(request)
    if namespace:
        namespace["metadata"] = metadata
        return type("RuntimeInvocationRequestContext", (), namespace)()

    return _RequestContextProxy(request, metadata)


class _RequestContextProxy:
    def __init__(self, request: Any, metadata: dict[str, Any]) -> None:
        self._request = request
        self.metadata = metadata

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)


__all__ = [
    "RunnerRuntimeClient",
    "RuntimeClient",
    "RuntimeInvocationResponse",
    "RuntimeTerminalReason",
]
