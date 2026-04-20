import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose import Ask

from iriai_build_v2.services.artifacts import _key_to_path, _sd_source_path
from iriai_build_v2.services.hosting import DocHostingService
from iriai_build_v2.services.markdown import to_markdown
from iriai_build_v2.models.outputs import (
    ArchitectureOutput,
    ArtifactPatchSet,
    Envelope,
    IntegrationReview,
    PRD,
    ProjectContext,
    RepoSpec,
    RevisionPlan,
    RevisionRequest,
    ReviewOutcome,
    ScopeOutput,
    Subfeature,
    SubfeatureDecomposition,
    SubfeatureEdge,
    SystemDesign,
    TechnicalPlan,
    Verdict,
    envelope_done,
)
from iriai_build_v2.models.state import BuildState
from iriai_build_v2.workflows.develop.workflow import FullDevelopWorkflow
from iriai_build_v2.workflows.planning._control import (
    STEP_AGENT_FILL,
    STEP_COMPLETE,
    STEP_PENDING,
    STEP_RUNNING,
    default_planning_control,
    ensure_subfeature_threads,
    sync_subfeature_threads,
    set_background_state,
    set_step_status,
)
from iriai_build_v2.workflows.planning.phases import (
    BroadPhase,
    PlanReviewPhase,
    ScopingPhase,
    SubfeaturePhase,
    TaskPlanningPhase,
)
from iriai_build_v2.workflows.planning.phases.pm import PMPhase
from iriai_build_v2.workflows.planning.phases.design import DesignPhase
from iriai_build_v2.workflows.planning.phases.architecture import ArchitecturePhase
from iriai_build_v2.workflows.planning.phases.broad import (
    _collect_subfeature_step_policies,
    _apply_broad_reconciliation_revisions,
    _revise_broad_artifact_from_reconciliation,
    _revise_decomposition_from_reconciliation,
    _run_broad_artifact_stage,
    _run_decomposition_stage,
)
from iriai_build_v2.workflows.planning.phases.subfeature import (
    _architecture_prompt,
    _design_prompt,
    _pm_prompt,
    _reset_stale_background_state,
    _run_global_architecture_tail,
    _run_global_design_tail,
    _run_global_prd_tail,
    _run_design_step,
    _run_pm_step,
    _run_architecture_step,
    _run_test_planning_step,
    _step_ready,
    _test_planning_prompt,
)
from iriai_build_v2.workflows._common._helpers import (
    _apply_patches,
    _clear_agent_session,
    _build_subfeature_context,
    _offload_if_large,
    decompose_and_gate,
    get_existing_artifact,
    get_gate_resume_artifact,
    get_resumable_artifact,
    interview_gate_review,
    integration_review,
    targeted_revision,
)
from iriai_build_v2.workflows._common._autonomy import interaction_actor_for_phase
from iriai_build_v2.workflows._common._tasks import HostedInterview
from iriai_build_v2.workflows.planning._stage_helpers import (
    planning_index_artifact_key,
    prepare_subfeature_context_artifacts,
)
from iriai_build_v2.workflows.planning.workflow import PlanningWorkflow
from iriai_build_v2.workflows.develop.phases import ImplementationPhase, PostTestObservationPhase
from iriai_build_v2.roles import (
    lead_architect_gate_reviewer,
    lead_architect_reviewer,
    lead_designer_gate_reviewer,
    lead_designer_reviewer,
    lead_pm_gate_reviewer,
    lead_task_planner_gate_reviewer,
    lead_task_planner_reviewer,
    user,
)


def _decomposition() -> SubfeatureDecomposition:
    return SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing"),
        ],
        edges=[
            SubfeatureEdge(
                from_subfeature="accounts",
                to_subfeature="billing",
                interface_type="api_call",
                description="Billing consumes account identity",
            )
        ],
        complete=True,
    )


class _TestMirror:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def feature_dir(self, feature_id: str) -> Path:
        path = self.base_dir / feature_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_artifact(self, feature_id: str, artifact_key: str, text: str) -> Path:
        rel_path = Path(_key_to_path(artifact_key))
        path = self.feature_dir(feature_id) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def delete_artifact(self, feature_id: str, artifact_key: str) -> None:
        rel_path = Path(_key_to_path(artifact_key))
        path = self.feature_dir(feature_id) / rel_path
        path.unlink(missing_ok=True)
        parent = path.parent
        root = self.feature_dir(feature_id)
        while parent != root and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _write_mirror_artifact(
    mirror: _TestMirror,
    *,
    feature_id: str,
    artifact_key: str,
    text: str,
    staging: bool = False,
    mtime_ns: int | None = None,
) -> Path:
    rel_path = Path(_key_to_path(artifact_key))
    base_dir = mirror.feature_dir(feature_id)
    if staging:
        path = base_dir / ".staging" / rel_path
    else:
        path = base_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.mark.asyncio
async def test_prepare_subfeature_context_artifacts_writes_manifest_and_planning_index(tmp_path):
    feature = SimpleNamespace(id="feat-context", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    broad_prd_path = _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="broad prd",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
    )

    context_path, manifest_path, planning_index_key = await prepare_subfeature_context_artifacts(
        runner,
        feature,
        thread_id="subfeature:accounts",
        step="design",
        step_title="Design",
        slug="accounts",
        subfeature_name="Accounts",
        context_text="merged overview context",
        source_groups=[
            ("Broad Artifacts", [("Broad PRD", str(broad_prd_path)), ("Missing", "")]),
        ],
    )

    assert Path(context_path).read_text(encoding="utf-8") == "merged overview context"
    manifest_text = Path(manifest_path).read_text(encoding="utf-8")
    assert "# Subfeature Context Manifest" in manifest_text
    assert f"`{broad_prd_path}`" in manifest_text
    assert "Missing" not in manifest_text
    assert planning_index_key == planning_index_artifact_key("design", "accounts")
    assert artifacts.put_calls == [
        (
            "planning-index-design:accounts",
            (
                "Planning context index for Accounts — Design.\n\n"
                f"Read the context manifest first: `{manifest_path}`\n"
                f"Use the merged overview context file as the canonical overview/reference: `{context_path}`\n"
                "Open the referenced source files selectively instead of loading everything eagerly."
            ),
        )
    ] 


@pytest.mark.asyncio
async def test_clear_agent_session_uses_runtime_override_and_clears_session_sizes():
    feature = SimpleNamespace(id="feat-session")
    session_key = "planner:feat-session"

    class _Store:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def delete(self, key: str) -> None:
            self.deleted.append(key)

    runner_store = _Store()
    runtime_store = _Store()
    runtime = SimpleNamespace(
        session_store=runtime_store,
        _session_messages={session_key: ["msg"]},
        _session_context={session_key: ["ctx"]},
        _session_sizes={session_key: 123},
    )
    runner = SimpleNamespace(
        sessions=runner_store,
        agent_runtime=SimpleNamespace(
            session_store=_Store(),
            _session_messages={},
            _session_context={},
            _session_sizes={},
        ),
    )
    actor = SimpleNamespace(
        name="planner",
        role=SimpleNamespace(metadata={"runtime_instance": runtime}),
    )

    await _clear_agent_session(runner, actor, feature)

    assert runner_store.deleted == [session_key]
    assert runtime_store.deleted == [session_key]
    assert session_key not in runtime._session_messages
    assert session_key not in runtime._session_context
    assert session_key not in runtime._session_sizes


@pytest.mark.asyncio
async def test_targeted_revision_uses_dedicated_clarifier_for_follow_up_questions(tmp_path):
    feature = SimpleNamespace(id="feat-revision-q", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    existing_text = "# Existing PRD\n\nCurrent content."

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            if key == "prd:accounts":
                return existing_text
            return None

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {"artifact_mirror": mirror}
            self.ask_count = 0
            self.clarifier_prompts: list[str] = []
            self.clarifier_names: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if type(task).__name__ == "Ask":
                self.ask_count += 1
                if self.ask_count == 1:
                    return ArtifactPatchSet(
                        patches=[],
                        summary="Should I use strict validation or best-effort defaults?",
                    )
                return ArtifactPatchSet(patches=[], summary="")
            if type(task).__name__ == "HostedInterview":
                self.clarifier_prompts.append(task.questioner.role.prompt)
                self.clarifier_names.append(task.questioner.role.name)
                q_key = "revision-questions:prd:accounts"
                q_path = mirror.feature_dir("feat-revision-q") / ".staging" / Path(_key_to_path(q_key))
                q_path.parent.mkdir(parents=True, exist_ok=True)
                q_path.write_text(
                    "User said proceed; make reasonable assumptions from the existing artifact.",
                    encoding="utf-8",
                )
                return SimpleNamespace(question="", complete=True, artifact_path=str(q_path), output=None)
            raise AssertionError(f"unexpected task type: {type(task).__name__}")

    runner = _Runner()

    await targeted_revision(
        runner,
        feature,
        "subfeature",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Tighten the PRD around validation behavior.",
                    reasoning="Need a clear default when configuration is missing.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=SimpleNamespace(name="lead-pm", prompt="GENERIC BASE PROMPT"),
        output_type=PRD,
        artifact_prefix="prd",
    )

    assert runner.clarifier_names == ["lead-pm-revision-clarifier"]
    assert len(runner.clarifier_prompts) == 1
    clarifier_prompt = runner.clarifier_prompts[0]
    assert "NEVER ask which feature is being worked on." in clarifier_prompt
    assert "NEVER ask where to write the artifact" in clarifier_prompt
    assert "proceed', 'delegate'" in clarifier_prompt


def test_offload_if_large_returns_absolute_prompt_path(tmp_path):
    large_prompt = "x" * 100_001

    result = _offload_if_large(large_prompt, tmp_path, "prompt-lead-designer-gate-reviewer")

    expected_path = (tmp_path / ".iriai-context" / "prompt-lead-designer-gate-reviewer.md").resolve()
    assert str(expected_path) in result
    assert expected_path.read_text(encoding="utf-8") == large_prompt
    assert result.startswith(f"Your full task prompt is in `{expected_path}`")


@pytest.mark.asyncio
async def test_interview_gate_review_skips_approved_staging_review_and_closes_feedback(tmp_path):
    feature = SimpleNamespace(id="feat-gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd",
        text="compiled prd",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:prd",
        text=(
            "# Gate Review: PRD\n\n"
            "- **Outcome:** **APPROVED — no changes requested**\n"
        ),
        staging=True,
    )

    session_file = mirror.feature_dir(feature.id) / ".feedback" / "prd" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "id": "qs_demo",
                "feature_id": feature.id,
                "artifact_key": "prd",
                "status": "active",
                "created_at": "2026-04-17T21:14:29.665Z",
                "submitted_at": None,
            }
        ),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return None

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={
            "artifact_mirror": mirror,
            "hosting": DocHostingService(mirror),
        },
    )

    async def _run(*args, **kwargs):
        raise AssertionError("interview_gate_review should not launch a new interview")

    runner.run = _run

    result = await interview_gate_review(
        runner,
        feature,
        "subfeature",
        lead_actor=lead_pm_gate_reviewer,
        decomposition=_decomposition(),
        artifact_prefix="prd",
        compiled_key="prd",
        base_role=SimpleNamespace(name="pm"),
        output_type=PRD,
        compiler_actor=SimpleNamespace(name="pm-compiler"),
        broad_key="prd:broad",
    )

    assert result == "compiled prd"
    assert ("prd", "compiled prd") in artifacts.put_calls
    assert any(key == "gate-review-ledger:prd" for key, _ in artifacts.put_calls)

    session = json.loads(session_file.read_text(encoding="utf-8"))
    assert session["status"] == "submitted"
    assert session["submitted_at"]


@pytest.mark.asyncio
async def test_interview_gate_review_marks_feedback_submitted_on_fresh_approval(tmp_path):
    feature = SimpleNamespace(id="feat-gate-fresh", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd",
        text="compiled prd",
    )

    session_file = mirror.feature_dir(feature.id) / ".feedback" / "prd" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "id": "qs_demo",
                "feature_id": feature.id,
                "artifact_key": "prd",
                "status": "active",
                "created_at": "2026-04-17T21:14:29.665Z",
                "submitted_at": None,
            }
        ),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return None

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={
            "artifact_mirror": mirror,
            "hosting": DocHostingService(mirror),
        },
    )

    async def _run(task, feature, phase_name):
        del task, feature, phase_name
        return SimpleNamespace(output=ReviewOutcome(approved=True, complete=True))

    runner.run = _run

    result = await interview_gate_review(
        runner,
        feature,
        "subfeature",
        lead_actor=lead_pm_gate_reviewer,
        decomposition=_decomposition(),
        artifact_prefix="prd",
        compiled_key="prd",
        base_role=SimpleNamespace(name="pm"),
        output_type=PRD,
        compiler_actor=SimpleNamespace(name="pm-compiler"),
        broad_key="prd:broad",
    )

    assert result == "compiled prd"
    assert ("prd", "compiled prd") in artifacts.put_calls
    assert any(key == "gate-review-ledger:prd" for key, _ in artifacts.put_calls)

    session = json.loads(session_file.read_text(encoding="utf-8"))
    assert session["status"] == "submitted"
    assert session["submitted_at"]


@pytest.mark.asyncio
async def test_hosted_interview_accepts_structured_gate_approval_output(tmp_path):
    feature = SimpleNamespace(id="feat-gate-structured", name="Feature Gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={
            "artifact_mirror": mirror,
            "hosting": DocHostingService(mirror),
        },
    )

    interview = HostedInterview(
        questioner=lead_pm_gate_reviewer,
        responder=user,
        initial_prompt="Gate review",
        output_type=Envelope[ReviewOutcome],
        done=envelope_done,
        artifact_key="gate-review:design",
        artifact_label="Gate Review — design",
    )

    result = SimpleNamespace(output=ReviewOutcome(approved=True, complete=True))

    await interview.on_done(runner, feature, result=result)

    assert artifacts.put_calls
    stored_key, stored_value = artifacts.put_calls[0]
    assert stored_key == "gate-review:design"
    assert '"approved": true' in stored_value.lower()


