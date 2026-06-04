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
