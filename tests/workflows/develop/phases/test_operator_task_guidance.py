"""Unit tests for operator-guidance injection into a partial re-dispatch.

An operator answers a task's filed question (a ``partial`` whose only
resolution is operator guidance) by writing the raw guidance text to the
``operator-task-guidance:{task_id}`` artifact. ``_operator_guidance_by_task``
reads that artifact for each task being re-dispatched so the caller can append
the text to that task's ``handover_context`` (mirroring
``commit_hygiene_feedback_by_task``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from iriai_build_v2.workflows.develop.phases.implementation import (
    _operator_guidance_by_task,
)


def run(coro):
    return asyncio.run(coro)


class _FakeArtifacts:
    """In-memory ``runner.artifacts`` keyed by ``(feature_id, key)``.

    Mirrors ``PostgresArtifactStore.get`` for ``str`` values: a ``str`` is
    stored and returned verbatim (raw text, no JSON wrapping). A missing key
    returns ``None`` (falsy), matching the production store.
    """

    def __init__(self, values: dict[tuple[str, str], object] | None = None) -> None:
        self.values = values or {}
        self.raise_on: set[str] = set()

    async def get(self, key: str, *, feature) -> object | None:
        if key in self.raise_on:
            raise RuntimeError("boom")
        return self.values.get((feature.id, key))


def _feature(feature_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=feature_id, slug=feature_id, metadata={})


def _runner(artifacts: _FakeArtifacts) -> SimpleNamespace:
    return SimpleNamespace(artifacts=artifacts)


def test_returns_guidance_text_for_tasks_with_artifact() -> None:
    feature = _feature("feat-1")
    artifacts = _FakeArtifacts(
        {
            ("feat-1", "operator-task-guidance:T-1"): "Use the v2 client API.",
            ("feat-1", "operator-task-guidance:T-3"): "  Skip the legacy shim.  ",
        }
    )
    result = run(
        _operator_guidance_by_task(_runner(artifacts), feature, ["T-1", "T-2", "T-3"])
    )
    # T-1 verbatim, T-3 stripped, T-2 absent -> omitted.
    assert result == {
        "T-1": "Use the v2 client API.",
        "T-3": "Skip the legacy shim.",
    }


def test_missing_artifact_is_omitted() -> None:
    feature = _feature("feat-1")
    artifacts = _FakeArtifacts({})
    result = run(_operator_guidance_by_task(_runner(artifacts), feature, ["T-1"]))
    assert result == {}


def test_empty_or_whitespace_guidance_is_omitted() -> None:
    feature = _feature("feat-1")
    artifacts = _FakeArtifacts(
        {
            ("feat-1", "operator-task-guidance:T-1"): "",
            ("feat-1", "operator-task-guidance:T-2"): "   \n  ",
        }
    )
    result = run(
        _operator_guidance_by_task(_runner(artifacts), feature, ["T-1", "T-2"])
    )
    assert result == {}


def test_read_error_is_treated_as_absent_guidance() -> None:
    feature = _feature("feat-1")
    artifacts = _FakeArtifacts(
        {("feat-1", "operator-task-guidance:T-2"): "Good guidance."}
    )
    artifacts.raise_on.add("operator-task-guidance:T-1")
    result = run(
        _operator_guidance_by_task(_runner(artifacts), feature, ["T-1", "T-2"])
    )
    # T-1 errored (treated as absent), T-2 still resolves.
    assert result == {"T-2": "Good guidance."}


def test_guidance_is_keyed_by_feature_id() -> None:
    # A guidance artifact for another feature must NOT leak into this feature.
    artifacts = _FakeArtifacts(
        {("other-feat", "operator-task-guidance:T-1"): "Wrong feature."}
    )
    result = run(
        _operator_guidance_by_task(_runner(artifacts), _feature("feat-1"), ["T-1"])
    )
    assert result == {}