@pytest.mark.asyncio
async def test_run_global_prd_tail_skips_compile_when_gate_already_approved(tmp_path, monkeypatch):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-prd", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:prd",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )

    session_file = mirror.feature_dir(feature.id) / ".feedback" / "prd" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps({"id": "qs_prd", "status": "active", "submitted_at": None}),
        encoding="utf-8",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"prd": "approved compiled prd"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("global PRD tail should not re-run compile/review when gate is approved")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _boom,
    )

    requests = await _run_global_prd_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.prd == "approved compiled prd"
    session = json.loads(session_file.read_text(encoding="utf-8"))
    assert session["status"] == "submitted"
    assert session["submitted_at"]


@pytest.mark.asyncio
async def test_run_global_prd_tail_resumes_existing_compiled_artifact_at_gate_review(tmp_path, monkeypatch):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-prd-pending-gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd",
        text="compiled pending-gate prd",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("pending-gate PRD resume should not re-run integration/compile")

    seen_gate_prefixes: list[str] = []

    async def _fake_interview_gate_review(*args, **kwargs):
        seen_gate_prefixes.append(kwargs["artifact_prefix"])
        return "approved compiled prd"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _fake_interview_gate_review,
    )

    requests = await _run_global_prd_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.prd == "approved compiled prd"
    assert seen_gate_prefixes == ["prd"]


@pytest.mark.asyncio
async def test_run_global_design_tail_skips_compile_when_gate_already_approved(tmp_path, monkeypatch):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-design", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:design",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )

    session_file = mirror.feature_dir(feature.id) / ".feedback" / "design-decisions" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps({"id": "qs_design", "status": "active", "submitted_at": None}),
        encoding="utf-8",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"design": "approved compiled design"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("global design tail should not re-run compile/review when gate is approved")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _boom,
    )

    requests = await _run_global_design_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.design == "approved compiled design"
    session = json.loads(session_file.read_text(encoding="utf-8"))
    assert session["status"] == "submitted"
    assert session["submitted_at"]


@pytest.mark.asyncio
async def test_run_global_design_tail_resumes_existing_compiled_artifact_at_gate_review(tmp_path, monkeypatch):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-design-pending-gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="design",
        text="compiled pending-gate design",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("pending-gate design resume should not re-run integration/compile")

    seen_gate_prefixes: list[str] = []

    async def _fake_interview_gate_review(*args, **kwargs):
        seen_gate_prefixes.append(kwargs["artifact_prefix"])
        return "approved compiled design"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _fake_interview_gate_review,
    )

    requests = await _run_global_design_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.design == "approved compiled design"
    assert seen_gate_prefixes == ["design"]


@pytest.mark.asyncio
async def test_run_global_architecture_tail_skips_compile_when_plan_and_system_design_gates_already_approved(
    tmp_path,
    monkeypatch,
):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-arch", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:plan",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:system-design",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )

    plan_session = mirror.feature_dir(feature.id) / ".feedback" / "plan" / "session.json"
    plan_session.parent.mkdir(parents=True, exist_ok=True)
    plan_session.write_text(
        json.dumps({"id": "qs_plan", "status": "active", "submitted_at": None}),
        encoding="utf-8",
    )
    sd_session = mirror.feature_dir(feature.id) / ".feedback" / "system-design" / "session.json"
    sd_session.parent.mkdir(parents=True, exist_ok=True)
    sd_session.write_text(
        json.dumps({"id": "qs_sd", "status": "active", "submitted_at": None}),
        encoding="utf-8",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "plan": "approved compiled plan",
                "system-design": "approved compiled system design",
            }.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("global architecture tail should not re-run approved plan/system-design work")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._compile_system_design",
        _boom,
    )

    requests = await _run_global_architecture_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.plan == "approved compiled plan"
    assert state.system_design == "approved compiled system design"
    plan_data = json.loads(plan_session.read_text(encoding="utf-8"))
    sd_data = json.loads(sd_session.read_text(encoding="utf-8"))
    assert plan_data["status"] == "submitted"
    assert sd_data["status"] == "submitted"


@pytest.mark.asyncio
async def test_run_global_architecture_tail_resumes_existing_compiled_plan_at_gate_review(
    tmp_path,
    monkeypatch,
):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-arch-plan-pending-gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="plan",
        text="compiled pending-gate plan",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:system-design",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"system-design": "approved compiled system design"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("pending-gate plan resume should not re-run integration/compile")

    seen_gate_prefixes: list[str] = []

    async def _fake_interview_gate_review(*args, **kwargs):
        seen_gate_prefixes.append(kwargs["artifact_prefix"])
        assert kwargs["artifact_prefix"] == "plan"
        return "approved compiled plan"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _fake_interview_gate_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._compile_system_design",
        _boom,
    )

    requests = await _run_global_architecture_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.plan == "approved compiled plan"
    assert state.system_design == "approved compiled system design"
    assert seen_gate_prefixes == ["plan"]


@pytest.mark.asyncio
async def test_run_global_architecture_tail_resumes_existing_compiled_system_design_at_gate_review(
    tmp_path,
    monkeypatch,
):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-arch-sd-pending-gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:plan",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )
    source_rel = _sd_source_path("system-design")
    assert source_rel is not None
    source_path = mirror.feature_dir(feature.id) / source_rel
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("compiled pending-gate system design source", encoding="utf-8")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="system-design",
        text="<html>compiled pending-gate system design</html>",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"plan": "approved compiled plan"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("pending-gate system-design resume should not re-run compilation")

    seen_gate_prefixes: list[str] = []

    async def _fake_interview_gate_review(*args, **kwargs):
        seen_gate_prefixes.append(kwargs["artifact_prefix"])
        assert kwargs["artifact_prefix"] == "system-design"
        return "approved compiled system design"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _fake_interview_gate_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._compile_system_design",
        _boom,
    )

    requests = await _run_global_architecture_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.plan == "approved compiled plan"
    assert state.system_design == "approved compiled system design"
    assert seen_gate_prefixes == ["system-design"]


@pytest.mark.asyncio
async def test_run_global_architecture_tail_skips_reapproved_plan_and_continues_with_system_design(
    tmp_path,
    monkeypatch,
):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-tail-arch-partial", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="gate-review:plan",
        text="# Gate Review\n\nOutcome: APPROVED\n",
        staging=True,
    )

    plan_session = mirror.feature_dir(feature.id) / ".feedback" / "plan" / "session.json"
    plan_session.parent.mkdir(parents=True, exist_ok=True)
    plan_session.write_text(
        json.dumps({"id": "qs_plan_only", "status": "active", "submitted_at": None}),
        encoding="utf-8",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"plan": "approved compiled plan"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _integration_boom(*args, **kwargs):
        raise AssertionError("approved plan should skip integration review on resume")

    async def _compile_boom(*args, **kwargs):
        raise AssertionError("approved plan should skip plan compilation on resume")

    async def _fake_compile_system_design(self, runner, feature, decomposition):
        del self, runner, feature, decomposition
        return None

    seen_gate_artifacts: list[str] = []

    async def _fake_interview_gate_review(*args, **kwargs):
        seen_gate_artifacts.append(kwargs["artifact_prefix"])
        assert kwargs["artifact_prefix"] == "system-design"
        return "approved compiled system design"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _integration_boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _compile_boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._compile_system_design",
        _fake_compile_system_design,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _fake_interview_gate_review,
    )

    requests = await _run_global_architecture_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert state.plan == "approved compiled plan"
    assert state.system_design == "approved compiled system design"
    assert seen_gate_artifacts == ["system-design"]
    plan_data = json.loads(plan_session.read_text(encoding="utf-8"))
    assert plan_data["status"] == "submitted"


def test_threaded_subfeature_prompts_are_index_and_manifest_first():
    sf = Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")
    prompt_builders = [
        _pm_prompt,
        _design_prompt,
        _architecture_prompt,
        _test_planning_prompt,
    ]

    for builder in prompt_builders:
        prompt = builder(sf, "/tmp/context.md", "/tmp/manifest.md")
        assert "Read the planning context index from the injected context first." in prompt
        assert "Read `/tmp/manifest.md` before proceeding." in prompt
        assert "Use `/tmp/context.md` as the overview/reference." in prompt
        assert "Read the full context file first." not in prompt


@pytest.mark.asyncio
async def test_pm_phase_resumes_compiled_artifact_at_gate_review(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-pm-gate", name="PM Gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(mirror, feature_id=feature.id, artifact_key="prd", text="compiled prd")
    state = BuildState(metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _fake_load(*args, **kwargs):
        return _decomposition()

    async def _fake_gate(*args, **kwargs):
        assert kwargs["artifact_prefix"] == "prd"
        return "approved prd"

    async def _boom(*args, **kwargs):
        raise AssertionError("PMPhase should resume at gate review, not restart planning")

    monkeypatch.setattr(PMPhase, "_load_decomposition", _fake_load)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.pm.interview_gate_review",
        _fake_gate,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.pm.broad_interview",
        _boom,
    )

    result = await PMPhase().execute(runner, feature, state)
    assert result.prd == "approved prd"


@pytest.mark.asyncio
async def test_design_phase_resumes_compiled_artifact_at_gate_review(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-design-gate", name="Design Gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(mirror, feature_id=feature.id, artifact_key="design", text="compiled design")
    (mirror.feature_dir(feature.id) / "mockup-unified.html").write_text("<html>mockup</html>", encoding="utf-8")
    state = BuildState(metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _fake_load(*args, **kwargs):
        return _decomposition()

    async def _fake_gate(*args, **kwargs):
        assert kwargs["artifact_prefix"] == "design"
        return "approved design"

    async def _boom(*args, **kwargs):
        raise AssertionError("DesignPhase should resume at gate review, not restart design planning")

    monkeypatch.setattr(DesignPhase, "_load_decomposition", _fake_load)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.design.interview_gate_review",
        _fake_gate,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.design.broad_interview",
        _boom,
    )

    result = await DesignPhase().execute(runner, feature, state)
    assert result.design == "approved design"


@pytest.mark.asyncio
async def test_architecture_phase_resumes_compiled_artifact_at_gate_review(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-arch-gate", name="Architecture Gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(mirror, feature_id=feature.id, artifact_key="plan", text="compiled plan")
    state = BuildState(metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"system-design": "approved sd"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _fake_load(*args, **kwargs):
        return _decomposition()

    async def _fake_plan_gate(self, runner, feature, decomposition):
        del self, runner, feature, decomposition
        return "approved plan"

    async def _fake_sd_gate(self, runner, feature, decomposition):
        del self, runner, feature, decomposition
        return "approved sd"

    async def _boom(*args, **kwargs):
        raise AssertionError("ArchitecturePhase should resume at gate review, not restart architecture planning")

    monkeypatch.setattr(ArchitecturePhase, "_load_decomposition", _fake_load)
    monkeypatch.setattr(ArchitecturePhase, "_plan_gate_review", _fake_plan_gate)
    monkeypatch.setattr(ArchitecturePhase, "_system_design_gate_review", _fake_sd_gate)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.broad_interview",
        _boom,
    )

    result = await ArchitecturePhase().execute(runner, feature, state)
    assert result.plan == "approved plan"
    assert result.system_design == "approved sd"


@pytest.mark.asyncio
async def test_run_global_architecture_tail_recompiles_after_failed_multi_artifact_publish_cleanup(
    tmp_path,
    monkeypatch,
):
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-arch-cleanup", name="Architecture Cleanup", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.values.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.values[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.values.pop(key, None)

    artifacts = _Artifacts()
    hosting = DocHostingService(mirror)
    original_push = hosting.push

    async def _failing_push(feature_id: str, key: str, content: str, label: str):
        url = await original_push(feature_id, key, content, label)
        if key == "system-design":
            raise RuntimeError("boom on system-design hosting")
        return url

    hosting.push = _failing_push  # type: ignore[method-assign]

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror, "hosting": hosting},
    )

    interview = HostedInterview(
        questioner=SimpleNamespace(name="architect"),
        responder=SimpleNamespace(name="user"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan",
        artifact_label="Architecture",
        additional_artifact_keys=["system-design"],
    )
    await interview.on_start(runner, feature)
    with pytest.raises(RuntimeError, match="boom on system-design hosting"):
        await interview.on_done(
            runner,
            feature,
            result=SimpleNamespace(
                artifact_path="",
                output=ArchitectureOutput(
                    plan=TechnicalPlan(architecture="compiled plan", complete=True),
                    system_design=SystemDesign(title="SD", overview="compiled sd", complete=True),
                    complete=True,
                ),
            ),
        )

    assert artifacts.values == {}
    assert not (mirror.feature_dir(feature.id) / _key_to_path("plan")).exists()
    assert not (mirror.feature_dir(feature.id) / _key_to_path("system-design")).exists()

    compile_called = False

    async def _fake_review(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(needs_revision=False, revision_instructions=None)

    async def _fake_compile_artifacts(*args, **kwargs):
        nonlocal compile_called
        compile_called = True
        return None

    async def _fake_gate_review(*args, **kwargs):
        return f"approved {kwargs['artifact_prefix']}"

    async def _fake_compile_system_design(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.integration_review",
        _fake_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_artifacts",
        _fake_compile_artifacts,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.interview_gate_review",
        _fake_gate_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._compile_system_design",
        _fake_compile_system_design,
    )

    requests = await _run_global_architecture_tail(runner, feature, state, control, decomposition)

    assert requests == []
    assert compile_called is True
    assert state.plan == "approved plan"
    assert state.system_design == "approved system-design"


@pytest.mark.asyncio
async def test_task_planning_phase_resumes_compiled_artifact_at_gate_review(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-dag-gate", name="Task Gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(mirror, feature_id=feature.id, artifact_key="dag", text="compiled dag")
    state = BuildState(metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )

    async def _fake_load(*args, **kwargs):
        return _decomposition()

    async def _fake_gate(*args, **kwargs):
        assert kwargs["artifact_prefix"] == "dag"
        return "approved dag"

    async def _boom(*args, **kwargs):
        raise AssertionError("TaskPlanningPhase should resume at gate review, not restart DAG planning")

    monkeypatch.setattr(TaskPlanningPhase, "_load_decomposition", _fake_load)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.interview_gate_review",
        _fake_gate,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_get_or_create_workstreams",
        _boom,
    )

    result = await TaskPlanningPhase().execute(runner, feature, state)
    assert result.dag == "approved dag"


def test_planning_workflow_uses_broad_then_subfeature_phases():
    phases = PlanningWorkflow().build_phases()

    assert phases == [
        ScopingPhase,
        BroadPhase,
        SubfeaturePhase,
        PlanReviewPhase,
        TaskPlanningPhase,
    ]


def test_full_develop_workflow_appends_implementation_and_observation():
    phases = FullDevelopWorkflow().build_phases()

    assert phases == [
        ScopingPhase,
        BroadPhase,
        SubfeaturePhase,
        PlanReviewPhase,
        TaskPlanningPhase,
        ImplementationPhase,
        PostTestObservationPhase,
    ]


@pytest.mark.asyncio
async def test_broad_step_skips_only_when_db_artifact_is_approved(monkeypatch):
    control = default_planning_control()
    set_step_status(control, step="prd", status=STEP_COMPLETE, provenance="human")
    state = SimpleNamespace(metadata={})
    feature = SimpleNamespace(id="feat-1", name="Feature", metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"prd:broad": "approved broad prd"}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            raise AssertionError("approved broad artifact should not be rewritten")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="broad:prd", resolver="terminal", thread_ts="", label="Broad PRD")

    pushed: list[str] = []

    async def _fake_push(*args, **kwargs):
        pushed.append(kwargs["artifact_text"])

    async def _unexpected_run(*args, **kwargs):
        raise AssertionError("broad interview/gate should not rerun for approved DB artifact")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.push_artifact_if_present",
        _fake_push,
    )
    runner.run = _unexpected_run

    result = await _run_broad_artifact_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
        step="prd",
        thread_id="broad:prd",
        label="Broad PRD",
        lead_actor=SimpleNamespace(context_keys=[]),
        background_actor=SimpleNamespace(context_keys=[]),
        output_type=ScopeOutput,
        artifact_key="prd:broad",
        artifact_label="Broad PRD",
        initial_prompt="prompt",
    )

    assert result == "approved broad prd"
    assert pushed == ["approved broad prd"]


