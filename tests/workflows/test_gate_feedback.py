from __future__ import annotations

import asyncio
import importlib.util
from types import SimpleNamespace
from pathlib import Path

import pytest

from iriai_compose import Feature

from iriai_build_v2.interfaces.slack.interaction import SlackInteractionRuntime
from iriai_build_v2.models.outputs import Envelope, ScopeOutput
from iriai_build_v2.services.artifacts import ArtifactMirror, _key_to_path
from iriai_build_v2.planning_signals import GateRejection
from iriai_build_v2.roles import scoper, user
from iriai_build_v2.workflows._common._helpers import gate_and_revise, gate_feedback_text


class _FeatureStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None, dict | None]] = []

    async def log_event(self, feature_id, event_type, source, content=None, metadata=None) -> None:
        self.events.append((event_type, source, content, metadata))


class _Artifacts:
    async def get(self, *args, **kwargs):
        return ""

    async def put(self, *args, **kwargs) -> None:
        return None


class _ContextProvider:
    async def resolve(self, *args, **kwargs) -> str:
        return ""


class _SlackAdapter:
    async def post_blocks(self, *args, **kwargs):
        return "123.456"

    async def update_message(self, *args, **kwargs) -> None:
        return None

    async def open_modal(self, *args, **kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_gate_and_revise_uses_structured_gate_feedback_in_revision_prompt():
    prompts: list[str] = []
    feature = SimpleNamespace(id="feat-1", name="Feature", metadata={})
    feature_store = _FeatureStore()
    gate_calls = 0

    async def _run(task, _feature, phase_name):
        nonlocal gate_calls
        del _feature, phase_name
        task_type = type(task).__name__
        if task_type == "Gate":
            gate_calls += 1
            if gate_calls == 1:
                return GateRejection("Please include workflow restart handling in scope.")
            return True
        if task_type == "Interview":
            prompts.append(task.initial_prompt)
            return ScopeOutput(
                feature_name="Feature",
                repositories=[],
                constraints=[],
                out_of_scope=[],
                user_decisions=[],
            )
        raise AssertionError(f"Unexpected task type: {task_type}")

    runner = SimpleNamespace(
        run=_run,
        artifacts=SimpleNamespace(),
        services={},
        feature_store=feature_store,
    )

    await gate_and_revise(
        runner,
        feature,
        "scoping",
        artifact="Draft scope text",
        actor=scoper,
        output_type=ScopeOutput,
        approver=user,
        label="Feature Scope",
    )

    assert prompts
    assert "Please include workflow restart handling in scope." in prompts[0]


@pytest.mark.asyncio
async def test_gate_and_revise_can_use_hosted_revision_and_preserve_structured_scope(tmp_path: Path):
    feature = SimpleNamespace(id="feat-1", name="Feature", metadata={})
    feature_store = _FeatureStore()
    mirror = ArtifactMirror(tmp_path)
    gate_calls = 0
    task_types: list[str] = []
    revised_scope = ScopeOutput(
        summary="Updated summary",
        scope_type="service_change",
        constraints=["Constraint"],
        out_of_scope=["Out"],
        user_decisions=["Decision"],
        complete=True,
    )
    artifact_path = mirror.feature_dir(feature.id) / _key_to_path("scope")

    async def _run(task, _feature, phase_name):
        nonlocal gate_calls
        del _feature, phase_name
        task_types.append(type(task).__name__)
        if type(task).__name__ == "Gate":
            gate_calls += 1
            if gate_calls == 1:
                return GateRejection("Please revise the summary.")
            return True
        if type(task).__name__ == "HostedInterview":
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                "# Feature Scope\n\n## User Decisions\n\n- Decision\n\n## Summary\n\nOut of order",
                encoding="utf-8",
            )
            return Envelope[ScopeOutput](
                output=revised_scope,
                complete=True,
                artifact_path=str(artifact_path),
            )
        raise AssertionError(f"Unexpected task type: {type(task).__name__}")

    runner = SimpleNamespace(
        run=_run,
        artifacts=SimpleNamespace(),
        services={"artifact_mirror": mirror},
        feature_store=feature_store,
    )

    artifact, artifact_text = await gate_and_revise(
        runner,
        feature,
        "scoping",
        artifact=ScopeOutput(summary="Draft summary"),
        actor=scoper,
        output_type=ScopeOutput,
        approver=user,
        label="Feature Scope",
        artifact_key="scope",
        hosted_revision=True,
        prefer_structured_output=True,
    )

    assert isinstance(artifact, ScopeOutput)
    assert artifact == revised_scope
    assert artifact_text == revised_scope.model_dump_json(indent=2)
    assert task_types == ["Gate", "HostedInterview", "Gate"]


def test_gate_feedback_text_accepts_gate_rejection_like_payloads():
    class _ForeignGateRejection:
        def __init__(self, feedback: str) -> None:
            self.feedback = feedback

    assert gate_feedback_text(_ForeignGateRejection("Use the Slack feedback")) == (
        "Use the Slack feedback"
    )
    assert gate_feedback_text({"feedback": "Use the dict feedback"}) == (
        "Use the dict feedback"
    )


class _ComposeLikeRunner:
    def __init__(self, runtime: SlackInteractionRuntime) -> None:
        self._runtime = runtime
        self.feature_store = _FeatureStore()
        self.artifacts = _Artifacts()
        self.context_provider = _ContextProvider()
        self.services = {}
        self.sessions = None
        self._phase_name = ""

    async def run(self, task, feature, **kwargs):
        phase_name = kwargs.pop("phase_name", "")
        if phase_name:
            self._phase_name = phase_name
        return await task.execute(self, feature, **kwargs)

    async def resolve(self, task, feature, **kwargs):
        return await self._runtime.ask(
            task,
            feature_id=feature.id,
            phase_name=self._phase_name,
            kind=kwargs.get("kind"),
            options=kwargs.get("options"),
        )


def _load_example_compose_gate():
    path = Path("/Users/danielzhang/src/iriai/iriai-compose/examples/_composite_tasks.py")
    spec = importlib.util.spec_from_file_location("compose_example_tasks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    namespace = dict(module.__dict__)
    module.Ask.model_rebuild(_types_namespace=namespace)
    module.Gate.model_rebuild(_types_namespace=namespace)
    return module.Gate


@pytest.mark.asyncio
async def test_compose_gate_preserves_structured_reject_feedback():
    main_gate = _load_example_compose_gate()
    runtime = SlackInteractionRuntime(_SlackAdapter())
    runtime.register_channel("feat-1", "C001")
    runner = _ComposeLikeRunner(runtime)
    feature = Feature(
        id="feat-1",
        name="Feature",
        slug="feature",
        workflow_name="full-develop",
        workspace_id="main",
    )

    async def _resolve_later():
        await asyncio.sleep(0.01)
        pending_id = next(iter(runtime._pending_futures))
        runtime._resolve_pending(
            pending_id,
            GateRejection("Please include workflow restart handling in scope."),
            label="Rejected",
        )

    waiter = asyncio.create_task(_resolve_later())
    result = await runner.run(
        main_gate(approver=user, prompt="Approve the scope?"),
        feature,
        phase_name="scoping",
    )
    await waiter

    assert result == "Please include workflow restart handling in scope."
