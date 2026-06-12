from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iriai_compose.actors import Role
from iriai_compose.storage import AgentSession

from iriai_build_v2.runtimes.claude_pool import (
    DEFAULT_ACTIVE_JOB_SPREAD_PENALTY,
    DEFAULT_RUNNER_MAX_ACTIVE,
    ClaudePoolProfile,
    ClaudePoolRunner,
    ClaudePoolRuntime,
    _apply_runner_umask,
    _bound_write_authorization,
    _classify_claude_pool_error,
    _coerce_profile,
    _job_state_path,
    _pool_write_auth_secret,
    _payload_dir,
    _profile_runtime_scratch_roots,
    _stable_json_digest,
    _write_sandbox_exec_profile,
    _write_json_atomic,
    find_late_completed_claude_pool_job,
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
            weight=1,
        ),
        ClaudePoolProfile(
            name="iriai-claude-2",
            user="iriai-claude-2",
            claude_command="/bin/echo",
            weight=9,
        ),
    ]


def _write_sandbox_manifest(
    sandbox_root: Path,
    cwd: Path,
    *,
    sandbox_id: str = "sandbox-04",
    writable_roots: list[Path] | None = None,
    blocked_roots: list[Path] | None = None,
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
                "blocked_roots": [str(path) for path in (blocked_roots or [])],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _authority_grant(
    repo_root: Path,
    *,
    grant_type: str = "product",
    write_guard_roots: list[Path] | None = None,
    contract_roots: list[Path] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "runtime-workspace-authority-grant-v1",
        "feature_id": "feat-1",
        "group_idx": 1,
        "lane_id": "test-lane",
        "grant_type": grant_type,
        "repo_id": "app",
        "repo_root": str(repo_root),
        "contract_roots": [
            str(path) for path in (contract_roots if contract_roots is not None else [repo_root])
        ],
        "create_parent_roots": [],
        "write_guard_roots": [
            str(path) for path in (write_guard_roots if write_guard_roots is not None else [repo_root])
        ],
        "promotable": grant_type != "diagnostic",
        "contract_ids": [44],
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    payload["grant_digest"] = _stable_json_digest(payload)
    return payload


def _attach_authority(
    manifest: dict[str, object],
    repo_root: Path,
    *,
    grant_type: str = "product",
    write_guard_roots: list[Path] | None = None,
    contract_roots: list[Path] | None = None,
) -> dict[str, object]:
    grant = _authority_grant(
        repo_root,
        grant_type=grant_type,
        write_guard_roots=write_guard_roots,
        contract_roots=contract_roots,
    )
    grants = [grant]
    binding = dict(manifest.get("runtime_workspace_binding") or {})
    binding.update({
        "authority_schema_version": "runtime-workspace-authority-grant-v1",
        "runtime_workspace_authority_grants": grants,
        "runtime_workspace_authority_grant_digest": _stable_json_digest(grants),
        "promotable": grant_type != "diagnostic",
    })
    manifest.update({
        "authority_schema_version": "runtime-workspace-authority-grant-v1",
        "runtime_workspace_authority_grants": grants,
        "runtime_workspace_authority_grant_digest": _stable_json_digest(grants),
        "promotable": grant_type != "diagnostic",
        "runtime_workspace_binding": binding,
    })
    return manifest


def _authority_binding_fields(
    repo_root: Path,
    *,
    grant_type: str = "product",
    write_guard_roots: list[Path] | None = None,
    contract_roots: list[Path] | None = None,
) -> dict[str, object]:
    grant = _authority_grant(
        repo_root,
        grant_type=grant_type,
        write_guard_roots=write_guard_roots,
        contract_roots=contract_roots,
    )
    grants = [grant]
    return {
        "write_guard_roots": [
            str(path) for path in (
                write_guard_roots if write_guard_roots is not None else [repo_root]
            )
        ],
        "write_guard_scope": "diagnostic" if grant_type == "diagnostic" else "contract",
        "authority_schema_version": "runtime-workspace-authority-grant-v1",
        "runtime_workspace_authority_grants": grants,
        "runtime_workspace_authority_grant_digest": _stable_json_digest(grants),
        "promotable": grant_type != "diagnostic",
    }


def test_pool_write_auth_secret_is_group_readable(tmp_path: Path) -> None:
    secret = _pool_write_auth_secret(tmp_path)

    assert secret
    mode = (tmp_path / "runtime-write-auth.secret").stat().st_mode & 0o777
    assert mode == 0o640


def test_pool_write_auth_secret_reads_existing_secret_when_chmod_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_path = tmp_path / "runtime-write-auth.secret"
    secret_path.write_text("existing-secret\n", encoding="utf-8")
    secret_path.chmod(0o640)

    def _deny_chmod(self: Path, mode: int) -> None:
        del self, mode
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "chmod", _deny_chmod)

    assert _pool_write_auth_secret(tmp_path) == "existing-secret"


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
async def test_weighted_selection_sends_most_work_to_profile_two(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_weighted_profiles())

    picked = [
        (await runtime._select_profile(session_key=f"actor-{idx}:feat", persistent=False)).name
        for idx in range(10)
    ]

    assert picked.count("iriai-claude-1") == 1
    assert picked.count("iriai-claude-2") == 9


def test_known_profiles_load_default_capacity_weights():
    assert _coerce_profile({"name": "iriai-claude-1"}).weight == 1
    assert _coerce_profile({"name": "iriai-claude-2"}).weight == 1
    assert _coerce_profile({"name": "iriai-claude-3"}).weight == 1


def test_load_profiles_defaults_to_single_active_profile(tmp_path: Path):
    profiles = load_profiles(tmp_path)

    assert [profile.name for profile in profiles] == ["iriai-claude-1"]
    assert [profile.weight for profile in profiles] == [1.0]


def test_load_profiles_migrates_legacy_default_capacity_profile_set(tmp_path: Path):
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
        "iriai-claude-1": 1,
    }
    stored = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    stored_weights = {
        profile["name"]: profile["weight"]
        for profile in stored["profiles"]
    }
    assert stored_weights == {
        "iriai-claude-1": 1,
    }


def test_load_profiles_migrates_deprecated_two_profile_default(tmp_path: Path):
    _write_json_atomic(
        tmp_path / "profiles.json",
        {
            "profiles": [
                {"name": "iriai-claude-1", "user": "iriai-claude-1", "weight": 1},
                {"name": "iriai-claude-2", "user": "iriai-claude-2", "weight": 9},
            ]
        },
    )

    profiles = load_profiles(tmp_path)

    assert [profile.name for profile in profiles] == ["iriai-claude-1"]
    stored = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert [profile["name"] for profile in stored["profiles"]] == ["iriai-claude-1"]


def test_load_profiles_preserves_custom_explicit_profile_set(tmp_path: Path):
    _write_json_atomic(
        tmp_path / "profiles.json",
        {
            "profiles": [
                {"name": "iriai-claude-1", "user": "iriai-claude-1", "weight": 1},
                {
                    "name": "custom-claude",
                    "user": "iriai-claude-2",
                    "claude_command": "/bin/echo",
                    "weight": 2,
                },
            ]
        },
    )

    profiles = load_profiles(tmp_path)

    assert [profile.name for profile in profiles] == ["iriai-claude-1", "custom-claude"]
    assert profiles[1].claude_command == "/bin/echo"


@pytest.mark.asyncio
async def test_removed_profiles_are_pruned_from_state_and_affinity(tmp_path: Path):
    _write_json_atomic(
        tmp_path / "profile_state.json",
        {
            "profiles": {
                "iriai-claude-1": {"status": "unavailable", "probe_after": "2000-01-01T00:00:00+00:00"},
                "iriai-claude-2": {"status": "unavailable", "probe_after": "2999-01-01T00:00:00+00:00"},
            }
        },
    )
    _write_json_atomic(
        tmp_path / "affinity.json",
        {
            "next_index": 1,
            "profile_weights": {"iriai-claude-1": 1.0, "iriai-claude-2": 9.0},
            "profile_dispatch_counts": {"iriai-claude-2": 15},
            "session_profiles": {"lead:feature": "iriai-claude-2"},
        },
    )
    runtime = ClaudePoolRuntime(
        root=tmp_path,
        profiles=[
            ClaudePoolProfile(
                name="iriai-claude-1",
                user="iriai-claude-1",
                claude_command="/bin/echo",
            )
        ],
    )
    runtime._clear_profile_unavailable("iriai-claude-1")

    picked = await runtime._select_profile(session_key="lead:feature", persistent=True)

    state = json.loads((tmp_path / "profile_state.json").read_text(encoding="utf-8"))
    affinity = json.loads((tmp_path / "affinity.json").read_text(encoding="utf-8"))
    assert picked.name == "iriai-claude-1"
    assert "iriai-claude-2" not in state["profiles"]
    assert affinity["profile_weights"] == {"iriai-claude-1": 1.0}
    assert "iriai-claude-2" not in affinity["profile_dispatch_counts"]
    assert affinity["session_profiles"] == {"lead:feature": "iriai-claude-1"}


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


