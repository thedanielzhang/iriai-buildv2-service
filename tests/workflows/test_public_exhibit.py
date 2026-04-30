import json

import pytest
from iriai_compose import Feature

from iriai_build_v2.workflows.public_exhibit import (
    PublicNarrativeBundle,
    PublicSummaryDraft,
    ensure_public_summary_narrative,
    refresh_public_exhibit_narratives,
)


class _MemoryArtifacts:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str, *, feature: Feature | None = None) -> str | None:
        del feature
        return self.values.get(key)

    async def put(self, key: str, value: str, *, feature: Feature | None = None) -> None:
        del feature
        self.values[key] = value


class _Runner:
    def __init__(self, values: dict[str, str] | None = None, bundle: PublicNarrativeBundle | None = None) -> None:
        self.artifacts = _MemoryArtifacts(values)
        self.bundle = bundle or PublicNarrativeBundle()
        self.resolve_calls = 0

    async def resolve(self, *args, **kwargs):
        del args, kwargs
        self.resolve_calls += 1
        return self.bundle


@pytest.fixture
def feature() -> Feature:
    return Feature(
        id="feat-1",
        name="Public Exhibit Feature",
        slug="public-exhibit-feature",
        workflow_name="full-develop",
        workspace_id="main",
    )


@pytest.mark.asyncio
async def test_ensure_public_summary_writes_fallback_without_source_artifacts(feature: Feature):
    runner = _Runner()

    await ensure_public_summary_narrative(runner, feature)

    assert runner.resolve_calls == 0
    saved = json.loads(runner.artifacts.values["public-summary"])
    assert saved["content"]["title"] == "Public Exhibit Feature"
    assert saved["content"]["description"]
    assert saved["provenance"]["source_artifact_keys"] == []


@pytest.mark.asyncio
async def test_ensure_public_summary_skips_existing_complete_summary(feature: Feature):
    existing = {
        "content": {
            "title": "Existing",
            "description": "Already public safe.",
            "current_focus": "Continuing.",
        }
    }
    runner = _Runner({"public-summary": json.dumps(existing)})

    await ensure_public_summary_narrative(runner, feature)

    assert runner.resolve_calls == 0
    assert json.loads(runner.artifacts.values["public-summary"]) == existing


@pytest.mark.asyncio
async def test_ensure_public_summary_replaces_unsafe_existing_summary(feature: Feature):
    existing = {
        "content": {
            "title": "Unsafe",
            "description": "Local path /Users/danielzhang/src/private should not be public.",
            "current_focus": "Leaking internals.",
        }
    }
    runner = _Runner({"public-summary": json.dumps(existing)})

    await ensure_public_summary_narrative(runner, feature)

    saved = json.loads(runner.artifacts.values["public-summary"])
    assert saved["content"]["title"] == "Public Exhibit Feature"
    assert "/Users/" not in json.dumps(saved)


@pytest.mark.asyncio
async def test_refresh_public_summary_fills_sparse_model_output(feature: Feature):
    runner = _Runner(
        {"prd:broad": "## Problem Statement\n\nBuild a public workflow exhibit."},
        bundle=PublicNarrativeBundle(
            public_summary=PublicSummaryDraft(title="Model supplied title"),
        ),
    )

    await refresh_public_exhibit_narratives(
        runner,
        feature,
        reason="test-refresh",
        summary_required=True,
    )

    saved = json.loads(runner.artifacts.values["public-summary"])
    assert saved["content"]["title"] == "Model supplied title"
    assert saved["content"]["description"]
    assert saved["content"]["current_focus"]
    assert saved["provenance"]["source_artifact_keys"] == ["prd:broad"]
