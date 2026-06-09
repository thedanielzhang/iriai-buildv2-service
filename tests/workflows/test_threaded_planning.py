import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from iriai_compose import Ask

from iriai_build_v2.services.artifacts import _key_to_path, _sd_source_path
from iriai_build_v2.services.hosting import DocHostingService
from iriai_build_v2.services.markdown import to_markdown
from iriai_build_v2.models.outputs import (
    ArchitecturalRisk,
    ArtifactAuditIssue,
    ArchitectureOutput,
    ArtifactPatchSet,
    ArtifactBackfillStatus,
    ArtifactBackfillSubfeatureStatus,
    DecisionLedger,
    DecisionRecord,
    Envelope,
    FileScope,
    GateReviewLedger,
    ImplementationDAG,
    ImplementationTask,
    ImplementationStep,
    IntegrationReview,
    JourneyVerification,
    JourneyVerifyStep,
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
    TaskAcceptanceCriterion,
    TaskFileScope,
    TaskReference,
    TechnicalPlan,
    TestAcceptanceCriterion as OutputTestAcceptanceCriterion,
    TestPlan as OutputTestPlan,
    VerifyBlock,
    Verdict,
    Workstream,
    WorkstreamDecomposition,
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
    _assert_compile_complete,
    _find_section,
    _parse_markdown_sections,
    _compile_piece_sentinel_ok,
    _write_revision_decision_context,
    _clear_agent_session,
    _build_subfeature_context,
    _is_model_boundary_failure,
    _offload_if_large,
    ContextPackage,
    ContextPackageItem,
    build_context_package,
    compile_artifacts,
    decompose_and_gate,
    generate_summary,
    get_existing_artifact,
    get_gate_resume_artifact,
    get_resumable_artifact,
    interview_gate_review,
    integration_review,
    targeted_revision,
)
from iriai_build_v2.workflows._common._autonomy import interaction_actor_for_phase
from iriai_build_v2.workflows._common._tasks import HostedInterview
from iriai_build_v2.workflows.planning.phases import task_planning as task_planning_module
from iriai_build_v2.workflows.planning._stage_helpers import (
    planning_index_artifact_key,
    prepare_subfeature_context_artifacts,
)
from iriai_build_v2.workflows.planning._sidecars import (
    build_structured_artifact,
    build_shared_planning_index,
    build_subfeature_planning_index,
    load_source_artifact_text,
    parity_check_structured_artifact,
    refresh_sidecar_for_source_artifact,
)
from iriai_build_v2.workflows.planning.workflow import PlanningWorkflow
from iriai_build_v2.workflows.develop.phases import ImplementationPhase, PostTestObservationPhase
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module
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


def _decision_ledger_text(*decisions: DecisionRecord) -> str:
    return to_markdown(
        DecisionLedger(
            decisions=list(decisions),
            complete=bool(decisions),
        )
    )


def _valid_task(
    *,
    task_id: str,
    slug: str,
    verification_gates: list[str] | None = None,
    dependencies: list[str] | None = None,
    step_ids: list[str] | None = None,
    requirement_ids: list[str] | None = None,
    journey_ids: list[str] | None = None,
) -> ImplementationTask:
    return ImplementationTask(
        id=task_id,
        name=f"Implement {slug}",
        description=f"{slug} task",
        subfeature_id=slug,
        step_ids=step_ids or ["STEP-1"],
        requirement_ids=requirement_ids if requirement_ids is not None else [f"REQ-{slug}"],
        journey_ids=journey_ids or [],
        acceptance_criteria=[
            TaskAcceptanceCriterion(description=f"{slug} acceptance criterion"),
        ],
        reference_material=[
            TaskReference(source="Plan STEP-1", content=f"{slug} reference material"),
            TaskReference(source=f"PRD REQ-{slug}", content=f"{slug} requirement context"),
            TaskReference(source=f"Test-Plan AC-{slug}-1", content=f"{slug} verification context"),
        ],
        verification_gates=verification_gates or [f"AC-{slug}-1"],
        dependencies=dependencies or [],
    )


def _slice_manifest_with_current_digests(
    *,
    slug: str,
    plan_text: str,
    test_plan_text: str,
    slices: list[task_planning_module.TaskPlanningSlice],
    statuses: list[task_planning_module.SlicePlanningStatus] | None = None,
    attempts: list[task_planning_module.SlicePlanningAttempt] | None = None,
) -> task_planning_module.TaskPlanningSliceManifest:
    normalized_plan = TaskPlanningPhase._normalize_artifact_markdown(plan_text, f"plan:{slug}")
    normalized_test_plan = TaskPlanningPhase._normalize_artifact_markdown(test_plan_text, "test-plan")
    return task_planning_module.TaskPlanningSliceManifest(
        slug=slug,
        slices=slices,
        statuses=statuses
        or [task_planning_module.SlicePlanningStatus(slice_id=slice_info.slice_id) for slice_info in slices],
        attempts=attempts or [],
        derivation_version=task_planning_module._SLICE_MANIFEST_DERIVATION_VERSION,
        plan_digest=TaskPlanningPhase._content_digest(normalized_plan),
        test_plan_digest=TaskPlanningPhase._content_digest(normalized_test_plan),
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


def test_task_planning_parses_markdown_test_plan_acceptance_and_scenarios():
    markdown = """
# Test Plan

## Overview

Backend setup validation.

## Acceptance Criteria

- **AC-accounts-1** — Boot succeeds.
  - linked_requirement: `REQ-1, REQ-2`
  - linked_journey_step_id: `J-ACC-1#step-1`
  - verification_method: `integration`
  - pass_condition: `/health` returns ready

- **AC-accounts-2** — Setup check emits findings.
  - linked_requirement: `REQ-3`
  - linked_journey_step_id: `J-ACC-2#step-2`
  - verification_method: `integration`
  - pass_condition: findings surface correctly

## Test Scenarios

### TS-1 — Happy path
- priority: `p0`
- linked_acceptance: `AC-accounts-1, -2`
- expected_outcome: startup succeeds

## Verification Checklist

- [ ] AC-accounts-1 covered

## Edge Cases

- AC-accounts-2 handles retries
""".strip()

    parsed = TaskPlanningPhase._parse_test_plan(markdown)

    assert parsed is not None
    assert [criterion.id for criterion in parsed.acceptance_criteria] == [
        "AC-accounts-1",
        "AC-accounts-2",
    ]
    assert parsed.acceptance_criteria[0].linked_requirement == "REQ-1, REQ-2"
    assert parsed.acceptance_criteria[0].linked_journey_step_id == "J-ACC-1#step-1"
    assert parsed.test_scenarios[0].linked_acceptance == [
        "AC-accounts-1",
        "AC-accounts-2",
    ]
    assert parsed.verification_checklist == ["AC-accounts-1 covered"]
    assert parsed.edge_cases == ["AC-accounts-2 handles retries"]


def test_task_planning_does_not_map_journey_step_suffix_to_technical_step():
    plan_markdown = """
## Implementation Steps

### STEP-1: Bootstrap

Bootstrap the backend.

### STEP-2: Finalize

REQ-2
Finalize setup.
""".strip()
    test_plan_markdown = """
## Acceptance Criteria

- **AC-accounts-1** — Journey-step-linked setup criterion.
  - linked_journey_step_id: `J-OTHER-99#step-1`
  - verification_method: `integration`
  - pass_condition: journey step one completes

- **AC-accounts-2** — Requirement-linked setup criterion.
  - linked_requirement: `REQ-2, REQ-9`
  - verification_method: `integration`
  - pass_condition: step two completes
""".strip()

    parsed = TaskPlanningPhase._parse_test_plan(test_plan_markdown)
    slices = TaskPlanningPhase._derive_atomic_slices_from_markdown_plan(
        plan_markdown,
        parsed,
        fallback_ac_ids=sorted(task_planning_module._extract_ac_ids(test_plan_markdown)),
    )

    assert [slice_info.slice_id for slice_info in slices] == ["slice-1", "slice-2"]
    assert slices[0].acceptance_criterion_ids == []
    assert slices[0].owned_acceptance_criterion_ids == []
    assert slices[0].supporting_acceptance_criterion_ids == []
    assert slices[0].strict_acceptance_criteria is False
    assert slices[1].acceptance_criterion_ids == ["AC-accounts-2"]
    assert slices[1].owned_acceptance_criterion_ids == ["AC-accounts-2"]
    assert slices[1].supporting_acceptance_criterion_ids == []
    assert slices[1].strict_acceptance_criteria is True


def test_task_planning_matches_markdown_slice_acceptance_by_requirement_nfr_and_decision_tokens():
    plan_markdown = """
## Implementation Steps

### STEP-20: Hostile repo defenses

D-GR-9
NFR-2
Protect checkout and rendering boundaries.

### STEP-21: Artifact RPC dispatcher

REQ-67
D-GR-7
Wire the dispatcher auth boundary.
""".strip()
    test_plan_markdown = """
## Acceptance Criteria

- **AC-accounts-1** — Decision-linked repo hardening criterion.
  - linked_requirement: `D-GR-9`
  - verification_method: `integration`
  - pass_condition: hardening is enforced

- **AC-accounts-2** — NFR-linked perf criterion.
  - linked_requirement: `NFR-2`
  - verification_method: `integration`
  - pass_condition: bounded latency

- **AC-accounts-3** — Mixed trace dispatcher criterion.
  - linked_requirement: `REQ-67, D-GR-7`
  - verification_method: `integration`
  - pass_condition: dispatcher auth works
""".strip()

    parsed = TaskPlanningPhase._parse_test_plan(test_plan_markdown)
    slices = TaskPlanningPhase._derive_atomic_slices_from_markdown_plan(
        plan_markdown,
        parsed,
        fallback_ac_ids=sorted(task_planning_module._extract_ac_ids(test_plan_markdown)),
    )

    assert [slice_info.slice_id for slice_info in slices] == ["slice-1", "slice-2"]
    assert slices[0].acceptance_criterion_ids == []
    assert slices[0].owned_acceptance_criterion_ids == []
    assert slices[0].supporting_acceptance_criterion_ids == ["AC-accounts-1", "AC-accounts-2"]
    assert slices[0].strict_acceptance_criteria is False
    assert slices[1].acceptance_criterion_ids == ["AC-accounts-3"]
    assert slices[1].owned_acceptance_criterion_ids == ["AC-accounts-3"]
    assert slices[1].supporting_acceptance_criterion_ids == []
    assert slices[1].strict_acceptance_criteria is True


def test_task_planning_matches_markdown_slice_acceptance_by_explicit_step_token():
    plan_markdown = """
## Implementation Steps

### STEP-21: Artifact RPC dispatcher

Implement serialized artifact writes.
""".strip()
    test_plan_markdown = """
## Acceptance Criteria

- **AC-accounts-21** — Dispatcher coverage explicitly tied to STEP-21.
  - linked_requirement: `STEP-21`
  - verification_method: `integration`
  - pass_condition: dispatcher behavior is covered
""".strip()

    parsed = TaskPlanningPhase._parse_test_plan(test_plan_markdown)
    slices = TaskPlanningPhase._derive_atomic_slices_from_markdown_plan(
        plan_markdown,
        parsed,
        fallback_ac_ids=sorted(task_planning_module._extract_ac_ids(test_plan_markdown)),
    )

    assert len(slices) == 1
    assert slices[0].acceptance_criterion_ids == ["AC-accounts-21"]
    assert slices[0].owned_acceptance_criterion_ids == ["AC-accounts-21"]
    assert slices[0].supporting_acceptance_criterion_ids == []
    assert slices[0].strict_acceptance_criteria is True


def test_task_planning_test_plan_excerpt_filters_markdown_slice_context():
    test_plan_markdown = """
## Acceptance Criteria

- **AC-accounts-1** — Boot succeeds.
  - linked_requirement: `REQ-1`
  - verification_method: `integration`
  - pass_condition: ready

- **AC-accounts-2** — Setup check emits findings.
  - linked_requirement: `REQ-2`
  - verification_method: `integration`
  - pass_condition: findings surface correctly

## Test Scenarios

### TS-1 — Happy path
- priority: `p0`
- linked_acceptance: `AC-accounts-1`
- expected_outcome: startup succeeds

### TS-2 — Findings path
- priority: `p1`
- linked_acceptance: `AC-accounts-2`
- expected_outcome: findings surface
""".strip()
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        acceptance_criterion_ids=["AC-accounts-1"],
    )

    excerpt = TaskPlanningPhase._test_plan_excerpt_for_slice(
        test_plan_markdown,
        slice_info,
    )

    assert "AC-accounts-1" in excerpt
    assert "AC-accounts-2" not in excerpt
    assert "TS-1" in excerpt
    assert "TS-2" not in excerpt


def test_task_planning_test_plan_excerpt_keeps_empty_structured_scope_empty():
    test_plan_markdown = """
## Acceptance Criteria

- **AC-accounts-1** — Boot succeeds.
  - linked_requirement: `REQ-1`
  - verification_method: `integration`
  - pass_condition: ready

## Test Scenarios

### TS-1 — Happy path
- priority: `p0`
- linked_acceptance: `AC-accounts-1`
- expected_outcome: startup succeeds
""".strip()
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )

    excerpt = TaskPlanningPhase._test_plan_excerpt_for_slice(
        test_plan_markdown,
        slice_info,
    )

    assert "AC-accounts-1" not in excerpt
    assert "TS-1" not in excerpt


def test_task_planning_test_plan_excerpt_includes_supporting_context_without_making_it_owned():
    test_plan_markdown = """
## Acceptance Criteria

- **AC-accounts-1** — Decision-backed dispatcher context.
  - linked_requirement: `D-GR-7`
  - verification_method: `integration`
  - pass_condition: dispatcher policy is documented

## Test Scenarios

### TS-1 — Dispatcher context
- priority: `p0`
- linked_acceptance: `AC-accounts-1`
- expected_outcome: dispatcher policy is visible
""".strip()
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        acceptance_criterion_ids=[],
        owned_acceptance_criterion_ids=[],
        supporting_acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=False,
    )

    excerpt = TaskPlanningPhase._test_plan_excerpt_for_slice(
        test_plan_markdown,
        slice_info,
    )
    target_only_excerpt = TaskPlanningPhase._test_plan_excerpt_for_slice(
        test_plan_markdown,
        slice_info,
        owned_only=True,
    )

    assert "AC-accounts-1" in excerpt
    assert "TS-1" in excerpt
    assert "AC-accounts-1" in target_only_excerpt
    assert "TS-1" in target_only_excerpt


@pytest.mark.asyncio
async def test_task_planning_target_only_context_package_uses_owned_only_excerpt_and_minimal_fallback(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-target-only-minimal", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="backend-foundation-setup", name="Backend Foundation Setup", description="BFS"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-A",
        name="Backend Foundation",
        subfeature_slugs=["backend-foundation-setup"],
        rationale="Backend foundation scope",
        depends_on=[],
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-5-4",
        title="Artifact RPC dispatcher",
        step_ids=["STEP-21"],
        owned_acceptance_criterion_ids=["AC-backend-foundation-setup-86"],
        acceptance_criterion_ids=["AC-backend-foundation-setup-86"],
        supporting_acceptance_criterion_ids=[
            "AC-backend-foundation-setup-83",
            "AC-backend-foundation-setup-84",
            "AC-backend-foundation-setup-85",
            "AC-backend-foundation-setup-87",
            "AC-backend-foundation-setup-88",
        ],
        strict_acceptance_criteria=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            big_block = "BROAD-CONTEXT\n" * 600
            self.store = {
                "plan:backend-foundation-setup": "## Implementation Steps\n\n### STEP-21: Artifact RPC dispatcher\n\nREQ-21\nDispatch artifact RPC requests.\n",
                "prd:backend-foundation-setup": "## Requirements\n\nREQ-21\nDispatcher RPC requests stay canonical.\n",
                "design:backend-foundation-setup": "## Verifiable States\n\nDispatcher request parsing remains canonical.\n",
                "system-design:backend-foundation-setup": "## Services\n\nArtifact service dispatcher.\n",
                "test-plan:backend-foundation-setup": """
## Acceptance Criteria

- **AC-backend-foundation-setup-86** — Canonical request shape is enforced.
  - linked_requirement: `REQ-186`
  - verification_method: `integration`
  - pass_condition: canonical request shape is enforced

- **AC-backend-foundation-setup-88** — Canonical response shape is enforced.
  - linked_requirement: `REQ-188`
  - verification_method: `integration`
  - pass_condition: canonical response shape is enforced

- **AC-backend-foundation-setup-83** — Supporting RPC context remains visible.
  - linked_requirement: `REQ-183`
  - verification_method: `integration`
  - pass_condition: supporting RPC context remains visible
""".strip(),
                "decisions:backend-foundation-setup": _decision_ledger_text(
                    DecisionRecord(
                        id="D-GR-11",
                        statement="Dispatcher uses canonical request/response contracts",
                        source_phase="subfeature",
                        subfeature_slug="backend-foundation-setup",
                    ),
                ),
                "prd:broad": f"## Requirements\n\n{big_block}",
                "design:broad": f"## Design System\n\n{big_block}",
                "plan:broad": f"## Implementation Steps\n\n{big_block}",
                "decisions:broad": _decision_ledger_text(
                    DecisionRecord(id="D-BROAD-1", statement="Broad decision", source_phase="broad"),
                ),
                "decisions": _decision_ledger_text(
                    DecisionRecord(id="D-GLOBAL-1", statement="Global decision", source_phase="plan-review"),
                ),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 15_000)

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        mode_label="target-only",
        direct_peer_only=True,
        slice_info=slice_info,
    )

    assert package is not None
    total_bytes, _breakdown = TaskPlanningPhase._estimate_context_package(package)
    assert total_bytes <= task_planning_module._SLICE_CONTEXT_SOFT_CAP_BYTES
    assert "broad-prd" not in package.item_paths
    assert "broad-design" not in package.item_paths
    assert "broad-plan" not in package.item_paths
    assert "subfeature-decisions" in package.item_paths

    test_plan_excerpt = Path(package.item_paths["test-plan"]).read_text(encoding="utf-8")
    assert "AC-backend-foundation-setup-86" in test_plan_excerpt
    assert "AC-backend-foundation-setup-83" not in test_plan_excerpt


@pytest.mark.asyncio
async def test_task_planning_target_only_context_package_generalizes_minimal_fallback_beyond_bfs(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-target-only-general", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        title="Accounts dispatcher",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-accounts-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            big_block = "BROAD-CONTEXT\n" * 600
            huge_target_decisions = _decision_ledger_text(
                *[
                    DecisionRecord(
                        id=f"D-ACCOUNTS-{idx}",
                        statement="Accounts decision " + ("y" * 200),
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    )
                    for idx in range(1, 25)
                ]
            )
            huge_decision_pack = _decision_ledger_text(
                *[
                    DecisionRecord(
                        id=f"D-DEC-{idx}",
                        statement="Decision context " + ("x" * 200),
                        source_phase="plan-review",
                    )
                    for idx in range(40)
                ]
            )
            self.store = {
                "plan:accounts": "## Implementation Steps\n\n### STEP-1: Accounts dispatcher\n\nREQ-accounts-1\nD-ACCOUNTS-1\nDispatch accounts requests.\n",
                "prd:accounts": "## Requirements\n\nREQ-accounts-1\nAccounts stay canonical.\n",
                "design:accounts": "## Verifiable States\n\nCMP-accounts#ready\n",
                "system-design:accounts": "## Services\n\nAccounts service.\n\n## Architecture Decisions\n\nD-ACCOUNTS-1\n",
                "test-plan:accounts": """
## Acceptance Criteria

- **AC-accounts-1** — Accounts request shape is enforced.
  - linked_requirement: `REQ-accounts-1`
  - verification_method: `integration`
  - pass_condition: accounts request shape is enforced
""".strip(),
                "decisions:accounts": huge_target_decisions,
                "prd:broad": f"## Requirements\n\n{big_block}",
                "design:broad": f"## Design System\n\n{big_block}",
                "plan:broad": f"## Implementation Steps\n\n{big_block}",
                "decisions:broad": huge_decision_pack,
                "decisions": huge_decision_pack,
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 15_000)
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_scoped_decision_pack",
        lambda self, *args, **kwargs: asyncio.sleep(0, result=("DECISION-PACK\n" * 3000)),
    )

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        mode_label="target-only",
        direct_peer_only=True,
        slice_info=slice_info,
    )

    assert package is not None
    total_bytes, _breakdown = TaskPlanningPhase._estimate_context_package(package)
    assert total_bytes <= task_planning_module._SLICE_CONTEXT_SOFT_CAP_BYTES
    assert "broad-prd" not in package.item_paths
    assert "broad-design" not in package.item_paths
    assert "broad-plan" not in package.item_paths
    assert "decision-pack" not in package.item_paths
    assert "subfeature-decisions" in package.item_paths
    target_decision_context = Path(package.item_paths["subfeature-decisions"]).read_text(encoding="utf-8")
    assert "Target Decision Context" in target_decision_context
    assert "Referenced Decision Records" in target_decision_context
    assert "D-ACCOUNTS-1" in target_decision_context


@pytest.mark.asyncio
async def test_task_planning_target_only_keeps_required_decision_context_even_when_over_budget(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-target-only-required-decisions", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        title="Accounts dispatcher",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-accounts-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "decisions"],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan:accounts": "## Implementation Steps\n\n### STEP-1: Accounts dispatcher\n\nREQ-accounts-1\nD-ACCOUNTS-1\nDispatch accounts requests.\n",
                "prd:accounts": "## Requirements\n\nREQ-accounts-1\nAccounts stay canonical.\n",
                "design:accounts": "## Verifiable States\n\nCMP-accounts#ready\n",
                "system-design:accounts": "## Services\n\nAccounts service.\n\n## Architecture Decisions\n\nD-ACCOUNTS-1\n",
                "test-plan:accounts": """
## Acceptance Criteria

- **AC-accounts-1** — Accounts request shape is enforced.
  - linked_requirement: `REQ-accounts-1`
  - verification_method: `integration`
  - pass_condition: accounts request shape is enforced
""".strip(),
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-ACCOUNTS-1",
                        statement="Accounts decision " + ("y" * 400),
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    )
                ),
                "decisions:broad": _decision_ledger_text(
                    DecisionRecord(id="D-BROAD-1", statement="Broad decision", source_phase="broad"),
                ),
                "decisions": _decision_ledger_text(
                    DecisionRecord(id="D-GLOBAL-1", statement="Global decision", source_phase="plan-review"),
                ),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 15_000)
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_scoped_decision_pack",
        lambda self, *args, **kwargs: asyncio.sleep(0, result=("DECISION-PACK\n" * 3000)),
    )

    def _estimate_context_package(cls, package):
        if package is None:
            return 0, {}
        if "decision-pack" in package.item_paths:
            return 30_000, {"decision": 30_000}
        if "subfeature-decisions" in package.item_paths:
            return 20_000, {"decision": 20_000}
        return 10_000, {"target": 10_000}

    monkeypatch.setattr(
        TaskPlanningPhase,
        "_estimate_context_package",
        classmethod(_estimate_context_package),
    )

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        mode_label="target-only",
        direct_peer_only=True,
        slice_info=slice_info,
    )

    assert package is not None
    assert "decision-pack" not in package.item_paths
    assert "subfeature-decisions" in package.item_paths


@pytest.mark.asyncio
async def test_task_planning_compact_target_decision_context_includes_contract_decision_ids():
    feature = SimpleNamespace(id="feat-task-plan-compact-target-decisions", metadata={})
    contract = task_planning_module.SubfeaturePlanningContract(
        slug="accounts",
        step_contracts=[
            task_planning_module.StepPlanningContract(
                step_id="STEP-1",
                decision_ids=["D-ACCOUNTS-7"],
            )
        ],
        contract_digest="digest",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            values = {
                "dag-contract:accounts": contract.model_dump_json(indent=2),
                "decisions-summary:accounts": "",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-ACCOUNTS-8",
                        statement="Unrelated target decision",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
                "decisions:broad": _decision_ledger_text(
                    DecisionRecord(id="D-BROAD-1", statement="Broad decision", source_phase="broad"),
                ),
                "decisions:global": _decision_ledger_text(
                    DecisionRecord(id="D-ACCOUNTS-7", statement="Contract-required decision", source_phase="plan-review"),
                ),
                "decisions": _decision_ledger_text(
                    DecisionRecord(id="D-BROAD-1", statement="Broad decision", source_phase="broad"),
                    DecisionRecord(id="D-ACCOUNTS-7", statement="Contract-required decision", source_phase="plan-review"),
                    DecisionRecord(
                        id="D-ACCOUNTS-8",
                        statement="Unrelated target decision",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
            }
            return values.get(key, "")

    result = await TaskPlanningPhase()._build_target_decision_context_item(
        SimpleNamespace(artifacts=_Artifacts(), services={}),
        feature,
        SimpleNamespace(id="WS-1"),
        SimpleNamespace(slug="accounts"),
        task_planning_module.TaskPlanningSlice(
            slice_id="slice-1",
            step_ids=["STEP-1"],
            required_reference_sources=["decisions"],
        ),
        mode_stem="target-only",
        target_bundle={"plan": "### STEP-1\nNo explicit decision id in excerpt\n"},
        feature_bundle={"metadata": "", "neighborhood": "", "edges": ""},
        compact=True,
    )

    assert result.complete is True
    assert result.item is not None
    assert "D-ACCOUNTS-7" in result.item.content
    assert "D-ACCOUNTS-8" not in result.item.content


@pytest.mark.asyncio
async def test_task_planning_compact_target_decision_context_falls_back_to_full_ledger_when_required():
    feature = SimpleNamespace(id="feat-task-plan-compact-target-decisions-required", metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            values = {
                "decisions-summary:accounts": "",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-ACCOUNTS-8",
                        statement="Only target decision available",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
                "decisions:broad": "",
                "decisions:global": "",
                "decisions": "",
            }
            return values.get(key, "")

    result = await TaskPlanningPhase()._build_target_decision_context_item(
        SimpleNamespace(artifacts=_Artifacts(), services={}),
        feature,
        SimpleNamespace(id="WS-1"),
        SimpleNamespace(slug="accounts"),
        task_planning_module.TaskPlanningSlice(
            slice_id="slice-1",
            step_ids=["STEP-1"],
            required_reference_sources=["decisions"],
        ),
        mode_stem="target-only",
        target_bundle={"plan": "### STEP-1\nNo referenced decision ids\n"},
        feature_bundle={"metadata": "", "neighborhood": "", "edges": ""},
        compact=True,
        required=True,
    )

    assert result.complete is True
    assert result.item is not None
    assert result.item.artifact_key == "decisions:accounts"


@pytest.mark.asyncio
async def test_task_planning_compact_target_decision_context_is_incomplete_when_required_ids_are_unresolved():
    feature = SimpleNamespace(id="feat-task-plan-compact-target-decisions-incomplete", metadata={})
    contract = task_planning_module.SubfeaturePlanningContract(
        slug="accounts",
        step_contracts=[
            task_planning_module.StepPlanningContract(
                step_id="STEP-1",
                decision_ids=["D-MISSING-1"],
            )
        ],
        contract_digest="digest",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            values = {
                "dag-contract:accounts": contract.model_dump_json(indent=2),
                "decisions-summary:accounts": "Generic accounts summary",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-ACCOUNTS-8",
                        statement="Unrelated target decision",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
                "decisions:broad": "",
                "decisions:global": "",
                "decisions": "",
            }
            return values.get(key, "")

    result = await TaskPlanningPhase()._build_target_decision_context_item(
        SimpleNamespace(artifacts=_Artifacts(), services={}),
        feature,
        SimpleNamespace(id="WS-1"),
        SimpleNamespace(slug="accounts"),
        task_planning_module.TaskPlanningSlice(
            slice_id="slice-1",
            step_ids=["STEP-1"],
            required_reference_sources=["decisions"],
        ),
        mode_stem="target-only",
        target_bundle={"plan": "### STEP-1\nNo explicit decision id in excerpt\n"},
        feature_bundle={"metadata": "", "neighborhood": "", "edges": ""},
        compact=True,
        required=True,
    )

    assert result.complete is False
    assert result.missing_ids == ["D-MISSING-1"]
    assert result.item is not None
    assert "Generic accounts summary" in result.item.content


@pytest.mark.asyncio
async def test_task_planning_target_only_keeps_decision_pack_when_compact_decision_context_is_incomplete(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-target-only-incomplete-decisions", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        title="Accounts dispatcher",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-accounts-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "decisions"],
    )
    contract = task_planning_module.SubfeaturePlanningContract(
        slug="accounts",
        step_contracts=[
            task_planning_module.StepPlanningContract(
                step_id="STEP-1",
                decision_ids=["D-MISSING-1"],
            )
        ],
        contract_digest="digest",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-contract:accounts": contract.model_dump_json(indent=2),
                "plan:accounts": "## Implementation Steps\n\n### STEP-1: Accounts dispatcher\n\nREQ-accounts-1\nDispatch accounts requests.\n",
                "prd:accounts": "## Requirements\n\nREQ-accounts-1\nAccounts stay canonical.\n",
                "design:accounts": "## Verifiable States\n\nCMP-accounts#ready\n",
                "system-design:accounts": "## Services\n\nAccounts service.\n",
                "test-plan:accounts": """
## Acceptance Criteria

- **AC-accounts-1** — Accounts request shape is enforced.
  - linked_requirement: `REQ-accounts-1`
  - verification_method: `integration`
  - pass_condition: accounts request shape is enforced
""".strip(),
                "decisions-summary:accounts": "Generic accounts summary",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-ACCOUNTS-8",
                        statement="Unrelated target decision",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
                "decisions:broad": "",
                "decisions": "",
                "decisions:global": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 15_000)
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_scoped_decision_pack",
        lambda self, *args, **kwargs: asyncio.sleep(0, result="## Scoped Decision Pack\n\n`D-MISSING-1`\n"),
    )

    def _estimate_context_package(cls, package):
        if package is None:
            return 0, {}
        if "decision-pack" in package.item_paths:
            return 16_000, {"decision": 16_000}
        if "subfeature-decisions" in package.item_paths:
            return 14_000, {"decision": 14_000}
        return 10_000, {"target": 10_000}

    monkeypatch.setattr(
        TaskPlanningPhase,
        "_estimate_context_package",
        classmethod(_estimate_context_package),
    )

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        mode_label="target-only",
        direct_peer_only=True,
        slice_info=slice_info,
    )

    assert package is not None
    assert "decision-pack" in package.item_paths


@pytest.mark.asyncio
async def test_task_planning_target_only_drops_decision_pack_when_compact_decision_context_is_complete(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-target-only-complete-decisions", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        title="Accounts dispatcher",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-accounts-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "decisions"],
    )
    contract = task_planning_module.SubfeaturePlanningContract(
        slug="accounts",
        step_contracts=[
            task_planning_module.StepPlanningContract(
                step_id="STEP-1",
                decision_ids=["D-GLOBAL-1"],
            )
        ],
        contract_digest="digest",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-contract:accounts": contract.model_dump_json(indent=2),
                "plan:accounts": "## Implementation Steps\n\n### STEP-1: Accounts dispatcher\n\nREQ-accounts-1\nDispatch accounts requests.\n",
                "prd:accounts": "## Requirements\n\nREQ-accounts-1\nAccounts stay canonical.\n",
                "design:accounts": "## Verifiable States\n\nCMP-accounts#ready\n",
                "system-design:accounts": "## Services\n\nAccounts service.\n",
                "test-plan:accounts": """
## Acceptance Criteria

- **AC-accounts-1** — Accounts request shape is enforced.
  - linked_requirement: `REQ-accounts-1`
  - verification_method: `integration`
  - pass_condition: accounts request shape is enforced
""".strip(),
                "decisions-summary:accounts": "Generic accounts summary",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-ACCOUNTS-8",
                        statement="Unrelated target decision",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
                "decisions:broad": "",
                "decisions": "",
                "decisions:global": _decision_ledger_text(
                    DecisionRecord(
                        id="D-GLOBAL-1",
                        statement="Required global decision",
                        source_phase="plan-review",
                    ),
                ),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 15_000)
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_scoped_decision_pack",
        lambda self, *args, **kwargs: asyncio.sleep(0, result="## Scoped Decision Pack\n\n`D-GLOBAL-1`\n"),
    )

    def _estimate_context_package(cls, package):
        if package is None:
            return 0, {}
        if "decision-pack" in package.item_paths:
            return 16_000, {"decision": 16_000}
        if "subfeature-decisions" in package.item_paths:
            return 14_000, {"decision": 14_000}
        return 10_000, {"target": 10_000}

    monkeypatch.setattr(
        TaskPlanningPhase,
        "_estimate_context_package",
        classmethod(_estimate_context_package),
    )

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        mode_label="target-only",
        direct_peer_only=True,
        slice_info=slice_info,
    )

    assert package is not None
    assert "decision-pack" not in package.item_paths
    assert "subfeature-decisions" in package.item_paths


def test_task_planning_target_slice_bundle_prefers_exact_step_sections_over_token_matches():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-4-4",
        title="Test suite",
        step_ids=["STEP-16"],
        requirement_ids=["REQ-21", "REQ-58"],
        acceptance_criterion_ids=["AC-backend-foundation-setup-22a"],
        strict_acceptance_criteria=True,
    )

    bundle = TaskPlanningPhase._target_slice_bundle(
        "backend-foundation-setup",
        slice_info,
        {
            "plan": """
## Implementation Steps

### STEP-2: Logging foundation

REQ-21
Logging setup uses shared requirement tokens.

### STEP-7: Dependency probes

REQ-21
Probe setup also uses shared requirement tokens.

### STEP-16: Test suite

REQ-21
REQ-58
Write the backend test harness and integration suite.
""".strip(),
            "prd": "",
            "design": "",
            "system-design": "",
            "test-plan": "",
        },
    )

    assert "### STEP-16" in bundle["plan"]
    assert "Write the backend test harness and integration suite." in bundle["plan"]
    assert "### STEP-2" not in bundle["plan"]
    assert "### STEP-7" not in bundle["plan"]


@pytest.mark.asyncio
async def test_task_planning_prompt_requires_requirement_ids_for_req_owning_slice(monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-req-prompt", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-1",
                slug="backend-foundation-setup",
                name="Backend Foundation Setup",
                description="BFS",
            ),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-A",
        name="Backend Foundation",
        subfeature_slugs=["backend-foundation-setup"],
        rationale="Backend foundation scope",
        depends_on=[],
    )
    subfeature = decomposition.subfeatures[0]
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-2-3",
        title="Dependency probes",
        step_ids=["STEP-7"],
        requirement_ids=["REQ-18", "REQ-19", "REQ-20", "REQ-21", "REQ-50"],
        acceptance_criterion_ids=["AC-backend-foundation-setup-19"],
        owned_acceptance_criterion_ids=["AC-backend-foundation-setup-19"],
        strict_acceptance_criteria=True,
    )

    async def _fake_package(self, *args, **kwargs):
        del self, args, kwargs
        return None

    async def _fake_context(self, *args, **kwargs):
        del self, args, kwargs
        return ""

    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_subfeature_task_context_package",
        _fake_package,
    )
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_subfeature_task_context",
        _fake_context,
    )

    prompt, package = await TaskPlanningPhase()._build_subfeature_task_prompt(
        SimpleNamespace(),
        feature,
        decomposition,
        workstream,
        subfeature,
        {},
        direct_peer_only=True,
        mode_label="target-only",
        slice_info=slice_info,
    )

    assert package is None
    assert "Requirement IDs in scope: REQ-18, REQ-19, REQ-20, REQ-21, REQ-50." in prompt
    assert "Every emitted task MUST include at least one requirement_id from this in-scope list." in prompt
    assert "Tasks missing requirement_ids will fail slice validation." in prompt
    assert "Only omit requirement_ids when Requirement IDs in scope is empty for this slice." in prompt


@pytest.mark.asyncio
async def test_task_planning_prompt_includes_path_discipline_and_repo_catalog(monkeypatch):
    """AC4: the subfeature planner prompt surfaces the Path Discipline guidance
    and the real workspace directory map (repo catalog)."""
    feature = SimpleNamespace(id="feat-task-plan-path-discipline", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-1",
                slug="studio-workflow-tab",
                name="Studio Workflow Tab",
                description="SWT",
            ),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-A",
        name="Studio Workflow",
        subfeature_slugs=["studio-workflow-tab"],
        rationale="Studio workflow scope",
        depends_on=[],
    )
    subfeature = decomposition.subfeatures[0]
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-14",
        title="Workflow tab view",
        step_ids=["STEP-14"],
        requirement_ids=["REQ-1"],
        acceptance_criterion_ids=["AC-studio-workflow-tab-1"],
        owned_acceptance_criterion_ids=["AC-studio-workflow-tab-1"],
        strict_acceptance_criteria=True,
    )

    directory_map = (
        "# Directory Map\n\n## Repos\n\n"
        "| Name | Path | Description | GitHub URL | Language |\n"
        "|------|------|-------------|------------|----------|\n"
        "| iriai-studio | iriai-studio | VS Code fork | https://example/x | TypeScript |\n"
    )
    project_ctx = ProjectContext(
        feature_name="Studio Workflow Tab",
        workspace_path="/tmp/ws",
        directory_map=directory_map,
    )

    async def _get(key, *, feature=None):
        del feature
        if key == "project":
            return project_ctx.model_dump_json()
        return None

    runner = SimpleNamespace(artifacts=SimpleNamespace(get=_get))

    async def _fake_package(self, *args, **kwargs):
        del self, args, kwargs
        return None

    async def _fake_context(self, *args, **kwargs):
        del self, args, kwargs
        return ""

    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_subfeature_task_context_package",
        _fake_package,
    )
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_build_subfeature_task_context",
        _fake_context,
    )

    prompt, package = await TaskPlanningPhase()._build_subfeature_task_prompt(
        runner,
        feature,
        decomposition,
        workstream,
        subfeature,
        {},
        direct_peer_only=True,
        mode_label="target-only",
        slice_info=slice_info,
    )

    assert package is None
    # (a) Path Discipline guidance is present
    assert "Path Discipline" in prompt
    assert "Repo Catalog" in prompt
    # (b) the real directory-map / repo-layout content is rendered into the prompt
    assert "iriai-studio | iriai-studio" in prompt
    assert "VS Code fork" in prompt


