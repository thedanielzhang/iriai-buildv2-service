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
async def test_done_terminates_on_written_artifact_despite_lingering_question(
    tmp_path: Path,
):
    # Regression for the Broad Design non-termination halt (feature d31adf8d): a
    # degraded completion turn (structured_output None -> session cycle -> the
    # agent loses turn-1 context and keeps re-asking for it) must NOT re-open a
    # finished interview whose artifact is ALREADY written. Completion is detected
    # by the written+ready deliverable — interviews are NEVER turn-bounded.
    feature = SimpleNamespace(id="feat-design-halt", name="Design")
    mirror = ArtifactMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=_ArtifactStore(),
        services={"hosting": _Hosting(), "artifact_mirror": mirror},
    )
    interview = HostedInterview(
        questioner=AgentActor(name="designer", role=Role(name="designer", prompt="Design.")),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: False,  # base predicate never fires on its own
        artifact_key="broad-design",
        artifact_label="Broad Design",
    )
    await interview.on_start(runner, feature)
    staging = interview._artifact_output_paths["broad-design"]

    # Healthy mid-interview: a pending question with NO artifact yet keeps going.
    assert interview.done(
        SimpleNamespace(question="which button variant?", output=None, artifact_path="")
    ) is False

    # The agent writes its final artifact (a NEW file), then its completion turn
    # is degraded and carries a stale question. The interview must terminate.
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text("# design system", encoding="utf-8")
    assert interview.done(
        SimpleNamespace(
            question="I'm paused awaiting the mode + PRD + output path",
            output=None,
            artifact_path="",
        )
    ) is True


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


# ── W-11 stub-render regression (gate-review verdicts clobbered by the empty
#    decision-ledger placeholder) ─────────────────────────────────────────────


def _empty_ledger_stub() -> str:
    """The exact placeholder render that clobbered gate-review artifacts."""
    from iriai_build_v2.models.outputs import DecisionLedger
    from iriai_build_v2.services.markdown import to_markdown

    return to_markdown(DecisionLedger())


def _gate_review_interview() -> HostedInterview:
    from iriai_build_v2.models.outputs import ReviewOutcome  # noqa: F401

    role = Role(name="gate-reviewer", prompt="Review it.")
    return HostedInterview(
        questioner=AgentActor(name="gate-reviewer", role=role),
        responder=InteractionActor(name="user", resolver="terminal"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="gate-review:plan",
        artifact_label="Gate Review — plan",
    )


def test_display_content_passes_gate_review_verdict_through_verbatim():
    """Root cause 1: hosting display conversion rendered any verdict JSON as
    the empty DecisionLedger stub and wrote it over the gate-review mirror."""
    import json as _json

    from iriai_build_v2.models.outputs import ReviewOutcome

    verdict = ReviewOutcome(approved=True, complete=True).model_dump_json(indent=2)
    out = DocHostingService._to_display_content(verdict, "gate-review:plan")
    assert out == verdict
    assert _json.loads(out)["approved"] is True


def test_display_content_never_guesses_all_default_decision_ledger():
    """Root cause 1b: the model-guessing fallback must not match a model whose
    only content is a default field (DecisionLedger validates ANY object)."""
    out = DocHostingService._to_display_content('{"some": "payload"}', "unmapped-key")
    assert "_No decisions recorded yet._" not in out
    assert out.startswith("```json")


@pytest.mark.asyncio
async def test_gate_review_on_done_ignores_stub_mirror_and_persists_verdict(tmp_path: Path):
    """Root cause 2 (re-ingestion): a prior display-clobbered mirror file
    (reviews/plan-gate-review.md == empty-ledger stub) must be treated as
    missing, so on_done falls back to the structured Envelope verdict.

    Reproduces live rows 2245985/2246025 (47-byte stubs persisted to the DB)
    and proves the persisted artifact is now a readable verdict.
    """
    import json as _json

    from iriai_build_v2.models.outputs import ReviewOutcome
    from iriai_build_v2.workflows._common._helpers import _gate_review_is_approved

    feature = SimpleNamespace(id="feat-gate-stub", name="Feature")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    hosting = DocHostingService(mirror)  # real service: writes the mirror file

    # Simulate the prior cycle's clobber: final mirror holds the stub, and a
    # stale staging draft holds the stub too. No fresh agent-written file.
    stub = _empty_ledger_stub()
    final_path = mirror.feature_dir(feature.id) / "reviews" / "plan-gate-review.md"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(stub, encoding="utf-8")
    staging_path = (
        mirror.feature_dir(feature.id) / ".staging" / "reviews" / "plan-gate-review.md"
    )
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_text(stub, encoding="utf-8")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": hosting, "artifact_mirror": mirror},
    )

    interview = _gate_review_interview()
    await interview.on_start(runner, feature)
    verdict = ReviewOutcome(approved=True, complete=True)
    await interview.on_done(
        runner,
        feature,
        result=SimpleNamespace(artifact_path="", output=verdict),
    )

    stored = await artifacts.get("gate-review:plan", feature=feature)
    assert stored is not None
    assert "_No decisions recorded yet._" not in stored
    assert _json.loads(stored)["approved"] is True
    assert _gate_review_is_approved(stored) is True

    # The hosted mirror file must now hold the verbatim verdict, not the stub
    # (this is what the resume fast-path reads via get_resumable_artifact).
    mirrored = final_path.read_text(encoding="utf-8")
    assert "_No decisions recorded yet._" not in mirrored
    assert _json.loads(mirrored)["approved"] is True


