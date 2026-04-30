from __future__ import annotations

from click.testing import CliRunner

from iriai_build_v2.interfaces.cli import app
from iriai_build_v2.runtime_policy import PRIMARY_IMPL_SECONDARY_REVIEW_POLICY


def test_plan_develop_and_bugfix_accept_agent_runtime(monkeypatch, tmp_path):
    calls: list[dict] = []

    async def _fake_run(workflow_name, name, workspace, auto, **kwargs):
        calls.append(
            {
                "workflow_name": workflow_name,
                "name": name,
                "workspace": workspace,
                "auto": auto,
                **kwargs,
            }
        )

    monkeypatch.setattr(app, "_run", _fake_run)
    runner = CliRunner()

    result = runner.invoke(
        app.cli,
        [
            "plan",
            "--name",
            "Pool Plan",
            "--workspace",
            str(tmp_path),
            "--agent-runtime",
            "claude_pool",
            "--auto",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app.cli,
        [
            "develop",
            "--name",
            "Pool Develop",
            "--workspace",
            str(tmp_path),
            "--agent-runtime",
            "pool",
            "--auto",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app.cli,
        [
            "bugfix",
            "--name",
            "Pool Bugfix",
            "--project",
            "demo",
            "--workspace",
            str(tmp_path),
            "--agent-runtime",
            "claude-pool",
            "--auto",
        ],
    )
    assert result.exit_code == 0, result.output

    assert [call["workflow_name"] for call in calls] == ["planning", "full-develop", "bugfix"]
    assert [call["agent_runtime"] for call in calls] == ["claude_pool", "claude_pool", "claude_pool"]


def test_claude_pool_commands_are_registered():
    runner = CliRunner()

    result = runner.invoke(app.cli, ["claude-pool", "--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output
    assert "install-launchagents" in result.output


def test_slack_routes_claude_pool_runtime(monkeypatch):
    calls: list[dict] = []

    async def _fake_run_slack_bridge(**kwargs):
        calls.append(kwargs)

    from iriai_build_v2.interfaces.slack import app as slack_app

    monkeypatch.setattr(slack_app, "run_slack_bridge", _fake_run_slack_bridge)
    runner = CliRunner()

    result = runner.invoke(
        app.cli,
        [
            "slack",
            "--channel",
            "C123",
            "--agent-runtime",
            "claude_pool",
            "--claude-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["agent_runtime"] == "claude_pool"
    assert calls[0]["agent_runtime_override"] is True
    assert calls[0]["single_agent_runtime"] is True


def test_slack_claude_pool_codex_review_flag(monkeypatch):
    calls: list[dict] = []

    async def _fake_run_slack_bridge(**kwargs):
        calls.append(kwargs)

    from iriai_build_v2.interfaces.slack import app as slack_app

    monkeypatch.setattr(slack_app, "run_slack_bridge", _fake_run_slack_bridge)
    runner = CliRunner()

    result = runner.invoke(
        app.cli,
        [
            "slack",
            "--channel",
            "C123",
            "--claude-pool-codex-review",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["agent_runtime"] == "claude_pool"
    assert calls[0]["agent_runtime_override"] is True
    assert calls[0]["runtime_policy"] == PRIMARY_IMPL_SECONDARY_REVIEW_POLICY
    assert calls[0]["runtime_policy_override"] is True