@pytest.mark.asyncio
async def test_task_planning_allows_empty_task_requirement_ids_for_slices_without_direct_requirements():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=[],
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=[],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is not None
    assert validation_error is None
    assert retryable is False


@pytest.mark.asyncio
async def test_task_planning_requires_task_requirement_ids_when_slice_has_direct_requirements():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-1"],
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=[],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is None
    assert validation_error == "T-accounts-1 is missing requirement_ids"
    assert retryable is True


@pytest.mark.asyncio
async def test_task_planning_canonicalizes_retired_backend_paths_before_fragment_persistence(monkeypatch):
    # Legacy static backend-prefix shim is the flag-OFF fallback; exercise it
    # explicitly (the default-on path is the agentic resolver, covered elsewhere).
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=[],
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )
    task = _valid_task(
        task_id="T-backend-1",
        slug="accounts",
        step_ids=["STEP-1"],
        requirement_ids=[],
    ).model_copy(update={
        "repo_path": "iriai-studio-backend",
        "file_scope": [
            TaskFileScope(
                path="iriai-studio-backend/src/iriai_studio_backend/security/hooks_disable.py",
                action="create",
            ),
            TaskFileScope(
                path="iriai-studio-backend/src-py/iriai_studio_backend/paths.py",
                action="modify",
            ),
        ],
        "files": ["src/iriai_studio_backend/security/__init__.py"],
    })
    dag = ImplementationDAG(
        tasks=[task],
        execution_order=[["T-backend-1"]],
        requirement_coverage={},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is not None
    assert validation_error is None
    assert retryable is False
    assert [scope.path for scope in validated.tasks[0].file_scope] == [
        "iriai-studio-backend/iriai_studio_backend/security/hooks_disable.py",
        "iriai-studio-backend/iriai_studio_backend/paths.py",
    ]
    assert validated.tasks[0].files == [
        "iriai-studio-backend/iriai_studio_backend/security/__init__.py",
    ]
    assert dag.tasks[0].file_scope[0].path == (
        "iriai-studio-backend/src/iriai_studio_backend/security/hooks_disable.py"
    )


@pytest.mark.asyncio
async def test_task_planning_root_dag_persistence_canonicalizes_subfeature_dags(monkeypatch):
    # Legacy static backend-prefix shim is the flag-OFF fallback.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    stale_dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="TASK-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
            ).model_copy(update={
                "repo_path": "iriai-studio-backend",
                "file_scope": [
                    TaskFileScope(
                        path="iriai-studio-backend/src/iriai_studio_backend/paths.py",
                        action="modify",
                    ),
                ],
            }),
        ],
        execution_order=[["TASK-1"]],
        requirement_coverage={"REQ-1": ["TASK-1"]},
        complete=True,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "dag:accounts":
                return stale_dag.model_dump_json()
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts())
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-1",
                slug="accounts",
                name="Accounts",
                description="Accounts",
            ),
        ],
        complete=True,
    )

    root_dag = await TaskPlanningPhase._build_approved_root_implementation_dag(
        runner,
        SimpleNamespace(id="feat", metadata={}),
        decomposition,
    )

    assert root_dag.tasks[0].file_scope[0].path == (
        "iriai-studio-backend/iriai_studio_backend/paths.py"
    )
    assert stale_dag.tasks[0].file_scope[0].path == (
        "iriai-studio-backend/src/iriai_studio_backend/paths.py"
    )


@pytest.mark.asyncio
async def test_task_planning_reconciles_partial_slice_requirement_coverage():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-1", "REQ-2"],
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-1": ["T-accounts-1"]},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is not None
    assert validation_error is None
    assert retryable is False
    task = validated.tasks[0]
    assert "REQ-2" in task.requirement_ids
    assert validated.requirement_coverage["REQ-2"] == [task.id]


@pytest.mark.asyncio
async def test_task_planning_requirement_reconciliation_does_not_mask_out_of_scope_refs():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-1", "REQ-2"],
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1", "REQ-999"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-1": ["T-accounts-1"], "REQ-999": ["T-accounts-1"]},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is None
    assert "outside slice" in (validation_error or "")
    assert "REQ-999" in (validation_error or "")
    assert retryable is True


@pytest.mark.asyncio
async def test_task_planning_requires_journey_ids_and_reference_source_completeness():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-1"],
        journey_ids=["J-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "test-plan"],
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
                journey_ids=[],
                verification_gates=["AC-accounts-1"],
            ).model_copy(
                update={
                    "reference_material": [
                        TaskReference(source="Plan STEP-1", content="accounts reference material"),
                    ]
                }
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is None
    assert retryable is True
    assert "T-accounts-1 is missing journey_ids" in validation_error
    assert "missing reference_material from required source family test-plan" in validation_error


@pytest.mark.asyncio
async def test_task_planning_unknown_reference_sources_do_not_satisfy_plan_source_completeness():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan"],
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                verification_gates=["AC-accounts-1"],
            ).model_copy(
                update={
                    "reference_material": [
                        TaskReference(source="workspace scratchpad", content="notes"),
                    ]
                }
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is None
    assert retryable is True
    assert "missing reference_material from required source family plan" in validation_error


@pytest.mark.asyncio
async def test_task_planning_hydrates_missing_required_reference_families_from_context_package(tmp_path):
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "design", "system-design"],
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                verification_gates=["AC-accounts-1"],
            ).model_copy(
                update={
                    "reference_material": [
                        TaskReference(source="Plan STEP-1", content="plan excerpt"),
                    ]
                }
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={},
        complete=True,
    )
    long_design_text = "## Slice Design Excerpts\n\n" + ("CMP-1#ready\n" * 400)
    design_path = tmp_path / "slice-design.md"
    design_path.write_text(long_design_text, encoding="utf-8")
    system_design_path = tmp_path / "slice-system-design.md"
    system_design_path.write_text("## Slice System Design Excerpts\n\nService: shell\n", encoding="utf-8")
    context_package = ContextPackage(
        index_path=str(tmp_path / "index.md"),
        manifest_path=str(tmp_path / "manifest.md"),
        item_paths={
            "design": str(design_path),
            "system-design": str(system_design_path),
        },
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
        context_package=context_package,
    )

    assert validation_error is None
    assert retryable is False
    assert validated is not None
    families = {
        TaskPlanningPhase._reference_source_family(reference.source)
        for task in validated.tasks
        for reference in task.reference_material
    }
    assert {"plan", "design", "system-design"}.issubset(families)
    design_reference = next(
        reference
        for task in validated.tasks
        for reference in task.reference_material
        if TaskPlanningPhase._reference_source_family(reference.source) == "design"
    )
    assert design_reference.content == long_design_text.strip()


@pytest.mark.asyncio
async def test_task_planning_reconciles_missing_owned_acceptance_gates_from_test_plan():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-accounts"],
        owned_acceptance_criterion_ids=["AC-accounts-1", "AC-accounts-2"],
        acceptance_criterion_ids=["AC-accounts-1", "AC-accounts-2"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "prd", "test-plan"],
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-accounts"],
                verification_gates=["AC-accounts-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )
    test_plan = OutputTestPlan(
        acceptance_criteria=[
            OutputTestAcceptanceCriterion(
                id="AC-accounts-1",
                description="first gate",
                linked_requirement="REQ-accounts",
                pass_condition="first passes",
            ),
            OutputTestAcceptanceCriterion(
                id="AC-accounts-2",
                description="second gate",
                linked_requirement="REQ-accounts",
                pass_condition="second passes",
            ),
        ],
        complete=True,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "test-plan:accounts":
                return test_plan.model_dump_json()
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        runner,
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validation_error is None
    assert retryable is False
    assert validated is not None
    task = validated.tasks[0]
    assert task.verification_gates == ["AC-accounts-1", "AC-accounts-2"]
    assert any("AC-accounts-2" in item.description for item in task.acceptance_criteria)
    assert any(reference.source == "Test Plan AC-accounts-2" for reference in task.reference_material)


@pytest.mark.asyncio
async def test_task_planning_reconciliation_does_not_mask_unknown_gates():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1"],
        acceptance_criterion_ids=["AC-accounts-1"],
        strict_acceptance_criteria=True,
    )
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                verification_gates=["AC-accounts-999"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={},
        complete=True,
    )

    validated, validation_error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        SimpleNamespace(),
        SimpleNamespace(id="feat", metadata={}),
        "accounts",
        slice_info,
        dag,
    )

    assert validated is None
    assert retryable is True
    assert "outside slice scope" in validation_error
    assert "AC-accounts-999" in validation_error


def test_task_planning_test_plan_excerpt_uses_exact_owned_structured_criteria():
    test_plan = OutputTestPlan(
        acceptance_criteria=[
            OutputTestAcceptanceCriterion(id="AC-accounts-1", description="owned one", linked_requirement="REQ-1"),
            OutputTestAcceptanceCriterion(id="AC-accounts-2", description="owned two", linked_requirement="REQ-2"),
            OutputTestAcceptanceCriterion(id="AC-accounts-3", description="unrelated", linked_requirement="REQ-3"),
            OutputTestAcceptanceCriterion(id="AC-accounts-4", description="global context", linked_requirement="REQ-4"),
        ],
        complete=True,
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        owned_acceptance_criterion_ids=["AC-accounts-1", "AC-accounts-2"],
        supporting_acceptance_criterion_ids=["AC-accounts-3"],
        global_obligation_ac_ids=["AC-accounts-4"],
        strict_acceptance_criteria=True,
    )

    excerpt = TaskPlanningPhase._test_plan_excerpt_for_slice(
        "",
        slice_info,
        owned_only=True,
        test_plan_model=test_plan,
    )

    assert "Owned Acceptance Criteria (Mandatory)" in excerpt
    assert "AC-accounts-1" in excerpt
    assert "AC-accounts-2" in excerpt
    assert "Global Obligation Acceptance Criteria (Optional Context)" in excerpt
    assert "AC-accounts-4" in excerpt
    assert "AC-accounts-3" not in excerpt


def test_task_planning_build_subfeature_dag_recomputes_requirement_coverage():
    dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                requirement_ids=["REQ-accounts-2"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts-stale": ["T-accounts-1"]},
        complete=True,
    )

    rebuilt = TaskPlanningPhase._build_subfeature_dag(dag, dag.tasks)

    assert rebuilt.requirement_coverage == {"REQ-accounts-2": ["T-accounts-1"]}


