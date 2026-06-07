"""Regression tests for the `resume` CLI command + `_run_resume` semantics.

The CLI previously had NO resume path (only Slack's `_resume_workflow`), so an
interrupted agent-driven run could not be re-attached without risking a fresh
feature. These tests pin the additive `resume` command: it loads the EXISTING
feature and calls `resume_workflow` (skipping completed phases) — never
`create_feature`/`execute_workflow`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from click.testing import CliRunner

from iriai_build_v2.interfaces.cli.app import _run_resume, cli


def test_resume_command_registered_with_options() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["resume", "--help"])
    assert result.exit_code == 0
    for opt in ("--feature-id", "--workspace", "--from-phase", "--driver", "--agent-runtime"):
        assert opt in result.output


def test_resume_requires_feature_id() -> None:
    result = CliRunner().invoke(cli, ["resume"])
    assert result.exit_code != 0
    assert "feature-id" in result.output.lower()


def test_run_resume_loads_existing_feature_and_calls_resume_workflow(monkeypatch, tmp_path) -> None:
    """_run_resume must load the existing feature and resume_workflow from its
    persisted phase — and must NOT create a fresh feature / execute_workflow."""
    calls: dict[str, object] = {}

    feature = SimpleNamespace(
        id="5b280bb4",
        name="Submittal Management",
        workflow_name="planning",
        metadata={"_db_phase": "subfeature"},
    )

    class FakeFeatureStore:
        async def get_feature(self, feature_id: str):
            calls["get_feature"] = feature_id
            return feature

    fake_env = SimpleNamespace(
        feature_store=FakeFeatureStore(),
        artifacts=object(),
        pool=object(),
    )

    sentinel_workflow = object()
    sentinel_state = object()

    class FakeRunner:
        async def resume_workflow(self, workflow, feat, state, *, resume_from_phase):
            calls["resume"] = {
                "workflow": workflow,
                "feature": feat,
                "state": state,
                "resume_from_phase": resume_from_phase,
            }
            return state

        async def execute_workflow(self, *a, **k):  # must NOT be called
            raise AssertionError("resume must not call execute_workflow")

    async def fake_bootstrap(_path):
        return fake_env

    def fake_build_runner(env, **kwargs):
        calls["build_runner"] = kwargs
        return FakeRunner()

    async def fake_assert(**kwargs):
        calls["resume_guard"] = kwargs.get("is_resume")
        return None

    async def fake_rebuild_state(workflow_name, artifacts, feat):
        calls["rebuild_state"] = workflow_name
        return sentinel_state

    def fake_select_workflow(name):
        return sentinel_workflow

    async def fake_teardown(_env):
        calls["teardown"] = True

    import iriai_build_v2.interfaces._bootstrap as bootstrap_mod
    import iriai_build_v2.execution_control.startup as startup_mod

    monkeypatch.setattr(bootstrap_mod, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(bootstrap_mod, "build_runner", fake_build_runner)
    monkeypatch.setattr(bootstrap_mod, "maybe_assert_adopted_or_legacy_for_resume", fake_assert)
    monkeypatch.setattr(bootstrap_mod, "rebuild_state", fake_rebuild_state)
    monkeypatch.setattr(bootstrap_mod, "select_workflow", fake_select_workflow)
    monkeypatch.setattr(bootstrap_mod, "teardown", fake_teardown)
    # Keep the control-plane env check a strict pass-through (no ENABLED branch).
    monkeypatch.setattr(startup_mod, "read_control_plane_env_flag", lambda: None)

    asyncio.run(
        _run_resume(
            "5b280bb4",
            str(tmp_path),
            agent_runtime="claude",
            driver="auto",
            from_phase=None,
        )
    )

    assert calls["get_feature"] == "5b280bb4"
    assert calls["resume_guard"] is True
    assert calls["rebuild_state"] == "planning"
    # Resume phase defaults to the feature's persisted _db_phase.
    assert calls["resume"]["resume_from_phase"] == "subfeature"
    assert calls["resume"]["workflow"] is sentinel_workflow
    assert calls["resume"]["feature"] is feature
    assert calls["teardown"] is True


def test_run_resume_explicit_from_phase_overrides(monkeypatch, tmp_path) -> None:
    """An explicit --from-phase wins over the persisted phase."""
    captured: dict[str, object] = {}

    feature = SimpleNamespace(
        id="abc", name="x", workflow_name="planning", metadata={"_db_phase": "subfeature"}
    )

    class FakeFeatureStore:
        async def get_feature(self, fid):
            return feature

    fake_env = SimpleNamespace(feature_store=FakeFeatureStore(), artifacts=object(), pool=object())

    class FakeRunner:
        async def resume_workflow(self, workflow, feat, state, *, resume_from_phase):
            captured["phase"] = resume_from_phase

    import iriai_build_v2.interfaces._bootstrap as bootstrap_mod
    import iriai_build_v2.execution_control.startup as startup_mod

    async def fake_bootstrap(_p):
        return fake_env

    async def noop_async(*a, **k):
        return None

    monkeypatch.setattr(bootstrap_mod, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(bootstrap_mod, "build_runner", lambda env, **k: FakeRunner())
    monkeypatch.setattr(bootstrap_mod, "maybe_assert_adopted_or_legacy_for_resume", noop_async)
    monkeypatch.setattr(bootstrap_mod, "rebuild_state", noop_async)
    monkeypatch.setattr(bootstrap_mod, "select_workflow", lambda n: object())
    monkeypatch.setattr(bootstrap_mod, "teardown", noop_async)
    monkeypatch.setattr(startup_mod, "read_control_plane_env_flag", lambda: None)

    asyncio.run(
        _run_resume("abc", str(tmp_path), agent_runtime="claude", driver="auto", from_phase="plan-review")
    )
    assert captured["phase"] == "plan-review"
