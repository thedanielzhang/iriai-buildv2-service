"""Unit tests for the cache-first ProjectProfile resolver used by sandbox
provisioning (`_resolve_project_profile`). Pure in-memory — no DB, no agent run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from iriai_compose import Feature

from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile
from iriai_build_v2.workflows.develop.phases.implementation import (
    _resolve_project_profile,
)


def _feature() -> Feature:
    return Feature(
        id="testfeat",
        name="t",
        slug="t",
        workflow_name="full-develop",
        workspace_id="main",
    )


class _FakeArtifacts:
    def __init__(self, stored: dict[str, Any]) -> None:
        self._stored = stored

    async def get(self, key: str, feature: Any = None) -> Any:
        return self._stored.get(key)

    async def put(self, key: str, value: Any, feature: Any = None) -> None:
        self._stored[key] = value


class _Runner:
    def __init__(self, artifacts: Any) -> None:
        self.artifacts = artifacts

    async def run(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("inference must not run when a cached profile exists")


def test_cache_hit_returns_profile_without_inferring() -> None:
    prof = ProjectProfile(
        project_kind="full_stack",
        adapter_id="compose",
        package_roots=["spend-client"],
        package_managers=["pnpm"],
    )
    runner = _Runner(_FakeArtifacts({"project-profile": prof.model_dump()}))
    out = asyncio.run(
        _resolve_project_profile(runner, _feature(), Path("/tmp/x"), "kaya-main")
    )
    assert out is not None
    assert out.package_managers == ["pnpm"]
    assert out.adapter_id == "compose"


def test_missing_artifacts_returns_none() -> None:
    class _NoArtifacts:
        pass

    out = asyncio.run(
        _resolve_project_profile(_NoArtifacts(), _feature(), Path("/tmp/x"), "r")
    )
    assert out is None