@pytest.mark.asyncio
async def test_task_planning_merge_slice_fragments_recomputes_requirement_coverage():
    feature = SimpleNamespace(id="feat-merge-req-coverage", metadata={})
    manifest = task_planning_module.TaskPlanningSliceManifest(
        slug="accounts",
        slices=[
            task_planning_module.TaskPlanningSlice(slice_id="slice-1", step_ids=["STEP-1"]),
            task_planning_module.TaskPlanningSlice(slice_id="slice-2", step_ids=["STEP-2"]),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(slice_id="slice-1", fragment_key="dag-fragment:accounts:slice-1"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-2", fragment_key="dag-fragment:accounts:slice-2"),
        ],
    )

    fragment_one = ImplementationDAG(
        tasks=[_valid_task(task_id="T-accounts-1", slug="accounts", requirement_ids=["REQ-accounts-1"])],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-stale": ["T-accounts-1"]},
        complete=True,
    )
    fragment_two = ImplementationDAG(
        tasks=[_valid_task(task_id="T-accounts-2", slug="accounts", requirement_ids=["REQ-accounts-2"])],
        execution_order=[["T-accounts-2"]],
        requirement_coverage={},
        complete=True,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "dag-fragment:accounts:slice-1": fragment_one.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-2": fragment_two.model_dump_json(indent=2),
            }.get(key, "")

    merged = await TaskPlanningPhase._merge_slice_fragments(
        SimpleNamespace(artifacts=_Artifacts()),
        feature,
        "accounts",
        manifest,
    )

    assert merged.requirement_coverage == {
        "REQ-accounts-1": ["T-accounts-1"],
        "REQ-accounts-2": ["T-accounts-2"],
    }


def test_task_planning_backend_like_late_steps_split_owned_and_supporting_acceptance_sets():
    plan_markdown = """
## Implementation Steps

### STEP-20: Hostile repo defenses

D-GR-9
Protect repo checkout and renderer boundaries.

### STEP-21: Artifact RPC dispatcher

D-GR-1
D-GR-11
D-GR-7
Implement serialized artifact writes.

### STEP-22: Perf regression gate

D-GR-12
NFR-1
NFR-2
Benchmark critical backend paths.

### STEP-25: Quarantine sweeper

D-GR-30
REQ-67
Sweep quarantined artifacts.

### STEP-26: Secret scanner

D-GR-31
REQ-67
Block secret-bearing commits.

### STEP-28: Idle timeout driver

D-GR-33
D-GR-35
Enforce server-side user-turn timeout.
""".strip()
    test_plan_markdown = """
## Acceptance Criteria

- **AC-backend-20** — Repo hardening matches D-GR-9.
  - linked_requirement: `D-GR-9`
  - verification_method: `integration`
  - pass_condition: repo hardening enforced

- **AC-backend-21** — RPC dispatcher matches artifact-write decisioning.
  - linked_requirement: `D-GR-1, D-GR-11, D-GR-7`
  - verification_method: `integration`
  - pass_condition: writes are serialized and signed

- **AC-backend-22** — Perf regression gate matches NFR budgets.
  - linked_requirement: `D-GR-12, NFR-1, NFR-2`
  - verification_method: `integration`
  - pass_condition: perf budgets enforced

- **AC-backend-25** — Quarantine sweep respects redaction rules.
  - linked_requirement: `D-GR-30, REQ-67`
  - verification_method: `integration`
  - pass_condition: quarantine is swept safely

- **AC-backend-26** — Secret scanner blocks commits.
  - linked_requirement: `D-GR-31, REQ-67`
  - verification_method: `integration`
  - pass_condition: scanner blocks and audits

- **AC-backend-28** — Idle timeout emits structured close reasons.
  - linked_requirement: `D-GR-33, D-GR-35`
  - verification_method: `integration`
  - pass_condition: timeout closes idle windows
""".strip()

    parsed = TaskPlanningPhase._parse_test_plan(test_plan_markdown)
    slices = TaskPlanningPhase._derive_atomic_slices_from_markdown_plan(
        plan_markdown,
        parsed,
        fallback_ac_ids=sorted(task_planning_module._extract_ac_ids(test_plan_markdown)),
    )

    assert [slice_info.step_ids for slice_info in slices] == [
        ["STEP-20"],
        ["STEP-21"],
        ["STEP-22"],
        ["STEP-25"],
        ["STEP-26"],
        ["STEP-28"],
    ]
    assert slices[0].acceptance_criterion_ids == []
    assert slices[0].supporting_acceptance_criterion_ids == ["AC-backend-20"]
    assert slices[0].strict_acceptance_criteria is False
    assert slices[1].acceptance_criterion_ids == []
    assert slices[1].supporting_acceptance_criterion_ids == ["AC-backend-21"]
    assert slices[1].strict_acceptance_criteria is False
    assert slices[2].acceptance_criterion_ids == []
    assert slices[2].supporting_acceptance_criterion_ids == ["AC-backend-22"]
    assert slices[2].strict_acceptance_criteria is False
    assert "AC-backend-25" in slices[3].acceptance_criterion_ids
    assert slices[3].acceptance_criterion_ids != sorted(task_planning_module._extract_ac_ids(test_plan_markdown))
    assert "AC-backend-25" in slices[3].owned_acceptance_criterion_ids
    assert slices[3].supporting_acceptance_criterion_ids == []
    assert slices[3].strict_acceptance_criteria is True
    assert "AC-backend-26" in slices[4].acceptance_criterion_ids
    assert slices[4].acceptance_criterion_ids != sorted(task_planning_module._extract_ac_ids(test_plan_markdown))
    assert "AC-backend-26" in slices[4].owned_acceptance_criterion_ids
    assert slices[4].supporting_acceptance_criterion_ids == []
    assert slices[4].strict_acceptance_criteria is True
    assert slices[5].acceptance_criterion_ids == []
    assert slices[5].supporting_acceptance_criterion_ids == ["AC-backend-28"]
    assert slices[5].strict_acceptance_criteria is False


@pytest.mark.asyncio
async def test_task_planning_backend_effective_coverage_scope_waives_superseded_ac_ids_only():
    feature = SimpleNamespace(id="feat-bfs-effective-coverage", metadata={})
    test_plan_text = """
## Acceptance Criteria

- **AC-backend-foundation-setup-25** — Verdict logic remains covered.
  - linked_requirement: `REQ-25`
  - verification_method: `integration`
  - pass_condition: verdict logic is covered

- **AC-backend-foundation-setup-26** — Deprecated recheck_setup bridge command triggers a sweep.
  - linked_requirement: `REQ-26`
  - verification_method: `integration`
  - pass_condition: old bridge token is accepted

- **AC-backend-foundation-setup-35** — Deprecated setup_check_probing order is preserved.
  - linked_requirement: `REQ-35`
  - verification_method: `integration`
  - pass_condition: probing order matches the retired contract

- **AC-backend-foundation-setup-73** — Deprecated single-file decision.key is generated.
  - linked_requirement: `REQ-73`
  - verification_method: `integration`
  - pass_condition: decision.key exists

- **AC-backend-foundation-setup-80** — Validation module rejects malformed payloads.
  - linked_requirement: `REQ-80`
  - verification_method: `integration`
  - pass_condition: malformed payloads are rejected
""".strip()

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "test-plan:backend-foundation-setup":
                return test_plan_text
            return ""

    coverage = await task_planning_module._validate_verification_gates_coverage(
        SimpleNamespace(artifacts=_Artifacts()),
        feature,
        "backend-foundation-setup",
        [
            _valid_task(
                task_id="T-bfs-25",
                slug="backend-foundation-setup",
                verification_gates=["AC-backend-foundation-setup-25"],
            )
        ],
    )

    assert coverage.unknown_gate_refs == []
    assert coverage.uncovered_ac_ids == ["AC-backend-foundation-setup-80"]


@pytest.mark.asyncio
async def test_validate_verification_gates_coverage_prefers_migrated_test_plan_sidecar_over_stale_contract():
    feature = SimpleNamespace(id="feat-vscode-coverage-sidecar", metadata={})
    sidecar = build_structured_artifact(
        "test-plan:vscode-fork-shell",
        json.dumps(
            {
                "acceptance_criteria": [
                    {"id": "AC-Q1", "description": "Quarantine banner appears."},
                    {"id": "AC-Q2", "description": "Dismissal RPC is wired."},
                ],
                "complete": True,
            }
        ),
        generated_from="approved_object",
    )
    backfill_status = ArtifactBackfillStatus(
        normalizer_version="test-sidecar-cutover",
        subfeatures={
            "vscode-fork-shell": ArtifactBackfillSubfeatureStatus(
                slug="vscode-fork-shell",
                migration_state="migrated",
            )
        },
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "artifact-backfill-status":
                return backfill_status.model_dump_json(indent=2)
            if key == "dag-contract:vscode-fork-shell":
                return json.dumps(
                    {
                        "slug": "vscode-fork-shell",
                        "canonical_ac_ids": ["AC-vscode-fork-shell-1"],
                        "waived_ac_ids": [],
                        "global_obligation_ac_ids": [],
                        "global_obligation_candidate_step_ids": {},
                        "step_contracts": [],
                    },
                    indent=2,
                )
            if key == "test-plan:vscode-fork-shell":
                return """
## Acceptance Criteria

- **AC-vscode-fork-shell-1** — Stale raw markdown gate.
  - verification_method: `integration`
  - pass_condition: stale raw markdown still exists
""".strip()
            if key == "test-plan-structured:vscode-fork-shell":
                return sidecar.model_dump_json(indent=2)
            return ""

    coverage = await task_planning_module._validate_verification_gates_coverage(
        SimpleNamespace(artifacts=_Artifacts()),
        feature,
        "vscode-fork-shell",
        [
            _valid_task(
                task_id="T-vscode-q1",
                slug="vscode-fork-shell",
                verification_gates=["AC-Q1"],
            )
        ],
    )

    assert coverage.unknown_gate_refs == []
    assert coverage.uncovered_ac_ids == ["AC-Q2"]


@pytest.mark.asyncio
async def test_validate_verification_gates_coverage_uses_mirror_sidecar_when_store_is_stale(tmp_path):
    feature = SimpleNamespace(id="feat-vscode-coverage-mirror", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    sidecar = build_structured_artifact(
        "test-plan:vscode-fork-shell",
        json.dumps(
            {
                "acceptance_criteria": [
                    {"id": "AC-Q1", "description": "Quarantine banner appears."},
                    {"id": "AC-Q2", "description": "Dismissal RPC is wired."},
                ],
                "complete": True,
            }
        ),
        generated_from="approved_object",
    )
    backfill_status = ArtifactBackfillStatus(
        normalizer_version="test-sidecar-cutover",
        subfeatures={
            "vscode-fork-shell": ArtifactBackfillSubfeatureStatus(
                slug="vscode-fork-shell",
                migration_state="migrated",
            )
        },
    )
    mirror.write_artifact(feature.id, "artifact-backfill-status", backfill_status.model_dump_json(indent=2))
    mirror.write_artifact(
        feature.id,
        "test-plan-structured:vscode-fork-shell",
        sidecar.model_dump_json(indent=2),
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "dag-contract:vscode-fork-shell":
                return json.dumps(
                    {
                        "slug": "vscode-fork-shell",
                        "canonical_ac_ids": ["AC-vscode-fork-shell-1"],
                        "waived_ac_ids": [],
                        "global_obligation_ac_ids": [],
                        "global_obligation_candidate_step_ids": {},
                        "step_contracts": [],
                    },
                    indent=2,
                )
            if key == "test-plan:vscode-fork-shell":
                return """
## Acceptance Criteria

- **AC-vscode-fork-shell-1** — Stale raw markdown gate.
  - verification_method: `integration`
  - pass_condition: stale raw markdown still exists
""".strip()
            return ""

    coverage = await task_planning_module._validate_verification_gates_coverage(
        SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror}),
        feature,
        "vscode-fork-shell",
        [
            _valid_task(
                task_id="T-vscode-q1",
                slug="vscode-fork-shell",
                verification_gates=["AC-Q1"],
            )
        ],
    )

    assert coverage.unknown_gate_refs == []
    assert coverage.uncovered_ac_ids == ["AC-Q2"]


def test_task_planning_recovers_embedded_step_heading_for_atomic_slices():
    plan_markdown = """
## Implementation Steps

### STEP-16: Bridge server side

REQ-13
Bridge state snapshot.
**Requirement IDs.** All SF-2 ACs.### STEP-17 — Centralized validation module

REQ-17
Centralize validation helpers.

### STEP-18: Validation call sites

REQ-18
Wire validation into handlers.
""".strip()
    test_plan_markdown = """
## Acceptance Criteria

- **AC-backend-foundation-setup-76** — Validation module rejects malformed payloads.
  - linked_requirement: `REQ-17`
  - verification_method: `integration`
  - pass_condition: invalid payloads are rejected
""".strip()

    parsed = TaskPlanningPhase._parse_test_plan(test_plan_markdown)
    slices = TaskPlanningPhase._derive_atomic_slices_from_markdown_plan(
        plan_markdown,
        parsed,
        fallback_ac_ids=sorted(task_planning_module._extract_ac_ids(test_plan_markdown)),
    )

    assert [slice_info.step_ids for slice_info in slices] == [
        ["STEP-16"],
        ["STEP-17"],
        ["STEP-18"],
    ]
    assert slices[1].step_titles == ["— Centralized validation module"]


@pytest.mark.asyncio
async def test_task_planning_contract_compiler_prefers_explicit_step_ownership_signals(tmp_path):
    feature = SimpleNamespace(id="feat-contract-compiler-explicit", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan:contracts": """
## Implementation Steps

### STEP-1: Bootstrap

- **Requirement refs.** REQ-1
- **AC refs.** AC-contracts-1
- **Acceptance.** Lands AC-contracts-1 exactly.

### STEP-2: Finalize

- **Requirement refs.** REQ-2
- **Acceptance.** Closes AC-contracts-2 safely.
""".strip(),
                "prd:contracts": "## Requirements\n\nREQ-1\nBootstrap.\n\nREQ-2\nFinalize.\n",
                "design:contracts": "",
                "system-design:contracts": "",
                "test-plan:contracts": """
## Acceptance Criteria

- **AC-contracts-1** — Bootstrap works.
  - linked_requirement: `REQ-1`
  - verification_method: `integration`
  - pass_condition: bootstrap works

- **AC-contracts-2** — Finalize works.
  - linked_requirement: `REQ-2`
  - verification_method: `integration`
  - pass_condition: finalize works
""".strip(),
                "decisions:contracts": "",
                "decisions": "",
                "decisions:broad": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    contract = await TaskPlanningPhase._compile_subfeature_planning_contract(
        runner,
        feature,
        "contracts",
    )

    assert contract.canonical_ac_ids == ["AC-contracts-1", "AC-contracts-2"]
    assert contract.global_obligation_ac_ids == []
    step_map = {step.step_id: step for step in contract.step_contracts}
    assert step_map["STEP-1"].explicit_owned_ac_ids == ["AC-contracts-1"]
    assert step_map["STEP-1"].owned_ac_ids == ["AC-contracts-1"]
    assert step_map["STEP-2"].explicit_owned_ac_ids == ["AC-contracts-2"]
    assert step_map["STEP-2"].owned_ac_ids == ["AC-contracts-2"]
    assert runner.artifacts.store["dag-contract:contracts"]


@pytest.mark.asyncio
async def test_task_planning_contract_compiler_marks_shared_requirement_ac_as_global(tmp_path):
    feature = SimpleNamespace(id="feat-contract-compiler-global", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan:contracts-global": """
## Implementation Steps

### STEP-1: Producer

- **Requirement refs.** REQ-shared

### STEP-2: Consumer

- **Requirement refs.** REQ-shared
""".strip(),
                "prd:contracts-global": "## Requirements\n\nREQ-shared\nShared.\n",
                "design:contracts-global": "",
                "system-design:contracts-global": "",
                "test-plan:contracts-global": """
## Acceptance Criteria

- **AC-contracts-global-1** — Shared behavior remains correct.
  - linked_requirement: `REQ-shared`
  - verification_method: `integration`
  - pass_condition: shared behavior remains correct
""".strip(),
                "decisions:contracts-global": "",
                "decisions": "",
                "decisions:broad": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    contract = await TaskPlanningPhase._compile_subfeature_planning_contract(
        runner,
        feature,
        "contracts-global",
    )

    assert contract.global_obligation_ac_ids == ["AC-contracts-global-1"]
    assert contract.global_obligation_candidate_step_ids == {
        "AC-contracts-global-1": ["STEP-2"]
    }
    step_map = {step.step_id: step for step in contract.step_contracts}
    assert step_map["STEP-1"].owned_ac_ids == ["AC-contracts-global-1"]
    assert step_map["STEP-2"].owned_ac_ids == []


@pytest.mark.asyncio
async def test_task_planning_contract_compiler_fails_when_ac_has_no_deterministic_owner(tmp_path):
    feature = SimpleNamespace(id="feat-contract-compiler-unresolved", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan:contracts-unresolved": """
## Implementation Steps

### STEP-1: Producer

Create producer workflow.

### STEP-2: Consumer

Create consumer workflow.
""".strip(),
                "prd:contracts-unresolved": "## Requirements\n\nREQ-shared\nShared.\n",
                "design:contracts-unresolved": "",
                "system-design:contracts-unresolved": "",
                "test-plan:contracts-unresolved": """
## Acceptance Criteria

- **AC-contracts-unresolved-1** — Shared behavior remains correct.
  - verification_method: `integration`
  - pass_condition: shared behavior remains correct
""".strip(),
                "decisions:contracts-unresolved": "",
                "decisions": "",
                "decisions:broad": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    with pytest.raises(task_planning_module.PlanningContractError) as excinfo:
        await TaskPlanningPhase._compile_subfeature_planning_contract(
            runner,
            feature,
            "contracts-unresolved",
        )

    assert "AC-contracts-unresolved-1 is not owned by any step" in str(excinfo.value)
    assert "dag-contract-report:contracts-unresolved" in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_contract_compiler_ignores_legacy_manifest_state(tmp_path):
    feature = SimpleNamespace(id="feat-contract-compiler-legacy-ignore", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    manifest = _slice_manifest_with_current_digests(
        slug="contracts-legacy-ignore",
        plan_text="""
## Implementation Steps

### STEP-1: Producer

Create producer workflow.

### STEP-2: Consumer

Create consumer workflow.
""".strip(),
        test_plan_text="""
## Acceptance Criteria

- **AC-contracts-legacy-ignore-1** — Shared behavior remains correct.
  - verification_method: `integration`
  - pass_condition: shared behavior remains correct
""".strip(),
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Producer",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-contracts-legacy-ignore-1"],
                owned_acceptance_criterion_ids=["AC-contracts-legacy-ignore-1"],
                strict_acceptance_criteria=True,
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:contracts-legacy-ignore": manifest.model_dump_json(indent=2),
                "plan:contracts-legacy-ignore": """
## Implementation Steps

### STEP-1: Producer

Create producer workflow.

### STEP-2: Consumer

Create consumer workflow.
""".strip(),
                "prd:contracts-legacy-ignore": "## Requirements\n\nREQ-shared\nShared.\n",
                "design:contracts-legacy-ignore": "",
                "system-design:contracts-legacy-ignore": "",
                "test-plan:contracts-legacy-ignore": """
## Acceptance Criteria

- **AC-contracts-legacy-ignore-1** — Shared behavior remains correct.
  - verification_method: `integration`
  - pass_condition: shared behavior remains correct
""".strip(),
                "decisions:contracts-legacy-ignore": "",
                "decisions": "",
                "decisions:broad": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    with pytest.raises(task_planning_module.PlanningContractError):
        await TaskPlanningPhase._compile_subfeature_planning_contract(
            runner,
            feature,
            "contracts-legacy-ignore",
        )


@pytest.mark.asyncio
async def test_task_planning_contract_compiler_rejects_requirement_ids_missing_from_prd(tmp_path):
    feature = SimpleNamespace(id="feat-contract-compiler-req-universe", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan:contracts-req": """
## Implementation Steps

### STEP-1: Producer

- **Requirement refs.** REQ-phantom
- **AC refs.** AC-contracts-req-1
""".strip(),
                "prd:contracts-req": "## Requirements\n\nREQ-real\nReal requirement.\n",
                "design:contracts-req": "",
                "system-design:contracts-req": "",
                "test-plan:contracts-req": """
## Acceptance Criteria

- **AC-contracts-req-1** — Shared behavior remains correct.
  - linked_requirement: `REQ-phantom`
  - verification_method: `integration`
  - pass_condition: shared behavior remains correct
""".strip(),
                "decisions:contracts-req": "",
                "decisions": "",
                "decisions:broad": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    with pytest.raises(task_planning_module.PlanningContractError) as excinfo:
        await TaskPlanningPhase._compile_subfeature_planning_contract(
            runner,
            feature,
            "contracts-req",
        )

    assert "STEP-1 references unknown requirement_id REQ-phantom" in str(excinfo.value)


@pytest.mark.asyncio
async def test_refresh_sidecar_for_source_artifact_updates_status_and_invalidates_indexes(tmp_path):
    feature = SimpleNamespace(id="feat-refresh-sidecar", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "planning-index:accounts": json.dumps({"slug": "accounts"}),
                "artifact-audit:accounts": json.dumps({"slug": "accounts"}),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    await refresh_sidecar_for_source_artifact(
        runner,
        feature,
        "plan:accounts",
        """
## Implementation Steps

### STEP-1: Bootstrap

- **Requirement refs.** REQ-1
- **AC refs.** AC-accounts-1
""".strip(),
        generated_from="approved_object",
    )

    assert "plan-structured:accounts" in runner.artifacts.store
    assert "planning-index:accounts" not in runner.artifacts.store
    assert "artifact-audit:accounts" not in runner.artifacts.store
    status = json.loads(runner.artifacts.store["artifact-backfill-status"])
    assert status["subfeatures"]["accounts"]["migration_state"] == "backfilled"
    assert status["subfeatures"]["accounts"]["join_complete"] is False


@pytest.mark.asyncio
async def test_load_source_artifact_text_prefers_mirror_markdown_for_source_artifacts(tmp_path):
    feature = SimpleNamespace(id="feat-source-prefer-mirror", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="plan:accounts",
        text="# Technical Plan\n\n## Implementation Steps\n\n### STEP-1: Mirror\n\n**Instructions:**\n\nUse the mirrored markdown.\n",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "plan:accounts":
                return TechnicalPlan(
                    steps=[ImplementationStep(id="STEP-1", objective="DB", instructions="Use DB JSON")],
                    complete=True,
                ).model_dump_json(indent=2)
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    source_text = await load_source_artifact_text(runner, feature, "plan:accounts")

    assert "Mirror" in source_text
    assert "DB JSON" not in source_text


def test_build_structured_artifact_chunkifies_json_plan_inputs():
    artifact_text = TechnicalPlan(
        steps=[
            ImplementationStep(id="STEP-1", objective="First", instructions="Do the first thing"),
            ImplementationStep(id="STEP-2", objective="Second", instructions="Do the second thing"),
        ],
        complete=True,
    ).model_dump_json(indent=2)

    sidecar = build_structured_artifact(
        "plan:demo",
        artifact_text,
        generated_from="approved_object",
    )

    chunk_ids = [step.chunk.chunk_id for step in sidecar.content.steps]
    assert all(chunk_ids)
    assert len(chunk_ids) == len(set(chunk_ids))
    assert all(step.chunk.content_digest for step in sidecar.content.steps)

    planning_index, report = build_subfeature_planning_index("demo", {"plan": sidecar}, None)
    assert report.issues == []
    slices = TaskPlanningPhase._derive_atomic_slices_from_planning_index(sidecar, planning_index)
    assert [slice_info.step_ids for slice_info in slices] == [["STEP-1"], ["STEP-2"]]
    assert len({slice_info.slice_id for slice_info in slices}) == 2


def test_build_structured_artifact_chunkifies_json_test_plan_inputs():
    artifact_text = OutputTestPlan(
        acceptance_criteria=[
            OutputTestAcceptanceCriterion(id="AC-demo-1", description="First", linked_requirement="REQ-1"),
            OutputTestAcceptanceCriterion(id="AC-demo-2", description="Second", linked_requirement="REQ-2"),
        ],
        complete=True,
    ).model_dump_json(indent=2)

    sidecar = build_structured_artifact(
        "test-plan:demo",
        artifact_text,
        generated_from="approved_object",
    )

    chunk_ids = [criterion.chunk.chunk_id for criterion in sidecar.content.acceptance_criteria]
    assert all(chunk_ids)
    assert len(chunk_ids) == len(set(chunk_ids))
    assert all(criterion.chunk.content_digest for criterion in sidecar.content.acceptance_criteria)


@pytest.mark.asyncio
async def test_shared_sidecar_bootstrap_skips_planning_index_on_parity_failure(monkeypatch):
    feature = SimpleNamespace(id="feat-shared-bootstrap-parity", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decomposition": _decomposition().model_dump_json(indent=2),
                "prd:broad": "# Broad PRD\n\n## Requirements\n\n1. **REQ-1 (must, functional):** Real requirement.\n",
                "design:broad": "",
                "plan:broad": "",
                "decisions:broad": "",
                "decisions:global": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    original_normalize = task_planning_module.normalize_and_persist_source_artifact

    async def _fake_normalize(*args, **kwargs):
        result = await original_normalize(*args, **kwargs)
        artifact_key = args[2]
        if artifact_key == "prd:broad":
            return SimpleNamespace(
                sidecar_key=result.sidecar_key,
                sidecar=result.sidecar,
                parity_messages=["synthetic parity failure"],
                issues=[
                    ArtifactAuditIssue(
                        classification="parity_failed",
                        artifact_family="prd",
                        artifact_key="prd:broad",
                        message="synthetic parity failure",
                    )
                ],
            )
        return result

    monkeypatch.setattr(task_planning_module, "normalize_and_persist_source_artifact", _fake_normalize)

    shared_index, status = await TaskPlanningPhase._ensure_shared_sidecar_bootstrap(
        runner,
        feature,
    )

    assert shared_index is None
    assert "planning-index:shared" not in runner.artifacts.store
    assert status.shared_statuses["prd"].status == "parity_failed"


@pytest.mark.asyncio
async def test_workstream_planner_context_uses_raw_broad_artifacts_until_shared_migrated(tmp_path):
    feature = SimpleNamespace(id="feat-workstream-shared-gate", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    raw_prd = "# Broad PRD\n\n## Requirements\n\n- accounts raw requirement\n"
    stale_sidecar = build_structured_artifact(
        "prd:broad",
        "# Broad PRD\n\n## Requirements\n\n- stale sidecar requirement\n",
        generated_from="markdown_backfill",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "prd:broad": raw_prd,
                "prd-structured:broad": stale_sidecar.model_dump_json(indent=2),
                "design:broad": "",
                "plan:broad": "",
                "decisions:broad": "",
                "decisions:global": "",
                "artifact-backfill-status": json.dumps(
                    {
                        "shared_statuses": {
                            "prd": {
                                "status": "backfilled",
                            }
                        }
                    }
                ),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    package = await TaskPlanningPhase._build_workstream_planner_context_package(
        runner,
        feature,
        decomposition,
    )

    assert package is not None
    prd_text = Path(package.item_paths["prd:broad"]).read_text(encoding="utf-8")
    assert "accounts raw requirement" in prd_text
    assert "stale sidecar requirement" not in prd_text


def test_plan_markdown_backfill_preserves_non_step_sections():
    markdown = to_markdown(
        TechnicalPlan(
            file_manifest=[FileScope(path="src/accounts.py", action="modify")],
            steps=[
                ImplementationStep(
                    id="STEP-1",
                    objective="Bootstrap",
                    instructions="Wire the bootstrap path.",
                )
            ],
            journey_verifications=[
                JourneyVerification(
                    journey_id="J-1",
                    steps=[
                        JourneyVerifyStep(
                            step_number=1,
                            verify_blocks=[VerifyBlock(type="api", expectation="bootstrap request succeeds")],
                            data_testids=["accounts-bootstrap"],
                        )
                    ],
                )
            ],
            architectural_risks=[
                ArchitecturalRisk(
                    id="RISK-1",
                    severity="high",
                    description="Bootstrap can regress startup ordering.",
                    mitigation="Keep startup sequencing explicit.",
                    affected_step_ids=["STEP-1"],
                )
            ],
            complete=True,
        )
    )

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    assert len(sidecar.content.file_manifest) == 1
    assert len(sidecar.content.journey_verifications) == 1
    assert len(sidecar.content.journey_verifications[0].steps) == 1
    assert sidecar.content.journey_verifications[0].steps[0].data_testids == ["accounts-bootstrap"]
    assert len(sidecar.content.architectural_risks) == 1
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_parses_bullet_metadata_with_ranges_and_series():
    markdown = """
# Plan

## Implementation Steps

### STEP-1 — Security primitives
- **Requirements:** D-GR-7, REQ-22/23/24, REQ-46.
- **Journeys:** J-SF1-1, J-SF1-2, J-SF1-10 … J-SF1-11.
- **Acceptance Criteria:** AC-1 through AC-3
- **Instructions:** Wire the shared services.
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert step.requirement_ids == ["REQ-22", "REQ-23", "REQ-24", "REQ-46"]
    assert step.journey_ids == ["J-SF1-1", "J-SF1-10", "J-SF1-11", "J-SF1-2"]
    assert step.refs.decision_aliases == ["D-GR-7"]
    assert step.owned_acceptance_criterion_ids == ["AC-1", "AC-2", "AC-3"]
    assert step.instructions == "Wire the shared services."
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_parses_paragraph_metadata_without_implementation_steps_heading():
    markdown = """
# Plan

## Decision Log

- Keep the launcher routed.

### STEP-1 — Extend AppPaths
**Objective.** Make AppPaths the source of truth.
**Scope.**
`paths.py`
**Instructions.**
Implement the path helpers.
**Requirements.** REQ-1 through REQ-3, D-GR-8.
**Journeys.** J-SF4-1 … J-SF4-2.
**Acceptance Criteria.**
- AC-1 is covered.
**Counterexamples.**
- Do not compute project paths ad hoc.
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert step.objective == "Make AppPaths the source of truth."
    assert step.instructions == "Implement the path helpers."
    assert step.requirement_ids == ["REQ-1", "REQ-2", "REQ-3"]
    assert step.journey_ids == ["J-SF4-1", "J-SF4-2"]
    assert step.refs.decision_aliases == ["D-GR-8"]
    assert step.acceptance_criteria == ["AC-1 is covered."]
    assert step.counterexamples == ["Do not compute project paths ad hoc."]
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_uses_objective_and_instructions_as_fallback_trace_sources():
    markdown = """
# Plan

## Implementation Steps

### STEP-1 — Patch canonical docs
**Objective.** Encode D-GR-1, REQ-34, and REQ-35 in the refreshed contract text.
**Instructions.**
Document J-DEMO-1 behavior and AC-1 ownership in the rewritten section.
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert step.requirement_ids == ["REQ-34", "REQ-35"]
    assert step.journey_ids == ["J-DEMO-1"]
    assert step.refs.decision_aliases == ["D-GR-1"]
    assert step.owned_acceptance_criterion_ids == ["AC-1"]


def test_plan_sidecar_index_links_test_plan_criteria_from_step_metadata():
    artifacts = {
        "plan": build_structured_artifact(
            "plan:demo",
            """
# Plan

## Implementation Steps

### STEP-1 — Launcher shell
- **Requirements:** REQ-1 through REQ-3
- **Journeys:** J-DEMO-1
""".strip(),
            generated_from="markdown_backfill",
        ),
        "prd": build_structured_artifact(
            "prd:demo",
            """
# PRD

## Requirements

1. **REQ-1 (must, functional):** First requirement.
2. **REQ-2 (must, functional):** Second requirement.
3. **REQ-3 (must, functional):** Third requirement.
""".strip(),
            generated_from="markdown_backfill",
        ),
        "test-plan": build_structured_artifact(
            "test-plan:demo",
            """
# Test Plan

## Acceptance Criteria

- **AC-demo-1** — Requirement-linked criterion.
  - linked_requirement: `REQ-2`
  - verification_method: `integration`
  - pass_condition: works
""".strip(),
            generated_from="markdown_backfill",
        ),
    }

    index, report = build_subfeature_planning_index("demo", artifacts)

    assert report.issues == []
    assert index.slice_inputs[0].owned_acceptance_criterion_ids == ["AC-demo-1"]


def test_plan_sidecar_index_links_journey_step_criteria_to_matching_step_journey():
    artifacts = {
        "plan": build_structured_artifact(
            "plan:demo",
            """
# Plan

## Implementation Steps

### STEP-1 — Workspace lifecycle
- **Journeys:** J-DEMO-2
""".strip(),
            generated_from="markdown_backfill",
        ),
        "test-plan": build_structured_artifact(
            "test-plan:demo",
            """
# Test Plan

## Acceptance Criteria

- **AC-demo-1** — Journey-step-linked criterion.
  - linked_journey_step_id: `J-DEMO-2#step-3`
  - verification_method: `integration`
  - pass_condition: works
""".strip(),
            generated_from="markdown_backfill",
        ),
    }

    index, report = build_subfeature_planning_index("demo", artifacts)

    assert report.issues == []
    assert index.slice_inputs[0].owned_acceptance_criterion_ids == ["AC-demo-1"]


def test_plan_sidecar_index_canonicalizes_shorthand_acceptance_ids_against_test_plan():
    artifacts = {
        "plan": build_structured_artifact(
            "plan:demo",
            """
# Plan

## Implementation Steps

### STEP-1 — Settings bridge
- **AC refs.** AC-13
""".strip(),
            generated_from="markdown_backfill",
        ),
        "test-plan": build_structured_artifact(
            "test-plan:demo",
            """
# Test Plan

## Acceptance Criteria

- **AC-demo-13** — Canonicalized criterion.
  - verification_method: `integration`
  - pass_condition: works
""".strip(),
            generated_from="markdown_backfill",
        ),
    }

    index, report = build_subfeature_planning_index("demo", artifacts)

    assert report.issues == []
    assert index.slice_inputs[0].owned_acceptance_criterion_ids == ["AC-demo-13"]


def test_prd_markdown_backfill_parses_numbered_requirements_and_strips_table_markup():
    markdown = """
# PRD: Demo

## Requirements

### Functional Requirements

1. **REQ-1 (must, functional):** First requirement.
2. **REQ-2 (should, security):** Second requirement that spans
   multiple lines in the source markdown.

## Acceptance Criteria

| ID | User Action | Expected Observation | Not Criteria | Requirement IDs |
|---|---|---|---|---|
| **AC-1** | User acts | Result is visible | No regression | REQ-1, `REQ-2` |
""".strip()

    sidecar = build_structured_artifact(
        "prd:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    assert [requirement.id for requirement in sidecar.content.structured_requirements] == ["REQ-1", "REQ-2"]
    assert sidecar.content.structured_requirements[1].description.endswith("multiple lines in the source markdown.")
    assert sidecar.content.structured_acceptance_criteria[0].id == "AC-1"
    assert sidecar.content.structured_acceptance_criteria[0].requirement_ids == ["REQ-1", "REQ-2"]
    assert parity_check_structured_artifact("prd:demo", markdown, sidecar) == []


def test_prd_markdown_backfill_parses_h4_journey_headings():
    markdown = """
# PRD: Demo

## Requirements

1. **REQ-1 (must, functional):** First requirement.

## User Journeys

### Happy Path Journeys

#### Journey J-DEMO-1: First launch
- **Actor:** User
- **Outcome:** App opens cleanly.

#### Journey J-DEMO-2: Relaunch
- **Actor:** User
- **Outcome:** App restores prior context.
""".strip()

    sidecar = build_structured_artifact(
        "prd:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    assert [journey.id for journey in sidecar.content.journeys] == ["J-DEMO-1", "J-DEMO-2"]
    assert parity_check_structured_artifact("prd:demo", markdown, sidecar) == []


def test_prd_markdown_backfill_expands_grouped_requirement_and_journey_ranges():
    markdown = """
# PRD: Demo

## Requirements

### Functional Requirements

5. **REQ-5..REQ-7:** Shared contract text.
13. **REQ-13..REQ-15b:** Push-event contract text.
29. **REQ-29..REQ-29c:** Auth contract text.

## User Journeys

### Happy Path Journeys

#### Journey J-B2..J-B5: Shared happy path family.
#### Journey J-B6 (NEW): Worker commit path.
""".strip()

    sidecar = build_structured_artifact(
        "prd:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    requirement_ids = [requirement.id for requirement in sidecar.content.structured_requirements]
    journey_ids = [journey.id for journey in sidecar.content.journeys]

    assert requirement_ids == [
        "REQ-5",
        "REQ-6",
        "REQ-7",
        "REQ-13",
        "REQ-14",
        "REQ-15",
        "REQ-15a",
        "REQ-15b",
        "REQ-29",
        "REQ-29a",
        "REQ-29b",
        "REQ-29c",
    ]
    assert journey_ids == ["J-B2", "J-B3", "J-B4", "J-B5", "J-B6"]
    assert parity_check_structured_artifact("prd:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_ignores_wildcard_journey_prose():
    markdown = """
# Plan

## Implementation Steps

### STEP-8: Verdict computation
- **Requirements:** REQ-21.
- **Instructions:** Map remediation hints to the J-SF2-* journey contracts.
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert step.journey_ids == []
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_expands_requirement_ranges_without_literal_tokens():
    markdown = """
# Plan

## Implementation Steps

### STEP-1: Wire contract
- **Requirements:** REQ-1..REQ-6, REQ-29..REQ-29c, REQ-44..REQ-46.
- **Journeys:** J-B1..J-B3.
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert set(step.requirement_ids) == {
        "REQ-1",
        "REQ-2",
        "REQ-3",
        "REQ-4",
        "REQ-5",
        "REQ-6",
        "REQ-29",
        "REQ-29a",
        "REQ-29b",
        "REQ-29c",
        "REQ-44",
        "REQ-45",
        "REQ-46",
    }
    assert step.journey_ids == ["J-B1", "J-B2", "J-B3"]
    assert all(".." not in requirement_id for requirement_id in step.requirement_ids)
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_strips_unmatched_requirement_suffix_punctuation():
    markdown = """
# Plan

## Implementation Steps

### STEP-12: Save fallback emitter
- **Requirements:** REQ-41 through REQ-45.
- **Journeys:** J-SF1-7.
- **Instructions:** Enforce the explicit-save fallback only when required.
- **Acceptance:**
  - Autosave of the same file -> zero commands (REQ-43).
  - Save a file outside the workspace -> zero commands (REQ-42).
  - With `fsevents=true` -> zero commands (REQ-41).
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert step.requirement_ids == ["REQ-41", "REQ-42", "REQ-43", "REQ-44", "REQ-45"]
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_plan_markdown_backfill_does_not_promote_acceptance_requirement_mentions():
    markdown = """
# Plan

## Implementation Steps

### STEP-13: Bridge events
- **Requirements:** REQ-29, REQ-30.
- **Acceptance:**
  - AC-STEP13-1: Emits the correct event ordering (REQ-25(c), D-SF2D-3).
""".strip()

    sidecar = build_structured_artifact(
        "plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    step = sidecar.content.steps[0]
    assert step.requirement_ids == ["REQ-29", "REQ-30"]
    assert "REQ-25(c)" not in step.requirement_ids
    assert parity_check_structured_artifact("plan:demo", markdown, sidecar) == []


def test_parity_ignores_incidental_ids_outside_canonical_sections():
    plan_markdown = """
# Plan

STEP-ghost is referenced here as an ordering note only.

## 17. Implementation Steps

### STEP-1: Real work
- **Requirement refs.** REQ-1
""".strip()
    plan_sidecar = build_structured_artifact("plan:demo", plan_markdown, generated_from="markdown_backfill")
    assert parity_check_structured_artifact("plan:demo", plan_markdown, plan_sidecar) == []

    prd_markdown = """
# PRD

## Problem Statement

This prose mentions REQ-ghost but it is not a canonical requirement row.

## Requirements

1. **REQ-1 (must, functional):** Real requirement.
""".strip()
    prd_sidecar = build_structured_artifact("prd:demo", prd_markdown, generated_from="markdown_backfill")
    assert parity_check_structured_artifact("prd:demo", prd_markdown, prd_sidecar) == []

    decisions_markdown = _decision_ledger_text(
        DecisionRecord(id="D-1", statement="This supersedes D-ghost in rationale only"),
    )
    decisions_sidecar = build_structured_artifact(
        "decisions:demo",
        decisions_markdown,
        generated_from="markdown_backfill",
    )
    assert parity_check_structured_artifact("decisions:demo", decisions_markdown, decisions_sidecar) == []


def test_test_plan_trace_normalization_handles_whole_backticked_lists():
    markdown = """
# Test Plan

## Acceptance Criteria

- **AC-demo-1** — Demo criterion.
  - linked_requirement: `REQ-1, NFR-2, D-3`
  - verification_method: `unit`
  - pass_condition: `Works`
""".strip()

    sidecar = build_structured_artifact(
        "test-plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    criterion = sidecar.content.acceptance_criteria[0]
    assert criterion.refs.requirement_ids == ["REQ-1"]
    assert criterion.refs.nfr_ids == ["NFR-2"]
    assert criterion.refs.decision_ids == ["D-3"]
    assert parity_check_structured_artifact("test-plan:demo", markdown, sidecar) == []


def test_test_plan_trace_normalization_handles_parenthetical_nfr_and_requirement_suffixes():
    markdown = """
# Test Plan

## Acceptance Criteria

- **AC-demo-1** — Demo criterion.
  - linked_requirement: `NFR (Accessibility), REQ-25(b), REQ-26.a, D-GR-7`
  - verification_method: `unit`
  - pass_condition: `Works`
""".strip()

    sidecar = build_structured_artifact(
        "test-plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    criterion = sidecar.content.acceptance_criteria[0]
    assert criterion.refs.nfr_ids == ["NFR (Accessibility)"]
    assert criterion.refs.requirement_ids == ["REQ-25(b)", "REQ-26.a"]
    assert criterion.refs.decision_aliases == ["D-GR-7"]
    assert parity_check_structured_artifact("test-plan:demo", markdown, sidecar) == []


def test_test_plan_markdown_backfill_parses_table_acceptance_criteria_without_incidental_refs():
    markdown = """
# Test Plan

## Acceptance Criteria

Intro text references AC-ghost but does not define it.

| AC-id | Description | REQ | Method | Pass condition |
|---|---|---|---|---|
| `AC-demo-1` | First criterion | REQ-1, D-2 | integration | Works |
| `AC-demo-2` | Second criterion | NFR-3 | unit | Also works |
""".strip()

    sidecar = build_structured_artifact(
        "test-plan:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    assert [criterion.id for criterion in sidecar.content.acceptance_criteria] == ["AC-demo-1", "AC-demo-2"]
    assert sidecar.content.acceptance_criteria[0].refs.requirement_ids == ["REQ-1"]
    assert sidecar.content.acceptance_criteria[0].refs.decision_ids == ["D-2"]
    assert sidecar.content.acceptance_criteria[1].refs.nfr_ids == ["NFR-3"]
    assert parity_check_structured_artifact("test-plan:demo", markdown, sidecar) == []


def test_system_design_markdown_backfill_accepts_decision_log_heading():
    markdown = """
# System Design

## Overview

Project system design.

## Decision Log

- **TP-1** Keep the hosted route.
- **TP-2** Reuse the shared bridge.
""".strip()

    sidecar = build_structured_artifact(
        "system-design:demo",
        markdown,
        generated_from="markdown_backfill",
    )

    assert sidecar.content.decisions == [
        "**TP-1** Keep the hosted route.",
        "**TP-2** Reuse the shared bridge.",
    ]
    assert parity_check_structured_artifact("system-design:demo", markdown, sidecar) == []


def test_system_design_parity_tolerates_html_escaping_in_rendered_markdown():
    sidecar = build_structured_artifact(
        "system-design:demo",
        json.dumps(
            {
                "title": "System Design",
                "overview": "Overview",
                "decisions": ["TP-1: Bearer <TOKEN> stays renderer-inaccessible."],
                "risks": [],
                "complete": True,
            }
        ),
        generated_from="markdown_backfill",
    )

    assert parity_check_structured_artifact(
        "system-design:demo",
        json.dumps(sidecar.content.model_dump(mode="json")),
        sidecar,
    ) == []


def test_decomposition_backfill_ignores_rationale_continuation_rows():
    markdown = to_markdown(
        SubfeatureDecomposition(
            subfeatures=[
                Subfeature(
                    id="SF-1",
                    slug="accounts",
                    name="Accounts",
                    description="Accounts area",
                    rationale="Keep ownership together.",
                )
            ],
            complete=True,
        )
    )

    sidecar = build_structured_artifact(
        "decomposition",
        markdown,
        generated_from="markdown_backfill",
    )

    assert [subfeature.slug for subfeature in sidecar.content.subfeatures] == ["accounts"]
    assert sidecar.content.subfeatures[0].rationale == "Keep ownership together."


@pytest.mark.asyncio
async def test_parity_failed_subfeature_backfill_persists_audit_and_summary(monkeypatch, tmp_path):
    feature = SimpleNamespace(id="feat-parity-failed-audit", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    markdown = """
## Implementation Steps

### STEP-1: Bootstrap
- **Requirement refs.** REQ-1
""".strip()
    sidecar = build_structured_artifact("plan:accounts", markdown, generated_from="markdown_backfill")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {"plan:accounts": markdown}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    async def _fake_normalize(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            sidecar_key="plan-structured:accounts",
            sidecar=sidecar,
            parity_messages=["synthetic parity failure"],
            issues=[
                ArtifactAuditIssue(
                    classification="parity_failed",
                    artifact_family="plan",
                    artifact_key="plan:accounts",
                    message="synthetic parity failure",
                )
            ],
        )

    monkeypatch.setattr(task_planning_module, "normalize_and_persist_source_artifact", _fake_normalize)
    monkeypatch.setattr(
        TaskPlanningPhase,
        "_subfeature_source_keys",
        classmethod(lambda cls, slug: ["plan:accounts"]),
    )

    status = await TaskPlanningPhase._ensure_subfeature_sidecar_backfill(
        runner,
        feature,
        "accounts",
        shared_index=None,
    )
    await TaskPlanningPhase._write_artifact_audit_summary(runner, feature, decomposition, status)

    report = json.loads(runner.artifacts.store["artifact-audit:accounts"])
    summary = json.loads(runner.artifacts.store["artifact-audit-summary"])
    assert report["issues"][0]["message"] == "synthetic parity failure"
    assert summary["parity_failed_slugs"] == ["accounts"]
    assert summary["source_repairs_required"] == ["accounts"]


@pytest.mark.asyncio
async def test_load_backfill_status_invalidates_missing_normalizer_version():
    feature = SimpleNamespace(id="feat-stale-backfill-status", metadata={})
    stale_status = ArtifactBackfillStatus().model_dump()
    stale_status.pop("normalizer_version", None)

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "artifact-backfill-status":
                return json.dumps(stale_status)
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts())

    status = await TaskPlanningPhase._load_backfill_status(runner, feature)

    assert status is None


@pytest.mark.asyncio
async def test_task_planning_migrated_slug_uses_sidecars_and_planning_index(tmp_path):
    feature = SimpleNamespace(id="feat-sidecar-cutover", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decomposition": decomposition.model_dump_json(indent=2),
                "prd:broad": """
## Requirements

| ID | Category | Priority | Description |
| --- | --- | --- | --- |
| REQ-100 | functional | must | Shared bootstrap contract |
""".strip(),
                "design:broad": "",
                "plan:broad": "",
                "decisions:broad": _decision_ledger_text(
                    DecisionRecord(
                        id="D-201",
                        statement="Broad bootstrap decision.",
                        source_phase="broad",
                    ),
                ),
                "decisions:global": "",
                "prd:accounts": """
## Requirements

| ID | Category | Priority | Description |
| --- | --- | --- | --- |
| REQ-1 | functional | must | Bootstrap works |
""".strip(),
                "design:accounts": """
## Verifiable States

| Component ID | State | Visual Description |
| --- | --- | --- |
| CMP-1 | ready | Ready state is visible |
""".strip(),
                "plan:accounts": """
## Implementation Steps

### STEP-1: Bootstrap

- **Requirement refs.** REQ-1
- **Decision refs.** D-SF1-P1
- **Verifiable state refs.** CMP-1#ready
- **AC refs.** AC-accounts-1
""".strip(),
                "system-design:accounts": SystemDesign(
                    title="Accounts System Design",
                    overview="Accounts system overview",
                    complete=True,
                ).model_dump_json(indent=2),
                "test-plan:accounts": """
## Acceptance Criteria

- **AC-accounts-1** — Bootstrap works.
  - linked_requirement: `REQ-1`
  - verification_method: `integration`
  - pass_condition: bootstrap works
""".strip(),
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(
                        id="D-101",
                        aliases=["D-SF1-P1"],
                        statement="Bootstrap uses the canonical startup contract.",
                        source_phase="subfeature",
                        subfeature_slug="accounts",
                    ),
                ),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    status = await TaskPlanningPhase._ensure_planning_sidecar_migration(
        runner,
        feature,
        decomposition,
    )

    assert status.subfeatures["accounts"].migration_state == "migrated"
    assert "planning-index:accounts" in runner.artifacts.store
    assert "plan-structured:accounts" in runner.artifacts.store

    runner.artifacts.store["plan:accounts"] = """
## Implementation Steps

### STEP-RAW: Wrong

- **Requirement refs.** REQ-raw
""".strip()
    runner.artifacts.store["decisions:accounts"] = "# stale raw decision artifact\n\nD-raw\n"
    runner.artifacts.store["prd:broad"] = "# stale broad prd\n\nREQ-raw-broad\n"
    runner.artifacts.store["decisions:broad"] = "# stale broad decisions\n\nD-raw-broad\n"

    target_texts = await TaskPlanningPhase._load_target_texts(
        runner,
        feature,
        "accounts",
        {},
    )
    assert "STEP-1" in target_texts["plan"]
    assert "STEP-RAW" not in target_texts["plan"]

    subfeature_decisions = await TaskPlanningPhase._load_artifact_text_for_planning(
        runner,
        feature,
        "decisions:accounts",
        backfill_status=status,
    )
    assert "D-101" in subfeature_decisions
    assert "D-raw" not in subfeature_decisions

    broad_prd = await TaskPlanningPhase._load_artifact_text_for_planning(
        runner,
        feature,
        "prd:broad",
        backfill_status=status,
    )
    assert "REQ-100" in broad_prd
    assert "REQ-raw-broad" not in broad_prd

    broad_decisions = await TaskPlanningPhase._load_artifact_text_for_planning(
        runner,
        feature,
        "decisions:broad",
        backfill_status=status,
    )
    assert "D-201" in broad_decisions
    assert "D-raw-broad" not in broad_decisions

    contract = await TaskPlanningPhase._compile_subfeature_planning_contract(
        runner,
        feature,
        "accounts",
    )
    assert contract.canonical_ac_ids == ["AC-accounts-1"]
    assert contract.global_obligation_ac_ids == []
    assert contract.decision_universe == ["D-101", "D-201"]
    assert contract.step_contracts[0].owned_ac_ids == ["AC-accounts-1"]

    manifest = await TaskPlanningPhase._derive_slice_manifest(
        runner,
        feature,
        decomposition.subfeatures[0],
    )
    assert len(manifest.slices) == 1
    assert manifest.slices[0].step_ids == ["STEP-1"]
    assert manifest.slices[0].owned_acceptance_criterion_ids == ["AC-accounts-1"]


def test_task_planning_target_only_test_plan_excerpt_falls_back_to_global_and_supporting_ac_context():
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        supporting_acceptance_criterion_ids=["AC-accounts-2"],
        global_obligation_ac_ids=["AC-accounts-3"],
    )
    test_plan_text = """
## Acceptance Criteria

- **AC-accounts-1** — Irrelevant criterion.
  - verification_method: `integration`
  - pass_condition: irrelevant

- **AC-accounts-2** — Supporting criterion.
  - verification_method: `integration`
  - pass_condition: supporting

- **AC-accounts-3** — Global criterion.
  - verification_method: `integration`
  - pass_condition: global
""".strip()

    excerpt = TaskPlanningPhase._test_plan_excerpt_for_slice(
        test_plan_text,
        slice_info,
        owned_only=True,
    )

    assert "AC-accounts-2" in excerpt
    assert "AC-accounts-3" in excerpt
    assert "AC-accounts-1" not in excerpt


def test_task_planning_step_contract_requires_system_design_when_available():
    contract = task_planning_module.SubfeaturePlanningContract(
        slug="accounts",
        has_prd_artifact=True,
        has_design_artifact=True,
        has_system_design_artifact=True,
        has_test_plan_artifact=True,
        requirement_universe=["REQ-1"],
    )
    step_contract = task_planning_module.StepPlanningContract(
        step_id="STEP-1",
        requirement_ids=["REQ-1"],
        owned_ac_ids=["AC-accounts-1"],
    )

    sources = TaskPlanningPhase._required_reference_sources_for_step_contract(
        contract,
        step_contract,
        target_bundle={
            "plan": "### STEP-1\nREQ-1\n",
            "prd": "REQ-1",
            "design": "CMP-accounts#ready",
            "system-design": "Accounts service",
            "test-plan": "AC-accounts-1",
        },
    )

    assert "plan" in sources
    assert "prd" in sources
    assert "design" in sources
    assert "test-plan" in sources
    assert "system-design" in sources


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


@pytest.mark.asyncio
async def test_targeted_revision_batches_system_design_requests_one_at_a_time(tmp_path):
    feature = SimpleNamespace(id="feat-sd-batch", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-1",
                slug="bridge-protocol",
                name="Bridge Protocol",
                description="Bridge",
            )
        ],
        complete=True,
    )
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="system-design:bridge-protocol",
        text="<h2>Overview</h2>\n<p>Current body</p>\n",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="decisions-summary:bridge-protocol",
        text="# Decision Summary\n\n- Keep the bridge simple.\n",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "system-design:bridge-protocol": "<h2>Overview</h2>\n<p>Current body</p>\n",
                "decisions-summary:bridge-protocol": "# Decision Summary\n\n- Keep the bridge simple.\n",
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
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[str] = []
            self.ask_count = 0

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if isinstance(task, Ask):
                self.prompts.append(task.prompt)
                self.ask_count += 1
                return ArtifactPatchSet(
                    patches=[
                        {
                            "target": "FULL_DOCUMENT",
                            "operation": "replace",
                            "content": f"<h2>Overview</h2>\n<p>Batch {self.ask_count}</p>\n",
                            "find": "",
                            "reasoning": "batch update",
                        }
                    ],
                    summary="",
                )
            raise AssertionError(f"unexpected task type: {type(task).__name__}")

    runner = _Runner()

    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Clarify the bridge schema surface.",
                    reasoning="Need explicit command contracts.",
                    affected_subfeatures=["bridge-protocol"],
                ),
                RevisionRequest(
                    description="Document telemetry events.",
                    reasoning="Need explicit observability hooks.",
                    affected_subfeatures=["bridge-protocol"],
                ),
            ],
            new_decisions=["Add telemetry timeline support."],
        ),
        decomposition=decomposition,
        base_role=lead_architect_gate_reviewer.role,
        output_type=SystemDesign,
        artifact_prefix="system-design",
        checkpoint_prefix="cycle-1",
    )

    assert result.ok is True
    assert result.revised_slugs == ["bridge-protocol"]
    assert len(runner.prompts) == 2
    assert all("Revision batch request file:" in prompt for prompt in runner.prompts)
    assert all("Revision decision context:" in prompt for prompt in runner.prompts)
    assert all("## Mandatory Decisions" not in prompt for prompt in runner.prompts)


@pytest.mark.asyncio
async def test_targeted_revision_rebatches_after_model_boundary_failure(tmp_path):
    feature = SimpleNamespace(id="feat-plan-rebatch", metadata={})
    decomposition = _decomposition()
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="plan:accounts",
        text="## Overview\nold\n",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {"plan:accounts": "## Overview\nold\n"}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key)

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.ask_count = 0

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if isinstance(task, Ask):
                self.ask_count += 1
                if self.ask_count == 1:
                    raise RuntimeError("prompt too long")
                return ArtifactPatchSet(
                    patches=[
                        {
                            "target": "FULL_DOCUMENT",
                            "operation": "replace",
                            "content": f"## Overview\nbatch {self.ask_count}\n",
                            "find": "",
                            "reasoning": "rebatched",
                        }
                    ],
                    summary="",
                )
            raise AssertionError(f"unexpected task type: {type(task).__name__}")

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Change the architecture section.",
                    reasoning="Need more detail.",
                    affected_subfeatures=["accounts"],
                ),
                RevisionRequest(
                    description="Add a file manifest note.",
                    reasoning="Review requested more scope detail.",
                    affected_subfeatures=["accounts"],
                ),
            ]
        ),
        decomposition=decomposition,
        base_role=lead_task_planner_gate_reviewer.role,
        output_type=TechnicalPlan,
        artifact_prefix="plan",
        checkpoint_prefix="cycle-2",
    )

    assert result.ok is True
    assert runner.ask_count == 3


@pytest.mark.asyncio
async def test_targeted_revision_marks_failure_after_exhausting_single_request_retry(tmp_path):
    feature = SimpleNamespace(id="feat-plan-fail", metadata={})
    decomposition = _decomposition()
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="plan:accounts",
        text="## Overview\nold\n",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {"plan:accounts": "## Overview\nold\n"}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key)

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.ask_count = 0

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if isinstance(task, Ask):
                self.ask_count += 1
                if self.ask_count == 1:
                    raise RuntimeError("structured_output is None for ArtifactPatchSet")
                raise RuntimeError("prompt too long")
            raise AssertionError(f"unexpected task type: {type(task).__name__}")

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Rewrite the overview.",
                    reasoning="Need a clearer framing.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_task_planner_gate_reviewer.role,
        output_type=TechnicalPlan,
        artifact_prefix="plan",
        checkpoint_prefix="cycle-3",
    )

    assert result.ok is False
    assert len(result.failed) == 1
    assert "prompt too long" in result.failed[0].reason
    assert runner.ask_count == 2


@pytest.mark.asyncio
async def test_targeted_revision_recovers_stale_markdown_section_targets_with_full_document_retry(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-prd-stale-target-retry", metadata={})
    decomposition = _decomposition()
    mirror = _TestMirror(tmp_path / "features")
    existing_text = (
        "# Accounts PRD\n\n"
        "## Requirements\n\n"
        "REQ-1: Accounts can sign in.\n\n"
        "## Journeys\n\n"
        "J-1: User signs in.\n"
    )
    revised_text = (
        "# Accounts PRD\n\n"
        "## Requirements\n\n"
        "REQ-1: Accounts can sign in with the guarded retry behavior documented.\n\n"
        "## Journeys\n\n"
        "J-1: User signs in.\n"
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text=existing_text,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"prd:accounts": existing_text}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            if len(self.prompts) <= 2:
                return ArtifactPatchSet(
                    patches=[
                        {
                            "target": "## 1. Architecture Summary",
                            "operation": "replace",
                            "content": "## 1. Architecture Summary\n\nStale section content.\n",
                            "find": "",
                            "reasoning": "stale section-targeted patch",
                        }
                    ],
                    summary="",
                )
            return ArtifactPatchSet(
                patches=[
                    {
                        "target": "FULL_DOCUMENT",
                        "operation": "replace",
                        "content": revised_text,
                        "find": "",
                        "reasoning": "recover with complete document",
                    }
                ],
                summary="",
            )

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Document the guarded retry behavior.",
                    reasoning="Plan review requested a PRD correction.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_pm_gate_reviewer.role,
        output_type=PRD,
        artifact_prefix="prd",
        checkpoint_prefix="cycle-stale-target",
    )

    assert result.ok is True
    assert result.revised_slugs == ["accounts"]
    assert runner.artifacts.store["prd:accounts"] == revised_text.rstrip("\n") + "\n"
    assert len(runner.prompts) == 3
    fallback_prompt = runner.prompts[2]
    assert (
        "Recovery mode: return exactly one patch with target 'FULL_DOCUMENT'"
        in fallback_prompt
    )
    assert "Do NOT target non-existent or stale section names" in fallback_prompt
    assert "- Requirements" in fallback_prompt
    assert "- Journeys" in fallback_prompt


@pytest.mark.asyncio
async def test_targeted_revision_resolves_full_document_retry_patchset_pointer(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-prd-patchset-pointer", metadata={})
    decomposition = _decomposition()
    mirror = _TestMirror(tmp_path / "features")
    existing_text = (
        "# Accounts PRD\n\n"
        "## Requirements\n\n"
        "REQ-1: Accounts can sign in.\n"
    )
    revised_text = (
        "# Accounts PRD\n\n"
        "## Requirements\n\n"
        "REQ-1: Accounts can sign in with the file pointer retry documented.\n"
    )
    pointer_patchset_path = tmp_path / "s3b-prd-patch.json"
    pointer_patchset_path.write_text(
        ArtifactPatchSet(
            patches=[
                {
                    "target": "FULL_DOCUMENT",
                    "operation": "replace",
                    "content": revised_text,
                    "find": "",
                    "reasoning": "real full document replacement",
                }
            ],
            summary="",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text=existing_text,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"prd:accounts": existing_text}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            if len(self.prompts) <= 2:
                return ArtifactPatchSet(
                    patches=[
                        {
                            "target": "## Missing Section",
                            "operation": "replace",
                            "content": "## Missing Section\n\nStale target.\n",
                            "find": "",
                            "reasoning": "force full document retry",
                        }
                    ],
                    summary="",
                )
            return ArtifactPatchSet(
                patches=[
                    {
                        "target": "FULL_DOCUMENT",
                        "operation": "replace",
                        "content": (
                            f"{pointer_patchset_path} contains the complete revised "
                            "artifact content. Use the already-written file."
                        ),
                        "find": "",
                        "reasoning": "pointer to patchset file",
                    }
                ],
                summary="",
            )

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Document the retry behavior.",
                    reasoning="Plan review requested the PRD correction.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_pm_gate_reviewer.role,
        output_type=PRD,
        artifact_prefix="prd",
        checkpoint_prefix="cycle-patchset-pointer",
    )

    assert result.ok is True
    assert result.revised_slugs == ["accounts"]
    assert runner.artifacts.store["prd:accounts"] == revised_text.rstrip("\n") + "\n"
    assert len(runner.prompts) == 3


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
async def test_root_dag_gate_transform_adds_aggregate_coverage_and_sf14_markers():
    feature = SimpleNamespace(id="feat-dag-gate", name="DAG Gate")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-14",
                slug="review-phase-views",
                name="Review Phase Views",
                description="Review phase views",
                requirement_ids=["R-4", "R-14"],
            ),
            Subfeature(
                id="SF-15",
                slug="other-surface",
                name="Other Surface",
                description="Other surface",
                requirement_ids=["NFR-8"],
            ),
        ],
        complete=True,
    )
    dag_rows = {
        "dag:review-phase-views": ImplementationDAG(
            tasks=[
                _valid_task(
                    task_id="review-phase-views-slice-10-TASK-SF14-S10-default-variant",
                    slug="review-phase-views",
                    requirement_ids=["REQ-21"],
                ),
                _valid_task(
                    task_id="review-phase-views-slice-10-TASK-SF14-S10-default-variant-tests",
                    slug="review-phase-views",
                    requirement_ids=["REQ-22"],
                ),
            ],
            requirement_coverage={
                "REQ-21": ["review-phase-views-slice-10-TASK-SF14-S10-default-variant"],
                "REQ-22": ["review-phase-views-slice-10-TASK-SF14-S10-default-variant-tests"],
            },
            complete=True,
        ).model_dump_json(),
        "dag:other-surface": ImplementationDAG(
            tasks=[
                _valid_task(
                    task_id="other-surface-task-1",
                    slug="other-surface",
                    requirement_ids=["REQ-99"],
                )
            ],
            requirement_coverage={},
            complete=True,
        ).model_dump_json(),
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return dag_rows.get(key)

    runner = SimpleNamespace(artifacts=_Artifacts())
    transformed = await TaskPlanningPhase._transform_compiled_dag_for_gate_review(
        runner,
        feature,
        decomposition,
        "# Compiled DAG\n\nExisting content.\n",
    )
    transformed_again = await TaskPlanningPhase._transform_compiled_dag_for_gate_review(
        runner,
        feature,
        decomposition,
        transformed,
    )

    assert transformed == transformed_again
    assert "## Aggregated Requirement Coverage (feature-level)" in transformed
    assert "- zero_uncovered: true" in transformed
    assert "uncovered_feature_requirements: []" in transformed
    assert "`R-4`" in transformed
    assert "`NFR-8`" in transformed
    assert "D-GR-DAG-1" in transformed
    assert "REVISIT-bugflow-kanban" in transformed
    assert "review-phase-views-slice-10-TASK-SF14-S10-default-variant" in transformed
    assert "review-phase-views-slice-10-TASK-SF14-S10-default-variant-tests" in transformed


@pytest.mark.asyncio
async def test_root_dag_surface_revision_requests_are_handled_once_per_digest():
    feature = SimpleNamespace(id="feat-dag-root-handler", name="DAG Gate")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(
                id="SF-14",
                slug="review-phase-views",
                name="Review Phase Views",
                description="Review phase views",
                requirement_ids=["R-4"],
            )
        ],
        complete=True,
    )
    dag_text = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="review-phase-views-slice-10-TASK-SF14-S10-default-variant",
                slug="review-phase-views",
                requirement_ids=["REQ-21"],
            )
        ],
        requirement_coverage={
            "REQ-21": ["review-phase-views-slice-10-TASK-SF14-S10-default-variant"]
        },
        complete=True,
    ).model_dump_json()

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return dag_text if key == "dag:review-phase-views" else None

    runner = SimpleNamespace(artifacts=_Artifacts())
    root_request = RevisionRequest(
        description=(
            "Add Aggregated Requirement Coverage and SF-14 "
            "revisit_checkpoint markers for D-GR-DAG-1"
        ),
        reasoning="Root compiled DAG review surface",
        affected_subfeatures=["review-phase-views"],
        severity="blocker",
    )
    normal_request = RevisionRequest(
        description="Tighten review phase task wording",
        reasoning="Task content issue",
        affected_subfeatures=["review-phase-views"],
        severity="blocker",
    )
    plan = RevisionPlan(requests=[root_request, normal_request])
    ledger = GateReviewLedger()

    filtered_plan, transformed = await TaskPlanningPhase._handle_root_dag_gate_revision_plan(
        runner,
        feature,
        decomposition,
        plan,
        "# Compiled DAG\n",
        ledger,
        1,
    )

    assert [request.description for request in filtered_plan.requests] == [
        normal_request.description
    ]
    assert "## Aggregated Requirement Coverage (feature-level)" in transformed
    assert ledger.findings
    assert ledger.findings[0].status == "fix_attempted"
    assert "root-surface transform applied" in ledger.findings[0].revision_attempts[0]

    with pytest.raises(RuntimeError, match="not converging"):
        await TaskPlanningPhase._handle_root_dag_gate_revision_plan(
            runner,
            feature,
            decomposition,
            RevisionPlan(requests=[root_request]),
            transformed,
            ledger,
            2,
        )


@pytest.mark.asyncio
async def test_targeted_revision_rejects_root_dag_surface_requests_before_patching():
    feature = SimpleNamespace(id="feat-dag-targeted", name="DAG Gate")
    plan = RevisionPlan(
        requests=[
            RevisionRequest(
                description="Add uncovered_feature_requirements to the root DAG",
                reasoning="Root compiled DAG surface",
                affected_subfeatures=["review-phase-views"],
                severity="blocker",
            )
        ]
    )
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={})
    result = await targeted_revision(
        runner,
        feature,
        "task-planning",
        revision_plan=plan,
        decomposition=SubfeatureDecomposition(
            subfeatures=[
                Subfeature(
                    id="SF-14",
                    slug="review-phase-views",
                    name="Review Phase Views",
                    description="Review phase views",
                )
            ]
        ),
        base_role=SimpleNamespace(name="planner", prompt=""),
        output_type=ImplementationDAG,
        artifact_prefix="dag",
    )

    assert not result.ok
    assert result.failed[0].slug == "__root__"


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


@pytest.mark.asyncio
async def test_task_planning_decomposes_pending_subfeatures_one_at_a_time_and_retries_with_direct_peers(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-per-sf", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts core"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing flows"),
            Subfeature(id="SF-3", slug="reports", name="Reports", description="Reporting views"),
        ],
        edges=[
            SubfeatureEdge(
                from_subfeature="accounts",
                to_subfeature="billing",
                interface_type="api_call",
                description="Billing reads account identities",
            )
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Runtime",
        subfeature_slugs=["accounts", "billing", "reports"],
        rationale="Shared runtime context",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": "Accounts plan",
            "prd": "Accounts prd",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        },
        "billing": {
            "plan": "Billing plan",
            "prd": "Billing prd",
            "design": "Billing design",
            "system-design": "Billing system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-billing-1\n",
        },
        "reports": {
            "plan": "Reports plan",
            "prd": "Reports prd",
            "design": "Reports design",
            "system-design": "Reports system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-reports-1\n",
        },
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "prd-summary:billing": "Billing summary",
                "prd-summary:reports": "Reports summary",
                "decisions-summary:billing": "Billing decision summary",
                "decisions-summary:reports": "Reports decision summary",
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "test-plan:billing": sf_upstream["billing"]["test-plan"],
                "test-plan:reports": sf_upstream["reports"]["test-plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[tuple[str, str]] = []
            self.calls: dict[str, int] = {}

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            actor_name = task.actor.name
            self.prompts.append((actor_name, task.prompt))
            count = self.calls.get(actor_name, 0) + 1
            self.calls[actor_name] = count
            if actor_name.endswith("accounts-slice-1-all-workstream-peers") and count == 1:
                raise RuntimeError("prompt too long")
            slug = actor_name.split("-slice-", 1)[0].rsplit("-", 1)[-1]
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id=f"T-{slug}-1",
                        slug=slug,
                        verification_gates=[f"AC-{slug}-1"],
                    )
                ],
                execution_order=[[f"T-{slug}-1"]],
                requirement_coverage={f"REQ-{slug}": [f"T-{slug}-1"]},
                complete=True,
            )

    runner = _Runner()

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.calls == {
        "dag-ws-WS-1-accounts-slice-1-all-workstream-peers": 1,
        "dag-ws-WS-1-accounts-slice-1-direct-peers-only": 1,
        "dag-ws-WS-1-billing-slice-1-all-workstream-peers": 1,
        "dag-ws-WS-1-reports-slice-1-all-workstream-peers": 1,
    }
    accounts_prompts = [
        prompt
        for actor_name, prompt in runner.prompts
        if actor_name.startswith("dag-ws-WS-1-accounts-slice-1-")
    ]
    assert len(accounts_prompts) == 2
    assert all("Read the context index first:" in prompt for prompt in accounts_prompts)
    assert all("Reports (reports)" not in prompt for prompt in accounts_prompts)
    first_index = Path(re.search(r"`([^`]+context-index\.md)`", accounts_prompts[0]).group(1))
    second_index = Path(re.search(r"`([^`]+context-index\.md)`", accounts_prompts[1]).group(1))
    assert first_index != second_index
    first_manifest = Path(re.search(r"`([^`]+context-manifest\.md)`", accounts_prompts[0]).group(1))
    second_manifest = Path(re.search(r"`([^`]+context-manifest\.md)`", accounts_prompts[1]).group(1))
    assert "all-workstream-peers" in first_index.name
    assert "direct-peers-only" in second_index.name
    assert "dag-ws-WS-1-accounts-slice-1-all-workstream-peers-peer-context.md" in first_manifest.read_text(encoding="utf-8")
    assert "dag-ws-WS-1-accounts-slice-1-direct-peers-only-peer-context.md" in second_manifest.read_text(encoding="utf-8")
    first_peer_text = (
        first_manifest.parent / "dag-ws-WS-1-accounts-slice-1-all-workstream-peers-peer-context.md"
    ).read_text(encoding="utf-8")
    second_peer_text = (
        second_manifest.parent / "dag-ws-WS-1-accounts-slice-1-direct-peers-only-peer-context.md"
    ).read_text(encoding="utf-8")
    assert "Reports (reports)" in first_peer_text
    assert "Reports (reports)" not in second_peer_text
    assert "Billing (billing)" in second_peer_text
    assert "dag-slices:accounts" in runner.artifacts.store
    assert "dag-fragment:accounts:slice-1" in runner.artifacts.store
    assert "dag:accounts" in runner.artifacts.store
    assert "dag:billing" in runner.artifacts.store
    assert "dag:reports" in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_context_builds_scoped_decision_pack_from_citations(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-context", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = SimpleNamespace(
        id="WS-1",
        name="Accounts",
        rationale="Shared backend work",
        subfeature_slugs=["accounts", "billing"],
        depends_on=[],
    )
    accounts = next(sf for sf in decomposition.subfeatures if sf.slug == "accounts")

    store = {
        "prd:accounts": "Accounts PRD [decision: D-2]",
        "design:accounts": "Accounts design",
        "plan:accounts": "Accounts plan [decision: D-3]",
        "system-design:accounts": "Accounts system design",
        "test-plan:accounts": "Accounts test plan",
        "decisions:accounts": _decision_ledger_text(
            DecisionRecord(id="D-2", statement="Use account decision", source_phase="subfeature", subfeature_slug="accounts"),
        ),
        "prd-summary:billing": "billing prd summary",
        "design-summary:billing": "billing design summary",
        "plan-summary:billing": "billing plan summary",
        "decisions-summary:billing": "# Decision Ledger — billing\n\n- D-4: Billing peer decision\n",
        "decisions:broad": _decision_ledger_text(
            DecisionRecord(id="D-1", statement="Use broad decision", source_phase="broad"),
            DecisionRecord(id="D-77", statement="Uncited broad decision", source_phase="broad"),
        ),
        "decisions:global": _decision_ledger_text(
            DecisionRecord(id="D-3", statement="Use global decision", source_phase="plan-review"),
            DecisionRecord(id="D-88", statement="Uncited global decision", source_phase="plan-review"),
        ),
        "decisions": _decision_ledger_text(
            DecisionRecord(id="D-1", statement="Use broad decision", source_phase="broad"),
            DecisionRecord(id="D-77", statement="Uncited broad decision", source_phase="broad"),
            DecisionRecord(id="D-2", statement="Use account decision", source_phase="subfeature", subfeature_slug="accounts"),
            DecisionRecord(id="D-3", statement="Use global decision", source_phase="plan-review"),
            DecisionRecord(id="D-88", statement="Uncited global decision", source_phase="plan-review"),
            DecisionRecord(id="D-4", statement="Billing peer decision", source_phase="subfeature", subfeature_slug="billing"),
            DecisionRecord(id="D-99", statement="Unrelated reports decision", source_phase="subfeature", subfeature_slug="reports"),
        ),
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        accounts,
        mode_label="all-workstream-peers",
        direct_peer_only=False,
    )

    assert package is not None
    assert "decision-pack" in package.item_paths

    manifest_text = Path(package.manifest_path).read_text(encoding="utf-8")
    assert "Referenced Non-target Decisions" in manifest_text

    decision_pack_text = Path(package.item_paths["decision-pack"]).read_text(encoding="utf-8")
    assert "D-3" in decision_pack_text
    assert "D-4" in decision_pack_text
    assert "D-1" not in decision_pack_text
    assert "D-77" not in decision_pack_text
    assert "D-88" not in decision_pack_text
    assert "D-99" not in decision_pack_text
    assert "`D-3`" in decision_pack_text
    assert "`peer-contract:billing`" in decision_pack_text

    peer_text = Path(package.item_paths["peer-context"]).read_text(encoding="utf-8")
    assert "Peer Contract Context" in peer_text
    assert "PRD Excerpts" in peer_text
    assert "Design Excerpts" in peer_text
    assert "Plan Excerpts" in peer_text


@pytest.mark.asyncio
async def test_task_planning_context_prefers_canonical_peer_artifacts_over_summaries(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-peer-canonical", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = SimpleNamespace(
        id="WS-1",
        name="Accounts",
        rationale="Shared backend work",
        subfeature_slugs=["accounts", "billing"],
        depends_on=[],
    )
    accounts = next(sf for sf in decomposition.subfeatures if sf.slug == "accounts")

    store = {
        "prd:accounts": "Accounts PRD mentions billing handoff",
        "design:accounts": "Accounts design references Billing API",
        "plan:accounts": "Accounts plan",
        "system-design:accounts": "Accounts system design",
        "test-plan:accounts": "Accounts test plan",
        "decisions:accounts": "",
        "prd:billing": "Canonical billing PRD excerpt",
        "design:billing": "Canonical billing design excerpt",
        "plan:billing": "Canonical billing plan excerpt",
        "test-plan:billing": "Canonical billing test-plan excerpt",
        "prd-summary:billing": "SUMMARY SHOULD NOT APPEAR",
        "design-summary:billing": "SUMMARY SHOULD NOT APPEAR",
        "plan-summary:billing": "SUMMARY SHOULD NOT APPEAR",
        "test-plan-summary:billing": "SUMMARY SHOULD NOT APPEAR",
        "decisions:broad": "",
        "decisions:global": "",
        "decisions": "",
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    package = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        accounts,
        mode_label="all-workstream-peers",
        direct_peer_only=False,
    )

    assert package is not None
    peer_text = Path(package.item_paths["peer-context"]).read_text(encoding="utf-8")
    assert "Canonical billing PRD excerpt" in peer_text
    assert "Canonical billing design excerpt" in peer_text
    assert "SUMMARY SHOULD NOT APPEAR" not in peer_text


@pytest.mark.asyncio
async def test_task_planning_scoped_decision_pack_narrows_with_direct_peer_mode(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-scope-mode", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing"),
            Subfeature(id="SF-3", slug="reports", name="Reports", description="Reports"),
        ],
        edges=[
            SubfeatureEdge(
                from_subfeature="accounts",
                to_subfeature="billing",
                interface_type="api_call",
                description="Billing depends on accounts",
            )
        ],
        complete=True,
    )
    workstream = SimpleNamespace(
        id="WS-1",
        name="Accounts",
        rationale="Shared backend work",
        subfeature_slugs=["accounts", "billing", "reports"],
        depends_on=[],
    )
    accounts = next(sf for sf in decomposition.subfeatures if sf.slug == "accounts")
    store = {
        "prd:accounts": "Accounts PRD",
        "plan:accounts": "Accounts plan",
        "design:accounts": "Accounts design mentions reports integration",
        "system-design:accounts": "Accounts system design",
        "test-plan:accounts": "Accounts test plan",
        "decisions:accounts": _decision_ledger_text(
            DecisionRecord(id="D-2", statement="Use account decision", source_phase="subfeature", subfeature_slug="accounts"),
        ),
        "prd-summary:billing": "billing prd summary",
        "design-summary:billing": "billing design summary",
        "plan-summary:billing": "billing plan summary",
        "prd-summary:reports": "reports prd summary",
        "design-summary:reports": "reports design summary",
        "plan-summary:reports": "reports plan summary",
        "decisions-summary:billing": "# Decision Ledger — billing\n\n- D-4: Billing peer decision\n",
        "decisions-summary:reports": "# Decision Ledger — reports\n\n- D-5: Reports peer decision\n",
        "decisions:broad": _decision_ledger_text(
            DecisionRecord(id="D-1", statement="Use broad decision", source_phase="broad"),
        ),
        "decisions:global": _decision_ledger_text(
            DecisionRecord(id="D-3", statement="Use global decision", source_phase="plan-review"),
        ),
        "decisions": _decision_ledger_text(
            DecisionRecord(id="D-1", statement="Use broad decision", source_phase="broad"),
            DecisionRecord(id="D-2", statement="Use account decision", source_phase="subfeature", subfeature_slug="accounts"),
            DecisionRecord(id="D-3", statement="Use global decision", source_phase="plan-review"),
            DecisionRecord(id="D-4", statement="Billing peer decision", source_phase="subfeature", subfeature_slug="billing"),
            DecisionRecord(id="D-5", statement="Reports peer decision", source_phase="subfeature", subfeature_slug="reports"),
        ),
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    all_peers = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        accounts,
        mode_label="all-workstream-peers",
        direct_peer_only=False,
    )
    direct_peers = await TaskPlanningPhase()._build_subfeature_task_context_package(
        runner,
        feature,
        decomposition,
        workstream,
        accounts,
        mode_label="direct-peers-only",
        direct_peer_only=True,
    )

    assert all_peers is not None
    assert direct_peers is not None
    all_text = Path(all_peers.item_paths["decision-pack"]).read_text(encoding="utf-8")
    direct_text = Path(direct_peers.item_paths["decision-pack"]).read_text(encoding="utf-8")
    all_peer_text = Path(all_peers.item_paths["peer-context"]).read_text(encoding="utf-8")
    direct_peer_text = Path(direct_peers.item_paths["peer-context"]).read_text(encoding="utf-8")

    assert "D-4" in all_text
    assert "D-5" in all_text
    assert "D-4" in direct_text
    assert "D-5" not in direct_text
    assert "Reports (reports)" in all_peer_text
    assert "Reports (reports)" not in direct_peer_text


@pytest.mark.asyncio
async def test_task_planning_preflights_context_budget_before_launch(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-budget", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts", "billing"],
        rationale="Shared planning context",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": "### STEP-1: Accounts\n\nREQ-accounts\n",
            "prd": "REQ-accounts",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        },
        "billing": {
            "plan": "### STEP-1: Billing\n\nREQ-billing\n\n" + ("Billing integration detail\n" * 250),
            "prd": "REQ-billing\n\n" + ("Billing peer requirement\n" * 250),
            "design": "## Design System\n\n" + ("Billing peer design detail\n" * 250),
            "system-design": "## Services\n\n" + ("Billing peer system detail\n" * 250),
            "test-plan": "## Acceptance Criteria\n\n- AC-billing-1\n\n" + ("Billing peer verification detail\n" * 250),
        },
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "prd:billing": sf_upstream["billing"]["prd"],
                "design:billing": sf_upstream["billing"]["design"],
                "plan:billing": sf_upstream["billing"]["plan"],
                "test-plan:billing": sf_upstream["billing"]["test-plan"],
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "decisions:accounts": "",
                "decisions:broad": "",
                "decisions:global": "",
                "decisions": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            self.calls.append(task.actor.name)
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-accounts-1",
                        slug="accounts",
                        verification_gates=["AC-accounts-1"],
                    )
                ],
                execution_order=[["T-accounts-1"]],
                requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
                complete=True,
            )

    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 18_000)
    monkeypatch.setattr(task_planning_module, "_SLICE_PEER_CAP_BYTES", 4_000)

    runner = _Runner()
    failure = await TaskPlanningPhase()._decompose_subfeature(
        runner,
        feature,
        decomposition,
        workstream,
        "accounts",
        sf_upstream,
    )

    assert failure is None
    assert runner.calls == ["dag-ws-WS-1-accounts-slice-1-target-only"]
    attempt_text = runner.artifacts.store["dag-fragment-attempt:accounts:slice-1:all-workstream-peers:1"]
    assert "Estimated context bytes" in attempt_text
    assert "peer" in attempt_text


@pytest.mark.asyncio
async def test_task_planning_splits_target_only_slice_when_budget_still_too_large(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-reslice", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts", "billing"],
        rationale="Shared planning context",
        depends_on=[],
    )
    plan_text = "".join(
        f"### STEP-{idx}: Step {idx}\n\nREQ-{idx} J-{idx}\n\n"
        + (
            "Target slice detail\n" * (800 if idx < 5 else 40)
        )
        + "\n"
        for idx in range(1, 6)
    )
    sf_upstream = {
        "accounts": {
            "plan": plan_text,
            "prd": "\n".join(
                [
                    *(
                        f"## Requirement REQ-{idx}\n\nREQ-{idx}\nJ-{idx}\n\n"
                        + ("Requirement detail\n" * (150 if idx < 5 else 20))
                        for idx in range(1, 6)
                    ),
                ]
            ),
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": json.dumps(
                {
                    "acceptance_criteria": [
                        {
                            "id": f"AC-{idx}",
                            "description": ("Acceptance detail " * 70).strip(),
                            "linked_requirement": f"REQ-{idx}",
                            "verification_method": "integration",
                            "pass_condition": f"Condition {idx}",
                            "linked_journey_step_id": f"STEP-{idx}",
                        }
                        for idx in range(1, 6)
                    ],
                    "complete": True,
                }
            ),
        },
        "billing": {
            "plan": "### STEP-1: Billing\n\nREQ-billing\n",
            "prd": "REQ-billing",
            "design": "Billing design",
            "system-design": "Billing system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-billing-1\n",
        },
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan:accounts": plan_text,
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "test-plan:billing": sf_upstream["billing"]["test-plan"],
                "decisions:accounts": "",
                "decisions:broad": "",
                "decisions:global": "",
                "decisions": "",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, sf_upstream.get("accounts", {}).get(key.split(":", 1)[0], ""))

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            self.calls.append(task.actor.name)
            target_slice_match = re.search(
                r"Target slice:\s+`[^`]+`\s+\(([^)]+)\)",
                task.prompt,
            )
            prompt_step_ids = sorted(
                {
                    int(match)
                    for match in re.findall(
                        r"STEP-(\d+)",
                        target_slice_match.group(1) if target_slice_match is not None else task.prompt,
                    )
                }
            )
            if not prompt_step_ids:
                actor_step_suffixes = re.findall(r"slice-(?:\d+-)*(\d+)", task.actor.name)
                if actor_step_suffixes:
                    prompt_step_ids = [int(actor_step_suffixes[-1])]
            if not prompt_step_ids:
                raise AssertionError(f"unexpected actor name: {task.actor.name}")
            tasks = []
            execution_order = []
            requirement_coverage = {}
            for step_num in prompt_step_ids:
                step_id, ac_id, req_id, journey_id = (
                    f"STEP-{step_num}",
                    f"AC-{step_num}",
                    f"REQ-{step_num}",
                    f"J-{step_num}",
                )
                task_id = f"T-{step_id}"
                tasks.append(
                    _valid_task(
                        task_id=task_id,
                        slug="accounts",
                        verification_gates=[ac_id],
                    ).model_copy(
                        update={
                            "step_ids": [step_id],
                            "requirement_ids": [req_id],
                            "journey_ids": [journey_id],
                            "reference_material": [
                                TaskReference(source=f"Plan {step_id}", content=f"{step_id} plan context"),
                                TaskReference(source=f"PRD {req_id}", content=f"{req_id} requirement context"),
                                TaskReference(source=f"Test-Plan {ac_id}", content=f"{ac_id} verification context"),
                                TaskReference(source="Design excerpt", content="design context"),
                                TaskReference(source="System-Design excerpt", content="system design context"),
                            ],
                        }
                    )
                )
                execution_order.append([task_id])
                requirement_coverage[req_id] = [task_id]
            return ImplementationDAG(
                tasks=tasks,
                execution_order=execution_order,
                requirement_coverage=requirement_coverage,
                complete=True,
            )

    monkeypatch.setattr(task_planning_module, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 30_000)
    original_estimate_context_package = TaskPlanningPhase._estimate_context_package.__func__

    def _estimate_context_package(cls, package):
        total_bytes, size_breakdown = original_estimate_context_package(cls, package)
        if package is None:
            return total_bytes, size_breakdown
        step_ids: set[str] = set()
        for path in package.item_paths.values():
            try:
                step_ids.update(re.findall(r"STEP-\d+", Path(path).read_text(encoding="utf-8")))
            except OSError:
                continue
        if len(step_ids) > 1:
            return 40_000, {"target": 40_000}
        return total_bytes, size_breakdown

    monkeypatch.setattr(
        TaskPlanningPhase,
        "_estimate_context_package",
        classmethod(_estimate_context_package),
    )

    runner = _Runner()
    failure = await TaskPlanningPhase()._decompose_subfeature(
        runner,
        feature,
        decomposition,
        workstream,
        "accounts",
        sf_upstream,
    )

    assert failure is None
    manifest = task_planning_module.TaskPlanningSliceManifest.model_validate_json(
        runner.artifacts.store["dag-slices:accounts"]
    )
    assert len(manifest.slices) > 2
    assert any(slice_info.slice_id.count("-") >= 2 for slice_info in manifest.slices)
    assert [status.slice_id for status in manifest.statuses] == [slice_info.slice_id for slice_info in manifest.slices]
    assert len({slice_info.slice_id for slice_info in manifest.slices}) == len(manifest.slices)
    assert any("target-only" in call for call in runner.calls)
    assert any(key.startswith("dag-fragment:accounts:") for key in runner.artifacts.store)
    assert "dag:accounts" in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_split_preserves_sibling_fragment_mappings_on_resume(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-reslice-resume", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    plan_text = "".join(
        f"### STEP-{idx}: Step {idx}\n\nREQ-{idx} J-{idx}\n\n"
        for idx in range(1, 5)
    )
    test_plan_text = "## Acceptance Criteria\n\n" + "\n".join(
        f"- AC-{idx}" for idx in range(1, 5)
    ) + "\n"
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="First",
                step_ids=["STEP-1"],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-2",
                title="Middle",
                step_ids=["STEP-2", "STEP-3"],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-3",
                title="Last",
                step_ids=["STEP-4"],
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-2",
                status="pending",
                fragment_key="dag-fragment:accounts:slice-2",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-3",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-3",
            ),
        ],
    )
    manifest.attempts = [
        task_planning_module.SlicePlanningAttempt(
            slice_id="slice-2",
            mode="target-only",
            attempt_key="dag-fragment-attempt:accounts:slice-2:target-only:1",
        )
    ]

    class _Artifacts:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.store = {
                "plan:accounts": plan_text,
                "test-plan:accounts": test_plan_text,
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": "fragment-1",
                "dag-fragment:accounts:slice-2": "fragment-2",
                "dag-fragment:accounts:slice-3": "fragment-3",
                "dag-fragment-attempt:accounts:slice-2:target-only:1": "attempt",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-1", "fragment-1")
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-2", "fragment-2")
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-3", "fragment-3")
    mirror.write_artifact(feature.id, "dag-fragment-attempt:accounts:slice-2:target-only:1", "attempt")

    changed = await TaskPlanningPhase._split_oversized_slice(
        runner,
        feature,
        manifest,
        "slice-2",
    )

    assert changed is True
    assert [slice_info.slice_id for slice_info in manifest.slices] == [
        "slice-1",
        "slice-2-1",
        "slice-2-2",
        "slice-3",
    ]
    assert len({slice_info.slice_id for slice_info in manifest.slices}) == len(manifest.slices)
    assert [status.slice_id for status in manifest.statuses] == [
        "slice-1",
        "slice-2-1",
        "slice-2-2",
        "slice-3",
    ]
    status_by_id = {status.slice_id: status for status in manifest.statuses}
    assert status_by_id["slice-1"].fragment_key == "dag-fragment:accounts:slice-1"
    assert status_by_id["slice-3"].fragment_key == "dag-fragment:accounts:slice-3"
    assert "dag-fragment:accounts:slice-1" in runner.artifacts.store
    assert "dag-fragment:accounts:slice-3" in runner.artifacts.store
    assert "dag-fragment:accounts:slice-2" not in runner.artifacts.store
    assert "dag-fragment-attempt:accounts:slice-2:target-only:1" not in runner.artifacts.store
    assert runner.artifacts.deleted == [
        "dag-fragment:accounts:slice-2",
        "dag-fragment-attempt:accounts:slice-2:target-only:1",
    ]


@pytest.mark.asyncio
async def test_task_planning_split_reapplies_bfs_owned_acceptance_overrides_to_child_slices(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-bfs-split", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    plan_text = """
## Implementation Steps

### STEP-8: Verdict publication

REQ-8
Publish setup-check verdicts.

### STEP-9: Worker env composition

REQ-9
Compose worker env values.

### STEP-13: Bridge auth handling

REQ-13
Handle auth fallback.

### STEP-17: Centralized validation module

REQ-17
Centralize validation.

### STEP-21: Artifact RPC dispatcher

REQ-21
Dispatch artifact RPC calls.
""".strip()
    test_plan_text = """
## Acceptance Criteria

- **AC-backend-foundation-setup-25** — Verdict publication stays canonical.
  - linked_requirement: `REQ-125`
  - verification_method: `integration`
  - pass_condition: verdict publication is canonical

- **AC-backend-foundation-setup-52** — Worker env composition is stable.
  - linked_requirement: `REQ-152`
  - verification_method: `integration`
  - pass_condition: worker env composition is stable

- **AC-backend-foundation-setup-38** — Auth handling enforces canonical fallback.
  - linked_requirement: `REQ-138`
  - verification_method: `integration`
  - pass_condition: canonical fallback is enforced

- **AC-backend-foundation-setup-39** — Auth handling persists diagnostics.
  - linked_requirement: `REQ-139`
  - verification_method: `integration`
  - pass_condition: diagnostics persist

- **AC-backend-foundation-setup-40** — Auth handling reports failures safely.
  - linked_requirement: `REQ-140`
  - verification_method: `integration`
  - pass_condition: failures are reported safely

- **AC-backend-foundation-setup-76** — Validation rejects malformed method.
  - linked_requirement: `REQ-176`
  - verification_method: `integration`
  - pass_condition: malformed method is rejected

- **AC-backend-foundation-setup-77** — Validation rejects malformed path.
  - linked_requirement: `REQ-177`
  - verification_method: `integration`
  - pass_condition: malformed path is rejected

- **AC-backend-foundation-setup-78** — Validation rejects malformed payload.
  - linked_requirement: `REQ-178`
  - verification_method: `integration`
  - pass_condition: malformed payload is rejected

- **AC-backend-foundation-setup-79** — Validation redacts internal details.
  - linked_requirement: `REQ-179`
  - verification_method: `integration`
  - pass_condition: internal details stay redacted

- **AC-backend-foundation-setup-80** — Validation preserves error codes.
  - linked_requirement: `REQ-180`
  - verification_method: `integration`
  - pass_condition: error codes are preserved

- **AC-backend-foundation-setup-81** — Validation stays centralized.
  - linked_requirement: `REQ-181`
  - verification_method: `integration`
  - pass_condition: validation stays centralized

- **AC-backend-foundation-setup-82** — Validation reports canonical payloads.
  - linked_requirement: `REQ-182`
  - verification_method: `integration`
  - pass_condition: canonical payloads are reported

- **AC-backend-foundation-setup-86** — Artifact RPC enforces canonical request shape.
  - linked_requirement: `REQ-186`
  - verification_method: `integration`
  - pass_condition: canonical request shape is enforced

- **AC-backend-foundation-setup-88** — Artifact RPC enforces canonical response shape.
  - linked_requirement: `REQ-188`
  - verification_method: `integration`
  - pass_condition: canonical response shape is enforced
""".strip()
    manifest = _slice_manifest_with_current_digests(
        slug="backend-foundation-setup",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="BFS parent",
                step_ids=["STEP-8", "STEP-9", "STEP-13", "STEP-17", "STEP-21"],
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="pending",
                fragment_key="dag-fragment:backend-foundation-setup:slice-1",
            ),
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.store = {
                "plan:backend-foundation-setup": plan_text,
                "test-plan:backend-foundation-setup": test_plan_text,
                "dag-slices:backend-foundation-setup": manifest.model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-1": "fragment",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    changed = await TaskPlanningPhase._split_oversized_slice(
        runner,
        feature,
        manifest,
        "slice-1",
    )

    assert changed is True
    slice_by_step = {
        slice_info.step_ids[0]: slice_info
        for slice_info in manifest.slices
    }
    assert slice_by_step["STEP-8"].owned_acceptance_criterion_ids == ["AC-backend-foundation-setup-25"]
    assert slice_by_step["STEP-9"].owned_acceptance_criterion_ids == ["AC-backend-foundation-setup-52"]
    assert set(slice_by_step["STEP-13"].owned_acceptance_criterion_ids) == {
        "AC-backend-foundation-setup-38",
        "AC-backend-foundation-setup-39",
        "AC-backend-foundation-setup-40",
    }
    assert set(slice_by_step["STEP-17"].owned_acceptance_criterion_ids) == {
        "AC-backend-foundation-setup-76",
        "AC-backend-foundation-setup-77",
        "AC-backend-foundation-setup-78",
        "AC-backend-foundation-setup-79",
        "AC-backend-foundation-setup-80",
        "AC-backend-foundation-setup-81",
        "AC-backend-foundation-setup-82",
    }
    assert set(slice_by_step["STEP-21"].owned_acceptance_criterion_ids) == {
        "AC-backend-foundation-setup-84",
        "AC-backend-foundation-setup-85",
        "AC-backend-foundation-setup-86",
        "AC-backend-foundation-setup-87",
        "AC-backend-foundation-setup-88",
    }


@pytest.mark.asyncio
async def test_workstream_planner_uses_compact_context_package(tmp_path):
    feature = SimpleNamespace(id="feat-workstream-package", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    store = {
        "plan": "FULL GLOBAL PLAN SHOULD NOT APPEAR",
        "decisions": "FULL CANONICAL DECISIONS SHOULD NOT APPEAR",
        "prd:broad": "## Requirements\n\nREQ-accounts\nREQ-billing\n",
        "design:broad": "## Design System\n\nShared runtime shell\n",
        "plan:broad": "## Implementation Steps\n\n### STEP-1: Shared runtime\n",
        "decisions:broad": _decision_ledger_text(
            DecisionRecord(id="D-1", statement="Broad decision", source_phase="broad"),
        ),
        "decisions:global": _decision_ledger_text(
            DecisionRecord(id="D-2", statement="Global decision", source_phase="plan-review"),
        ),
        "prd:accounts": "REQ-accounts",
        "plan:accounts": "### STEP-1: Accounts\n\nREQ-accounts\n",
        "test-plan:accounts": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        "prd:billing": "REQ-billing",
        "plan:billing": "### STEP-1: Billing\n\nREQ-billing\n",
        "test-plan:billing": "## Acceptance Criteria\n\n- AC-billing-1\n",
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    package = await TaskPlanningPhase()._build_workstream_planner_context_package(
        runner,
        feature,
        decomposition,
    )

    assert package is not None
    manifest_text = Path(package.manifest_path).read_text(encoding="utf-8")
    assert "Subfeature Planning Digests" in manifest_text
    assert "workstream-planner-subfeature-digests.md" in manifest_text
    assert "workstream-planner-decisions.md" in manifest_text
    package_text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in package.item_paths.values()
    )
    assert "FULL GLOBAL PLAN SHOULD NOT APPEAR" not in package_text
    assert "FULL CANONICAL DECISIONS SHOULD NOT APPEAR" not in package_text


@pytest.mark.asyncio
async def test_task_planning_marks_slug_failed_after_repeated_model_boundary_errors(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-boundary", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts", "billing"],
        rationale="Shared planning context",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": "Accounts plan",
            "prd": "Accounts prd",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        },
        "billing": {
            "plan": "Billing plan",
            "prd": "Billing prd",
            "design": "Billing design",
            "system-design": "Billing system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-billing-1\n",
        },
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "test-plan:billing": sf_upstream["billing"]["test-plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            if "billing" in task.actor.name:
                raise AssertionError("workstream should stop after accounts fails")
            if task.actor.name.endswith("all-workstream-peers"):
                raise RuntimeError("structured_output is None for ImplementationDAG")
            if task.actor.name.endswith("direct-peers-only"):
                raise RuntimeError("structured_output is None for ImplementationDAG")
            raise RuntimeError("prompt too long")

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
        )

    assert len(failures) == 1
    assert failures[0].slug == "accounts"
    assert "prompt too long" in failures[0].reason
    assert runner.calls == [
        "dag-ws-WS-1-accounts-slice-1-all-workstream-peers",
        "dag-ws-WS-1-accounts-slice-1-direct-peers-only",
        "dag-ws-WS-1-accounts-slice-1-target-only",
    ]
    assert "dag:accounts" not in runner.artifacts.store
    assert "dag:billing" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_retries_wrapped_model_boundary_failures(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-wrapped-boundary", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Shared planning context",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": "Accounts plan",
            "prd": "Accounts prd",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        },
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            if task.actor.name.endswith("all-workstream-peers"):
                outer = RuntimeError("Task Ask failed in phase 'task-planning'")
                outer.__cause__ = RuntimeError(
                    "structured_output is None for ImplementationDAG after retry"
                )
                raise outer
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-accounts-1",
                        slug="accounts",
                        verification_gates=["AC-accounts-1"],
                    )
                ],
                execution_order=[["T-accounts-1"]],
                requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
                complete=True,
            )

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.calls == [
        "dag-ws-WS-1-accounts-slice-1-all-workstream-peers",
        "dag-ws-WS-1-accounts-slice-1-direct-peers-only",
    ]
    assert "dag-slices:accounts" in runner.artifacts.store
    assert "dag-fragment:accounts:slice-1" in runner.artifacts.store
    assert "dag:accounts" in runner.artifacts.store


def test_is_model_boundary_failure_detects_wrapped_cause():
    outer = RuntimeError("Task Ask failed in phase 'task-planning'")
    outer.__cause__ = RuntimeError(
        "structured_output is None for ImplementationDAG after retry"
    )

    assert _is_model_boundary_failure(outer) is True


@pytest.mark.asyncio
async def test_task_planning_blocks_invalid_verification_coverage_before_persisting_dag(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-coverage", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    sf_upstream = {
        "bridge-protocol": {
            "plan": "Bridge plan",
            "prd": "Bridge prd",
            "design": "Bridge design",
            "system-design": "Bridge system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-bridge-protocol-1\n- AC-bridge-protocol-2\n",
        }
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "test-plan:bridge-protocol": sf_upstream["bridge-protocol"]["test-plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-bridge-1",
                        slug="bridge-protocol",
                        verification_gates=["AC-1"],
                    )
                ],
                execution_order=[["T-bridge-1"]],
                requirement_coverage={"REQ-bridge": ["T-bridge-1"]},
                complete=True,
            )

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert len(failures) == 1
    assert "outside slice scope" in failures[0].reason
    assert "AC-1" in failures[0].reason
    assert "dag:bridge-protocol" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_coverage_repair_reconciles_completed_fragment_without_llm():
    feature = SimpleNamespace(id="feat-task-plan-deterministic-repair", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    slice_info = task_planning_module.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-bridge"],
        owned_acceptance_criterion_ids=["AC-bridge-protocol-1", "AC-bridge-protocol-2"],
        acceptance_criterion_ids=["AC-bridge-protocol-1", "AC-bridge-protocol-2"],
        strict_acceptance_criteria=True,
        required_reference_sources=["plan", "prd", "test-plan"],
    )
    manifest = task_planning_module.TaskPlanningSliceManifest(
        slug="bridge-protocol",
        slices=[slice_info],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:bridge-protocol:slice-1",
            )
        ],
    )
    stale_fragment = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-bridge-1",
                slug="bridge-protocol",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-bridge"],
                verification_gates=["AC-bridge-protocol-1"],
            )
        ],
        execution_order=[["T-bridge-1"]],
        requirement_coverage={"REQ-bridge": ["T-bridge-1"]},
        complete=True,
    )
    test_plan = OutputTestPlan(
        acceptance_criteria=[
            OutputTestAcceptanceCriterion(
                id="AC-bridge-protocol-1",
                description="first bridge gate",
                linked_requirement="REQ-bridge",
            ),
            OutputTestAcceptanceCriterion(
                id="AC-bridge-protocol-2",
                description="second bridge gate",
                linked_requirement="REQ-bridge",
                pass_condition="second bridge condition",
            ),
        ],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-fragment:bridge-protocol:slice-1": stale_fragment.model_dump_json(indent=2),
                "test-plan:bridge-protocol": test_plan.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True}

        async def run(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("deterministic repair should not invoke the model")

    runner = _Runner()

    repaired = await TaskPlanningPhase()._attempt_coverage_repair(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        {},
        manifest,
        task_planning_module.VerificationCoverageResult(
            slug="bridge-protocol",
            uncovered_ac_ids=["AC-bridge-protocol-2"],
            uncovered_owned_ac_ids=["AC-bridge-protocol-2"],
        ),
    )

    assert repaired is not None
    persisted = json.loads(runner.artifacts.store["dag-fragment:bridge-protocol:slice-1"])
    assert persisted["tasks"][0]["verification_gates"] == [
        "AC-bridge-protocol-1",
        "AC-bridge-protocol-2",
    ]
    assert "AC-bridge-protocol-2" in {
        gate for task in repaired.tasks for gate in task.verification_gates
    }


@pytest.mark.asyncio
async def test_task_planning_autonomous_coverage_repair_persists_corrected_dag(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-autorepair", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    sf_upstream = {
        "bridge-protocol": {
            "plan": "Bridge plan",
            "prd": "Bridge prd",
            "design": "Bridge design",
            "system-design": "Bridge system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-bridge-protocol-1\n- AC-bridge-protocol-2\n",
        }
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "test-plan:bridge-protocol": sf_upstream["bridge-protocol"]["test-plan"],
                "plan:bridge-protocol": sf_upstream["bridge-protocol"]["plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

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
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            if task.actor.name == "dag-ws-WS-3-bridge-protocol-slice-1-all-workstream-peers":
                return ImplementationDAG(
                    tasks=[
                        _valid_task(
                            task_id="T-bridge-1",
                            slug="bridge-protocol",
                            verification_gates=["AC-bridge-protocol-1"],
                        )
                    ],
                    execution_order=[["T-bridge-1"]],
                    requirement_coverage={"REQ-bridge": ["T-bridge-1"]},
                    complete=True,
                )
            if task.actor.name == "dag-ws-WS-3-bridge-protocol-slice-1-repair-all-workstream-peers":
                return ImplementationDAG(
                    tasks=[
                        _valid_task(
                            task_id="T-bridge-1",
                            slug="bridge-protocol",
                            verification_gates=[
                                "AC-bridge-protocol-1",
                                "AC-bridge-protocol-2",
                            ],
                        )
                    ],
                    execution_order=[["T-bridge-1"]],
                    requirement_coverage={"REQ-bridge": ["T-bridge-1"]},
                    complete=True,
                )
            raise AssertionError(f"unexpected actor: {task.actor.name}")

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.calls == [
        "dag-ws-WS-3-bridge-protocol-slice-1-all-workstream-peers",
        "dag-ws-WS-3-bridge-protocol-slice-1-repair-all-workstream-peers",
    ]
    assert "dag-fragment:bridge-protocol:slice-1" in runner.artifacts.store
    persisted = json.loads(runner.artifacts.store["dag:bridge-protocol"])
    assert persisted["tasks"][0]["verification_gates"] == [
        "AC-bridge-protocol-1",
        "AC-bridge-protocol-2",
    ]


@pytest.mark.asyncio
async def test_task_planning_autonomous_coverage_repair_resplits_oversized_target_only_slice(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-autorepair-resplit", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    sf_upstream = {
        "bridge-protocol": {
            "plan": (
                "### STEP-1: Init\n\nREQ-bridge-1\nJ-bridge-1\n\n"
                "### STEP-2: Wire\n\nREQ-bridge-2\nJ-bridge-2\n"
            ),
            "prd": "REQ-bridge-1\nREQ-bridge-2\n",
            "design": "Bridge design",
            "system-design": "Bridge system design",
            "test-plan": json.dumps(
                {
                    "acceptance_criteria": [
                        {
                            "id": "AC-bridge-protocol-1",
                            "description": "Init works",
                            "linked_requirement": "REQ-bridge-1",
                            "verification_method": "integration",
                            "pass_condition": "Init condition",
                            "linked_journey_step_id": "STEP-1",
                        },
                        {
                            "id": "AC-bridge-protocol-2",
                            "description": "Wire works",
                            "linked_requirement": "REQ-bridge-2",
                            "verification_method": "integration",
                            "pass_condition": "Wire condition",
                            "linked_journey_step_id": "STEP-2",
                        },
                    ],
                    "complete": True,
                }
            ),
        }
    }
    manifest = task_planning_module.TaskPlanningSliceManifest(
        slug="bridge-protocol",
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Whole subfeature",
                step_ids=["STEP-1", "STEP-2"],
                requirement_ids=["REQ-bridge-1", "REQ-bridge-2"],
                journey_ids=["J-bridge-1", "J-bridge-2"],
                acceptance_criterion_ids=["AC-bridge-protocol-1", "AC-bridge-protocol-2"],
                strict_acceptance_criteria=True,
            )
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:bridge-protocol:slice-1",
            )
        ],
    )
    stale_fragment = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-bridge-parent",
                slug="bridge-protocol",
                verification_gates=["AC-bridge-protocol-1"],
            ).model_copy(
                update={
                    "step_ids": ["STEP-1", "STEP-2"],
                    "requirement_ids": ["REQ-bridge-1", "REQ-bridge-2"],
                }
            )
        ],
        execution_order=[["T-bridge-parent"]],
        requirement_coverage={"REQ-bridge-1": ["T-bridge-parent"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:bridge-protocol": manifest.model_dump_json(indent=2),
                "dag-fragment:bridge-protocol:slice-1": stale_fragment.model_dump_json(indent=2),
                "plan:bridge-protocol": sf_upstream["bridge-protocol"]["plan"],
                "test-plan:bridge-protocol": sf_upstream["bridge-protocol"]["test-plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "autonomous_remainder": True},
    )
    calls: list[tuple[str, str, str]] = []

    async def _fake_repair_slice_fragment(
        self,
        runner_arg,
        feature_arg,
        manifest_arg,
        decomposition_arg,
        workstream_arg,
        subfeature_arg,
        slice_info,
        sf_upstream_arg,
        current_fragment,
        findings,
        *,
        mode_label,
        direct_peer_only,
    ):
        del self, runner_arg, feature_arg, manifest_arg, decomposition_arg, workstream_arg
        del subfeature_arg, sf_upstream_arg, current_fragment, findings, direct_peer_only
        calls.append(("repair", slice_info.slice_id, mode_label))
        return task_planning_module.SlicePlanResult(
            slice_id=slice_info.slice_id,
            error=f"repair {mode_label} failed",
            retryable=True,
            over_budget=(mode_label == "target-only"),
            attempt=task_planning_module.SlicePlanningAttempt(
                slice_id=slice_info.slice_id,
                mode=f"repair-{mode_label}",
                chosen_mode=mode_label,
                attempt=TaskPlanningPhase._next_slice_attempt_number(
                    manifest,
                    slice_id=slice_info.slice_id,
                    mode_label=f"repair-{mode_label}",
                ),
                attempt_key=TaskPlanningPhase._slice_attempt_key(
                    "bridge-protocol",
                    slice_info.slice_id,
                    f"repair-{mode_label}",
                    TaskPlanningPhase._next_slice_attempt_number(
                        manifest,
                        slice_id=slice_info.slice_id,
                        mode_label=f"repair-{mode_label}",
                    ),
                ),
                status="failed",
            ),
        )

    async def _fake_plan_slice(
        self,
        runner_arg,
        feature_arg,
        manifest_arg,
        decomposition_arg,
        workstream_arg,
        subfeature_arg,
        slice_info,
        sf_upstream_arg,
        *,
        mode_label,
        direct_peer_only,
    ):
        del self, runner_arg, feature_arg, manifest_arg, decomposition_arg, workstream_arg
        del subfeature_arg, sf_upstream_arg, direct_peer_only
        calls.append(("plan", slice_info.slice_id, mode_label))
        step_id = slice_info.step_ids[0]
        ac_id = slice_info.acceptance_criterion_ids[0]
        req_id = slice_info.requirement_ids[0]
        task_id = f"T-{slice_info.slice_id}"
        return task_planning_module.SlicePlanResult(
            slice_id=slice_info.slice_id,
            dag=ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id=task_id,
                        slug="bridge-protocol",
                        verification_gates=[ac_id],
                    ).model_copy(
                        update={
                            "step_ids": [step_id],
                            "requirement_ids": [req_id],
                        }
                    )
                ],
                execution_order=[[task_id]],
                requirement_coverage={req_id: [task_id]},
                complete=True,
            ),
            attempt=task_planning_module.SlicePlanningAttempt(
                slice_id=slice_info.slice_id,
                mode=mode_label,
                chosen_mode=mode_label,
                attempt=TaskPlanningPhase._next_slice_attempt_number(
                    manifest,
                    slice_id=slice_info.slice_id,
                    mode_label=mode_label,
                ),
                attempt_key=TaskPlanningPhase._slice_attempt_key(
                    "bridge-protocol",
                    slice_info.slice_id,
                    mode_label,
                    TaskPlanningPhase._next_slice_attempt_number(
                        manifest,
                        slice_id=slice_info.slice_id,
                        mode_label=mode_label,
                    ),
                ),
                status="succeeded",
            ),
        )

    monkeypatch.setattr(TaskPlanningPhase, "_repair_slice_fragment", _fake_repair_slice_fragment)
    monkeypatch.setattr(TaskPlanningPhase, "_plan_slice", _fake_plan_slice)

    repaired = await TaskPlanningPhase()._attempt_coverage_repair(
        runner,
        feature,
        decomposition,
        workstream,
        decomposition.subfeatures[0],
        sf_upstream,
        manifest,
        task_planning_module.VerificationCoverageResult(
            slug="bridge-protocol",
            uncovered_ac_ids=["AC-bridge-protocol-2"],
        ),
    )

    assert repaired is not None
    assert [slice_info.slice_id for slice_info in manifest.slices] == ["slice-1-1", "slice-1-2"]
    assert "dag-fragment:bridge-protocol:slice-1" not in runner.artifacts.store
    assert "dag-fragment:bridge-protocol:slice-1-1" in runner.artifacts.store
    assert "dag-fragment:bridge-protocol:slice-1-2" in runner.artifacts.store
    assert calls == [
        ("repair", "slice-1", "all-workstream-peers"),
        ("repair", "slice-1", "direct-peers-only"),
        ("repair", "slice-1", "target-only"),
        ("plan", "slice-1-1", "all-workstream-peers"),
        ("plan", "slice-1-2", "all-workstream-peers"),
    ]


def test_task_planning_attempt_numbers_increment_by_slice_and_mode():
    manifest = task_planning_module.TaskPlanningSliceManifest(
        slug="accounts",
        attempts=[
            task_planning_module.SlicePlanningAttempt(
                slice_id="slice-1",
                mode="target-only",
                attempt=1,
                attempt_key="dag-fragment-attempt:accounts:slice-1:target-only:1",
            ),
            task_planning_module.SlicePlanningAttempt(
                slice_id="slice-1",
                mode="target-only",
                attempt=2,
                attempt_key="dag-fragment-attempt:accounts:slice-1:target-only:2",
            ),
            task_planning_module.SlicePlanningAttempt(
                slice_id="slice-1",
                mode="repair-target-only",
                attempt=1,
                attempt_key="dag-fragment-attempt:accounts:slice-1:repair-target-only:1",
            ),
        ],
    )

    assert TaskPlanningPhase._next_slice_attempt_number(
        manifest,
        slice_id="slice-1",
        mode_label="target-only",
    ) == 3
    assert TaskPlanningPhase._next_slice_attempt_number(
        manifest,
        slice_id="slice-1",
        mode_label="repair-target-only",
    ) == 2
    assert TaskPlanningPhase._next_slice_attempt_number(
        manifest,
        slice_id="slice-2",
        mode_label="target-only",
    ) == 1


@pytest.mark.asyncio
async def test_task_planning_normalizes_same_wave_dependency_edges_before_persisting_dag(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-order-fix", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    sf_upstream = {
        "bridge-protocol": {
            "plan": "Bridge plan",
            "prd": "Bridge prd",
            "design": "Bridge design",
            "system-design": "Bridge system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-bridge-protocol-1\n",
        }
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "test-plan:bridge-protocol": sf_upstream["bridge-protocol"]["test-plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-bridge-1",
                        slug="bridge-protocol",
                        verification_gates=["AC-bridge-protocol-1"],
                    ),
                    _valid_task(
                        task_id="T-bridge-2",
                        slug="bridge-protocol",
                        verification_gates=["AC-bridge-protocol-1"],
                        dependencies=["T-bridge-1"],
                    ),
                ],
                execution_order=[["T-bridge-1", "T-bridge-2"]],
                requirement_coverage={
                    "REQ-bridge": ["T-bridge-1", "T-bridge-2"],
                },
                complete=True,
            )

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    persisted = json.loads(runner.artifacts.store["dag:bridge-protocol"])
    assert persisted["execution_order"] == [["T-bridge-1"], ["T-bridge-2"]]


def test_task_planning_preserves_intentionally_serialized_waves():
    dag = ImplementationDAG(
        tasks=[
            _valid_task(task_id="T-bridge-1", slug="bridge-protocol"),
            _valid_task(task_id="T-bridge-2", slug="bridge-protocol"),
        ],
        execution_order=[["T-bridge-1"], ["T-bridge-2"]],
        complete=True,
    )

    normalized, changed = TaskPlanningPhase._normalize_subfeature_execution_order(dag)

    assert changed is False
    assert normalized.execution_order == [["T-bridge-1"], ["T-bridge-2"]]


def test_task_planning_pushes_backward_dependencies_later_without_merging():
    dag = ImplementationDAG(
        tasks=[
            _valid_task(task_id="T-bridge-1", slug="bridge-protocol"),
            _valid_task(
                task_id="T-bridge-2",
                slug="bridge-protocol",
                dependencies=["T-bridge-1"],
            ),
        ],
        execution_order=[["T-bridge-2"], ["T-bridge-1"]],
        complete=True,
    )

    normalized, changed = TaskPlanningPhase._normalize_subfeature_execution_order(dag)

    assert changed is True
    assert normalized.execution_order == [["T-bridge-1"], ["T-bridge-2"]]


def test_task_planning_namespaces_slice_task_ids_and_dependencies():
    dag = ImplementationDAG(
        tasks=[
            _valid_task(task_id="TASK-1", slug="live-edit-sync"),
            _valid_task(
                task_id="TASK-2",
                slug="live-edit-sync",
                dependencies=["TASK-1"],
            ),
        ],
        execution_order=[["TASK-1"], ["TASK-2"]],
        requirement_coverage={"REQ-1": ["TASK-1", "TASK-2"]},
        complete=True,
    )

    namespaced, changed = TaskPlanningPhase._namespace_slice_task_ids(
        dag,
        slug="live-edit-sync",
        slice_id="slice-11",
    )

    assert changed is True
    assert [task.id for task in namespaced.tasks] == [
        "live-edit-sync-slice-11-TASK-1",
        "live-edit-sync-slice-11-TASK-2",
    ]
    assert namespaced.tasks[1].dependencies == ["live-edit-sync-slice-11-TASK-1"]
    assert namespaced.execution_order == [
        ["live-edit-sync-slice-11-TASK-1"],
        ["live-edit-sync-slice-11-TASK-2"],
    ]
    assert namespaced.requirement_coverage == {
        "REQ-1": [
            "live-edit-sync-slice-11-TASK-1",
            "live-edit-sync-slice-11-TASK-2",
        ]
    }


def test_task_planning_rejects_cyclic_dependency_graphs():
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="T-bridge-1",
                name="Task 1",
                description="Task 1",
                subfeature_id="bridge-protocol",
                dependencies=["T-bridge-2"],
            ),
            ImplementationTask(
                id="T-bridge-2",
                name="Task 2",
                description="Task 2",
                subfeature_id="bridge-protocol",
                dependencies=["T-bridge-1"],
            ),
        ],
        execution_order=[["T-bridge-1"], ["T-bridge-2"]],
        complete=True,
    )

    with pytest.raises(ValueError, match="cyclic or unsatisfied dependencies"):
        TaskPlanningPhase._normalize_subfeature_execution_order(dag)


@pytest.mark.asyncio
async def test_task_planning_without_autonomous_remainder_blocks_on_coverage_drift(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-no-autorepair", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    sf_upstream = {
        "bridge-protocol": {
            "plan": "Bridge plan",
            "prd": "Bridge prd",
            "design": "Bridge design",
            "system-design": "Bridge system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-bridge-protocol-1\n- AC-bridge-protocol-2\n",
        }
    }

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "decisions": "Global decisions",
                "test-plan:bridge-protocol": sf_upstream["bridge-protocol"]["test-plan"],
                "plan:bridge-protocol": sf_upstream["bridge-protocol"]["plan"],
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-bridge-1",
                        slug="bridge-protocol",
                        verification_gates=["AC-bridge-protocol-1"],
                    )
                ],
                execution_order=[["T-bridge-1"]],
                requirement_coverage={"REQ-bridge": ["T-bridge-1"]},
                complete=True,
            )

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert len(failures) == 1
    assert "AC-bridge-protocol-2" in failures[0].reason
    assert runner.calls == ["dag-ws-WS-3-bridge-protocol-slice-1-all-workstream-peers"]
    assert "dag:bridge-protocol" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_normalizes_failed_slices_in_place_and_preserves_completed_fragments(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-normalize-pending", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    plan_text = """
## Implementation Steps

### STEP-1: Bootstrap

REQ-shared
Bootstrap the backend.

### STEP-20: Hostile repo defenses

REQ-shared
D-GR-9
Protect checkout and rendering boundaries.
""".strip()
    test_plan_text = """
## Acceptance Criteria

- **AC-accounts-1** — Journey-only criterion.
  - linked_requirement: `REQ-shared`
  - verification_method: `integration`
  - pass_condition: shared requirement completes

- **AC-accounts-2** — Decision-linked repo hardening criterion.
  - linked_requirement: `D-GR-9`
  - verification_method: `integration`
  - pass_condition: hardening is enforced
""".strip()
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-accounts-1", "AC-accounts-2"],
                strict_acceptance_criteria=False,
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-2",
                title="Hostile repo defenses",
                step_ids=["STEP-20"],
                acceptance_criterion_ids=["AC-accounts-1", "AC-accounts-2"],
                strict_acceptance_criteria=False,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-2",
                status="failed",
                retry_mode="target-only",
                context_paths=["/tmp/context.md"],
                last_error="estimated context exceeds budget",
                fragment_key="dag-fragment:accounts:slice-2",
            ),
        ],
        attempts=[
            task_planning_module.SlicePlanningAttempt(
                slice_id="slice-2",
                mode="target-only",
                attempt=1,
                status="failed",
                attempt_key="dag-fragment-attempt:accounts:slice-2:target-only:1",
            )
        ],
    )
    completed_fragment = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
                verification_gates=["AC-accounts-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": completed_fragment.model_dump_json(indent=2),
                "dag-fragment-attempt:accounts:slice-2:target-only:1": "# stale attempt\n",
                "plan:accounts": plan_text,
                "test-plan:accounts": test_plan_text,
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    await TaskPlanningPhase._normalize_pending_slice_manifest(runner, feature, manifest)

    assert manifest.slices[0].acceptance_criterion_ids == []
    assert manifest.slices[0].owned_acceptance_criterion_ids == []
    assert manifest.slices[0].global_obligation_ac_ids == ["AC-accounts-1"]
    assert manifest.slices[0].strict_acceptance_criteria is False
    assert manifest.slices[1].acceptance_criterion_ids == []
    assert manifest.slices[1].owned_acceptance_criterion_ids == []
    assert manifest.slices[1].supporting_acceptance_criterion_ids == []
    assert manifest.slices[1].global_obligation_ac_ids == [
        "AC-accounts-1",
        "AC-accounts-2",
    ]
    assert manifest.slices[1].strict_acceptance_criteria is False
    assert manifest.statuses[0].status == "completed"
    assert manifest.statuses[1].status == "pending"
    assert manifest.statuses[1].retry_mode == ""
    assert manifest.statuses[1].context_paths == []
    assert manifest.statuses[1].last_error == ""
    assert manifest.attempts == []
    assert runner.artifacts.deleted == ["dag-fragment-attempt:accounts:slice-2:target-only:1"]
    assert "dag-fragment:accounts:slice-1" in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_bfs_child_manifest_normalization_reopens_only_changed_completed_slices(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-bfs-child-reconcile", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    plan_text = """
## Implementation Steps

### STEP-2: Logging foundation

REQ-2
Initialize logging and redaction defaults.

### STEP-8: Verdict publication

REQ-8
Publish setup-check verdicts.

### STEP-9: Worker env composition

REQ-9
Compose worker environment values.

### STEP-13: Bridge auth handling

REQ-13
Handle CLI auth fallback.

### STEP-17: Centralized validation module

REQ-17
Centralize validation helpers.

### STEP-21: Artifact RPC dispatcher

REQ-21
Dispatch artifact RPC requests.

### STEP-29: Final cleanup

REQ-29
Finalize remaining backend cleanup.
""".strip()
    test_plan_ids = [1, 25, 38, 39, 40, 52, 56, 76, 77, 78, 79, 80, 81, 82, 86, 88]
    test_plan_text = "## Acceptance Criteria\n\n" + "\n\n".join(
        f"- **AC-backend-foundation-setup-{ac_id}** — Backend acceptance criterion {ac_id}.\n"
        f"  - linked_requirement: `REQ-{29 if ac_id == 1 else 100 + ac_id}`\n"
        "  - verification_method: `integration`\n"
        f"  - pass_condition: criterion {ac_id} is satisfied"
        for ac_id in test_plan_ids
    )
    validation_supporting = [f"AC-backend-foundation-setup-{ac_id}" for ac_id in (76, 77, 78, 79, 80, 81, 82)]
    manifest = _slice_manifest_with_current_digests(
        slug="backend-foundation-setup",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1-2",
                title="Logging foundation",
                step_ids=["STEP-2"],
                acceptance_criterion_ids=["AC-backend-foundation-setup-56"],
                owned_acceptance_criterion_ids=["AC-backend-foundation-setup-56"],
                strict_acceptance_criteria=True,
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-2-4",
                title="Verdict publication",
                step_ids=["STEP-8"],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-3-1",
                title="Worker env composition",
                step_ids=["STEP-9"],
                supporting_acceptance_criterion_ids=["AC-backend-foundation-setup-52"],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-4-1",
                title="Bridge auth handling",
                step_ids=["STEP-13"],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-4-5",
                title="Centralized validation module",
                step_ids=["STEP-17"],
                supporting_acceptance_criterion_ids=validation_supporting,
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-5-4",
                title="Artifact RPC dispatcher",
                step_ids=["STEP-21"],
                supporting_acceptance_criterion_ids=[
                    "AC-backend-foundation-setup-84",
                    "AC-backend-foundation-setup-85",
                    "AC-backend-foundation-setup-86",
                    "AC-backend-foundation-setup-87",
                    "AC-backend-foundation-setup-88",
                ],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-7-4",
                title="Final cleanup",
                step_ids=["STEP-29"],
                acceptance_criterion_ids=["AC-backend-foundation-setup-1"],
                strict_acceptance_criteria=True,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1-2",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-1-2",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-2-4",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-2-4",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-3-1",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-3-1",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-4-1",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-4-1",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-4-5",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-4-5",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-5-4",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-5-4",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-7-4",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-7-4",
            ),
        ],
        attempts=[
            task_planning_module.SlicePlanningAttempt(
                slice_id="slice-4-1",
                mode="direct-peers-only",
                attempt=1,
                status="failed",
                attempt_key="dag-fragment-attempt:backend-foundation-setup:slice-4-1:direct-peers-only:1",
            )
        ],
    )

    def _fragment(
        slice_id: str,
        step_id: str,
        gates: list[str],
        *,
        requirement_ids: list[str] | None = None,
    ) -> ImplementationDAG:
        return ImplementationDAG(
            tasks=[
                _valid_task(
                    task_id=f"T-{slice_id}",
                    slug="backend-foundation-setup",
                    step_ids=[step_id],
                    requirement_ids=requirement_ids,
                    verification_gates=gates,
                )
            ],
            execution_order=[[f"T-{slice_id}"]],
            requirement_coverage={"REQ-backend-foundation-setup": [f"T-{slice_id}"]},
            complete=True,
        )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:backend-foundation-setup": manifest.model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-1-2": _fragment("slice-1-2", "STEP-2", ["AC-backend-foundation-setup-56"], requirement_ids=["REQ-2"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-2-4": _fragment("slice-2-4", "STEP-8", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-8"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-3-1": _fragment("slice-3-1", "STEP-9", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-9"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-4-1": _fragment("slice-4-1", "STEP-13", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-13"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-4-5": _fragment("slice-4-5", "STEP-17", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-17"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-5-4": _fragment("slice-5-4", "STEP-21", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-67"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-7-4": _fragment("slice-7-4", "STEP-29", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-29"]).model_dump_json(indent=2),
                "dag-fragment-attempt:backend-foundation-setup:slice-4-1:direct-peers-only:1": "# stale attempt\n",
                "plan:backend-foundation-setup": plan_text,
                "test-plan:backend-foundation-setup": test_plan_text,
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    await TaskPlanningPhase._normalize_pending_slice_manifest(runner, feature, manifest)

    slice_map = {slice_info.slice_id: slice_info for slice_info in manifest.slices}
    assert slice_map["slice-1-2"].owned_acceptance_criterion_ids == ["AC-backend-foundation-setup-56"]
    assert slice_map["slice-2-4"].owned_acceptance_criterion_ids == ["AC-backend-foundation-setup-25"]
    assert slice_map["slice-3-1"].owned_acceptance_criterion_ids == ["AC-backend-foundation-setup-52"]
    assert slice_map["slice-3-1"].supporting_acceptance_criterion_ids == []
    assert set(slice_map["slice-4-1"].owned_acceptance_criterion_ids) == {
        "AC-backend-foundation-setup-38",
        "AC-backend-foundation-setup-39",
        "AC-backend-foundation-setup-40",
    }
    assert set(slice_map["slice-4-5"].owned_acceptance_criterion_ids) == set(validation_supporting)
    assert slice_map["slice-4-5"].supporting_acceptance_criterion_ids == []
    assert set(slice_map["slice-5-4"].owned_acceptance_criterion_ids) == {
        "AC-backend-foundation-setup-84",
        "AC-backend-foundation-setup-85",
        "AC-backend-foundation-setup-86",
        "AC-backend-foundation-setup-87",
        "AC-backend-foundation-setup-88",
    }
    assert slice_map["slice-7-4"].owned_acceptance_criterion_ids == ["AC-backend-foundation-setup-1"]
    assert slice_map["slice-7-4"].global_obligation_ac_ids == []

    reopened = {"slice-2-4", "slice-3-1", "slice-4-1", "slice-4-5", "slice-5-4"}
    for slice_id in reopened:
        status = next(status for status in manifest.statuses if status.slice_id == slice_id)
        assert status.status == "pending"
        assert status.retry_mode == ""
        assert status.context_paths == []
        assert status.last_error == ""
    assert next(status for status in manifest.statuses if status.slice_id == "slice-1-2").status == "completed"

    assert set(runner.artifacts.deleted) == {
        "dag-fragment:backend-foundation-setup:slice-2-4",
        "dag-fragment:backend-foundation-setup:slice-3-1",
        "dag-fragment:backend-foundation-setup:slice-4-1",
        "dag-fragment:backend-foundation-setup:slice-4-5",
        "dag-fragment:backend-foundation-setup:slice-5-4",
        "dag-fragment-attempt:backend-foundation-setup:slice-4-1:direct-peers-only:1",
    }
    assert "dag-fragment:backend-foundation-setup:slice-1-2" in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_bfs_child_resume_retries_only_reconciled_slices_and_uses_effective_coverage_scope(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-bfs-resume", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="backend-foundation-setup", name="Backend Foundation Setup", description="BFS"),
            Subfeature(id="SF-2", slug="vscode-fork-shell", name="VSCode Fork Shell", description="VSCode"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-A",
        name="Backend Foundation",
        subfeature_slugs=["backend-foundation-setup", "vscode-fork-shell"],
        rationale="Backend foundation scope",
        depends_on=[],
    )
    plan_text = """
## Implementation Steps

### STEP-2: Logging foundation

REQ-2
Initialize logging and redaction defaults.

### STEP-8: Verdict publication

REQ-8
Publish setup-check verdicts.

### STEP-9: Worker env composition

REQ-9
Compose worker environment values.

### STEP-13: Bridge auth handling

REQ-13
Handle CLI auth fallback.

### STEP-17: Centralized validation module

REQ-17
Centralize validation helpers.

### STEP-21: Artifact RPC dispatcher

REQ-67
D-GR-1
D-GR-7
D-GR-11
Dispatch artifact RPC requests.

### STEP-29: Final cleanup

REQ-29
Finalize remaining backend cleanup.
""".strip()
    test_plan_ids = [
        1,
        25,
        26,
        35,
        36,
        38,
        39,
        40,
        52,
        56,
        73,
        74,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        84,
        85,
        86,
        87,
        88,
    ]
    test_plan_text = "## Acceptance Criteria\n\n" + "\n\n".join(
        f"- **AC-backend-foundation-setup-{ac_id}** — Backend acceptance criterion {ac_id}.\n"
        f"  - linked_requirement: `REQ-{29 if ac_id == 1 else ac_id}`\n"
        "  - verification_method: `integration`\n"
        f"  - pass_condition: criterion {ac_id} is satisfied"
        for ac_id in test_plan_ids
    )
    sf_upstream = {
        "backend-foundation-setup": {
            "plan": plan_text,
            "prd": "Backend PRD",
            "design": "Backend design",
            "system-design": "Backend system design",
            "test-plan": test_plan_text,
        },
        "vscode-fork-shell": {
            "plan": "## Implementation Steps\n\n### STEP-1: VSCode shell\n\nREQ-1\nShip VSCode shell.\n",
            "prd": "VSCode PRD",
            "design": "VSCode design",
            "system-design": "VSCode system design",
            "test-plan": "## Acceptance Criteria\n\n- **AC-vscode-fork-shell-1** — VSCode shell works.\n  - linked_requirement: `REQ-1`\n  - verification_method: `integration`\n  - pass_condition: shell works",
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="backend-foundation-setup",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1-2",
                title="Logging foundation",
                step_ids=["STEP-2"],
                acceptance_criterion_ids=["AC-backend-foundation-setup-56"],
                owned_acceptance_criterion_ids=["AC-backend-foundation-setup-56"],
                strict_acceptance_criteria=True,
            ),
            task_planning_module.TaskPlanningSlice(slice_id="slice-2-4", title="Verdict publication", step_ids=["STEP-8"]),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-3-1",
                title="Worker env composition",
                step_ids=["STEP-9"],
                supporting_acceptance_criterion_ids=["AC-backend-foundation-setup-52"],
            ),
            task_planning_module.TaskPlanningSlice(slice_id="slice-4-1", title="Bridge auth handling", step_ids=["STEP-13"]),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-4-5",
                title="Centralized validation module",
                step_ids=["STEP-17"],
                supporting_acceptance_criterion_ids=[
                    "AC-backend-foundation-setup-76",
                    "AC-backend-foundation-setup-77",
                    "AC-backend-foundation-setup-78",
                    "AC-backend-foundation-setup-79",
                    "AC-backend-foundation-setup-80",
                    "AC-backend-foundation-setup-81",
                    "AC-backend-foundation-setup-82",
                ],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-5-4",
                title="Artifact RPC dispatcher",
                step_ids=["STEP-21"],
                supporting_acceptance_criterion_ids=[
                    "AC-backend-foundation-setup-86",
                    "AC-backend-foundation-setup-88",
                ],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-7-4",
                title="Final cleanup",
                step_ids=["STEP-29"],
                acceptance_criterion_ids=["AC-backend-foundation-setup-1"],
                strict_acceptance_criteria=True,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(slice_id="slice-1-2", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-1-2"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-2-4", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-2-4"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-3-1", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-3-1"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-4-1", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-4-1"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-4-5", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-4-5"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-5-4", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-5-4"),
            task_planning_module.SlicePlanningStatus(slice_id="slice-7-4", status="completed", fragment_key="dag-fragment:backend-foundation-setup:slice-7-4"),
        ],
    )
    vscode_manifest = _slice_manifest_with_current_digests(
        slug="vscode-fork-shell",
        plan_text=sf_upstream["vscode-fork-shell"]["plan"],
        test_plan_text=sf_upstream["vscode-fork-shell"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="VSCode shell",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
                acceptance_criterion_ids=["AC-vscode-fork-shell-1"],
                owned_acceptance_criterion_ids=["AC-vscode-fork-shell-1"],
                strict_acceptance_criteria=True,
            )
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:vscode-fork-shell:slice-1",
            )
        ],
    )
    vscode_manifest.complete = True

    def _fragment(
        slice_id: str,
        step_id: str,
        gates: list[str],
        *,
        requirement_ids: list[str] | None = None,
    ) -> ImplementationDAG:
        return ImplementationDAG(
            tasks=[
                _valid_task(
                    task_id=f"T-{slice_id}",
                    slug="backend-foundation-setup",
                    step_ids=[step_id],
                    requirement_ids=requirement_ids,
                    verification_gates=gates,
                )
            ],
            execution_order=[[f"T-{slice_id}"]],
            requirement_coverage={"REQ-backend-foundation-setup": [f"T-{slice_id}"]},
            complete=True,
        )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:backend-foundation-setup": manifest.model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-1-2": _fragment("slice-1-2", "STEP-2", ["AC-backend-foundation-setup-56"], requirement_ids=["REQ-2"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-2-4": _fragment("slice-2-4", "STEP-8", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-8"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-3-1": _fragment("slice-3-1", "STEP-9", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-9"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-4-1": _fragment("slice-4-1", "STEP-13", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-13"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-4-5": _fragment("slice-4-5", "STEP-17", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-17"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-5-4": _fragment("slice-5-4", "STEP-21", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-67"]).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-7-4": _fragment("slice-7-4", "STEP-29", ["AC-backend-foundation-setup-1"], requirement_ids=["REQ-29"]).model_dump_json(indent=2),
                "dag-slices:vscode-fork-shell": vscode_manifest.model_dump_json(indent=2),
                "dag-fragment:vscode-fork-shell:slice-1": _fragment(
                    "slice-1",
                    "STEP-1",
                    ["AC-vscode-fork-shell-1"],
                    requirement_ids=["REQ-1"],
                ).model_dump_json(indent=2),
                "dag:vscode-fork-shell": ImplementationDAG(
                    tasks=[_valid_task(task_id="T-vscode-1", slug="vscode-fork-shell", verification_gates=["AC-vscode-fork-shell-1"])],
                    execution_order=[["T-vscode-1"]],
                    requirement_coverage={"REQ-vscode-fork-shell": ["T-vscode-1"]},
                    complete=True,
                ).model_dump_json(indent=2),
                "plan:backend-foundation-setup": plan_text,
                "test-plan:backend-foundation-setup": test_plan_text,
                "plan:vscode-fork-shell": sf_upstream["vscode-fork-shell"]["plan"],
                "test-plan:vscode-fork-shell": sf_upstream["vscode-fork-shell"]["test-plan"],
                "decisions": "Global decisions",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            actor_name = task.actor.name
            if actor_name.endswith("slice-2-4-all-workstream-peers"):
                return _fragment(
                    "slice-2-4-replanned",
                    "STEP-8",
                    ["AC-backend-foundation-setup-25"],
                    requirement_ids=["REQ-8"],
                )
            if actor_name.endswith("slice-3-1-all-workstream-peers"):
                return _fragment(
                    "slice-3-1-replanned",
                    "STEP-9",
                    ["AC-backend-foundation-setup-52"],
                    requirement_ids=["REQ-9"],
                )
            if actor_name.endswith("slice-4-1-all-workstream-peers"):
                return _fragment(
                    "slice-4-1-replanned",
                    "STEP-13",
                    [
                        "AC-backend-foundation-setup-38",
                        "AC-backend-foundation-setup-39",
                        "AC-backend-foundation-setup-40",
                    ],
                    requirement_ids=["REQ-13"],
                )
            if actor_name.endswith("slice-4-5-all-workstream-peers"):
                return _fragment(
                    "slice-4-5-replanned",
                    "STEP-17",
                    [
                        "AC-backend-foundation-setup-76",
                        "AC-backend-foundation-setup-77",
                        "AC-backend-foundation-setup-78",
                        "AC-backend-foundation-setup-79",
                        "AC-backend-foundation-setup-80",
                        "AC-backend-foundation-setup-81",
                        "AC-backend-foundation-setup-82",
                    ],
                    requirement_ids=["REQ-17"],
                )
            if actor_name.endswith("slice-5-4-all-workstream-peers"):
                return _fragment(
                    "slice-5-4-replanned",
                    "STEP-21",
                    [
                        "AC-backend-foundation-setup-84",
                        "AC-backend-foundation-setup-85",
                        "AC-backend-foundation-setup-86",
                        "AC-backend-foundation-setup-87",
                        "AC-backend-foundation-setup-88",
                    ],
                    requirement_ids=["REQ-67"],
                )
            if actor_name.endswith("slice-7-4-all-workstream-peers"):
                return _fragment(
                    "slice-7-4-replanned",
                    "STEP-29",
                    ["AC-backend-foundation-setup-1"],
                    requirement_ids=["REQ-29"],
                )
            raise AssertionError(f"unexpected actor name: {actor_name}")

    runner = _Runner()

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert set(runner.calls) == {
        "dag-ws-WS-A-backend-foundation-setup-slice-2-4-all-workstream-peers",
        "dag-ws-WS-A-backend-foundation-setup-slice-3-1-all-workstream-peers",
        "dag-ws-WS-A-backend-foundation-setup-slice-4-1-all-workstream-peers",
        "dag-ws-WS-A-backend-foundation-setup-slice-4-5-all-workstream-peers",
        "dag-ws-WS-A-backend-foundation-setup-slice-5-4-all-workstream-peers",
    }
    persisted_manifest = task_planning_module.TaskPlanningSliceManifest.model_validate_json(
        runner.artifacts.store["dag-slices:backend-foundation-setup"]
    )
    assert persisted_manifest.complete is True
    assert all(status.status == "completed" for status in persisted_manifest.statuses)
    persisted_dag = json.loads(runner.artifacts.store["dag:backend-foundation-setup"])
    persisted_gates = {
        gate
        for task in persisted_dag["tasks"]
        for gate in task["verification_gates"]
    }
    assert "AC-backend-foundation-setup-26" not in persisted_gates
    assert {
        "AC-backend-foundation-setup-1",
        "AC-backend-foundation-setup-25",
        "AC-backend-foundation-setup-52",
        "AC-backend-foundation-setup-56",
        "AC-backend-foundation-setup-38",
        "AC-backend-foundation-setup-39",
        "AC-backend-foundation-setup-40",
        "AC-backend-foundation-setup-76",
        "AC-backend-foundation-setup-77",
        "AC-backend-foundation-setup-78",
        "AC-backend-foundation-setup-79",
        "AC-backend-foundation-setup-80",
        "AC-backend-foundation-setup-81",
        "AC-backend-foundation-setup-82",
        "AC-backend-foundation-setup-84",
        "AC-backend-foundation-setup-85",
        "AC-backend-foundation-setup-86",
        "AC-backend-foundation-setup-87",
        "AC-backend-foundation-setup-88",
    } <= persisted_gates


@pytest.mark.asyncio
async def test_task_planning_bfs_final_frontier_reopens_only_last_three_slices(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-bfs-final-frontier", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="backend-foundation-setup", name="Backend Foundation Setup", description="BFS"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-A",
        name="Backend Foundation",
        subfeature_slugs=["backend-foundation-setup"],
        rationale="Backend foundation scope",
        depends_on=[],
    )
    sf_upstream = {
        "backend-foundation-setup": {
            "plan": """
## Implementation Steps

### STEP-2: Logging foundation

REQ-2
Logging foundation.

### STEP-7: DependencyProbeService

REQ-18
REQ-19
REQ-20
REQ-21
REQ-50
Dependency probe implementation.

### STEP-16: Test suite

REQ-21
REQ-58
Test harness and integration suite.

### STEP-21: Artifact RPC dispatcher

D-GR-1
D-GR-8
D-GR-11
Artifact RPC dispatcher and rate limiter.
""".strip(),
            "prd": "BFS prd",
            "design": "BFS design",
            "system-design": "BFS system design",
            "test-plan": """
## Acceptance Criteria

- **AC-backend-foundation-setup-56** — Logging foundation is present.
  - linked_requirement: `REQ-56`
  - verification_method: `unit`
  - pass_condition: present

- **AC-backend-foundation-setup-19** — Probe set is complete.
  - linked_requirement: `REQ-18`
  - verification_method: `integration`
  - pass_condition: complete

- **AC-backend-foundation-setup-21** — Claude auth fallback works.
  - linked_requirement: `REQ-18`
  - verification_method: `integration`
  - pass_condition: works

- **AC-backend-foundation-setup-22** — Probes run in parallel.
  - linked_requirement: `REQ-20`
  - verification_method: `integration`
  - pass_condition: parallel

- **AC-backend-foundation-setup-84** — Artifact-RPC method namespace is exact.
  - linked_requirement: `D-GR-1`
  - verification_method: `unit`
  - pass_condition: exact

- **AC-backend-foundation-setup-85** — Artifact-RPC enforces validation module.
  - linked_requirement: `D-GR-1, D-GR-8`
  - verification_method: `integration`
  - pass_condition: validated

- **AC-backend-foundation-setup-86** — Artifact-RPC serializer is shared.
  - linked_requirement: `D-GR-1`
  - verification_method: `integration`
  - pass_condition: serialized

- **AC-backend-foundation-setup-87** — Artifact-RPC rate-limits per worker.
  - linked_requirement: `D-GR-1, D-GR-11`
  - verification_method: `integration`
  - pass_condition: rate-limited

- **AC-backend-foundation-setup-88** — Artifact-RPC unavailable surfaces the catalog id.
  - linked_requirement: `D-GR-1`
  - verification_method: `integration`
  - pass_condition: unavailable
""".strip(),
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="backend-foundation-setup",
        plan_text=sf_upstream["backend-foundation-setup"]["plan"],
        test_plan_text=sf_upstream["backend-foundation-setup"]["test-plan"],
        slices=[
                task_planning_module.TaskPlanningSlice(
                    slice_id="slice-1-2",
                    title="Logging foundation",
                    step_ids=["STEP-2"],
                    requirement_ids=["REQ-2"],
                    acceptance_criterion_ids=["AC-backend-foundation-setup-56"],
                    owned_acceptance_criterion_ids=["AC-backend-foundation-setup-56"],
                    strict_acceptance_criteria=True,
                ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-2-3",
                title="Dependency probes",
                step_ids=["STEP-7"],
                requirement_ids=["REQ-18", "REQ-19", "REQ-20", "REQ-21", "REQ-50"],
                acceptance_criterion_ids=[
                    "AC-backend-foundation-setup-19",
                    "AC-backend-foundation-setup-21",
                    "AC-backend-foundation-setup-22",
                ],
                owned_acceptance_criterion_ids=[
                    "AC-backend-foundation-setup-19",
                    "AC-backend-foundation-setup-21",
                    "AC-backend-foundation-setup-22",
                ],
                strict_acceptance_criteria=True,
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-4-4",
                title="Test suite",
                step_ids=["STEP-16"],
                requirement_ids=["REQ-21", "REQ-58"],
                acceptance_criterion_ids=[],
                strict_acceptance_criteria=False,
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-5-4",
                title="Artifact RPC dispatcher",
                step_ids=["STEP-21"],
                acceptance_criterion_ids=["AC-backend-foundation-setup-86", "AC-backend-foundation-setup-88"],
                owned_acceptance_criterion_ids=["AC-backend-foundation-setup-86", "AC-backend-foundation-setup-88"],
                supporting_acceptance_criterion_ids=[
                    "AC-backend-foundation-setup-84",
                    "AC-backend-foundation-setup-85",
                    "AC-backend-foundation-setup-87",
                ],
                strict_acceptance_criteria=True,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1-2",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-1-2",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-2-3",
                status="failed",
                retry_mode="target-only",
                last_error="missing requirement_ids",
                fragment_key="dag-fragment:backend-foundation-setup:slice-2-3",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-4-4",
                status="failed",
                retry_mode="target-only",
                last_error="missing requirement_ids",
                fragment_key="dag-fragment:backend-foundation-setup:slice-4-4",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-5-4",
                status="completed",
                fragment_key="dag-fragment:backend-foundation-setup:slice-5-4",
            ),
        ],
    )

    def _fragment(
        slice_id: str,
        step_id: str,
        gates: list[str],
        *,
        requirement_ids: list[str],
    ) -> ImplementationDAG:
        return ImplementationDAG(
            tasks=[
                ImplementationTask(
                    id=f"T-{slice_id}",
                    name="Implement backend-foundation-setup",
                    description="backend-foundation-setup task",
                    subfeature_id="backend-foundation-setup",
                    step_ids=[step_id],
                    requirement_ids=requirement_ids,
                        acceptance_criteria=[
                            TaskAcceptanceCriterion(description="backend-foundation-setup acceptance criterion"),
                        ],
                        reference_material=[
                            TaskReference(source=f"Plan {step_id}", content="backend-foundation-setup reference material"),
                            TaskReference(source=f"PRD {step_id}", content="backend-foundation-setup requirement context"),
                            TaskReference(source=f"Test-Plan {step_id}", content="backend-foundation-setup verification context"),
                        ],
                        verification_gates=gates,
                        dependencies=[],
                    )
                ],
            execution_order=[[f"T-{slice_id}"]],
            requirement_coverage={"REQ-backend-foundation-setup": [f"T-{slice_id}"]},
            complete=True,
        )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:backend-foundation-setup": manifest.model_dump_json(indent=2),
                    "dag-fragment:backend-foundation-setup:slice-1-2": _fragment(
                        "slice-1-2",
                        "STEP-2",
                        ["AC-backend-foundation-setup-56"],
                        requirement_ids=["REQ-2"],
                    ).model_dump_json(indent=2),
                "dag-fragment:backend-foundation-setup:slice-5-4": _fragment(
                    "slice-5-4",
                    "STEP-21",
                    ["AC-backend-foundation-setup-86", "AC-backend-foundation-setup-88"],
                    requirement_ids=[],
                ).model_dump_json(indent=2),
                "plan:backend-foundation-setup": sf_upstream["backend-foundation-setup"]["plan"],
                "test-plan:backend-foundation-setup": sf_upstream["backend-foundation-setup"]["test-plan"],
                "decisions": "Global decisions",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.calls.append(task.actor.name)
            actor_name = task.actor.name
            if actor_name.endswith("slice-2-3-all-workstream-peers"):
                return _fragment(
                    "slice-2-3-replanned",
                    "STEP-7",
                    [
                        "AC-backend-foundation-setup-19",
                        "AC-backend-foundation-setup-21",
                        "AC-backend-foundation-setup-22",
                    ],
                    requirement_ids=["REQ-18", "REQ-19", "REQ-20", "REQ-21", "REQ-50"],
                )
            if actor_name.endswith("slice-4-4-all-workstream-peers"):
                return _fragment(
                    "slice-4-4-replanned",
                    "STEP-16",
                    [],
                    requirement_ids=["REQ-21", "REQ-58"],
                )
            if actor_name.endswith("slice-5-4-all-workstream-peers"):
                return _fragment(
                    "slice-5-4-replanned",
                    "STEP-21",
                    [
                        "AC-backend-foundation-setup-84",
                        "AC-backend-foundation-setup-85",
                        "AC-backend-foundation-setup-86",
                        "AC-backend-foundation-setup-87",
                        "AC-backend-foundation-setup-88",
                    ],
                    requirement_ids=[],
                )
            raise AssertionError(f"unexpected actor name: {actor_name}")

    runner = _Runner()

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert set(runner.calls) == {
        "dag-ws-WS-A-backend-foundation-setup-slice-2-3-all-workstream-peers",
        "dag-ws-WS-A-backend-foundation-setup-slice-4-4-all-workstream-peers",
        "dag-ws-WS-A-backend-foundation-setup-slice-5-4-all-workstream-peers",
    }
    persisted_manifest = task_planning_module.TaskPlanningSliceManifest.model_validate_json(
        runner.artifacts.store["dag-slices:backend-foundation-setup"]
    )
    assert persisted_manifest.complete is True
    assert all(status.status == "completed" for status in persisted_manifest.statuses)
    persisted_dag = json.loads(runner.artifacts.store["dag:backend-foundation-setup"])
    persisted_gates = {
        gate
        for task in persisted_dag["tasks"]
        for gate in task["verification_gates"]
    }
    assert {
        "AC-backend-foundation-setup-19",
        "AC-backend-foundation-setup-21",
        "AC-backend-foundation-setup-22",
        "AC-backend-foundation-setup-84",
        "AC-backend-foundation-setup-85",
        "AC-backend-foundation-setup-86",
        "AC-backend-foundation-setup-87",
        "AC-backend-foundation-setup-88",
    } <= persisted_gates


@pytest.mark.asyncio
async def test_task_planning_keeps_completed_dag_when_only_contract_digest_changes(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-contract-digest-only", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": """
## Implementation Steps

### STEP-1: Bootstrap

- **Requirement refs.** REQ-accounts-1
- **AC refs.** AC-accounts-1
""".strip(),
            "prd": "## Requirements\n\nREQ-accounts-1\nAccounts bootstrap.\n",
            "design": "",
            "system-design": "",
            "test-plan": """
## Acceptance Criteria

- **AC-accounts-1** — Accounts bootstrap works.
  - linked_requirement: `REQ-accounts-1`
  - verification_method: `integration`
  - pass_condition: accounts bootstrap works
""".strip(),
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=sf_upstream["accounts"]["plan"],
        test_plan_text=sf_upstream["accounts"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-accounts-1"],
                acceptance_criterion_ids=["AC-accounts-1"],
                owned_acceptance_criterion_ids=["AC-accounts-1"],
                strict_acceptance_criteria=True,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
        ],
    )
    manifest.complete = True
    completed_fragment = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-accounts-1"],
                verification_gates=["AC-accounts-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts-1": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag:accounts": completed_fragment.model_dump_json(indent=2),
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": completed_fragment.model_dump_json(indent=2),
                "plan:accounts": sf_upstream["accounts"]["plan"],
                "prd:accounts": sf_upstream["accounts"]["prd"],
                "design:accounts": sf_upstream["accounts"]["design"],
                "system-design:accounts": sf_upstream["accounts"]["system-design"],
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "decisions:accounts": "",
                "decisions": "",
                "decisions:broad": "",
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    called_slugs: list[str] = []

    async def _fake_decompose_subfeature(
        self,
        runner_arg,
        feature_arg,
        decomposition_arg,
        workstream_arg,
        slug,
        sf_upstream_arg,
    ):
        del self, runner_arg, feature_arg, decomposition_arg, workstream_arg, sf_upstream_arg
        called_slugs.append(slug)
        return None

    monkeypatch.setattr(TaskPlanningPhase, "_decompose_subfeature", _fake_decompose_subfeature)

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert called_slugs == []
    assert runner.artifacts.deleted == []
    assert "dag:accounts" in runner.artifacts.store
    persisted_manifest = task_planning_module.TaskPlanningSliceManifest.model_validate_json(
        runner.artifacts.store["dag-slices:accounts"]
    )
    assert persisted_manifest.complete is True
    assert persisted_manifest.contract_digest


@pytest.mark.asyncio
async def test_task_planning_resume_reuses_completed_slice_fragments(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-resume", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": "## Implementation Steps\n\n### STEP-1: Bootstrap\n\n**Instructions:**\n\nBootstrap\n\n### STEP-2: Finalize\n\n**Instructions:**\n\nFinalize\n",
            "prd": "Accounts prd",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-accounts-1\n- AC-accounts-2\n",
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=sf_upstream["accounts"]["plan"],
        test_plan_text=sf_upstream["accounts"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                acceptance_criterion_ids=["AC-accounts-1"],
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-2",
                title="Finalize",
                acceptance_criterion_ids=["AC-accounts-2"],
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
            task_planning_module.SlicePlanningStatus(slice_id="slice-2"),
        ],
    )
    completed_fragment = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
                verification_gates=["AC-accounts-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": completed_fragment.model_dump_json(indent=2),
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "plan:accounts": sf_upstream["accounts"]["plan"],
                "decisions": "Global decisions",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            assert task.actor.name == "dag-ws-WS-1-accounts-slice-2-all-workstream-peers"
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-accounts-2",
                        slug="accounts",
                        verification_gates=["AC-accounts-2"],
                    )
                ],
                execution_order=[["T-accounts-2"]],
                requirement_coverage={"REQ-accounts": ["T-accounts-2"]},
                complete=True,
            )

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.calls == ["dag-ws-WS-1-accounts-slice-2-all-workstream-peers"]
    persisted = json.loads(runner.artifacts.store["dag:accounts"])
    assert [task["id"] for task in persisted["tasks"]] == ["T-accounts-1", "T-accounts-2"]


@pytest.mark.asyncio
async def test_task_planning_revalidates_completed_subfeatures_before_skip(tmp_path, monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-revalidate-completed", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": """
## Implementation Steps

### STEP-1: Bootstrap

- **Requirement refs.** REQ-accounts-1
- **AC refs.** AC-accounts-1
""".strip(),
            "prd": "## Requirements\n\nREQ-accounts-1\nAccounts bootstrap.\n",
            "design": "",
            "system-design": "",
            "test-plan": """
## Acceptance Criteria

- **AC-accounts-1** — Accounts bootstrap works.
  - linked_requirement: `REQ-accounts-1`
  - verification_method: `integration`
  - pass_condition: accounts bootstrap works
""".strip(),
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=sf_upstream["accounts"]["plan"],
        test_plan_text=sf_upstream["accounts"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-accounts-1"],
                acceptance_criterion_ids=[],
                strict_acceptance_criteria=False,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
        ],
    )
    completed_fragment = ImplementationDAG(
        tasks=[_valid_task(task_id="T-accounts-1", slug="accounts", verification_gates=["AC-accounts-1"])],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts-1": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag:accounts": completed_fragment.model_dump_json(indent=2),
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": completed_fragment.model_dump_json(indent=2),
                "plan:accounts": sf_upstream["accounts"]["plan"],
                "prd:accounts": sf_upstream["accounts"]["prd"],
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "decisions": "",
                "decisions:broad": "",
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    called_slugs: list[str] = []

    async def _fake_decompose_subfeature(
        self,
        runner_arg,
        feature_arg,
        decomposition_arg,
        workstream_arg,
        slug,
        sf_upstream_arg,
    ):
        del self, runner_arg, feature_arg, decomposition_arg, workstream_arg, sf_upstream_arg
        called_slugs.append(slug)
        return None

    monkeypatch.setattr(TaskPlanningPhase, "_decompose_subfeature", _fake_decompose_subfeature)

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert called_slugs == ["accounts"]
    assert "dag:accounts" not in runner.artifacts.store
    persisted_manifest = task_planning_module.TaskPlanningSliceManifest.model_validate_json(
        runner.artifacts.store["dag-slices:accounts"]
    )
    assert persisted_manifest.complete is False


@pytest.mark.asyncio
async def test_task_planning_retries_only_missing_fragment_slices_after_in_place_normalization(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-normalized-retry", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": """
## Implementation Steps

### STEP-1: Bootstrap

REQ-1
Bootstrap the backend.

### STEP-20: Hostile repo defenses

D-GR-9
Protect checkout and rendering boundaries.
""".strip(),
            "prd": "Accounts prd",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": """
## Acceptance Criteria

- **AC-accounts-1** — Bootstrap criterion.
  - linked_requirement: `REQ-1`
  - verification_method: `integration`
  - pass_condition: bootstrap succeeds

- **AC-accounts-2** — Repo hardening criterion.
  - linked_requirement: `D-GR-9`
  - verification_method: `integration`
  - pass_condition: hardening is enforced
""".strip(),
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=sf_upstream["accounts"]["plan"],
        test_plan_text=sf_upstream["accounts"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                step_titles=["Bootstrap"],
                acceptance_criterion_ids=["AC-accounts-1"],
                strict_acceptance_criteria=True,
            ),
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-2",
                title="Hostile repo defenses",
                step_ids=["STEP-20"],
                acceptance_criterion_ids=["AC-accounts-1", "AC-accounts-2"],
                strict_acceptance_criteria=False,
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-2",
                status="failed",
                last_error="estimated context exceeds budget",
            ),
        ],
    )
    completed_fragment = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                step_ids=["STEP-1"],
                requirement_ids=["REQ-1"],
                verification_gates=["AC-accounts-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": completed_fragment.model_dump_json(indent=2),
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "plan:accounts": sf_upstream["accounts"]["plan"],
                "decisions": "Global decisions",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            assert task.actor.name == "dag-ws-WS-1-accounts-slice-2-all-workstream-peers"
            return ImplementationDAG(
                tasks=[
                    _valid_task(
                        task_id="T-accounts-2",
                        slug="accounts",
                        verification_gates=["AC-accounts-2"],
                        step_ids=["STEP-20"],
                    )
                ],
                execution_order=[["T-accounts-2"]],
                requirement_coverage={"REQ-accounts": ["T-accounts-2"]},
                complete=True,
            )

    runner = _Runner()
    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.calls == ["dag-ws-WS-1-accounts-slice-2-all-workstream-peers"]
    normalized_manifest = task_planning_module.TaskPlanningSliceManifest.model_validate_json(
        runner.artifacts.store["dag-slices:accounts"]
    )
    normalized_slice = next(slice_info for slice_info in normalized_manifest.slices if slice_info.slice_id == "slice-2")
    assert normalized_slice.acceptance_criterion_ids == []
    assert normalized_slice.owned_acceptance_criterion_ids == []
    assert normalized_slice.supporting_acceptance_criterion_ids == []
    assert normalized_slice.global_obligation_ac_ids == ["AC-accounts-2"]
    assert normalized_slice.strict_acceptance_criteria is False


@pytest.mark.asyncio
async def test_task_planning_resume_deletes_invalid_completed_fragment_and_replans(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-invalid-fragment", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-1",
        name="Accounts",
        subfeature_slugs=["accounts"],
        rationale="Accounts scope",
        depends_on=[],
    )
    sf_upstream = {
        "accounts": {
            "plan": "## Implementation Steps\n\n### STEP-1: Bootstrap\n\n**Instructions:**\n\nBootstrap\n",
            "prd": "Accounts prd",
            "design": "Accounts design",
            "system-design": "Accounts system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=sf_upstream["accounts"]["plan"],
        test_plan_text=sf_upstream["accounts"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-accounts-1"],
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:accounts:slice-1",
            ),
        ],
    )
    invalid_fragment = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="T-accounts-1",
                name="Accounts",
                description="Accounts task",
                subfeature_id="accounts",
                verification_gates=["AC-accounts-1"],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": invalid_fragment.model_dump_json(indent=2),
                "test-plan:accounts": sf_upstream["accounts"]["test-plan"],
                "plan:accounts": sf_upstream["accounts"]["plan"],
                "decisions": "Global decisions",
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            if not isinstance(task, Ask):
                raise AssertionError(f"unexpected task type: {type(task).__name__}")
            self.calls.append(task.actor.name)
            return ImplementationDAG(
                tasks=[_valid_task(task_id="T-accounts-1", slug="accounts", verification_gates=["AC-accounts-1"])],
                execution_order=[["T-accounts-1"]],
                requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
                complete=True,
            )

    runner = _Runner()
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-1", invalid_fragment.model_dump_json(indent=2))

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.artifacts.deleted == ["dag-fragment:accounts:slice-1"]
    assert runner.calls == ["dag-ws-WS-1-accounts-slice-1-all-workstream-peers"]
    persisted_fragment = json.loads(runner.artifacts.store["dag-fragment:accounts:slice-1"])
    assert persisted_fragment["tasks"][0]["step_ids"] == ["STEP-1"]


@pytest.mark.asyncio
async def test_task_planning_resume_rewrites_normalizable_fragment_without_replanning(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-normalize-fragment", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="bridge-protocol", name="Bridge Protocol", description="Bridge"),
        ],
        complete=True,
    )
    workstream = Workstream(
        id="WS-3",
        name="Bridge",
        subfeature_slugs=["bridge-protocol"],
        rationale="Bridge scope",
        depends_on=[],
    )
    sf_upstream = {
        "bridge-protocol": {
            "plan": "## Implementation Steps\n\n### STEP-1: Bootstrap\n\n**Instructions:**\n\nBootstrap\n",
            "prd": "Bridge prd",
            "design": "Bridge design",
            "system-design": "Bridge system design",
            "test-plan": "## Acceptance Criteria\n\n- AC-bridge-protocol-1\n",
        },
    }
    manifest = _slice_manifest_with_current_digests(
        slug="bridge-protocol",
        plan_text=sf_upstream["bridge-protocol"]["plan"],
        test_plan_text=sf_upstream["bridge-protocol"]["test-plan"],
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-bridge-protocol-1"],
            ),
        ],
        statuses=[
            task_planning_module.SlicePlanningStatus(
                slice_id="slice-1",
                status="completed",
                fragment_key="dag-fragment:bridge-protocol:slice-1",
            ),
        ],
    )
    fragment = ImplementationDAG(
        tasks=[
            _valid_task(task_id="T-bridge-1", slug="bridge-protocol", verification_gates=["AC-bridge-protocol-1"]),
            _valid_task(
                task_id="T-bridge-2",
                slug="bridge-protocol",
                verification_gates=["AC-bridge-protocol-1"],
                dependencies=["T-bridge-1"],
            ),
        ],
        execution_order=[["T-bridge-1", "T-bridge-2"]],
        requirement_coverage={"REQ-bridge-protocol": ["T-bridge-1", "T-bridge-2"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:bridge-protocol": manifest.model_dump_json(indent=2),
                "dag-fragment:bridge-protocol:slice-1": fragment.model_dump_json(indent=2),
                "test-plan:bridge-protocol": sf_upstream["bridge-protocol"]["test-plan"],
                "plan:bridge-protocol": sf_upstream["bridge-protocol"]["plan"],
                "decisions": "Global decisions",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[str] = []

        async def run(self, task, feature, phase_name):
            del task, feature, phase_name
            self.calls.append("unexpected")
            raise AssertionError("planner should not rerun for a normalizable fragment")

    runner = _Runner()

    failures = await TaskPlanningPhase()._decompose_workstream(
        runner,
        feature,
        decomposition,
        workstream,
        sf_upstream,
    )

    assert failures == []
    assert runner.calls == []
    persisted_fragment = json.loads(runner.artifacts.store["dag-fragment:bridge-protocol:slice-1"])
    assert persisted_fragment["execution_order"] == [["T-bridge-1"], ["T-bridge-2"]]
    persisted_dag = json.loads(runner.artifacts.store["dag:bridge-protocol"])
    assert persisted_dag["execution_order"] == [["T-bridge-1"], ["T-bridge-2"]]


@pytest.mark.asyncio
async def test_task_planning_reuses_matching_slice_manifest(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-manifest-reuse", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    subfeature = Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")
    plan_text = "## Implementation Steps\n\n### STEP-1: Bootstrap\n\nBootstrap\n\n- **AC refs.** AC-accounts-1\n"
    step_section_text = "### STEP-1: Bootstrap\n\nBootstrap\n\n- **AC refs.** AC-accounts-1"
    test_plan_text = "## Acceptance Criteria\n\n- AC-accounts-1\n"
    manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                step_titles=["Bootstrap"],
                acceptance_criterion_ids=["AC-accounts-1"],
                owned_acceptance_criterion_ids=["AC-accounts-1"],
                strict_acceptance_criteria=True,
                mandatory_source_chars=len(step_section_text),
                required_reference_sources=["plan", "test-plan"],
                slice_contract_digest=TaskPlanningPhase._slice_contract_digest(
                    step_ids=["STEP-1"],
                    requirement_ids=[],
                    journey_ids=[],
                    owned_acceptance_criterion_ids=["AC-accounts-1"],
                    supporting_acceptance_criterion_ids=[],
                    global_obligation_ac_ids=[],
                    required_reference_sources=["plan", "test-plan"],
                ),
            ),
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": manifest.model_dump_json(indent=2),
                "plan:accounts": plan_text,
                "test-plan:accounts": test_plan_text,
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})

    derived = await TaskPlanningPhase._derive_slice_manifest(runner, feature, subfeature)

    assert derived.model_dump(exclude={"contract_digest"}) == manifest.model_dump(exclude={"contract_digest"})
    assert derived.contract_digest


@pytest.mark.asyncio
async def test_task_planning_invalidates_slice_manifest_when_derivation_version_changes(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-version-change", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    subfeature = Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")
    plan_text = "## Implementation Steps\n\n### STEP-1: Bootstrap\n\nBootstrap\n\n- **AC refs.** AC-accounts-1\n"
    test_plan_text = "## Acceptance Criteria\n\n- AC-accounts-1\n"
    stale_manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-accounts-1"],
            ),
        ],
    ).model_copy(update={"derivation_version": task_planning_module._SLICE_MANIFEST_DERIVATION_VERSION - 1})
    stale_fragment = ImplementationDAG(
        tasks=[_valid_task(task_id="T-accounts-1", slug="accounts", verification_gates=["AC-accounts-1"])],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": stale_manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": stale_fragment.model_dump_json(indent=2),
                "dag:vscode-fork-shell": "preserve me",
                "plan:accounts": plan_text,
                "test-plan:accounts": test_plan_text,
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-1", stale_fragment.model_dump_json(indent=2))
    mirror.write_artifact(feature.id, "dag:vscode-fork-shell", "preserve me")

    derived = await TaskPlanningPhase._derive_slice_manifest(runner, feature, subfeature)

    assert runner.artifacts.deleted == ["dag-fragment:accounts:slice-1"]
    assert derived.derivation_version == task_planning_module._SLICE_MANIFEST_DERIVATION_VERSION
    assert "dag-fragment:accounts:slice-1" not in runner.artifacts.store
    assert runner.artifacts.store["dag:vscode-fork-shell"] == "preserve me"


@pytest.mark.asyncio
async def test_task_planning_invalidates_slice_manifest_when_plan_changes(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-plan-change", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    subfeature = Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")
    old_plan_text = "## Implementation Steps\n\n### STEP-1: Bootstrap\n\nBootstrap\n\n- **AC refs.** AC-accounts-1\n"
    new_plan_text = (
        "## Implementation Steps\n\n### STEP-1: Bootstrap\n\nBootstrap\n\n- **AC refs.** AC-accounts-1\n\n"
        "### STEP-2: Finalize\n\nFinalize\n\n- **AC refs.** AC-accounts-2\n"
    )
    test_plan_text = "## Acceptance Criteria\n\n- AC-accounts-1\n- AC-accounts-2\n"
    stale_manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=old_plan_text,
        test_plan_text=test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-accounts-1"],
            ),
        ],
    )
    stale_fragment = ImplementationDAG(
        tasks=[_valid_task(task_id="T-accounts-1", slug="accounts", verification_gates=["AC-accounts-1"])],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": stale_manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": stale_fragment.model_dump_json(indent=2),
                "plan:accounts": new_plan_text,
                "test-plan:accounts": test_plan_text,
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-1", stale_fragment.model_dump_json(indent=2))

    derived = await TaskPlanningPhase._derive_slice_manifest(runner, feature, subfeature)

    assert runner.artifacts.deleted == ["dag-fragment:accounts:slice-1"]
    assert derived.plan_digest != stale_manifest.plan_digest
    assert derived.test_plan_digest == stale_manifest.test_plan_digest
    assert [slice_info.step_ids for slice_info in derived.slices] == [["STEP-1", "STEP-2"]]
    assert "dag-fragment:accounts:slice-1" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_task_planning_invalidates_slice_manifest_when_test_plan_changes(tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-test-plan-change", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    subfeature = Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts")
    plan_text = "## Implementation Steps\n\n### STEP-1: Bootstrap\n\nBootstrap\n\n- **AC refs.** AC-accounts-1, AC-accounts-2\n"
    old_test_plan_text = "## Acceptance Criteria\n\n- AC-accounts-1\n"
    new_test_plan_text = "## Acceptance Criteria\n\n- AC-accounts-1\n- AC-accounts-2\n"
    stale_manifest = _slice_manifest_with_current_digests(
        slug="accounts",
        plan_text=plan_text,
        test_plan_text=old_test_plan_text,
        slices=[
            task_planning_module.TaskPlanningSlice(
                slice_id="slice-1",
                title="Bootstrap",
                step_ids=["STEP-1"],
                acceptance_criterion_ids=["AC-accounts-1"],
            ),
        ],
    )
    stale_fragment = ImplementationDAG(
        tasks=[_valid_task(task_id="T-accounts-1", slug="accounts", verification_gates=["AC-accounts-1"])],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag-slices:accounts": stale_manifest.model_dump_json(indent=2),
                "dag-fragment:accounts:slice-1": stale_fragment.model_dump_json(indent=2),
                "plan:accounts": plan_text,
                "test-plan:accounts": new_test_plan_text,
            }
            self.deleted: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deleted.append(key)
            self.store.pop(key, None)

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"artifact_mirror": mirror})
    mirror.write_artifact(feature.id, "dag-fragment:accounts:slice-1", stale_fragment.model_dump_json(indent=2))

    derived = await TaskPlanningPhase._derive_slice_manifest(runner, feature, subfeature)

    assert runner.artifacts.deleted == ["dag-fragment:accounts:slice-1"]
    assert derived.plan_digest == stale_manifest.plan_digest
    assert derived.test_plan_digest != stale_manifest.test_plan_digest
    assert derived.slices[0].acceptance_criterion_ids == ["AC-accounts-1", "AC-accounts-2"]
    assert derived.slices[0].owned_acceptance_criterion_ids == ["AC-accounts-1", "AC-accounts-2"]
    assert derived.slices[0].global_obligation_ac_ids == []
    assert "dag-fragment:accounts:slice-1" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_implementation_prompt_context_materializes_without_artifact_mirror():
    feature = SimpleNamespace(id="feat-non-mirror-context", metadata={})

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    package = await implementation_module._build_prompt_context_package(
        runner,
        feature,
        title="Verification Context",
        file_stem="verify",
        intro_lines=["Use these files."],
        sections=[
            ("handover", "Implementation Handover", "handover details"),
            ("test-plan", "Test Plan", "test plan details"),
        ],
    )

    assert package is not None
    assert Path(package.index_path).exists()
    assert Path(package.manifest_path).exists()
    assert "handover details" in Path(package.item_paths["handover"]).read_text(encoding="utf-8")
    prompt = implementation_module._context_package_prompt(package)
    assert "Read the context index first" in prompt


@pytest.mark.asyncio
async def test_workstream_planner_prompt_uses_file_backed_context(tmp_path):
    feature = SimpleNamespace(id="feat-workstream-context", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "plan": "Global technical plan",
                "decisions": "Global decisions",
                "prd-summary:accounts": "Accounts summary",
                "prd-summary:billing": "Billing summary",
                "prd-summary:reports": "Reports summary",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name):
            del feature, phase_name
            self.prompts.append(task.prompt)
            return WorkstreamDecomposition(
                workstreams=[
                    Workstream(
                        id="WS-1",
                        name="Accounts",
                        subfeature_slugs=["accounts"],
                        rationale="Accounts scope",
                    )
                ],
                execution_order=[["WS-1"]],
                complete=True,
            )

    runner = _Runner()
    await TaskPlanningPhase()._get_or_create_workstreams(runner, feature, decomposition)

    assert len(runner.prompts) == 1
    prompt = runner.prompts[0]
    assert "Read the context index first:" in prompt
    assert "## Technical Plan" not in prompt
    manifest_path = Path(re.search(r"`([^`]+context-manifest\.md)`", prompt).group(1))
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "Broad / Global Decisions" in manifest_text
    assert "Subfeature Planning Digests" in manifest_text
    assert "Subfeature Decomposition" in manifest_text


@pytest.mark.asyncio
async def test_task_planning_phase_preserves_round_parallelism(monkeypatch):
    feature = SimpleNamespace(id="feat-task-plan-rounds", metadata={})
    state = BuildState(metadata={}, decomposition=_decomposition().model_dump_json(indent=2))
    decomposition = _decomposition()
    ws_decomp = WorkstreamDecomposition(
        workstreams=[
            Workstream(id="WS-1", name="Accounts", subfeature_slugs=["accounts"], rationale="Accounts"),
            Workstream(id="WS-2", name="Billing", subfeature_slugs=["billing"], rationale="Billing"),
        ],
        execution_order=[["WS-1", "WS-2"]],
        complete=True,
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
    )

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_get_workstreams(*args, **kwargs):
        return ws_decomp

    async def _fake_load_upstream(*args, **kwargs):
        return {}

    active = 0
    max_active = 0

    async def _fake_decompose(*args, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return []

    async def _fake_review(*args, **kwargs):
        return IntegrationReview(needs_revision=False, complete=True)

    async def _fake_compile(*args, **kwargs):
        return "compiled dag"

    async def _fake_gate(*args, **kwargs):
        return "approved dag"

    monkeypatch.setattr(TaskPlanningPhase, "_load_decomposition", _fake_load_decomposition)
    monkeypatch.setattr(TaskPlanningPhase, "_get_or_create_workstreams", _fake_get_workstreams)
    monkeypatch.setattr(TaskPlanningPhase, "_load_sf_upstream", _fake_load_upstream)
    monkeypatch.setattr(TaskPlanningPhase, "_decompose_workstream", _fake_decompose)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.integration_review",
        _fake_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.compile_artifacts",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.interview_gate_review",
        _fake_gate,
    )

    result = await TaskPlanningPhase().execute(runner, feature, state)

    assert max_active == 2
    assert result.dag == "approved dag"


@pytest.mark.asyncio
async def test_task_planning_clears_stale_blocked_artifact_before_successful_retry(monkeypatch, tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-retry-cleanup", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    ws_decomp = WorkstreamDecomposition(
        workstreams=[
            Workstream(id="WS-1", name="Accounts", subfeature_slugs=["accounts"], rationale="Accounts"),
        ],
        execution_order=[["WS-1"]],
        complete=True,
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="task-planning-blocked",
        text="old blocked report",
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="dag:strategy",
        text=ws_decomp.model_dump_json(indent=2),
    )
    state = BuildState(metadata={}, decomposition=decomposition.model_dump_json(indent=2))

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "task-planning-blocked": "old blocked report",
                "dag:strategy": ws_decomp.model_dump_json(indent=2),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}

        async def run(self, task, feature, phase_name=None):
            del task, feature, phase_name
            raise AssertionError("unexpected runner.run call")

    runner = _Runner()

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_load_upstream(*args, **kwargs):
        return {}

    async def _fake_decompose(*args, **kwargs):
        assert "task-planning-blocked" not in runner.artifacts.store
        return []

    async def _fake_review(*args, **kwargs):
        return IntegrationReview(needs_revision=False, complete=True)

    async def _fake_compile(*args, **kwargs):
        return "compiled dag"

    async def _fake_gate(*args, **kwargs):
        return "approved dag"

    monkeypatch.setattr(TaskPlanningPhase, "_load_decomposition", _fake_load_decomposition)
    monkeypatch.setattr(TaskPlanningPhase, "_load_sf_upstream", _fake_load_upstream)
    monkeypatch.setattr(TaskPlanningPhase, "_decompose_workstream", _fake_decompose)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.integration_review",
        _fake_review,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.compile_artifacts",
        _fake_compile,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.interview_gate_review",
        _fake_gate,
    )

    result = await TaskPlanningPhase().execute(runner, feature, state)

    assert result.dag == "approved dag"
    assert "task-planning-blocked" not in runner.artifacts.store
    assert not (
        Path(mirror.feature_dir(feature.id)) / "task-planning-blocked.md"
    ).exists()


@pytest.mark.asyncio
async def test_task_planning_phase_blocks_and_skips_downstream_on_failed_workstream(monkeypatch, tmp_path):
    feature = SimpleNamespace(id="feat-task-plan-blocked", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    state = BuildState(metadata={}, decomposition=_decomposition().model_dump_json(indent=2))
    decomposition = _decomposition()
    ws_decomp = WorkstreamDecomposition(
        workstreams=[
            Workstream(id="WS-1", name="Accounts", subfeature_slugs=["accounts"], rationale="Accounts"),
        ],
        execution_order=[["WS-1"]],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.notifications: list[str] = []

        async def run(self, task, feature, phase_name=None):
            del feature, phase_name
            message = getattr(task, "message", None)
            if message is not None:
                self.notifications.append(message)
                return None
            raise AssertionError(f"unexpected task type: {type(task).__name__}")

    runner = _Runner()

    async def _fake_load_decomposition(*args, **kwargs):
        return decomposition

    async def _fake_get_workstreams(*args, **kwargs):
        return ws_decomp

    async def _fake_load_upstream(*args, **kwargs):
        return {}

    async def _fake_decompose(*args, **kwargs):
        return [
            task_planning_module.TaskPlanningFailure(
                workstream_id="WS-1",
                slug="accounts",
                reason="prompt too long",
            )
        ]

    async def _boom(*args, **kwargs):
        raise AssertionError("downstream DAG review/compile should be skipped when task planning blocks")

    monkeypatch.setattr(TaskPlanningPhase, "_load_decomposition", _fake_load_decomposition)
    monkeypatch.setattr(TaskPlanningPhase, "_get_or_create_workstreams", _fake_get_workstreams)
    monkeypatch.setattr(TaskPlanningPhase, "_load_sf_upstream", _fake_load_upstream)
    monkeypatch.setattr(TaskPlanningPhase, "_decompose_workstream", _fake_decompose)
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.integration_review",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.compile_artifacts",
        _boom,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.task_planning.interview_gate_review",
        _boom,
    )

    with pytest.raises(RuntimeError, match="Task planning blocked"):
        await TaskPlanningPhase().execute(runner, feature, state)

    assert "task-planning-blocked" in runner.artifacts.store
    assert runner.notifications
    assert "## Task Planning Blocked" in runner.notifications[0]
    assert "WS-1/accounts: prompt too long" in runner.notifications[0]


@pytest.mark.asyncio
async def test_write_revision_decision_context_preserves_full_prior_decisions_in_companion_file(tmp_path):
    feature = SimpleNamespace(id="feat-full-prior-decisions", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    full_prior_decisions = "Decision line\n" * 2000 + "FINAL-TAIL-MARKER\n"

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    context_path = await _write_revision_decision_context(
        runner,
        feature,
        artifact_prefix="plan",
        sf_slug="accounts",
        revision_plan=RevisionPlan(),
        prior_decisions=full_prior_decisions,
        batch_entries=[(0, RevisionRequest(description="Revise", reasoning="Because"))],
        minimal=False,
    )

    context_text = Path(context_path).read_text(encoding="utf-8")
    assert "Fallback Prior Decisions" in context_text
    assert "FINAL-TAIL-MARKER" not in context_text
    match = re.search(r"`([^`]+revision-prior-decisions-[^`]+\.md)`", context_text)
    assert match is not None
    prior_path = Path(match.group(1))
    assert prior_path.read_text(encoding="utf-8") == full_prior_decisions
    assert "FINAL-TAIL-MARKER" in prior_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_plan_review_reviewer_prompts_use_file_backed_context(monkeypatch, tmp_path):
    feature = SimpleNamespace(id="feat-plan-review-context", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = _decomposition()
    state = BuildState(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        plan="compiled plan",
        system_design="compiled system design",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "prd:accounts": "Accounts prd",
                "design:accounts": "Accounts design",
                "plan:accounts": "Accounts plan",
                "system-design:accounts": "Accounts system design",
                "test-plan:accounts": "## Acceptance Criteria\n\n- AC-accounts-1\n",
                "decisions:accounts": "Accounts decisions",
                "prd:billing": "Billing prd",
                "design:billing": "Billing design",
                "plan:billing": "Billing plan",
                "system-design:billing": "Billing system design",
                "test-plan:billing": "## Acceptance Criteria\n\n- AC-billing-1\n",
                "decisions:billing": "Billing decisions",
                "decisions": "Canonical decisions",
                "prd-summary:billing": "Billing summary",
                "design-summary:billing": "Billing design summary",
                "plan-summary:billing": "Billing plan summary",
                "prd-summary:accounts": "Accounts summary",
                "design-summary:accounts": "Accounts design summary",
                "plan-summary:accounts": "Accounts plan summary",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.feature_store = None
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, Ask):
                self.prompts.append(task.prompt)
                return Verdict(approved=True, summary="ok")
            raise AssertionError(f"unexpected task type: {type(task).__name__}")

    async def _skip_gates(self, runner_arg, feature_arg, state_arg, decomposition_arg):
        del self, runner_arg, feature_arg, decomposition_arg
        state_arg.metadata["ran_gates"] = True
        return state_arg

    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.plan_review.PlanReviewPhase._run_gates",
        _skip_gates,
    )

    runner = _Runner()
    result = await PlanReviewPhase().execute(runner, feature, state)

    assert result.metadata["ran_gates"] is True
    assert runner.prompts
    assert all("Read the context index first:" in prompt for prompt in runner.prompts)
    assert all("## PRD — accounts" not in prompt for prompt in runner.prompts)
    manifest_path = Path(re.search(r"`([^`]+context-manifest\.md)`", runner.prompts[0]).group(1))
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "Accounts prd" not in runner.prompts[0]
    assert "PRD" in manifest_text
    assert "Referenced Decisions" in manifest_text


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
async def test_interview_gate_review_uses_file_backed_review_package(tmp_path):
    feature = SimpleNamespace(id="feat-gate-package", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return {"decisions": "D-1 current decision"}.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.put_calls.append((key, value))

    async def _run_case(*, services, compiled_text: str = "", use_cache: bool = False):
        prompts: list[str] = []

        async def _fake_run(task, feature, phase_name):
            del feature, phase_name
            prompts.append(task.initial_prompt)
            return SimpleNamespace(output=ReviewOutcome(approved=True, complete=True, revision_plan=RevisionPlan()))

        if use_cache:
            from iriai_build_v2.workflows._common import _helpers as helpers_module

            helpers_module._COMPILED_ARTIFACT_CACHE[(feature.id, "prd")] = compiled_text

        runner = SimpleNamespace(
            artifacts=_Artifacts(),
            services=services,
            run=_fake_run,
        )

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
            compiled_artifact_text="" if use_cache else compiled_text,
        )
        prompt = prompts[0]
        manifest_path = Path(
            re.search(r"Then read the context manifest: `([^`]+)`", prompt).group(1)
        )
        manifest_text = manifest_path.read_text(encoding="utf-8")
        compiled_path = Path(
            re.search(r"- \*\*Compiled prd\*\*: `([^`]+)`", manifest_text).group(1)
        )
        return result, prompt, compiled_path

    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd",
        text="compiled prd body",
    )
    mirror_result, mirror_prompt, mirror_compiled_path = await _run_case(
        services={"artifact_mirror": mirror, "hosting": DocHostingService(mirror)},
    )
    assert mirror_result == "compiled prd body"
    assert "Read the context index first:" in mirror_prompt
    assert "compiled prd body" not in mirror_prompt
    assert mirror_compiled_path.read_text(encoding="utf-8").strip() == "compiled prd body"

    no_mirror_result, no_mirror_prompt, no_mirror_compiled_path = await _run_case(
        services={},
        compiled_text="compiled prd body",
        use_cache=True,
    )
    assert no_mirror_result == "compiled prd body"
    assert "Read the context index first:" in no_mirror_prompt
    assert "compiled prd body" not in no_mirror_prompt
    assert no_mirror_compiled_path.read_text(encoding="utf-8").strip() == "compiled prd body"


@pytest.mark.asyncio
async def test_build_context_package_prefers_db_artifact_over_stale_mirror(tmp_path):
    feature = SimpleNamespace(id="feat-context-package-db", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="stale mirrored prd",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {"prd:accounts": "authoritative db prd"}.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    package = await build_context_package(
        runner,
        feature,
        title="DB Preferred Context",
        file_stem="db-preferred-context",
        intro_lines=["Use DB-backed artifacts first."],
        items=[
            ContextPackageItem(
                key="prd",
                label="Accounts PRD",
                group="Target",
                artifact_key="prd:accounts",
            )
        ],
    )

    assert package is not None
    assert Path(package.item_paths["prd"]).read_text(encoding="utf-8").strip() == "authoritative db prd"


@pytest.mark.asyncio
async def test_build_context_package_replaces_read_only_existing_context_file(tmp_path):
    feature = SimpleNamespace(id="feat-context-package-perms", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )
    context_dir = mirror.feature_dir(feature.id) / ".iriai-context"
    context_dir.mkdir(parents=True)
    existing_path = context_dir / "permission-context-task-specs.md"
    existing_path.write_text("stale context\n", encoding="utf-8")
    existing_path.chmod(0o444)

    try:
        package = await build_context_package(
            runner,
            feature,
            title="Permission Context",
            file_stem="permission-context",
            intro_lines=["Use generated context."],
            items=[
                ContextPackageItem(
                    key="task-specs",
                    label="Task Specs",
                    group="Target",
                    content="fresh context",
                )
            ],
        )
    finally:
        existing_path.chmod(0o644)

    assert package is not None
    rewritten_path = Path(package.item_paths["task-specs"])
    assert rewritten_path.read_text(encoding="utf-8").strip() == "fresh context"
    assert rewritten_path.stat().st_mode & 0o222


@pytest.mark.asyncio
async def test_build_context_package_rejects_path_traversal_file_name(tmp_path):
    feature = SimpleNamespace(id="feat-context-package-safe-name", metadata={})
    mirror = _TestMirror(tmp_path / "features")

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    with pytest.raises(ValueError, match="plain filename"):
        await build_context_package(
            runner,
            feature,
            title="Safe Filename Context",
            file_stem="safe-filename-context",
            intro_lines=["Use generated context."],
            items=[
                ContextPackageItem(
                    key="escape",
                    label="Escape",
                    group="Target",
                    content="should not write",
                    file_name="../escape.md",
                )
            ],
        )

    assert not (mirror.feature_dir(feature.id) / "escape.md").exists()


@pytest.mark.asyncio
async def test_generate_summary_uses_file_backed_context_package(tmp_path):
    feature = SimpleNamespace(id="feat-summary-package", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="# Accounts\n\nREQ-1\nJ-1\nD-2\n",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:accounts": "# Accounts\n\nREQ-1\nJ-1\nD-2\n",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(id="D-2", statement="Account decision", source_phase="subfeature", subfeature_slug="accounts"),
                ),
            }.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del key, value, feature
            return None

    prompts: list[str] = []

    async def _fake_run(task, feature, phase_name=""):
        del feature, phase_name
        prompts.append(task.prompt)
        return "Summary body"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        run=_fake_run,
    )

    await generate_summary(runner, feature, "prd", "accounts")

    assert prompts
    assert "Read the context index first:" in prompts[0]
    assert "# Accounts" not in prompts[0]


@pytest.mark.asyncio
async def test_compile_artifacts_uses_hierarchical_compile_when_large(monkeypatch, tmp_path):
    feature = SimpleNamespace(id="feat-compile-hier", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
            Subfeature(id="SF-2", slug="billing", name="Billing", description="Billing"),
            Subfeature(id="SF-3", slug="reports", name="Reports", description="Reports"),
        ],
        edges=[],
        complete=True,
    )

    broad_text = "broad\n" * 30
    # Each per-SF source carries its own provenance marker + a CMP body header,
    # so the always-on completeness guard has real markers to track through the
    # map-reduce stages (and the final union must preserve all three).
    sf_source = {
        slug: (
            f"<!-- SF: {slug} -->\n"
            f"## Subfeature body ({slug})\n\n"
            f"#### Widget (CMP-1)\n\n"
            + ("section\n" * 20)
        )
        for slug in ("accounts", "billing", "reports")
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            values = {
                "plan:broad": broad_text,
                "decomposition": decomposition.model_dump_json(indent=2),
                "plan:accounts": sf_source["accounts"],
                "plan:billing": sf_source["billing"],
                "plan:reports": sf_source["reports"],
            }
            return values.get(key, "")

    calls: list[str] = []
    stage_sources: dict[str, str] = {}

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        calls.append(task.prompt)
        paths = re.findall(r"`([^`]+)`", task.prompt)
        assert paths
        stage = re.search(r"Stage: ([^\n]+)", task.prompt).group(1)
        source_text = Path(paths[0]).read_text(encoding="utf-8")
        stage_sources[stage] = source_text
        # Faithful fake compiler: echo every SF provenance marker and CMP body
        # header present in the source, so the always-on guard sees a complete
        # union at every stage.
        markers = re.findall(r"<!--\s*SF:\s*([A-Za-z0-9][A-Za-z0-9_-]*)", source_text)
        n_cmp = len(re.findall(r"(?m)^#{2,4}\s+.*\bCMP-\d+", source_text))
        body = "compiled line\n" * 6
        for slug in dict.fromkeys(markers):  # preserve order, dedup
            body += f"<!-- SF: {slug} -->\n"
        for i in range(max(n_cmp, 1)):
            body += f"#### CompiledWidget (CMP-{i + 1})\n"
        Path(paths[-1]).write_text(body, encoding="utf-8")
        return None

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        run=_fake_run,
    )

    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD",
        500,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES",
        300,
    )

    compiled = await compile_artifacts(
        runner,
        feature,
        "plan-review",
        compiler_actor=lead_architect_reviewer,
        decomposition=decomposition,
        artifact_prefix="plan",
        broad_key="plan:broad",
        final_key="plan",
    )

    assert compiled.startswith(("compiled line\n" * 6).strip())
    # The always-on completeness guard passed: all three subfeature markers
    # survived into the final union.
    for slug in ("accounts", "billing", "reports"):
        assert f"<!-- SF: {slug} -->" in compiled
    assert len(calls) > 1
    assert any("Stage: regroup-1-1" in prompt for prompt in calls)
    assert "## Broad Artifact (plan:broad)" not in stage_sources["cluster-1"]
    assert "## Decomposition" not in stage_sources["cluster-1"]
    assert "## Broad Artifact (plan:broad)" not in stage_sources["regroup-1-1"]
    assert "## Decomposition" not in stage_sources["regroup-1-1"]
    assert "## Broad Artifact (plan:broad)" in stage_sources["final"]
    assert "## Decomposition" in stage_sources["final"]


def _build_hier_compile_runner(
    tmp_path,
    *,
    final_writer,
    slugs=("accounts", "billing", "reports"),
    cmp_per_sf=1,
):
    """Shared harness: 3-SF decomposition + a fake runner whose intermediate
    stages echo every SF marker / CMP body header, and whose FINAL stage is
    produced by ``final_writer(source_text) -> str``.

    Returns (feature, decomposition, runner).
    """
    feature = SimpleNamespace(id="feat-compile-guard", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id=f"SF-{i + 1}", slug=slug, name=slug.title(), description=slug)
            for i, slug in enumerate(slugs)
        ],
        edges=[],
        complete=True,
    )
    broad_text = "broad\n" * 30

    def _sf_body(slug):
        body = f"<!-- SF: {slug} -->\n## Subfeature body ({slug})\n\n"
        for i in range(cmp_per_sf):
            body += f"#### Widget-{slug}-{i} (CMP-{i + 1})\n\n" + ("section\n" * 20)
        return body

    sf_source = {slug: _sf_body(slug) for slug in slugs}

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            values = {
                "plan:broad": broad_text,
                "decomposition": decomposition.model_dump_json(indent=2),
            }
            for slug in slugs:
                values[f"plan:{slug}"] = sf_source[slug]
            return values.get(key, "")

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        paths = re.findall(r"`([^`]+)`", task.prompt)
        assert paths
        source_path = Path(paths[0])
        out_path = Path(paths[-1])
        source_text = source_path.read_text(encoding="utf-8")
        stage_match = re.search(r"Stage: ([^\n]+)", task.prompt)
        stage = stage_match.group(1) if stage_match else "per-bundle"
        if stage == "final":
            out_path.write_text(final_writer(source_text), encoding="utf-8")
            return None
        # Intermediate / per-bundle stages: faithful echo of all markers + CMP
        # body headers found in the source.
        markers = re.findall(
            r"<!--\s*SF:\s*([A-Za-z0-9][A-Za-z0-9_-]*)", source_text
        )
        n_cmp = len(re.findall(r"(?m)^#{2,4}\s+.*\bCMP-\d+", source_text))
        body = "compiled line\n" * 6
        for slug in dict.fromkeys(markers):
            body += f"<!-- SF: {slug} -->\n"
        for i in range(max(n_cmp, 1)):
            body += f"#### CompiledWidget (CMP-{i + 1})\n"
        out_path.write_text(body, encoding="utf-8")
        return None

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        run=_fake_run,
    )
    return feature, decomposition, runner


@pytest.mark.asyncio
async def test_compile_artifacts_guard_raises_when_bundle_dropped(monkeypatch, tmp_path):
    """Negative guard test: a final-merge output that drops a subfeature
    marker (and most component bodies) must HARD-RAISE naming the dropped SF."""

    def _truncating_final(source_text):
        # Simulate the truncation defect: emit ONLY the first subfeature's
        # marker + a single CMP body, dropping the rest.
        first = re.search(
            r"<!--\s*SF:\s*([A-Za-z0-9][A-Za-z0-9_-]*)", source_text
        ).group(1)
        return f"<!-- SF: {first} -->\n#### OnlyWidget (CMP-1)\nbody\n"

    feature, decomposition, runner = _build_hier_compile_runner(
        tmp_path, final_writer=_truncating_final
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )

    with pytest.raises(RuntimeError) as excinfo:
        await compile_artifacts(
            runner,
            feature,
            "plan-review",
            compiler_actor=lead_architect_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
            final_key="plan",
        )
    msg = str(excinfo.value)
    assert "completeness guard FAILED" in msg
    # Names the dropped subfeatures (billing + reports survive in sources but
    # not in the truncated output).
    assert "billing" in msg
    assert "reports" in msg


def test_compile_guard_expected_slugs_catches_chunk_stage_drop():
    """Chunk-stage guard: raw per-SF sources carry no `<!-- SF: -->` markers
    (the compiler emits them), so the guard keys the survival check on the
    KNOWN input slugs via ``expected_slugs``. A cluster compile that drops a
    whole subfeature must HARD-RAISE at its origin instead of poisoning every
    downstream stage's baseline."""
    with pytest.raises(RuntimeError) as ei:
        _assert_compile_complete(
            # rendered chunk source: `## Subfeature:` headers, NO `<!-- SF -->`
            sources_text="## Subfeature: S2 (s2)\n\nbody\n\n## Subfeature: S3a (s3a)\n\nbody\n",
            compiled_text="<!-- SF: s2 -->\n## merged\n",  # s3a marker dropped
            artifact_prefix="plan-chunk-2",
            stage_label="cluster-2",
            expected_slugs={"s2", "s3a"},
            real_slugs={"s2", "s3a"},
        )
    msg = str(ei.value)
    assert "completeness guard FAILED" in msg
    assert "s3a" in msg


def test_compile_guard_regroup_stage_catches_marker_drop():
    """Regroup-stage guard: the inputs (cluster outputs) DO carry `<!-- SF -->`
    markers, so a regroup merge that silently drops one subfeature (the exact
    S3a/S6 drop the final-only guard missed — its baseline was the already-
    stripped regroup output) must HARD-RAISE."""
    src = "<!-- SF: s1 -->\nbody\n<!-- SF: s2 -->\nbody\n<!-- SF: s3a -->\nbody\n"
    out = "<!-- SF: s1 -->\nbody\n<!-- SF: s2 -->\nbody\n"  # s3a dropped at regroup
    with pytest.raises(RuntimeError) as ei:
        _assert_compile_complete(
            sources_text=src,
            compiled_text=out,
            artifact_prefix="plan-regroup-1-1",
            stage_label="regroup-1-1",
            real_slugs={"s1", "s2", "s3a"},
        )
    assert "s3a" in str(ei.value)


def test_compile_guard_real_slugs_ignores_synthetic_markers():
    """real_slugs filtering: a dropped SYNTHETIC marker (e.g.
    `compilation-provenance`, or the `cluster-*`/`regroup-*` scaffolding) must
    NOT raise — only real subfeatures are required to survive. Prevents a false
    positive when the compiler consolidates synthetic provenance."""
    src = "<!-- SF: s1 -->\nbody\n<!-- SF: compilation-provenance -->\nx\n"
    out = "<!-- SF: s1 -->\nbody\n"  # synthetic marker gone, real one kept
    # Must NOT raise (no exception expected).
    _assert_compile_complete(
        sources_text=src,
        compiled_text=out,
        artifact_prefix="plan-regroup-1-1",
        stage_label="regroup-1-1",
        real_slugs={"s1"},
    )


@pytest.mark.asyncio
async def test_compile_artifacts_guard_passes_on_complete_renumbered_union(
    monkeypatch, tmp_path
):
    """Positive guard test: a complete union with GLOBALLY-RENUMBERED IDs
    (input CMP-1.. → output CMP-80..126) passes — the guard never compares
    literal IDs, only marker survival + body count-floor."""

    def _complete_renumbered_final(source_text):
        markers = list(
            dict.fromkeys(
                re.findall(
                    r"<!--\s*SF:\s*([A-Za-z0-9][A-Za-z0-9_-]*)", source_text
                )
            )
        )
        n_cmp = len(re.findall(r"(?m)^#{2,4}\s+.*\bCMP-\d+", source_text))
        body = "# Compiled\n\n"
        for slug in markers:
            body += f"<!-- SF: {slug} -->\n"
        # Renumber into a high, disjoint global range (CMP-80..) to prove the
        # guard is renumber-safe; emit at least as many bodies as the sources.
        for i in range(n_cmp):
            body += f"#### CompiledWidget (CMP-{80 + i})\n"
        return body

    feature, decomposition, runner = _build_hier_compile_runner(
        tmp_path, final_writer=_complete_renumbered_final, cmp_per_sf=3
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )

    compiled = await compile_artifacts(
        runner,
        feature,
        "plan-review",
        compiler_actor=lead_architect_reviewer,
        decomposition=decomposition,
        artifact_prefix="plan",
        broad_key="plan:broad",
        final_key="plan",
    )
    for slug in ("accounts", "billing", "reports"):
        assert f"<!-- SF: {slug} -->" in compiled
    # Renumbered output ids present, original low ids absent.
    assert "CMP-80" in compiled
    assert "(CMP-1)" not in compiled


@pytest.mark.asyncio
async def test_compile_artifacts_deterministic_final_merge(monkeypatch, tmp_path):
    """Deterministic-merge test: with deterministic_final_merge=True the final
    stage is assembled per-bundle WITHOUT an LLM 'final' call; the output
    contains every bundle's bodies (all expected global CMP ids) and does NOT
    mis-offset a cross-bundle reference."""

    final_stage_calls: list[str] = []
    per_bundle_calls: list[str] = []

    feature = SimpleNamespace(id="feat-det-merge", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    slugs = ("accounts", "billing", "reports")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id=f"SF-{i + 1}", slug=slug, name=slug.title(), description=slug)
            for i, slug in enumerate(slugs)
        ],
        edges=[],
        complete=True,
    )
    broad_text = "broad\n" * 30

    def _sf_body(slug, *, cross_ref=False):
        body = f"<!-- SF: {slug} -->\n## Subfeature body ({slug})\n\n"
        # Two owned components per SF, local CMP-1 / CMP-2. Bodies are sized so
        # the hierarchical compile retains >=2 bundles at the final stage,
        # exercising a genuine multi-bundle deterministic union.
        body += f"#### Owned-{slug}-A (CMP-1)\n\n" + ("section text line\n" * 120)
        body += f"#### Owned-{slug}-B (CMP-2)\n\n" + ("section text line\n" * 120)
        if cross_ref:
            # A cross-bundle reference written with an owning-SF prefix; the
            # per-bundle driver must NOT offset this.
            body += "This composes S2 CMP-17 (owned elsewhere).\n"
        return body

    sf_source = {
        "accounts": _sf_body("accounts"),
        "billing": _sf_body("billing", cross_ref=True),
        "reports": _sf_body("reports"),
    }

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            values = {
                "plan:broad": broad_text,
                "decomposition": decomposition.model_dump_json(indent=2),
            }
            for slug in slugs:
                values[f"plan:{slug}"] = sf_source[slug]
            return values.get(key, "")

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        paths = re.findall(r"`([^`]+)`", task.prompt)
        assert paths
        source_text = Path(paths[0]).read_text(encoding="utf-8")
        out_path = Path(paths[-1])
        stage_match = re.search(r"Stage: ([^\n]+)", task.prompt)
        if stage_match and stage_match.group(1) == "final":
            # MUST NOT be reached when deterministic_final_merge=True.
            final_stage_calls.append(task.prompt)
            out_path.write_text("SHOULD NOT HAPPEN\n", encoding="utf-8")
            return None
        # Per-bundle or intermediate stage. Emit markers + CMP bodies. When the
        # prompt carries a global offset (per-bundle driver), apply it to OWNED
        # CMP ids but leave 'Sx CMP-n' cross-refs untouched.
        offset_match = re.search(r"GLOBAL OFFSET of \+(\d+)", task.prompt)
        markers = list(
            dict.fromkeys(
                re.findall(
                    r"<!--\s*SF:\s*([A-Za-z0-9][A-Za-z0-9_-]*)", source_text
                )
            )
        )
        body = "compiled line\n" * 6
        for slug in markers:
            body += f"<!-- SF: {slug} -->\n"
        if offset_match is not None:
            per_bundle_calls.append(task.prompt)
            offset = int(offset_match.group(1))
            # Owned components: re-emit each source's local owned CMP ids,
            # offset to global. Find owned (non 'Sx CMP-') CMP headers.
            owned = re.findall(r"(?m)^#{2,4}\s+.*\bCMP-(\d+)", source_text)
            for local in owned:
                body += f"#### Global (CMP-{int(local) + offset})\n"
            # Preserve any cross-bundle 'Sx CMP-n' refs verbatim.
            for cross in re.findall(r"\bS\d+ CMP-\d+", source_text):
                body += f"ref {cross}\n"
        else:
            # Intermediate (cluster/regroup) merge: echo CMP body headers AND
            # carry forward any 'Sx CMP-n' cross-bundle references verbatim
            # (a faithful intermediate merge preserves them), plus pad the body
            # so the final stage retains >=2 bundles.
            n_cmp = len(re.findall(r"(?m)^#{2,4}\s+.*\bCMP-\d+", source_text))
            for i in range(max(n_cmp, 1)):
                body += f"#### CompiledWidget (CMP-{i + 1})\n\n" + (
                    "intermediate body line\n" * 120
                )
            for cross in re.findall(r"\bS\d+ CMP-\d+", source_text):
                body += f"composes {cross}\n"
        out_path.write_text(body, encoding="utf-8")
        return None

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        run=_fake_run,
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )

    compiled = await compile_artifacts(
        runner,
        feature,
        "plan-review",
        compiler_actor=lead_architect_reviewer,
        decomposition=decomposition,
        artifact_prefix="plan",
        broad_key="plan:broad",
        final_key="plan",
        deterministic_final_merge=True,
    )

    # The LLM 'final' single-shot merge was NEVER invoked.
    assert final_stage_calls == []
    # The per-bundle driver ran — a genuine multi-bundle union (>=2 bundles),
    # one bounded LLM call per bundle.
    assert len(per_bundle_calls) >= 2
    assert "<!-- ===== Part 2 of " in compiled
    # Every subfeature survived.
    for slug in slugs:
        assert f"<!-- SF: {slug} -->" in compiled
    # The cross-bundle reference was preserved verbatim (NOT offset).
    assert "S2 CMP-17" in compiled
    # Global renumbering applied: a later bundle's owned ids were offset past
    # the first bundle's local range (the second bundle's CMP-1/2 became
    # CMP-(1+offset)/CMP-(2+offset) with offset>0), so a global id beyond the
    # first bundle's max-local id appears.
    global_ids = sorted(
        int(m) for m in re.findall(r"^#### Global \(CMP-(\d+)\)", compiled, re.M)
    )
    assert global_ids, "expected globally-renumbered owned component ids"
    assert max(global_ids) > 2, "second bundle's ids should be offset past CMP-2"
    # Guard passed (no raise) — output reaches here.
    assert compiled


# ──────────────────────────────────────────────────────────────────────
# Incremental / resumable compile (flag-gated) — T1, T2, T6, T7, T8
# ──────────────────────────────────────────────────────────────────────


def _build_incremental_compile_runner(tmp_path, *, slugs=None):
    """Deterministic-merge compile harness with a PUT-capable artifact store.

    Mirrors ``_build_hier_compile_runner`` (faithful per-stage echo of all SF
    markers + CMP body headers, applying any GLOBAL OFFSET to OWNED CMP ids
    and preserving ``Sx CMP-n`` cross-refs), but the runner's ``artifacts``
    object supports both ``get`` and ``put`` so ``compile-piece:*`` markers
    persist.  Every LLM compile call is recorded by ``stage_label`` so reuse
    vs recompile is observable by counting calls.

    Returns (feature, decomposition, runner, calls).  ``calls`` is a list of
    stage labels; intermediate cluster calls look like ``cluster-{idx}`` and
    per-bundle calls like ``per-bundle`` (the bundle prompt carries no Stage:
    line).  The SF bodies are sized so each SF lands in its own cluster /
    bundle under the test thresholds (one piece per SF).
    """
    if slugs is None:
        slugs = ("accounts", "billing", "reports", "payments")
    feature = SimpleNamespace(id="feat-incremental", name="Incremental", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id=f"SF-{i + 1}", slug=slug, name=slug.title(), description=slug)
            for i, slug in enumerate(slugs)
        ],
        edges=[],
        complete=True,
    )
    broad_text = "broad\n" * 30

    def _sf_body(slug):
        body = f"<!-- SF: {slug} -->\n## Subfeature body ({slug})\n\n"
        body += f"#### Owned-{slug}-A (CMP-1)\n\n" + ("section text line\n" * 120)
        body += f"#### Owned-{slug}-B (CMP-2)\n\n" + ("section text line\n" * 120)
        return body

    sf_source = {slug: _sf_body(slug) for slug in slugs}

    class _Artifacts:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            seeded = {
                "plan:broad": broad_text,
                "decomposition": decomposition.model_dump_json(indent=2),
            }
            for slug in slugs:
                seeded[f"plan:{slug}"] = sf_source[slug]
            if key in seeded:
                return seeded[key]
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    calls: list[str] = []

    async def _fake_run(task, feature, phase_name):
        del feature, phase_name
        paths = re.findall(r"`([^`]+)`", task.prompt)
        assert paths
        source_text = Path(paths[0]).read_text(encoding="utf-8")
        out_path = Path(paths[-1])
        stage_match = re.search(r"Stage: ([^\n]+)", task.prompt)
        stage = stage_match.group(1) if stage_match else "per-bundle"
        calls.append(stage)
        offset_match = re.search(r"GLOBAL OFFSET of \+(\d+)", task.prompt)
        markers = list(
            dict.fromkeys(
                re.findall(r"<!--\s*SF:\s*([A-Za-z0-9][A-Za-z0-9_-]*)", source_text)
            )
        )
        body = "compiled line\n" * 6
        for slug in markers:
            body += f"<!-- SF: {slug} -->\n"
        if offset_match is not None:
            offset = int(offset_match.group(1))
            owned = re.findall(r"(?m)^#{2,4}\s+.*\bCMP-(\d+)", source_text)
            for local in owned:
                body += f"#### Global (CMP-{int(local) + offset})\n"
            for cross in re.findall(r"\bS\d+ CMP-\d+", source_text):
                body += f"ref {cross}\n"
        else:
            n_cmp = len(re.findall(r"(?m)^#{2,4}\s+.*\bCMP-\d+", source_text))
            for i in range(max(n_cmp, 1)):
                body += f"#### CompiledWidget (CMP-{i + 1})\n\n" + (
                    "intermediate body line\n" * 120
                )
            for cross in re.findall(r"\bS\d+ CMP-\d+", source_text):
                body += f"composes {cross}\n"
        out_path.write_text(body, encoding="utf-8")
        return None

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
        run=_fake_run,
    )
    return feature, decomposition, runner, calls


async def _run_incremental_compile(feature, decomposition, runner):
    return await compile_artifacts(
        runner,
        feature,
        "plan-review",
        compiler_actor=lead_architect_reviewer,
        decomposition=decomposition,
        artifact_prefix="plan",
        broad_key="plan:broad",
        final_key="plan",
        deterministic_final_merge=True,
        incremental_compile=True,
    )


@pytest.mark.asyncio
async def test_compile_reuses_completed_clusters_when_only_marker_present(
    monkeypatch, tmp_path
):
    """T1 — marker fast-path: a first incremental compile seeds all cluster /
    bundle outputs + ``compile-piece:*`` markers; a second identical compile
    makes ZERO LLM calls (every piece reused via the marker fast-path) and
    produces byte-identical output."""
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )
    feature, decomposition, runner, calls = _build_incremental_compile_runner(tmp_path)

    first = await _run_incremental_compile(feature, decomposition, runner)
    assert calls, "first compile must invoke the LLM for every piece"
    marker_keys = [k for k in runner.artifacts.store if k.startswith("compile-piece:")]
    assert marker_keys, "first compile must write compile-piece markers"

    calls.clear()
    second = await _run_incremental_compile(feature, decomposition, runner)

    # Marker fast-path: not a single LLM compile call on the second run.
    assert calls == [], f"expected zero LLM calls on reuse, got {calls}"
    # Byte-for-byte identical compiled artifact (deterministic concat re-ran).
    assert second == first