@pytest.mark.asyncio
async def test_broad_step_reopens_gate_for_mirror_only_draft(tmp_path, monkeypatch):
    control = default_planning_control()
    control["broad_steps"]["prd"]["mode_selected"] = True
    control["broad_steps"]["prd"]["mode"] = "interactive"
    state = SimpleNamespace(metadata={})
    feature = SimpleNamespace(id="feat-1", name="Feature", metadata={})

    mirror_dir = tmp_path / "features"

    class _Mirror:
        def feature_dir(self, feature_id: str):
            path = mirror_dir / feature_id
            path.mkdir(parents=True, exist_ok=True)
            return path

    class _Hosting:
        def get_url(self, key: str):
            return {"prd:broad": "https://example.test/features/feat-1/prd:broad"}.get(key)

    draft_path = _Mirror().feature_dir(feature.id) / _key_to_path("prd:broad")
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text("draft broad prd", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(), "hosting": _Hosting()},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="broad:prd", resolver="terminal", thread_ts="", label="Broad PRD")

    gate_prompts: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        gate_prompts.append(task.prompt)
        return True

    pushed: list[str] = []

    async def _fake_push(*args, **kwargs):
        pushed.append(kwargs["artifact_text"])

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.push_artifact_if_present",
        _fake_push,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    runner.run = _fake_run

    result = await _run_broad_artifact_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
        step="prd",
        thread_id="broad:prd",
        label="Broad PRD",
        lead_actor=SimpleNamespace(context_keys=[]),
        background_actor=SimpleNamespace(context_keys=[]),
        output_type=ScopeOutput,
        artifact_key="prd:broad",
        artifact_label="Broad PRD",
        initial_prompt="prompt",
    )

    assert result == "draft broad prd"
    assert len(gate_prompts) == 1
    assert "Review in browser: https://example.test/features/feat-1/prd:broad" in gate_prompts[0]
    assert gate_prompts[0].startswith("Broad PRD\nReview in browser: https://example.test/features/feat-1/prd:broad")
    assert gate_prompts[0].endswith("Accept this draft for broad reconciliation?")
    assert pushed == []
    assert ("prd:broad", "draft broad prd") in artifacts.put_calls
    assert control["broad_steps"]["prd"]["status"] == STEP_COMPLETE


@pytest.mark.asyncio
async def test_broad_architecture_stage_reuses_existing_plan_draft(monkeypatch, tmp_path):
    control = default_planning_control()
    control["broad_steps"]["architecture"]["mode_selected"] = True
    control["broad_steps"]["architecture"]["mode"] = "interactive"
    state = SimpleNamespace(metadata={})
    feature = SimpleNamespace(id="feat-plan", name="Feature", metadata={})

    mirror_dir = tmp_path / "features"

    class _Mirror:
        def feature_dir(self, feature_id: str):
            path = mirror_dir / feature_id
            path.mkdir(parents=True, exist_ok=True)
            return path

    draft_path = _Mirror().feature_dir(feature.id) / _key_to_path("plan:broad")
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text("draft broad plan", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror()},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(
            thread_id="broad:architecture",
            resolver="terminal",
            thread_ts="",
            label="Broad Architecture",
        )

    gate_prompts: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        gate_prompts.append(task.prompt)
        return True

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    runner.run = _fake_run

    result = await _run_broad_artifact_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
        step="architecture",
        thread_id="broad:architecture",
        label="Broad Architecture",
        lead_actor=SimpleNamespace(context_keys=[]),
        background_actor=SimpleNamespace(context_keys=[]),
        output_type=ScopeOutput,
        artifact_key="plan:broad",
        artifact_label="Broad Architecture",
        initial_prompt="prompt",
    )

    assert result == "draft broad plan"
    assert gate_prompts == [
        "Broad Architecture:\n\ndraft broad plan\n\nAccept this draft for broad reconciliation?"
    ]
    assert ("plan:broad", "draft broad plan") in artifacts.put_calls
    assert control["broad_steps"]["architecture"]["status"] == STEP_COMPLETE


@pytest.mark.asyncio
async def test_get_resumable_artifact_prefers_newer_staging_over_final_and_db(tmp_path):
    feature = SimpleNamespace(id="feat-resume")
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="final broad prd",
        mtime_ns=1_000_000_000,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="staged broad prd",
        staging=True,
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return "approved broad prd"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_resumable_artifact(runner, feature, "prd:broad")

    assert result == "staged broad prd"


@pytest.mark.asyncio
async def test_get_resumable_artifact_prefers_final_over_older_staging(tmp_path):
    feature = SimpleNamespace(id="feat-final")
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="final broad prd",
        mtime_ns=2_000_000_000,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="old staged broad prd",
        staging=True,
        mtime_ns=1_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return "approved broad prd"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_resumable_artifact(runner, feature, "prd:broad")

    assert result == "final broad prd"


@pytest.mark.asyncio
async def test_get_resumable_artifact_uses_staging_when_final_missing(tmp_path):
    feature = SimpleNamespace(id="feat-staging-only")
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="staged broad prd",
        staging=True,
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return "approved broad prd"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_resumable_artifact(runner, feature, "prd:broad")

    assert result == "staged broad prd"


@pytest.mark.asyncio
async def test_get_resumable_artifact_falls_back_to_db_when_no_local_draft(tmp_path):
    feature = SimpleNamespace(id="feat-db")
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return "approved broad prd"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_resumable_artifact(runner, feature, "prd:broad")

    assert result == "approved broad prd"


@pytest.mark.asyncio
async def test_get_existing_artifact_prefers_system_design_source_mirror_over_rendered_html(tmp_path):
    feature = SimpleNamespace(id="feat-sd-existing")
    mirror = _TestMirror(tmp_path / "features")
    feature_dir = mirror.feature_dir(feature.id)
    source_rel = _sd_source_path("system-design")
    assert source_rel is not None
    source_path = feature_dir / source_rel
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("canonical system design source", encoding="utf-8")

    html_path = feature_dir / _key_to_path("system-design")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text("<html>rendered system design</html>", encoding="utf-8")

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_existing_artifact(runner, feature, "system-design")

    assert result == "canonical system design source"


@pytest.mark.asyncio
async def test_get_gate_resume_artifact_ignores_rendered_system_design_html_when_source_missing(tmp_path):
    feature = SimpleNamespace(id="feat-sd-gate")
    mirror = _TestMirror(tmp_path / "features")
    html_path = mirror.feature_dir(feature.id) / _key_to_path("system-design")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text("<html>rendered system design</html>", encoding="utf-8")

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_gate_resume_artifact(runner, feature, "system-design")

    assert result is None


@pytest.mark.asyncio
async def test_get_gate_resume_artifact_prefers_db_over_stale_final_mirror(tmp_path):
    feature = SimpleNamespace(id="feat-gate-db")
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="stale mirrored prd",
        mtime_ns=1_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"prd:accounts": "approved db prd"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_gate_resume_artifact(runner, feature, "prd:accounts")

    assert result == "approved db prd"


@pytest.mark.asyncio
async def test_get_gate_resume_artifact_uses_fresher_staging_when_db_missing(tmp_path):
    feature = SimpleNamespace(id="feat-gate-staging")
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="older mirrored prd",
        mtime_ns=1_000_000_000,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="staged prd",
        staging=True,
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_gate_resume_artifact(runner, feature, "prd:accounts")

    assert result == "staged prd"


@pytest.mark.asyncio
async def test_get_gate_resume_artifact_falls_back_to_final_mirror_when_db_missing_and_staging_is_stale(tmp_path):
    feature = SimpleNamespace(id="feat-gate-final")
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="final mirrored prd",
        mtime_ns=2_000_000_000,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="old staged prd",
        staging=True,
        mtime_ns=1_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    result = await get_gate_resume_artifact(runner, feature, "prd:accounts")

    assert result == "final mirrored prd"


@pytest.mark.asyncio
async def test_broad_step_prefers_resumable_staging_draft_over_db(monkeypatch, tmp_path):
    control = default_planning_control()
    control["broad_steps"]["prd"]["mode_selected"] = True
    control["broad_steps"]["prd"]["mode"] = "interactive"
    state = SimpleNamespace(metadata={})
    feature = SimpleNamespace(id="feat-staged-broad", name="Feature", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="staged broad prd",
        staging=True,
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {"prd:broad": "approved broad prd"}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="broad:prd", resolver="terminal", thread_ts="", label="Broad PRD")

    gate_prompts: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        gate_prompts.append(task.prompt)
        return True

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    runner.run = _fake_run

    result = await _run_broad_artifact_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
        step="prd",
        thread_id="broad:prd",
        label="Broad PRD",
        lead_actor=SimpleNamespace(context_keys=[]),
        background_actor=SimpleNamespace(context_keys=[]),
        output_type=ScopeOutput,
        artifact_key="prd:broad",
        artifact_label="Broad PRD",
        initial_prompt="prompt",
    )

    assert result == "staged broad prd"
    assert gate_prompts == [
        "Broad PRD:\n\nstaged broad prd\n\nAccept this draft for broad reconciliation?"
    ]
    assert ("prd:broad", "staged broad prd") in artifacts.put_calls


def test_subfeature_step_waits_for_broad_and_same_subfeature_dependencies():
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)

    assert not _step_ready(control, decomposition, "accounts", "pm")

    for broad_step in ("prd", "design", "architecture"):
        control["broad_steps"][broad_step]["status"] = STEP_COMPLETE

    assert not _step_ready(control, decomposition, "accounts", "pm")
    control["broad_steps"]["reconciliation"]["status"] = STEP_COMPLETE
    assert _step_ready(control, decomposition, "accounts", "pm")
    assert _step_ready(control, decomposition, "billing", "pm")

    assert not _step_ready(control, decomposition, "billing", "design")
    set_step_status(control, slug="billing", step="pm", status=STEP_COMPLETE)
    assert _step_ready(control, decomposition, "billing", "design")


def test_subfeature_step_blocks_when_thread_background_task_is_active():
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    for broad_step in ("prd", "design", "architecture", "reconciliation"):
        control["broad_steps"][broad_step]["status"] = STEP_COMPLETE

    assert _step_ready(control, decomposition, "accounts", "pm")

    set_background_state(
        control,
        slug="accounts",
        step="pm",
        active=True,
        status="running",
        reason="agent_fill",
    )
    assert not _step_ready(control, decomposition, "accounts", "pm")


def test_subfeature_context_keeps_edge_connected_artifacts_full_text():
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing"),
            Subfeature(id="SF-3", slug="reporting", name="Reporting", description="Reporting"),
        ],
        edges=[
            SubfeatureEdge(
                from_subfeature="accounts",
                to_subfeature="billing",
                interface_type="api_call",
                description="Billing consumes account identity",
            )
        ],
        complete=True,
    )

    context = _build_subfeature_context(
        decomposition,
        "billing",
        completed_artifacts={
            "accounts": "FULL ACCOUNTS",
            "reporting": "FULL REPORTING",
        },
        completed_summaries={
            "reporting": "SUMMARY REPORTING",
        },
        broad_text="BROAD",
        decomposition_text="DECOMPOSITION",
    )

    assert "## Subfeature: accounts (connected — full text)\n\nFULL ACCOUNTS" in context
    assert "## Subfeature: reporting (summary)\n\nSUMMARY REPORTING" in context


