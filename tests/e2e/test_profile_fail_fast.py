"""Item-6: e2e ProjectProfile fail-fast (IRIAI_E2E_REQUIRE_PROFILE).

Flag OFF (default/unset) must be today's behavior exactly: the resolution
chain profile -> registry profile -> hardcoded iriai-studio electron default,
and the CLI registry-open degradation to (None, None) with a [warn].

Flag ON: a missing or structurally-invalid profile is a loud typed error
(E2EProfileRequiredError / ClickException) — never the studio default. The
develop-side auto-infer flow (implementation._resolve_project_profile) is the
designed inference path and is intentionally untouched by this flag.
"""

from __future__ import annotations

import asyncio

import click
import pytest

from iriai_build_v2.interfaces.cli import e2e_cmd
from iriai_build_v2.workflows.develop.e2e import pass_ as pass_mod
from iriai_build_v2.workflows.develop.e2e.checkpoint import (
    RepoCheckpoint,
    SealedCheckpoint,
)
from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile

FLAG = "IRIAI_E2E_REQUIRE_PROFILE"


def _checkpoint() -> SealedCheckpoint:
    return SealedCheckpoint(
        feature_id="feat-x",
        group_idx=1,
        repos=[RepoCheckpoint(repo_id="r", repo_path="/x/kaya-main",
                              result_commit="abc123")],
    )


class _Reg:
    def __init__(self, profile):
        self._profile = profile

    async def get_profile(self):
        return self._profile


def _misaligned_profile() -> ProjectProfile:
    # service_names/languages/test_cmds are index-aligned parallel lists;
    # mismatched lengths => alignment_errors() non-empty.
    return ProjectProfile(
        adapter_id="compose", repo_path="kaya-main",
        service_names=["api", "web"], service_languages=["python"],
        service_test_cmds=[],
    )


def _run_pass(registry) -> object:
    return asyncio.run(pass_mod.run_full_pass(
        _checkpoint(), feature_id="feat-x", registry=registry,
        live_dsn="postgresql://unused",
    ))


# ── run_full_pass (the pass-level resolution chain) ─────────────────────────


def test_on_missing_profile_raises_typed_before_any_provisioning(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(pass_mod.E2EProfileRequiredError) as excinfo:
        _run_pass(_Reg(None))
    msg = str(excinfo.value)
    assert "feat-x" in msg and "P6" in msg and "profile-json" in msg


def test_on_no_registry_raises_typed(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(pass_mod.E2EProfileRequiredError):
        _run_pass(None)


def test_on_misaligned_profile_raises_with_errors(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(pass_mod.E2EProfileRequiredError) as excinfo:
        _run_pass(_Reg(_misaligned_profile()))
    assert "structurally invalid" in str(excinfo.value)


def test_off_missing_profile_falls_back_to_studio_default(monkeypatch):
    """Parity: flag OFF resolves the studio default and proceeds into the
    studio path (which immediately fails on the nonexistent clone source —
    proving the default WAS selected, exactly today's behavior)."""
    monkeypatch.delenv(FLAG, raising=False)
    seen = {}

    def fake_default():
        seen["used"] = True
        return ProjectProfile(
            project_kind="electron", repo_path="iriai-studio",
            adapter_id="browser",
        )

    monkeypatch.setattr(pass_mod, "_default_profile", fake_default)

    class _Boom(Exception):
        pass

    class FakeSubstrate:
        def __init__(self, *a, **k):
            pass

        async def clone_checkpoint(self, *a, **k):
            raise _Boom("studio clone attempted")

    monkeypatch.setattr(pass_mod, "CloneSubstrate", FakeSubstrate)
    with pytest.raises(_Boom):
        _run_pass(_Reg(None))
    assert seen.get("used") is True


def test_live_repo_tmpl_env_override(monkeypatch):
    # Default (env unset at import time) is the previous literal byte-for-byte.
    assert "visual-studio-code-frontend-for-project-workflow-manager" in (
        pass_mod.LIVE_REPO_TMPL
    )
    monkeypatch.setattr(
        pass_mod, "LIVE_REPO_TMPL", "/scratch/{feature}/repos/{repo}",
    )
    assert pass_mod._live_repo("fX", "iriai-studio") == "/scratch/fX/repos/iriai-studio"


# ── CLI resolution (_resolve_profile / _open_live) ──────────────────────────


def test_cli_off_resolves_studio_default(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    profile = asyncio.run(e2e_cmd._resolve_profile(None, _Reg(None)))
    assert profile.adapter_id == "browser" and profile.repo_path == "iriai-studio"


def test_cli_on_missing_profile_raises(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(click.ClickException, match="no project-profile artifact"):
        asyncio.run(e2e_cmd._resolve_profile(None, _Reg(None)))
    with pytest.raises(click.ClickException):
        asyncio.run(e2e_cmd._resolve_profile(None, None))


def test_cli_on_valid_profile_resolves(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    persisted = ProjectProfile(adapter_id="http_service", repo_path="svc")
    profile = asyncio.run(e2e_cmd._resolve_profile(None, _Reg(persisted)))
    assert profile.adapter_id == "http_service"


def test_cli_on_misaligned_profile_raises(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(click.ClickException, match="structurally invalid"):
        asyncio.run(e2e_cmd._resolve_profile(None, _Reg(_misaligned_profile())))


def test_cli_on_profile_json_still_wins(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv(FLAG, "1")
    override = tmp_path / "p.json"
    override.write_text(json.dumps({
        "adapter_id": "compose", "repo_path": "kaya-main",
    }))
    profile = asyncio.run(e2e_cmd._resolve_profile(str(override), _Reg(None)))
    assert profile.adapter_id == "compose"


def test_open_live_on_registry_error_raises(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setattr(e2e_cmd, "LIVE_DSN", "postgresql://nonexistent-host:1/x")
    with pytest.raises(click.ClickException, match="registry unavailable"):
        asyncio.run(e2e_cmd._open_live("feat-x"))


def test_open_live_off_registry_error_degrades(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(e2e_cmd, "LIVE_DSN", "postgresql://nonexistent-host:1/x")
    pool, registry = asyncio.run(e2e_cmd._open_live("feat-x"))
    assert pool is None and registry is None
