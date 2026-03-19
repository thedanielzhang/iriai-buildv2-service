"""Tests for bootstrap pure functions: slugify, select_workflow, build_state."""

from iriai_build_v2.interfaces._bootstrap import build_state, select_workflow, slugify
from iriai_build_v2.models.state import BugFixState, BuildState


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