def test_reset_stale_background_state_requeues_running_step():
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)

    set_step_status(control, slug="accounts", step="pm", status=STEP_RUNNING)
    set_background_state(
        control,
        slug="accounts",
        step="pm",
        active=True,
        status="running",
        reason="agent_fill",
    )

    changed = _reset_stale_background_state(control, decomposition)

    assert changed is True
    assert control["subfeatures"]["accounts"]["background_task"]["active"] is False
    assert control["subfeatures"]["accounts"]["background_task"]["status"] == "interrupted"
    assert control["subfeatures"]["accounts"]["steps"]["pm"]["status"] == "pending"


def test_reset_stale_background_state_requeues_interactive_running_step():
    decomposition = _decomposition()
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)

    set_step_status(control, slug="accounts", step="pm", status=STEP_RUNNING)

    changed = _reset_stale_background_state(control, decomposition)

    assert changed is True
    assert control["subfeatures"]["accounts"]["steps"]["pm"]["status"] == "pending"


def test_sync_subfeature_threads_prunes_removed_slugs():
    control = default_planning_control()
    ensure_subfeature_threads(control, _decomposition())

    revised = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-3", slug="ledger", name="Ledger", description="Ledger"),
        ],
        complete=True,
    )

    sync_subfeature_threads(control, revised)

    assert sorted(control["subfeatures"]) == ["accounts", "ledger"]


@pytest.mark.asyncio
async def test_collect_subfeature_step_policies_prepares_threads_without_prompting(monkeypatch):
    control = default_planning_control()
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-collect", metadata={})
    decomposition = _decomposition()
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={}, feature_store=None)

    handles: list[tuple[str, str]] = []

    async def _fake_ensure_thread(*args, **kwargs):
        handles.append((kwargs["thread_id"], kwargs["label"]))
        return SimpleNamespace(
            thread_id=kwargs["thread_id"],
            resolver=f"terminal.thread.{kwargs['thread_id']}",
            thread_ts="123",
            label=kwargs["label"],
        )

    async def _fake_persist(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.persist_planning_control",
        _fake_persist,
    )

    await _collect_subfeature_step_policies(
        runner,
        feature,
        state,
        control,
        decomposition,
        phase_name="broad",
    )

    assert sorted(control["subfeatures"]) == ["accounts", "billing"]
    assert all(
        not control["subfeatures"][slug]["steps"][step]["mode_selected"]
        for slug in ("accounts", "billing")
        for step in ("pm", "design", "architecture", "test_planning")
    )
    assert handles == [
        ("subfeature:accounts", "Accounts"),
        ("subfeature:billing", "Billing"),
    ]


@pytest.mark.asyncio
async def test_integration_review_ignores_cached_review_when_requested():
    feature = SimpleNamespace(id="feat-review")
    review_json = json.dumps(
        IntegrationReview(needs_revision=True, revision_instructions={"prd": "stale"}).model_dump()
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "integration-review:broad": review_json,
                "prd:broad": "Broad PRD",
                "design:broad": "Broad Design",
                "plan:broad": "Broad Architecture",
                "decomposition": _decomposition().model_dump_json(),
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={})
    run_calls: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        run_calls.append(task.artifact_key)
        return SimpleNamespace(output=IntegrationReview(needs_revision=False, summary="fresh"))

    runner.run = _fake_run

    result = await integration_review(
        runner,
        feature,
        "broad",
        lead_actor=lead_architect_reviewer,
        decomposition=_decomposition(),
        artifact_prefix="broad",
        review_key_suffix="broad",
        artifact_keys_by_target={
            "prd": "prd:broad",
            "design": "design:broad",
            "architecture": "plan:broad",
            "decomposition": "decomposition",
        },
        target_label="revision targets",
        use_cached_review=False,
    )

    assert result.summary == "fresh"
    assert run_calls == ["integration-review:broad"]
    assert any(key == "integration-review:broad" for key, _ in artifacts.put_calls)


@pytest.mark.asyncio
async def test_integration_review_clears_reviewer_session_before_launch(monkeypatch):
    feature = SimpleNamespace(id="feat-review-clear")

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "Broad PRD",
                "design:broad": "Broad Design",
                "plan:broad": "Broad Architecture",
                "decomposition": _decomposition().model_dump_json(),
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={})
    cleared: list[str] = []
    run_calls: list[str] = []

    async def _fake_clear(_runner, actor, _feature):
        cleared.append(actor.name)

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        run_calls.append(task.artifact_key)
        return SimpleNamespace(output=IntegrationReview(needs_revision=False, summary="fresh"))

    runner.run = _fake_run
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers._clear_agent_session",
        _fake_clear,
    )

    result = await integration_review(
        runner,
        feature,
        "broad",
        lead_actor=lead_architect_reviewer,
        decomposition=_decomposition(),
        artifact_prefix="broad",
        review_key_suffix="broad",
        artifact_keys_by_target={
            "prd": "prd:broad",
            "design": "design:broad",
            "architecture": "plan:broad",
            "decomposition": "decomposition",
        },
        target_label="revision targets",
        use_cached_review=False,
    )

    assert result.summary == "fresh"
    assert cleared == ["lead-architect-reviewer"]
    assert run_calls == ["integration-review:broad"]


def test_review_and_gate_review_actors_use_lightweight_context_keys():
    expected = ["project", "scope"]

    assert lead_designer_reviewer.context_keys == expected
    assert lead_designer_gate_reviewer.context_keys == expected
    assert lead_architect_reviewer.context_keys == expected
    assert lead_architect_gate_reviewer.context_keys == expected
    assert lead_task_planner_reviewer.context_keys == expected
    assert lead_task_planner_gate_reviewer.context_keys == expected


@pytest.mark.asyncio
async def test_integration_review_uses_responder_override_and_resumable_artifacts(monkeypatch):
    feature = SimpleNamespace(id="feat-review-threaded")
    runner = SimpleNamespace(
        artifacts=SimpleNamespace(put_calls=[]),
        services={},
    )

    async def _fake_put(key: str, value: str, *, feature):
        del key, value, feature
        return None

    runner.artifacts.put = _fake_put
    resumable_reads: list[str] = []

    async def _fake_get_resumable(_runner, _feature, artifact_key):
        resumable_reads.append(artifact_key)
        return {
            "prd:broad": "Broad PRD",
            "design:broad": "Broad Design",
            "plan:broad": "Broad Architecture",
            "decomposition": _decomposition().model_dump_json(),
        }.get(artifact_key)

    responder = user.model_copy(update={"resolver": "threaded"})

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        assert task.responder == responder
        return SimpleNamespace(output=IntegrationReview(needs_revision=False, summary="fresh"))

    runner.run = _fake_run

    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.get_resumable_artifact",
        _fake_get_resumable,
    )

    result = await integration_review(
        runner,
        feature,
        "broad",
        lead_actor=lead_architect_reviewer,
        decomposition=_decomposition(),
        artifact_prefix="broad",
        review_key_suffix="broad",
        artifact_keys_by_target={
            "prd": "prd:broad",
            "design": "design:broad",
            "architecture": "plan:broad",
            "decomposition": "decomposition",
        },
        target_label="revision targets",
        use_cached_review=False,
        responder=responder,
        prefer_local_artifacts=True,
    )

    assert result.summary == "fresh"
    assert resumable_reads[:4] == ["prd:broad", "design:broad", "plan:broad", "decomposition"]
    assert "integration-review:broad" in resumable_reads


@pytest.mark.asyncio
async def test_broad_reconciliation_revisions_apply_in_fixed_order(monkeypatch):
    state = BuildState()
    decomposition = _decomposition()
    calls: list[str] = []

    async def _fake_revise_artifact(*args, **kwargs):
        calls.append(kwargs["step"])
        return "revised"

    async def _fake_revise_decomposition(*args, **kwargs):
        calls.append("decomposition")
        return decomposition

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._revise_broad_artifact_from_reconciliation",
        _fake_revise_artifact,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._revise_decomposition_from_reconciliation",
        _fake_revise_decomposition,
    )

    result = await _apply_broad_reconciliation_revisions(
        SimpleNamespace(),
        SimpleNamespace(),
        state,
        default_planning_control(),
        phase_name="broad",
        decomposition=decomposition,
        revision_instructions={
            "decomposition": "revise decomposition",
            "architecture": "revise architecture",
        },
    )

    assert result is decomposition
    assert calls == ["architecture", "decomposition"]


@pytest.mark.asyncio
async def test_broad_reconciliation_revision_prefers_resumable_local_draft(monkeypatch, tmp_path):
    control = default_planning_control()
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-reconcile", name="Feature", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:broad",
        text="staged broad prd",
        staging=True,
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {"prd:broad": "approved broad prd"}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        feature_store=None,
    )
    prompts: list[str] = []
    refresh_calls: list[str] = []

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="broad:prd", resolver="terminal", thread_ts="", label="Broad PRD")

    async def _fake_run_broad_interview(*args, **kwargs):
        prompts.append(kwargs["initial_prompt"])
        return "revised broad prd", "human"

    async def _fake_refresh(*args, **kwargs):
        refresh_calls.append(kwargs["source_text"])

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._run_broad_interview",
        _fake_run_broad_interview,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._refresh_broad_decisions",
        _fake_refresh,
    )

    result = await _revise_broad_artifact_from_reconciliation(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
        step="prd",
        thread_id="broad:prd",
        label="Broad PRD",
        lead_actor=SimpleNamespace(context_keys=[]),
        background_actor=SimpleNamespace(context_keys=[]),
        output_type=ScopeOutput,
        artifact_key="prd:broad",
        artifact_label="Broad PRD",
        instruction="Tighten the draft",
        source_phase="broad",
        artifact_kind="prd",
        state_field="prd",
    )

    assert result == "revised broad prd"
    assert "Current draft:\nstaged broad prd" in prompts[0]
    assert ("prd:broad", "revised broad prd") in artifacts.put_calls
    assert refresh_calls == ["revised broad prd"]


@pytest.mark.asyncio
async def test_broad_decomposition_revision_prefers_resumable_local_draft(monkeypatch, tmp_path):
    control = default_planning_control()
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-decomp-reconcile", name="Feature", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    revised = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-9", slug="ledger", name="Ledger", description="Ledger")],
        complete=True,
    )
    staged_text = revised.model_dump_json()
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="decomposition",
        text=staged_text,
        staging=True,
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {"decomposition": _decomposition().model_dump_json()}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        feature_store=None,
    )
    prompts: list[str] = []

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(
            thread_id="broad:decomposition",
            resolver="terminal",
            thread_ts="",
            label="Broad Decomposition",
        )

    async def _fake_run_decomposition_interview(*args, **kwargs):
        prompts.append(kwargs["initial_prompt"])
        return staged_text, revised, "human"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._run_decomposition_interview",
        _fake_run_decomposition_interview,
    )

    result = await _revise_decomposition_from_reconciliation(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
        instruction="Rename the subfeature",
        decomposition=_decomposition(),
    )

    assert result == revised
    assert f"Current decomposition:\n{staged_text}" in prompts[0]
    assert ("decomposition", staged_text) in artifacts.put_calls


@pytest.mark.asyncio
async def test_broad_decomposition_stage_prefers_db_artifact_over_rendered_mirror(monkeypatch, tmp_path):
    control = default_planning_control()
    set_step_status(control, step="decomposition", status=STEP_COMPLETE, provenance="human")
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-decomp-stage", name="Feature", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    revised = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-3", slug="ledger", name="Ledger", description="Ledger")],
        complete=True,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="decomposition",
        text="# Subfeature Decomposition\n\nstale rendered mirror",
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"decomposition": revised.model_dump_json()}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            raise AssertionError("completed decomposition resume should not rewrite artifact")

    class _Hosting:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str]] = []
            self._urls: dict[str, str] = {}

        async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
            self.calls.append((feature_id, key, content, label))
            url = f"https://example.test/features/{feature_id}/{key}"
            self._urls[key] = url
            return url

        def get_url(self, key: str) -> str | None:
            return self._urls.get(key)

    hosting = _Hosting()
    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": hosting},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(
            thread_id="broad:decomposition",
            resolver="terminal",
            thread_ts="",
            label="Broad Decomposition",
        )

    async def _unexpected_run(*args, **kwargs):
        raise AssertionError("completed decomposition resume should not rerun")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    runner.run = _unexpected_run

    result = await _run_decomposition_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
    )

    assert result == revised
    assert hosting.calls == [
        (
            feature.id,
            "decomposition",
            revised.model_dump_json(),
            f"Subfeature Decomposition — {feature.name}",
        )
    ]


@pytest.mark.asyncio
async def test_broad_decomposition_stage_ignores_invalid_markdown_resume_and_reruns(monkeypatch, tmp_path):
    control = default_planning_control()
    control["broad_steps"]["decomposition"]["mode_selected"] = True
    control["broad_steps"]["decomposition"]["mode"] = "interactive"
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-decomp-invalid", name="Feature", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="decomposition",
        text="# stale markdown decomposition",
        staging=True,
        mtime_ns=2_000_000_000,
    )
    revised = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-4", slug="ledger", name="Ledger", description="Ledger")],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {"decomposition": "# stale markdown decomposition"}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(
            thread_id="broad:decomposition",
            resolver="terminal",
            thread_ts="",
            label="Broad Decomposition",
        )

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        if type(task).__name__ == "Gate":
            return True
        return SimpleNamespace(output=revised)

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._build_decomposition_interview_actors",
        lambda handle: (lead_architect_reviewer, lead_architect_reviewer, user),
    )
    runner.run = _fake_run

    result = await _run_decomposition_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
    )

    expected = revised.model_dump_json()
    assert result == revised
    assert ("decomposition", expected) in artifacts.put_calls