@pytest.mark.asyncio
async def test_compile_adopts_preexisting_outputs_with_no_markers(
    monkeypatch, tmp_path
):
    """T2 — content-validation fallback (the current crashed run): valid piece
    outputs exist on disk with NO markers, and the LAST final bundle is
    missing (crash point). A restart reuses all clusters + bundles 1..N-1 by
    content-validation (retroactively writing markers), re-emits ONLY the
    missing final bundle, and the final guard passes."""
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )
    feature, decomposition, runner, calls = _build_incremental_compile_runner(tmp_path)

    # Produce valid piece outputs on disk (the crashed run's leftovers).
    first = await _run_incremental_compile(feature, decomposition, runner)

    # Simulate a PRE-RESUMABILITY crash: wipe ALL markers (they did not exist
    # before the upgrade), and remove the last final-bundle output (the crash
    # point left it missing).
    runner.artifacts.store.clear()
    feature_dir = Path(runner.services["artifact_mirror"].feature_dir(feature.id))
    bundle_files = sorted(feature_dir.glob("compile-intermediate-plan-finalbundle-*.md"))
    assert len(bundle_files) >= 2
    last_bundle = bundle_files[-1]
    last_bundle.unlink()

    calls.clear()
    second = await _run_incremental_compile(feature, decomposition, runner)

    # Every piece EXCEPT the missing final bundle was adopted by content
    # validation — exactly one LLM call (the re-emit of the missing bundle).
    assert len(calls) == 1, f"expected exactly one recompile, got {calls}"
    assert calls[0] == "per-bundle"
    # Markers were retroactively backfilled for every adopted + recompiled piece.
    marker_keys = {k for k in runner.artifacts.store if k.startswith("compile-piece:")}
    n_clusters = len(list(feature_dir.glob("compile-intermediate-plan-chunk-*.md")))
    n_bundles = len(bundle_files)
    assert len(marker_keys) == n_clusters + n_bundles
    # The final whole-union guard passed and the output equals the from-scratch
    # compile (the re-emitted bundle is deterministic in the offset).
    assert second == first


