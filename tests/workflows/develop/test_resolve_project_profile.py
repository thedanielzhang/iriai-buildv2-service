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
    _commit_hygiene_strategy_of,
    _resolve_project_profile,
    _resolve_project_profile_cached,
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


# --- P3: cache-only resolver + strategy accessor -------------------------------


def test_cached_resolver_reads_profile_without_source_or_repo() -> None:
    prof = ProjectProfile(commit_hygiene_strategy="restage_autofix")
    runner = _Runner(_FakeArtifacts({"project-profile": prof.model_dump()}))
    out = asyncio.run(_resolve_project_profile_cached(runner, _feature()))
    assert out is not None
    assert out.commit_hygiene_strategy == "restage_autofix"


def test_cached_resolver_missing_artifacts_returns_none() -> None:
    class _NoArtifacts:
        pass

    out = asyncio.run(_resolve_project_profile_cached(_NoArtifacts(), _feature()))
    assert out is None


def test_cached_resolver_empty_store_returns_none() -> None:
    runner = _Runner(_FakeArtifacts({}))
    out = asyncio.run(_resolve_project_profile_cached(runner, _feature()))
    assert out is None


def test_commit_hygiene_strategy_of() -> None:
    # Absent profile / unset field => the studio rule_grant default ("").
    assert _commit_hygiene_strategy_of(None) == ""
    assert _commit_hygiene_strategy_of(ProjectProfile()) == ""
    assert (
        _commit_hygiene_strategy_of(
            ProjectProfile(commit_hygiene_strategy="restage_autofix")
        )
        == "restage_autofix"
    )
    # Tolerant of whitespace / duck-typed objects.
    assert (
        _commit_hygiene_strategy_of(
            ProjectProfile(commit_hygiene_strategy="  rule_grant  ")
        )
        == "rule_grant"
    )
