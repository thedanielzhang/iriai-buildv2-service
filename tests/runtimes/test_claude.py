from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose.actors import Role
from pydantic import BaseModel

from iriai_build_v2.config import BUDGET_TIERS
from iriai_build_v2.runtimes.claude import (
    ClaudeAgentRuntime,
    ClaudeApiErrorStorm,
    ClaudeStreamWatchdogStall,
    StructuredOutputExhausted,
    _API_ERROR_STORM_THRESHOLD,
    _LIVE_WORK_STALE_SECONDS,
    _resolve_stream_inactivity_timeout,
    _stream_inactivity_timeout_s,
)
from iriai_build_v2.workflows.develop.execution.runtime_client import _classify_exception


class _FakeClaudeAgentOptions:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakePermissionResultAllow:
    pass


class _FakePermissionResultDeny:
    def __init__(self, *, message: str):
        self.message = message


def _write_sandbox_manifest(
    sandbox_root: Path,
    cwd: Path,
    *,
    sandbox_id: str = "sandbox-04",
    writable_roots: list[Path] | None = None,
) -> Path:
    manifest_path = sandbox_root / "sandbox-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "sandbox-runner-v1",
                "sandbox_id": sandbox_id,
                "root": str(sandbox_root),
                "repo_roots": {"app": str(cwd)},
                "writable_roots": [
                    str(path) for path in (writable_roots if writable_roots is not None else [cwd])
                ],
                "blocked_roots": [],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_budget_tiers_use_opus_4_8_native_1m_context():
    assert BUDGET_TIERS["opus"] == "claude-opus-4-8"
    assert BUDGET_TIERS["opus_1m"] == "claude-opus-4-8"


def test_build_options_default_to_opus_4_8_high_effort(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="pm", prompt="Plan the work", tools=["Read"])

    options = runtime._build_options(role, workspace=None)

    assert options.model == "claude-opus-4-8"
    assert options.effort == "high"


def test_build_options_preserves_explicit_effort(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="summarizer", prompt="Summarize", tools=["Read"], effort="high")

    options = runtime._build_options(role, workspace=None)

    assert options.model == "claude-opus-4-8"
    assert options.effort == "high"


def test_build_options_normalizes_legacy_xhigh_effort_to_high(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    role = SimpleNamespace(
        name="implementer",
        prompt="Build",
        tools=["Read"],
        model="claude-opus-4-8",
        effort="xhigh",
        metadata={},
    )

    options = runtime._build_options(role, workspace=None)

    assert options.effort == "high"


def test_neutralize_clears_nested_claudecode_marker(monkeypatch):
    from iriai_build_v2.runtimes.claude import _neutralize_nested_claude_session_env

    monkeypatch.setenv("CLAUDECODE", "1")

    cleared = _neutralize_nested_claude_session_env()

    assert cleared is True
    assert "CLAUDECODE" not in os.environ


def test_neutralize_leaves_non_marker_value(monkeypatch):
    from iriai_build_v2.runtimes.claude import _neutralize_nested_claude_session_env

    monkeypatch.setenv("CLAUDECODE", "0")

    cleared = _neutralize_nested_claude_session_env()

    assert cleared is False
    assert os.environ.get("CLAUDECODE") == "0"


def test_runtime_init_clears_nested_claudecode_marker(monkeypatch):
    # The runtime is, by design, launched from inside a Claude Code session,
    # where CLAUDECODE=1 would otherwise crash every spawned CLI subprocess.
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    monkeypatch.setenv("CLAUDECODE", "1")

    ClaudeAgentRuntime()

    assert os.environ.get("CLAUDECODE") != "1"


@pytest.mark.asyncio
async def test_bound_write_role_forces_sandbox_and_blocks_escapes(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk.types",
        SimpleNamespace(
            PermissionResultAllow=_FakePermissionResultAllow,
            PermissionResultDeny=_FakePermissionResultDeny,
        ),
    )

    cwd = tmp_path / "sandbox"
    repo = cwd / "repo"
    outside = tmp_path / "outside"
    repo.mkdir(parents=True)
    outside.mkdir()
    (repo / "link-out").symlink_to(outside, target_is_directory=True)
    manifest_path = _write_sandbox_manifest(cwd, repo)

    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write", "Edit"],
        metadata={
            "sandbox": False,
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo),
                "workspace_override": str(repo),
                "writable_roots": [str(repo)],
                "readonly_roots": [],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": "2999-01-01T00:00:00+00:00",
                "runtime": "claude",
            },
        },
    )

    options = runtime._build_options(role, workspace=SimpleNamespace(path=str(outside)))

    assert options.cwd == str(repo)
    assert options.sandbox == {"enabled": True}
    assert options.can_use_tool is not None
    assert not getattr(options, "add_dirs", None)
    allowed = await options.can_use_tool("Write", {"file_path": "src/new.py"}, None)
    absolute_escape = await options.can_use_tool("Write", {"file_path": str(outside / "x.py")}, None)
    relative_escape = await options.can_use_tool("Edit", {"file_path": "../outside/x.py"}, None)
    symlink_escape = await options.can_use_tool("Write", {"file_path": "link-out/x.py"}, None)

    assert isinstance(allowed, _FakePermissionResultAllow)
    assert isinstance(absolute_escape, _FakePermissionResultDeny)
    assert isinstance(relative_escape, _FakePermissionResultDeny)
    assert isinstance(symlink_escape, _FakePermissionResultDeny)


