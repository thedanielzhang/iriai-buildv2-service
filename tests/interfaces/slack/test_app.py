from __future__ import annotations

import signal

import pytest

import iriai_build_v2.interfaces.slack.app as slack_app
from iriai_build_v2.execution_control import startup as ec_startup


class _StopStartup(Exception):
    """Aborts run_slack_bridge once we've sampled the signal state, so the test
    never has to mock the full Slack adapter/orchestrator startup."""


@pytest.mark.asyncio
async def test_early_sigusr1_guard_installed_before_slow_startup(monkeypatch):
    # Regression: the operator-free resume trigger (SIGUSR1, sent by the dashboard
    # via os.kill) must not kill the bridge when it races startup. The real async
    # handler is installed only after the multi-second control-plane / Slack-auth
    # startup; until then SIGUSR1's default disposition is 'terminate'. A resume
    # sent in that window killed the bridge (observed exit code -30). The early
    # guard must therefore be installed BEFORE any slow startup step.
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    captured: dict[str, object] = {}

    def fake_flag():
        # Sampled at the first startup step, right after the early guard install
        # and before any slow await — exactly the window a racing resume hits.
        captured["handler"] = signal.getsignal(signal.SIGUSR1)
        return ec_startup.EnvFlagState.DISABLED

    async def fake_load(_ids):
        raise _StopStartup()

    monkeypatch.setattr(ec_startup, "read_control_plane_env_flag", fake_flag)
    monkeypatch.setattr(slack_app, "_load_ignored_mention_user_ids", fake_load)

    original = signal.getsignal(signal.SIGUSR1)
    try:
        with pytest.raises(_StopStartup):
            await slack_app.run_slack_bridge(planning_channel="C0TEST", workspace=None)
    finally:
        signal.signal(signal.SIGUSR1, original)

    handler = captured.get("handler")
    # A real, callable handler is installed during startup — NOT the default
    # 'terminate' disposition that would kill the bridge on a racing resume.
    assert handler not in (signal.SIG_DFL, signal.SIG_IGN, None)
    assert callable(handler)
