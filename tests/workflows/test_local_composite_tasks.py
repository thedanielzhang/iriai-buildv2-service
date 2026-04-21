from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from iriai_compose import (
    AgentActor,
    AgentRuntime,
    Ask,
    DefaultWorkflowRunner,
    Feature,
    InMemoryStore,
    InteractionActor,
    InteractionRuntime,
    Role,
    Workspace,
)
from iriai_compose.prompts import Select

from iriai_build_v2.interfaces.auto_interaction import (
    AgentDelegateInteractionRuntime,
    _ApprovalDecision,
    _ChoiceDecision,
)
from iriai_build_v2.models.outputs import (
    Envelope,
    ReviewOutcome,
    RevisionPlan,
    RevisionRequest,
    Subfeature,
    SubfeatureDecomposition,
    Verdict,
)
from iriai_build_v2.models.state import BuildState
from iriai_build_v2.planning_signals import GateRejection
from iriai_build_v2.workflows._common import Choose, Gate, Interview, Notify, Respond
from iriai_build_v2.workflows._common._helpers import (
    TargetedRevisionFailure,
    TargetedRevisionResult,
)
from iriai_build_v2.workflows._runner import TrackedWorkflowRunner
from iriai_build_v2.workflows.planning.phases import plan_review as plan_review_module
from iriai_build_v2.workflows.planning.phases.plan_review import PlanReviewPhase


class _MockAgentRuntime(AgentRuntime):
    name = "mock-agent"

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def ask(self, task, **kwargs):
        call = {
            "prompt": task.to_prompt(),
            "context": kwargs.get("context", ""),
            "continuation": task.continuation,
        }
        self.calls.append(call)
        return self._handler(call)


class _MockInteractionRuntime(InteractionRuntime):
    name = "mock-human"

    def __init__(self, *, choose: Any = "", respond: str = "mock input"):
        self._choose = choose
        self._respond = respond
        self.calls: list[dict[str, Any]] = []

    async def ask(self, task, **kwargs):
        del kwargs
        self.calls.append(
            {
                "prompt": task.prompt,
                "input": task.input,
                "input_type": task.input_type,
            }
        )
        if isinstance(task.input, Select):
            if self._choose != "":
                return self._choose
            return task.input.options[0] if task.input.options else ""
        return self._respond


class _NotifyRuntime(InteractionRuntime):
    name = "notify"

    def __init__(self) -> None:
        self.ask_calls: list[Any] = []
        self.notify_calls: list[dict[str, str]] = []

    async def ask(self, task, **kwargs):
        self.ask_calls.append((task, kwargs))
        raise AssertionError("Notify should not route through InteractionRuntime.ask")

    async def notify(self, *, feature_id: str, phase_name: str, message: str) -> None:
        self.notify_calls.append(
            {
                "feature_id": feature_id,
                "phase_name": phase_name,
                "message": message,
            }
        )


class _FeatureStore:
    async def log_event(self, *args, **kwargs):
        del args, kwargs
        return None


@pytest.fixture
def feature() -> Feature:
    return Feature(
        id="feat-1",
        name="Feature",
        slug="feature",
        workflow_name="planning",
        workspace_id="main",
    )


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(id="main", path=Path("/tmp/workspace"), branch="main")


@pytest.mark.asyncio
async def test_local_interview_loops_until_done(feature: Feature, workspace: Workspace):
    role = Role(name="pm", prompt="Ask questions.")
    questioner = AgentActor(name="pm", role=role)
    responder = InteractionActor(name="user", resolver="human")

    def _handler(call: dict[str, Any]) -> str:
        if call["continuation"]:
            return "DONE"
        return "What problem does this feature solve?"

    runner = DefaultWorkflowRunner(
        runtimes={
            "agent": _MockAgentRuntime(_handler),
            "human": _MockInteractionRuntime(respond="It unifies review tasks."),
        },
        stores={"artifacts": InMemoryStore()},
        workspaces={"main": workspace},
    )

    result = await runner.run(
        Interview(
            questioner=questioner,
            responder=responder,
            initial_prompt="Kick off discovery.",
            done=lambda value: value == "DONE",
        ),
        feature,
    )

    assert result == "DONE"


@pytest.mark.asyncio
async def test_local_gate_returns_feedback_from_gate_rejection(feature: Feature, workspace: Workspace):
    approver = InteractionActor(name="user", resolver="human")
    runner = DefaultWorkflowRunner(
        runtimes={
            "agent": _MockAgentRuntime(lambda call: "unused"),
            "human": _MockInteractionRuntime(choose=GateRejection(feedback="Needs fixes.")),
        },
        stores={"artifacts": InMemoryStore()},
        workspaces={"main": workspace},
    )

    result = await runner.run(Gate(approver=approver, prompt="Approve?"), feature)

    assert result == "Needs fixes."