def test_internal_server_api_error_is_retryable_transient_failure():
    error = (
        "Claude CLI failed with exit code 1: "
        '{"type":"result","is_error":true,"result":"API Error: '
        '{\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"api_error\\",'
        '\\"message\\":\\"Internal server error\\"}}"}'
    )

    assert _classify_claude_pool_error(error) == "transient_api_error"


@pytest.mark.asyncio
async def test_invoke_fails_over_when_profile_hits_transient_api_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    role = Role(name="root-cause-analyst", prompt="Return RCA.", metadata={})
    calls: list[str] = []

    async def _fake_submit_and_wait(*args, **kwargs):
        profile = kwargs["profile"]
        calls.append(profile.name)
        if len(calls) == 1:
            raise RuntimeError(
                'Claude CLI failed with exit code 1: {"type":"result",'
                '"is_error":true,"result":"API Error: {'
                '\\"type\\":\\"error\\",\\"error\\":{'
                '\\"type\\":\\"api_error\\",'
                '\\"message\\":\\"Internal server error\\"}}"}'
            )
        return ("ok", None, {})

    monkeypatch.setattr(runtime, "_submit_and_wait", _fake_submit_and_wait)

    result = await runtime.invoke(
        role,
        "Return RCA.",
        workspace=SimpleNamespace(path=tmp_path),
        session_key="root-cause-analyst:feat-1",
    )

    assert result == "ok"
    assert calls == ["iriai-claude-1", "iriai-claude-2"]
    state = json.loads((tmp_path / "profile_state.json").read_text())
    record = state["profiles"]["iriai-claude-1"]
    assert record["reason"] == "transient_api_error"
    probe_after = datetime.fromisoformat(record["probe_after"])
    delay = (probe_after - datetime.now(UTC)).total_seconds()
    assert 0 < delay <= 60


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


# W-Q: an in-flight job is a MILD spread preference, never a busy/skip signal.
@pytest.mark.asyncio
async def test_active_job_is_mild_spread_preference_not_busy_exclusion(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    # claude-1 has one in-flight (queued) job: small spread penalty only.
    _write_json_atomic(
        _job_state_path(tmp_path, "queued", "iriai-claude-1", "inflight-1"),
        {"id": "inflight-1", "status": "queued"},
    )
    # claude-2 and claude-3 are idle but carry heavier recent usage.
    for idx, profile in enumerate(("iriai-claude-2", "iriai-claude-3")):
        job_id = f"recent-cost-{idx}"
        payload_dir = _payload_dir(tmp_path, job_id)
        payload_dir.mkdir(parents=True)
        result_path = payload_dir / "result.json"
        _write_json_atomic(
            result_path,
            {"ok": True, "raw": {"total_cost_usd": 5.0, "usage": {}}},
        )
        _write_json_atomic(
            _job_state_path(tmp_path, "done", profile, job_id),
            {"id": job_id, "paths": {"result": str(result_path)}},
        )

    picked = await runtime._select_profile(session_key="actor:feat", persistent=False)

    # Under the old active*1000.0 scoring the in-flight job made claude-1
    # lose to any idle profile; with the mild penalty it wins over idle
    # profiles with heavier recent usage.
    assert picked.name == "iriai-claude-1"


# W-Q: usage_limited gating is untouched — an UNAVAILABLE profile stays
# excluded even when every available profile is busy with concurrent jobs.
@pytest.mark.asyncio
async def test_usage_limited_profile_still_excluded_when_others_busy(tmp_path: Path):
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
    for profile in ("iriai-claude-2", "iriai-claude-3"):
        for idx in range(3):
            _write_json_atomic(
                _job_state_path(tmp_path, "queued", profile, f"busy-{profile}-{idx}"),
                {"id": f"busy-{profile}-{idx}", "status": "queued"},
            )

    picked = [
        (await runtime._select_profile(session_key=f"actor-{idx}:feat", persistent=False)).name
        for idx in range(4)
    ]

    assert "iriai-claude-1" not in picked


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


@pytest.mark.asyncio
async def test_bound_pool_manifest_includes_runtime_binding_fields(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    profile = _profiles()[0]
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    binding = {
        "sandbox_id": "sandbox-04",
        "cwd": str(cwd),
        "workspace_override": str(cwd),
        "repo_roots": [str(cwd / "repo")],
        "contract_ids": ["contract-1"],
        "writable_roots": [str(cwd)],
        "readonly_roots": [],
        "blocked_roots": [str(tmp_path / "blocked")],
        "manifest_path": str(manifest_path),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "runtime": "claude_pool",
    }
    role = Role(
        name="observer",
        prompt="Return ok.",
        tools=["Read"],
        metadata={"runtime_workspace_binding": binding},
    )

    task = asyncio.create_task(
        runtime._submit_and_wait(
            role,
            "Do work.",
            output_type=None,
            workspace=SimpleNamespace(path=cwd),
            session_key="implementer:feat-1",
            profile=profile,
        )
    )

    for _ in range(100):
        queued = list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))
        if queued:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("bound job was not queued")

    manifest = json.loads(queued[0].read_text(encoding="utf-8"))
    assert manifest["role"]["model"] == "claude-opus-4-8"
    assert manifest["role"]["effort"] == "high"
    assert manifest["runtime_workspace_binding"]["sandbox_id"] == "sandbox-04"
    assert manifest["sandbox_id"] == "sandbox-04"
    assert manifest["repo_roots"] == [str(cwd / "repo")]
    assert manifest["contract_ids"] == ["contract-1"]
    assert manifest["writable_roots"] == [str(cwd)]
    assert manifest["blocked_roots"] == [str(tmp_path / "blocked")]
    assert manifest["manifest_path"] == str(manifest_path)
    assert manifest["expires_at"] == binding["expires_at"]

    result_path = Path(manifest["paths"]["result"])
    _write_json_atomic(result_path, {"ok": True, "result_text": "ok"})
    _write_json_atomic(
        _job_state_path(tmp_path, "done", profile.name, manifest["id"]),
        {**manifest, "status": "done"},
    )

    result_text, structured_output, raw = await task
    assert result_text == "ok"
    assert structured_output is None
    assert raw is None


def test_bound_pool_sandbox_profile_excludes_global_temp_roots(tmp_path: Path):
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    result_path = tmp_path / "payload" / "result.json"
    result_path.parent.mkdir()
    profile_path = tmp_path / "payload" / "sandbox.sb"
    manifest = {
        "cwd": str(cwd),
        "paths": {
            "prompt": str(tmp_path / "payload" / "prompt.txt"),
            "system_prompt": str(tmp_path / "payload" / "system.txt"),
            "schema": str(tmp_path / "payload" / "schema.json"),
            "result": str(result_path),
            "stdout": str(tmp_path / "payload" / "stdout.log"),
            "stderr": str(tmp_path / "payload" / "stderr.log"),
            "sandbox_profile": str(profile_path),
        },
        "runtime_workspace_binding": {
            "writable_roots": [str(cwd / "src")],
        },
    }

    written = _write_sandbox_exec_profile(manifest)

    profile = written.read_text(encoding="utf-8")
    assert f'(subpath "{tempfile.gettempdir()}"' not in profile
    if os.environ.get("TMPDIR"):
        assert f'(subpath "{os.environ["TMPDIR"].rstrip("/")}"' not in profile
    assert str(cwd) in profile
    assert str(result_path.parent) in profile