@pytest.mark.asyncio
async def test_bound_write_role_accepts_file_level_writable_roots(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk.types",
        SimpleNamespace(
            PermissionResultAllow=_FakePermissionResultAllow,
            PermissionResultDeny=_FakePermissionResultDeny,
        ),
    )

    sandbox_root = tmp_path / "sandbox"
    repo = sandbox_root / "repos" / "app"
    allowed_file = repo / "src" / "allowed.py"
    allowed_file.parent.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root,
        repo,
        writable_roots=[allowed_file],
    )
    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write"],
        metadata={
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo),
                "workspace_override": str(repo),
                "repo_roots": {"app": str(repo)},
                "writable_roots": [str(allowed_file)],
                "readonly_roots": [],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": "2999-01-01T00:00:00+00:00",
                "runtime": "claude",
            },
        },
    )

    options = runtime._build_options(role, workspace=SimpleNamespace(path=str(repo)))

    assert options.cwd == str(repo)
    allowed = await options.can_use_tool("Write", {"file_path": "src/allowed.py"}, None)
    sibling = await options.can_use_tool("Write", {"file_path": "src/other.py"}, None)
    assert isinstance(allowed, _FakePermissionResultAllow)
    assert isinstance(sibling, _FakePermissionResultDeny)


def test_bound_write_role_rejects_writable_root_outside_bound_repo(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    sandbox_root = tmp_path / "sandbox"
    repo = sandbox_root / "repos" / "app"
    outside_repo_file = sandbox_root / "other" / "allowed.py"
    repo.mkdir(parents=True)
    outside_repo_file.parent.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root,
        repo,
        writable_roots=[outside_repo_file],
    )
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write"],
        metadata={
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo),
                "workspace_override": str(repo),
                "repo_roots": {"app": str(repo)},
                "writable_roots": [str(outside_repo_file)],
                "readonly_roots": [],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": "2999-01-01T00:00:00+00:00",
                "runtime": "claude",
            },
        },
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    with pytest.raises(RuntimeError, match="writable root is outside bound repo roots"):
        runtime._build_options(role, workspace=SimpleNamespace(path=str(repo)))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda binding, canonical, root: binding.update({"manifest_path": str(root / "missing.json")}), "sandbox manifest does not exist"),
        (
            lambda binding, canonical, root: (
                (root / "bad-manifest.json").write_text("{", encoding="utf-8"),
                binding.update({"manifest_path": str(root / "bad-manifest.json")}),
            ),
            "unreadable sandbox manifest",
        ),
        (
            lambda binding, canonical, root: binding.update(
                {"cwd": str(canonical), "workspace_override": str(canonical)}
            ),
            "cwd is outside sandbox root",
        ),
        (
            lambda binding, canonical, root: binding.update({"writable_roots": [str(canonical)]}),
            "binding writable root is outside sandbox root|binding writable roots do not match manifest",
        ),
    ],
)
def test_bound_write_role_rejects_unproved_binding(
    monkeypatch,
    tmp_path: Path,
    mutate,
    message: str,
):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )

    sandbox_root = tmp_path / "sandbox"
    cwd = sandbox_root / "repos" / "app"
    cwd.mkdir(parents=True)
    canonical = tmp_path / "canonical" / "app"
    canonical.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(sandbox_root, cwd)
    binding = {
        "sandbox_id": "sandbox-04",
        "cwd": str(cwd),
        "workspace_override": str(cwd),
        "writable_roots": [str(cwd)],
        "readonly_roots": [],
        "blocked_roots": [],
        "manifest_path": str(manifest_path),
        "expires_at": "2999-01-01T00:00:00+00:00",
        "runtime": "claude",
    }
    mutate(binding, canonical, tmp_path)
    role = Role(
        name="implementer",
        prompt="Implement safely.",
        tools=["Read", "Write"],
        metadata={"runtime_workspace_binding": binding},
    )

    runtime = object.__new__(ClaudeAgentRuntime)
    with pytest.raises(RuntimeError, match=message):
        runtime._build_options(role, workspace=SimpleNamespace(path=str(cwd)))