@pytest.mark.asyncio
async def test_local_choose_and_respond_proxy_to_interaction_runtime(feature: Feature, workspace: Workspace):
    chooser = InteractionActor(name="user", resolver="human")
    runner = DefaultWorkflowRunner(
        runtimes={
            "agent": _MockAgentRuntime(lambda call: "unused"),
            "human": _MockInteractionRuntime(choose="B", respond="More detail"),
        },
        stores={"artifacts": InMemoryStore()},
        workspaces={"main": workspace},
    )

    chosen = await runner.run(
        Choose(chooser=chooser, prompt="Pick one", options=["A", "B"]),
        feature,
    )
    replied = await runner.run(
        Respond(responder=chooser, prompt="Tell me more"),
        feature,
    )

    assert chosen == "B"
    assert replied == "More detail"


@pytest.mark.asyncio
async def test_notify_uses_runtime_notify_without_creating_interaction(feature: Feature, workspace: Workspace):
    runtime = _NotifyRuntime()
    runner = DefaultWorkflowRunner(
        runtimes={"terminal": runtime},
        stores={"artifacts": InMemoryStore()},
        workspaces={"main": workspace},
    )
    runner.interaction_runtimes = {"terminal": runtime}

    await runner.run(
        Notify(message="Re-running reviewers to verify..."),
        feature,
        phase_name="plan-review",
    )

    assert runtime.ask_calls == []
    assert runtime.notify_calls == [
        {
            "feature_id": feature.id,
            "phase_name": "plan-review",
            "message": "Re-running reviewers to verify...",
        }
    ]


@pytest.mark.asyncio
async def test_autonomous_runtime_treats_select_prompts_as_choose() -> None:
    runtime = AgentDelegateInteractionRuntime(agent_runtime=None)
    task = Ask(
        actor=InteractionActor(name="user", resolver="human"),
        prompt="Pick the best option",
        input=Select(options=["Option A", "Option B"]),
        input_type=Select,
    )

    async def _run_delegate_task(**kwargs):
        assert kwargs["actor_name"] == "autonomous-chooser"
        return _ChoiceDecision(choice="Option B")

    runtime._run_delegate_task = _run_delegate_task  # type: ignore[method-assign]

    result = await runtime.ask(task)

    assert result == "Option B"


@pytest.mark.asyncio
async def test_local_gate_routes_autonomous_runtime_through_approval_path(
    feature: Feature,
    workspace: Workspace,
) -> None:
    runtime = AgentDelegateInteractionRuntime(agent_runtime=None)

    async def _run_delegate_task(**kwargs):
        assert kwargs["actor_name"] == "autonomous-approver"
        return _ApprovalDecision(approved=False, feedback="Needs fixes.")

    runtime._run_delegate_task = _run_delegate_task  # type: ignore[method-assign]

    runner = DefaultWorkflowRunner(
        runtimes={"auto": runtime},
        stores={"artifacts": InMemoryStore()},
        workspaces={"main": workspace},
    )

    result = await runner.run(
        Gate(
            approver=InteractionActor(name="user", resolver="auto"),
            prompt="Approve this architecture?",
        ),
        feature,
    )

    assert result == "Needs fixes."


@pytest.mark.asyncio
async def test_tracked_runner_legacy_choose_normalization_uses_real_select(
    feature: Feature,
    workspace: Workspace,
) -> None:
    runtime = _MockInteractionRuntime(choose="B")
    runner = TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        runtimes={"human": runtime},
        stores={"artifacts": InMemoryStore()},
        workspaces={"main": workspace},
    )

    result = await runner.resolve(
        InteractionActor(name="user", resolver="human"),
        "Pick one",
        feature=feature,
        kind="choose",
        options=["A", "B"],
    )

    assert result == "B"
    assert isinstance(runtime.calls[0]["input"], Select)
    assert runtime.calls[0]["input_type"] is Select


class _Artifacts:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.values = dict(initial or {})

    async def get(self, key: str, *, feature):
        del feature
        return self.values.get(key, "")

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.values[key] = value


class _StopAfterNotify(Exception):
    pass


class _PlanReviewRunner:
    def __init__(self, *, artifacts: _Artifacts, run_results: list[Any]) -> None:
        self.artifacts = artifacts
        self.services = {}
        self.feature_store = None
        self._run_results = list(run_results)
        self.notifications: list[str] = []

    async def run(self, task, feature, phase_name=""):
        del feature, phase_name
        if isinstance(task, Notify):
            self.notifications.append(task.message)
            raise _StopAfterNotify
        if not self._run_results:
            raise AssertionError(f"Unexpected runner.run call for {type(task).__name__}")
        result = self._run_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.mark.asyncio