@pytest.mark.asyncio
async def test_compile_crash_after_2_of_4_pieces_reuses_2_recompiles_2(
    monkeypatch, tmp_path
):
    """T6 — the operator's headline: with 4 clusters, 2 already complete
    (output + marker present) and 2 missing, a restart makes exactly 2 cluster
    LLM calls (the missing pieces) — the other 2 are reused — and the output
    matches a from-scratch compile."""
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )
    feature, decomposition, runner, calls = _build_incremental_compile_runner(tmp_path)

    # Baseline from-scratch compile (seeds all 4 cluster + 4 bundle pieces).
    first = await _run_incremental_compile(feature, decomposition, runner)
    feature_dir = Path(runner.services["artifact_mirror"].feature_dir(feature.id))
    cluster_files = sorted(feature_dir.glob("compile-intermediate-plan-chunk-*.md"))
    bundle_files = sorted(feature_dir.glob("compile-intermediate-plan-finalbundle-*.md"))
    assert len(cluster_files) == 4
    assert len(bundle_files) == 4

    # Simulate a crash after 2 of 4 clusters: keep cluster outputs+markers for
    # clusters 1-2, delete the outputs for clusters 3-4 AND drop ALL bundle
    # outputs (bundles depend on the recompiled clusters). Keep cluster 1-2
    # markers so they hit the fast-path; clusters 3-4 are clean misses.
    def _cluster_marker_key_for(idx):
        return next(
            k
            for k in runner.artifacts.store
            if k.startswith(f"compile-piece:plan-chunk-{idx}:")
        )

    keep_cluster_markers = {
        _cluster_marker_key_for(1): runner.artifacts.store[_cluster_marker_key_for(1)],
        _cluster_marker_key_for(2): runner.artifacts.store[_cluster_marker_key_for(2)],
    }
    runner.artifacts.store.clear()
    runner.artifacts.store.update(keep_cluster_markers)
    cluster_files[2].unlink()  # cluster-3 output gone
    cluster_files[3].unlink()  # cluster-4 output gone
    for b in bundle_files:
        b.unlink()  # all bundles must recompute (no markers, no outputs)

    calls.clear()
    second = await _run_incremental_compile(feature, decomposition, runner)

    cluster_calls = [c for c in calls if c.startswith("cluster-")]
    bundle_calls = [c for c in calls if c == "per-bundle"]
    # Exactly the 2 missing clusters recompiled (3 and 4); 1 and 2 reused.
    assert sorted(cluster_calls) == ["cluster-3", "cluster-4"], cluster_calls
    # All 4 bundles recomputed (their outputs were removed and no markers).
    assert len(bundle_calls) == 4, bundle_calls
    # Output identical to the from-scratch compile.
    assert second == first


