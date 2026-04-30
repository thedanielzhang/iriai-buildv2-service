from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iriai_compose.actors import Role
from iriai_compose.storage import AgentSession

from iriai_build_v2.runtimes.claude_pool import (
    ClaudePoolProfile,
    ClaudePoolRunner,
    ClaudePoolRuntime,
    _apply_runner_umask,
    _coerce_profile,
    _job_state_path,
    _payload_dir,
    _write_json_atomic,
    load_profiles,
)


class _SimpleOutput(BaseModel):
    message: str


class _MemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, AgentSession] = {}
        self.deleted: list[str] = []

    async def load(self, session_key: str) -> AgentSession | None:
        return self.sessions.get(session_key)

    async def save(self, session: AgentSession) -> None:
        self.sessions[session.session_key] = session

    async def delete(self, session_key: str) -> None:
        self.deleted.append(session_key)
        self.sessions.pop(session_key, None)


class _FakeClaudeRunner(ClaudePoolRunner):
    async def _execute_claude(self, manifest: dict) -> None:
        paths = manifest["paths"]
        Path(paths["stdout"]).write_text('{"result": "{\\"message\\": \\"ok\\"}"}', encoding="utf-8")
        Path(paths["stderr"]).write_text("", encoding="utf-8")
        self._write_result(
            manifest,
            {
                "ok": True,
                "kind": "claude",
                "result_text": '{"message": "ok"}',
                "structured_output": {"message": "ok"},
                "raw": {"result": '{"message": "ok"}'},
            },
        )


def _profiles() -> list[ClaudePoolProfile]:
    return [
        ClaudePoolProfile(name="iriai-claude-1", user="iriai-claude-1", claude_command="/bin/echo"),
        ClaudePoolProfile(name="iriai-claude-2", user="iriai-claude-2", claude_command="/bin/echo"),
        ClaudePoolProfile(name="iriai-claude-3", user="iriai-claude-3", claude_command="/bin/echo"),
    ]


def _weighted_profiles() -> list[ClaudePoolProfile]:
    return [
        ClaudePoolProfile(
            name="iriai-claude-1",
            user="iriai-claude-1",
            claude_command="/bin/echo",
            weight=5,
        ),
        ClaudePoolProfile(
            name="iriai-claude-2",
            user="iriai-claude-2",
            claude_command="/bin/echo",
            weight=1,
        ),
        ClaudePoolProfile(
            name="iriai-claude-3",
            user="iriai-claude-3",
            claude_command="/bin/echo",
            weight=9,
        ),
    ]


@pytest.mark.asyncio
async def test_round_robin_assigns_ephemeral_jobs_evenly(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())

    picked = [
        (await runtime._select_profile(session_key=f"actor-{idx}:feat", persistent=False)).name
        for idx in range(6)
    ]

    assert picked == [
        "iriai-claude-1",
        "iriai-claude-2",
        "iriai-claude-3",
        "iriai-claude-1",
        "iriai-claude-2",
        "iriai-claude-3",
    ]


@pytest.mark.asyncio
async def test_weighted_selection_sends_most_work_to_profile_three(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_weighted_profiles())

    picked = [
        (await runtime._select_profile(session_key=f"actor-{idx}:feat", persistent=False)).name
        for idx in range(15)
    ]

    assert picked.count("iriai-claude-1") == 5
    assert picked.count("iriai-claude-2") == 1
    assert picked.count("iriai-claude-3") == 9


def test_known_profiles_load_default_capacity_weights():
    assert _coerce_profile({"name": "iriai-claude-1"}).weight == 5
    assert _coerce_profile({"name": "iriai-claude-2"}).weight == 1
    assert _coerce_profile({"name": "iriai-claude-3"}).weight == 9


def test_load_profiles_migrates_legacy_default_capacity_weights(tmp_path: Path):
    _write_json_atomic(
        tmp_path / "profiles.json",
        {
            "profiles": [
                {"name": "iriai-claude-1", "user": "iriai-claude-1", "weight": 5},
                {"name": "iriai-claude-2", "user": "iriai-claude-2", "weight": 1},
                {"name": "iriai-claude-3", "user": "iriai-claude-3", "weight": 12},
            ]
        },
    )

    profiles = load_profiles(tmp_path)
    weights = {profile.name: profile.weight for profile in profiles}

    assert weights == {
        "iriai-claude-1": 5,
        "iriai-claude-2": 1,
        "iriai-claude-3": 9,
    }
    stored = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    stored_weights = {
        profile["name"]: profile["weight"]
        for profile in stored["profiles"]
    }
    assert stored_weights["iriai-claude-3"] == 9


@pytest.mark.asyncio
async def test_session_affinity_keeps_persistent_session_on_same_profile(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())

    first = await runtime._select_profile(session_key="pm:feat-1", persistent=True)
    second = await runtime._select_profile(session_key="pm:feat-1", persistent=True)
    third = await runtime._select_profile(session_key="architect:feat-1", persistent=True)

    assert first.name == second.name
    assert third.name == "iriai-claude-2"