@pytest.mark.asyncio
async def test_broad_decomposition_stage_reuses_db_artifact_without_rerunning_interview(monkeypatch, tmp_path):
    control = default_planning_control()
    control["broad_steps"]["decomposition"]["mode_selected"] = True
    control["broad_steps"]["decomposition"]["mode"] = "interactive"
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-decomp-resume", name="Feature", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-7", slug="ledger", name="Ledger", description="Ledger")],
        complete=True,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="decomposition",
        text="# Subfeature Decomposition\n\nstale rendered mirror",
        mtime_ns=2_000_000_000,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {"decomposition": decomposition.model_dump_json()}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _Hosting:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str]] = []
            self._urls: dict[str, str] = {}

        async def push(self, feature_id: str, key: str, content: str, label: str) -> str:
            self.calls.append((feature_id, key, content, label))
            url = f"https://example.test/features/{feature_id}/{key}"
            self._urls[key] = url
            return url

        def get_url(self, key: str) -> str | None:
            return self._urls.get(key)

    artifacts = _Artifacts()
    hosting = _Hosting()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror, "hosting": hosting},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(
            thread_id="broad:decomposition",
            resolver="terminal",
            thread_ts="",
            label="Broad Decomposition",
        )

    async def _fake_persist(*args, **kwargs):
        return None

    gate_prompts: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        if type(task).__name__ == "Gate":
            gate_prompts.append(task.prompt)
            return True
        raise AssertionError("resume should reuse existing decomposition instead of rerunning the interview")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._build_decomposition_interview_actors",
        lambda handle: (lead_architect_reviewer, lead_architect_reviewer, user),
    )
    runner.run = _fake_run

    result = await _run_decomposition_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
    )

    assert result == decomposition
    assert len(gate_prompts) == 1
    assert '"slug":"ledger"' in gate_prompts[0].replace(" ", "")
    assert gate_prompts[0].endswith("Accept this draft for broad reconciliation?")
    assert ("decomposition", decomposition.model_dump_json()) in artifacts.put_calls
    assert hosting.calls[0][2] == decomposition.model_dump_json()


@pytest.mark.asyncio
async def test_broad_decomposition_gate_includes_review_url(monkeypatch):
    control = default_planning_control()
    control["broad_steps"]["decomposition"]["mode_selected"] = True
    control["broad_steps"]["decomposition"]["mode"] = "interactive"
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-decomp-link", name="Feature", metadata={})
    revised = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-4", slug="ledger", name="Ledger", description="Ledger")],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _Hosting:
        def get_url(self, key: str):
            return {"decomposition": "https://example.test/features/feat-decomp-link/decomposition"}.get(key)

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": _Hosting()},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(
            thread_id="broad:decomposition",
            resolver="terminal",
            thread_ts="",
            label="Broad Decomposition",
        )

    async def _fake_persist(*args, **kwargs):
        return None

    gate_prompts: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        if type(task).__name__ == "Gate":
            gate_prompts.append(task.prompt)
            return True
        return SimpleNamespace(output=revised)

    async def _fake_push(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.push_artifact_if_present",
        _fake_push,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._build_decomposition_interview_actors",
        lambda handle: (lead_architect_reviewer, lead_architect_reviewer, user),
    )
    runner.run = _fake_run

    result = await _run_decomposition_stage(
        runner,
        feature,
        state,
        control,
        phase_name="broad",
    )

    assert result == revised
    assert gate_prompts == [
        "Subfeature Decomposition\nReview in browser: https://example.test/features/feat-decomp-link/decomposition:\n\n"
        f"{revised.model_dump_json()}\n\nAccept this draft for broad reconciliation?"
    ]


@pytest.mark.asyncio
async def test_decompose_and_gate_includes_review_url(monkeypatch):
    feature = SimpleNamespace(id="feat-legacy-decomp", name="Feature", metadata={})
    revised = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _Hosting:
        def get_url(self, key: str):
            return {"decomposition": "https://example.test/features/feat-legacy-decomp/decomposition"}.get(key)

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"hosting": _Hosting()},
        feature_store=None,
    )

    gate_prompts: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        if type(task).__name__ == "Gate":
            gate_prompts.append(task.prompt)
            return True
        return SimpleNamespace(output=revised)

    runner.run = _fake_run

    result = await decompose_and_gate(
        runner,
        feature,
        "pm",
        lead_actor=user,
        approver=user,
        broad_artifact_key="prd",
    )

    assert result == revised
    assert len(gate_prompts) == 1
    assert gate_prompts[0].startswith(
        "Subfeature Decomposition\nReview in browser: https://example.test/features/feat-legacy-decomp/decomposition:"
    )
    assert '"slug": "accounts"' in gate_prompts[0]
    assert gate_prompts[0].endswith("Approve this decomposition?")


@pytest.mark.asyncio
async def test_architecture_step_does_not_skip_when_plan_exists_without_system_design(monkeypatch):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    control["broad_steps"]["architecture"]["status"] = STEP_COMPLETE
    set_step_status(control, slug="accounts", step="design", status=STEP_COMPLETE)
    set_step_status(control, slug="accounts", step="architecture", status=STEP_COMPLETE)
    state = SimpleNamespace(metadata={})

    class _Artifacts:
        def __init__(self):
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            values = {
                "plan:accounts": "approved plan text",
                "system-design:accounts": "",
                "plan:broad": "broad plan",
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "decomposition": decomposition.model_dump_json(),
            }
            return values.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={},
        feature_store=None,
    )
    feature = SimpleNamespace(id="feat-1", metadata={})

    gate_calls: list[str] = []

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal", thread_ts="", label="Accounts")

    async def _fake_get_gate_resume_artifact(_runner, _feature, artifact_key):
        if artifact_key == "plan:accounts":
            return "approved plan text"
        return None

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        gate_calls.append(kwargs["artifact_key"])
        if kwargs["artifact_key"].startswith("system-design"):
            return "{}", "{}"
        return "approved plan text", "approved plan text"

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_convert_and_host_sd(self, _runner, _feature, _sd_key, _sd_text, _sf_name):
        return "{}"

    async def _fake_rehost_plan_and_sd(self, *_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.get_gate_resume_artifact",
        _fake_get_gate_resume_artifact,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.gate_and_revise",
        _fake_gate_and_revise,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name="actor"),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.build_subfeature_context_text",
        lambda *args, **kwargs: "context",
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        lambda *args, **kwargs: asyncio.sleep(0, result=("/tmp/context.md", "/tmp/manifest.md", "planning-index-architecture:accounts")),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.push_artifact_if_present",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._convert_and_host_sd",
        _fake_convert_and_host_sd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._rehost_plan_and_sd",
        _fake_rehost_plan_and_sd,
    )

    result = await _run_architecture_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode="interactive",
        detach_on_background=False,
    )

    assert result == "approved plan text"
    assert gate_calls == ["plan:accounts", "system-design:accounts"]


@pytest.mark.asyncio
async def test_architecture_step_starts_fresh_after_failed_multi_artifact_publish_cleanup(
    tmp_path,
    monkeypatch,
):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    control["broad_steps"]["architecture"]["status"] = STEP_COMPLETE
    set_step_status(control, slug="accounts", step="design", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-arch-step-cleanup", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.values = {
                "plan:broad": "broad plan",
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "decomposition": decomposition.model_dump_json(),
                "prd:accounts": "approved prd",
                "design:accounts": "approved design",
                "decisions:broad": "",
                "decisions:accounts": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.values.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.values[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.values.pop(key, None)

    artifacts = _Artifacts()
    failing_hosting = DocHostingService(mirror)
    original_push = failing_hosting.push

    async def _failing_push(feature_id: str, key: str, content: str, label: str):
        url = await original_push(feature_id, key, content, label)
        if key == "system-design:accounts":
            raise RuntimeError("boom on subfeature system-design hosting")
        return url

    failing_hosting.push = _failing_push  # type: ignore[method-assign]
    rollback_runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror, "hosting": failing_hosting},
    )

    failed_interview = HostedInterview(
        questioner=SimpleNamespace(name="architect"),
        responder=SimpleNamespace(name="user"),
        initial_prompt="Start",
        done=lambda _result: True,
        artifact_key="plan:accounts",
        artifact_label="Architecture — Accounts",
        additional_artifact_keys=["system-design:accounts"],
    )
    await failed_interview.on_start(rollback_runner, feature)
    with pytest.raises(RuntimeError, match="boom on subfeature system-design hosting"):
        await failed_interview.on_done(
            rollback_runner,
            feature,
            result=SimpleNamespace(
                artifact_path="",
                output=ArchitectureOutput(
                    plan=TechnicalPlan(architecture="stale compiled plan", complete=True),
                    system_design=SystemDesign(title="SD", overview="stale compiled sd", complete=True),
                    complete=True,
                ),
            ),
        )

    assert await get_gate_resume_artifact(rollback_runner, feature, "plan:accounts") is None
    assert not (mirror.feature_dir(feature.id) / _key_to_path("plan:accounts")).exists()

    prompts: list[str] = []

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_prepare(*args, **kwargs):
        return "/tmp/arch-context.md", "/tmp/arch-manifest.md", "planning-index-architecture:accounts"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_convert_and_host_sd(self, _runner, _feature, _sd_key, sd_text, _sf_name):
        del self, _runner, _feature, _sd_key, _sf_name
        return sd_text

    async def _fake_rehost_plan_and_sd(self, *_args, **_kwargs):
        return '{"services":[]}'

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        return kwargs["artifact"], kwargs["artifact"]

    async def _fake_clear(*args, **kwargs):
        return None

    async def _fake_run(task, feature, *, phase_name):
        del feature, phase_name
        prompts.append(task.initial_prompt)
        return SimpleNamespace(
            output=SimpleNamespace(
                plan="fresh plan",
                system_design='{"services":[]}',
            )
        )

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal.thread.accounts", thread_ts="", label="Accounts"),
        ),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.gate_and_revise",
        _fake_gate_and_revise,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name=kwargs["suffix"], role=SimpleNamespace(metadata={})),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ThreadedHostedInterview",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._clear_agent_session",
        _fake_clear,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._convert_and_host_sd",
        _fake_convert_and_host_sd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._rehost_plan_and_sd",
        _fake_rehost_plan_and_sd,
    )

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
        feature_store=None,
        run=_fake_run,
    )

    result = await _run_architecture_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode=STEP_AGENT_FILL,
        detach_on_background=False,
    )

    assert result == "fresh plan"
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_pm_step_gate_reentry_prefers_approved_db_artifact_over_stale_local_mirror(monkeypatch, tmp_path):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-pm-resume", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="stale local pm draft",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:accounts": "approved db pm",
                "decomposition": decomposition.model_dump_json(),
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal", thread_ts="", label="Accounts")

    async def _fake_complete(*args, **kwargs):
        assert kwargs["result"] == "approved db pm"
        return "approved db pm"

    async def _fake_load_completed(*args, **kwargs):
        return {}, {}

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._complete_single_artifact_step",
        _fake_complete,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_completed_stage_maps",
        _fake_load_completed,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name="actor"),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.build_subfeature_context_text",
        lambda *args, **kwargs: "context",
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        lambda *args, **kwargs: asyncio.sleep(0, result=("/tmp/context.md", "/tmp/manifest.md", "planning-index-pm:accounts")),
    )

    result = await _run_pm_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode="interactive",
        detach_on_background=False,
    )

    assert result == "approved db pm"


@pytest.mark.asyncio
async def test_design_step_gate_reentry_prefers_approved_db_artifact_over_stale_local_mirror(monkeypatch, tmp_path):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-design-resume", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="design:accounts",
        text="stale local design draft",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "design:accounts": "approved db design",
                "prd:accounts": "approved prd",
                "decomposition": decomposition.model_dump_json(),
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        feature_store=None,
    )

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal", thread_ts="", label="Accounts")

    async def _fake_complete(*args, **kwargs):
        assert kwargs["result"] == "approved db design"
        return "approved db design"

    async def _fake_load_completed(*args, **kwargs):
        return {}, {}

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._complete_single_artifact_step",
        _fake_complete,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_completed_stage_maps",
        _fake_load_completed,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name="actor"),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.build_subfeature_context_text",
        lambda *args, **kwargs: "context",
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        lambda *args, **kwargs: asyncio.sleep(0, result=("/tmp/context.md", "/tmp/manifest.md", "planning-index-design:accounts")),
    )

    result = await _run_design_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode="interactive",
        detach_on_background=False,
    )

    assert result == "approved db design"


@pytest.mark.asyncio
async def test_architecture_step_gate_reentry_prefers_approved_db_plan_and_system_design(monkeypatch, tmp_path):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    set_step_status(control, slug="accounts", step="design", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-arch-reentry", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="plan:accounts",
        text="stale local plan",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="system-design:accounts",
        text='{"services":[{"name":"stale-service"}]}',
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "plan:accounts": "approved db plan",
                "system-design:accounts": '{"services":[{"name":"approved-service"}]}',
                "prd:accounts": "approved prd",
                "design:accounts": "approved design",
                "decomposition": decomposition.model_dump_json(),
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "plan:broad": "broad plan",
                "decisions:broad": "",
                "decisions:accounts": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        feature_store=None,
    )
    gate_inputs: list[tuple[str, str]] = []
    hosted_sd_inputs: list[str] = []

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal", thread_ts="", label="Accounts")

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        gate_inputs.append((kwargs["artifact_key"], kwargs["artifact"]))
        return kwargs["artifact"], kwargs["artifact"]

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_convert_and_host_sd(self, _runner, _feature, _sd_key, sd_text, _sf_name):
        hosted_sd_inputs.append(sd_text)
        return sd_text

    async def _fake_rehost_plan_and_sd(self, *_args, **_kwargs):
        return None

    async def _fake_load_completed(*args, **kwargs):
        return {}, {}

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.gate_and_revise",
        _fake_gate_and_revise,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_completed_stage_maps",
        _fake_load_completed,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name="actor"),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.build_subfeature_context_text",
        lambda *args, **kwargs: "context",
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        lambda *args, **kwargs: asyncio.sleep(0, result=("/tmp/context.md", "/tmp/manifest.md", "planning-index-architecture:accounts")),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.push_artifact_if_present",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._convert_and_host_sd",
        _fake_convert_and_host_sd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._rehost_plan_and_sd",
        _fake_rehost_plan_and_sd,
    )

    result = await _run_architecture_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode="interactive",
        detach_on_background=False,
    )

    assert result == "approved db plan"
    assert gate_inputs == [
        ("plan:accounts", "approved db plan"),
        ("system-design:accounts", '{"services":[{"name":"approved-service"}]}'),
    ]
    assert hosted_sd_inputs == ['{"services":[{"name":"approved-service"}]}']