async def test_plan_review_revision_summary_uses_notify(monkeypatch):
    feature = SimpleNamespace(id="feat-plan-review", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments"),
        ],
        complete=True,
    )
    state = BuildState(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        plan="existing plan",
        system_design="existing system design",
    )
    artifacts = _Artifacts(
        {
            "plan": "existing plan",
            "plan:payments": "existing subfeature plan",
        }
    )
    runner = _PlanReviewRunner(
        artifacts=artifacts,
        run_results=[
            Verdict(approved=False, summary="Needs changes"),
            Verdict(approved=False, summary="Needs changes"),
            Envelope[ReviewOutcome](
                output=ReviewOutcome(
                    approved=False,
                    revision_plan=RevisionPlan(
                        requests=[
                            RevisionRequest(
                                description="Revise the technical plan.",
                                reasoning="The plan review found a missing requirement.",
                                affected_subfeatures=["payments"],
                                affected_artifact_types=["plan"],
                            )
                        ],
                    ),
                    complete=True,
                ),
                complete=True,
            ),
        ],
    )

    async def _normalize(*args, **kwargs):
        del args, kwargs
        return (
            False,
            None,
            RevisionPlan(
                requests=[
                    RevisionRequest(
                        description="Revise the technical plan.",
                        reasoning="The plan review found a missing requirement.",
                        affected_subfeatures=["payments"],
                        affected_artifact_types=["plan"],
                    )
                ]
            ),
        )

    async def _noop_revision(*args, **kwargs):
        del args, kwargs
        return TargetedRevisionResult(
            artifact_prefix="plan",
            revised_slugs=["payments"],
        )

    async def _compile(*args, **kwargs):
        del args, kwargs
        return "updated technical plan"

    async def _skip_gates(self, runner_arg, feature_arg, state_arg, decomposition_arg):
        del self, runner_arg, feature_arg, decomposition_arg
        return state_arg

    monkeypatch.setattr(plan_review_module, "_normalize_plan_review_state", _normalize)
    monkeypatch.setattr(plan_review_module, "targeted_revision", _noop_revision)
    monkeypatch.setattr(plan_review_module, "compile_artifacts", _compile)
    monkeypatch.setattr(PlanReviewPhase, "_run_gates", _skip_gates)

    with pytest.raises(_StopAfterNotify):
        await PlanReviewPhase().execute(runner, feature, state)

    assert runner.notifications == [
        "## Revisions Applied (Cycle 1)\n\n"
        "- plan: revised (13 → 22 bytes)\n\n"
        "Re-running reviewers to verify..."
    ]


@pytest.mark.asyncio
async def test_plan_review_blocks_when_required_revision_batch_fails(monkeypatch):
    feature = SimpleNamespace(id="feat-plan-review-blocked", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments"),
        ],
        complete=True,
    )
    state = BuildState(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        system_design="existing system design",
    )
    artifacts = _Artifacts(
        {
            "system-design": "existing system design",
            "system-design:payments": "existing subfeature system design",
        }
    )
    runner = _PlanReviewRunner(
        artifacts=artifacts,
        run_results=[
            Verdict(approved=False, summary="Needs changes"),
            Verdict(approved=False, summary="Needs changes"),
            Envelope[ReviewOutcome](
                output=ReviewOutcome(
                    approved=False,
                    revision_plan=RevisionPlan(
                        requests=[
                            RevisionRequest(
                                description="Revise the system design.",
                                reasoning="The review found a missing contract.",
                                affected_subfeatures=["payments"],
                                affected_artifact_types=["system-design"],
                            )
                        ],
                    ),
                    complete=True,
                ),
                complete=True,
            ),
        ],
    )

    async def _normalize(*args, **kwargs):
        del args, kwargs
        return (
            False,
            None,
            RevisionPlan(
                requests=[
                    RevisionRequest(
                        description="Revise the system design.",
                        reasoning="The review found a missing contract.",
                        affected_subfeatures=["payments"],
                        affected_artifact_types=["system-design"],
                    )
                ]
            ),
        )

    async def _failed_revision(*args, **kwargs):
        del args, kwargs
        return TargetedRevisionResult(
            artifact_prefix="system-design",
            failed=[
                TargetedRevisionFailure(
                    artifact_prefix="system-design",
                    slug="payments",
                    reason="batch 0 failed: prompt too long",
                )
            ],
        )

    compile_calls: list[str] = []

    async def _compile(*args, **kwargs):
        compile_calls.append(kwargs.get("artifact_prefix", ""))
        return "updated system design"

    monkeypatch.setattr(plan_review_module, "_normalize_plan_review_state", _normalize)
    monkeypatch.setattr(plan_review_module, "targeted_revision", _failed_revision)
    monkeypatch.setattr(plan_review_module, "compile_artifacts", _compile)

    with pytest.raises(_StopAfterNotify):
        await PlanReviewPhase().execute(runner, feature, state)

    assert compile_calls == []
    assert len(runner.notifications) == 1
    assert "## Plan Review Blocked (Cycle 1)" in runner.notifications[0]
    assert "system-design:payments — batch 0 failed: prompt too long" in runner.notifications[0]