class _WatchdogResultMessage:
    def __init__(self, result: str = "ok"):
        self.result = result


class _BaseFakeClient:
    """Async-context-manager Claude client stub. Subclasses override the phase
    (connect / query / receive) that should hang or stream."""

    async def __aenter__(self):
        await self._on_connect()
        return self

    async def __aexit__(self, *exc):
        return False

    async def _on_connect(self):
        return None

    async def query(self, prompt):
        return None

    def receive_response(self):
        async def _gen():
            if False:  # pragma: no cover - empty async generator
                yield None

        return _gen()


class _SilentClient(_BaseFakeClient):
    """receive_response() yields nothing and never ends — models a wedged CLI
    subprocess that stays alive but produces no output."""

    def receive_response(self):
        async def _gen():
            await asyncio.sleep(3600)
            yield None  # pragma: no cover - never reached

        return _gen()


class _ConnectHangClient(_BaseFakeClient):
    """__aenter__ (connect) never returns — models a CLI that wedges during the
    control-protocol init handshake before any message. The earlier
    receive-only watchdog missed this entirely; the lifecycle watchdog must
    still fire."""

    async def _on_connect(self):
        await asyncio.sleep(3600)


class _QueryHangClient(_BaseFakeClient):
    """query() never returns — models a wedge while sending the prompt."""

    async def query(self, prompt):
        await asyncio.sleep(3600)


class _ActiveClient(_BaseFakeClient):
    """Streams `n_pre` non-result messages (each after `gap` seconds) then a
    ResultMessage — models a healthy job that keeps the stream active."""

    def __init__(self, result_msg, *, n_pre: int = 4, gap: float = 0.1):
        self._result_msg = result_msg
        self._n_pre = n_pre
        self._gap = gap

    def receive_response(self):
        async def _gen():
            for _ in range(self._n_pre):
                if self._gap:
                    await asyncio.sleep(self._gap)
                yield object()
            yield self._result_msg

        return _gen()


class _InnerTimeoutClient(_BaseFakeClient):
    """receive_response() raises a genuine TimeoutError from inside the stream
    (not the watchdog deadline)."""

    def receive_response(self):
        async def _gen():
            raise TimeoutError("provider timed out")
            yield None  # pragma: no cover - unreachable, makes this a generator

        return _gen()


def _bare_runtime() -> ClaudeAgentRuntime:
    runtime = object.__new__(ClaudeAgentRuntime)
    runtime.on_message = None
    # _emit_message only touches _invocation_activity when an invocation id is
    # set on the contextvar; provide it so the test never passes by luck.
    runtime._invocation_activity = {}
    runtime._active_invocations = {}
    return runtime


@pytest.mark.asyncio
async def test_dispatch_watchdog_raises_on_silent_stream():
    runtime = _bare_runtime()

    start = time.monotonic()
    with pytest.raises(ClaudeStreamWatchdogStall):
        await runtime._run_dispatch_bounded(
            lambda: _SilentClient(), "p", _WatchdogResultMessage, 0.05
        )
    elapsed = time.monotonic() - start

    # The whole point: it must NOT hang on a silent subprocess.
    assert elapsed < 5.0


