from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_compose import to_str
from iriai_compose import AgentActor, InteractionActor, Role

from iriai_build_v2.services.artifacts import ArtifactMirror
from iriai_build_v2.services.hosting import DocHostingService
from iriai_build_v2.models.outputs import (
    ArchitectureOutput,
    Subfeature,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
)
from iriai_build_v2.workflows._common._helpers import (
    get_existing_artifact,
    get_gate_resume_artifact,
)
from iriai_build_v2.workflows._common._tasks import HostedInterview
from iriai_build_v2.workflows.planning.phases.plan_review import _load_review_discussion


class _ArtifactStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    async def get(self, key: str, *, feature) -> str | None:
        return self._values.get((feature.id, key))

    async def put(self, key: str, value: str, *, feature) -> None:
        self._values[(feature.id, key)] = value

    async def delete(self, key: str, *, feature) -> None:
        self._values.pop((feature.id, key), None)


class _FailingArtifactStore(_ArtifactStore):
    def __init__(self, *, fail_on_key: str) -> None:
        super().__init__()
        self._fail_on_key = fail_on_key

    async def put(self, key: str, value: str, *, feature) -> None:
        if key == self._fail_on_key:
            raise RuntimeError(f"artifact-store failed for {key}")
        await super().put(key, value, feature=feature)


class _Hosting:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
        self.calls.append((feature_id, key, content, label))
        return f"http://localhost:9000/features/{feature_id}/{key}"

    async def delete(self, feature_id: str, key: str) -> None:
        self.deleted.append((feature_id, key))


class _FailingDocHostingService(DocHostingService):
    def __init__(self, mirror: ArtifactMirror, *, fail_on_key: str) -> None:
        super().__init__(mirror)
        self.fail_on_key = fail_on_key

    async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
        url = await super().push(feature_id, key, content, label)
        if key == self.fail_on_key:
            raise RuntimeError(f"hosting failed for {key}")
        return url


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
    stage_path = mirror.feature_dir(feature.id) / ".staging" / "plan-review-discussion-4.md"
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.write_text("# discussion", encoding="utf-8")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = _interview()
    await interview.on_start(runner, feature)
    await interview.on_done(
        runner,
        feature,
        result=SimpleNamespace(artifact_path="", output=None),
    )

    assert await artifacts.get("plan-review-discussion-4", feature=feature) == "# discussion"
    assert hosting.calls
    assert hosting.calls[0][1] == "plan-review-discussion-4"


@pytest.mark.asyncio
async def test_hosted_interview_prefers_structured_output_when_requested(tmp_path: Path):
    feature = SimpleNamespace(id="feat-structured", name="Feature")
    artifacts = _ArtifactStore()
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)
    stage_path = mirror.feature_dir(feature.id) / ".staging" / "decomposition.md"
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.write_text("# markdown decomposition", encoding="utf-8")
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="reviewer", role=Role(name="reviewer", prompt="Review it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="decomposition",
        artifact_label="Subfeature Decomposition",
        prefer_structured_output=True,
    )
    await interview.on_start(runner, feature)
    await interview.on_done(
        runner,
        feature,
        result=SimpleNamespace(artifact_path=str(stage_path), output=decomposition),
    )

    expected = to_str(decomposition)
    assert await artifacts.get("decomposition", feature=feature) == expected
    assert hosting.calls
    assert hosting.calls[0][2] == expected


@pytest.mark.asyncio
async def test_hosted_interview_uses_staging_paths_in_prompt(tmp_path: Path):
    feature = SimpleNamespace(id="feat-1", name="Feature")
    artifacts = _ArtifactStore()
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = _interview()
    await interview.on_start(runner, feature)

    assert ".staging/plan-review-discussion-4.md" in interview.initial_prompt