@pytest.mark.asyncio
async def test_ensure_gate_verdict_persisted_replaces_stub_everywhere(tmp_path: Path):
    """Backstop: stub/empty DB row + mirror + staging are replaced by the
    structured verdict JSON; the resume fast-path then reads approval."""
    import json as _json

    from iriai_build_v2.models.outputs import ReviewOutcome
    from iriai_build_v2.workflows._common._helpers import (
        _ensure_gate_verdict_persisted,
        _gate_review_is_approved,
    )

    feature = SimpleNamespace(id="feat-gate-backstop", name="Feature")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": _Hosting(), "artifact_mirror": mirror},
    )

    stub = _empty_ledger_stub()
    await artifacts.put("gate-review:plan", stub, feature=feature)
    final_path = mirror.feature_dir(feature.id) / "reviews" / "plan-gate-review.md"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(stub, encoding="utf-8")
    staging_path = (
        mirror.feature_dir(feature.id) / ".staging" / "reviews" / "plan-gate-review.md"
    )
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_text(stub, encoding="utf-8")

    outcome = ReviewOutcome(approved=True, complete=True)
    await _ensure_gate_verdict_persisted(
        runner, feature, gate_review_key="gate-review:plan", outcome=outcome
    )

    stored = await artifacts.get("gate-review:plan", feature=feature)
    assert _json.loads(stored)["approved"] is True
    assert _gate_review_is_approved(stored) is True
    assert _json.loads(final_path.read_text(encoding="utf-8"))["approved"] is True
    assert not staging_path.exists()  # stale stub draft no longer shadows resume


@pytest.mark.asyncio
async def test_ensure_gate_verdict_persisted_overwrites_contradicting_stale_verdict(
    tmp_path: Path,
):
    """W-11b: a stale real ``approved=false`` JSON verdict in the DB row,
    mirror, and staging (the previous cycle's verdict, re-ingested over the
    new approval) is overwritten by the authoritative in-process outcome."""
    import json as _json

    from iriai_build_v2.models.outputs import ReviewOutcome
    from iriai_build_v2.workflows._common._helpers import (
        _ensure_gate_verdict_persisted,
        _gate_review_is_approved,
    )

    feature = SimpleNamespace(id="feat-gate-stale", name="Feature")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": _Hosting(), "artifact_mirror": mirror},
    )

    stale = _json.dumps(
        {"approved": False, "revision_plan": "Fix AC-3 coverage.", "complete": True},
        indent=2,
    )
    await artifacts.put("gate-review:plan", stale, feature=feature)
    final_path = mirror.feature_dir(feature.id) / "reviews" / "plan-gate-review.md"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(stale, encoding="utf-8")
    staging_path = (
        mirror.feature_dir(feature.id) / ".staging" / "reviews" / "plan-gate-review.md"
    )
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_text(stale, encoding="utf-8")

    outcome = ReviewOutcome(approved=True, complete=True)
    await _ensure_gate_verdict_persisted(
        runner, feature, gate_review_key="gate-review:plan", outcome=outcome
    )

    stored = await artifacts.get("gate-review:plan", feature=feature)
    assert _json.loads(stored)["approved"] is True
    assert _gate_review_is_approved(stored) is True
    assert _json.loads(final_path.read_text(encoding="utf-8"))["approved"] is True
    assert not staging_path.exists()  # stale contradicting draft no longer shadows


@pytest.mark.asyncio
async def test_ensure_gate_verdict_persisted_keeps_matching_verdict(tmp_path: Path):
    """A stored JSON verdict whose ``approved`` MATCHES the in-process outcome
    is left byte-identical (richer stored JSON must not be replaced)."""
    import json as _json

    from iriai_build_v2.models.outputs import ReviewOutcome
    from iriai_build_v2.workflows._common._helpers import _ensure_gate_verdict_persisted

    feature = SimpleNamespace(id="feat-gate-match", name="Feature")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": _Hosting(), "artifact_mirror": mirror},
    )

    matching = _json.dumps(
        {"approved": True, "notes": "Richer verdict from the reviewer."}, indent=2
    )
    await artifacts.put("gate-review:plan", matching, feature=feature)
    final_path = mirror.feature_dir(feature.id) / "reviews" / "plan-gate-review.md"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(matching, encoding="utf-8")

    await _ensure_gate_verdict_persisted(
        runner,
        feature,
        gate_review_key="gate-review:plan",
        outcome=ReviewOutcome(approved=True, complete=True),
    )

    assert await artifacts.get("gate-review:plan", feature=feature) == matching
    assert final_path.read_text(encoding="utf-8") == matching


@pytest.mark.asyncio
async def test_ensure_gate_verdict_persisted_never_overwrites_real_review(tmp_path: Path):
    """The backstop must not clobber a real reviewer-written gate review."""
    from iriai_build_v2.models.outputs import ReviewOutcome
    from iriai_build_v2.workflows._common._helpers import _ensure_gate_verdict_persisted

    feature = SimpleNamespace(id="feat-gate-keep", name="Feature")
    artifacts = _ArtifactStore()
    mirror = ArtifactMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": _Hosting(), "artifact_mirror": mirror},
    )

    review_md = "# Plan Gate Review\n\n**Outcome:** approved\n\nDetailed reasoning."
    await artifacts.put("gate-review:plan", review_md, feature=feature)
    final_path = mirror.feature_dir(feature.id) / "reviews" / "plan-gate-review.md"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(review_md, encoding="utf-8")

    await _ensure_gate_verdict_persisted(
        runner,
        feature,
        gate_review_key="gate-review:plan",
        outcome=ReviewOutcome(approved=True, complete=True),
    )

    assert await artifacts.get("gate-review:plan", feature=feature) == review_md
    assert final_path.read_text(encoding="utf-8") == review_md
