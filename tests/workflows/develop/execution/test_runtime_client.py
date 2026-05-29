from __future__ import annotations

import ast
import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iriai_compose.actors import AgentActor, Role
from iriai_compose.exceptions import TaskExecutionError
from iriai_compose.tasks import Ask

from iriai_build_v2.workflows.develop.execution.runtime_client import (
    RunnerRuntimeClient,
    RuntimeClient,
)
import iriai_build_v2.workflows.develop.execution.runtime_client as runtime_client_module


def _request(**overrides):
    data = {
        "attempt_id": 7,
        "invocation_id": "invoke-7",
        "runtime": "codex",
        "actor_name": "implementer",
        "actor_role": "implementer",
        "actor_metadata": SimpleNamespace(actor_name="implementer", actor_role="implementer"),
        "workspace_binding": SimpleNamespace(sandbox_id="sandbox-1", cwd="/tmp/sandbox"),
        "prompt": "Implement the task.",
        "prompt_ref": 11,
        "output_schema": "{}",
        "output_schema_digest": "schema:digest",
        "output_type_name": "ImplementationResult",
        "timeout_seconds": 5,
        "retry_within_invocation": True,
        "cancellation_token": None,
        "metadata": {},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _ask_factory(request, actor):
    return SimpleNamespace(
        actor=actor,
        prompt=request.prompt,
        output_type=getattr(request, "output_type", None),
    )


class _ProviderError(RuntimeError):
    provider_request_id = "req-provider-1"
    error_code = "rate_limited"


@pytest.mark.asyncio
async def test_provider_exception_converts_to_failed_response() -> None:
    class Runner:
        async def run(self, ask):
            assert ask.prompt == "Implement the task."
            raise _ProviderError("provider refused the request")

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request())

    assert response.status == "failed"
    assert response.terminal_reason == "provider_error"
    assert response.provider_request_id == "req-provider-1"
    assert response.provider_error_code == "rate_limited"
    assert response.raw_text == "provider refused the request"
    assert response.process_started is False


@pytest.mark.asyncio
async def test_runtime_client_does_not_hard_timeout_long_running_runner() -> None:
    class Runner:
        async def run(self, ask):
            await asyncio.sleep(0.03)
            return {"task_id": "TASK-7", "summary": "finished", "status": "completed"}

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request(timeout_seconds=0.01))

    assert response.status == "completed"
    assert response.terminal_reason == "completed"
    assert response.structured_output == {
        "task_id": "TASK-7",
        "summary": "finished",
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_runner_timeout_exception_maps_to_timeout_response() -> None:
    class Runner:
        async def run(self, ask):
            raise TimeoutError("runtime stale")

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request(timeout_seconds=0.01))

    assert response.status == "failed"
    assert response.terminal_reason == "timeout"
    assert response.raw_text == "runtime stale"


@pytest.mark.asyncio
async def test_cancellation_before_process_start_does_not_call_runner() -> None:
    class Runner:
        async def run(self, ask):
            raise AssertionError("runner must not be called once cancelled")

    class CancellationRegistry:
        def is_cancelled(self, token):
            return token == "cancel-now"

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
        cancellation_registry=CancellationRegistry(),
    )

    response = await client.invoke(_request(cancellation_token="cancel-now"))

    assert response.status == "cancelled"
    assert response.terminal_reason == "cancelled"
    assert response.process_started is False
    assert response.structured_output is None


@pytest.mark.asyncio
async def test_completed_structured_output_and_provider_metadata_are_preserved() -> None:
    calls = []

    class Runner:
        async def run(self, ask, feature, phase_name=""):
            calls.append((ask, feature, phase_name))
            return SimpleNamespace(
                structured_output={
                    "task_id": "TASK-7",
                    "summary": "updated runtime boundary",
                    "status": "completed",
                },
                raw_text="All done.",
                provider_metadata={
                    "provider_request_id": "req-complete-1",
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                    "adapter_retry_ids": ["adapter-retry-1"],
                },
                raw_artifact_id=101,
            )

    def actor_factory(request):
        return SimpleNamespace(name=request.actor_name, role=request.actor_role)

    client = RuntimeClient(
        runner_factory=lambda request: Runner(),
        actor_factory=actor_factory,
        ask_factory=_ask_factory,
    )

    feature = SimpleNamespace(id="feature-1")
    response = await client.invoke(
        _request(metadata={"feature": feature, "phase_name": "implementation"})
    )

    assert response.status == "completed"
    assert response.terminal_reason == "completed"
    assert response.structured_output == {
        "task_id": "TASK-7",
        "summary": "updated runtime boundary",
        "status": "completed",
    }
    assert response.raw_text == "All done."
    assert response.raw_artifact_id == 101
    assert response.provider_request_id == "req-complete-1"
    assert response.usage == {"input_tokens": 12, "output_tokens": 8}
    assert response.adapter_retry_ids == ["adapter-retry-1"]
    assert response.adapter_retry_count == 1
    assert calls[0][0].actor.name == "implementer"
    assert calls[0][1] is feature
    assert calls[0][2] == "implementation"