def test_offset_precompute_equivalence():
    """T7 — the offset-precompute hoist is behavior-preserving: the hoisted
    ``bundle_offsets`` sequence equals the running ``+= _max_local_cmp``
    accumulation, including cross-bundle ``Sx CMP-n`` refs (which must NOT
    advance the owned-offset)."""
    from iriai_build_v2.workflows._common._helpers import _max_local_cmp

    # Representative bundles with owned CMP ids AND cross-bundle refs.
    final_sources = [
        ("b1", "<!-- SF: a -->\n#### X (CMP-1)\n#### Y (CMP-2)\n#### Z (CMP-3)\n"),
        # cross-ref S1 CMP-2 must be excluded from b2's owned max
        ("b2", "<!-- SF: b -->\n#### P (CMP-1)\nrefs S1 CMP-2\n#### Q (CMP-2)\n"),
        ("b3", "<!-- SF: c -->\nrefs S1 CMP-1 and S2 CMP-2\n#### R (CMP-1)\n"),
        ("b4", "<!-- SF: d -->\nno owned components, only ref S3 CMP-1\n"),
        ("b5", "<!-- SF: e -->\n#### S (CMP-1)\n#### T (CMP-2)\n"),
    ]

    # Pre-hoist reference: running accumulation advanced AFTER each bundle.
    running = 0
    pre_hoist_offsets = []
    for _name, text in final_sources:
        pre_hoist_offsets.append(running)
        running += _max_local_cmp(text)

    # Hoisted precompute (exactly the production §2.2 pass).
    hoisted = []
    acc = 0
    for _name, text in final_sources:
        hoisted.append(acc)
        acc += _max_local_cmp(text)

    assert hoisted == pre_hoist_offsets
    # Concrete values: b1 owns max 3 → b2 offset 3; b2 owns max 2 (S1 CMP-2
    # excluded) → b3 offset 5; b3 owns max 1 → b4 offset 6; b4 owns 0 → b5
    # offset 6.
    assert hoisted == [0, 3, 5, 6, 6]