@pytest.mark.asyncio
async def test_dispatch_watchdog_raises_on_connect_hang():
    # The phase the receive-only watchdog missed: a wedge during connect, before
    # any message ever arrives. The lifecycle watchdog must still fire.
    runtime = _bare_runtime()

    start = time.monotonic()
    with pytest.raises(ClaudeStreamWatchdogStall):
        await runtime._run_dispatch_bounded(
            lambda: _ConnectHangClient(), "p", _WatchdogResultMessage, 0.05
        )
    assert time.monotonic() - start < 5.0


@pytest.mark.asyncio
async def test_dispatch_watchdog_raises_on_query_hang():
    runtime = _bare_runtime()

    start = time.monotonic()
    with pytest.raises(ClaudeStreamWatchdogStall):
        await runtime._run_dispatch_bounded(
            lambda: _QueryHangClient(), "p", _WatchdogResultMessage, 0.05
        )
    assert time.monotonic() - start < 5.0


@pytest.mark.asyncio
async def test_dispatch_watchdog_returns_result_on_active_stream():
    # Total stream duration (5 messages * 0.1s = 0.5s) exceeds the window (0.3s),
    # but each inter-message gap (0.1s) is well under it — so a working stream
    # that keeps emitting must reset the deadline and never trip the watchdog.
    # A naive total-runtime cap would fire at 0.3s and fail this test.
    runtime = _bare_runtime()
    result = _WatchdogResultMessage("done")

    out = await runtime._run_dispatch_bounded(
        lambda: _ActiveClient(result, n_pre=4, gap=0.1), "p", _WatchdogResultMessage, 0.3
    )

    assert out is result


class _WedgedSubprocessClient(_BaseFakeClient):
    """Silent stream (so the inactivity watchdog fires) that exposes a live CLI
    subprocess via client._transport._process.pid — models the wedge where the
    subprocess won't reap, so the watchdog must SIGKILL the pid to unblock."""

    def __init__(self, pid: int) -> None:
        self._transport = SimpleNamespace(_process=SimpleNamespace(pid=pid))

    def receive_response(self):
        async def _gen():
            await asyncio.sleep(3600)
            yield None  # pragma: no cover - never reached

        return _gen()