@pytest.mark.asyncio
async def test_architecture_step_gate_reentry_uses_regenerated_system_design_after_plan_revision(monkeypatch, tmp_path):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    set_step_status(control, slug="accounts", step="design", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-arch-reentry-revised", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "plan:accounts": "approved db plan",
                "system-design:accounts": '{"services":[{"name":"approved-service"}]}',
                "prd:accounts": "approved prd",
                "design:accounts": "approved design",
                "decomposition": decomposition.model_dump_json(),
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "plan:broad": "broad plan",
                "decisions:broad": "",
                "decisions:accounts": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        feature_store=None,
    )
    gate_inputs: list[tuple[str, str]] = []

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal", thread_ts="", label="Accounts")

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        gate_inputs.append((kwargs["artifact_key"], kwargs["artifact"]))
        if kwargs["artifact_key"] == "plan:accounts":
            await kwargs["post_update"]("plan:accounts", "revised db plan")
            return "revised db plan", "revised db plan"
        return kwargs["artifact"], kwargs["artifact"]

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_convert_and_host_sd(self, _runner, _feature, _sd_key, sd_text, _sf_name):
        del self, _runner, _feature, _sd_key, _sf_name
        if sd_text == "revised db plan":
            return '{"services":[{"name":"revised-service"}]}'
        return sd_text

    async def _fake_rehost_plan_and_sd(self, _runner, _feature, _plan_key, _sd_key, _sf_name, plan_text):
        del self, _runner, _feature, _plan_key, _sd_key, _sf_name
        assert plan_text == "revised db plan"
        return '{"services":[{"name":"revised-service"}]}'

    async def _fake_load_completed(*args, **kwargs):
        return {}, {}

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.gate_and_revise",
        _fake_gate_and_revise,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_completed_stage_maps",
        _fake_load_completed,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name="actor"),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.build_subfeature_context_text",
        lambda *args, **kwargs: "context",
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        lambda *args, **kwargs: asyncio.sleep(0, result=("/tmp/context.md", "/tmp/manifest.md", "planning-index-architecture:accounts")),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.push_artifact_if_present",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._convert_and_host_sd",
        _fake_convert_and_host_sd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._rehost_plan_and_sd",
        _fake_rehost_plan_and_sd,
    )

    result = await _run_architecture_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode="interactive",
        detach_on_background=False,
    )

    assert result == "revised db plan"
    assert gate_inputs == [
        ("plan:accounts", "approved db plan"),
        ("system-design:accounts", '{"services":[{"name":"revised-service"}]}'),
    ]
    assert ("system-design:accounts", '{"services":[{"name":"revised-service"}]}') in artifacts.put_calls


@pytest.mark.asyncio
async def test_pm_step_fresh_start_uses_planning_index_context_and_clears_sessions(monkeypatch):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-pm-index", metadata={})
    prompts: list[str] = []
    actor_context_keys: list[list[str]] = []
    cleared: list[str] = []

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "broad prd",
                "decomposition": decomposition.model_dump_json(),
                "decisions:broad": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_prepare(*args, **kwargs):
        return "/tmp/pm-context.md", "/tmp/pm-manifest.md", "planning-index-pm:accounts"

    async def _fake_complete(*args, **kwargs):
        assert kwargs["result"] == "pm draft"
        return "approved pm"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_clear(_runner, actor, _feature):
        cleared.append(actor.name)

    async def _fake_run(task, feature, *, phase_name):
        del feature, phase_name
        prompts.append(task.initial_prompt)
        return "pm draft"

    def _fake_make_actor(*args, **kwargs):
        actor_context_keys.append(list(kwargs["context_keys"]))
        return SimpleNamespace(name=kwargs["suffix"], role=SimpleNamespace(metadata={}))

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal.thread.accounts", thread_ts="", label="Accounts"),
        ),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._complete_single_artifact_step",
        _fake_complete,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        _fake_make_actor,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ThreadedHostedInterview",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._clear_agent_session",
        _fake_clear,
    )

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
        run=_fake_run,
    )

    result = await _run_pm_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode=STEP_AGENT_FILL,
        detach_on_background=False,
    )

    assert result == "approved pm"
    assert actor_context_keys == [
        ["project", "scope", "planning-index-pm:accounts"],
        ["project", "scope", "planning-index-pm:accounts"],
    ]
    assert cleared == ["pm", "pm-shadow"]
    assert "Read `/tmp/pm-manifest.md` before proceeding." in prompts[0]
    assert "Use `/tmp/pm-context.md` as the overview/reference." in prompts[0]


@pytest.mark.asyncio
async def test_design_step_fresh_start_uses_planning_index_context_and_clears_sessions(monkeypatch):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-design-index", metadata={})
    prompts: list[str] = []
    actor_context_keys: list[list[str]] = []
    cleared: list[str] = []

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "decomposition": decomposition.model_dump_json(),
                "prd:accounts": "approved prd",
                "decisions:broad": "",
                "decisions:accounts": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_prepare(*args, **kwargs):
        return "/tmp/design-context.md", "/tmp/design-manifest.md", "planning-index-design:accounts"

    async def _fake_complete(*args, **kwargs):
        assert kwargs["result"] == "design draft"
        return "approved design"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_host_mockup(*args, **kwargs):
        return "https://example.test/mockup"

    async def _fake_clear(_runner, actor, _feature):
        cleared.append(actor.name)

    async def _fake_run(task, feature, *, phase_name):
        del feature, phase_name
        prompts.append(task.initial_prompt)
        return "design draft"

    def _fake_make_actor(*args, **kwargs):
        actor_context_keys.append(list(kwargs["context_keys"]))
        return SimpleNamespace(name=kwargs["suffix"], role=SimpleNamespace(metadata={}))

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal.thread.accounts", thread_ts="", label="Accounts"),
        ),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._complete_single_artifact_step",
        _fake_complete,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.design.DesignPhase._host_sf_mockup",
        _fake_host_mockup,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        _fake_make_actor,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ThreadedHostedInterview",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._clear_agent_session",
        _fake_clear,
    )

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
        run=_fake_run,
    )

    result = await _run_design_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode=STEP_AGENT_FILL,
        detach_on_background=False,
    )

    assert result == "approved design"
    assert actor_context_keys == [
        ["project", "scope", "planning-index-design:accounts"],
        ["project", "scope", "planning-index-design:accounts"],
    ]
    assert cleared == ["design", "design-shadow"]
    assert "Read `/tmp/design-manifest.md` before proceeding." in prompts[0]
    assert "Use `/tmp/design-context.md` as the overview/reference." in prompts[0]


@pytest.mark.asyncio
async def test_architecture_step_fresh_start_uses_planning_index_context_and_clears_sessions(monkeypatch):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    set_step_status(control, slug="accounts", step="design", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-arch-index", metadata={})
    prompts: list[str] = []
    actor_context_keys: list[list[str]] = []
    cleared: list[str] = []

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "plan:broad": "broad plan",
                "decomposition": decomposition.model_dump_json(),
                "prd:accounts": "approved prd",
                "design:accounts": "approved design",
                "decisions:broad": "",
                "decisions:accounts": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_prepare(*args, **kwargs):
        return "/tmp/arch-context.md", "/tmp/arch-manifest.md", "planning-index-architecture:accounts"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_convert_and_host_sd(self, _runner, _feature, _sd_key, sd_text, _sf_name):
        del self, _runner, _feature, _sd_key, _sf_name
        return sd_text

    async def _fake_rehost_plan_and_sd(self, *_args, **_kwargs):
        return '{"services":[]}'

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        return kwargs["artifact"], kwargs["artifact"]

    async def _fake_clear(_runner, actor, _feature):
        cleared.append(actor.name)

    async def _fake_run(task, feature, *, phase_name):
        del feature, phase_name
        prompts.append(task.initial_prompt)
        return SimpleNamespace(
            output=SimpleNamespace(
                plan="fresh plan",
                system_design='{"services":[]}',
            )
        )

    def _fake_make_actor(*args, **kwargs):
        actor_context_keys.append(list(kwargs["context_keys"]))
        return SimpleNamespace(name=kwargs["suffix"], role=SimpleNamespace(metadata={}))

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal.thread.accounts", thread_ts="", label="Accounts"),
        ),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.gate_and_revise",
        _fake_gate_and_revise,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        _fake_make_actor,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ThreadedHostedInterview",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._clear_agent_session",
        _fake_clear,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._convert_and_host_sd",
        _fake_convert_and_host_sd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.architecture.ArchitecturePhase._rehost_plan_and_sd",
        _fake_rehost_plan_and_sd,
    )

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
        run=_fake_run,
    )

    result = await _run_architecture_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode=STEP_AGENT_FILL,
        detach_on_background=False,
    )

    assert result == "fresh plan"
    assert actor_context_keys == [
        ["project", "scope", "planning-index-architecture:accounts"],
        ["project", "scope", "planning-index-architecture:accounts"],
    ]
    assert cleared == ["architecture", "architecture-shadow"]
    assert "Read `/tmp/arch-manifest.md` before proceeding." in prompts[0]
    assert "Use `/tmp/arch-context.md` as the overview/reference." in prompts[0]


@pytest.mark.asyncio
async def test_test_planning_step_fresh_start_uses_planning_index_context_and_clears_sessions(monkeypatch):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    set_step_status(control, slug="accounts", step="design", status=STEP_COMPLETE)
    set_step_status(control, slug="accounts", step="architecture", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-test-index", metadata={})
    prompts: list[str] = []
    actor_context_keys: list[list[str]] = []
    cleared: list[str] = []

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "plan:broad": "broad plan",
                "decomposition": decomposition.model_dump_json(),
                "prd:accounts": "approved prd",
                "design:accounts": "approved design",
                "plan:accounts": "approved plan",
                "system-design:accounts": '{"services":[]}',
                "decisions:broad": "",
                "decisions:accounts": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_prepare(*args, **kwargs):
        return "/tmp/test-context.md", "/tmp/test-manifest.md", "planning-index-test-planning:accounts"

    async def _fake_complete(*args, **kwargs):
        assert kwargs["result"] == "test draft"
        return "approved test plan"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _fake_clear(_runner, actor, _feature):
        cleared.append(actor.name)

    async def _fake_run(task, feature, *, phase_name):
        del feature, phase_name
        prompts.append(task.initial_prompt)
        return "test draft"

    def _fake_make_actor(*args, **kwargs):
        actor_context_keys.append(list(kwargs["context_keys"]))
        return SimpleNamespace(name=kwargs["suffix"], role=SimpleNamespace(metadata={}))

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal.thread.accounts", thread_ts="", label="Accounts"),
        ),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._complete_single_artifact_step",
        _fake_complete,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        _fake_make_actor,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ThreadedHostedInterview",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._clear_agent_session",
        _fake_clear,
    )

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
        run=_fake_run,
    )

    result = await _run_test_planning_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode=STEP_AGENT_FILL,
        detach_on_background=False,
    )

    assert result == "approved test plan"
    assert actor_context_keys == [
        ["project", "scope", "planning-index-test-planning:accounts"],
        ["project", "scope", "planning-index-test-planning:accounts"],
    ]
    assert cleared == ["test-planning", "test-planning-shadow"]
    assert "Read `/tmp/test-manifest.md` before proceeding." in prompts[0]
    assert "Use `/tmp/test-context.md` as the overview/reference." in prompts[0]


@pytest.mark.asyncio
async def test_design_step_resume_response_does_not_clear_sessions(monkeypatch):
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    set_step_status(control, slug="accounts", step="pm", status=STEP_COMPLETE)
    state = BuildState(metadata={})
    feature = SimpleNamespace(id="feat-design-resume-response", metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "broad prd",
                "design:broad": "broad design",
                "decomposition": decomposition.model_dump_json(),
                "prd:accounts": "approved prd",
                "decisions:broad": "",
                "decisions:accounts": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    async def _fake_persist(*args, **kwargs):
        return None

    async def _fake_prepare(*args, **kwargs):
        return "/tmp/design-context.md", "/tmp/design-manifest.md", "planning-index-design:accounts"

    async def _fake_continue(*args, **kwargs):
        return "resumed draft"

    async def _fake_complete(*args, **kwargs):
        assert kwargs["result"] == "resumed draft"
        return "approved design"

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _fake_generate_summary(*args, **kwargs):
        return "summary"

    async def _should_not_clear(*args, **kwargs):
        raise AssertionError("resume continuation should not clear fresh sessions")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.persist_planning_control",
        _fake_persist,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(thread_id="subfeature:accounts", resolver="terminal.thread.accounts", thread_ts="", label="Accounts"),
        ),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.prepare_subfeature_context_artifacts",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.continue_threaded_interview_in_background",
        _fake_continue,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._complete_single_artifact_step",
        _fake_complete,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.refresh_decision_ledger",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.generate_summary",
        _fake_generate_summary,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_user",
        lambda base_user, *, resolver: base_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.make_thread_actor",
        lambda *args, **kwargs: SimpleNamespace(name=kwargs["suffix"], role=SimpleNamespace(metadata={})),
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._clear_agent_session",
        _should_not_clear,
    )

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
        run=lambda *args, **kwargs: None,
    )

    result = await _run_design_step(
        runner,
        feature,
        state,
        control,
        asyncio.Lock(),
        asyncio.Lock(),
        decomposition,
        decomposition.subfeatures[0],
        mode=STEP_AGENT_FILL,
        resume_response="pending response",
        detach_on_background=False,
    )

    assert result == "approved design"


