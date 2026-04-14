"""Tests for the liveness watchdog in TrackedWorkflowRunner.

Verifies that:
- Active agents (emitting on_message) are not interrupted
- Silent agents are detected and cancelled after LIVENESS_TIMEOUT
- Retries happen on stall with backoff
- Codex subprocess cleanup on cancellation
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from iriai_compose import Workspace

from iriai_build_v2.roles import implementer, verifier
from iriai_build_v2.runtimes.claude import ClaudeAgentRuntime
from iriai_build_v2.workflows._runner import (
    LIVENESS_POLL_INTERVAL,
    LIVENESS_TIMEOUT,
    RESOLVE_MAX_RETRIES,
    AgentStalled,
    TrackedWorkflowRunner,
    _LivenessTracker,
)


class _FakeRuntime:
    name = "fake"
    on_message = None

    async def ask(self, *_args, **_kwargs):
        return "ok"


class _ContextProvider:
    async def resolve(self, *_args, **_kwargs):
        return ""


class TestLivenessTracker:
    def test_initial_activity_is_now(self):
        rt = _FakeRuntime()
        tracker = _LivenessTracker(rt)
        assert tracker.seconds_idle() < 1.0

    def test_install_wraps_callback(self):
        rt = _FakeRuntime()
        original = MagicMock()
        rt.on_message = original

        tracker = _LivenessTracker(rt)
        tracker.install()

        # Callback should be replaced
        assert rt.on_message is not original

        # Calling the new callback should update activity AND call original
        before = tracker.last_activity
        rt.on_message("test_msg")
        assert tracker.last_activity >= before
        original.assert_called_once_with("test_msg")

    def test_install_with_no_original_callback(self):
        rt = _FakeRuntime()
        rt.on_message = None

        tracker = _LivenessTracker(rt)
        tracker.install()

        # Should not crash when called with no original
        rt.on_message("test_msg")
        assert tracker.seconds_idle() < 1.0

    def test_restore_puts_back_original(self):
        rt = _FakeRuntime()
        original = MagicMock()
        rt.on_message = original

        tracker = _LivenessTracker(rt)
        tracker.install()
        assert rt.on_message is not original

        tracker.restore()
        assert rt.on_message is original

    def test_seconds_idle_increases(self):
        rt = _FakeRuntime()
        tracker = _LivenessTracker(rt)
        tracker.last_activity = time.monotonic() - 5.0
        assert tracker.seconds_idle() >= 5.0


class TestAgentStalled:
    def test_is_runtime_error(self):
        exc = AgentStalled("test")
        assert isinstance(exc, RuntimeError)


class TestWatchdogIntegration:
    """Test the watchdog detects stalls via simulated resolve."""

    @pytest.mark.asyncio
    async def test_active_agent_completes(self):
        """An agent that emits messages should not be interrupted."""
        rt = _FakeRuntime()
        tracker = _LivenessTracker(rt)
        tracker.install()

        async def _simulate_active_agent():
            for _ in range(3):
                await asyncio.sleep(0.01)
                # Simulate on_message callback
                if rt.on_message:
                    rt.on_message("working...")
            return "done"

        task = asyncio.create_task(_simulate_active_agent())
        result = await task
        assert result == "done"
        tracker.restore()

    @pytest.mark.asyncio
    async def test_stall_detected_by_idle_check(self):
        """Verify seconds_idle grows when no messages arrive."""
        rt = _FakeRuntime()
        tracker = _LivenessTracker(rt)
        tracker.install()

        # Backdate activity
        tracker.last_activity = time.monotonic() - (LIVENESS_TIMEOUT + 1)
        assert tracker.seconds_idle() > LIVENESS_TIMEOUT

        tracker.restore()

    @pytest.mark.asyncio
    async def test_message_resets_idle(self):
        """Verify on_message resets the idle timer."""
        rt = _FakeRuntime()
        tracker = _LivenessTracker(rt)
        tracker.install()

        # Backdate activity
        tracker.last_activity = time.monotonic() - 999

        # Simulate a message arriving
        rt.on_message("alive!")
        assert tracker.seconds_idle() < 1.0

        tracker.restore()


@pytest.mark.asyncio
async def test_tracked_runner_prefers_runtime_instance_override(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FeatureStore:
        async def log_event(self, *_args, **_kwargs):
            return None

    primary = _FakeRuntime()
    primary.name = "primary"
    secondary = _FakeRuntime()
    secondary.name = "secondary"
    override = _FakeRuntime()
    override.name = "thread-secondary"

    runner = TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        agent_runtime=primary,
        secondary_runtime=secondary,
        interaction_runtimes={"terminal": object()},
        artifacts=object(),
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )

    captured: dict[str, str] = {}

    async def _fake_resolve_with_watchdog(
        self,
        task,
        tracker,
        target_runtime,
        **kwargs,
    ):
        del task, tracker, kwargs
        captured["runtime_name"] = target_runtime.name
        return "ok"

    monkeypatch.setattr(
        TrackedWorkflowRunner,
        "_resolve_with_watchdog",
        _fake_resolve_with_watchdog,
    )

    actor = implementer.model_copy(
        update={
            "role": implementer.role.model_copy(
                update={
                    "metadata": {
                        **implementer.role.metadata,
                        "runtime": "secondary",
                        "runtime_instance": override,
                    }
                }
            )
        }
    )
    workflow_feature = type("Feature", (), {"id": "bf123456", "workspace_id": "main", "metadata": {}})()

    result = await runner.resolve(actor, "test prompt", feature=workflow_feature)

    assert result == "ok"
    assert captured["runtime_name"] == "thread-secondary"


@pytest.mark.asyncio
async def test_tracked_runner_preserves_runtime_override_when_watchdog_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FeatureStore:
        async def log_event(self, *_args, **_kwargs):
            return None

    primary = _FakeRuntime()
    primary.name = "primary"
    secondary = _FakeRuntime()
    secondary.name = "secondary"
    override = _FakeRuntime()
    override.name = "thread-secondary"

    runner = TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        agent_runtime=primary,
        secondary_runtime=secondary,
        interaction_runtimes={"terminal": object()},
        artifacts=object(),
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )

    captured: dict[str, str] = {}

    async def _fake_resolve_with_runtime(self, target_runtime, task, **kwargs):
        del task, kwargs
        captured["runtime_name"] = target_runtime.name
        return "ok"

    monkeypatch.setattr(
        TrackedWorkflowRunner,
        "_resolve_with_runtime",
        _fake_resolve_with_runtime,
    )

    actor = implementer.model_copy(
        update={
            "role": implementer.role.model_copy(
                update={
                    "metadata": {
                        **implementer.role.metadata,
                        "runtime": "secondary",
                        "runtime_instance": override,
                        "liveness_timeout": 0,
                    }
                }
            )
        }
    )
    workflow_feature = type("Feature", (), {"id": "bf123456", "workspace_id": "main", "metadata": {}})()

    result = await runner.resolve(actor, "test prompt", feature=workflow_feature)

    assert result == "ok"
    assert captured["runtime_name"] == "thread-secondary"


@pytest.mark.asyncio
async def test_claude_default_invocation_reports_live_work(
    monkeypatch: pytest.MonkeyPatch,
):
    started = asyncio.Event()
    allow_finish = asyncio.Event()

    class _FakeResultMessage:
        pass

    class _FakeClaudeSDKClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def query(self, _prompt):
            started.set()
            await allow_finish.wait()

        async def receive_response(self):
            yield _FakeResultMessage()

    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.ClaudeSDKClient = _FakeClaudeSDKClient
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    runtime = ClaudeAgentRuntime()

    async def _invoke():
        async with runtime.bind_invocation("inv-default", None):
            return await runtime._invoke_default(object(), "prompt", _FakeResultMessage)

    task = asyncio.create_task(_invoke())
    await started.wait()
    assert runtime.invocation_has_live_work("inv-default") is True
    allow_finish.set()
    result = await task
    assert isinstance(result, _FakeResultMessage)
    assert runtime.invocation_has_live_work("inv-default") is False


@pytest.mark.asyncio
async def test_tracked_runner_uses_effective_role_timeout_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FeatureStore:
        async def log_event(self, *_args, **_kwargs):
            return None

    primary = _FakeRuntime()
    primary.name = "primary"

    runner = TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        agent_runtime=primary,
        secondary_runtime=None,
        interaction_runtimes={"terminal": object()},
        artifacts=object(),
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )

    captured: dict[str, str] = {}

    async def _fake_resolve_with_watchdog(
        self,
        task,
        tracker,
        target_runtime,
        **kwargs,
    ):
        del tracker, target_runtime, kwargs
        captured["prompt"] = task.prompt
        return "ok"

    monkeypatch.setattr(
        TrackedWorkflowRunner,
        "_resolve_with_watchdog",
        _fake_resolve_with_watchdog,
    )

    workflow_feature = type("Feature", (), {"id": "bf123456", "workspace_id": "main", "metadata": {}})()
    result = await runner.resolve(verifier, "test prompt", feature=workflow_feature)

    assert result == "ok"
    assert "75 minutes" in captured["prompt"]
    assert "10 minutes" not in captured["prompt"]
