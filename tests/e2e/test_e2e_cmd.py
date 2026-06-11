"""P5: e2e CLI de-hardcode — profile precedence (--profile-json -> registry ->
studio default), the env-driven live-repo template, and the AC-K-11 studio
non-regression anchor (the electron default profile is unchanged)."""

from __future__ import annotations

import asyncio
import json

import pytest

from iriai_build_v2.interfaces.cli import e2e_cmd
from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile


def test_default_electron_profile_unchanged():
    # AC-K-11 anchor: the studio fallback profile must stay byte-for-byte.
    p = e2e_cmd._default_electron_profile()
    assert p.project_kind == "electron"
    assert p.repo_path == "iriai-studio"
    assert p.adapter_id == "browser"
    assert p.build_cmd == "npm run compile"
    assert p.start_cmd == "./scripts/code.sh"
    assert p.native_test_configs == [
        "playwright.config.badge.ts",
        "playwright.config.chat.ts",
        "playwright.config.lifecycle.ts",
    ]
    assert p.ready_probe_kind == "http_get"
    assert p.ready_probe_target == "http://127.0.0.1:4174"


def test_load_profile_json(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "project_kind": "full_stack", "adapter_id": "compose",
        "repo_path": "kaya-main", "compose_profiles": ["spend-client"],
    }))
    p = e2e_cmd._load_profile_json(str(path))
    assert isinstance(p, ProjectProfile)
    assert p.adapter_id == "compose"
    assert p.compose_profiles == ["spend-client"]


class _Reg:
    def __init__(self, profile):
        self._profile = profile

    async def get_profile(self):
        return self._profile


def test_resolve_profile_precedence(tmp_path):
    override = tmp_path / "p.json"
    override.write_text(json.dumps({"adapter_id": "compose", "repo_path": "kaya-main"}))
    persisted = ProjectProfile(adapter_id="http_service", repo_path="svc")

    # 1) --profile-json wins over the registry.
    p1 = asyncio.run(e2e_cmd._resolve_profile(str(override), _Reg(persisted)))
    assert p1.adapter_id == "compose"
    # 2) else the persisted registry profile.
    p2 = asyncio.run(e2e_cmd._resolve_profile(None, _Reg(persisted)))
    assert p2.adapter_id == "http_service"
    # 3) else the studio electron default (AC-K-11: profile-absent => unchanged).
    p3 = asyncio.run(e2e_cmd._resolve_profile(None, _Reg(None)))
    assert p3.adapter_id == "browser" and p3.repo_path == "iriai-studio"
    # no registry at all -> still the studio default.
    p4 = asyncio.run(e2e_cmd._resolve_profile(None, None))
    assert p4.adapter_id == "browser"


def test_live_repo_path_uses_template(monkeypatch):
    monkeypatch.setattr(e2e_cmd, "_LIVE_REPO_TMPL", "/scratch/{feature}/repos/{repo}")
    assert e2e_cmd._live_repo_path("featX", "kaya-main") == "/scratch/featX/repos/kaya-main"


def _wire_once(monkeypatch, *, pass_behavior):
    """Wire the `e2e --once` path with a fake checkpoint/preflight/pass."""
    from iriai_build_v2.workflows.develop.e2e import checkpoint as cp_mod
    from iriai_build_v2.workflows.develop.e2e import pass_ as pass_mod
    from iriai_build_v2.workflows.develop.e2e import runner_loop as rl
    from iriai_build_v2.workflows.develop.e2e.checkpoint import (
        RepoCheckpoint,
        SealedCheckpoint,
    )

    cursor_writes: list = []

    class _OnceReg:
        async def put_cursor(self, c):
            cursor_writes.append(c)

    async def fake_open_live(feature):
        return None, _OnceReg()

    cp = SealedCheckpoint(
        feature_id="f", group_idx=9,
        repos=[RepoCheckpoint(repo_id="r", repo_path="/x/kaya-main",
                              result_commit="c9")])

    async def fake_fetch(feature, *, dsn=None, max_group_idx=None):
        return cp

    monkeypatch.setattr(e2e_cmd, "_open_live", fake_open_live)
    monkeypatch.setattr(cp_mod, "fetch_latest_sealed_checkpoint", fake_fetch)
    monkeypatch.setattr(
        rl, "host_preflight",
        lambda **k: rl.Preflight(True, 0.0, 8.0, 100.0))
    monkeypatch.setattr(pass_mod, "run_full_pass", pass_behavior)
    return cursor_writes


def test_e2e_once_refused_pass_does_not_write_cursor(monkeypatch):
    # Item-11 G2: a refused compose preflight raises E2EPassRefused — the CLI
    # --once path must NOT consume the sealed checkpoint (no put_cursor).
    from iriai_build_v2.workflows.develop.e2e.pass_ import E2EPassRefused

    async def refused_pass(*a, **kw):
        raise E2EPassRefused("compose preflight refused: single-stack mutex")

    cursor_writes = _wire_once(monkeypatch, pass_behavior=refused_pass)
    asyncio.run(e2e_cmd._e2e("f", loop=False, do_pass=True))
    assert cursor_writes == []  # cursor held — re-run retries the SAME checkpoint


def test_e2e_once_normal_pass_still_writes_cursor(monkeypatch):
    # Regression guard for the fix: the normal path keeps advancing the cursor.
    from iriai_build_v2.workflows.develop.e2e.pass_ import PassSummary

    async def ok_pass(cp, **kw):
        return PassSummary(group_idx=cp.group_idx, boot_smoke="pass")

    cursor_writes = _wire_once(monkeypatch, pass_behavior=ok_pass)
    asyncio.run(e2e_cmd._e2e("f", loop=False, do_pass=True))
    assert len(cursor_writes) == 1
    assert cursor_writes[0].group_idx == 9
    assert cursor_writes[0].last_processed_commit == "c9"