@pytest.mark.asyncio
async def test_broad_phase_resumes_at_reconciliation_when_broad_artifacts_are_complete(monkeypatch):
    phase = BroadPhase()
    decomposition = _decomposition()
    control = default_planning_control()
    for step in ("prd", "design", "architecture", "decomposition"):
        set_step_status(control, step=step, status=STEP_COMPLETE, provenance="human")
    state = BuildState(metadata={"planning_control": control})
    feature = SimpleNamespace(id="feat-broad", name="Feature", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:broad": "approved broad prd",
                "design:broad": "approved broad design",
                "plan:broad": "approved broad plan",
                "decomposition": decomposition.model_dump_json(),
                "integration-review:broad": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
    )
    order: list[str] = []
    threaded_user = SimpleNamespace(name="thread-user", resolver="terminal")

    async def _fake_ensure_thread(*args, **kwargs):
        return SimpleNamespace(thread_id="thread", resolver="terminal", thread_ts="", label="label")

    async def _fake_push(*args, **kwargs):
        return None

    async def _fake_review(*args, **kwargs):
        order.append("review")
        assert kwargs["use_cached_review"] is False
        assert kwargs["artifact_keys_by_target"]["architecture"] == "plan:broad"
        assert kwargs["responder"] is threaded_user
        assert kwargs["prefer_local_artifacts"] is True
        return IntegrationReview(needs_revision=False, summary="clean")

    async def _fake_collect(_runner, _feature, _state, control_arg, _decomposition, *, phase_name):
        del _runner, _feature, _state, _decomposition, phase_name
        order.append("collect")
        assert not control_arg["subfeatures"]

    async def _unexpected_run(*args, **kwargs):
        raise AssertionError("broad resume should not reopen draft interviews or gates")

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.push_artifact_if_present",
        _fake_push,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.integration_review",
        _fake_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.make_thread_actor",
        lambda actor, **kwargs: actor,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad.make_thread_user",
        lambda base_user, *, resolver: threaded_user,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.broad._collect_subfeature_step_policies",
        _fake_collect,
    )
    runner.run = _unexpected_run

    result = await phase.execute(runner, feature, state)

    assert result.prd == "approved broad prd"
    assert result.design == "approved broad design"
    assert result.plan == "approved broad plan"
    assert json.loads(result.decomposition)["subfeatures"][0]["slug"] == "accounts"
    assert order == ["review", "collect"]
    assert state.metadata["planning_control"]["broad_steps"]["reconciliation"]["status"] == STEP_COMPLETE
    assert state.metadata["planning_control"]["current_stage"] == "subfeature"


@pytest.mark.asyncio
async def test_subfeature_phase_backfills_legacy_reconciliation_gate(monkeypatch):
    phase = SubfeaturePhase()
    state = BuildState(
        metadata={
            "planning_control": {
                "broad_steps": {"prd": {"status": "complete"}},
            }
        }
    )
    feature = SimpleNamespace(id="feat-subfeature", metadata={"_db_phase": "subfeature"})
    decomposition = SubfeatureDecomposition(complete=True)
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={}, feature_store=None)

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_global_prd(*args, **kwargs):
        return []

    async def _fake_global_design(*args, **kwargs):
        return []

    async def _fake_global_arch(*args, **kwargs):
        return []

    async def _fake_compile(*args, **kwargs):
        return ""

    async def _fake_sync(*args, **kwargs):
        return kwargs["plan_text"], kwargs["system_design_text"]

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_decomposition",
        _fake_load_decomposition,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_prd_tail",
        _fake_global_prd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_design_tail",
        _fake_global_design,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_architecture_tail",
        _fake_global_arch,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_decision_ledger",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.sync_compiled_decision_mirrors",
        _fake_sync,
    )

    result = await phase.execute(runner, feature, state)

    reconciliation = result.metadata["planning_control"]["broad_steps"]["reconciliation"]
    assert reconciliation["status"] == STEP_COMPLETE
    assert reconciliation["provenance"] == "legacy_compat"


@pytest.mark.asyncio
async def test_subfeature_phase_does_not_backfill_reconciliation_for_broad_stage(monkeypatch):
    phase = SubfeaturePhase()
    state = BuildState(
        metadata={
            "planning_control": {
                "broad_steps": {"prd": {"status": "complete"}},
            }
        }
    )
    feature = SimpleNamespace(id="feat-broad-stage", metadata={"_db_phase": "broad"})
    decomposition = SubfeatureDecomposition(complete=True)
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={}, feature_store=None)

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_global_prd(*args, **kwargs):
        return []

    async def _fake_global_design(*args, **kwargs):
        return []

    async def _fake_global_arch(*args, **kwargs):
        return []

    async def _fake_compile(*args, **kwargs):
        return ""

    async def _fake_sync(*args, **kwargs):
        return kwargs["plan_text"], kwargs["system_design_text"]

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_decomposition",
        _fake_load_decomposition,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_prd_tail",
        _fake_global_prd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_design_tail",
        _fake_global_design,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_architecture_tail",
        _fake_global_arch,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_decision_ledger",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.sync_compiled_decision_mirrors",
        _fake_sync,
    )

    result = await phase.execute(runner, feature, state)

    reconciliation = result.metadata["planning_control"]["broad_steps"]["reconciliation"]
    assert reconciliation["status"] != STEP_COMPLETE


@pytest.mark.asyncio
async def test_subfeature_phase_syncs_pruned_threads_on_start(monkeypatch):
    phase = SubfeaturePhase()
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    control["subfeatures"]["legacy"] = {
        "thread_id": "subfeature:legacy",
        "resolver": "terminal.thread.subfeature:legacy",
        "thread_ts": "",
        "label": "Legacy",
        "status": STEP_PENDING,
        "background_task": {"active": False, "status": "", "step": "", "reason": ""},
        "steps": {
            "pm": {"status": STEP_COMPLETE, "mode": "interactive", "mode_selected": True},
            "design": {"status": STEP_COMPLETE, "mode": "interactive", "mode_selected": True},
            "architecture": {"status": STEP_COMPLETE, "mode": "interactive", "mode_selected": True},
            "test_planning": {"status": STEP_COMPLETE, "mode": "interactive", "mode_selected": True},
        },
    }
    for broad_step in ("prd", "design", "architecture", "reconciliation"):
        control["broad_steps"][broad_step]["status"] = STEP_COMPLETE
    for step in ("pm", "design", "architecture", "test_planning"):
        set_step_status(control, slug="accounts", step=step, status=STEP_COMPLETE)

    state = BuildState(metadata={"planning_control": control})
    feature = SimpleNamespace(id="feat-prune", metadata={})
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={}, feature_store=None)

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_global_prd(*args, **kwargs):
        return []

    async def _fake_global_design(*args, **kwargs):
        return []

    async def _fake_global_arch(*args, **kwargs):
        return []

    async def _fake_compile(*args, **kwargs):
        return ""

    async def _fake_sync(*args, **kwargs):
        return kwargs["plan_text"], kwargs["system_design_text"]

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_decomposition",
        _fake_load_decomposition,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_prd_tail",
        _fake_global_prd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_design_tail",
        _fake_global_design,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_architecture_tail",
        _fake_global_arch,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_decision_ledger",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.sync_compiled_decision_mirrors",
        _fake_sync,
    )

    result = await phase.execute(runner, feature, state)

    assert sorted(result.metadata["planning_control"]["subfeatures"]) == ["accounts"]


@pytest.mark.asyncio
async def test_subfeature_phase_prompts_ready_threads_in_parallel_and_launches_background_work(monkeypatch):
    phase = SubfeaturePhase()
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing"),
        ],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    for broad_step in ("prd", "design", "architecture", "reconciliation"):
        control["broad_steps"][broad_step]["status"] = STEP_COMPLETE
    for slug in ("accounts", "billing"):
        set_step_status(control, slug=slug, step="design", status=STEP_COMPLETE)
        set_step_status(control, slug=slug, step="architecture", status=STEP_COMPLETE)
        set_step_status(control, slug=slug, step="test_planning", status=STEP_COMPLETE)
    state = BuildState(metadata={"planning_control": control})
    feature = SimpleNamespace(id="feat-parallel", metadata={})
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={}, feature_store=None)

    prompt_barrier = asyncio.Event()
    launch_barrier = asyncio.Event()
    prompt_resolvers: list[str] = []
    prompt_calls: list[str] = []
    step_calls: list[tuple[str, str]] = []

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_ensure_thread(*args, **kwargs):
        thread_id = kwargs["thread_id"]
        return SimpleNamespace(
            thread_id=thread_id,
            resolver=f"terminal.thread.{thread_id}",
            thread_ts="",
            label=kwargs["label"],
        )

    async def _fake_choose(_runner, _feature, *, chooser, phase_name, prompt):
        del _runner, _feature, phase_name
        prompt_resolvers.append(chooser.resolver)
        prompt_calls.append(prompt)
        if len(prompt_calls) == 2:
            prompt_barrier.set()
        await prompt_barrier.wait()
        return "Finish in background"

    async def _fake_run_pm_step(
        _runner,
        _feature,
        _state,
        _control,
        _control_lock,
        _subfeature_lock,
        _decomposition,
        sf,
        *,
        mode,
        resume_response=None,
        detach_on_background,
    ):
        del _runner, _feature, _state, _control_lock, _subfeature_lock, _decomposition, resume_response, detach_on_background
        step_calls.append((sf.slug, mode))
        if len(step_calls) == 2:
            launch_barrier.set()
        await launch_barrier.wait()
        set_step_status(_control, slug=sf.slug, step="pm", status=STEP_COMPLETE, provenance="agent_fill")
        return f"{sf.slug}:{mode}"

    async def _fake_global_prd(*args, **kwargs):
        return []

    async def _fake_global_design(*args, **kwargs):
        return []

    async def _fake_global_arch(*args, **kwargs):
        return []

    async def _fake_compile(*args, **kwargs):
        return ""

    async def _fake_sync(*args, **kwargs):
        return kwargs["plan_text"], kwargs["system_design_text"]

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_decomposition",
        _fake_load_decomposition,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.choose_step_mode",
        _fake_choose,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_pm_step",
        _fake_run_pm_step,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_prd_tail",
        _fake_global_prd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_design_tail",
        _fake_global_design,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_architecture_tail",
        _fake_global_arch,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_decision_ledger",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.sync_compiled_decision_mirrors",
        _fake_sync,
    )

    result = await phase.execute(runner, feature, state)

    assert sorted(prompt_calls) == [
        "How should I handle Accounts — PM?",
        "How should I handle Billing — PM?",
    ]
    assert sorted(prompt_resolvers) == [
        "terminal.thread.subfeature:accounts",
        "terminal.thread.subfeature:billing",
    ]
    assert sorted(step_calls) == [
        ("accounts", STEP_AGENT_FILL),
        ("billing", STEP_AGENT_FILL),
    ]
    for slug in ("accounts", "billing"):
        step_record = result.metadata["planning_control"]["subfeatures"][slug]["steps"]["pm"]
        assert step_record["mode_selected"] is True
        assert step_record["mode"] == STEP_AGENT_FILL


@pytest.mark.asyncio
async def test_subfeature_phase_does_not_deadlock_on_cyclic_edges(monkeypatch):
    phase = SubfeaturePhase()
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing"),
        ],
        edges=[
            SubfeatureEdge(
                from_subfeature="accounts",
                to_subfeature="billing",
                interface_type="api_call",
                description="Billing consumes account identity",
            ),
            SubfeatureEdge(
                from_subfeature="billing",
                to_subfeature="accounts",
                interface_type="event",
                description="Accounts receives billing updates",
            ),
        ],
        complete=True,
    )
    control = default_planning_control()
    ensure_subfeature_threads(control, decomposition)
    for broad_step in ("prd", "design", "architecture", "reconciliation"):
        control["broad_steps"][broad_step]["status"] = STEP_COMPLETE
    for slug in ("accounts", "billing"):
        set_step_status(control, slug=slug, step="design", status=STEP_COMPLETE)
        set_step_status(control, slug=slug, step="architecture", status=STEP_COMPLETE)
        set_step_status(control, slug=slug, step="test_planning", status=STEP_COMPLETE)
    state = BuildState(metadata={"planning_control": control})
    feature = SimpleNamespace(id="feat-cycle", metadata={})
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={}, feature_store=None)

    step_calls: list[tuple[str, str]] = []

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_ensure_thread(*args, **kwargs):
        thread_id = kwargs["thread_id"]
        return SimpleNamespace(
            thread_id=thread_id,
            resolver=f"terminal.thread.{thread_id}",
            thread_ts="",
            label=kwargs["label"],
        )

    async def _fake_choose(*args, **kwargs):
        return "Finish in background"

    async def _fake_run_pm_step(
        _runner,
        _feature,
        _state,
        _control,
        _control_lock,
        _subfeature_lock,
        _decomposition,
        sf,
        *,
        mode,
        resume_response=None,
        detach_on_background,
    ):
        del _runner, _feature, _state, _control_lock, _subfeature_lock, _decomposition, resume_response, detach_on_background
        step_calls.append((sf.slug, mode))
        set_step_status(_control, slug=sf.slug, step="pm", status=STEP_COMPLETE, provenance="agent_fill")
        return f"{sf.slug}:{mode}"

    async def _fake_global_prd(*args, **kwargs):
        return []

    async def _fake_global_design(*args, **kwargs):
        return []

    async def _fake_global_arch(*args, **kwargs):
        return []

    async def _fake_compile(*args, **kwargs):
        return ""

    async def _fake_sync(*args, **kwargs):
        return kwargs["plan_text"], kwargs["system_design_text"]

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._load_decomposition",
        _fake_load_decomposition,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.ensure_planning_thread",
        _fake_ensure_thread,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.choose_step_mode",
        _fake_choose,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_pm_step",
        _fake_run_pm_step,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_prd_tail",
        _fake_global_prd,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_design_tail",
        _fake_global_design,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature._run_global_architecture_tail",
        _fake_global_arch,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.compile_decision_ledger",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.subfeature.sync_compiled_decision_mirrors",
        _fake_sync,
    )

    result = await phase.execute(runner, feature, state)

    assert sorted(step_calls) == [
        ("accounts", STEP_AGENT_FILL),
        ("billing", STEP_AGENT_FILL),
    ]
    for slug in ("accounts", "billing"):
        assert result.metadata["planning_control"]["subfeatures"][slug]["steps"]["pm"]["status"] == STEP_COMPLETE


