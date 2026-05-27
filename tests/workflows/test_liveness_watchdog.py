"""Tests for the liveness watchdog in TrackedWorkflowRunner.

Verifies that:
- Active agents (emitting on_message) are not interrupted
- Silent agents are detected and cancelled after LIVENESS_TIMEOUT
- Retries happen on stall with backoff
- Codex subprocess cleanup on cancellation
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from iriai_compose import Workspace
from iriai_compose.tasks import Ask

from iriai_build_v2.agent_concurrency import AgentConcurrencyLimiter
from iriai_build_v2.roles import implementer, smoke_tester, test_author, verifier
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


class _LongContextProvider:
    def __init__(self, context: str) -> None:
        self.context = context

    async def resolve(self, *_args, **_kwargs):
        return self.context


class _FeatureStore:
    def __init__(self) -> None:
        self.events: list[tuple[tuple, dict]] = []

    async def log_event(self, *_args, **_kwargs):
        self.events.append((_args, _kwargs))
        return None


class _CaptureRuntime:
    name = "capture"
    on_message = None

    def __init__(self) -> None:
        self.task = None
        self.kwargs = None

    async def ask(self, task, **kwargs):
        self.task = task
        self.kwargs = kwargs
        return "ok"


class _CountingRuntime:
    name = "counting"
    on_message = None

    def __init__(self, *, fail_actor: str | None = None) -> None:
        self.fail_actor = fail_actor
        self.active = 0
        self.max_active = 0

    async def ask(self, task, **_kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            if task.actor.name == self.fail_actor:
                raise RuntimeError("boom")
            return task.actor.name
        finally:
            self.active -= 1


def _feature(feature_id: str = "bf123456"):
    return SimpleNamespace(id=feature_id, workspace_id="main", metadata={})


def _write_sandbox_manifest(
    sandbox_root: Path,
    *,
    sandbox_id: str,
    writable_roots: list[str],
) -> Path:
    """Write a minimal-but-valid Slice 04 ``sandbox-manifest.json``.

    ``TrackedWorkflowRunner.resolve`` validates a write-producing role's
    ``runtime_workspace_binding`` against an on-disk sandbox manifest
    (``_runtime_workspace_binding_error`` with ``validate_manifest=True``):
    the manifest must exist under a real ``root`` directory that also
    contains the bound ``cwd`` and every writable root. This helper produces
    that manifest so a bound write role can resolve successfully (or fail on
    a *later* check, e.g. ``expires_at``).
    """
    sandbox_root.mkdir(parents=True, exist_ok=True)
    manifest_path = sandbox_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sandbox_id": sandbox_id,
                "root": str(sandbox_root.resolve()),
                "writable_roots": [
                    str(Path(root).resolve()) for root in writable_roots
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _ask(name: str) -> Ask:
    role = implementer.role.model_copy(
        update={
            "metadata": {
                **implementer.role.metadata,
                "liveness_timeout": 0,
            }
        }
    )
    return Ask(
        actor=implementer.model_copy(update={"name": name, "role": role}),
        prompt="do work",
    )


def _runner(runtime, limiter: AgentConcurrencyLimiter | None = None) -> TrackedWorkflowRunner:
    return TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        agent_runtime=runtime,
        secondary_runtime=None,
        agent_concurrency_limiter=limiter,
        interaction_runtimes={"terminal": object()},
        artifacts=object(),
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )


@pytest.mark.asyncio
async def test_runner_offloads_combined_context_and_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from iriai_build_v2.workflows._common import _helpers

    monkeypatch.setattr(_helpers, "PROMPT_FILE_THRESHOLD", 100)
    runtime = _CaptureRuntime()
    feature_store = _FeatureStore()
    runner = TrackedWorkflowRunner(
        feature_store=feature_store,
        agent_runtime=runtime,
        secondary_runtime=None,
        interaction_runtimes={"terminal": object()},
        artifacts=object(),
        sessions=object(),
        context_provider=_LongContextProvider("context " * 40),
        workspaces={"main": Workspace(id="main", path=tmp_path)},
    )
    runner.services["worktree_root"] = tmp_path

    result = await runner.run(
        Ask(actor=smoke_tester, prompt="short task"),
        _feature(),
    )

    assert result == "ok"
    assert runtime.task is not None
    assert "Your full task prompt is in `" in runtime.task.prompt
    assert runtime.kwargs["context"] == ""
    prompt_path = re.search(r"`([^`]+)`", runtime.task.prompt)
    assert prompt_path is not None
    offloaded = Path(prompt_path.group(1)).read_text(encoding="utf-8")
    assert "context context" in offloaded
    assert "## Task" in offloaded
    assert "short task" in offloaded
    agent_start_events = [
        kwargs for args, kwargs in feature_store.events
        if len(args) >= 2 and args[1] == "agent_start"
    ]
    assert agent_start_events
    metadata = agent_start_events[-1]["metadata"]
    assert metadata["prompt_offloaded"] is True
    assert metadata["context_length"] > 100
    assert metadata["combined_prompt_length"] == len(offloaded)
    assert metadata["runtime_prompt_length"] < metadata["combined_prompt_length"]


@pytest.mark.asyncio
async def test_bound_runner_does_not_offload_prompt_into_bound_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from iriai_build_v2.workflows._common import _helpers

    monkeypatch.setattr(_helpers, "PROMPT_FILE_THRESHOLD", 100)
    runtime = _CaptureRuntime()
    feature_store = _FeatureStore()
    sandbox_root = tmp_path / "sandbox"
    bound = sandbox_root / "bound"
    bound.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root, sandbox_id="sandbox-04", writable_roots=[str(bound)]
    )
    runner = TrackedWorkflowRunner(
        feature_store=feature_store,
        agent_runtime=runtime,
        secondary_runtime=None,
        interaction_runtimes={"terminal": object()},
        artifacts=object(),
        sessions=object(),
        context_provider=_LongContextProvider("context " * 40),
        workspaces={"main": Workspace(id="main", path=tmp_path)},
    )
    role = implementer.role.model_copy(
        update={
            "metadata": {
                **implementer.role.metadata,
                "liveness_timeout": 0,
                "runtime_workspace_binding": {
                    "sandbox_id": "sandbox-04",
                    "cwd": str(bound),
                    "workspace_override": str(bound),
                    "writable_roots": [str(bound)],
                    "readonly_roots": [],
                    "blocked_roots": [str(tmp_path / "blocked")],
                    "manifest_path": str(manifest_path),
                    "expires_at": "2999-01-01T00:00:00+00:00",
                    "runtime": "claude",
                },
            },
        }
    )
    actor = implementer.model_copy(update={"role": role})

    result = await runner.resolve(actor, "short task", feature=_feature())

    assert result == "ok"
    assert runtime.task is not None
    assert "Your full task prompt is in `" not in runtime.task.prompt
    assert runtime.kwargs["context"] == "context " * 40
    assert not (bound / ".iriai-context").exists()
    agent_start_events = [
        kwargs for args, kwargs in feature_store.events
        if len(args) >= 2 and args[1] == "agent_start"
    ]
    assert agent_start_events
    metadata = agent_start_events[-1]["metadata"]
    assert metadata["prompt_offloaded"] is False
    assert metadata["prompt_offload_path"] == ""


@pytest.mark.asyncio
async def test_bound_runner_ignores_legacy_workspace_override_and_worktree_root(tmp_path: Path):
    runtime = _CaptureRuntime()
    runner = _runner(runtime)
    legacy = tmp_path / "legacy"
    worktree = tmp_path / "worktree"
    sandbox_root = tmp_path / "sandbox"
    bound = sandbox_root / "bound"
    legacy.mkdir()
    worktree.mkdir()
    bound.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root, sandbox_id="sandbox-04", writable_roots=[str(bound)]
    )
    runner.services["worktree_root"] = worktree

    role = implementer.role.model_copy(
        update={
            "metadata": {
                **implementer.role.metadata,
                "workspace_override": str(legacy),
                "liveness_timeout": 0,
                "runtime_workspace_binding": {
                    "sandbox_id": "sandbox-04",
                    "cwd": str(bound),
                    "workspace_override": str(bound),
                    "writable_roots": [str(bound)],
                    "readonly_roots": [],
                    "blocked_roots": [str(tmp_path / "blocked")],
                    "manifest_path": str(manifest_path),
                    "expires_at": "2999-01-01T00:00:00+00:00",
                    "runtime": "claude",
                },
            },
        }
    )
    actor = implementer.model_copy(update={"role": role})

    result = await runner.resolve(actor, "write in the bound workspace", feature=_feature())

    assert result == "ok"
    assert runtime.kwargs["workspace"].path == bound
    assert f"Your working directory is `{bound}`" in runtime.task.prompt
    assert str(legacy) not in runtime.task.prompt
    assert str(worktree) not in runtime.task.prompt


@pytest.mark.asyncio
async def test_sandbox_required_write_role_without_binding_fails_closed(tmp_path: Path):
    runtime = _CaptureRuntime()
    runner = _runner(runtime)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    role = implementer.role.model_copy(
        update={
            "metadata": {
                **implementer.role.metadata,
                "workspace_override": str(legacy),
                "sandbox_required": True,
                "liveness_timeout": 0,
            },
        }
    )
    actor = implementer.model_copy(update={"role": role})

    with pytest.raises(RuntimeError, match="Runtime workspace binding required"):
        await runner.resolve(actor, "write without binding", feature=_feature())

    assert runtime.task is None


@pytest.mark.asyncio
async def test_sandbox_required_test_author_without_binding_fails_closed(tmp_path: Path):
    runtime = _CaptureRuntime()
    runner = _runner(runtime)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    role = test_author.role.model_copy(
        update={
            "metadata": {
                **test_author.role.metadata,
                "workspace_override": str(legacy),
                "sandbox_required": True,
                "liveness_timeout": 0,
            },
        }
    )
    actor = test_author.model_copy(update={"role": role})

    with pytest.raises(RuntimeError, match="Runtime workspace binding required"):
        await runner.resolve(actor, "author tests without binding", feature=_feature())

    assert runtime.task is None


def _bound_role_with_metadata(metadata: dict) -> object:
    role = implementer.role.model_copy(
        update={
            "metadata": {
                **implementer.role.metadata,
                "sandbox_required": True,
                "liveness_timeout": 0,
                "runtime_workspace_binding": metadata,
            },
        }
    )
    return implementer.model_copy(update={"role": role})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "mutate", "match"),
    [
        (
            "symlink_cwd",
            lambda tmp_path, binding: binding.update({
                "cwd": str(tmp_path / "cwd-link"),
                "workspace_override": str(tmp_path / "cwd-link"),
            }),
            "cwd is symlinked",
        ),
        (
            "missing_cwd",
            lambda tmp_path, binding: binding.update({
                "cwd": str(tmp_path / "missing"),
                "workspace_override": str(tmp_path / "missing"),
            }),
            "cwd does not exist",
        ),
        (
            "workspace_override_mismatch",
            lambda tmp_path, binding: binding.update({
                "workspace_override": str(tmp_path / "other"),
            }),
            "workspace_override must match cwd",
        ),
        (
            "blocked_root",
            lambda tmp_path, binding: binding.update({
                "blocked_roots": [str(tmp_path)],
            }),
            "blocked root",
        ),
        (
            "outside_allowed_roots",
            lambda tmp_path, binding: binding.update({
                "writable_roots": [str(tmp_path / "other")],
                "repo_roots": {"app": str(tmp_path / "other")},
            }),
            "outside runtime workspace roots",
        ),
        (
            "missing_expires_at",
            lambda _tmp_path, binding: binding.pop("expires_at"),
            "missing expires_at",
        ),
        (
            "expired",
            lambda _tmp_path, binding: binding.update({
                "expires_at": "2000-01-01T00:00:00+00:00",
            }),
            "binding is expired",
        ),
    ],
)
async def test_invalid_runtime_workspace_binding_metadata_fails_before_runtime(
    tmp_path: Path,
    case: str,
    mutate,
    match: str,
):
    del case
    runtime = _CaptureRuntime()
    runner = _runner(runtime)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "cwd-link").symlink_to(cwd, target_is_directory=True)
    # A valid sandbox manifest so the metadata-mutation cases fail on the
    # specific binding field under test (e.g. `expires_at`) rather than on a
    # missing manifest — the cases that fail on an earlier path/root check
    # never reach the manifest block, so this is inert for them.
    manifest_path = _write_sandbox_manifest(
        tmp_path, sandbox_id="sandbox-04", writable_roots=[str(cwd)]
    )
    binding = {
        "sandbox_id": "sandbox-04",
        "cwd": str(cwd),
        "workspace_override": str(cwd),
        "writable_roots": [str(cwd)],
        "readonly_roots": [],
        "blocked_roots": [str(tmp_path / "blocked")],
        "repo_roots": {"app": str(cwd)},
        "manifest_path": str(manifest_path),
        "expires_at": "2999-01-01T00:00:00+00:00",
        "runtime": "claude",
    }
    mutate(tmp_path, binding)
    actor = _bound_role_with_metadata(binding)

    with pytest.raises(RuntimeError, match=match):
        await runner.resolve(actor, "write with invalid binding", feature=_feature())

    assert runtime.task is None


@pytest.mark.asyncio
async def test_runtime_workspace_binding_allows_file_level_writable_root(
    tmp_path: Path,
):
    runtime = _CaptureRuntime()
    runner = _runner(runtime)
    cwd = tmp_path / "cwd"
    writable_file = cwd / "src" / "allowed.py"
    writable_file.parent.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        tmp_path,
        sandbox_id="sandbox-04",
        writable_roots=[str(writable_file)],
    )
    actor = _bound_role_with_metadata(
        {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "workspace_override": str(cwd),
            "writable_roots": [str(writable_file)],
            "readonly_roots": [],
            "blocked_roots": [str(tmp_path / "blocked")],
            "repo_roots": {"app": str(cwd)},
            "manifest_path": str(manifest_path),
            "expires_at": "2999-01-01T00:00:00+00:00",
            "runtime": "claude",
        }
    )

    result = await runner.resolve(actor, "write with file-level root", feature=_feature())

    assert result == "ok"
    assert runtime.kwargs["workspace"].path == cwd


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


@pytest.mark.asyncio
async def test_tracked_runner_limits_parallel_agent_invocations():
    runtime = _CountingRuntime()
    limiter = AgentConcurrencyLimiter(2)
    runner = _runner(runtime, limiter)

    results = await runner.parallel(
        [_ask(f"worker-{idx}") for idx in range(5)],
        _feature(),
    )

    assert results == [f"worker-{idx}" for idx in range(5)]
    assert runtime.max_active == 2
    assert limiter.active_count == 0
    assert limiter.queued_count == 0


@pytest.mark.asyncio
async def test_shared_agent_limiter_caps_multiple_runners():
    runtime = _CountingRuntime()
    limiter = AgentConcurrencyLimiter(2)
    runner_a = _runner(runtime, limiter)
    runner_b = _runner(runtime, limiter)

    await asyncio.gather(
        runner_a.parallel([_ask(f"a-{idx}") for idx in range(3)], _feature("feat-a")),
        runner_b.parallel([_ask(f"b-{idx}") for idx in range(3)], _feature("feat-b")),
    )

    assert runtime.max_active == 2
    assert limiter.active_count == 0
    assert limiter.queued_count == 0


@pytest.mark.asyncio
async def test_agent_limiter_releases_permit_after_runtime_error():
    runtime = _CountingRuntime(fail_actor="fail")
    limiter = AgentConcurrencyLimiter(1)
    runner = _runner(runtime, limiter)

    with pytest.raises(RuntimeError, match="boom"):
        await runner.resolve(_ask("fail"), _feature())

    assert limiter.active_count == 0
    assert limiter.queued_count == 0


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
