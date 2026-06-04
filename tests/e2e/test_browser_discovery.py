"""Tests for browser-adapter config discovery + notarized real-app gating."""

from __future__ import annotations

import pytest

from iriai_build_v2.workflows.develop.e2e.adapters import Instance
from iriai_build_v2.workflows.develop.e2e.adapters.browser import BrowserAdapter
from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile


def test_discover_configs_finds_all_lanes(tmp_path):
    (tmp_path / "playwright.config.badge.ts").write_text("//")
    (tmp_path / "playwright.config.chat.ts").write_text("//")
    (tmp_path / "test" / "e2e-projectSurface").mkdir(parents=True)
    (tmp_path / "test" / "e2e-projectSurface" / "playwright.config.ts").write_text("//")
    (tmp_path / "playwright" / "planning-phase-view").mkdir(parents=True)
    (tmp_path / "playwright" / "planning-phase-view" / "playwright.config.ts").write_text("//")
    # noise that must NOT be picked up
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "readme.txt").write_text("x")

    found = BrowserAdapter.discover_configs(tmp_path)
    assert "playwright.config.badge.ts" in found
    assert "playwright.config.chat.ts" in found
    assert "test/e2e-projectSurface/playwright.config.ts" in found
    assert "playwright/planning-phase-view/playwright.config.ts" in found
    assert len(found) == 4


@pytest.mark.asyncio
async def test_real_app_e2e_not_applicable_without_dmg(tmp_path, monkeypatch):
    import iriai_build_v2.workflows.develop.e2e.adapters.browser as br
    monkeypatch.setattr(br.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("STUDIO_NOTARIZED_DMG", raising=False)
    adapter = BrowserAdapter()
    inst = Instance(profile=ProjectProfile(adapter_id="browser"), checkout_dir=tmp_path)
    nr = await adapter.run_real_app_e2e(inst)
    assert nr.applicable is False
    assert "DMG_NOT_FOUND" in nr.skip_reason


@pytest.mark.asyncio
async def test_real_app_e2e_not_applicable_off_darwin(tmp_path, monkeypatch):
    import iriai_build_v2.workflows.develop.e2e.adapters.browser as br
    monkeypatch.setattr(br.platform, "system", lambda: "Linux")
    adapter = BrowserAdapter()
    inst = Instance(profile=ProjectProfile(adapter_id="browser"), checkout_dir=tmp_path)
    nr = await adapter.run_real_app_e2e(inst)
    assert nr.applicable is False
    assert "darwin-only" in nr.skip_reason


@pytest.mark.asyncio
async def test_real_app_e2e_discovers_dist_dmg(tmp_path, monkeypatch):
    import iriai_build_v2.workflows.develop.e2e.adapters.browser as br
    monkeypatch.setattr(br.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("STUDIO_NOTARIZED_DMG", raising=False)
    (tmp_path / "dist").mkdir()
    dmg = tmp_path / "dist" / "Iriai-Studio-1.2.3-arm64.dmg"
    dmg.write_text("x")
    # discovery should find exactly one DMG (we don't actually run playwright here)
    assert BrowserAdapter._discover_dmg(tmp_path, None) == str(dmg)