@pytest.mark.asyncio
async def test_select_profile_skips_session_limited_profile_until_probe_due(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    future = datetime.now(UTC) + timedelta(minutes=5)
    _write_json_atomic(
        tmp_path / "profile_state.json",
        {
            "profiles": {
                "iriai-claude-1": {
                    "status": "unavailable",
                    "reason": "usage_limited",
                    "probe_after": future.isoformat(),
                }
            }
        },
    )

    picked = await runtime._select_profile(session_key="actor:feat", persistent=False)

    assert picked.name == "iriai-claude-2"


@pytest.mark.asyncio
async def test_select_profile_probes_and_reuses_recovered_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    past = datetime.now(UTC) - timedelta(seconds=1)
    _write_json_atomic(
        tmp_path / "profile_state.json",
        {
            "profiles": {
                "iriai-claude-1": {
                    "status": "unavailable",
                    "reason": "usage_limited",
                    "probe_after": past.isoformat(),
                }
            }
        },
    )
    probed: list[str] = []

    async def _fake_probe(**kwargs):
        probed.append(kwargs["profile"].name)
        return {"ok": True}

    monkeypatch.setattr(
        "iriai_build_v2.runtimes.claude_pool.submit_availability_check",
        _fake_probe,
    )

    picked = await runtime._select_profile(session_key="actor:feat", persistent=False)

    assert picked.name == "iriai-claude-1"
    assert probed == ["iriai-claude-1"]
    assert (json.loads((tmp_path / "profile_state.json").read_text())["profiles"]) == {}


def test_mark_profile_failure_uses_short_probe_window_not_fixed_long_cooldown(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())

    runtime._mark_profile_failure(
        _profiles()[1],
        "You've hit your org's monthly usage limit; resets soon",
    )

    state = json.loads((tmp_path / "profile_state.json").read_text())
    record = state["profiles"]["iriai-claude-2"]
    probe_after = datetime.fromisoformat(record["probe_after"])
    delay = (probe_after - datetime.now(UTC)).total_seconds()
    assert record["status"] == "unavailable"
    assert record["reason"] == "usage_limited"
    assert "cooldown_until" not in record
    assert 0 < delay <= 120


@pytest.mark.asyncio
async def test_invoke_fails_over_when_profile_hits_usage_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    role = Role(name="implementer", prompt="Say ok.", metadata={})
    calls: list[str] = []

    async def _fake_submit_and_wait(*args, **kwargs):
        profile = kwargs["profile"]
        calls.append(profile.name)
        if len(calls) == 1:
            raise RuntimeError("You've hit your org's monthly usage limit")
        return ("ok", None, {})

    monkeypatch.setattr(runtime, "_submit_and_wait", _fake_submit_and_wait)

    result = await runtime.invoke(
        role,
        "Say ok.",
        workspace=SimpleNamespace(path=tmp_path),
        session_key="implementer:feat-1",
    )

    assert result == "ok"
    assert calls == ["iriai-claude-1", "iriai-claude-2"]
    state = json.loads((tmp_path / "profile_state.json").read_text())
    assert state["profiles"]["iriai-claude-1"]["reason"] == "usage_limited"


@pytest.mark.asyncio
async def test_select_profile_prefers_lower_recent_usage(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    job_id = "costly-profile-one"
    payload_dir = _payload_dir(tmp_path, job_id)
    payload_dir.mkdir(parents=True)
    result_path = payload_dir / "result.json"
    _write_json_atomic(
        result_path,
        {
            "ok": True,
            "raw": {
                "total_cost_usd": 12.0,
                "usage": {
                    "input_tokens": 10_000,
                    "output_tokens": 2_000,
                },
            },
        },
    )
    _write_json_atomic(
        _job_state_path(tmp_path, "done", "iriai-claude-1", job_id),
        {"id": job_id, "paths": {"result": str(result_path)}},
    )

    picked = await runtime._select_profile(session_key="actor:feat", persistent=False)

    assert picked.name == "iriai-claude-2"


@pytest.mark.asyncio
async def test_invoke_validates_structured_output_from_runner(tmp_path: Path):
    store = _MemorySessionStore()
    runtime = ClaudePoolRuntime(
        root=tmp_path,
        profiles=_profiles(),
        session_store=store,
        poll_interval=0.01,
    )
    role = Role(name="implementer", prompt="Return JSON.", metadata={"max_session_chars": 1000})

    invoke_task = asyncio.create_task(
        runtime.invoke(
            role,
            "Say ok.",
            output_type=_SimpleOutput,
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
        )
    )

    for _ in range(100):
        queued = list((tmp_path / "jobs" / "queued" / "iriai-claude-1").glob("*.json"))
        if queued:
            break
        await asyncio.sleep(0.01)

    runner = _FakeClaudeRunner(profile="iriai-claude-1", root=tmp_path, heartbeat_interval=0.01)
    await runner.run_once(wait=True)

    result = await invoke_task

    assert result == _SimpleOutput(message="ok")
    assert store.sessions["implementer:feat-1"].metadata["turns"][-1]["text"] == '{"message": "ok"}'


def test_atomic_claim_prevents_duplicate_execution(tmp_path: Path):
    profiles = _profiles()
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=profiles)
    del runtime
    job_id = "abc123"
    payload_dir = _payload_dir(tmp_path, job_id)
    payload_dir.mkdir(parents=True)
    manifest = {
        "id": job_id,
        "kind": "claude",
        "profile": "iriai-claude-1",
        "paths": {"result": str(payload_dir / "result.json")},
    }
    queued_path = _job_state_path(tmp_path, "queued", "iriai-claude-1", job_id)
    _write_json_atomic(queued_path, manifest)

    runner_a = _FakeClaudeRunner(profile="iriai-claude-1", root=tmp_path)
    runner_b = _FakeClaudeRunner(profile="iriai-claude-1", root=tmp_path)

    assert runner_a._claim(queued_path) is not None
    assert runner_b._claim(queued_path) is None


def test_invocation_liveness_uses_running_job_heartbeat(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), heartbeat_timeout=30)
    job_id = "abc123"
    running_path = _job_state_path(tmp_path, "running", "iriai-claude-1", job_id)
    _write_json_atomic(
        running_path,
        {
            "id": job_id,
            "status": "running",
            "heartbeat_at": "2020-01-01T00:00:00+00:00",
        },
    )
    runtime._invocation_jobs["inv-1"] = {job_id}

    assert runtime.invocation_has_live_work("inv-1") is False