@pytest.mark.asyncio
async def test_process_started_failure_is_marked_for_diagnostic_capture() -> None:
    class ProcessFailure(RuntimeError):
        process_started = True
        return_code = 2
        stdout_artifact_id = 201
        stderr_artifact_id = 202

    class Runner:
        @contextlib.contextmanager
        def bind_invocation_observer(self, observer):
            self.observer = observer
            yield

        async def run(self, ask):
            self.observer.on_invocation_start("provider-invocation-1")
            raise ProcessFailure("Codex CLI failed with exit code 2: bad flag")

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request())

    assert response.status == "failed"
    assert response.terminal_reason == "process_failed"
    assert response.process_started is True
    assert response.provider_error_code == "2"
    assert response.stdout_artifact_id == 201
    assert response.stderr_artifact_id == 202
    assert "exit code 2" in response.raw_text


@pytest.mark.asyncio
async def test_schema_error_maps_to_structured_output_invalid() -> None:
    class ResultModel(BaseModel):
        count: int

    class Runner:
        async def run(self, ask):
            ResultModel.model_validate({"count": "not an int"})

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request())

    assert response.status == "failed"
    assert response.terminal_reason == "structured_output_invalid"
    assert "validation error" in response.raw_text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "cwd is under a blocked binding root",
        "cwd is outside bound repo roots",
        "runtime artifact root is symlinked",
        "adapter temp dir is outside sandbox root",
        "Codex runtime temp dir /tmp/x is outside the bound sandbox/artifact roots",
        "Bound Claude write role implementer manifest is outside sandbox root",
        "Bound Claude pool job cwd is outside writable roots",
    ],
)
async def test_bound_workspace_guard_errors_map_to_sandbox_binding_failed(message: str) -> None:
    class Runner:
        async def run(self, ask):
            raise RuntimeError(message)

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request())

    assert response.status == "failed"
    assert response.terminal_reason == "sandbox_binding_failed"
    assert response.raw_text == message


@pytest.mark.asyncio
async def test_wrapped_task_execution_error_uses_inner_binding_failure() -> None:
    inner_message = "Bound Codex write role implementer cwd is outside writable roots"

    class Runner:
        async def run(self, ask):
            task = Ask(
                actor=AgentActor(
                    name="implementer",
                    role=Role(name="implementer", prompt="", tools=["Write"]),
                ),
                prompt=ask.prompt,
            )
            feature = SimpleNamespace(id="8ac124d6")
            try:
                raise RuntimeError(inner_message)
            except RuntimeError as exc:
                raise TaskExecutionError(
                    task=task,
                    feature=feature,
                    phase_name="implementation",
                ) from exc

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request())

    assert response.status == "failed"
    assert response.terminal_reason == "sandbox_binding_failed"
    assert response.raw_text == inner_message


@pytest.mark.asyncio
async def test_runtime_error_without_guard_message_maps_to_provider_error() -> None:
    class Runner:
        async def run(self, ask):
            raise RuntimeError("provider returned an overloaded response")

    client = RuntimeClient(
        runner=Runner(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(_request())

    assert response.status == "failed"
    assert response.terminal_reason == "provider_error"
    assert response.raw_text == "provider returned an overloaded response"


@pytest.mark.asyncio
async def test_runner_runtime_client_accepts_runner_feature_and_phase_context() -> None:
    seen = []

    class Runner:
        async def run(self, ask, feature, phase_name=""):
            seen.append((ask.prompt, feature.id, phase_name))
            return {"task_id": "TASK-7", "summary": "ok"}

    client = RunnerRuntimeClient(
        runner=SimpleNamespace(),
        actor_factory=lambda request: SimpleNamespace(name=request.actor_name),
        ask_factory=_ask_factory,
    )

    response = await client.invoke(
        _request(),
        runner=Runner(),
        feature=SimpleNamespace(id="feature-parent"),
        phase_name="implementation",
    )

    assert response.status == "completed"
    assert response.structured_output == {"task_id": "TASK-7", "summary": "ok"}
    assert seen == [("Implement the task.", "feature-parent", "implementation")]


def test_runtime_client_has_no_route_repair_commit_checkpoint_or_slack_imports() -> None:
    source_path = Path(runtime_client_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.append(node.module or "")
            imported_names.extend(alias.name for alias in node.names)

    forbidden = ("route", "repair", "commit", "checkpoint", "slack")
    offenders = [
        name
        for name in imported_names
        for token in forbidden
        if token in name.lower()
    ]
    assert offenders == []
