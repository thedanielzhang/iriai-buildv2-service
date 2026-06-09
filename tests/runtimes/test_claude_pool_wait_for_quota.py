"""Wait-for-quota-recovery behavior of ClaudePoolRuntime.

When every pool member is unavailable (e.g. all accounts hit their usage
window at once), the runtime must wait for a member to recover — bounded by
``IRIAI_CLAUDE_POOL_USAGE_WAIT_MAX_SECONDS`` — instead of raising and
crashing the whole workflow. With the wait disabled (``0``) the previous
fail-fast behavior must be preserved exactly.

No live DB and no real Claude CLI: ``_submit_and_wait`` / ``_select_profile``
are monkeypatched, ``asyncio.sleep`` is faked, and time is controlled via the
module-level ``_now()`` seam.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_compose.actors import Role

import iriai_build_v2.runtimes.claude_pool as claude_pool
from iriai_build_v2.runtimes.claude_pool import (
    ClaudePoolProfile,
    ClaudePoolRuntime,
    _write_json_atomic,
)

NO_PROFILE_ERROR = (
    "No Claude pool profile is currently available; "
    "next readiness probe after 2026-06-09T00:00:00+00:00"
)


def _profiles() -> list[ClaudePoolProfile]:
    return [
        ClaudePoolProfile(
            name="iriai-claude-1", user="iriai-claude-1", claude_command="/bin/echo"
        ),
        ClaudePoolProfile(
            name="iriai-claude-2", user="iriai-claude-2", claude_command="/bin/echo"
        ),
    ]


def _write_profile_state(
    root: Path,
    names: list[str],
    *,
    reason: str = "usage_limited",
    probe_after: datetime,
) -> None:
    _write_json_atomic(
        root / "profile_state.json",
        {
            "profiles": {
                name: {
                    "status": "unavailable",
                    "reason": reason,
                    "probe_after": probe_after.isoformat(),
                }
                for name in names
            }
        },
    )


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace asyncio.sleep with an instant fake; returns recorded durations."""
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(claude_pool.asyncio, "sleep", _fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_wait_disabled_all_unavailable_raises_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Env 0 -> today's fail-fast behavior, byte-identical message, no sleep."""
    monkeypatch.setattr(claude_pool, "DEFAULT_USAGE_WAIT_MAX_SECONDS", 0.0)
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    sleeps = _patch_sleep(monkeypatch)

    async def _select_always_exhausted(**kwargs):
        raise RuntimeError(NO_PROFILE_ERROR)

    monkeypatch.setattr(runtime, "_select_profile", _select_always_exhausted)
    role = Role(name="implementer", prompt="Say ok.", metadata={})

    with pytest.raises(RuntimeError) as excinfo:
        await runtime.invoke(
            role,
            "Say ok.",
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
        )

    assert str(excinfo.value) == NO_PROFILE_ERROR
    assert sleeps == []


@pytest.mark.asyncio
async def test_wait_disabled_failover_exhaustion_message_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Env 0: mid-job exhaustion keeps 'Claude pool exhausted after ... on ...'."""
    monkeypatch.setattr(claude_pool, "DEFAULT_USAGE_WAIT_MAX_SECONDS", 0.0)
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    sleeps = _patch_sleep(monkeypatch)
    select_calls = {"n": 0}

    async def _fake_select(**kwargs):
        select_calls["n"] += 1
        if select_calls["n"] == 1:
            return runtime.profiles[0]
        raise RuntimeError(NO_PROFILE_ERROR)

    async def _fake_submit_and_wait(*args, **kwargs):
        raise RuntimeError("You've hit your org's monthly usage limit")

    monkeypatch.setattr(runtime, "_select_profile", _fake_select)
    monkeypatch.setattr(runtime, "_submit_and_wait", _fake_submit_and_wait)
    role = Role(name="implementer", prompt="Say ok.", metadata={})

    with pytest.raises(RuntimeError) as excinfo:
        await runtime.invoke(
            role,
            "Say ok.",
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
        )

    assert str(excinfo.value) == (
        f"Claude pool exhausted after usage_limited on iriai-claude-1: {NO_PROFILE_ERROR}"
    )
    assert sleeps == []


@pytest.mark.asyncio
async def test_wait_enabled_initial_selection_waits_then_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """All-unavailable on first selection -> one wait iteration -> dispatch."""
    monkeypatch.setattr(claude_pool, "DEFAULT_USAGE_WAIT_MAX_SECONDS", 7200.0)
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    sleeps = _patch_sleep(monkeypatch)
    # usage_limited with probe_after in the past -> sleep clamps to the 15s floor.
    _write_profile_state(
        tmp_path,
        ["iriai-claude-1", "iriai-claude-2"],
        reason="usage_limited",
        probe_after=datetime.now(UTC) - timedelta(seconds=5),
    )
    select_calls = {"n": 0}

    async def _fake_select(**kwargs):
        select_calls["n"] += 1
        if select_calls["n"] == 1:
            raise RuntimeError(NO_PROFILE_ERROR)
        return runtime.profiles[0]

    async def _fake_submit_and_wait(*args, **kwargs):
        return ("ok", None, {})

    monkeypatch.setattr(runtime, "_select_profile", _fake_select)
    monkeypatch.setattr(runtime, "_submit_and_wait", _fake_submit_and_wait)
    role = Role(name="implementer", prompt="Say ok.", metadata={})

    with caplog.at_level(logging.INFO, logger="iriai_build_v2.runtimes.claude_pool"):
        result = await runtime.invoke(
            role,
            "Say ok.",
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
        )

    assert result == "ok"
    assert select_calls["n"] == 2
    assert sleeps == [15.0]
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "ALL members unavailable" in record.getMessage()
    ]
    assert warnings, "expected the loud all-unavailable WARNING line"
    assert "waiting for quota/availability reset" in warnings[0].getMessage()
    recovered = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO
        and "recovered after" in record.getMessage()
        and "resuming dispatch" in record.getMessage()
    ]
    assert recovered, "expected the recovery INFO line"
    assert "iriai-claude-1" in recovered[0].getMessage()


@pytest.mark.asyncio
async def test_midjob_failover_waits_and_reattempts_recovered_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """usage_limited on A, then B, then all-unavailable, then B recovers.

    B was already in the ``attempted`` set; the wait must clear it so the
    recovered member is re-attempted with the same role+prompt.
    """
    monkeypatch.setattr(claude_pool, "DEFAULT_USAGE_WAIT_MAX_SECONDS", 7200.0)
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    sleeps = _patch_sleep(monkeypatch)
    submit_calls: list[tuple[str, str]] = []
    select_calls = {"n": 0}

    async def _fake_select(**kwargs):
        select_calls["n"] += 1
        if select_calls["n"] == 1:
            return runtime.profiles[0]  # initial pick: A
        if select_calls["n"] == 2:
            return runtime.profiles[1]  # failover pick: B
        if select_calls["n"] == 3:
            raise RuntimeError(NO_PROFILE_ERROR)  # both limited
        # B's usage window reset while we were waiting: clear its record so
        # the top-of-loop unavailability check sees it as healthy again.
        _write_json_atomic(tmp_path / "profile_state.json", {"profiles": {}})
        return runtime.profiles[1]

    async def _fake_submit_and_wait(role, prompt, **kwargs):
        profile = kwargs["profile"]
        submit_calls.append((profile.name, prompt))
        if len(submit_calls) <= 2:
            raise RuntimeError("You've hit your org's monthly usage limit")
        return ("ok", None, {})

    monkeypatch.setattr(runtime, "_select_profile", _fake_select)
    monkeypatch.setattr(runtime, "_submit_and_wait", _fake_submit_and_wait)
    role = Role(name="implementer", prompt="Say ok.", metadata={})

    result = await runtime.invoke(
        role,
        "Say ok.",
        workspace=SimpleNamespace(path=tmp_path),
        session_key="implementer:feat-1",
    )

    assert result == "ok"
    assert [name for name, _prompt in submit_calls] == [
        "iriai-claude-1",
        "iriai-claude-2",
        "iriai-claude-2",
    ]
    # Same prompt re-submitted on every attempt.
    assert len({prompt for _name, prompt in submit_calls}) == 1
    assert len(sleeps) == 1


@pytest.mark.asyncio
async def test_wait_deadline_exceeded_raises_loud_exhausted_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """No recovery before the deadline -> loud error including the wait time."""
    monkeypatch.setattr(claude_pool, "DEFAULT_USAGE_WAIT_MAX_SECONDS", 100.0)
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    start = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
    clock = {"now": start}
    monkeypatch.setattr(claude_pool, "_now", lambda: clock["now"])
    _write_profile_state(
        tmp_path,
        ["iriai-claude-1", "iriai-claude-2"],
        reason="usage_limited",
        probe_after=start + timedelta(seconds=60),
    )
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    monkeypatch.setattr(claude_pool.asyncio, "sleep", _fake_sleep)

    async def _select_always_exhausted(**kwargs):
        raise RuntimeError(NO_PROFILE_ERROR)

    monkeypatch.setattr(runtime, "_select_profile", _select_always_exhausted)
    role = Role(name="implementer", prompt="Say ok.", metadata={})

    with caplog.at_level(
        logging.WARNING, logger="iriai_build_v2.runtimes.claude_pool"
    ), pytest.raises(RuntimeError) as excinfo:
        await runtime.invoke(
            role,
            "Say ok.",
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
        )

    message = str(excinfo.value)
    assert message.startswith("Claude pool exhausted: all members unavailable")
    # Sleeps: 60 (until probe_after), then 15s-floor steps: 60+15+15+15 = 105.
    assert sleeps == [60.0, 15.0, 15.0, 15.0]
    assert "waiting 105s" in message
    assert "IRIAI_CLAUDE_POOL_USAGE_WAIT_MAX_SECONDS=100" in message
    assert NO_PROFILE_ERROR in message
    warnings = [
        record
        for record in caplog.records
        if "ALL members unavailable" in record.getMessage()
    ]
    assert len(warnings) == len(sleeps)