def test_bound_pool_sandbox_profile_includes_profile_session_scratch_root(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    profile_path = tmp_path / "payload" / "sandbox.sb"
    scratch_root = tmp_path / "home" / ".claude" / "session-env"
    manifest = {
        "cwd": str(cwd),
        "paths": {
            "prompt": str(tmp_path / "payload" / "prompt.txt"),
            "result": str(tmp_path / "payload" / "result.json"),
            "sandbox_profile": str(profile_path),
        },
        "runtime_scratch_roots": [str(scratch_root)],
        "runtime_workspace_binding": {
            "writable_roots": [str(cwd)],
        },
    }

    written = _write_sandbox_exec_profile(manifest)

    profile = written.read_text(encoding="utf-8")
    assert str(cwd) in profile
    assert str(scratch_root) in profile


def test_product_write_guard_uses_contract_roots_not_repo_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "sandbox"
    allowed_parent = cwd / "src/vs/workbench/contrib/workflowTab/views/implementation"
    allowed_parent.mkdir(parents=True)
    profile_path = tmp_path / "payload" / "sandbox.sb"
    manifest = {
        "cwd": str(cwd),
        "paths": {
            "prompt": str(tmp_path / "payload" / "prompt.txt"),
            "result": str(tmp_path / "payload" / "result.json"),
            "sandbox_profile": str(profile_path),
        },
        "runtime_workspace_binding": {
            "writable_roots": [str(allowed_parent / "index.ts")],
            "write_guard_roots": [str(allowed_parent)],
            "write_guard_scope": "contract",
        },
    }

    written = _write_sandbox_exec_profile(manifest)

    profile = written.read_text(encoding="utf-8")
    assert f'(subpath "{cwd}")' not in profile
    assert f'(subpath "{allowed_parent}")' in profile


def test_bound_write_authorization_covers_runtime_scratch_roots(
    tmp_path: Path,
) -> None:
    manifest = {
        "id": "job",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(tmp_path / "sandbox"),
        "paths": {"prompt": str(tmp_path / "prompt.md")},
        "runtime_scratch_roots": [str(tmp_path / "home" / ".claude" / "session-env")],
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(tmp_path / "sandbox"),
            "workspace_override": str(tmp_path / "sandbox"),
            "repo_roots": {"app": str(tmp_path / "sandbox")},
            "writable_roots": [str(tmp_path / "sandbox")],
            "blocked_roots": [],
            "contract_ids": [44],
            "manifest_path": str(tmp_path / "sandbox-manifest.json"),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
    }
    tampered = {
        **manifest,
        "runtime_scratch_roots": [str(tmp_path / "other-home" / ".claude" / "session-env")],
    }

    assert _bound_write_authorization(manifest, "secret") != _bound_write_authorization(
        tampered,
        "secret",
    )


def test_bound_write_authorization_covers_write_guard_scope(
    tmp_path: Path,
) -> None:
    manifest = {
        "id": "job",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(tmp_path / "sandbox"),
        "paths": {"prompt": str(tmp_path / "prompt.md")},
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(tmp_path / "sandbox"),
            "workspace_override": str(tmp_path / "sandbox"),
            "repo_roots": {"app": str(tmp_path / "sandbox")},
            "writable_roots": [str(tmp_path / "sandbox" / "src" / "index.ts")],
            "write_guard_roots": [str(tmp_path / "sandbox" / "src")],
            "write_guard_scope": "contract",
            "blocked_roots": [],
            "contract_ids": [44],
            "manifest_path": str(tmp_path / "sandbox-manifest.json"),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
    }
    tampered = {
        **manifest,
        "runtime_workspace_binding": {
            **manifest["runtime_workspace_binding"],
            "write_guard_scope": "diagnostic",
        },
    }

    assert _bound_write_authorization(manifest, "secret") != _bound_write_authorization(
        tampered,
        "secret",
    )


def test_bound_write_authorization_covers_authority_grant_digest(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "sandbox"
    manifest = _attach_authority(
        {
            "id": "job",
            "created_at": datetime.now(UTC).isoformat(),
            "cwd": str(repo_root),
            "paths": {"prompt": str(tmp_path / "prompt.md")},
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(repo_root),
                "workspace_override": str(repo_root),
                "repo_roots": {"app": str(repo_root)},
                "writable_roots": [str(repo_root / "src" / "index.ts")],
                "write_guard_roots": [str(repo_root / "src")],
                "write_guard_scope": "contract",
                "blocked_roots": [],
                "contract_ids": [44],
                "manifest_path": str(repo_root / "sandbox-manifest.json"),
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "runtime": "claude_pool",
            },
        },
        repo_root,
        write_guard_roots=[repo_root / "src"],
        contract_roots=[repo_root / "src" / "index.ts"],
    )
    tampered = {
        **manifest,
        "runtime_workspace_binding": {
            **manifest["runtime_workspace_binding"],
            "runtime_workspace_authority_grant_digest": "tampered",
        },
    }

    assert _bound_write_authorization(manifest, "secret") != _bound_write_authorization(
        tampered,
        "secret",
    )


@pytest.mark.asyncio
async def test_bound_pool_worker_requires_authority_grant_for_write_role(
    tmp_path: Path,
) -> None:
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    manifest = {
        "id": "job",
        "kind": "claude",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(cwd),
        "role": {"name": "fake", "tools": ["Read", "Write"]},
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "workspace_override": str(cwd),
            "repo_roots": {"app": str(cwd)},
            "writable_roots": [str(cwd)],
            "write_guard_roots": [str(cwd)],
            "write_guard_scope": "contract",
            "blocked_roots": [],
            "contract_ids": [44],
            "manifest_path": str(manifest_path),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
        "runtime_workspace_write_authorized": True,
        "runtime_workspace_write_guard": "sandbox_exec",
        "paths": {
            "prompt": str(tmp_path / "prompt.md"),
            "sandbox_profile": str(tmp_path / "sandbox-exec.sb"),
        },
    }
    manifest["runtime_workspace_write_authorization"] = _bound_write_authorization(
        manifest,
        _pool_write_auth_secret(tmp_path),
    )

    with pytest.raises(RuntimeError, match="authority grant"):
        await runner._execute_claude(manifest)


@pytest.mark.asyncio
async def test_bound_pool_worker_rejects_write_guard_grant_mismatch(
    tmp_path: Path,
) -> None:
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "sandbox"
    (cwd / "src").mkdir(parents=True)
    (cwd / "other").mkdir()
    manifest_path = _write_sandbox_manifest(
        cwd,
        cwd,
        writable_roots=[cwd / "src" / "index.ts"],
    )
    manifest = {
        "id": "job",
        "kind": "claude",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(cwd),
        "role": {"name": "fake", "tools": ["Read", "Write"]},
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "workspace_override": str(cwd),
            "repo_roots": {"app": str(cwd)},
            "writable_roots": [str(cwd / "src" / "index.ts")],
            "write_guard_roots": [str(cwd / "other")],
            "write_guard_scope": "contract",
            "blocked_roots": [],
            "contract_ids": [44],
            "manifest_path": str(manifest_path),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
        "runtime_workspace_write_authorized": True,
        "runtime_workspace_write_guard": "sandbox_exec",
        "paths": {
            "prompt": str(tmp_path / "prompt.md"),
            "sandbox_profile": str(tmp_path / "sandbox-exec.sb"),
        },
    }
    _attach_authority(
        manifest,
        cwd,
        write_guard_roots=[cwd / "src"],
        contract_roots=[cwd / "src" / "index.ts"],
    )
    manifest["runtime_workspace_write_authorization"] = _bound_write_authorization(
        manifest,
        _pool_write_auth_secret(tmp_path),
    )

    with pytest.raises(RuntimeError, match="write guard roots"):
        await runner._execute_claude(manifest)


@pytest.mark.asyncio
async def test_disabled_runner_profile_refuses_to_claim_work(tmp_path: Path) -> None:
    _write_json_atomic(
        tmp_path / "profiles.json",
        {
            "profiles": [
                {
                    "name": "iriai-claude-1",
                    "user": "iriai-claude-1",
                    "claude_command": "/bin/echo",
                },
                {
                    "name": "iriai-claude-2",
                    "user": "iriai-claude-2",
                    "claude_command": "/bin/echo",
                },
            ]
        },
    )
    runner = _FakeClaudeRunner(profile="iriai-claude-2", root=tmp_path)
    _write_json_atomic(
        tmp_path / "profiles.json",
        {
            "profiles": [
                {
                    "name": "iriai-claude-1",
                    "user": "iriai-claude-1",
                    "claude_command": "/bin/echo",
                }
            ]
        },
    )
    job_id = "disabled-job"
    queued_path = _job_state_path(tmp_path, "queued", "iriai-claude-2", job_id)
    _write_json_atomic(
        queued_path,
        {
            "id": job_id,
            "kind": "health",
            "profile": "iriai-claude-2",
            "status": "queued",
        },
    )

    await runner.run_once()

    assert queued_path.exists()
    assert not _job_state_path(tmp_path, "running", "iriai-claude-2", job_id).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tools", "metadata"),
    [
        (["Read", "Write"], {}),
        (["Read", "Bash"], {}),
        (["Read"], {"write_producing": True}),
    ],
)
async def test_bound_pool_write_producing_role_submits_authorized_sandbox_job(
    tmp_path: Path,
    tools: list[str],
    metadata: dict[str, object],
):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    profile = _profiles()[0]
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    role = Role(
        name="implementer",
        prompt="Return ok.",
        tools=tools,
        metadata={
            **metadata,
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(cwd),
                "workspace_override": str(cwd),
                "repo_roots": {"app": str(cwd)},
                "writable_roots": [str(cwd)],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "runtime": "claude_pool",
                **_authority_binding_fields(cwd),
            }
        },
    )

    task = asyncio.create_task(runtime._submit_and_wait(
        role,
        "Do work.",
        output_type=None,
        workspace=SimpleNamespace(path=cwd),
        session_key="implementer:feat-1",
        profile=profile,
    ))
    await asyncio.sleep(0.05)
    queued = list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))
    assert len(queued) == 1
    manifest = json.loads(queued[0].read_text(encoding="utf-8"))
    assert manifest["runtime_workspace_write_authorized"] is True
    assert manifest["runtime_workspace_write_guard"] == "sandbox_exec"
    assert manifest["runtime_scratch_roots"] == [
        str(path) for path in _profile_runtime_scratch_roots(profile)
    ]
    assert manifest["runtime_workspace_write_authorization"] == _bound_write_authorization(
        manifest,
        _pool_write_auth_secret(tmp_path),
    )
    assert manifest["runtime_workspace_binding"]["cwd"] == str(cwd)
    result_path = Path(manifest["paths"]["result"])
    _write_json_atomic(result_path, {"ok": True, "result_text": "ok"})
    _write_json_atomic(
        _job_state_path(tmp_path, "done", profile.name, manifest["id"]),
        {**manifest, "status": "done"},
    )

    result_text, structured_output, raw = await task
    assert result_text == "ok"
    assert structured_output is None
    assert raw is None


