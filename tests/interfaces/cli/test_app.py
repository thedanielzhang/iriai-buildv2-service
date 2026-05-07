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


def test_slack_concurrency_max_flag(monkeypatch):
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
            "codex",
            "--concurrency-max",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["agent_runtime"] == "codex"
    assert calls[0]["concurrency_max"] == 2


def test_slack_ignore_mention_user_id_flag(monkeypatch):
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
            "--ignore-mention-user-id",
            "U_SUPERVISOR",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["ignored_mention_user_ids"] == {"U_SUPERVISOR"}


def test_slack_verbosity_flag(monkeypatch):
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
            "--slack-verbosity",
            "quiet",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["slack_verbosity"] == "quiet"


def test_slack_concurrency_max_rejects_zero():
    runner = CliRunner()

    result = runner.invoke(
        app.cli,
        [
            "slack",
            "--channel",
            "C123",
            "--concurrency-max",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value for '--concurrency-max'" in result.output


def test_supervisor_command_uses_separate_token_env_names(monkeypatch):
    calls: list[dict] = []

    async def _fake_run_supervisor_slack_app(**kwargs):
        calls.append(kwargs)

    from iriai_build_v2.supervisor import slack as supervisor_slack

    monkeypatch.setattr(
        supervisor_slack,
        "run_supervisor_slack_app",
        _fake_run_supervisor_slack_app,
    )
    runner = CliRunner()

    result = runner.invoke(
        app.cli,
        [
            "supervisor",
            "--channel",
            "CSUP",
            "--feature",
            "feat-1",
            "--dashboard-url",
            "https://dash.example/feature/feat-1",
            "--runtime",
            "codex",
            "--mode",
            "singleplayer",
            "--supervisor-mode",
            "guarded",
            "--poll-interval",
            "15",
            "--digest-interval",
            "45",
            "--worktree-root",
            "/tmp/feature/repos/iriai-studio",
            "--forbidden-path",
            "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0] == {
        "channel": "CSUP",
        "feature_id": "feat-1",
        "dashboard_url": "https://dash.example/feature/feat-1",
        "runtime": "codex",
        "mode": "singleplayer",
        "supervisor_mode": "guarded",
        "poll_interval_seconds": 15.0,
        "min_digest_interval_seconds": 45.0,
        "worktree_roots": ["/tmp/feature/repos/iriai-studio"],
        "forbidden_paths": [
            "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
        ],
        "app_token_env": "SUPERVISOR_SLACK_APP_TOKEN",
        "bot_token_env": "SUPERVISOR_SLACK_BOT_TOKEN",
    }


def test_supervisor_command_defaults_to_singleplayer(monkeypatch):
    calls: list[dict] = []

    async def _fake_run_supervisor_slack_app(**kwargs):
        calls.append(kwargs)

    from iriai_build_v2.supervisor import slack as supervisor_slack

    monkeypatch.setattr(
        supervisor_slack,
        "run_supervisor_slack_app",
        _fake_run_supervisor_slack_app,
    )
    runner = CliRunner()

    result = runner.invoke(app.cli, ["supervisor", "--channel", "CSUP"])

    assert result.exit_code == 0, result.output
    assert calls[0]["mode"] == "singleplayer"