def test_compile_piece_sentinel_ok_unit():
    """T5 (unit slice) — sentinel rejects obvious mid-write truncation and
    accepts a clean markdown boundary."""
    assert _compile_piece_sentinel_ok("#### Widget (CMP-1)\nbody text\n") is True
    assert _compile_piece_sentinel_ok("") is False
    assert _compile_piece_sentinel_ok("   \n  ") is False
    assert _compile_piece_sentinel_ok("text\n```\nopen fence never closed\n") is False
    assert _compile_piece_sentinel_ok("a paragraph\n##") is False
    assert _compile_piece_sentinel_ok("line ending in a continuation \\") is False
    # Balanced fence is fine.
    assert _compile_piece_sentinel_ok("```\ncode\n```\ndone\n") is True


@pytest.mark.asyncio
async def test_legacy_callers_unchanged_when_flag_off(monkeypatch, tmp_path):
    """T8 — default-off byte-for-byte: a deterministic-merge compile with
    ``incremental_compile`` left at its False default writes NO
    ``compile-piece:*`` markers, makes a full set of LLM calls every run (no
    reuse), and produces output identical to the incremental-on path."""
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_HIERARCHICAL_THRESHOLD", 500
    )
    monkeypatch.setattr(
        "iriai_build_v2.workflows._common._helpers.COMPILE_CLUSTER_TARGET_BYTES", 300
    )

    # Default-off run (legacy pm.py / design.py style: no incremental flag).
    feat_off, decomp_off, runner_off, calls_off = _build_incremental_compile_runner(
        tmp_path / "off"
    )
    out_off = await compile_artifacts(
        runner_off,
        feat_off,
        "plan-review",
        compiler_actor=lead_architect_reviewer,
        decomposition=decomp_off,
        artifact_prefix="plan",
        broad_key="plan:broad",
        final_key="plan",
        deterministic_final_merge=True,
        # incremental_compile defaults False — legacy behavior.
    )
    # No markers written, full LLM call set.
    assert not [k for k in runner_off.artifacts.store if k.startswith("compile-piece:")]
    n_calls_first = len(calls_off)
    assert n_calls_first > 0

    # Re-running default-off re-does ALL work (no reuse) — same call count.
    calls_off.clear()
    out_off2 = await compile_artifacts(
        runner_off,
        feat_off,
        "plan-review",
        compiler_actor=lead_architect_reviewer,
        decomposition=decomp_off,
        artifact_prefix="plan",
        broad_key="plan:broad",
        final_key="plan",
        deterministic_final_merge=True,
    )
    assert len(calls_off) == n_calls_first, "default-off must never reuse pieces"
    assert out_off2 == out_off

    # Incremental-on produces byte-identical output to the default-off path
    # (reuse changes only WHICH calls run, never the deterministic result).
    feat_on, decomp_on, runner_on, _calls_on = _build_incremental_compile_runner(
        tmp_path / "on"
    )
    out_on = await _run_incremental_compile(feat_on, decomp_on, runner_on)
    assert out_on == out_off


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


