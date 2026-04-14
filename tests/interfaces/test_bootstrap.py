"""Tests for bootstrap helpers and runner wiring."""

from types import SimpleNamespace

import pytest

from iriai_build_v2.interfaces._bootstrap import (
    build_runner,
    build_state,
    rebuild_state,
    select_workflow,
    slugify,
)
from iriai_build_v2.models.state import BugFixState, BugFixV2State, BuildState


# ── slugify ───────────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert slugify("My Feature") == "my-feature"

    def test_special_chars(self):
        assert slugify("Add OAuth 2.0 Support!") == "add-oauth-2-0-support"

    def test_leading_trailing_stripped(self):
        assert slugify("--hello--") == "hello"

    def test_consecutive_specials(self):
        assert slugify("a   b___c") == "a-b-c"

    def test_already_slug(self):
        assert slugify("already-clean") == "already-clean"

    def test_empty(self):
        assert slugify("") == ""


# ── select_workflow ───────────────────────────────────────────────────────


class TestSelectWorkflow:
    def test_planning(self):
        from iriai_build_v2.workflows import PlanningWorkflow

        wf = select_workflow("planning")
        assert isinstance(wf, PlanningWorkflow)

    def test_bugfix(self):
        from iriai_build_v2.workflows import BugFixWorkflow

        wf = select_workflow("bugfix")
        assert isinstance(wf, BugFixWorkflow)

    def test_bugfix_v2(self):
        from iriai_build_v2.workflows import BugFixV2Workflow

        wf = select_workflow("bugfix-v2")
        assert isinstance(wf, BugFixV2Workflow)

    def test_develop_default(self):
        from iriai_build_v2.workflows import FullDevelopWorkflow

        wf = select_workflow("develop")
        assert isinstance(wf, FullDevelopWorkflow)

    def test_unknown_returns_develop(self):
        from iriai_build_v2.workflows import FullDevelopWorkflow

        wf = select_workflow("anything-else")
        assert isinstance(wf, FullDevelopWorkflow)


# ── build_state ───────────────────────────────────────────────────────────


class TestBuildState:
    def test_returns_build_state_for_planning(self):
        state = build_state("planning")
        assert isinstance(state, BuildState)

    def test_returns_build_state_for_develop(self):
        state = build_state("develop")
        assert isinstance(state, BuildState)

    def test_returns_bugfix_state(self):
        state = build_state("bugfix", project="myproj", bug_report="it broke")
        assert isinstance(state, BugFixState)
        assert state.project == "myproj"
        assert state.bug_report == "it broke"

    def test_bugfix_default_fields(self):
        state = build_state("bugfix")
        assert isinstance(state, BugFixState)
        assert state.project == ""
        assert state.bug_report == ""

    def test_bugfix_v2_state(self):
        state = build_state("bugfix-v2")
        assert isinstance(state, BugFixV2State)
        assert state.source_feature_id == ""
        assert state.phase == "bugflow-setup"

    @pytest.mark.asyncio
    async def test_rebuild_bugfix_v2_state(self):
        class _Artifacts:
            async def get(self, key: str, *, feature):
                values = {
                    "project": "Project workspace: /tmp/workspace",
                    "bugflow-queue": '{"active_step":"Waiting for reports"}',
                    "bugflow-decisions": '[]',
                    "bugflow-source-context": '{"source_feature_id":"beced7b1"}',
                }
                return values.get(key)

        feature = SimpleNamespace(
            id="bf123456",
            metadata={
                "source_feature_id": "beced7b1",
                "source_feature_name": "Checkout flow",
                "workspace_path": "/tmp/workspace",
            },
        )

        state = await rebuild_state("bugfix-v2", _Artifacts(), feature)

        assert isinstance(state, BugFixV2State)
        assert state.source_feature_id == "beced7b1"
        assert state.source_feature_name == "Checkout flow"
        assert state.project == "Project workspace: /tmp/workspace"


class TestBuildRunner:
    def test_codex_primary_uses_codex_for_secondary_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        created: list[str | None] = []

        def _fake_create_agent_runtime(
            name: str | None,
            *,
            session_store=None,
            on_message=None,
            interactive_roles=None,
        ):
            del session_store, on_message, interactive_roles
            created.append(name)
            return SimpleNamespace(name=name)

        class _FakeRunner:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(
            "iriai_build_v2.runtimes.create_agent_runtime",
            _fake_create_agent_runtime,
        )
        monkeypatch.setattr(
            "iriai_build_v2.workflows.TrackedWorkflowRunner",
            _FakeRunner,
        )

        env = SimpleNamespace(
            sessions=object(),
            feature_store=object(),
            artifacts=object(),
            context_provider=object(),
            workspace=object(),
            feedback_service=object(),
            preview_service=object(),
            playwright_service=object(),
            artifact_mirror=object(),
            workspace_manager=object(),
        )

        runner = build_runner(
            env,
            interaction_runtimes={"terminal": object()},
            agent_runtime_name="codex",
        )

        assert created == ["codex", "codex"]
        assert runner.kwargs["agent_runtime"].name == "codex"
        assert runner.kwargs["secondary_runtime"].name == "codex"

    def test_claude_single_runtime_uses_claude_for_secondary_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        created: list[str | None] = []

        def _fake_create_agent_runtime(
            name: str | None,
            *,
            session_store=None,
            on_message=None,
            interactive_roles=None,
        ):
            del session_store, on_message, interactive_roles
            created.append(name)
            return SimpleNamespace(name=name)

        class _FakeRunner:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(
            "iriai_build_v2.runtimes.create_agent_runtime",
            _fake_create_agent_runtime,
        )
        monkeypatch.setattr(
            "iriai_build_v2.workflows.TrackedWorkflowRunner",
            _FakeRunner,
        )

        env = SimpleNamespace(
            sessions=object(),
            feature_store=object(),
            artifacts=object(),
            context_provider=object(),
            workspace=object(),
            feedback_service=object(),
            preview_service=object(),
            playwright_service=object(),
            artifact_mirror=object(),
            workspace_manager=object(),
        )

        runner = build_runner(
            env,
            interaction_runtimes={"terminal": object()},
            agent_runtime_name="claude",
            single_agent_runtime=True,
        )

        assert created == ["claude", "claude"]
        assert runner.kwargs["agent_runtime"].name == "claude"
        assert runner.kwargs["secondary_runtime"].name == "claude"