@pytest.mark.asyncio
async def test_bound_pool_write_role_accepts_file_level_writable_roots(
    tmp_path: Path,
) -> None:
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    profile = _profiles()[0]
    sandbox_root = tmp_path / "sandbox"
    cwd = sandbox_root / "repos" / "app"
    writable_file = cwd / "src" / "allowed.py"
    writable_file.parent.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(
        sandbox_root,
        cwd,
        writable_roots=[writable_file],
    )
    role = Role(
        name="implementer",
        prompt="Return ok.",
        tools=["Read", "Write"],
        metadata={
            "runtime_workspace_binding": {
                "sandbox_id": "sandbox-04",
                "cwd": str(cwd),
                "workspace_override": str(cwd),
                "repo_roots": {"app": str(cwd)},
                "writable_roots": [str(writable_file)],
                "blocked_roots": [],
                "manifest_path": str(manifest_path),
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "runtime": "claude_pool",
                **_authority_binding_fields(
                    cwd,
                    write_guard_roots=[writable_file.parent],
                    contract_roots=[writable_file],
                ),
            }
        },
    )

    task = asyncio.create_task(runtime._submit_and_wait(
        role,
        "Do work.",
        output_type=None,
        workspace=SimpleNamespace(path=cwd),
        session_key="implementer:feat-1",
        profile=profile,
    ))
    await asyncio.sleep(0.05)
    queued = list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))
    assert len(queued) == 1
    manifest = json.loads(queued[0].read_text(encoding="utf-8"))
    assert manifest["runtime_workspace_binding"]["writable_roots"] == [str(writable_file)]
    result_path = Path(manifest["paths"]["result"])
    _write_json_atomic(result_path, {"ok": True, "result_text": "ok"})
    _write_json_atomic(
        _job_state_path(tmp_path, "done", profile.name, manifest["id"]),
        {**manifest, "status": "done"},
    )

    result_text, structured_output, raw = await task
    assert result_text == "ok"
    assert structured_output is None
    assert raw is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda binding, root, cwd, outside: binding.update(
                {"manifest_path": str(root / "missing.json")}
            ),
            "sandbox manifest does not exist",
        ),
        (
            lambda binding, root, cwd, outside: (
                (root / "bad-manifest.json").write_text("{", encoding="utf-8"),
                binding.update({"manifest_path": str(root / "bad-manifest.json")}),
            ),
            "unreadable sandbox manifest",
        ),
        (
            lambda binding, root, cwd, outside: binding.update({"sandbox_id": "stale-sandbox"}),
            "sandbox_id does not match manifest",
        ),
        (
            lambda binding, root, cwd, outside: binding.update(
                {
                    "cwd": str(outside),
                    "workspace_override": str(outside),
                    "writable_roots": [str(outside)],
                }
            ),
            "cwd is outside sandbox root",
        ),
    ],
)
async def test_bound_pool_write_binding_rejects_unproved_binding_before_queue(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    profile = _profiles()[0]
    sandbox_root = tmp_path / "sandbox"
    cwd = sandbox_root / "repos" / "app"
    cwd.mkdir(parents=True)
    outside = tmp_path / "canonical" / "app"
    outside.mkdir(parents=True)
    manifest_path = _write_sandbox_manifest(sandbox_root, cwd)
    binding = {
        "sandbox_id": "sandbox-04",
        "cwd": str(cwd),
        "workspace_override": str(cwd),
        "repo_roots": {"app": str(cwd)},
        "writable_roots": [str(cwd)],
        "blocked_roots": [],
        "manifest_path": str(manifest_path),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "runtime": "claude_pool",
    }
    mutate(binding, tmp_path, cwd, outside)
    role = Role(
        name="implementer",
        prompt="Return ok.",
        tools=["Read", "Write"],
        metadata={"runtime_workspace_binding": binding},
    )

    workspace_path = outside if "outside sandbox root" in message else cwd
    with pytest.raises(RuntimeError, match=message):
        await runtime._submit_and_wait(
            role,
            "Do work.",
            output_type=None,
            workspace=SimpleNamespace(path=workspace_path),
            session_key="implementer:feat-1",
            profile=profile,
        )
    assert not list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))


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


@pytest.mark.asyncio
async def test_pool_wait_allows_healthy_long_running_job_with_fresh_heartbeat(tmp_path: Path):
    profile = _profiles()[0]
    runtime = ClaudePoolRuntime(
        root=tmp_path,
        profiles=[profile],
        poll_interval=0.005,
        job_stale_timeout=0.5,
        job_absolute_timeout=0,
    )
    role = Role(name="implementer", prompt="", metadata={})

    async def complete_job() -> None:
        queued_paths: list[Path] = []
        while not queued_paths:
            queued_paths = list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))
            await asyncio.sleep(0.005)
        queued_path = queued_paths[0]
        manifest = json.loads(queued_path.read_text(encoding="utf-8"))
        running_path = _job_state_path(tmp_path, "running", profile.name, manifest["id"])
        manifest.update({
            "status": "running",
            "claimed_at": "2000-01-01T00:00:00+00:00",
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        })
        _write_json_atomic(running_path, manifest)
        queued_path.unlink()
        await asyncio.sleep(0.03)
        result_path = Path(manifest["paths"]["result"])
        _write_json_atomic(
            result_path,
            {
                "ok": True,
                "result_text": "ok",
                "structured_output": None,
                "raw": {"result": "ok"},
            },
        )
        done_path = _job_state_path(tmp_path, "done", profile.name, manifest["id"])
        manifest.update({
            "status": "done",
            "finished_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        })
        _write_json_atomic(running_path, manifest)
        os.replace(running_path, done_path)

    worker = asyncio.create_task(complete_job())
    try:
        final_text, structured, raw = await runtime._submit_and_wait(
            role,
            "Do work.",
            output_type=None,
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
            profile=profile,
        )
    finally:
        await worker

    assert final_text == "ok"
    assert structured is None
    assert raw == {"result": "ok"}


@pytest.mark.asyncio
async def test_pool_wait_fails_stale_running_job_heartbeat(tmp_path: Path):
    profile = _profiles()[0]
    runtime = ClaudePoolRuntime(
        root=tmp_path,
        profiles=[profile],
        poll_interval=0.005,
        job_stale_timeout=0.01,
        job_absolute_timeout=0,
    )
    role = Role(name="implementer", prompt="", metadata={})

    async def make_job_stale() -> None:
        queued_paths: list[Path] = []
        while not queued_paths:
            queued_paths = list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))
            await asyncio.sleep(0.005)
        queued_path = queued_paths[0]
        manifest = json.loads(queued_path.read_text(encoding="utf-8"))
        running_path = _job_state_path(tmp_path, "running", profile.name, manifest["id"])
        manifest.update({
            "status": "running",
            "heartbeat_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-01T00:00:00+00:00",
        })
        _write_json_atomic(running_path, manifest)
        queued_path.unlink()

    worker = asyncio.create_task(make_job_stale())
    with pytest.raises(TimeoutError, match="heartbeat is stale"):
        await runtime._submit_and_wait(
            role,
            "Do work.",
            output_type=None,
            workspace=SimpleNamespace(path=tmp_path),
            session_key="implementer:feat-1",
            profile=profile,
        )
    await worker


def test_find_late_completed_pool_job_requires_matching_identity(tmp_path: Path):
    profile = _profiles()[0]
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=[profile])
    del runtime
    job_id = "late123"
    payload_dir = _payload_dir(tmp_path, job_id)
    payload_dir.mkdir(parents=True)
    schema = {"type": "object", "properties": {"task_id": {"type": "string"}}}
    schema_digest = _stable_json_digest(schema)
    schema_path = payload_dir / "schema.json"
    schema_path.write_text(json.dumps(schema, sort_keys=True), encoding="utf-8")
    result_path = payload_dir / "result.json"
    _write_json_atomic(
        result_path,
        {
            "ok": True,
            "result_text": "{\"task_id\":\"TASK-late\",\"status\":\"completed\"}",
            "structured_output": {"task_id": "TASK-late", "status": "completed"},
            "raw": {"result": "ok"},
        },
    )
    manifest = {
        "id": job_id,
        "kind": "claude",
        "feature_id": "feat-late",
        "status": "done",
        "profile": profile.name,
        "invocation_id": "invoke-late",
        "dispatch_idempotency_key": "idem:late",
        "runtime_workspace_binding": {
            "attempt_id": 144,
            "sandbox_id": "sandbox-late",
            "role_metadata": {
                "group_idx": 78,
                "task_ids": ["TASK-late"],
            },
        },
        "paths": {
            "result": str(result_path),
            "schema": str(schema_path),
            "stdout": str(payload_dir / "stdout.json"),
            "stderr": str(payload_dir / "stderr.log"),
        },
    }
    _write_json_atomic(_job_state_path(tmp_path, "done", profile.name, job_id), manifest)

    recovered = find_late_completed_claude_pool_job(
        root=tmp_path,
        feature_id="feat-late",
        group_idx=78,
        task_id="TASK-late",
        attempt_id=144,
        sandbox_id="sandbox-late",
        invocation_id="invoke-late",
        idempotency_key="idem:late",
        output_schema_digest=schema_digest,
    )

    assert recovered is not None
    assert recovered.job_id == job_id
    assert recovered.structured_output["task_id"] == "TASK-late"
    assert recovered.schema_digest_validation == "matched_schema_file"
    recovered_by_alias = find_late_completed_claude_pool_job(
        root=tmp_path,
        feature_id="feat-late",
        group_idx=78,
        task_id="TASK-late",
        attempt_id=144,
        sandbox_id="dispatch-sandbox-late",
        sandbox_ids=["dispatch-sandbox-late", "sandbox-late"],
        invocation_id="invoke-late",
        idempotency_key="idem:late",
        output_schema_digest=schema_digest,
    )

    assert recovered_by_alias is not None
    assert recovered_by_alias.job_id == job_id
    assert (
        find_late_completed_claude_pool_job(
            root=tmp_path,
            feature_id="feat-late",
            group_idx=78,
            task_id="TASK-other",
            attempt_id=144,
            sandbox_id="sandbox-late",
            invocation_id="invoke-late",
            idempotency_key="idem:late",
            output_schema_digest=schema_digest,
        )
        is None
    )


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