@pytest.mark.asyncio
async def test_dispatch_watchdog_sigkills_wedged_subprocess(monkeypatch):
    # The wedge: the CLI subprocess won't reap, so anyio process.wait() never
    # resolves and task.cancel() alone re-hangs on the SDK teardown's wait().
    # The watchdog must SIGKILL the live pid first so wait() resolves.
    runtime = _bare_runtime()
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    start = time.monotonic()
    with pytest.raises(ClaudeStreamWatchdogStall):
        await runtime._run_dispatch_bounded(
            lambda: _WedgedSubprocessClient(424242), "p", _WatchdogResultMessage, 0.05
        )

    assert time.monotonic() - start < 5.0
    assert killed == [(424242, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_invocation_live_work_goes_stale_when_stream_wedges():
    # The liveness watchdog (workflows/_runner.py) extends its grace period while
    # an invocation reports live work. A wedged receive stops refreshing the
    # activity timestamp, so it must go stale -> False, letting the watchdog
    # cancel instead of extending grace forever (the 8ac124d6 freeze).
    runtime = _bare_runtime()
    loop = asyncio.get_running_loop()

    runtime._active_invocations["inv"] = loop.time()
    assert runtime.invocation_has_live_work("inv") is True

    runtime._active_invocations["inv"] = loop.time() - (_LIVE_WORK_STALE_SECONDS + 5)
    assert runtime.invocation_has_live_work("inv") is False

    assert runtime.invocation_has_live_work("unknown") is False


@pytest.mark.asyncio
async def test_dispatch_watchdog_disabled_skips_watchdog():
    # inactivity_timeout=None (role opt-out) must drain without a watchdog.
    runtime = _bare_runtime()
    result = _WatchdogResultMessage("done")

    out = await runtime._run_dispatch_bounded(
        lambda: _ActiveClient(result, n_pre=2, gap=0.0), "p", _WatchdogResultMessage, None
    )

    assert out is result


@pytest.mark.asyncio
async def test_dispatch_watchdog_reraises_inner_timeout():
    # A genuine TimeoutError from inside the stream must stay a TimeoutError
    # (classified "timeout"), not be relabeled a watchdog stall.
    runtime = _bare_runtime()

    with pytest.raises(TimeoutError) as excinfo:
        # Generous window so the watchdog deadline itself never fires.
        await runtime._run_dispatch_bounded(
            lambda: _InnerTimeoutClient(), "p", _WatchdogResultMessage, 30.0
        )

    assert not isinstance(excinfo.value, ClaudeStreamWatchdogStall)
    assert _classify_exception(excinfo.value) == "timeout"


@pytest.mark.asyncio
async def test_dispatch_watchdog_runs_on_connected_and_cleanup():
    # The interactive path registers the live client via on_connected and must
    # run the returned cleanup when the dispatch ends.
    runtime = _bare_runtime()
    result = _WatchdogResultMessage("done")
    events: list[str] = []

    def on_connected(client):
        events.append("connected")
        return lambda: events.append("cleanup")

    out = await runtime._run_dispatch_bounded(
        lambda: _ActiveClient(result, n_pre=1, gap=0.0),
        "p",
        _WatchdogResultMessage,
        1.0,
        on_connected=on_connected,
    )

    assert out is result
    assert events == ["connected", "cleanup"]


def test_watchdog_stall_classifies_as_watchdog_stall():
    reason = _classify_exception(
        ClaudeStreamWatchdogStall("Claude CLI stream watchdog stalled: produced no output for 600s")
    )
    assert reason == "watchdog_stall"


def test_structured_output_exhausted_contract_for_lane_reaper():
    # The bugfix lane reaper (queue._exc_is_structured_output_exhausted) detects
    # this failure by isinstance AND by class name to classify it terminal, so
    # the type must stay a RuntimeError subclass named exactly this. Reverting
    # the raise sites to a bare RuntimeError would silently restore the
    # respawn/dead-stall loop, so lock the contract here.
    assert issubclass(StructuredOutputExhausted, RuntimeError)
    assert StructuredOutputExhausted.__name__ == "StructuredOutputExhausted"


def test_resolve_stream_inactivity_timeout_respects_role_metadata(monkeypatch):
    monkeypatch.delenv("IRIAI_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S", raising=False)

    base = dict(name="impl", prompt="x", tools=["Read"])

    # No metadata → env default (600s).
    assert _resolve_stream_inactivity_timeout(Role(**base)) == 600.0
    # liveness_timeout=0 → disabled (opt-out for long silent suites).
    assert (
        _resolve_stream_inactivity_timeout(Role(**base, metadata={"liveness_timeout": 0}))
        is None
    )
    # Positive override is used verbatim.
    assert (
        _resolve_stream_inactivity_timeout(Role(**base, metadata={"liveness_timeout": 1800}))
        == 1800.0
    )
    # Garbage / negative → env default.
    assert (
        _resolve_stream_inactivity_timeout(Role(**base, metadata={"liveness_timeout": "nope"}))
        == 600.0
    )
    assert (
        _resolve_stream_inactivity_timeout(Role(**base, metadata={"liveness_timeout": -5}))
        == 600.0
    )


def test_stream_inactivity_timeout_rejects_non_finite_env(monkeypatch):
    # A typo'd 'inf' must NOT silently disable the watchdog.
    monkeypatch.setenv("IRIAI_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S", "inf")
    assert _stream_inactivity_timeout_s() == 600.0
    monkeypatch.setenv("IRIAI_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S", "0")
    assert _stream_inactivity_timeout_s() == 600.0
    monkeypatch.setenv("IRIAI_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S", "abc")
    assert _stream_inactivity_timeout_s() == 600.0
    monkeypatch.setenv("IRIAI_CLAUDE_STREAM_INACTIVITY_TIMEOUT_S", "45")
    assert _stream_inactivity_timeout_s() == 45.0


# ── Structured-output dispatches must disable extended thinking ──
# The bundled CLI's StructuredOutput Stop-hook fights an unmodifiable
# extended-thinking block, looping forever on 400 invalid_request_error. Disabling
# thinking (→ --max-thinking-tokens 0) for structured-output dispatches removes the
# thinking block the forced continuation cannot carry.


class _StructuredOut(BaseModel):
    value: int


def test_build_options_keeps_thinking_for_structured_output_by_default(monkeypatch):
    # Structured output must NOT disable extended thinking — doing so would
    # compromise reasoning fidelity for every RCA/verdict dispatch. Structured
    # output + thinking is the working path (it carried sealed groups 0..77).
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="rca", prompt="Find the bug", tools=["Read"])

    options = runtime._build_options(role, workspace=None, output_type=_StructuredOut)

    assert getattr(options, "thinking", None) is None
    assert options.output_format["type"] == "json_schema"
    assert options.effort == "high"


def test_build_options_disables_thinking_only_as_scoped_fallback(monkeypatch):
    # The ONLY path that disables thinking is the explicit last-resort fallback
    # after a confirmed provider-error storm (invoke passes disable_thinking=True).
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="rca", prompt="Find the bug", tools=["Read"])

    options = runtime._build_options(
        role, workspace=None, output_type=_StructuredOut, disable_thinking=True
    )

    assert options.thinking == {"type": "disabled"}
    # Effort is a separate CLI flag and must be untouched even in the fallback.
    assert options.effort == "high"