def test_runner_builds_claude_cli_command_shape(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    del runtime
    runner = _FakeClaudeRunner(profile="iriai-claude-1", root=tmp_path)
    manifest = {
        "role": {
            "model": "claude-sonnet-4-6",
            "effort": "high",
            "tools": ["Read", "Edit"],
        },
        "claude": {
            "command": "/opt/homebrew/bin/claude",
            "permission_mode": "bypassPermissions",
            "add_dirs": ["~/.npm"],
        },
    }

    command = runner._build_claude_command(
        manifest,
        system_prompt="You are a test role.",
        schema={"type": "object", "properties": {"message": {"type": "string"}}},
    )

    assert command[:2] == ["/opt/homebrew/bin/claude", "-p"]
    assert "--input-format" in command
    assert "--output-format" in command
    assert "--json-schema" in command
    assert "--system-prompt" in command
    assert "--model" in command
    assert "--effort" in command
    assert "--permission-mode" in command
    assert "--allowedTools" in command
    assert "--add-dir" in command
    assert "--no-session-persistence" in command


def test_runner_umask_is_group_writable(monkeypatch: pytest.MonkeyPatch):
    calls: list[int] = []

    def _fake_umask(value: int) -> int:
        calls.append(value)
        return 0o022

    monkeypatch.setattr("iriai_build_v2.runtimes.claude_pool.os.umask", _fake_umask)

    applied = _apply_runner_umask("0002")

    assert applied == 0o002
    assert calls == [0o002]


@pytest.mark.asyncio
async def test_fake_load_completes_one_thousand_jobs_without_single_flat_queue(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    del runtime
    counts = {"iriai-claude-1": 0, "iriai-claude-2": 0, "iriai-claude-3": 0}

    for idx in range(1000):
        profile = _profiles()[idx % 3].name
        counts[profile] += 1
        job_id = f"{idx:04x}{idx:04x}"
        payload_dir = _payload_dir(tmp_path, job_id)
        payload_dir.mkdir(parents=True)
        for name in ("prompt.md", "system_prompt.md"):
            (payload_dir / name).write_text("test", encoding="utf-8")
        (payload_dir / "schema.json").write_text(
            json.dumps(_SimpleOutput.model_json_schema()),
            encoding="utf-8",
        )
        manifest = {
            "id": job_id,
            "kind": "claude",
            "profile": profile,
            "status": "queued",
            "cwd": str(tmp_path),
            "role": {"name": "fake", "model": "sonnet", "effort": "low", "tools": []},
            "paths": {
                "prompt": str(payload_dir / "prompt.md"),
                "system_prompt": str(payload_dir / "system_prompt.md"),
                "schema": str(payload_dir / "schema.json"),
                "result": str(payload_dir / "result.json"),
                "stdout": str(payload_dir / "stdout.json"),
                "stderr": str(payload_dir / "stderr.log"),
            },
        }
        _write_json_atomic(_job_state_path(tmp_path, "queued", profile, job_id), manifest)

    runners = [
        _FakeClaudeRunner(profile=profile.name, root=tmp_path, heartbeat_interval=0.01)
        for profile in _profiles()
    ]
    await asyncio.gather(*(runner.run_once(wait=True) for runner in runners))

    completed = 0
    for profile, expected in counts.items():
        done = list((tmp_path / "jobs" / "done" / profile).glob("*.json"))
        completed += len(done)
        assert len(done) == expected
    assert completed == 1000
    assert len(list((tmp_path / "payloads").iterdir())) > 1