def test_runner_wraps_bound_write_jobs_in_sandbox_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _FakeClaudeRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    profile_path = tmp_path / "payload" / "sandbox-exec.sb"
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    manifest = {
        "cwd": str(cwd),
        "role": {"name": "implementer", "tools": ["Read", "Write"]},
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "writable_roots": [str(cwd)],
            "blocked_roots": [str(tmp_path / "canonical")],
            "manifest_path": str(manifest_path),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
        "runtime_workspace_write_guard": "sandbox_exec",
        "paths": {"sandbox_profile": str(profile_path)},
        "claude": {"command": "/bin/echo"},
    }
    monkeypatch.setattr("iriai_build_v2.runtimes.claude_pool.shutil.which", lambda name: f"/usr/bin/{name}")

    command = runner._build_claude_command(
        manifest,
        system_prompt="",
        schema=None,
    )

    assert command[:3] == ["/usr/bin/sandbox-exec", "-f", str(profile_path)]
    profile = profile_path.read_text(encoding="utf-8")
    assert "(deny file-write*)" in profile
    assert str(cwd) in profile
    assert str(tmp_path / "canonical") not in profile


@pytest.mark.asyncio
async def test_bound_pool_worker_rejects_absent_symlinked_expired_and_missing_cwd(tmp_path: Path):
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    valid_cwd = tmp_path / "sandbox"
    valid_cwd.mkdir()
    manifest_path = _write_sandbox_manifest(valid_cwd, valid_cwd)
    real_cwd = tmp_path / "real"
    real_cwd.mkdir()
    symlink_cwd = tmp_path / "linked"
    symlink_cwd.symlink_to(real_cwd, target_is_directory=True)

    base_binding = {
        "sandbox_id": "sandbox-04",
        "cwd": str(valid_cwd),
        "writable_roots": [str(valid_cwd)],
        "blocked_roots": [],
        "manifest_path": str(manifest_path),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "runtime": "claude_pool",
    }

    cases = [
        ({**base_binding}, None, "missing cwd"),
        ({**base_binding, "cwd": str(tmp_path / "missing")}, str(tmp_path / "missing"), "does not exist"),
        ({**base_binding, "cwd": str(symlink_cwd)}, str(symlink_cwd), "symlinked"),
        (
            {
                **base_binding,
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
            str(valid_cwd),
            "expired",
        ),
    ]

    for binding, cwd, message in cases:
        manifest = {
            "id": "job",
            "kind": "claude",
            "cwd": cwd,
            "runtime_workspace_binding": binding,
            "paths": {},
        }
        with pytest.raises(RuntimeError, match=message):
            await runner._execute_claude(manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        {"name": "fake", "tools": ["Read", "Bash"]},
        {"name": "fake", "tools": ["Read", "Edit"]},
        {"name": "fake", "tools": ["Read"], "metadata": {"write_producing": True}},
        {"name": "fake", "tools": ["Read"], "write_producing": True},
    ],
)
async def test_bound_pool_worker_rejects_handwritten_write_role_manifest(
    tmp_path: Path,
    role: dict[str, object],
) -> None:
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    manifest = {
        "id": "job",
        "kind": "claude",
        "cwd": str(cwd),
        "role": role,
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "writable_roots": [str(cwd)],
            "blocked_roots": [],
            "manifest_path": str(manifest_path),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
        "paths": {},
    }

    with pytest.raises(RuntimeError, match="write-producing"):
        await runner._execute_claude(manifest)


@pytest.mark.asyncio
async def test_bound_pool_worker_rejects_unbound_write_role_manifest(
    tmp_path: Path,
) -> None:
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "canonical"
    cwd.mkdir()
    manifest = {
        "id": "job",
        "kind": "claude",
        "cwd": str(cwd),
        "role": {"name": "fake", "tools": ["Read", "Write"]},
        "paths": {},
    }

    with pytest.raises(RuntimeError, match="requires runtime workspace binding"):
        await runner._execute_claude(manifest)


def _scratch_write_manifest(tmp_path: Path, scratch: Path) -> dict:
    """Manifest with the exact scratch-write shape the submit side produces."""
    manifest = {
        "id": "job-scratch",
        "kind": "claude",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(tmp_path),
        "role": {"name": "compiler", "tools": ["Read", "Glob", "Grep", "Write"]},
        "paths": {"prompt": str(tmp_path / "payload" / "prompt.md")},
        "runtime_scratch_roots": [],
        "pool_scratch_write_root": str(scratch.resolve()),
        "write_guard_roots": [str(scratch.resolve())],
        "runtime_workspace_write_guard": "sandbox_exec",
    }
    manifest["pool_scratch_write_authorization"] = _bound_write_authorization(
        manifest,
        _pool_write_auth_secret(tmp_path),
    )
    return manifest


@pytest.mark.asyncio
async def test_scratch_write_role_submits_sandbox_confined_scratch_job(
    tmp_path: Path,
) -> None:
    """B-5 dispatch fix: a Write-bearing compile role WITHOUT a workspace
    binding dispatches legally when it declares a validated temp scratch
    root — manifest carries the sandbox-exec guard scoped to that root plus
    an HMAC authorization, and the pool worker validation accepts it."""
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles(), poll_interval=0.01)
    profile = _profiles()[0]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scratch = tmp_path / "compile-scratch"
    scratch.mkdir()
    role = Role(
        name="compiler",
        prompt="Compile.",
        tools=["Read", "Glob", "Grep", "Write"],
        metadata={"pool_scratch_write_root": str(scratch)},
    )

    task = asyncio.create_task(runtime._submit_and_wait(
        role,
        "Compile the sources.",
        output_type=None,
        workspace=SimpleNamespace(path=workspace),
        session_key="pm-compiler:feat-1",
        profile=profile,
    ))
    await asyncio.sleep(0.05)
    queued = list((tmp_path / "jobs" / "queued" / profile.name).glob("*.json"))
    assert len(queued) == 1
    manifest = json.loads(queued[0].read_text(encoding="utf-8"))
    resolved_scratch = str(scratch.resolve())
    assert manifest["pool_scratch_write_root"] == resolved_scratch
    assert manifest["write_guard_roots"] == [resolved_scratch]
    assert manifest["runtime_workspace_write_guard"] == "sandbox_exec"
    assert "runtime_workspace_binding" not in manifest
    assert manifest["pool_scratch_write_authorization"] == _bound_write_authorization(
        manifest,
        _pool_write_auth_secret(tmp_path),
    )

    # The pool worker validation accepts the queued manifest as-is — the
    # binding-required rejection no longer fires for this shape.
    runner = ClaudePoolRunner(profile=profile.name, root=tmp_path)
    runner._validate_bound_job_manifest(manifest)

    result_path = Path(manifest["paths"]["result"])
    _write_json_atomic(result_path, {"ok": True, "result_text": "ok"})
    _write_json_atomic(
        _job_state_path(tmp_path, "done", profile.name, manifest["id"]),
        {**manifest, "status": "done"},
    )
    result_text, structured_output, raw = await task
    assert result_text == "ok"
    assert structured_output is None


def test_scratch_write_submit_rejects_non_temp_scratch_root(tmp_path: Path) -> None:
    """A scratch root OUTSIDE the system temp tree fails loud at submit."""
    del tmp_path
    from iriai_build_v2.runtimes.claude import _validated_pool_scratch_root

    outside = Path.home()
    with pytest.raises(RuntimeError, match="strictly inside a system temp"):
        _validated_pool_scratch_root(str(outside), role_name="compiler")
    with pytest.raises(RuntimeError, match="not an existing directory"):
        _validated_pool_scratch_root("/tmp/iriai-test-missing-scratch-dir-xyz", role_name="compiler")
    with pytest.raises(RuntimeError, match="must be absolute"):
        _validated_pool_scratch_root("relative/dir", role_name="compiler")
    # The temp base itself is rejected — only strict subdirectories qualify.
    # (/private/tmp, not /tmp: on macOS /tmp is a symlink, which is rejected
    # even earlier by the symlink check.)
    with pytest.raises(RuntimeError, match="strictly inside a system temp"):
        _validated_pool_scratch_root("/private/tmp", role_name="compiler")