def test_build_options_keeps_thinking_enabled_without_structured_output(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=_FakeClaudeAgentOptions),
    )
    runtime = object.__new__(ClaudeAgentRuntime)
    role = Role(name="rca", prompt="Find the bug", tools=["Read"])

    options = runtime._build_options(role, workspace=None)

    # Free-form dispatches keep the default (thinking unset → CLI default on).
    assert getattr(options, "thinking", None) is None


# ── A fast provider-error/retry loop must be abandoned ──
# It streams a message every few hundred ms, so it keeps resetting the inactivity
# deadline and the time-based watchdog alone can never catch it.


class _ProviderErrorStormClient(_BaseFakeClient):
    """receive_response() yields provider-error assistant messages forever —
    models the StructuredOutput Stop-hook vs unmodifiable-thinking-block 400 loop
    that the time-based watchdog cannot catch because each message resets it."""

    def receive_response(self):
        from claude_agent_sdk.types import AssistantMessage

        async def _gen():
            while True:
                yield AssistantMessage(content=[], model="m", error="invalid_request")

        return _gen()


class _TextProviderErrorStormClient(_BaseFakeClient):
    """Same loop, but the error arrives as text the bundled CLI prints rather than
    the SDK's structured ``error`` field."""

    def receive_response(self):
        from claude_agent_sdk.types import AssistantMessage, TextBlock

        async def _gen():
            while True:
                yield AssistantMessage(
                    content=[
                        TextBlock(
                            text='API Error: 400 {"type":"invalid_request_error",'
                            '"message":"thinking blocks cannot be modified"}'
                        )
                    ],
                    model="m",
                )

        return _gen()


class _RecoveringErrorClient(_BaseFakeClient):
    """A few provider errors, each followed by a genuine progress message that
    resets the counter, then a ResultMessage — a transient blip the model recovers
    from, which must NOT be abandoned."""

    def __init__(self, result_msg):
        self._result = result_msg

    def receive_response(self):
        from claude_agent_sdk.types import AssistantMessage, TextBlock

        async def _gen():
            for _ in range(_API_ERROR_STORM_THRESHOLD + 3):
                yield AssistantMessage(content=[], model="m", error="invalid_request")
                yield AssistantMessage(
                    content=[TextBlock(text="continuing the investigation")],
                    model="m",
                )
            yield self._result

        return _gen()


@pytest.mark.asyncio
async def test_dispatch_aborts_on_provider_error_storm():
    runtime = _bare_runtime()
    start = time.monotonic()
    # Large inactivity timeout so the TIME-based watchdog cannot be what fires —
    # the storm detector must catch it on message content instead.
    with pytest.raises(ClaudeApiErrorStorm):
        await runtime._run_dispatch_bounded(
            lambda: _ProviderErrorStormClient(), "p", _WatchdogResultMessage, 30.0
        )
    assert time.monotonic() - start < 5.0