@pytest.mark.asyncio
async def test_hosted_interview_done_requires_all_declared_artifacts(tmp_path: Path):
    feature = SimpleNamespace(id="feat-arch-done", name="Architecture")
    artifacts = _ArtifactStore()
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await interview.on_start(runner, feature)

    primary_path = mirror.feature_dir(feature.id) / ".staging" / "subfeatures" / "billing" / "plan.md"
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    primary_path.write_text("# plan", encoding="utf-8")

    assert (
        interview.done(
            SimpleNamespace(
                question="",
                output=None,
                artifact_path=str(primary_path),
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_hosted_interview_requires_all_declared_artifacts_before_persisting(tmp_path: Path):
    feature = SimpleNamespace(id="feat-arch", name="Architecture")
    artifacts = _ArtifactStore()
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await interview.on_start(runner, feature)

    plan_path = mirror.feature_dir(feature.id) / ".staging" / "subfeatures" / "billing" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# plan", encoding="utf-8")

    with pytest.raises(RuntimeError, match="required additional artifact 'system-design:billing'"):
        await interview.on_done(
            runner,
            feature,
            result=SimpleNamespace(artifact_path=str(plan_path), output=None),
        )

    assert await artifacts.get("plan:billing", feature=feature) is None
    assert await artifacts.get("system-design:billing", feature=feature) is None
    assert hosting.calls == []


@pytest.mark.asyncio
async def test_hosted_interview_persists_multi_artifact_structured_output_atomically(tmp_path: Path):
    feature = SimpleNamespace(id="feat-arch-output", name="Architecture")
    artifacts = _ArtifactStore()
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await interview.on_start(runner, feature)

    output = ArchitectureOutput(
        plan=TechnicalPlan(architecture="Introduce a billing orchestration layer.", complete=True),
        system_design=SystemDesign(title="Billing System Design", overview="Billing services and flows.", complete=True),
        complete=True,
    )

    await interview.on_done(
        runner,
        feature,
        result=SimpleNamespace(artifact_path="", output=output),
    )

    assert await artifacts.get("plan:billing", feature=feature) == to_str(output.plan)
    assert await artifacts.get("system-design:billing", feature=feature) == to_str(output.system_design)
    assert [call[1] for call in hosting.calls] == ["plan:billing", "system-design:billing"]


@pytest.mark.asyncio
async def test_hosted_interview_rolls_back_if_later_artifact_store_write_fails(tmp_path: Path):
    feature = SimpleNamespace(id="feat-arch-put-fail", name="Architecture")
    artifacts = _FailingArtifactStore(fail_on_key="system-design:billing")
    hosting = _Hosting()
    mirror = ArtifactMirror(tmp_path)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await interview.on_start(runner, feature)

    output = ArchitectureOutput(
        plan=TechnicalPlan(architecture="Plan text", complete=True),
        system_design=SystemDesign(title="SD", overview="System design", complete=True),
        complete=True,
    )

    with pytest.raises(RuntimeError, match="artifact-store failed for system-design:billing"):
        await interview.on_done(
            runner,
            feature,
            result=SimpleNamespace(artifact_path="", output=output),
        )

    assert await artifacts.get("plan:billing", feature=feature) is None
    assert await artifacts.get("system-design:billing", feature=feature) is None
    assert hosting.calls == []


@pytest.mark.asyncio
async def test_hosted_interview_rolls_back_store_and_hosting_if_later_hosting_push_fails(tmp_path: Path):
    feature = SimpleNamespace(id="feat-arch-host-fail", name="Architecture")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    hosting = _FailingDocHostingService(mirror, fail_on_key="system-design:billing")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await interview.on_start(runner, feature)

    output = ArchitectureOutput(
        plan=TechnicalPlan(architecture="Plan text", complete=True),
        system_design=SystemDesign(title="SD", overview="System design", complete=True),
        complete=True,
    )

    with pytest.raises(RuntimeError, match="hosting failed for system-design:billing"):
        await interview.on_done(
            runner,
            feature,
            result=SimpleNamespace(artifact_path="", output=output),
        )

    assert await artifacts.get("plan:billing", feature=feature) is None
    assert await artifacts.get("system-design:billing", feature=feature) is None
    assert await get_existing_artifact(runner, feature, "plan:billing") is None
    assert await get_gate_resume_artifact(runner, feature, "plan:billing") is None


@pytest.mark.asyncio
async def test_hosted_interview_retry_succeeds_after_prior_hosting_failure(tmp_path: Path):
    feature = SimpleNamespace(id="feat-arch-retry", name="Architecture")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    failing_hosting = _FailingDocHostingService(mirror, fail_on_key="system-design:billing")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": failing_hosting, "artifact_mirror": mirror},
    )

    interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await interview.on_start(runner, feature)

    output = ArchitectureOutput(
        plan=TechnicalPlan(architecture="Plan text", complete=True),
        system_design=SystemDesign(title="SD", overview="System design", complete=True),
        complete=True,
    )

    with pytest.raises(RuntimeError):
        await interview.on_done(
            runner,
            feature,
            result=SimpleNamespace(artifact_path="", output=output),
        )

    succeeding_hosting = _Hosting()
    runner.services["hosting"] = succeeding_hosting
    retry_interview = HostedInterview(
        questioner=AgentActor(name="architect", role=Role(name="architect", prompt="Design it.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:billing",
        artifact_label="Architecture — Billing",
        additional_artifact_keys=["system-design:billing"],
    )
    await retry_interview.on_start(runner, feature)
    await retry_interview.on_done(
        runner,
        feature,
        result=SimpleNamespace(artifact_path="", output=output),
    )

    assert await artifacts.get("plan:billing", feature=feature) == to_str(output.plan)
    assert await artifacts.get("system-design:billing", feature=feature) == to_str(output.system_design)
    assert [call[1] for call in succeeding_hosting.calls] == ["plan:billing", "system-design:billing"]


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


@pytest.mark.asyncio
async def test_rehost_existing_registers_nested_broad_artifact_urls(tmp_path: Path):
    mirror = ArtifactMirror(tmp_path)
    feature_id = "feat-1"
    mirror.write_artifact(feature_id, "prd:broad", "# broad prd")
    mirror.write_artifact(feature_id, "decisions:broad", "# decisions")

    hosting = DocHostingService(mirror)

    count = await hosting.rehost_existing(feature_id, label_prefix="Feature — ")

    assert count == 2
    assert hosting.get_url("prd:broad") == f"http://localhost:9000/features/{feature_id}/prd:broad"
    assert hosting.get_url("decisions:broad") == f"http://localhost:9000/features/{feature_id}/decisions:broad"


@pytest.mark.asyncio
async def test_doc_hosting_renders_decomposition_as_markdown(tmp_path: Path):
    mirror = ArtifactMirror(tmp_path)
    hosting = DocHostingService(mirror)
    feature_id = "feat-decomp"
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )

    await hosting.push(
        feature_id,
        "decomposition",
        decomposition.model_dump_json(),
        "Subfeature Decomposition — Feature",
    )

    rendered = (mirror.feature_dir(feature_id) / "decomposition.md").read_text(encoding="utf-8")
    assert rendered.startswith("# Subfeature Decomposition")
    assert "`accounts`" in rendered
    assert "## Complete" in rendered