@pytest.mark.asyncio
async def test_scratch_write_pool_worker_rejects_tampered_scratch_manifests(
    tmp_path: Path,
) -> None:
    """Hand-edited scratch-write manifests are rejected: guard-root mismatch,
    missing sandbox-exec guard, forged authorization, non-temp root."""
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    scratch = tmp_path / "compile-scratch"
    scratch.mkdir()

    # Guard roots widened beyond the scratch root.
    manifest = _scratch_write_manifest(tmp_path, scratch)
    manifest["write_guard_roots"] = [str(scratch.resolve()), str(tmp_path)]
    with pytest.raises(RuntimeError, match="write guard roots"):
        await runner._execute_claude(manifest)

    # Missing sandbox-exec guard key.
    manifest = _scratch_write_manifest(tmp_path, scratch)
    manifest.pop("runtime_workspace_write_guard")
    with pytest.raises(RuntimeError, match="sandbox-exec write guard"):
        await runner._execute_claude(manifest)

    # Forged/edited authorization.
    manifest = _scratch_write_manifest(tmp_path, scratch)
    manifest["pool_scratch_write_authorization"] = "0" * 64
    with pytest.raises(RuntimeError, match="invalid write authorization"):
        await runner._execute_claude(manifest)

    # Scratch root swapped to a non-temp path after authorization.
    manifest = _scratch_write_manifest(tmp_path, scratch)
    manifest["pool_scratch_write_root"] = str(Path.home())
    with pytest.raises(RuntimeError, match="strictly inside a system temp"):
        await runner._execute_claude(manifest)