async def _drive_plan_review_cascade(
    monkeypatch,
    *,
    revision_requests: list[RevisionRequest],
    seed_artifacts: dict[str, str],
):
    """Drive PlanReviewPhase.execute through one revision wave and capture every
    targeted_revision dispatch.

    Cycle 1 is short-circuited via the "valid report exists" continue path
    (a pre-seeded ``plan-review-cycle-1`` report) so the phase jumps straight to
    the recovered discussion → revision wave → cascades. Cycle 2 runs reviews
    that all approve, ending the loop. Returns the list of
    ``(artifact_prefix, RevisionPlan)`` dispatched through targeted_revision.
    """
    from iriai_build_v2.workflows._common._helpers import TargetedRevisionResult

    phase = PlanReviewPhase()
    decomposition = _decomposition()
    state = BuildState(decomposition=decomposition.model_dump_json())
    feature = SimpleNamespace(id="feat-plan-review-cascade", metadata={})

    review_outcome = ReviewOutcome(
        approved=False,
        revision_plan=RevisionPlan(requests=revision_requests, new_decisions=[]),
        complete=True,
    )
    discussion_json = (
        "```json\n"
        + json.dumps({"output": review_outcome.model_dump(), "complete": True})
        + "\n```"
    )

    store: dict[str, str] = {
        "plan-review-cycle-1": (
            "# Plan Review Report\n\n**1 concerns, 0 gaps** across 2 subfeatures.\n"
        ),
        "plan-review-discussion-1": discussion_json,
    }
    store.update(seed_artifacts)

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=None,
    )

    async def _approve_reviews(task, feature, phase_name=""):
        # Cycle-2 reviews + Notify tasks: approve everything so the loop ends.
        del feature, phase_name
        if isinstance(task, Ask):
            return Verdict(approved=True, summary="ok")
        return None

    runner.run = _approve_reviews

    dispatches: list[tuple[str, RevisionPlan]] = []

    async def _fake_targeted_revision(_runner, _feature, _phase, **kwargs):
        dispatches.append((kwargs["artifact_prefix"], kwargs["revision_plan"]))
        return TargetedRevisionResult(artifact_prefix=kwargs["artifact_prefix"])

    async def _fake_compile(_runner, _feature, _phase, **kwargs):
        del _runner, _feature, _phase
        return f"compiled {kwargs['artifact_prefix']}"

    async def _noop_async(*args, **kwargs):
        return None

    async def _fake_build_context(*args, **kwargs):
        return "context"

    async def _fake_build_package(*args, **kwargs):
        # Return None so the phase falls back to inline context (no mirror).
        return None

    async def _fake_run_gates(self, runner_arg, feature_arg, state_arg, decomp_arg):
        del self, runner_arg, feature_arg, decomp_arg
        state_arg.metadata["ran_gates"] = True
        return state_arg

    mod = "iriai_build_v2.workflows.planning.phases.plan_review"
    monkeypatch.setattr(f"{mod}.targeted_revision", _fake_targeted_revision)
    monkeypatch.setattr(f"{mod}.compile_artifacts", _fake_compile)
    monkeypatch.setattr(f"{mod}.refresh_decision_ledger", _noop_async)
    monkeypatch.setattr(f"{mod}.generate_summary", _noop_async)
    monkeypatch.setattr(f"{mod}._build_sf_review_context", _fake_build_context)
    monkeypatch.setattr(f"{mod}._build_edge_review_context", _fake_build_context)
    monkeypatch.setattr(
        f"{mod}._build_sf_review_context_package", _fake_build_package
    )
    monkeypatch.setattr(
        f"{mod}._build_edge_review_context_package", _fake_build_package
    )
    monkeypatch.setattr(
        f"{mod}.PlanReviewPhase._run_gates", _fake_run_gates
    )

    await phase.execute(runner, feature, state)
    return dispatches


@pytest.mark.asyncio
async def test_plan_review_test_plan_cascade_forwards_requirement_signal(monkeypatch):
    # Fix A: the per-SF test-plan cascade must forward the AC-coverage signal
    # (affected_requirement_ids + severity + cross_subfeature) so the
    # test-planner gains ACs for new/changed REQs in the SAME cycle, breaking
    # the producer-consumer lag loop.
    req = RevisionRequest(
        description="Add new requirement REQ-accounts-9 for SSO login",
        reasoning="User decided SSO is in scope",
        affected_subfeatures=["accounts"],
        affected_requirement_ids=["REQ-accounts-9", "REQ-accounts-10"],
        severity="major",
        cross_subfeature=True,
    )
    dispatches = await _drive_plan_review_cascade(
        monkeypatch,
        revision_requests=[req],
        seed_artifacts={
            "test-plan:accounts": "## Acceptance Criteria\n\n- AC-accounts-1\n",
        },
    )

    test_plan_dispatches = [
        plan for prefix, plan in dispatches if prefix == "test-plan"
    ]
    assert len(test_plan_dispatches) == 1, dispatches
    forwarded = test_plan_dispatches[0].requests
    assert len(forwarded) == 1
    fwd = forwarded[0]
    # The REQ-ids survive the cascade rebuild (previously dropped).
    assert fwd.affected_requirement_ids == ["REQ-accounts-9", "REQ-accounts-10"]
    assert fwd.severity == "major"
    assert fwd.cross_subfeature is True
    assert fwd.affected_subfeatures == ["accounts"]


@pytest.mark.asyncio
async def test_plan_review_system_design_cascade_dispatches_when_flag_on(monkeypatch):
    # Fix B (flag ON): a peer system-design artifact gets a targeted revision
    # dispatch in the same cycle, forwarding the requirement signal.
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.plan_review.PLAN_REVIEW_SD_CASCADE",
        True,
    )
    req = RevisionRequest(
        description="Account service now needs an event bus topic",
        reasoning="New async decision",
        affected_subfeatures=["accounts"],
        affected_requirement_ids=["REQ-accounts-12"],
        severity="blocker",
        cross_subfeature=False,
    )
    dispatches = await _drive_plan_review_cascade(
        monkeypatch,
        revision_requests=[req],
        seed_artifacts={
            "system-design:accounts": "# System Design — accounts\n\nold\n",
        },
    )

    # The main _ARTIFACT_CONFIGS wave dispatches system-design once (the request
    # touches system-design:accounts); the cascade adds a SECOND, terminal
    # per-SF dispatch. Flag ON => 2 system-design dispatches.
    sd_dispatches = [
        plan for prefix, plan in dispatches if prefix == "system-design"
    ]
    assert len(sd_dispatches) == 2, dispatches
    # Every dispatched system-design request forwards the requirement signal.
    for plan in sd_dispatches:
        assert len(plan.requests) == 1
        fwd = plan.requests[0]
        assert fwd.affected_subfeatures == ["accounts"]
        assert fwd.affected_requirement_ids == ["REQ-accounts-12"]
        assert fwd.severity == "blocker"
        assert fwd.cross_subfeature is False


@pytest.mark.asyncio
async def test_plan_review_system_design_cascade_noop_when_flag_off(monkeypatch):
    # Fix B (flag OFF, default): NO extra system-design cascade dispatch — the
    # flag-off path must be a strict no-op (additivity / no regression).
    monkeypatch.setattr(
        "iriai_build_v2.workflows.planning.phases.plan_review.PLAN_REVIEW_SD_CASCADE",
        False,
    )
    req = RevisionRequest(
        description="Account service now needs an event bus topic",
        reasoning="New async decision",
        affected_subfeatures=["accounts"],
        affected_requirement_ids=["REQ-accounts-12"],
        severity="blocker",
        cross_subfeature=False,
    )
    dispatches = await _drive_plan_review_cascade(
        monkeypatch,
        revision_requests=[req],
        seed_artifacts={
            "system-design:accounts": "# System Design — accounts\n\nold\n",
        },
    )

    # The main wave dispatches the request against the system-design artifact
    # config too, but NO terminal system-design *cascade* dispatch should appear
    # beyond it. With the flag off there is exactly the main-wave dispatch and
    # no cascade-specific re-dispatch. Assert there is no extra dispatch beyond
    # what the main _ARTIFACT_CONFIGS wave produces for this single request.
    sd_dispatches = [
        plan for prefix, plan in dispatches if prefix == "system-design"
    ]
    # Main wave dispatches system-design once (from _ARTIFACT_CONFIGS); the
    # cascade adds a SECOND dispatch only when the flag is on. Flag off => 1.
    assert len(sd_dispatches) == 1, dispatches


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


def test_find_section_normalized_match_tolerates_whitespace_and_slash_spacing():
    """The exact `_clean_header` rule misses headers that differ only by
    internal whitespace or ` / ` vs `/` spacing; the normalized-match fallback
    must still locate the section instead of forcing a full-document rewrite."""
    text = (
        "## 1. Overview\nintro\n\n"
        "## 2. Services/Components\nservice body\n\n"
        "## 3. Data  Model\ndata body\n"
    )
    sections = _parse_markdown_sections(text)

    # ' / ' spacing in the target vs '/' in the real header.
    match = _find_section(sections, "2. Services / Components")
    assert match is not None
    assert match[0] == "## 2. Services/Components"

    # Internal double-space in the real header, single space in the target.
    match2 = _find_section(sections, "3. Data Model")
    assert match2 is not None
    assert match2[0] == "## 3. Data  Model"


def test_apply_patches_find_replace_matches_normalized_header():
    """find_replace whose target differs from the real header only by ` / ` vs
    `/` spacing must now APPLY (via the normalized fallback) instead of being
    skipped and falling back to a full rewrite."""
    text = (
        "## 1. Overview\nintro\n\n"
        "## 2. Services / Components\nold service text\n"
    )
    patches = [
        SimpleNamespace(
            target="2. Services/Components",
            operation="find_replace",
            content="new service text",
            find="old service text",
            reasoning="",
        ),
    ]

    revised = _apply_patches(text, patches)

    assert "new service text" in revised
    assert "old service text" not in revised


def test_apply_patches_find_replace_honors_occurrence_for_duplicate_headers():
    """A duplicate-header occurrence=2 find_replace must edit the SECOND
    matching section, leaving the first untouched — exercised through the
    normalized fallback path as well."""
    text = (
        "## Services / Components\nfirst body\n\n"
        "## Other\nmiddle\n\n"
        "## Services/Components\nsecond body\n"
    )
    patches = [
        SimpleNamespace(
            target="Services/Components",
            operation="find_replace",
            content="second body REVISED",
            find="second body",
            reasoning="",
            occurrence=2,
        ),
    ]

    revised = _apply_patches(text, patches)

    assert "first body" in revised
    assert "second body REVISED" in revised
    # The first occurrence's body is left intact.
    assert revised.count("first body") == 1


@pytest.mark.asyncio
async def test_targeted_revision_full_document_retry_rejects_dropped_marker(tmp_path):
    """DEFECT-2 guard: after repeated patch-guard rejection, the FULL_DOCUMENT
    retry output is now completeness-guarded. A regen that stays above the 50%
    size floor but silently DROPS a subfeature provenance marker / CMP body
    must be REJECTED (fail loud), not accepted and written."""
    feature = SimpleNamespace(
        id="feat-fulldoc-guard", metadata={"_db_phase": "plan-review"}
    )
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="A"),
        ],
        edges=[],
        complete=True,
    )
    mirror = _TestMirror(tmp_path / "features")
    # Existing doc carries the SF marker + two CMP component bodies.
    existing_text = (
        "<!-- SF: accounts -->\n"
        "## 1. Overview\nintro\n\n"
        "#### LoginForm (CMP-1)\nlogin body here with plenty of text to clear floors\n\n"
        "#### SignupForm (CMP-2)\nsignup body here with plenty of text to clear floors\n"
    )
    # Regen stays well above 50% size but drops the SF marker AND both CMP bodies.
    lossy_regen = (
        "## 1. Overview\nrewritten intro with plenty of additional padding text "
        "so the size floor is comfortably cleared and only the markers are lost\n"
    )
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text=existing_text,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"prd:accounts": existing_text}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(getattr(task, "prompt", ""))
            # First two batch attempts: a find_replace whose find text is NOT
            # present, so the patch guard rejects → forces FULL_DOCUMENT retry.
            if len(self.prompts) <= 2:
                return ArtifactPatchSet(
                    patches=[
                        {
                            "target": "Overview",
                            "operation": "find_replace",
                            "content": "x",
                            "find": "THIS TEXT IS NOT IN THE DOCUMENT",
                            "reasoning": "force rejection",
                        }
                    ],
                    summary="",
                )
            # FULL_DOCUMENT retry returns the lossy regen.
            return ArtifactPatchSet(
                patches=[
                    {
                        "target": "FULL_DOCUMENT",
                        "operation": "replace",
                        "content": lossy_regen,
                        "find": "",
                        "reasoning": "lossy full-document regen",
                    }
                ],
                summary="",
            )

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Revise accounts PRD.",
                    reasoning="Exercise full-document completeness guard.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_pm_gate_reviewer.role,
        output_type=PRD,
        artifact_prefix="prd",
        checkpoint_prefix="cycle-fulldoc",
    )

    # The lossy regen must be REJECTED, never written to the store.
    assert result.failed
    assert "completeness guard" in result.failed[0].reason
    assert runner.artifacts.store["prd:accounts"] == existing_text


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


@pytest.mark.asyncio
async def test_targeted_revision_syncs_stale_mirror_from_db_before_prompt(tmp_path):
    feature = SimpleNamespace(id="feat-auto-rev-stale-mirror", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    mirror = _TestMirror(tmp_path / "features")
    existing_path = _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="# Title\n\nStale mirror body\n",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "prd:accounts": "# Title\n\nFresh DB body\n",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.calls: list[Any] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.calls.append(task)
            return ArtifactPatchSet(patches=[], summary="")

    runner = _Runner()
    await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="No-op check.",
                    reasoning="Exercise stale mirror sync.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_pm_gate_reviewer.role,
        output_type=PRD,
        artifact_prefix="prd",
    )

    assert existing_path.read_text(encoding="utf-8") == "# Title\n\nFresh DB body\n"
    ask_prompts = [task.prompt for task in runner.calls if isinstance(task, Ask)]
    assert any(str(existing_path) in prompt for prompt in ask_prompts)


@pytest.mark.asyncio
async def test_targeted_revision_rejects_invalid_json_artifact_rewrite(tmp_path):
    feature = SimpleNamespace(id="feat-auto-rev-json-guard", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    mirror = _TestMirror(tmp_path / "features")
    existing_dag = ImplementationDAG(
        tasks=[
            _valid_task(
                task_id="T-accounts-1",
                slug="accounts",
                verification_gates=[],
            )
        ],
        execution_order=[["T-accounts-1"]],
        requirement_coverage={"REQ-accounts": ["T-accounts-1"]},
        complete=True,
    ).model_dump_json(indent=2)

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"dag:accounts": existing_dag}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}

        async def run(self, task, feature, phase_name=""):
            del task, feature, phase_name
            return ArtifactPatchSet(
                patches=[
                    {
                        "target": "FULL_DOCUMENT",
                        "operation": "replace",
                        "content": "# Decision Ledger\n\n_No decisions recorded yet._\n",
                        "find": "",
                        "reasoning": "bad rewrite",
                    }
                ],
                summary="",
            )

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "task-planning",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Revise DAG.",
                    reasoning="Exercise JSON guard.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_task_planner_reviewer.role,
        output_type=ImplementationDAG,
        artifact_prefix="dag",
    )

    assert result.failed
    assert "not valid ImplementationDAG JSON" in result.failed[0].reason
    assert runner.artifacts.store["dag:accounts"] == existing_dag


@pytest.mark.asyncio
async def test_targeted_revision_retries_json_artifact_section_patch_after_cache_clear(tmp_path):
    feature = SimpleNamespace(id="feat-auto-rev-json-targeted", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="accounts", name="Accounts", description="Accounts"),
        ],
        complete=True,
    )
    mirror = _TestMirror(tmp_path / "features")
    existing_sd = SystemDesign(
        title="Accounts",
        overview="Current service boundary.",
        complete=True,
    ).model_dump_json(indent=2)
    revised_sd = SystemDesign(
        title="Accounts",
        overview="Revised service boundary.",
        complete=True,
    ).model_dump_json(indent=2)

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"system-design:accounts": existing_sd}
            self.deletes: list[str] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def delete(self, key: str, *, feature):
            del feature
            self.deletes.append(key)
            self.store.pop(key, None)

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": mirror}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            if len(self.prompts) == 1:
                return ArtifactPatchSet(
                    patches=[
                        {
                            "target": "Services",
                            "operation": "find_replace",
                            "content": "new service",
                            "find": "old service",
                            "reasoning": "bad section patch for JSON",
                        }
                    ],
                    summary="",
                )
            return ArtifactPatchSet(
                patches=[
                    {
                        "target": "FULL_DOCUMENT",
                        "operation": "replace",
                        "content": revised_sd,
                        "find": "",
                        "reasoning": "valid full JSON replacement",
                    }
                ],
                summary="",
            )

    runner = _Runner()
    result = await targeted_revision(
        runner,
        feature,
        "plan-review",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Revise system-design service boundary.",
                    reasoning="Plan review requested a structured update.",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        decomposition=decomposition,
        base_role=lead_architect_gate_reviewer.role,
        output_type=SystemDesign,
        artifact_prefix="system-design",
        checkpoint_prefix="cycle-json",
    )

    assert result.ok is True
    assert runner.artifacts.store["system-design:accounts"].strip() == revised_sd
    assert len(runner.prompts) == 2
    assert "This artifact is JSON-backed" in runner.prompts[0]
    assert "Do NOT target markdown/HTML section names" in runner.prompts[0]
    assert any(
        key.startswith("patches:cycle-json:system-design:accounts:batch-0")
        for key in runner.artifacts.deletes
    )


@pytest.mark.asyncio
async def test_revision_decision_context_prefers_cited_records_over_full_ledger(tmp_path):
    feature = SimpleNamespace(id="feat-revision-decisions", metadata={})
    mirror = _TestMirror(tmp_path / "features")
    _write_mirror_artifact(
        mirror,
        feature_id=feature.id,
        artifact_key="prd:accounts",
        text="# Accounts\n\nUses D-7\n",
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            return {
                "prd:accounts": "# Accounts\n\nUses D-7\n",
                "decisions-summary:accounts": "Active summary mentions D-7",
                "decisions:accounts": _decision_ledger_text(
                    DecisionRecord(id="D-7", statement="Local decision", source_phase="subfeature", subfeature_slug="accounts"),
                    DecisionRecord(id="D-8", statement="Other decision", source_phase="subfeature", subfeature_slug="accounts"),
                ),
                "decisions": _decision_ledger_text(
                    DecisionRecord(id="D-7", statement="Local decision", source_phase="subfeature", subfeature_slug="accounts"),
                    DecisionRecord(id="D-8", statement="Other decision", source_phase="subfeature", subfeature_slug="accounts"),
                ),
            }.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror},
    )

    context_path = await _write_revision_decision_context(
        runner,
        feature,
        artifact_prefix="prd",
        sf_slug="accounts",
        revision_plan=RevisionPlan(
            requests=[
                RevisionRequest(
                    description="Revise D-7 handling",
                    reasoning="Keep D-7 consistent",
                    affected_subfeatures=["accounts"],
                )
            ]
        ),
        prior_decisions="",
        batch_entries=[(0, RevisionRequest(description="Revise D-7 handling", reasoning="Keep D-7 consistent", affected_subfeatures=["accounts"]))],
        minimal=False,
    )

    context_text = Path(context_path).read_text(encoding="utf-8")
    assert "Referenced Decision Records" in context_text
    assert "D-7: Local decision" in context_text
    assert "Fallback Decision Ledger" not in context_text


# ── Deterministic union system-design merge (skip 32K-blowing LLM) ────────────


def _sd_part(
    *,
    title: str,
    services: list[tuple[str, str]] | None = None,
    connections: list[tuple[str, str]] | None = None,
    endpoints: list[tuple[str, str, str]] | None = None,
    entities: list[tuple[str, str, str]] | None = None,
    relations: list[tuple[str, str, str]] | None = None,
    call_paths: list[str] | None = None,
    decisions: list[str] | None = None,
    risks: list[str] | None = None,
) -> SystemDesign:
    from iriai_build_v2.models.outputs import (
        APICallPath,
        APIEndpoint,
        Entity,
        EntityRelation,
        ServiceConnection,
        ServiceNode,
    )

    return SystemDesign(
        title=title,
        services=[
            ServiceNode(id=sid, name=sid, kind="service", description=desc)
            for sid, desc in (services or [])
        ],
        connections=[
            ServiceConnection(from_id=a, to_id=b, label=f"{a}->{b}")
            for a, b in (connections or [])
        ],
        api_endpoints=[
            APIEndpoint(method=m, path=p, service_id=s, description=p)
            for m, p, s in (endpoints or [])
        ],
        entities=[
            Entity(id=eid, name=name, service_id=svc)
            for eid, name, svc in (entities or [])
        ],
        entity_relations=[
            EntityRelation(from_entity=a, to_entity=b, kind=k)
            for a, b, k in (relations or [])
        ],
        call_paths=[
            APICallPath(id=cpid, name=cpid, description=cpid)
            for cpid in (call_paths or [])
        ],
        decisions=list(decisions or []),
        risks=list(risks or []),
    )


def test_merge_system_designs_dedups_by_key_without_content_loss():
    """merge_system_designs unions per-SF designs, dropping only exact-key dups."""
    from iriai_build_v2.services.system_design_html import merge_system_designs

    part_a = _sd_part(
        title="A",
        services=[("svc-shared", "shared from A"), ("svc-a", "only A")],
        connections=[("svc-a", "svc-shared")],
        endpoints=[("GET", "/a", "svc-a"), ("GET", "/shared", "svc-shared")],
        entities=[("E-shared", "Shared", "svc-shared"), ("E-a", "OnlyA", "svc-a")],
        relations=[("E-a", "E-shared", "one-to-many")],
        call_paths=["CP-shared", "CP-a"],
        decisions=["D shared", "D only-a"],
        risks=["R shared"],
    )
    part_b = _sd_part(
        title="B",
        # svc-shared duplicates A's id (dropped); description from A wins (first).
        services=[("svc-shared", "shared from B"), ("svc-b", "only B")],
        connections=[("svc-a", "svc-shared"), ("svc-b", "svc-shared")],
        # (GET,/shared,svc-shared) duplicates A's endpoint (dropped).
        endpoints=[("GET", "/shared", "svc-shared"), ("POST", "/b", "svc-b")],
        entities=[("E-shared", "Shared", "svc-shared"), ("E-b", "OnlyB", "svc-b")],
        relations=[("E-a", "E-shared", "one-to-many"), ("E-b", "E-shared", "one-to-one")],
        call_paths=["CP-shared", "CP-b"],
        decisions=["D shared", "D only-b"],
        risks=["R shared", "R only-b"],
    )

    merged = merge_system_designs([part_a, part_b], title="Union", overview="ov")

    assert merged.title == "Union"
    assert merged.overview == "ov"
    assert merged.complete is True

    # services: dedup by id, first occurrence (A's description) wins.
    assert [s.id for s in merged.services] == ["svc-shared", "svc-a", "svc-b"]
    assert merged.services[0].description == "shared from A"

    # connections: dedup by (from_id, to_id).
    assert [(c.from_id, c.to_id) for c in merged.connections] == [
        ("svc-a", "svc-shared"),
        ("svc-b", "svc-shared"),
    ]

    # api_endpoints: dedup by (method, path, service_id).
    assert [(e.method, e.path, e.service_id) for e in merged.api_endpoints] == [
        ("GET", "/a", "svc-a"),
        ("GET", "/shared", "svc-shared"),
        ("POST", "/b", "svc-b"),
    ]

    # entities: dedup by id.
    assert [e.id for e in merged.entities] == ["E-shared", "E-a", "E-b"]

    # entity_relations: dedup by (from, to, kind) — the two E-x->E-shared with
    # different kinds are BOTH kept (not collapsed).
    assert [(r.from_entity, r.to_entity, r.kind) for r in merged.entity_relations] == [
        ("E-a", "E-shared", "one-to-many"),
        ("E-b", "E-shared", "one-to-one"),
    ]

    # call_paths: dedup by id.
    assert [cp.id for cp in merged.call_paths] == ["CP-shared", "CP-a", "CP-b"]

    # decisions / risks: concat + dedup preserving order.
    assert merged.decisions == ["D shared", "D only-a", "D only-b"]
    assert merged.risks == ["R shared", "R only-b"]


def test_merge_system_designs_roundtrips_through_json_fast_path():
    """The merged model serializes to JSON the existing fast-path can re-parse."""
    from iriai_build_v2.services.system_design_html import (
        merge_system_designs,
        render_system_design_html,
    )

    merged = merge_system_designs(
        [_sd_part(title="A", services=[("svc-a", "A")])],
        title="Union",
    )
    sd_json = merged.model_dump_json(indent=2)

    # Mirror the JSON fast-path in _convert_and_host_sd.
    reparsed = SystemDesign.model_validate(json.loads(sd_json))
    assert any(
        getattr(reparsed, f)
        for f in ("services", "connections", "api_endpoints", "entities")
    )
    html = render_system_design_html(reparsed)
    assert "<!DOCTYPE html>" in html


@pytest.mark.asyncio
async def test_convert_and_host_union_uses_deterministic_merge_not_llm(tmp_path, monkeypatch):
    """For the union key, _convert_and_host_sd merges per-SF sidecars and never
    invokes the sd-converter LLM Ask."""
    from iriai_build_v2.services.artifacts import structured_artifact_key
    from iriai_build_v2.models.outputs import StructuredArtifactEnvelope, StructuredArtifact

    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="alpha", name="Alpha", description="a"),
            Subfeature(id="SF-2", slug="beta", name="Beta", description="b"),
        ],
        decomposition_rationale="Two subfeatures.",
    )

    def _sidecar_payload(content: SystemDesign, slug: str) -> str:
        return StructuredArtifact[SystemDesign](
            meta=StructuredArtifactEnvelope(
                artifact_family="system-design",
                artifact_key=f"system-design:{slug}",
                scope_kind="subfeature",
                scope_slug=slug,
            ),
            content=content,
        ).model_dump_json()

    store = {
        "decomposition": decomposition.model_dump_json(),
        structured_artifact_key("system-design:alpha"): _sidecar_payload(
            _sd_part(
                title="Alpha",
                services=[("svc-alpha", "alpha")],
                decisions=["D shared", "D alpha"],
            ),
            "alpha",
        ),
        structured_artifact_key("system-design:beta"): _sidecar_payload(
            _sd_part(
                title="Beta",
                services=[("svc-alpha", "dup"), ("svc-beta", "beta")],
                decisions=["D shared", "D beta"],
            ),
            "beta",
        ),
    }

    class _Artifacts:
        async def get(self, key, *, feature):
            del feature
            return store.get(key, "")

        async def put(self, key, value, *, feature):
            del feature
            store[key] = value

    pushed: dict[str, str] = {}

    class _Hosting:
        async def push(self, feature_id, key, content, label):
            pushed[key] = content

    def _boom(*args, **kwargs):
        raise AssertionError("runner.run (LLM Ask) must NOT be called for the union merge")

    mirror = _TestMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": _Hosting()},
        run=_boom,
    )
    feature = SimpleNamespace(id="feat-union", name="Union Feature")

    union_prose = "## Compiled Union\n\nProse aggregation of all subfeatures.\n"
    result = await ArchitecturePhase()._convert_and_host_sd(
        runner, feature, "system-design", union_prose, "Union Feature"
    )

    # The deterministic merge fired: result is merged JSON, HTML was pushed,
    # and the LLM Ask (runner.run) was never invoked (would have raised).
    merged = SystemDesign.model_validate(json.loads(result))
    assert [s.id for s in merged.services] == ["svc-alpha", "svc-beta"]
    assert merged.services[0].description == "alpha"  # first-occurrence wins
    assert merged.decisions == ["D shared", "D alpha", "D beta"]
    assert "system-design" in pushed
    assert "<!DOCTYPE html>" in pushed["system-design"]

    # The prose source companion is preserved (NOT overwritten with JSON).
    source_rel = _sd_source_path("system-design")
    source_path = mirror.feature_dir("feat-union") / source_rel
    assert source_path.read_text(encoding="utf-8") == union_prose


@pytest.mark.asyncio
async def test_convert_and_host_union_falls_through_to_llm_when_sidecars_missing(
    tmp_path, monkeypatch
):
    """When per-SF sidecars are absent, the union path falls through to the
    existing LLM converter (no regression)."""
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="alpha", name="Alpha", description="a"),
            Subfeature(id="SF-2", slug="beta", name="Beta", description="b"),
        ],
    )
    store = {"decomposition": decomposition.model_dump_json()}

    class _Artifacts:
        async def get(self, key, *, feature):
            del feature
            return store.get(key, "")

        async def put(self, key, value, *, feature):
            del feature
            store[key] = value

    ran = {"called": False}

    async def _fake_run(task, feature, **kwargs):
        ran["called"] = True
        return _sd_part(title="LLM", services=[("svc-llm", "from-llm")])

    class _Hosting:
        async def push(self, feature_id, key, content, label):
            pass

    mirror = _TestMirror(tmp_path)
    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": mirror, "hosting": _Hosting()},
        run=_fake_run,
    )
    feature = SimpleNamespace(id="feat-missing", name="Union Feature")

    result = await ArchitecturePhase()._convert_and_host_sd(
        runner, feature, "system-design", "## Prose union\n", "Union Feature"
    )

    # No usable sidecars → fell through to the LLM Ask.
    assert ran["called"] is True
    merged = SystemDesign.model_validate(json.loads(result))
    assert [s.id for s in merged.services] == ["svc-llm"]
