from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_compose import AgentActor, InteractionActor, Role

from iriai_build_v2.services.artifacts import ArtifactMirror
from iriai_build_v2.workflows._common._tasks import HostedInterview
from iriai_build_v2.workflows.planning.phases.plan_review import _load_review_discussion


class _ArtifactStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    async def get(self, key: str, *, feature) -> str | None:
        return self._values.get((feature.id, key))

    async def put(self, key: str, value: str, *, feature) -> None:
        self._values[(feature.id, key)] = value


class _Hosting:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
        self.calls.append((feature_id, key, content, label))
        return f"http://localhost:9000/features/{feature_id}/{key}"


def _interview() -> HostedInterview:
    role = Role(name="reviewer", prompt="Review it.")
    return HostedInterview(
        questioner=AgentActor(name="reviewer", role=role),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan-review-discussion-4",
        artifact_label="Plan Review Discussion — Cycle 4",
    )


@pytest.mark.asyncio
async def test_hosted_interview_persists_file_artifact_to_store(tmp_path: Path):
    feature = SimpleNamespace(id="feat-1", name="Feature")
    artifacts = _ArtifactStore()
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)
    mirror.write_artifact(feature.id, "plan-review-discussion-4", "# discussion")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    await _interview().on_done(
        runner,
        feature,
        result=SimpleNamespace(artifact_path="", output=None),
    )

    assert await artifacts.get("plan-review-discussion-4", feature=feature) == "# discussion"
    assert hosting.calls
    assert hosting.calls[0][1] == "plan-review-discussion-4"


@pytest.mark.asyncio
async def test_load_review_discussion_recovers_from_mirror(tmp_path: Path):
    feature = SimpleNamespace(id="feat-1", name="Feature")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    mirror.write_artifact(feature.id, "plan-review-discussion-4", "# recovered discussion")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
    )

    text = await _load_review_discussion(runner, feature, "plan-review-discussion-4")

    assert text == "# recovered discussion"
    assert await artifacts.get("plan-review-discussion-4", feature=feature) == "# recovered discussion"