@pytest.mark.asyncio
async def test_dispatch_aborts_on_text_provider_error_storm():
    runtime = _bare_runtime()
    with pytest.raises(ClaudeApiErrorStorm):
        await runtime._run_dispatch_bounded(
            lambda: _TextProviderErrorStormClient(), "p", _WatchdogResultMessage, 30.0
        )


@pytest.mark.asyncio
async def test_dispatch_does_not_abort_when_errors_interleaved_with_progress():
    runtime = _bare_runtime()
    result = _WatchdogResultMessage("done")
    out = await runtime._run_dispatch_bounded(
        lambda: _RecoveringErrorClient(result), "p", _WatchdogResultMessage, 30.0
    )
    assert out is result


def test_provider_error_storm_classifies_as_watchdog_stall():
    exc = ClaudeApiErrorStorm(
        "Claude CLI dispatch watchdog: provider error storm — produced no usable output"
    )
    # Subclass of the time-based stall, classified into the same infra-retryable
    # terminal reason so the dispatch self-recovers on retry.
    assert isinstance(exc, ClaudeStreamWatchdogStall)
    assert _classify_exception(exc) == "watchdog_stall"


@pytest.mark.asyncio
async def test_invoke_disables_thinking_only_after_a_storm(monkeypatch):
    # Quality contract: extended thinking stays ON for the normal dispatch. Only
    # when that dispatch hits a CONFIRMED provider-error storm does invoke retry
    # THIS one dispatch with thinking disabled — a scoped fallback, never a
    # global reasoning-quality cut.
    runtime = object.__new__(ClaudeAgentRuntime)
    runtime._interactive_roles = set()
    runtime._session_messages = {}
    runtime._session_sizes = {}
    runtime._session_context = {}
    runtime.session_store = None
    runtime._active_invocations = {}
    runtime._invocation_activity = {}

    role = Role(name="rca", prompt="Find the bug", tools=["Read"], metadata={})

    build_thinking_flags: list[bool] = []

    def fake_build_options(_role, _workspace, output_type=None, *, disable_thinking=False):
        build_thinking_flags.append(disable_thinking)
        return _FakeClaudeAgentOptions(
            output_format={"type": "json_schema"} if output_type else None
        )

    monkeypatch.setattr(runtime, "_build_options", fake_build_options)

    class _Result:
        result = "done"
        subtype = "success"
        session_id = None
        structured_output = {"value": 7}

    dispatches = {"n": 0}

    async def fake_invoke_default(_opts, _prompt, _ResultMessage, _timeout):
        dispatches["n"] += 1
        if dispatches["n"] == 1:
            raise ClaudeApiErrorStorm("provider error storm; produced no output; watchdog")
        return _Result()

    monkeypatch.setattr(runtime, "_invoke_default", fake_invoke_default)

    out = await runtime.invoke(role, "diagnose", output_type=_StructuredOut)

    assert isinstance(out, _StructuredOut) and out.value == 7
    # First attempt thinking ON; only the post-storm fallback turns it OFF.
    assert build_thinking_flags == [False, True]
    assert dispatches["n"] == 2


@pytest.mark.asyncio
async def test_invoke_falls_back_to_no_thinking_on_structured_output_exhaustion(monkeypatch):
    # Same scoped fallback, but triggered by a CLEAN exhaustion
    # (subtype=error_max_structured_output_retries, no result) rather than a 400
    # storm — thinking-on still could not emit structured output, so retry once
    # with thinking disabled instead of failing the dispatch terminally.
    runtime = object.__new__(ClaudeAgentRuntime)
    runtime._interactive_roles = set()
    runtime._session_messages = {}
    runtime._session_sizes = {}
    runtime._session_context = {}
    runtime.session_store = None
    runtime._active_invocations = {}
    runtime._invocation_activity = {}

    role = Role(name="rca", prompt="Find the bug", tools=["Read"], metadata={})

    build_thinking_flags: list[bool] = []

    def fake_build_options(_role, _workspace, output_type=None, *, disable_thinking=False):
        build_thinking_flags.append(disable_thinking)
        return _FakeClaudeAgentOptions(
            output_format={"type": "json_schema"} if output_type else None
        )

    monkeypatch.setattr(runtime, "_build_options", fake_build_options)

    class _Exhausted:
        result = None
        subtype = "error_max_structured_output_retries"
        session_id = None
        structured_output = None

    class _Good:
        result = "done"
        subtype = "success"
        session_id = None
        structured_output = {"value": 9}

    dispatches = {"n": 0}

    async def fake_invoke_default(_opts, _prompt, _ResultMessage, _timeout):
        dispatches["n"] += 1
        return _Exhausted() if dispatches["n"] == 1 else _Good()

    monkeypatch.setattr(runtime, "_invoke_default", fake_invoke_default)

    out = await runtime.invoke(role, "diagnose", output_type=_StructuredOut)

    assert isinstance(out, _StructuredOut) and out.value == 9
    assert build_thinking_flags == [False, True]
    assert dispatches["n"] == 2