@pytest.mark.asyncio
async def test_plan_review_still_uses_decomposition_edges_for_edge_reviews(monkeypatch):
    phase = PlanReviewPhase()
    decomposition = _decomposition()
    state = BuildState(decomposition=decomposition.model_dump_json())
    feature = SimpleNamespace(id="feat-plan-review-edges", metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "decomposition":
                return decomposition.model_dump_json()
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
    )
    edge_contexts: list[tuple[str, str]] = []

    async def _fake_sf_context(*args, **kwargs):
        return "sf context"

    async def _fake_edge_context(_runner, _feature, edge, _decomposition):
        edge_contexts.append((edge.from_subfeature, edge.to_subfeature))
        return "edge context"

    async def _fake_run(task, feature, phase_name):
        del task, feature, phase_name
        return Verdict(approved=True, summary="ok")

    async def _fake_run_gates(self, runner, feature, state, decomposition):
        del self, runner, feature, decomposition
        state.metadata["ran_gates"] = True
        return state

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.plan_review._build_sf_review_context",
        _fake_sf_context,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.plan_review._build_edge_review_context",
        _fake_edge_context,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.plan_review.PlanReviewPhase._run_gates",
        _fake_run_gates,
    )
    runner.run = _fake_run

    result = await phase.execute(runner, feature, state)

    assert edge_contexts == [("accounts", "billing")]
    assert result.metadata["ran_gates"] is True


@pytest.mark.asyncio
async def test_scoping_phase_reuses_existing_scope_draft_and_marks_approval(monkeypatch):
    phase = ScopingPhase()
    feature = SimpleNamespace(id="feat-1", name="Feature", slug="feature", metadata={})
    state = BuildState()
    scope_model = ScopeOutput(
        summary="Summary",
        scope_type="new_application",
        repos=[RepoSpec(name="repo-a", action="new")],
        complete=True,
    )
    scope_text = "# Scope\n\nApproved scope"

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "scope:approved": "",
                "scope": scope_text,
                "scope:draft": scope_model.model_dump_json(indent=2),
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _WorkspaceManager:
        def __init__(self) -> None:
            self.calls: list[ScopeOutput] = []

        async def setup_feature_workspace(self, feature, scope):
            del feature
            self.calls.append(scope)
            return SimpleNamespace(model_dump_json=lambda indent=2: '{"project":"ok"}')

    artifacts = _Artifacts()
    workspace_manager = _WorkspaceManager()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"workspace_manager": workspace_manager},
    )

    async def _unexpected_run(*args, **kwargs):
        raise AssertionError("scoping interview should not rerun when a draft scope exists")

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        assert kwargs["artifact"] == scope_model
        assert kwargs["hosted_revision"] is True
        assert kwargs["prefer_structured_output"] is True
        return scope_model, scope_text

    runner.run = _unexpected_run
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.scoping.gate_and_revise",
        _fake_gate_and_revise,
    )

    result = await phase.execute(runner, feature, state)

    expected_scope = to_markdown(scope_model)
    assert result.scope == expected_scope
    assert ("scope", expected_scope) in artifacts.put_calls
    assert ("scope:approved", "approved") in artifacts.put_calls
    assert ("scope:draft", scope_model.model_dump_json(indent=2)) in artifacts.put_calls
    assert ('project', '{"project":"ok"}') in artifacts.put_calls
    assert workspace_manager.calls == [scope_model]


@pytest.mark.asyncio
async def test_scoping_phase_recovers_structured_scope_after_legacy_resume(monkeypatch):
    phase = ScopingPhase()
    feature = SimpleNamespace(id="feat-2", name="Feature", slug="feature", metadata={})
    state = BuildState()
    scope_text = "# Scope\n\nApproved legacy scope"
    recovered_scope = ScopeOutput(
        summary="Recovered",
        scope_type="service_change",
        repos=[RepoSpec(name="repo-b", action="extend")],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "scope:approved": "",
                "scope": scope_text,
                "scope:draft": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _WorkspaceManager:
        def __init__(self) -> None:
            self.calls: list[ScopeOutput] = []

        async def setup_feature_workspace(self, feature, scope):
            del feature
            self.calls.append(scope)
            return SimpleNamespace(model_dump_json=lambda indent=2: '{"project":"ok"}')

    artifacts = _Artifacts()
    workspace_manager = _WorkspaceManager()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"workspace_manager": workspace_manager},
    )

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        assert task.output_type is ScopeOutput
        return recovered_scope

    async def _fake_gate_and_revise(_runner, _feature, _phase_name, **kwargs):
        assert kwargs["artifact"] == scope_text
        assert kwargs["hosted_revision"] is True
        assert kwargs["prefer_structured_output"] is True
        return scope_text, scope_text

    runner.run = _fake_run
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.scoping.gate_and_revise",
        _fake_gate_and_revise,
    )

    result = await phase.execute(runner, feature, state)

    expected_scope = to_markdown(recovered_scope)
    assert result.scope == expected_scope
    assert ("scope", expected_scope) in artifacts.put_calls
    assert ("scope:approved", "approved") in artifacts.put_calls
    assert ("scope:draft", recovered_scope.model_dump_json(indent=2)) in artifacts.put_calls
    assert workspace_manager.calls == [recovered_scope]


@pytest.mark.asyncio
async def test_scoping_phase_rebuilds_legacy_project_after_approved_scope(monkeypatch):
    phase = ScopingPhase()
    feature = SimpleNamespace(id="feat-3", name="Feature", slug="feature", metadata={})
    state = BuildState(scope="# stale")
    scope_model = ScopeOutput(
        summary="Summary",
        scope_type="new_application",
        repos=[
            RepoSpec(name="iriai-build-v2", local_path="iriai-build-v2", action="read_only"),
            RepoSpec(
                name="iriai-build-v2/dashboard-ui",
                local_path="iriai-build-v2/dashboard-ui",
                github_url="https://github.com/thedanielzhang/iriai-build-v2",
                action="read_only",
                relevance="dashboard refs",
            ),
        ],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "scope:approved": "approved",
                "scope": "# stale",
                "scope:draft": scope_model.model_dump_json(indent=2),
                "project": "Project workspace: /tmp/workspace",
                "decisions:broad": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _WorkspaceManager:
        def __init__(self) -> None:
            self.calls: list[ScopeOutput] = []

        async def setup_feature_workspace(self, feature, scope):
            del feature
            self.calls.append(scope)
            return ProjectContext(
                feature_name="Feature",
                scope_type=scope.scope_type,
                repos=scope.repos,
                worktree_root="/tmp/worktrees",
                workspace_path="/tmp/workspace",
                outputs_path="/tmp/outputs",
            )

    artifacts = _Artifacts()
    workspace_manager = _WorkspaceManager()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"workspace_manager": workspace_manager},
        run=None,
    )

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _unexpected_run(*args, **kwargs):
        raise AssertionError("approved scope should not reopen the interview")

    runner.run = _unexpected_run
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.scoping.refresh_decision_ledger",
        _fake_refresh,
    )

    result = await phase.execute(runner, feature, state)

    assert result.scope == to_markdown(scope_model)
    assert any(key == "project" and value.startswith("{") for key, value in artifacts.put_calls)
    assert workspace_manager.calls == [scope_model]


@pytest.mark.asyncio
async def test_scoping_phase_normalizes_nested_repo_paths_before_workspace_setup(tmp_path, monkeypatch):
    phase = ScopingPhase()
    feature = SimpleNamespace(id="feat-4", name="Feature", slug="feature", metadata={})
    state = BuildState(scope="# stale")

    repo_root = tmp_path / "iriai-build-v2"
    repo_root.mkdir(parents=True)
    (repo_root / ".git").mkdir()
    (repo_root / "dashboard-ui").mkdir()

    scope_model = ScopeOutput(
        summary="Summary",
        scope_type="new_application",
        repos=[
            RepoSpec(
                name="iriai-build-v2/dashboard-ui",
                local_path="iriai-build-v2/dashboard-ui",
                github_url="https://github.com/thedanielzhang/iriai-build-v2",
                action="read_only",
                relevance="dashboard refs",
            ),
        ],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {
                "scope:approved": "approved",
                "scope": "# stale",
                "scope:draft": scope_model.model_dump_json(indent=2),
                "project": "",
                "decisions:broad": "",
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    class _WorkspaceManager:
        def __init__(self) -> None:
            self._base = tmp_path
            self.calls: list[ScopeOutput] = []

        async def setup_feature_workspace(self, feature, scope):
            del feature
            self.calls.append(scope)
            return ProjectContext(
                feature_name="Feature",
                scope_type=scope.scope_type,
                repos=scope.repos,
                worktree_root="/tmp/worktrees",
                workspace_path=str(tmp_path),
                outputs_path="/tmp/outputs",
            )

    artifacts = _Artifacts()
    workspace_manager = _WorkspaceManager()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"workspace_manager": workspace_manager},
        run=None,
    )

    async def _fake_refresh(*args, **kwargs):
        return None

    async def _unexpected_run(*args, **kwargs):
        raise AssertionError("approved scope should not reopen the interview")

    runner.run = _unexpected_run
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.scoping.refresh_decision_ledger",
        _fake_refresh,
    )

    await phase.execute(runner, feature, state)

    normalized_scope = workspace_manager.calls[0]
    assert len(normalized_scope.repos) == 1
    assert normalized_scope.repos[0].name == "iriai-build-v2"
    assert normalized_scope.repos[0].local_path == "iriai-build-v2"
    assert "dashboard-ui" in normalized_scope.repos[0].relevance


def test_interaction_actor_for_phase_uses_auto_only_for_autonomous_remainder():
    runner = SimpleNamespace(services={"autonomous_remainder": True})

    feature = SimpleNamespace(metadata={"_db_phase": "plan-review"})
    actor = interaction_actor_for_phase(
        runner,
        feature,
        phase_name="plan-review",
        fallback=user,
    )
    assert actor.resolver == "auto"

    early_feature = SimpleNamespace(metadata={"_db_phase": "subfeature"})
    early_actor = interaction_actor_for_phase(
        runner,
        early_feature,
        phase_name="subfeature",
        fallback=user,
    )
    assert early_actor is user


def test_apply_patches_supports_common_alias_operations():
    text = "## Overview\nold text\n\n## Risks\nkeep\n"
    patches = [
        SimpleNamespace(
            target="Overview",
            operation="replace_section",
            content="## Overview\nnew text",
            find="",
            reasoning="",
        ),
        SimpleNamespace(
            target="Risks",
            operation="append",
            content="## Appendix\nextra",
            find="",
            reasoning="",
        ),
    ]

    revised = _apply_patches(text, patches)

    assert "new text" in revised
    assert "## Appendix\nextra" in revised


@pytest.mark.asyncio
async def test_targeted_revision_uses_manifest_paths_and_auto_responder(tmp_path):
    feature = SimpleNamespace(id="feat-auto-rev", metadata={"_db_phase": "plan-review"})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-1",
                slug="artifact-repo-phase-lifecycle",
                name="Artifact Repo Phase Lifecycle",
                description="ARL",
            )
        ],
        edges=[],
        complete=True,
    )
    mirror = _TestMirror(tmp_path / "features")
    existing_path = _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:artifact-repo-phase-lifecycle",
        text="# Title\n\nCurrent body\n",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="decisions",
        text="decision ledger",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "prd:artifact-repo-phase-lifecycle": "# Title\n\nCurrent body\n",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key)

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": mirror,
                "autonomous_remainder": True,
            }
            self.calls: list[Any] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.calls.append(task)
            if isinstance(task, HostedInterview):
                q_path = (
                    mirror.feature_dir("feat-auto-rev")
                    / ".staging"
                    / "subfeatures"
                    / "prd:artifact-repo-phase-lifecycle"
                    / "revision-questions.md"
                )
                q_path.parent.mkdir(parents=True, exist_ok=True)
                q_path.write_text(
                    "Proceed with reasonable assumptions.",
                    encoding="utf-8",
                )
                return Envelope(
                    question="",
                    complete=True,
                    artifact_path=str(q_path),
                    output=None,
                )
            if len([c for c in self.calls if isinstance(c, Ask)]) == 1:
                return ArtifactPatchSet(patches=[], summary="Need clarification")
            return ArtifactPatchSet(
                patches=[
                    SimpleNamespace(
                        target="FULL_DOCUMENT",
                        operation="replace",
                        content="# Title\n\nRevised body\n",
                        find="",
                        reasoning="",
                    )
                ],
                summary="",
            )

    runner = _Runner()
    revision_plan = RevisionPlan(
        requests=[
            RevisionRequest(
                description="Update the PRD with cycle-2 revisions.",
                reasoning="Plan review requested changes.",
                affected_subfeatures=["artifact-repo-phase-lifecycle"],
            )
        ]
    )

    await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=revision_plan,
        decomposition=decomposition,
        base_role=lead_pm_gate_reviewer.role,
        output_type=PRD,
        artifact_prefix="prd",
        checkpoint_prefix="gate-2",
    )

    ask_prompts = [task.prompt for task in runner.calls if isinstance(task, Ask)]
    assert any("Revision source manifest:" in prompt for prompt in ask_prompts)
    assert any(str(existing_path) in prompt for prompt in ask_prompts)

    interviews = [task for task in runner.calls if isinstance(task, HostedInterview)]
    assert len(interviews) == 1
    assert interviews[0].responder.resolver == "auto"