def test_runner_wraps_scratch_write_jobs_in_sandbox_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scratch-write job command is wrapped in sandbox-exec with writes
    allowed ONLY under the scratch root + payload dir — never the cwd."""
    runner = _FakeClaudeRunner(profile="iriai-claude-1", root=tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scratch = tmp_path / "compile-scratch"
    scratch.mkdir()
    profile_path = tmp_path / "payload" / "sandbox-exec.sb"
    manifest = _scratch_write_manifest(tmp_path, scratch)
    manifest["cwd"] = str(workspace)
    manifest["paths"] = {"sandbox_profile": str(profile_path)}
    manifest["claude"] = {"command": "/bin/echo"}
    monkeypatch.setattr(
        "iriai_build_v2.runtimes.claude_pool.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    command = runner._build_claude_command(manifest, system_prompt="", schema=None)

    assert command[:3] == ["/usr/bin/sandbox-exec", "-f", str(profile_path)]
    profile = profile_path.read_text(encoding="utf-8")
    assert "(deny file-write*)" in profile
    assert str(scratch.resolve()) in profile
    assert str(workspace) not in profile


@pytest.mark.asyncio
async def test_bound_pool_worker_rejects_spoofed_write_authorization_flag(
    tmp_path: Path,
) -> None:
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    manifest = {
        "id": "job",
        "kind": "claude",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(cwd),
        "role": {"name": "fake", "tools": ["Read", "Write"]},
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "workspace_override": str(cwd),
            "repo_roots": {"app": str(cwd)},
            "writable_roots": [str(cwd)],
            "blocked_roots": [],
            "contract_ids": [44],
            "manifest_path": str(manifest_path),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "runtime": "claude_pool",
        },
        "runtime_workspace_write_authorized": True,
        "runtime_workspace_write_guard": "sandbox_exec",
        "paths": {"prompt": str(tmp_path / "prompt.md")},
    }
    manifest["paths"]["sandbox_profile"] = str(tmp_path / "sandbox-exec.sb")
    manifest["runtime_workspace_write_authorization"] = _bound_write_authorization(
        manifest,
        "attacker-secret",
    )

    with pytest.raises(RuntimeError, match="invalid write authorization"):
        await runner._execute_claude(manifest)


@pytest.mark.asyncio
async def test_bound_pool_worker_rejects_edited_binding_expiry_after_authorization(
    tmp_path: Path,
) -> None:
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    manifest_path = _write_sandbox_manifest(cwd, cwd)
    manifest = {
        "id": "job",
        "kind": "claude",
        "created_at": datetime.now(UTC).isoformat(),
        "cwd": str(cwd),
        "role": {"name": "fake", "tools": ["Read", "Write"]},
        "runtime_workspace_binding": {
            "sandbox_id": "sandbox-04",
            "cwd": str(cwd),
            "workspace_override": str(cwd),
            "repo_roots": {"app": str(cwd)},
            "writable_roots": [str(cwd)],
            "blocked_roots": [],
            "contract_ids": [44],
            "manifest_path": str(manifest_path),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "runtime": "claude_pool",
        },
        "runtime_workspace_write_authorized": True,
        "runtime_workspace_write_guard": "sandbox_exec",
        "paths": {
            "prompt": str(tmp_path / "prompt.md"),
            "sandbox_profile": str(tmp_path / "sandbox-exec.sb"),
        },
    }
    manifest["expires_at"] = manifest["runtime_workspace_binding"]["expires_at"]
    manifest["runtime_workspace_write_authorization"] = _bound_write_authorization(
        manifest,
        _pool_write_auth_secret(tmp_path),
    )
    manifest["runtime_workspace_binding"]["expires_at"] = (
        datetime.now(UTC) + timedelta(hours=2)
    ).isoformat()
    manifest["expires_at"] = manifest["runtime_workspace_binding"]["expires_at"]

    with pytest.raises(RuntimeError, match="invalid write authorization"):
        await runner._execute_claude(manifest)


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
        # max_active=0 disables the W-Q per-cycle claim cap so a single
        # run_once drains the whole queue (this test exercises queue layout,
        # not the concurrency rail).
        _FakeClaudeRunner(
            profile=profile.name, root=tmp_path, heartbeat_interval=0.01, max_active=0
        )
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


def _queue_fake_claude_job(tmp_path: Path, profile: str, job_id: str) -> None:
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


# W-Q safety rail: the runner claims at most (max_active - currently_active)
# manifests per cycle and leaves the rest queued for a later cycle.
@pytest.mark.asyncio
async def test_runner_claim_cap_claims_up_to_max_active_and_leaves_rest_queued(
    tmp_path: Path,
) -> None:
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    del runtime
    for idx in range(6):
        _queue_fake_claude_job(tmp_path, "iriai-claude-1", f"cap{idx}0000")
    runner = _FakeClaudeRunner(
        profile="iriai-claude-1", root=tmp_path, heartbeat_interval=0.01, max_active=4
    )

    await runner.run_once(wait=True)

    queued_dir = tmp_path / "jobs" / "queued" / "iriai-claude-1"
    done_dir = tmp_path / "jobs" / "done" / "iriai-claude-1"
    assert len(list(done_dir.glob("*.json"))) == 4
    assert len(list(queued_dir.glob("*.json"))) == 2

    # Next cycle picks up the remainder once active slots free up.
    await runner.run_once(wait=True)

    assert len(list(done_dir.glob("*.json"))) == 6
    assert list(queued_dir.glob("*.json")) == []


def test_runner_claim_cap_defaults_to_eight(tmp_path: Path) -> None:
    # 8 >= max remaining wave width + headroom (operator concurrency
    # directive 2026-06-12 00:4x): the profile runner must never throttle
    # below dispatched wave width.
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_profiles())
    del runtime
    assert DEFAULT_RUNNER_MAX_ACTIVE == 8
    runner = ClaudePoolRunner(profile="iriai-claude-1", root=tmp_path)
    assert runner.max_active == 8


# ---------------------------------------------------------------------------
# agent_pool: heterogeneous flat pool (N Claude accounts + Codex member)
# ---------------------------------------------------------------------------


class _FakeCodexRuntime:
    """Stand-in for the embedded CodexAgentRuntime so tests don't need the
    Codex CLI on PATH. Injected as ``runtime._codex_runtime``."""

    def __init__(
        self,
        *,
        responses: list[object] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.bind_calls: list[dict[str, object]] = []
        self.live_invocations: set[str] = set()
        self._responses = list(responses or [])
        self._raises = raises

    @asynccontextmanager
    async def bind_invocation(self, invocation_id: str, activity_sink):
        self.bind_calls.append(
            {"invocation_id": invocation_id, "activity_sink": activity_sink}
        )
        try:
            yield
        finally:
            pass

    def invocation_has_live_work(self, invocation_id: str) -> bool:
        return invocation_id in self.live_invocations

    async def invoke(
        self,
        role,
        prompt,
        *,
        output_type=None,
        workspace=None,
        session_key=None,
    ):
        self.calls.append({"role": role, "prompt": prompt, "session_key": session_key})
        if self._raises is not None:
            raise self._raises
        if self._responses:
            return self._responses.pop(0)
        if output_type is not None:
            return output_type(message="ok")
        return "ok"


def _codex_profiles() -> list[ClaudePoolProfile]:
    """Three Claude accounts + one Codex member (a flat 4-member pool)."""
    return [
        ClaudePoolProfile(name="iriai-claude-1", user="iriai-claude-1", claude_command="/bin/echo"),
        ClaudePoolProfile(name="iriai-claude-2", user="iriai-claude-2", claude_command="/bin/echo"),
        ClaudePoolProfile(name="iriai-claude-3", user="iriai-claude-3", claude_command="/bin/echo"),
        ClaudePoolProfile(name="codex", user="codex", kind="codex"),
    ]


def _runtime_with_fake_codex(tmp_path: Path, **kwargs) -> ClaudePoolRuntime:
    """Build a codex-inclusive pool with a fake embedded codex runtime so the
    real Codex CLI is never required."""
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_codex_profiles(), **kwargs)
    runtime._codex_runtime = _FakeCodexRuntime()
    return runtime


# 1. coerce reads kind=codex; defaults stay claude.
def test_coerce_profile_reads_codex_kind_and_defaults_to_claude():
    assert _coerce_profile({"name": "iriai-claude-1"}).kind == "claude"
    assert _coerce_profile("iriai-claude-2").kind == "claude"
    codex = _coerce_profile({"name": "codex", "kind": "Codex"})
    assert codex.kind == "codex"
    # codex members need no real OS user -> defaults to name.
    assert codex.user == "codex"


# 2a. load_profiles preserves a codex member through migration (no rewrite).
def test_load_profiles_preserves_codex_member_without_rewrite(tmp_path: Path):
    original = {
        "profiles": [
            {"name": "iriai-claude-1", "user": "iriai-claude-1", "weight": 5},
            {"name": "iriai-claude-2", "user": "iriai-claude-2", "weight": 1},
            {"name": "iriai-claude-3", "user": "iriai-claude-3", "weight": 12},
            {"name": "codex", "kind": "codex", "weight": 8},
        ]
    }
    _write_json_atomic(tmp_path / "profiles.json", original)
    before = (tmp_path / "profiles.json").read_text(encoding="utf-8")

    profiles = load_profiles(tmp_path)

    # The legacy 3-claude migration is keyed on exact name-list equality; a
    # codex member makes it never match, so the file is NOT rewritten.
    after = (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert after == before
    by_name = {p.name: p for p in profiles}
    assert by_name["codex"].kind == "codex"
    assert by_name["iriai-claude-1"].weight == 5
    assert by_name["iriai-claude-3"].weight == 12


# 2b. existing legacy 3-claude migration STILL passes (regression).
def test_legacy_three_claude_migration_still_collapses_without_codex(tmp_path: Path):
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
    assert [p.name for p in profiles] == ["iriai-claude-1"]


# 3. classify codex usage/rate/quota RuntimeError strings.
def test_classify_codex_usage_and_transient_strings():
    assert _classify_claude_pool_error(
        "Codex CLI failed with exit code 1: usage limit reached, resets at 5pm"
    ) == "usage_limited"
    assert _classify_claude_pool_error("insufficient_quota for this account") == "usage_limited"
    assert _classify_claude_pool_error("You've reached your usage limit") == "usage_limited"
    assert _classify_claude_pool_error("plan limit exceeded") == "usage_limited"
    assert _classify_claude_pool_error("429 Too Many Requests") == "transient_api_error"
    assert _classify_claude_pool_error("OpenAI rate limit hit") == "transient_api_error"


# 4. codex member gets steady weighted rotation (~1/N, interleaved not tail).
@pytest.mark.asyncio
async def test_codex_member_gets_steady_weighted_rotation(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)

    picked = [
        (await runtime._select_profile(session_key=f"actor-{idx}:feat", persistent=False)).name
        for idx in range(12)
    ]

    # Equal weights, 4 members -> codex gets a steady ~1/4 share.
    assert picked.count("codex") == 3
    # Interleaved, not bunched at the tail: codex appears in the first third.
    assert "codex" in picked[:4]


# 5. codex member cooldown + failover (usage-limit -> mark -> failover to claude).
@pytest.mark.asyncio
async def test_codex_usage_limit_cools_down_and_fails_over_to_claude(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path, poll_interval=0.01)
    # Drive codex to be selected FIRST (least-loaded) by giving every claude
    # member prior dispatch load. All claude members stay available so failover
    # picks one directly without the all-limited probe-recovery cycle.
    _write_json_atomic(
        tmp_path / "affinity.json",
        {
            "next_index": 0,
            "session_profiles": {},
            # Match the live profile weights so _sync_affinity_weights does not
            # reset the dispatch counts we pre-seed below.
            "profile_weights": {
                "iriai-claude-1": 1.0,
                "iriai-claude-2": 1.0,
                "iriai-claude-3": 1.0,
                "codex": 1.0,
            },
            "profile_dispatch_counts": {
                "iriai-claude-1": 50,
                "iriai-claude-2": 50,
                "iriai-claude-3": 50,
            },
        },
    )
    role = Role(name="implementer", prompt="Say ok.", metadata={})

    calls: list[str] = []

    async def _fake_submit_and_wait(*args, **kwargs):
        profile = kwargs["profile"]
        calls.append(profile.name)
        if profile.kind == "codex":
            raise RuntimeError("Codex CLI failed with exit code 1: usage limit reached")
        return ("ok", None, {})

    runtime._submit_and_wait = _fake_submit_and_wait  # type: ignore[method-assign]

    result = await runtime.invoke(
        role,
        "Say ok.",
        workspace=SimpleNamespace(path=tmp_path),
        session_key="implementer:feat-1",
    )

    assert result == "ok"
    # codex was tried first, hit usage limit, got cooled down, then failover
    # routed to an available claude member.
    assert calls[0] == "codex"
    assert calls[-1].startswith("iriai-claude")
    state = json.loads((tmp_path / "profile_state.json").read_text())
    assert state["profiles"]["codex"]["reason"] == "usage_limited"


# 6. all-claude-limited spills to codex (selector returns codex).
@pytest.mark.asyncio
async def test_all_claude_limited_spills_to_codex(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    _write_json_atomic(
        tmp_path / "profile_state.json",
        {
            "profiles": {
                "iriai-claude-1": {"status": "unavailable", "reason": "usage_limited", "probe_after": future},
                "iriai-claude-2": {"status": "unavailable", "reason": "usage_limited", "probe_after": future},
                "iriai-claude-3": {"status": "unavailable", "reason": "usage_limited", "probe_after": future},
            }
        },
    )

    picked = await runtime._select_profile(session_key="actor:feat", persistent=False)

    assert picked.name == "codex"
    assert picked.kind == "codex"


# 7. codex load-score uses in-memory active counter.
def test_codex_load_score_uses_in_memory_active(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    assert runtime._profile_load_score("codex") == 0.0
    runtime._record_codex_dispatch_active("codex", 1)
    assert runtime._profile_load_score("codex") == DEFAULT_ACTIVE_JOB_SPREAD_PENALTY
    runtime._record_codex_dispatch_active("codex", -1)
    assert runtime._profile_load_score("codex") == 0.0
    # Clamped at >= 0.
    runtime._record_codex_dispatch_active("codex", -5)
    assert runtime._profile_load_score("codex") == 0.0


# W-Q: a 7-job wave spreads across codex + claude-1 + claude-2 with NO
# profile excluded for busyness — every member keeps receiving concurrent
# jobs (target shape ~3/2/2 given equal availability and equal weight).
@pytest.mark.asyncio
async def test_seven_job_wave_spreads_concurrently_across_codex_and_claude(
    tmp_path: Path,
) -> None:
    profiles = [
        ClaudePoolProfile(name="codex", user="codex", kind="codex"),
        ClaudePoolProfile(
            name="iriai-claude-1", user="iriai-claude-1", claude_command="/bin/echo"
        ),
        ClaudePoolProfile(
            name="iriai-claude-2", user="iriai-claude-2", claude_command="/bin/echo"
        ),
    ]
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=profiles)
    runtime._codex_runtime = _FakeCodexRuntime()

    picked: list[str] = []
    for idx in range(7):
        profile = await runtime._select_profile(
            session_key=f"actor-{idx}:feat", persistent=False
        )
        picked.append(profile.name)
        # Simulate the dispatched job staying in flight for the whole wave
        # (none complete before the next selection).
        if profile.kind == "codex":
            runtime._record_codex_dispatch_active(profile.name, 1)
        else:
            _write_json_atomic(
                _job_state_path(tmp_path, "queued", profile.name, f"wave-{idx}"),
                {"id": f"wave-{idx}", "status": "queued"},
            )

    counts = {name: picked.count(name) for name in ("codex", "iriai-claude-1", "iriai-claude-2")}
    # Every profile keeps taking concurrent jobs (>= 2 each) — busyness never
    # excludes; the spread is the round-robin target shape 3/2/2.
    assert sum(counts.values()) == 7
    assert all(count >= 2 for count in counts.values()), counts
    assert counts == {"codex": 3, "iriai-claude-1": 2, "iriai-claude-2": 2}


# 7b. dispatching to codex bumps and clears the in-memory active counter.
@pytest.mark.asyncio
async def test_submit_and_wait_codex_tracks_active_and_adapts_return(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    codex_profile = next(p for p in runtime.profiles if p.kind == "codex")
    role = Role(name="reader", prompt="Read.", metadata={})

    text, structured, raw = await runtime._submit_and_wait(
        role,
        "Do it.",
        output_type=None,
        workspace=SimpleNamespace(path=tmp_path),
        session_key="reader:feat-1",
        profile=codex_profile,
    )

    assert text == "ok"
    assert structured is None
    assert raw is None
    # Counter returned to zero after the await completes.
    assert runtime._codex_active.get("codex", 0) == 0
    assert len(runtime._codex_runtime.calls) == 1


@pytest.mark.asyncio
async def test_submit_and_wait_codex_forwards_bound_invocation_to_embedded_runtime(
    tmp_path: Path,
):
    runtime = _runtime_with_fake_codex(tmp_path)
    codex_profile = next(p for p in runtime.profiles if p.kind == "codex")
    role = Role(name="reader", prompt="Read.", metadata={})
    sink = object()

    async with runtime.bind_invocation("inv-codex-1", sink):
        await runtime._submit_and_wait(
            role,
            "Do it.",
            output_type=None,
            workspace=SimpleNamespace(path=tmp_path),
            session_key="reader:feat-1",
            profile=codex_profile,
        )

    assert runtime._codex_runtime.bind_calls == [
        {"invocation_id": "inv-codex-1", "activity_sink": sink}
    ]


def test_invocation_liveness_consults_embedded_codex_runtime(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    runtime._codex_runtime.live_invocations.add("inv-codex-1")

    assert runtime.invocation_has_live_work("inv-codex-1") is True
    assert runtime.invocation_has_live_work("other-invocation") is False


# 7c. structured output from codex is adapted to the (json, dict, None) triple.
@pytest.mark.asyncio
async def test_submit_and_wait_codex_adapts_structured_output(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_codex_profiles())
    runtime._codex_runtime = _FakeCodexRuntime(responses=[_SimpleOutput(message="hello")])
    codex_profile = next(p for p in runtime.profiles if p.kind == "codex")
    role = Role(name="planner", prompt="Plan.", metadata={})

    text, structured, raw = await runtime._submit_and_wait(
        role,
        "Plan it.",
        output_type=_SimpleOutput,
        workspace=SimpleNamespace(path=tmp_path),
        session_key="planner:feat-1",
        profile=codex_profile,
    )

    assert json.loads(text) == {"message": "hello"}
    assert structured == {"message": "hello"}
    assert raw is None


# 8. codex probe recovers dynamically (clears unavailable, no restart).
@pytest.mark.asyncio
async def test_codex_probe_recovers_member_dynamically(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    _write_json_atomic(
        tmp_path / "profile_state.json",
        {"profiles": {"codex": {"status": "unavailable", "reason": "usage_limited", "probe_after": past}}},
    )
    codex_profile = next(p for p in runtime.profiles if p.kind == "codex")

    recovered = await runtime._probe_profile_available(codex_profile)

    assert recovered is True
    state = json.loads((tmp_path / "profile_state.json").read_text())
    assert "codex" not in state.get("profiles", {})
    # The probe ran an in-process codex turn (no job-queue file created).
    assert len(runtime._codex_runtime.calls) == 1
    assert not list((tmp_path / "jobs" / "queued" / "codex").glob("*.json"))


# 8b. a failing codex probe re-marks the member with the classified reason.
@pytest.mark.asyncio
async def test_codex_probe_failure_re_marks_member(tmp_path: Path):
    runtime = ClaudePoolRuntime(root=tmp_path, profiles=_codex_profiles())
    runtime._codex_runtime = _FakeCodexRuntime(
        raises=RuntimeError("Codex CLI failed with exit code 1: usage limit reached")
    )
    codex_profile = next(p for p in runtime.profiles if p.kind == "codex")

    recovered = await runtime._probe_profile_available(codex_profile)

    assert recovered is False
    state = json.loads((tmp_path / "profile_state.json").read_text())
    assert state["profiles"]["codex"]["reason"] == "usage_limited"


# 9. bound write-producing role remains eligible for codex.
@pytest.mark.asyncio
async def test_bound_write_role_can_select_codex_member(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    role = Role(
        name="implementer",
        prompt="Implement.",
        tools=["Write", "Edit"],
        metadata={
            "runtime_workspace_binding": {
                "runtime": "claude_pool",
                "cwd": str(cwd),
                "manifest_path": str(cwd / "sandbox-manifest.json"),
            }
        },
    )
    assert runtime._excluded_kinds_for_role(role) == frozenset()

    # Drive codex to be the least-loaded member (so it WOULD win selection for
    # the bound write role): give every claude member prior dispatch load.
    affinity = {
        "next_index": 0,
        "session_profiles": {},
        "profile_weights": {
            "iriai-claude-1": 1.0,
            "iriai-claude-2": 1.0,
            "iriai-claude-3": 1.0,
            "codex": 1.0,
        },
        "profile_dispatch_counts": {
            "iriai-claude-1": 50,
            "iriai-claude-2": 50,
            "iriai-claude-3": 50,
        },
    }
    _write_json_atomic(tmp_path / "affinity.json", affinity)

    # Unconstrained role -> codex wins (it is the least-loaded member).
    unconstrained = await runtime._select_best_available_profile(affinity, {})
    assert unconstrained.name == "codex"

    # Bound write-producing role -> codex is still eligible; dispatch adapts the
    # role binding immediately before invoking the embedded Codex runtime.
    bound = await runtime._select_best_available_profile(
        affinity, {}, exclude_kinds=runtime._excluded_kinds_for_role(role)
    )
    assert bound.name == "codex"
    assert bound.kind == "codex"


# 9a. codex dispatch rewrites only the binding runtime on the cloned role.
@pytest.mark.asyncio
async def test_submit_and_wait_codex_adapts_binding_runtime_only(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    codex_profile = next(p for p in runtime.profiles if p.kind == "codex")
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    binding = {
        "runtime": "claude_pool",
        "cwd": str(cwd),
        "manifest_path": str(cwd / "sandbox-manifest.json"),
        "sandbox_id": "sandbox-04",
        "writable_roots": [str(cwd / "src")],
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    role = Role(
        name="implementer",
        prompt="Implement.",
        tools=["Write", "Edit"],
        metadata={"runtime_workspace_binding": binding, "write_producing": True},
    )

    await runtime._submit_and_wait(
        role,
        "Do it.",
        output_type=None,
        workspace=SimpleNamespace(path=cwd),
        session_key="implementer:feat-1",
        profile=codex_profile,
    )

    assert len(runtime._codex_runtime.calls) == 1
    passed_role = runtime._codex_runtime.calls[0]["role"]
    passed_binding = passed_role.metadata["runtime_workspace_binding"]
    assert passed_role is not role
    assert passed_binding == {**binding, "runtime": "codex"}
    assert role.metadata["runtime_workspace_binding"] == binding


# 9b. a binding-less write role (planning artifact author) still reaches codex.
def test_binding_less_write_role_does_not_exclude_codex(tmp_path: Path):
    runtime = _runtime_with_fake_codex(tmp_path)
    role = Role(name="architect", prompt="Design.", tools=["Write"], metadata={})
    assert runtime._excluded_kinds_for_role(role) == frozenset()


# 10. factory wiring.
def test_factory_agent_pool_resolves_to_claude_pool_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import iriai_build_v2.runtimes.claude_pool as claude_pool_module
    from iriai_build_v2.runtimes import (
        create_agent_runtime,
        normalize_agent_runtime,
        secondary_agent_runtime_name,
    )

    assert normalize_agent_runtime("agent_pool") == "agent_pool"
    assert normalize_agent_runtime("agent-pool") == "agent_pool"
    assert secondary_agent_runtime_name("agent_pool") == "agent_pool"
    # Its own secondary -> alternation bypassed.
    assert secondary_agent_runtime_name("agent_pool", single_runtime=True) == "agent_pool"

    monkeypatch.setattr(claude_pool_module, "DEFAULT_POOL_ROOT", tmp_path)
    monkeypatch.setenv("IRIAI_CLAUDE_POOL_PROFILES", "iriai-claude-1")
    runtime = create_agent_runtime(
        "agent_pool",
        session_store=None,
        on_message=None,
        interactive_roles=set(),
    )
    assert isinstance(runtime, ClaudePoolRuntime)
    # A pure-claude profiles.json builds no embedded codex runtime.
    assert runtime._codex_runtime is None


# 10b. CLI: --agent-runtime agent_pool parses; --claude-only doesn't error.
def test_cli_agent_pool_and_claude_only_parse():
    from click.testing import CliRunner

    from iriai_build_v2.interfaces.cli.app import cli

    runner = CliRunner()
    # Use --help on the command to exercise option parsing without a live DB.
    result = runner.invoke(cli, ["plan", "--help"])
    assert result.exit_code == 0
    assert "agent_pool" in result.output

    from iriai_build_v2.runtimes import normalize_agent_runtime

    # The claude-only guard must accept agent_pool as a valid primary.
    assert normalize_agent_runtime("agent_pool") in {"claude", "claude_pool", "agent_pool"}