class _DeadCliClient(_BaseFakeClient):
    """receive_response() never ends (orphaned stream) while exposing a CLI
    subprocess pid that has already exited and been reaped — models the macOS
    ThreadedChildWatcher orphan: the loop sits idle awaiting a closed stream that
    will never yield. The watchdog must abandon it fast on the dead pid, not wait
    out the full inactivity deadline."""

    def __init__(self, dead_pid: int):
        self._transport = SimpleNamespace(_process=SimpleNamespace(pid=dead_pid))

    def receive_response(self):
        async def _gen():
            await asyncio.sleep(3600)
            yield None  # pragma: no cover - never reached

        return _gen()


@pytest.mark.asyncio
async def test_dispatch_abandons_fast_when_cli_pid_already_exited(monkeypatch):
    # The recurring bridge hang: the CLI exits but receive_response never ends.
    # The dead-pid fast-path must abandon in ~seconds, NOT wait out the (here
    # large) inactivity deadline.
    monkeypatch.setattr("iriai_build_v2.runtimes.claude._WATCHDOG_POLL_SECONDS", 0.02)
    monkeypatch.setattr("iriai_build_v2.runtimes.claude._CLI_EXIT_GRACE_SECONDS", 0.04)

    proc = subprocess.Popen(["true"])
    proc.wait()  # reaped → its pid is now dead (os.kill -> ProcessLookupError)
    dead_pid = proc.pid

    runtime = _bare_runtime()
    start = time.monotonic()
    with pytest.raises(ClaudeStreamWatchdogStall):
        # 30s inactivity timeout: the inactivity path must NOT be what fires.
        await runtime._run_dispatch_bounded(
            lambda: _DeadCliClient(dead_pid), "p", _WatchdogResultMessage, 30.0
        )
    assert time.monotonic() - start < 5.0


class _TeardownHangClient(_BaseFakeClient):
    """receive_response() ends immediately — so _run's inner finally runs and
    NULLS connected_client — then __aexit__ (SDK teardown) hangs while exposing an
    already-exited pid. Models the observed teardown orphan: the dead-pid check
    must fire off the RETAINED cli_pid, since connected_client is already None."""

    def __init__(self, dead_pid: int):
        self._transport = SimpleNamespace(_process=SimpleNamespace(pid=dead_pid))

    def receive_response(self):
        async def _gen():
            if False:  # pragma: no cover - empty async generator (ends at once)
                yield None

        return _gen()

    async def __aexit__(self, *exc):
        await asyncio.sleep(3600)  # teardown wedge
        return False


@pytest.mark.asyncio
async def test_dispatch_abandons_fast_on_teardown_orphan(monkeypatch):
    # The wedge that slipped past the first fix: the receive loop ENDS (so
    # connected_client is nulled) but the SDK's async-with teardown hangs. The
    # dead-pid fast-path must still fire using the retained cli_pid.
    monkeypatch.setattr("iriai_build_v2.runtimes.claude._WATCHDOG_POLL_SECONDS", 0.02)
    monkeypatch.setattr("iriai_build_v2.runtimes.claude._CLI_EXIT_GRACE_SECONDS", 0.04)

    proc = subprocess.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid

    runtime = _bare_runtime()
    start = time.monotonic()
    with pytest.raises(ClaudeStreamWatchdogStall):
        await runtime._run_dispatch_bounded(
            lambda: _TeardownHangClient(dead_pid), "p", _WatchdogResultMessage, 30.0
        )
    assert time.monotonic() - start < 5.0
