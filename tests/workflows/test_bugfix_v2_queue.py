from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from iriai_compose import Ask, Gate, Interview

from iriai_build_v2.models.outputs import (
    BugFixAttempt,
    BugGroup,
    BugTriage,
    Check,
    EvidenceArtifact,
    EvidenceBundle,
    Envelope,
    ImplementationResult,
    Issue,
    Observation,
    RepairStrategyDecision,
    ReproductionResult,
    RootCauseAnalysis,
    Verdict,
)
from iriai_build_v2.workflows.bugfix_v2.models import (
    BugflowDecisionRecord,
    BugflowIntake,
    BugflowLaneSnapshot,
    BugflowQueueSnapshot,
    BugflowReportSnapshot,
    lane_key,
    report_key,
)
from iriai_build_v2.workflows.bugfix_v2.phases import queue as queue_module
from iriai_build_v2.workflows.develop.phases.implementation import PlannedBugDispatch, PlannedBugGroup


class _Artifacts:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    async def get(self, key: str, *, feature) -> str | None:
        return self.values.get((feature.id, key))

    async def put(self, key: str, value: str, *, feature) -> None:
        self.values[(feature.id, key)] = value


class _Adapter:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str | None]] = []

    async def post_message(self, channel: str, text: str, *, thread_ts: str | None = None):
        self.messages.append((channel, text, thread_ts))
        return "1234.5678"


class _RootRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def make_thread_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return object()


class _FeatureStore:
    def __init__(self, events: list[dict[str, object]] | None = None) -> None:
        self._events = events or []

    async def get_events(self, _feature_id: str):
        return list(self._events)

    @asynccontextmanager
    async def advisory_lock(self, _feature_id: str, _name: str):
        yield None


def _feature() -> SimpleNamespace:
    return SimpleNamespace(
        id="bf123456",
        name="Bugflow",
        slug="bugflow-bf123456",
        metadata={
            "channel_id": "CBUGFLOW",
            "source_feature_id": "beced7b1",
            "source_feature_name": "Checkout",
        },
    )


def _report(
    report_id: str,
    *,
    status: str,
    category: str,
    summary: str,
) -> BugflowReportSnapshot:
    return BugflowReportSnapshot(
        report_id=report_id,
        root_message_ts=f"ts-{report_id}",
        thread_ts=f"ts-{report_id}",
        root_message_text=f"[bug] {summary}",
        title=summary,
        summary=summary,
        category=category,
        severity="major",
        status=status,
    )


def _ordinary_strategy_decision(**overrides) -> RepairStrategyDecision:
    payload = {
        "strategy_mode": "ordinary_retry",
        "reasoning": "Retry with the latest known RCA context.",
        "stable_blockers": [],
        "new_blockers": [],
        "failing_checks": [],
        "stable_failure_family": "",
        "bundle_summary": "",
        "scope_expansion": [],
        "required_files": [],
        "required_checks": [],
        "required_evidence_modes": [],
        "similar_cluster_hints": [],
        "merge_recommendation": "none",
        "why_not_ordinary_retry": "",
    }
    payload.update(overrides)
    return RepairStrategyDecision(**payload)


@pytest.mark.asyncio
async def test_process_report_queues_requirement_for_lane(monkeypatch: pytest.MonkeyPatch):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report(
        "BR-2001",
        status="intake_pending",
        category="",
        summary="Tax field is missing",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        interaction_runtimes={"terminal": _RootRuntime()},
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Interview):
            assert report.root_message_text in task.initial_prompt
            return Envelope[BugflowIntake](
                output=BugflowIntake(
                    title="Tax field is missing",
                    description="The form should show taxes before submit.",
                    candidate_category="requirement",
                    severity="major",
                ),
                complete=True,
            )
        if isinstance(task, Ask):
            return Observation(
                id=report.report_id,
                category="requirement",
                severity="major",
                title="Tax field is missing",
                description="The form should show taxes before submit.",
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    dispatch_called = False

    async def _fake_dispatch(*_args, **_kwargs):
        nonlocal dispatch_called
        dispatch_called = True
        return {"status": "FIXED", "summary": "should not run"}

    monkeypatch.setattr(queue_module, "_dispatch_observation", _fake_dispatch)

    phase = queue_module.BugflowQueuePhase()
    await phase._process_report(
        runner,
        feature,
        report.report_id,
    )

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert saved.status == "queued"
    assert saved.current_step == f"Queued requirement lane for {report.report_id}"
    assert dispatch_called is False


@pytest.mark.asyncio
async def test_ensure_non_bug_lanes_creates_isolated_lane_for_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report("BR-2100", status="queued", category="requirement", summary="Tax field is missing")
    report.affected_area = "frontend/src/tax.tsx"
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is RepairStrategyDecision:
            return _ordinary_strategy_decision(
                stable_blockers=[Issue(severity="major", description="Checkout button does nothing", file="frontend/src/checkout.tsx")],
                bundle_summary="Retry the lane with the same RCA context.",
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "abc123"}

    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)
    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: tmp_path / "main" / "repos")

    phase = queue_module.BugflowQueuePhase()
    await phase._ensure_non_bug_lanes(runner, feature, [report])

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert saved.lane_id.startswith("L-")
    assert saved.cluster_id.startswith("C-")

    lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(saved.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(lane, queue_module.BugflowLaneSnapshot)
    assert lane.category == "requirement"
    assert lane.lock_scope == ["file:frontend/src/tax.tsx"]
    assert adapter.messages[-1] == (
        "CBUGFLOW",
        f"{report.report_id}: assigned to isolated lane {saved.lane_id} for requirement work.",
        report.thread_ts,
    )


def test_make_thread_actor_binds_report_runtime_instance(monkeypatch: pytest.MonkeyPatch):
    feature = _feature()
    report = _report("BR-2400", status="validation_pending", category="bug", summary="Save returns 500")
    adapter = _Adapter()

    created: list[str | None] = []

    def _fake_create_agent_runtime(
        name: str | None,
        *,
        session_store=None,
        on_message=None,
        interactive_roles=None,
    ):
        del session_store, interactive_roles
        created.append(name)
        return SimpleNamespace(name=name, on_message=on_message)

    monkeypatch.setattr(queue_module, "create_agent_runtime", _fake_create_agent_runtime)

    runner = SimpleNamespace(
        agent_runtime=SimpleNamespace(name="claude"),
        secondary_runtime=SimpleNamespace(name="codex"),
        sessions=object(),
        services={"slack_adapter": adapter},
    )

    actor = queue_module._make_thread_actor(
        runner,
        feature,
        report,
        queue_module.integration_tester,
        "bugflow-validate-BR-2400",
        runtime="secondary",
    )

    assert created == ["claude", "codex"]
    assert actor.role.metadata["runtime"] == "secondary"
    assert actor.role.metadata["runtime_instance"].name == "codex"


def test_make_thread_user_matches_existing_interview_persistence_pattern(
    monkeypatch: pytest.MonkeyPatch,
):
    feature = _feature()
    report = _report("BR-2401", status="intake_pending", category="bug", summary="Save returns 500")
    adapter = _Adapter()
    root_runtime = _RootRuntime()

    created: list[tuple[str | None, set[str] | None]] = []

    def _fake_create_agent_runtime(
        name: str | None,
        *,
        session_store=None,
        on_message=None,
        interactive_roles=None,
    ):
        del session_store, on_message
        created.append((name, interactive_roles))
        return SimpleNamespace(name=name, interactive_roles=interactive_roles)

    monkeypatch.setattr(queue_module, "create_agent_runtime", _fake_create_agent_runtime)

    runner = SimpleNamespace(
        agent_runtime=SimpleNamespace(name="claude", _interactive_roles={"bug-interviewer", "implementer"}),
        secondary_runtime=SimpleNamespace(name="codex", _interactive_roles={"bug-interviewer"}),
        sessions=object(),
        services={"slack_adapter": adapter},
        interaction_runtimes={"terminal": root_runtime},
    )

    resolver, thread_user = queue_module._make_thread_user(runner, feature, report)

    assert resolver == f"terminal.thread.{report.report_id}"
    assert thread_user.resolver == resolver
    assert created == [
        ("claude", {"bug-interviewer", "implementer"}),
        ("codex", {"bug-interviewer"}),
    ]
    assert root_runtime.calls[-1]["persist_turns"] is True
    assert root_runtime.calls[-1]["agent_runtime"].name == "claude"


def test_ensure_report_retry_state_upgrades_stale_budget():
    report = _report("BR-2402", status="blocked", category="bug", summary="Save returns 500")
    report.attempts_used = 3
    report.max_attempts = 3

    saved = queue_module._ensure_report_retry_state(report)

    assert saved.max_attempts == 50
    assert saved.attempts_used == 3


@pytest.mark.asyncio
async def test_process_report_uses_live_bug_activity_when_deciding_retriage(monkeypatch: pytest.MonkeyPatch):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report(
        "BR-2200",
        status="validation_pending",
        category="bug",
        summary="Checkout button does nothing",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        interaction_runtimes={"terminal": _RootRuntime()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ReproductionResult:
            return ReproductionResult(reproduced=True, summary="Still reproduces")
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=False,
                summary="Still broken",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run
    monkeypatch.setattr(queue_module, "_has_active_bug_lanes", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))

    phase = queue_module.BugflowQueuePhase()
    phase._planning_task = None
    await phase._process_report(runner, feature, report.report_id)

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert saved.status == "queued"
    assert saved.current_step == f"{report.report_id} queued for RCA"


@pytest.mark.asyncio
async def test_process_report_routes_bug_validation_to_secondary_runtime():
    feature = _feature()
    artifacts = _Artifacts()
    report = _report(
        "BR-2201",
        status="validation_pending",
        category="bug",
        summary="Checkout button does nothing",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    seen_runtimes: list[str | None] = []
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        interaction_runtimes={"terminal": _RootRuntime()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask):
            seen_runtimes.append(task.actor.role.metadata.get("runtime"))
            if task.output_type is ReproductionResult:
                return ReproductionResult(reproduced=True, summary="Still reproduces")
            if task.output_type is Verdict:
                return Verdict(
                    approved=False,
                    summary="Still broken",
                    concerns=[],
                    suggestions=[],
                    checks=[],
                    gaps=[],
                )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    phase = queue_module.BugflowQueuePhase()
    await phase._process_report(runner, feature, report.report_id)

    assert seen_runtimes == ["secondary", "secondary"]


@pytest.mark.asyncio
async def test_process_report_requires_ui_proof_before_no_repro_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report(
        "BR-2202",
        status="validation_pending",
        category="bug",
        summary="Canvas drag connection disappears",
    )
    report.ui_involved = True
    report.evidence_modes = ["ui"]
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)
    trace_file = tmp_path / "trace.zip"
    trace_file.write_text("trace", encoding="utf-8")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        interaction_runtimes={"terminal": _RootRuntime()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ReproductionResult:
            return ReproductionResult(
                reproduced=False,
                summary="Could not reproduce",
                proof=EvidenceBundle(
                    ui_involved=True,
                    evidence_modes=["ui"],
                    summary="Trace only; screenshot missing.",
                    artifacts=[
                        EvidenceArtifact(
                            kind="trace",
                            label="trace.zip",
                            local_path=str(trace_file),
                        )
                    ],
                ),
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run
    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)

    phase = queue_module.BugflowQueuePhase()
    await phase._process_report(runner, feature, report.report_id)

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert saved.status == "validation_pending"
    assert "required reproduction proof" in saved.current_step
    assert saved.latest_proof_key == queue_module.proof_key(report.report_id, "reproduce")
    assert "screenshot" in adapter.messages[-1][1].lower()


@pytest.mark.asyncio
async def test_process_report_records_terminal_proof_for_approved_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report(
        "BR-2203",
        status="validation_pending",
        category="bug",
        summary="Canvas drag connection disappears",
    )
    report.ui_involved = True
    report.evidence_modes = ["ui", "api"]
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)
    trace_file = tmp_path / "trace.zip"
    trace_file.write_text("trace", encoding="utf-8")
    screenshot_file = tmp_path / "shot.png"
    screenshot_file.write_text("png", encoding="utf-8")

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        interaction_runtimes={"terminal": _RootRuntime()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ReproductionResult:
            return ReproductionResult(reproduced=True, summary="Still reproduces")
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=True,
                summary="This is already fixed on the current head.",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
                proof=EvidenceBundle(
                    ui_involved=True,
                    evidence_modes=["ui", "api"],
                    summary="Verified with Playwright plus API postcondition evidence.",
                    state_change=True,
                    artifacts=[
                        EvidenceArtifact(kind="trace", label="trace.zip", local_path=str(trace_file)),
                        EvidenceArtifact(kind="screenshot", label="after-fix", local_path=str(screenshot_file)),
                        EvidenceArtifact(kind="api-response", label="PATCH /edges", excerpt="200 OK"),
                        EvidenceArtifact(kind="api-response", role="postcondition", label="GET /edges", excerpt="edge persisted"),
                    ],
                ),
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run
    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)

    phase = queue_module.BugflowQueuePhase()
    await phase._process_report(runner, feature, report.report_id)

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert saved.status == "resolved-no-repro"
    assert saved.terminal_proof_key == queue_module.proof_key(report.report_id, "terminal")
    terminal = queue_module.parse_model(
        artifacts.values[(feature.id, saved.terminal_proof_key)],
        queue_module.BugflowProofRecord,
    )
    assert isinstance(terminal, queue_module.BugflowProofRecord)
    assert "/proof/bf123456/BR-2203/terminal-" in terminal.bundle_url
    assert "proof bundle:" in adapter.messages[-1][1].lower()


def test_missing_terminal_proof_requires_backend_postcondition_for_state_change():
    report = _report(
        "BR-2204",
        status="validation_pending",
        category="bug",
        summary="Saving the edge should persist it",
    )
    report.evidence_modes = ["api"]

    missing = queue_module._missing_terminal_proof_requirements(
        report,
        EvidenceBundle(
            ui_involved=False,
            evidence_modes=["api"],
            summary="Saw a 200 response.",
            state_change=True,
            artifacts=[
                EvidenceArtifact(kind="api-response", label="PATCH /edges", excerpt="200 OK"),
            ],
        ),
    )

    assert "independent postcondition evidence" in missing


@pytest.mark.asyncio
async def test_plan_bug_reports_creates_lane_and_cluster_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report_one = _report("BR-1001", status="queued", category="bug", summary="Checkout button does nothing")
    report_two = _report("BR-1002", status="queued", category="bug", summary="Order summary never refreshes")
    await artifacts.put(report_key(report_one.report_id), report_one.model_dump_json(), feature=feature)
    await artifacts.put(report_key(report_two.report_id), report_two.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore(),
        services={"slack_adapter": _Adapter()},
        interaction_runtimes={"terminal": _RootRuntime()},
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is RepairStrategyDecision:
            return _ordinary_strategy_decision(
                stable_blockers=[Issue(severity="major", description="Checkout button does nothing", file="frontend/src/checkout.tsx")],
                bundle_summary="Retry with the initial RCA context.",
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    dispatch = PlannedBugDispatch(
        attempt_number=1,
        triage=BugTriage(
            groups=[
                BugGroup(
                    group_id="BG-1",
                    likely_root_cause="missing submit handler",
                    issue_indices=[0],
                    severity="major",
                    affected_files_hint=["frontend/src/checkout.tsx"],
                )
            ]
        ),
        groups=[
            PlannedBugGroup(
                group=BugGroup(
                    group_id="BG-1",
                    likely_root_cause="missing submit handler",
                    issue_indices=[0],
                    severity="major",
                    affected_files_hint=["frontend/src/checkout.tsx"],
                ),
                rca=RootCauseAnalysis(
                    hypothesis="missing submit handler",
                    evidence=["button rendered without action"],
                    affected_files=["frontend/src/checkout.tsx"],
                    proposed_approach="restore submit binding",
                    confidence="high",
                ),
                issue_text="- [major] Checkout button does nothing",
                rca_key="bug-rca:bugflow-plan:bg-1",
            )
        ],
        fixable_groups=[],
        contradiction_groups=[],
        schedule=[["BG-1"]],
        dispatch_key="bug-dispatch:bugflow-plan:attempt-1",
    )
    dispatch.fixable_groups = list(dispatch.groups)

    captured_kwargs: dict[str, object] = {}

    async def _fake_plan(*_args, **_kwargs):
        captured_kwargs.update(_kwargs)
        return dispatch

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "abc123"}

    monkeypatch.setattr(queue_module, "_plan_bug_groups", _fake_plan)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)
    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: tmp_path / "main" / "repos")

    phase = queue_module.BugflowQueuePhase()
    await phase._plan_bug_reports(runner, feature, [report_one.report_id, report_two.report_id])

    lane_keys = [key for (feature_id, key) in artifacts.values if feature_id == feature.id and key.startswith("bugflow-lane:")]
    cluster_keys = [key for (feature_id, key) in artifacts.values if feature_id == feature.id and key.startswith("bugflow-cluster:")]
    assert len(lane_keys) == 1
    assert len(cluster_keys) == 1

    saved_one = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report_one.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved_one, queue_module.BugflowReportSnapshot)
    assert saved_one.lane_id.startswith("L-")
    assert saved_one.cluster_id.startswith("C-")
    assert captured_kwargs.get("rca_runtime") == "secondary"


@pytest.mark.asyncio
async def test_plan_bug_reports_single_report_uses_thread_bound_secondary_planning_actor(
    monkeypatch: pytest.MonkeyPatch,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-1003", status="queued", category="bug", summary="Checkout button does nothing")
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    created: list[str | None] = []

    def _fake_create_agent_runtime(
        name: str | None,
        *,
        session_store=None,
        on_message=None,
        interactive_roles=None,
    ):
        del session_store, on_message, interactive_roles
        created.append(name)
        return SimpleNamespace(name=name)

    monkeypatch.setattr(queue_module, "create_agent_runtime", _fake_create_agent_runtime)

    captured_actor = None

    async def _fake_plan(*_args, **kwargs):
        nonlocal captured_actor
        actor_factory = kwargs.get("actor_factory")
        assert callable(actor_factory)
        captured_actor = actor_factory(queue_module.integration_tester, "triage")
        return PlannedBugDispatch(
            attempt_number=1,
            triage=BugTriage(groups=[]),
            groups=[],
            fixable_groups=[],
            contradiction_groups=[],
            schedule=[],
            dispatch_key="bug-dispatch:test:attempt-1",
        )

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore(),
        services={"slack_adapter": _Adapter()},
        interaction_runtimes={"terminal": _RootRuntime()},
        agent_runtime=SimpleNamespace(name="claude", _interactive_roles={"bug-interviewer", "implementer"}),
        secondary_runtime=SimpleNamespace(name="codex", _interactive_roles={"bug-interviewer"}),
        sessions=object(),
    )

    monkeypatch.setattr(queue_module, "_plan_bug_groups", _fake_plan)

    phase = queue_module.BugflowQueuePhase()
    await phase._plan_bug_reports(runner, feature, [report.report_id])

    assert created == ["claude", "codex"]
    assert captured_actor is not None
    assert captured_actor.role.metadata["runtime"] == "secondary"
    assert captured_actor.role.metadata["runtime_instance"].name == "codex"


@pytest.mark.asyncio
async def test_execute_observation_lane_routes_fix_primary_and_verify_secondary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-2300", status="queued", category="requirement", summary="Tax field is missing")
    lane = BugflowLaneSnapshot(
        lane_id="L-2300",
        report_ids=[report.report_id],
        category="requirement",
        status="active_fix",
        workspace_root=str(tmp_path / "lanes" / "L-2300" / "repos"),
        base_main_commits_by_repo={},
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
        agent_runtime=SimpleNamespace(name="claude", _interactive_roles={"bug-interviewer", "implementer"}),
        secondary_runtime=SimpleNamespace(name="codex", _interactive_roles={"bug-interviewer"}),
        sessions=object(),
    )

    captured: dict[str, object] = {}

    async def _fake_dispatch(*_args, **_kwargs):
        captured.update(_kwargs)
        return {"status": "FIXED", "summary": "Added tax field"}

    def _fake_create_agent_runtime(
        name: str | None,
        *,
        session_store=None,
        on_message=None,
        interactive_roles=None,
    ):
        del session_store, on_message, interactive_roles
        return SimpleNamespace(name=name)

    monkeypatch.setattr(queue_module, "_dispatch_observation", _fake_dispatch)
    monkeypatch.setattr(queue_module, "_lane_modified_files", lambda *_args, **_kwargs: asyncio.sleep(0, result=["frontend/src/tax.tsx"]))
    monkeypatch.setattr(queue_module, "create_agent_runtime", _fake_create_agent_runtime)

    phase = queue_module.BugflowQueuePhase()
    success = await phase._execute_observation_lane(runner, feature, lane)

    assert success is True
    assert captured["rca_runtime"] == "secondary"
    assert captured["implement_runtime"] == "primary"
    assert captured["test_runtime"] == "primary"
    assert captured["verify_runtime"] == "secondary"
    actor = captured["actor_factory"](
        queue_module.implementer,
        "obs-impl-BR-2300",
        runtime="primary",
        workspace_path=str(lane.workspace_root),
    )
    assert actor.role.metadata["runtime"] == "primary"
    assert actor.role.metadata["runtime_instance"].name == "claude"


@pytest.mark.asyncio
async def test_execute_bug_lane_routes_fix_primary_and_verify_secondary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-2301", status="queued", category="bug", summary="Checkout button does nothing")
    report.thread_ts = "ts-BR-2301"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-2301",
        group_id="BG-2301",
        report_ids=[report.report_id],
        lane_id="L-2301",
    )
    lane_root = tmp_path / "lanes" / "L-2301" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)
    lane = BugflowLaneSnapshot(
        lane_id="L-2301",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={},
        latest_rca_keys=["bug-rca:test:BG-2301"],
        issue_summary="Checkout button does nothing",
        lane_attempt=1,
    )
    rca = RootCauseAnalysis(
        hypothesis="Missing submit handler",
        evidence=["Button click is never bound"],
        affected_files=["frontend/src/checkout.tsx"],
        proposed_approach="Restore submit binding",
        confidence="high",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put("bug-rca:test:BG-2301", rca.model_dump_json(), feature=feature)

    seen: list[tuple[str | None, str]] = []
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask):
            seen.append((task.actor.role.metadata.get("runtime"), task.output_type.__name__))
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="task-1",
                    summary="Restored submit binding",
                    files_modified=["frontend/src/checkout.tsx"],
                )
            if task.output_type is Verdict:
                return Verdict(
                    approved=True,
                    summary="Looks good",
                    concerns=[],
                    suggestions=[],
                    checks=[],
                    gaps=[],
                )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    async def _noop(*_args, **_kwargs):
        return None

    async def _fake_append_attempts(*_args, **_kwargs):
        return None

    monkeypatch.setattr(queue_module, "_commit_repos_in_root", _noop)
    monkeypatch.setattr(queue_module, "_run_regression", _noop)
    monkeypatch.setattr(queue_module, "_append_bug_fix_attempts", _fake_append_attempts)
    monkeypatch.setattr(queue_module, "_resolve_fix_workspace_from_root", lambda *_args, **_kwargs: str(lane_root))

    phase = queue_module.BugflowQueuePhase()
    success = await phase._execute_bug_lane(runner, feature, lane)

    assert success is True
    assert seen == [("primary", "ImplementationResult"), ("secondary", "Verdict")]


@pytest.mark.asyncio
async def test_execute_bug_lane_passes_thread_actor_factory_to_retry_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-2301B", status="queued", category="bug", summary="Checkout button does nothing")
    report.thread_ts = "ts-BR-2301B"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-2301B",
        group_id="BG-2301B",
        report_ids=[report.report_id],
        lane_id="L-2301B",
    )
    lane_root = tmp_path / "lanes" / "L-2301B" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)
    lane = BugflowLaneSnapshot(
        lane_id="L-2301B",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={},
        latest_rca_keys=["bug-rca:test:BG-2301B"],
        issue_summary="Checkout button does nothing",
        lane_attempt=1,
    )
    rca = RootCauseAnalysis(
        hypothesis="Missing submit handler",
        evidence=["Button click is never bound"],
        affected_files=["frontend/src/checkout.tsx"],
        proposed_approach="Restore submit binding",
        confidence="high",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put("bug-rca:test:BG-2301B", rca.model_dump_json(), feature=feature)

    def _fake_create_agent_runtime(
        name: str | None,
        *,
        session_store=None,
        on_message=None,
        interactive_roles=None,
    ):
        del session_store, on_message, interactive_roles
        return SimpleNamespace(name=name)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
        agent_runtime=SimpleNamespace(name="claude", _interactive_roles={"implementer"}),
        secondary_runtime=SimpleNamespace(name="codex", _interactive_roles={"bug-interviewer"}),
        sessions=object(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ImplementationResult:
            return ImplementationResult(
                task_id="task-1",
                summary="Restored submit binding",
                files_modified=["frontend/src/checkout.tsx"],
            )
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=False,
                summary="Still failing",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    async def _noop(*_args, **_kwargs):
        return None

    captured_actor = None

    async def _fake_retry(*_args, **kwargs):
        nonlocal captured_actor
        actor_factory = kwargs.get("actor_factory")
        assert callable(actor_factory)
        captured_actor = actor_factory(
            queue_module.integration_tester,
            "probe",
            runtime="secondary",
            workspace_path=str(lane_root),
        )
        return BugFixAttempt(
            bug_id="L-2301B-retry-1",
            group_id="BG-2301B",
            source_verdict="lane-retry:L-2301B",
            description="Checkout button does nothing",
            root_cause="Missing submit handler",
            fix_applied="Tried a second binding fix",
            files_modified=["frontend/src/checkout.tsx"],
            re_verify_result="FAIL",
            attempt_number=2,
        )

    runner.run = _fake_run
    monkeypatch.setattr(queue_module, "create_agent_runtime", _fake_create_agent_runtime)
    monkeypatch.setattr(queue_module, "_commit_repos_in_root", _noop)
    monkeypatch.setattr(queue_module, "_append_bug_fix_attempts", _noop)
    monkeypatch.setattr(queue_module, "_resolve_fix_workspace_from_root", lambda *_args, **_kwargs: str(lane_root))
    monkeypatch.setattr(queue_module, "_single_rca_fix_verify", _fake_retry)

    phase = queue_module.BugflowQueuePhase()
    success = await phase._execute_bug_lane(runner, feature, lane)

    assert success is False
    assert captured_actor is not None
    assert captured_actor.role.metadata["runtime_instance"].name == "codex"


@pytest.mark.asyncio
async def test_execute_bug_lane_passes_thread_actor_factory_to_regression_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-2301C", status="queued", category="bug", summary="Checkout button does nothing")
    report.thread_ts = "ts-BR-2301C"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-2301C",
        group_id="BG-2301C",
        report_ids=[report.report_id],
        lane_id="L-2301C",
    )
    lane_root = tmp_path / "lanes" / "L-2301C" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)
    lane = BugflowLaneSnapshot(
        lane_id="L-2301C",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={},
        latest_rca_keys=["bug-rca:test:BG-2301C"],
        issue_summary="Checkout button does nothing",
        lane_attempt=1,
    )
    rca = RootCauseAnalysis(
        hypothesis="Missing submit handler",
        evidence=["Button click is never bound"],
        affected_files=["frontend/src/checkout.tsx"],
        proposed_approach="Restore submit binding",
        confidence="high",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put("bug-rca:test:BG-2301C", rca.model_dump_json(), feature=feature)

    def _fake_create_agent_runtime(
        name: str | None,
        *,
        session_store=None,
        on_message=None,
        interactive_roles=None,
    ):
        del session_store, on_message, interactive_roles
        return SimpleNamespace(name=name)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
        agent_runtime=SimpleNamespace(name="claude", _interactive_roles={"implementer"}),
        secondary_runtime=SimpleNamespace(name="codex", _interactive_roles={"bug-interviewer"}),
        sessions=object(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ImplementationResult:
            return ImplementationResult(
                task_id="task-1",
                summary="Restored submit binding",
                files_modified=["frontend/src/checkout.tsx"],
            )
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=True,
                summary="Looks good",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    async def _noop(*_args, **_kwargs):
        return None

    captured_actor = None

    async def _fake_regression(*_args, **kwargs):
        nonlocal captured_actor
        actor_factory = kwargs.get("actor_factory")
        assert callable(actor_factory)
        captured_actor = actor_factory(
            queue_module.integration_tester,
            "probe",
            runtime="secondary",
            workspace_path=str(lane_root),
        )
        return None

    runner.run = _fake_run
    monkeypatch.setattr(queue_module, "create_agent_runtime", _fake_create_agent_runtime)
    monkeypatch.setattr(queue_module, "_commit_repos_in_root", _noop)
    monkeypatch.setattr(queue_module, "_append_bug_fix_attempts", _noop)
    monkeypatch.setattr(queue_module, "_resolve_fix_workspace_from_root", lambda *_args, **_kwargs: str(lane_root))
    monkeypatch.setattr(queue_module, "_run_regression", _fake_regression)

    phase = queue_module.BugflowQueuePhase()
    success = await phase._execute_bug_lane(runner, feature, lane)

    assert success is True
    assert captured_actor is not None
    assert captured_actor.role.metadata["runtime_instance"].name == "codex"


@pytest.mark.asyncio
async def test_admit_planned_lanes_only_starts_disjoint_work(
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
    )

    reports = [
        _report("BR-1", status="queued", category="bug", summary="A"),
        _report("BR-2", status="queued", category="bug", summary="B"),
        _report("BR-3", status="queued", category="bug", summary="C"),
    ]
    for report in reports:
        await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    lanes = [
        BugflowLaneSnapshot(
            lane_id="L-1",
            report_ids=["BR-1"],
            status="planned",
            lock_scope=["file:frontend/src/a.tsx"],
            workspace_root=str(tmp_path / "L-1"),
        ),
        BugflowLaneSnapshot(
            lane_id="L-2",
            report_ids=["BR-2"],
            status="planned",
            lock_scope=["repo:frontend"],
            workspace_root=str(tmp_path / "L-2"),
        ),
        BugflowLaneSnapshot(
            lane_id="L-3",
            report_ids=["BR-3"],
            status="planned",
            lock_scope=["file:backend/app.py"],
            workspace_root=str(tmp_path / "L-3"),
        ),
    ]
    for lane in lanes:
        await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)

    started: list[str] = []

    async def _fake_execute_lane(_runner, _feature, lane_id: str):
        started.append(lane_id)
        await asyncio.sleep(0)

    phase = queue_module.BugflowQueuePhase()
    phase._execute_lane = _fake_execute_lane  # type: ignore[method-assign]

    await phase._admit_planned_lanes(runner, feature, lanes)
    await asyncio.gather(*phase._lane_tasks.values())

    assert started == ["L-1", "L-3"]
    blocked_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key("L-2"))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(blocked_lane, queue_module.BugflowLaneSnapshot)
    assert blocked_lane.status == "planned"
    assert blocked_lane.wait_reason == "Waiting for overlapping lane work to finish"


@pytest.mark.asyncio
async def test_revalidate_pending_retriage_clears_stale_lane_and_cluster_on_requeue():
    feature = _feature()
    artifacts = _Artifacts()
    report = _report(
        "BR-6001",
        status="pending_retriage",
        category="bug",
        summary="Checkout button does nothing",
    )
    report.cluster_id = "C-old"
    report.lane_id = "L-old"
    report.cluster = {"cluster_id": "C-old", "status": "active_fix"}
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(
        "bugflow-queue",
        BugflowQueueSnapshot(report_ids=[report.report_id]).model_dump_json(),
        feature=feature,
    )

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ReproductionResult:
            return ReproductionResult(reproduced=True, summary="Still reproduces")
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=False,
                summary="Still broken",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    phase = queue_module.BugflowQueuePhase()
    await phase._revalidate_pending_retriage(runner, feature)

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert saved.status == "queued"
    assert saved.cluster_id == ""
    assert saved.cluster is None
    assert saved.lane_id == ""


@pytest.mark.asyncio
async def test_maybe_revalidate_pending_retriage_runs_when_queue_is_idle(monkeypatch: pytest.MonkeyPatch):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report(
        "BR-6002",
        status="pending_retriage",
        category="bug",
        summary="Still pending",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(
        "bugflow-queue",
        BugflowQueueSnapshot(report_ids=[report.report_id]).model_dump_json(),
        feature=feature,
    )

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore(),
    )

    called = False

    async def _fake_revalidate(_runner, _feature):
        nonlocal called
        called = True

    monkeypatch.setattr(queue_module, "_has_active_bug_lanes", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))

    phase = queue_module.BugflowQueuePhase()
    monkeypatch.setattr(phase, "_revalidate_pending_retriage", _fake_revalidate)
    await phase._maybe_revalidate_pending_retriage(runner, feature)

    assert called is True


@pytest.mark.asyncio
async def test_reap_promotion_task_keeps_lane_recoverable_when_respawn_cannot_start():
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    lane = BugflowLaneSnapshot(
        lane_id="L-9000",
        report_ids=["BR-9000"],
        category="bug",
        status="verified_pending_promotion",
        promotion_status="queued",
        workspace_root="/tmp/L-9000",
    )
    report = _report("BR-9000", status="active_fix", category="bug", summary="A")
    report.lane_id = lane.lane_id
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore(),
        services={"slack_adapter": adapter},
    )

    async def _boom():
        raise RuntimeError("missing main bugflow root")

    phase = queue_module.BugflowQueuePhase()
    phase._promotion_lane_id = lane.lane_id
    phase._promotion_task = asyncio.create_task(_boom())
    await asyncio.sleep(0)

    await phase._reap_promotion_task(runner, feature)

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_promotion = await queue_module._load_promotion_queue(runner, feature)
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert saved_lane.status == "verified_pending_promotion"
    assert "missing main bugflow root" in saved_lane.wait_reason.lower()
    assert saved_promotion.execution_state == "recovering"
    assert "awaiting retry" in saved_promotion.status_text.lower()


@pytest.mark.asyncio
async def test_load_reports_includes_event_backed_reports():
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-EVT1", status="intake_pending", category="bug", summary="From event")
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore([
            {
                "event_type": "bugflow_report_created",
                "metadata": {"report_id": report.report_id},
            }
        ]),
    )

    reports = await queue_module._load_reports(runner, feature)

    assert [item.report_id for item in reports] == [report.report_id]


@pytest.mark.asyncio
async def test_reap_lane_tasks_marks_lane_recovering_when_respawn_cannot_start():
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report(
        "BR-4001",
        status="active_fix",
        category="bug",
        summary="Checkout button does nothing",
    )
    report.lane_id = "L-4001"
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(
        lane_key("L-4001"),
        BugflowLaneSnapshot(
            lane_id="L-4001",
            report_ids=[report.report_id],
            status="active_fix",
            workspace_root="/tmp/l-4001",
        ).model_dump_json(),
        feature=feature,
    )

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
    )

    async def _boom():
        raise RuntimeError("implementer stalled 3 times (600s inactivity each) - giving up")

    task = asyncio.create_task(_boom())
    with pytest.raises(RuntimeError):
        await task

    phase = queue_module.BugflowQueuePhase()
    phase._lane_tasks = {"L-4001": task}
    await phase._reap_lane_tasks(runner, feature)

    saved = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key("L-4001"))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(saved, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert saved.status == "active_fix"
    assert saved_lane.execution_state == "recovering"
    assert "implementer stalled 3 times" in saved_lane.wait_reason


@pytest.mark.asyncio
async def test_reap_cancelled_lane_task_recovers_only_current_execution_nonce(
    monkeypatch: pytest.MonkeyPatch,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report("BR-CANCEL", status="active_fix", category="bug", summary="Cancelled lane")
    report.lane_id = "L-CANCEL"
    lane = BugflowLaneSnapshot(
        lane_id="L-CANCEL",
        report_ids=[report.report_id],
        status="active_fix",
        category="bug",
        workspace_root="/tmp/l-cancel",
        execution_state="running",
        execution_nonce="exec-current",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)

    respawned = False

    async def _fake_respawn(*_args, **_kwargs):
        nonlocal respawned
        respawned = True

    monkeypatch.setattr(queue_module, "_respawn_lane_from_latest_main", _fake_respawn)

    phase = queue_module.BugflowQueuePhase()
    task = asyncio.create_task(asyncio.sleep(3600))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    phase._lane_tasks = {lane.lane_id: task}
    phase._lane_task_state = {
        lane.lane_id: queue_module._ExecutionTaskState(
            kind="lane",
            resource_id=lane.lane_id,
            nonce="exec-current",
        )
    }

    runner = SimpleNamespace(artifacts=artifacts, services={"slack_adapter": adapter})
    await phase._reap_lane_tasks(runner, feature)

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert respawned is True
    assert saved_lane.execution_state == "recovering"
    assert saved_report.latest_execution_notice_key.startswith("lane-task:L-CANCEL:exec-current")


@pytest.mark.asyncio
async def test_reap_cancelled_lane_task_ignores_stale_execution_nonce():
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report("BR-STALE", status="active_fix", category="bug", summary="Stale lane")
    report.lane_id = "L-STALE"
    lane = BugflowLaneSnapshot(
        lane_id="L-STALE",
        report_ids=[report.report_id],
        status="active_fix",
        category="bug",
        workspace_root="/tmp/l-stale",
        execution_state="running",
        execution_nonce="exec-new",
    )
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)

    phase = queue_module.BugflowQueuePhase()
    task = asyncio.create_task(asyncio.sleep(3600))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    phase._lane_tasks = {lane.lane_id: task}
    phase._lane_task_state = {
        lane.lane_id: queue_module._ExecutionTaskState(
            kind="lane",
            resource_id=lane.lane_id,
            nonce="exec-old",
        )
    }

    runner = SimpleNamespace(artifacts=artifacts, services={"slack_adapter": adapter})
    await phase._reap_lane_tasks(runner, feature)

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert saved_lane.execution_state == "running"
    assert saved_report.latest_execution_notice_key == ""


@pytest.mark.asyncio
async def test_execute_lane_false_respawns_bug_lane_when_retry_budget_remains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-4100", status="active_fix", category="bug", summary="Checkout button does nothing")
    report.lane_id = "L-4100"
    report.cluster_id = "C-4100"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-4100",
        report_ids=[report.report_id],
        lane_id="L-4100",
        status="active_fix",
        current_phase="fixing",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-4100",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(tmp_path / "lane"),
        latest_verify_summary="Retry failed",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is RepairStrategyDecision:
            return _ordinary_strategy_decision(
                stable_blockers=[Issue(severity="major", description="Promotion verify failed", file="frontend/src/checkout.tsx")],
                bundle_summary="Retry promotion from a fresh lane.",
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    phase = queue_module.BugflowQueuePhase()

    async def _fake_execute_bug_lane(_runner, _feature, _lane):
        return False

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    monkeypatch.setattr(phase, "_execute_bug_lane", _fake_execute_bug_lane)
    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)

    await phase._execute_lane(runner, feature, lane.lane_id)

    saved_old_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_old_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_old_lane.status == "superseded"
    assert saved_report.status == "queued"
    assert saved_report.lane_id != lane.lane_id
    assert saved_report.attempts_used == 1
    assert saved_report.last_failed_lane_id == lane.lane_id
    assert saved_report.last_failure_kind == "lane-verify"
    assert saved_cluster.status == "planned"
    assert saved_cluster.attempt_number == 2
    assert any("attempt 1/50 failed in re-verification" in message[1].lower() for message in adapter.messages)
    assert any("switching to ordinary retry mode" in message[1].lower() for message in adapter.messages)


@pytest.mark.asyncio
async def test_execute_lane_false_blocks_bug_lane_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report("BR-4101", status="active_fix", category="bug", summary="Checkout button does nothing")
    report.lane_id = "L-4101"
    report.cluster_id = "C-4101"
    report.attempts_used = 49
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-4101",
        report_ids=[report.report_id],
        lane_id="L-4101",
        status="active_fix",
        current_phase="fixing",
        attempt_number=50,
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-4101",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(tmp_path / "lane"),
        latest_verify_summary="Retry failed",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    phase = queue_module.BugflowQueuePhase()

    async def _fake_execute_bug_lane(_runner, _feature, _lane):
        return False

    monkeypatch.setattr(phase, "_execute_bug_lane", _fake_execute_bug_lane)

    await phase._execute_lane(runner, feature, lane.lane_id)

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_lane.status == "blocked"
    assert saved_report.status == "blocked"
    assert saved_report.attempts_used == 50
    assert saved_cluster.status == "blocked"
    assert "attempt 50/50 failed" in adapter.messages[-1][1].lower()


@pytest.mark.asyncio
async def test_recover_stale_active_lane_respawns_after_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report("BR-7001", status="active_fix", category="bug", summary="Checkout button does nothing")
    report.lane_id = "L-stale"
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(
        lane_key("L-stale"),
        BugflowLaneSnapshot(
            lane_id="L-stale",
            report_ids=[report.report_id],
            category="bug",
            status="active_fix",
            workspace_root=str(tmp_path / "stale-lane"),
            base_main_commits_by_repo={"frontend": "abc123"},
        ).model_dump_json(),
        feature=feature,
    )

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: tmp_path / "main" / "repos")
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)

    phase = queue_module.BugflowQueuePhase()
    stale_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key("L-stale"))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(stale_lane, queue_module.BugflowLaneSnapshot)

    await phase._recover_stale_execution_state(runner, feature, [stale_lane])

    saved_old_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key("L-stale"))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved_old_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert saved_old_lane.status == "superseded"
    assert saved_report.status == "queued"
    assert saved_report.lane_id != "L-stale"
    assert saved_report.current_step.startswith("Respawned into isolated lane")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lane_attempt", "failure_attr", "expected_attempts_used", "expected_attempt_number"),
    [
        (1, "latest_verify_summary", 1, 2),
        (2, "latest_regression_summary", 2, 3),
    ],
)
async def test_recover_retryable_legacy_blocked_report_auto_respawns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lane_attempt: int,
    failure_attr: str,
    expected_attempts_used: int,
    expected_attempt_number: int,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-legacy", status="blocked", category="bug", summary="Checkout button does nothing")
    report.current_step = "Lane blocked"
    report.lane_id = "L-legacy"
    report.cluster_id = "C-legacy"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-legacy",
        report_ids=[report.report_id],
        lane_id="L-legacy",
        status="blocked",
        current_phase="blocked",
        attempt_number=lane_attempt,
    )
    lane_kwargs = {
        "lane_id": "L-legacy",
        "report_ids": [report.report_id],
        "category": "bug",
        "source_cluster_id": cluster.cluster_id,
        "status": "blocked",
        "workspace_root": str(tmp_path / "legacy-lane"),
        "lane_attempt": lane_attempt,
        "promotion_status": "blocked",
        "wait_reason": "Legacy blocked lane",
        failure_attr: "FAIL",
    }
    lane = BugflowLaneSnapshot(**lane_kwargs)

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_retryable_blocked_reports(runner, feature, [report], [lane])

    saved_old_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_old_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_old_lane.status == "superseded"
    assert saved_report.status == "queued"
    assert saved_report.attempts_used == expected_attempts_used
    assert saved_report.lane_id != lane.lane_id
    assert saved_cluster.status == "planned"
    assert saved_cluster.attempt_number == expected_attempt_number
    assert f"attempt {expected_attempts_used}/50 failed" in adapter.messages[-1][1].lower()


@pytest.mark.asyncio
async def test_recover_retryable_blocked_report_upgrades_stale_budget_and_respawns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-upgrade", status="blocked", category="bug", summary="Checkout button does nothing")
    report.current_step = "Lane blocked"
    report.lane_id = "L-upgrade"
    report.cluster_id = "C-upgrade"
    report.attempts_used = 3
    report.max_attempts = 3
    report.last_failed_lane_id = "L-upgrade"
    report.last_failure_kind = "lane-regression"
    report.last_failure_reason = "Regression still failing"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-upgrade",
        report_ids=[report.report_id],
        lane_id="L-upgrade",
        status="blocked",
        current_phase="blocked",
        attempt_number=3,
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-upgrade",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="blocked",
        workspace_root=str(tmp_path / "upgrade-lane"),
        lane_attempt=3,
        promotion_status="blocked",
        wait_reason="Regression still failing",
        latest_regression_summary="FAIL",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_retryable_blocked_reports(runner, feature, [report], [lane])

    saved_old_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_old_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_old_lane.status == "superseded"
    assert saved_report.status == "queued"
    assert saved_report.attempts_used == 3
    assert saved_report.max_attempts == 50


@pytest.mark.asyncio
async def test_recover_retryable_blocked_report_skips_human_attention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-human", status="blocked", category="bug", summary="Checkout button does nothing")
    report.current_step = "Lane blocked"
    report.lane_id = "L-human"
    report.cluster_id = "C-human"
    report.strategy_mode = "human_attention"
    report.last_failure_kind = "human_attention"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-human",
        report_ids=[report.report_id],
        lane_id="L-human",
        status="blocked",
        current_phase="blocked",
        strategy_mode="human_attention",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-human",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="blocked",
        workspace_root=str(tmp_path / "human-lane"),
        lane_attempt=4,
        promotion_status="blocked",
        wait_reason="The convergence strategist chose human attention.",
        latest_regression_summary="FAIL",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    respawned = False

    async def _fake_respawn(*_args, **_kwargs):
        nonlocal respawned
        respawned = True

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_respawn_lane_from_latest_main", _fake_respawn)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_retryable_blocked_reports(runner, feature, [report], [lane])

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert respawned is False
    assert saved_report.status == "blocked"
    assert saved_report.strategy_mode == "human_attention"
    assert saved_report.lane_id == lane.lane_id
    assert adapter.messages == []


@pytest.mark.asyncio
async def test_respawn_lane_applied_intent_repairs_cluster_and_report_pointers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-repair", status="queued", category="bug", summary="Repair pointers")
    report.cluster_id = "C-repair"
    report.lane_id = "L-old"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-repair",
        report_ids=[report.report_id],
        lane_id="L-old",
        status="queued",
        current_phase="planned",
    )
    old_lane = BugflowLaneSnapshot(
        lane_id="L-old",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="superseded",
        workspace_root=str(tmp_path / "old"),
    )
    new_lane = BugflowLaneSnapshot(
        lane_id="L-new",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="planned",
        current_phase="planned",
        wait_reason="Recovered lane",
        workspace_root=str(tmp_path / "new"),
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(old_lane.lane_id), old_lane.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(new_lane.lane_id), new_lane.model_dump_json(), feature=feature)

    runner = SimpleNamespace(artifacts=artifacts)
    await queue_module._save_respawn_intent(
        runner,
        feature,
        old_lane.lane_id,
        {
            "old_lane_id": old_lane.lane_id,
            "new_lane_id": new_lane.lane_id,
            "status": "applied",
        },
    )

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)

    await queue_module._respawn_lane_from_latest_main(
        runner,
        feature,
        old_lane,
        "Resume replay",
    )

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_report.lane_id == "L-new"
    assert saved_cluster.lane_id == "L-new"
    assert saved_cluster.status == "planned"


@pytest.mark.asyncio
async def test_apply_cluster_strategy_human_attention_preserves_blocked_cluster(
    monkeypatch: pytest.MonkeyPatch,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    report = _report("BR-human-apply", status="active_fix", category="bug", summary="Needs human attention")
    report.cluster_id = "C-human-apply"
    report.lane_id = "L-human-apply"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-human-apply",
        report_ids=[report.report_id],
        lane_id="L-human-apply",
        status="active_fix",
        current_phase="reverify",
        strategy_status="decided",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-human-apply",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        current_phase="reverify",
        workspace_root="/tmp/human-apply",
    )
    decision = RepairStrategyDecision(
        strategy_mode="human_attention",
        reasoning="Automation ran out of safe materially different moves.",
        stable_blockers=[Issue(severity="blocker", description="Manual reconciliation required")],
        new_blockers=[],
        failing_checks=[],
        stable_failure_family="manual reconciliation required",
        bundle_summary="No safe automated move remains.",
        scope_expansion=[],
        required_files=[],
        required_checks=[],
        required_evidence_modes=[],
        similar_cluster_hints=[],
        merge_recommendation="none",
        why_not_ordinary_retry="The same family keeps failing without a safe automated next step.",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)

    async def _fake_record_terminal_proof(*_args, **_kwargs):
        return None

    async def _fake_post_terminal_notice(*_args, **_kwargs):
        return None

    monkeypatch.setattr(queue_module, "_record_terminal_proof", _fake_record_terminal_proof)
    monkeypatch.setattr(queue_module, "_post_terminal_notice", _fake_post_terminal_notice)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
    )

    applied = await queue_module._apply_cluster_strategy(
        runner,
        feature,
        cluster,
        lane,
        [report],
        decision=decision,
        decision_key="bugflow-strategy:C-human-apply:2",
        failure_bundle_key="bugflow-failure-bundle:C-human-apply:2",
        failure_bundle={
            "strategy_round": 2,
            "stable_failure_family": "manual reconciliation required",
        },
        reason=decision.reasoning,
        failed_attempt=7,
        failure_kind="lane-regression",
        initial=False,
    )

    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert applied is False
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert saved_cluster.status == "blocked"
    assert saved_cluster.current_phase == "blocked"
    assert saved_cluster.strategy_status == "applied"
    assert saved_report.status == "blocked"


@pytest.mark.asyncio
async def test_decide_cluster_strategy_reuses_existing_decision_artifact():
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-reuse", status="active_fix", category="bug", summary="Reuse strategy")
    report.cluster_id = "C-reuse"
    report.lane_id = "L-reuse"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-reuse",
        report_ids=[report.report_id],
        lane_id="L-reuse",
        status="active_fix",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-reuse",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root="/tmp/reuse",
    )
    decision = _ordinary_strategy_decision(
        reasoning="Reuse the stored decision instead of asking again.",
        stable_failure_family="reused family",
    )
    decision_key = "bugflow-strategy:C-reuse:2"
    await artifacts.put(decision_key, decision.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        run=AsyncMock(side_effect=AssertionError("strategist should not run")),
    )

    loaded_key, loaded_decision = await queue_module._decide_cluster_strategy(
        runner,
        feature,
        cluster,
        lane,
        [report],
        failure_bundle_key="bugflow-failure-bundle:C-reuse:2",
        failure_bundle={
            "strategy_round": 2,
            "stable_failure_family": "reused family",
            "stable_blockers": [],
            "new_blockers": [],
            "failing_checks": [],
            "bundle_summary": "Reuse this decision",
            "similar_cluster_hints": [],
            "history_summary": "prior history",
            "detailed_attempts": [],
            "failure_reason": "same failure",
            "current_rca_summary": "same RCA",
        },
        reason="same failure",
    )

    assert loaded_key == decision_key
    assert loaded_decision.strategy_mode == "ordinary_retry"
    assert runner.run.await_count == 0


@pytest.mark.asyncio
async def test_recover_retryable_blocked_report_skips_when_sibling_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report_a = _report("BR-sibling-a", status="blocked", category="bug", summary="First report")
    report_a.current_step = "Lane blocked"
    report_a.lane_id = "L-sibling"
    report_a.cluster_id = "C-sibling"
    report_a.attempts_used = 2
    report_a.max_attempts = 50
    report_a.last_failure_kind = "lane-regression"

    report_b = _report("BR-sibling-b", status="blocked", category="bug", summary="Second report")
    report_b.current_step = "Lane blocked"
    report_b.lane_id = "L-sibling"
    report_b.cluster_id = "C-sibling"
    report_b.attempts_used = 50
    report_b.max_attempts = 50
    report_b.last_failure_kind = "lane-regression"

    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-sibling",
        report_ids=[report_a.report_id, report_b.report_id],
        lane_id="L-sibling",
        status="blocked",
        current_phase="blocked",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-sibling",
        report_ids=[report_a.report_id, report_b.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="blocked",
        workspace_root=str(tmp_path / "sibling-lane"),
        lane_attempt=3,
        promotion_status="blocked",
        latest_regression_summary="FAIL",
    )

    for report in [report_a, report_b]:
        await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    respawned = False

    async def _fake_respawn(*_args, **_kwargs):
        nonlocal respawned
        respawned = True

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_respawn_lane_from_latest_main", _fake_respawn)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_retryable_blocked_reports(runner, feature, [report_a, report_b], [lane])

    assert respawned is False


@pytest.mark.asyncio
async def test_recover_stale_promoting_lane_finalizes_if_commits_already_on_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-7002", status="active_fix", category="bug", summary="Checkout button does nothing")
    report.lane_id = "L-promoting"
    report.cluster_id = "C-promoting"
    lane_root = tmp_path / "stale-lane"
    lane_root.mkdir(parents=True, exist_ok=True)
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-promoting",
        report_ids=[report.report_id],
        lane_id="L-promoting",
        status="active_fix",
        current_phase="promoting",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-promoting",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="promoting",
        promotion_status="promoting",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={"frontend": "abc123"},
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    pushed = False

    async def _fake_push(_root):
        nonlocal pushed
        pushed = True

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_lane_commits_already_on_main", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(queue_module, "_push_clones_to_source_root", _fake_push)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_stale_execution_state(runner, feature, [lane])

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert pushed is True
    assert saved_lane.status == "promoted"
    assert saved_report.status == "resolved"
    assert saved_cluster.status == "resolved"


@pytest.mark.asyncio
async def test_recover_stale_promoting_lane_finalizes_if_marked_applied_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-applied", status="active_fix", category="bug", summary="Checkout button does nothing")
    report.lane_id = "L-applied"
    report.cluster_id = "C-applied"
    lane_root = tmp_path / "applied-lane"
    lane_root.mkdir(parents=True, exist_ok=True)
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-applied",
        report_ids=[report.report_id],
        lane_id="L-applied",
        status="active_fix",
        current_phase="promoting",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-applied",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="promoting",
        promotion_status="applied-main",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={"frontend": "abc123"},
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    pushed = False

    async def _fake_push(_root):
        nonlocal pushed
        pushed = True

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_lane_commits_already_on_main", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(queue_module, "_push_clones_to_source_root", _fake_push)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_stale_execution_state(runner, feature, [lane])

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert pushed is True
    assert saved_lane.status == "promoted"


def test_strategy_scope_override_adds_repo_lock_for_bare_repo_name():
    lane = queue_module.BugflowLaneSnapshot(
        lane_id="L-scope",
        report_ids=["BR-1"],
        category="bug",
        status="planned",
        lock_scope=[],
        repo_paths=[],
    )
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-scope",
        report_ids=["BR-1"],
    )
    decision = RepairStrategyDecision(
        strategy_mode="broaden_scope",
        reasoning="Need to widen to the full frontend repo.",
        stable_blockers=[],
        new_blockers=[],
        failing_checks=[],
        stable_failure_family="ui parity mismatch",
        bundle_summary="Broaden to repo scope.",
        scope_expansion=["frontend"],
        required_files=[],
        required_checks=[],
        required_evidence_modes=[],
        similar_cluster_hints=[],
        merge_recommendation="none",
        why_not_ordinary_retry="Repo-level lock is needed.",
    )

    lock_scope, repo_paths, affected_files = queue_module._strategy_scope_override(lane, cluster, decision)

    assert lock_scope == ["repo:frontend"]
    assert repo_paths == ["frontend"]
    assert affected_files == []


@pytest.mark.asyncio
async def test_promote_lane_rechecks_state_after_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    lane = BugflowLaneSnapshot(
        lane_id="L-promote",
        report_ids=["BR-8001"],
        category="bug",
        status="verified_pending_promotion",
        workspace_root=str(tmp_path / "lane"),
        base_main_commits_by_repo={"frontend": "abc123"},
    )
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)

    class _PromoteFeatureStore(_FeatureStore):
        @asynccontextmanager
        async def advisory_lock(self, _feature_id: str, _name: str):
            updated = queue_module.parse_model(
                artifacts.values[(feature.id, lane_key("L-promote"))],
                queue_module.BugflowLaneSnapshot,
            )
            assert isinstance(updated, queue_module.BugflowLaneSnapshot)
            updated.status = "promoted"
            updated.promotion_status = "pushed"
            await artifacts.put(lane_key(updated.lane_id), updated.model_dump_json(), feature=feature)
            yield None

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_PromoteFeatureStore(),
    )

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: tmp_path / "main" / "repos")

    phase = queue_module.BugflowQueuePhase()
    await phase._promote_lane(runner, feature, lane.lane_id)

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    promotion_queue = queue_module.parse_model(
        artifacts.values[(feature.id, "bugflow-promotion-queue")],
        queue_module.BugflowPromotionQueueSnapshot,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(promotion_queue, queue_module.BugflowPromotionQueueSnapshot)
    assert saved_lane.status == "promoted"
    assert promotion_queue.promoting_lane_id == ""
    assert promotion_queue.status_text == "Promotion idle"


@pytest.mark.asyncio
async def test_promote_lane_respawns_on_verification_failure_when_budget_remains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)
    lane_root = tmp_path / "lane"
    lane_root.mkdir(parents=True, exist_ok=True)
    promotion_root = tmp_path / "promotion"
    promotion_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-8002", status="active_fix", category="bug", summary="A")
    report.lane_id = "L-promote-fail"
    report.cluster_id = "C-promote-fail"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-promote-fail",
        report_ids=[report.report_id],
        lane_id="L-promote-fail",
        status="verified_pending_promotion",
        current_phase="reverify",
        attempt_number=1,
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-promote-fail",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="verified_pending_promotion",
        promotion_status="queued",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={"frontend": "abc123"},
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is RepairStrategyDecision:
            return _ordinary_strategy_decision(
                stable_blockers=[Issue(severity="major", description="Promotion verify failed", file="frontend/src/checkout.tsx")],
                bundle_summary="Retry promotion from a fresh lane.",
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    async def _fake_create_promotion_root(_main_root, _feature, _lane):
        return promotion_root, {}, {}

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        new_root = tmp_path / "lanes" / lane_id / "repos"
        new_root.mkdir(parents=True, exist_ok=True)
        return new_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    async def _fake_remove(*_args, **_kwargs):
        return None

    async def _fake_verify(*_args, **_kwargs):
        return Verdict(
            approved=False,
            summary="Promotion verify failed",
            concerns=[],
            suggestions=[],
            checks=[],
            gaps=[],
        )

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_promotion_worktree_root", _fake_create_promotion_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)
    monkeypatch.setattr(queue_module, "_lane_commit_sequences", lambda *_args, **_kwargs: asyncio.sleep(0, result={"frontend": ["abc"]}))
    monkeypatch.setattr(queue_module, "_cherry_pick_lane_commits", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(queue_module, "_promotion_verify_lane", _fake_verify)
    monkeypatch.setattr(queue_module, "_run_regression", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(queue_module, "_remove_worktree_root", _fake_remove)

    phase = queue_module.BugflowQueuePhase()
    await phase._promote_lane(runner, feature, lane.lane_id)

    saved_old_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_old_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_old_lane.status == "superseded"
    assert saved_report.status == "queued"
    assert saved_report.attempts_used == 1
    assert saved_report.last_failure_kind == "promotion-verify"
    assert saved_cluster.attempt_number == 2
    assert any("attempt 1/50 failed in promotion verification" in message[1].lower() for message in adapter.messages)
    assert any("switching to ordinary retry mode" in message[1].lower() for message in adapter.messages)


@pytest.mark.asyncio
async def test_retry_attempt_refreshes_latest_lane_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-8300", status="queued", category="bug", summary="Checkout button does nothing")
    report.thread_ts = "ts-BR-8300"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-8300",
        group_id="BG-8300",
        report_ids=[report.report_id],
        lane_id="L-8300",
    )
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)
    lane_root = tmp_path / "lanes" / "L-8300" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)
    lane = BugflowLaneSnapshot(
        lane_id="L-8300",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(lane_root),
        base_main_commits_by_repo={},
        latest_rca_keys=["bug-rca:test:BG-8300"],
        issue_summary="Checkout button does nothing",
        lane_attempt=1,
    )
    rca = RootCauseAnalysis(
        hypothesis="Missing submit handler",
        evidence=["Button click is never bound"],
        affected_files=["frontend/src/checkout.tsx"],
        proposed_approach="Restore submit binding",
        confidence="high",
    )
    trace_file = tmp_path / "trace.zip"
    trace_file.write_text("trace", encoding="utf-8")
    screenshot_file = tmp_path / "shot.png"
    screenshot_file.write_text("png", encoding="utf-8")

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put("bug-rca:test:BG-8300", rca.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ImplementationResult:
            return ImplementationResult(
                task_id="task-1",
                summary="Restored submit binding",
                files_modified=["frontend/src/checkout.tsx"],
            )
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=False,
                summary="Still failing",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
                proof=EvidenceBundle(
                    ui_involved=False,
                    evidence_modes=["repo"],
                    summary="Initial verify failed",
                    artifacts=[
                        EvidenceArtifact(kind="trace", label="trace.zip", local_path=str(trace_file)),
                        EvidenceArtifact(kind="screenshot", label="shot", local_path=str(screenshot_file)),
                    ],
                ),
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    runner.run = _fake_run

    async def _noop(*_args, **_kwargs):
        return None

    async def _fake_retry(*_args, **_kwargs):
        return BugFixAttempt(
            bug_id="L-8300-retry-1",
            group_id="BG-8300",
            source_verdict="lane-retry:L-8300",
            description="Checkout button does nothing",
            root_cause="Missing submit handler",
            fix_applied="Tried a second binding fix",
            files_modified=["frontend/src/checkout.tsx"],
            re_verify_result="FAIL",
            attempt_number=2,
        )

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_commit_repos_in_root", _noop)
    monkeypatch.setattr(queue_module, "_run_regression", _noop)
    monkeypatch.setattr(queue_module, "_append_bug_fix_attempts", _noop)
    monkeypatch.setattr(queue_module, "_resolve_fix_workspace_from_root", lambda *_args, **_kwargs: str(lane_root))
    monkeypatch.setattr(queue_module, "_single_rca_fix_verify", _fake_retry)

    phase = queue_module.BugflowQueuePhase()
    success = await phase._execute_bug_lane(runner, feature, lane)

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    latest_proof = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.proof_key(report.report_id, "lane-verify"))],
        queue_module.BugflowProofRecord,
    )
    assert success is False
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(latest_proof, queue_module.BugflowProofRecord)
    assert saved_report.latest_proof_key == queue_module.proof_key(report.report_id, "lane-verify")
    assert "Retry re-verify for L-8300 finished with FAIL." == latest_proof.bundle.summary
    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    retry_verify_key = "bug-reverify:lane-retry:L-8300:L-8300-retry-1"
    retry_verdict = queue_module.parse_model(
        artifacts.values[(feature.id, retry_verify_key)],
        queue_module.Verdict,
    )
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(retry_verdict, queue_module.Verdict)
    assert saved_lane.latest_verify_keys == [retry_verify_key]
    assert retry_verdict.summary == "Retry re-verify for L-8300 finished with FAIL."


@pytest.mark.asyncio
async def test_execute_bug_lane_clears_stale_regression_pointer_after_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-8301", status="queued", category="bug", summary="Checkout button does nothing")
    report.thread_ts = "ts-BR-8301"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-8301",
        group_id="BG-8301",
        report_ids=[report.report_id],
        lane_id="L-8301",
    )
    lane_root = tmp_path / "lanes" / "L-8301" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)
    lane = BugflowLaneSnapshot(
        lane_id="L-8301",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(lane_root),
        latest_rca_keys=["bug-rca:test:BG-8301"],
        issue_summary="Checkout button does nothing",
        lane_attempt=1,
    )
    rca = RootCauseAnalysis(
        hypothesis="Missing submit handler",
        evidence=["Button click is never bound"],
        affected_files=["frontend/src/checkout.tsx"],
        proposed_approach="Restore submit binding",
        confidence="high",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put("bug-rca:test:BG-8301", rca.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": _Adapter()},
        feature_store=_FeatureStore(),
    )

    async def _fake_run(task, *_args, **_kwargs):
        if isinstance(task, Ask) and task.output_type is ImplementationResult:
            return ImplementationResult(
                task_id="task-1",
                summary="Restored submit binding",
                files_modified=["frontend/src/checkout.tsx"],
            )
        if isinstance(task, Ask) and task.output_type is Verdict:
            return Verdict(
                approved=True,
                summary="Looks good before regression",
                concerns=[],
                suggestions=[],
                checks=[],
                gaps=[],
            )
        raise AssertionError(f"Unexpected task: {task!r}")

    async def _noop(*_args, **_kwargs):
        return None

    async def _fake_regression(*_args, **_kwargs):
        return Verdict(
            approved=False,
            summary="Regression still fails",
            concerns=[],
            suggestions=[],
            checks=[Check(criterion="regression suite", result="FAIL", detail="Existing flow broke")],
            gaps=[],
        )

    async def _fake_retry(*_args, **_kwargs):
        return BugFixAttempt(
            bug_id="L-8301-retry-1",
            group_id="BG-8301",
            source_verdict="lane-retry:L-8301",
            description="Checkout button does nothing",
            root_cause="Missing submit handler",
            fix_applied="Adjusted the retry fix",
            files_modified=["frontend/src/checkout.tsx"],
            re_verify_result="FAIL",
            attempt_number=2,
        )

    runner.run = _fake_run
    monkeypatch.setattr(queue_module, "_commit_repos_in_root", _noop)
    monkeypatch.setattr(queue_module, "_append_bug_fix_attempts", _noop)
    monkeypatch.setattr(queue_module, "_resolve_fix_workspace_from_root", lambda *_args, **_kwargs: str(lane_root))
    monkeypatch.setattr(queue_module, "_run_regression", _fake_regression)
    monkeypatch.setattr(queue_module, "_single_rca_fix_verify", _fake_retry)

    phase = queue_module.BugflowQueuePhase()
    success = await phase._execute_bug_lane(runner, feature, lane)

    saved_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(lane.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    retry_verify_key = "bug-reverify:lane-regression:L-8301:L-8301-retry-1"
    retry_verdict = queue_module.parse_model(
        artifacts.values[(feature.id, retry_verify_key)],
        queue_module.Verdict,
    )
    assert success is False
    assert isinstance(saved_lane, queue_module.BugflowLaneSnapshot)
    assert isinstance(retry_verdict, queue_module.Verdict)
    assert saved_lane.latest_verify_keys == [retry_verify_key]
    assert saved_lane.latest_regression_keys == []
    assert saved_lane.latest_regression_summary == ""
    assert retry_verdict.summary == "Regression retry for L-8301 finished with FAIL."


@pytest.mark.asyncio
async def test_retry_or_block_bug_lane_applies_broaden_scope_strategy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-8400", status="active_fix", category="bug", summary="Checkout button does nothing")
    report.lane_id = "L-8400"
    report.cluster_id = "C-8400"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-8400",
        report_ids=[report.report_id],
        lane_id="L-8400",
        status="active_fix",
        current_phase="reverify",
        affected_files=["frontend/src/checkout.tsx"],
        repo_paths=["frontend"],
        latest_rca_key="bug-rca:test:C-8400",
        latest_rca_summary="Frontend/backend parity mismatch",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-8400",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(tmp_path / "lane"),
        lock_scope=["file:frontend/src/checkout.tsx"],
        repo_paths=["frontend"],
        latest_rca_keys=["bug-rca:test:C-8400"],
        latest_rca_summary="Frontend/backend parity mismatch",
        latest_verify_summary="Still failing in validation",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put("bug-rca:test:C-8400", RootCauseAnalysis(
        hypothesis="Frontend/backend parity mismatch",
        evidence=["Validation still disagrees after local UI fix"],
        affected_files=["frontend/src/checkout.tsx", "backend/src/orders.py"],
        proposed_approach="Broaden scope to include backend parity surfaces.",
        confidence="high",
    ).model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    async def _fake_build_bundle(*_args, **_kwargs):
        return (
            "bugflow-failure-bundle:C-8400:1",
            {
                "cluster_id": "C-8400",
                "strategy_round": 1,
                "bundle_summary": "Repeated validation mismatch between frontend and backend parity checks.",
                "stable_failure_family": "checkout parity mismatch",
                "similar_cluster_ids": ["C-peer"],
                "similar_cluster_hints": ["C-peer: resolved with a broader frontend/backend parity fix."],
            },
        )

    async def _fake_decide(*_args, **_kwargs):
        return (
            "bugflow-strategy:C-8400:1",
            RepairStrategyDecision(
                strategy_mode="broaden_scope",
                reasoning="The same local fix keeps missing adjacent backend parity validation.",
                stable_blockers=[Issue(severity="major", description="Frontend/backend parity mismatch", file="frontend/src/checkout.tsx")],
                new_blockers=[],
                failing_checks=[Check(criterion="frontend/backend parity", result="FAIL", detail="Validation still disagrees after local UI fix")],
                stable_failure_family="checkout parity mismatch",
                bundle_summary="Broaden scope to include backend parity surfaces.",
                scope_expansion=[],
                required_files=["frontend/src/checkout.tsx", "backend/src/orders.py"],
                required_checks=["frontend/backend parity"],
                required_evidence_modes=["ui", "api"],
                similar_cluster_hints=["C-peer: resolved with a broader frontend/backend parity fix."],
                merge_recommendation="none",
                why_not_ordinary_retry="Local-only retries keep rediscovering the same parity blocker.",
            ),
        )

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)
    monkeypatch.setattr(queue_module, "_build_cluster_failure_bundle", _fake_build_bundle)
    monkeypatch.setattr(queue_module, "_decide_cluster_strategy", _fake_decide)

    continued = await queue_module._retry_or_block_bug_lane(
        runner,
        feature,
        lane,
        reason="Parity verification still fails",
        failure_kind="lane-verify",
    )

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(cluster.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert continued is True
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_report.strategy_mode == "broaden_scope"
    assert saved_report.latest_failure_bundle_key == "bugflow-failure-bundle:C-8400:1"
    assert saved_report.strategy_required_evidence_modes == ["ui", "api"]
    assert saved_cluster.strategy_mode == "broaden_scope"
    new_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(saved_report.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(new_lane, queue_module.BugflowLaneSnapshot)
    assert "file:frontend/src/checkout.tsx" in new_lane.lock_scope
    assert "file:backend/src/orders.py" in new_lane.lock_scope
    assert new_lane.latest_rca_keys == [cluster.latest_rca_key]
    assert new_lane.latest_rca_summary == cluster.latest_rca_summary


@pytest.mark.asyncio
async def test_recover_missing_rca_context_blocked_lane_respawns_non_counted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-rca-recover", status="blocked", category="bug", summary="Checkout button does nothing")
    report.current_step = "Lane blocked"
    report.lane_id = "L-rca-recover"
    report.cluster_id = "C-rca-recover"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-rca-recover",
        report_ids=[report.report_id],
        lane_id="L-rca-recover",
        status="blocked",
        current_phase="blocked",
        latest_rca_key="bug-rca:test:C-rca-recover",
        latest_rca_summary="Missing submit handler",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-rca-recover",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="blocked",
        workspace_root=str(tmp_path / "recover-lane"),
        lane_attempt=2,
        wait_reason="L-rca-recover is missing RCA context",
        latest_rca_keys=[],
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)
    await artifacts.put(cluster.latest_rca_key, RootCauseAnalysis(
        hypothesis="Missing submit handler",
        evidence=["Button click is never bound"],
        affected_files=["frontend/src/checkout.tsx"],
        proposed_approach="Restore submit binding",
        confidence="high",
    ).model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "def456"}

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)

    phase = queue_module.BugflowQueuePhase()
    await phase._recover_retryable_blocked_reports(runner, feature, [report], [lane])

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    new_lane = queue_module.parse_model(
        artifacts.values[(feature.id, lane_key(saved_report.lane_id))],
        queue_module.BugflowLaneSnapshot,
    )
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert isinstance(new_lane, queue_module.BugflowLaneSnapshot)
    assert saved_report.status == "queued"
    assert saved_report.attempts_used == 0
    assert new_lane.lane_id != lane.lane_id
    assert new_lane.latest_rca_keys == [cluster.latest_rca_key]


@pytest.mark.asyncio
async def test_plan_bug_reports_initializes_cluster_strategy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    report = _report("BR-8450", status="queued", category="bug", summary="Checkout button does nothing")
    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        feature_store=_FeatureStore(),
        services={"slack_adapter": _Adapter()},
        interaction_runtimes={"terminal": _RootRuntime()},
    )

    dispatch = PlannedBugDispatch(
        attempt_number=1,
        triage=BugTriage(
            groups=[
                BugGroup(
                    group_id="BG-8450",
                    likely_root_cause="selector/runtime contract mismatch",
                    issue_indices=[0],
                    severity="major",
                    affected_files_hint=["frontend/src/checkout.tsx"],
                )
            ]
        ),
        groups=[
            PlannedBugGroup(
                group=BugGroup(
                    group_id="BG-8450",
                    likely_root_cause="selector/runtime contract mismatch",
                    issue_indices=[0],
                    severity="major",
                    affected_files_hint=["frontend/src/checkout.tsx"],
                ),
                rca=RootCauseAnalysis(
                    hypothesis="Selector/runtime contract mismatch",
                    evidence=["Rendered selector disagrees with runtime contract"],
                    affected_files=["frontend/src/checkout.tsx"],
                    proposed_approach="Align selector/runtime contract across both surfaces",
                    confidence="high",
                ),
                issue_text="- [major] Checkout button does nothing",
                rca_key="bug-rca:test:BG-8450",
            )
        ],
        fixable_groups=[],
        contradiction_groups=[],
        schedule=[["BG-8450"]],
        dispatch_key="bug-dispatch:test:attempt-1",
    )
    dispatch.fixable_groups = list(dispatch.groups)

    async def _fake_plan(*_args, **_kwargs):
        return dispatch

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "abc123"}

    async def _fake_build_bundle(*_args, **_kwargs):
        return (
            "bugflow-failure-bundle:C-init:1",
            {
                "cluster_id": "C-init",
                "strategy_round": 1,
                "bundle_summary": "Cross-surface selector/runtime mismatch present from initial RCA.",
                "stable_failure_family": "selector runtime mismatch",
                "similar_cluster_ids": [],
                "similar_cluster_hints": [],
            },
        )

    async def _fake_decide(*_args, **_kwargs):
        return (
            "bugflow-strategy:C-init:1",
            RepairStrategyDecision(
                strategy_mode="contract_reconciliation",
                reasoning="The RCA already points to a consumer/provider mismatch across UI and runtime semantics.",
                stable_blockers=[Issue(severity="major", description="Selector/runtime contract mismatch", file="frontend/src/checkout.tsx")],
                new_blockers=[],
                failing_checks=[Check(criterion="selector/runtime contract", result="FAIL", detail="The contract is inconsistent before the first fix lane")],
                stable_failure_family="selector runtime mismatch",
                bundle_summary="Treat this as a contract reconciliation lane from the start.",
                scope_expansion=[],
                required_files=["frontend/src/checkout.tsx", "backend/src/runtime_contract.ts"],
                required_checks=["selector/runtime contract"],
                required_evidence_modes=["ui", "repo"],
                similar_cluster_hints=[],
                merge_recommendation="none",
                why_not_ordinary_retry="A plain local retry would miss the cross-surface contract work.",
            ),
        )

    monkeypatch.setattr(queue_module, "_plan_bug_groups", _fake_plan)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)
    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: tmp_path / "main" / "repos")
    monkeypatch.setattr(queue_module, "_build_cluster_failure_bundle", _fake_build_bundle)
    monkeypatch.setattr(queue_module, "_decide_cluster_strategy", _fake_decide)

    phase = queue_module.BugflowQueuePhase()
    await phase._plan_bug_reports(runner, feature, [report.report_id])

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    saved_cluster = queue_module.parse_model(
        artifacts.values[(feature.id, queue_module.cluster_key(saved_report.cluster_id))],
        queue_module.BugflowClusterSnapshot,
    )
    assert isinstance(saved_cluster, queue_module.BugflowClusterSnapshot)
    assert saved_report.strategy_mode == "contract_reconciliation"
    assert saved_report.latest_failure_bundle_key == "bugflow-failure-bundle:C-init:1"
    assert saved_cluster.strategy_mode == "contract_reconciliation"
    assert saved_cluster.strategy_decision_key == "bugflow-strategy:C-init:1"


@pytest.mark.asyncio
async def test_retry_or_block_bug_lane_minimizes_before_respawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    feature = _feature()
    artifacts = _Artifacts()
    adapter = _Adapter()
    main_root = tmp_path / "main" / "repos"
    main_root.mkdir(parents=True, exist_ok=True)

    report = _report("BR-8460", status="active_fix", category="bug", summary="Canvas drag is flaky")
    report.lane_id = "L-8460"
    report.cluster_id = "C-8460"
    cluster = queue_module.BugflowClusterSnapshot(
        cluster_id="C-8460",
        report_ids=[report.report_id],
        lane_id="L-8460",
        status="active_fix",
        current_phase="reverify",
    )
    lane = BugflowLaneSnapshot(
        lane_id="L-8460",
        report_ids=[report.report_id],
        category="bug",
        source_cluster_id=cluster.cluster_id,
        status="active_fix",
        workspace_root=str(tmp_path / "lane"),
        latest_verify_summary="Flaky drag flow still fails",
    )

    await artifacts.put(report_key(report.report_id), report.model_dump_json(), feature=feature)
    await artifacts.put(lane_key(lane.lane_id), lane.model_dump_json(), feature=feature)
    await artifacts.put(queue_module.cluster_key(cluster.cluster_id), cluster.model_dump_json(), feature=feature)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"slack_adapter": adapter},
        feature_store=_FeatureStore(),
    )

    async def _fake_create_lane_root(_main_root, _feature, lane_id):
        lane_root = tmp_path / "lanes" / lane_id / "repos"
        lane_root.mkdir(parents=True, exist_ok=True)
        return lane_root, {"frontend": f"lane/{lane_id}"}, {"frontend": "ghi789"}

    async def _fake_build_bundle(*_args, **_kwargs):
        return (
            "bugflow-failure-bundle:C-8460:1",
            {
                "cluster_id": "C-8460",
                "strategy_round": 1,
                "bundle_summary": "The failing UI path is still too broad and noisy.",
                "stable_failure_family": "flaky drag flow",
                "similar_cluster_ids": [],
                "similar_cluster_hints": [],
            },
        )

    async def _fake_decide(*_args, **_kwargs):
        return (
            "bugflow-strategy:C-8460:1",
            RepairStrategyDecision(
                strategy_mode="minimize_counterexample",
                reasoning="We need a smaller deterministic drag counterexample before another fix attempt.",
                stable_blockers=[Issue(severity="major", description="Flaky drag flow", file="frontend/src/canvas.tsx")],
                new_blockers=[],
                failing_checks=[Check(criterion="drag interaction", result="FAIL", detail="Still broad and noisy")],
                stable_failure_family="flaky drag flow",
                bundle_summary="Reduce the drag reproduction before the next fix lane.",
                scope_expansion=[],
                required_files=["frontend/src/canvas.tsx"],
                required_checks=["minimal drag reproduction"],
                required_evidence_modes=["ui"],
                similar_cluster_hints=[],
                merge_recommendation="none",
                why_not_ordinary_retry="Another broad retry would not teach us anything new.",
            ),
        )

    minimized = False

    async def _fake_minimize(*_args, **_kwargs):
        nonlocal minimized
        minimized = True
        return True

    monkeypatch.setattr(queue_module, "_get_feature_root", lambda *_args, **_kwargs: main_root)
    monkeypatch.setattr(queue_module, "_create_lane_worktree_root", _fake_create_lane_root)
    monkeypatch.setattr(queue_module, "_build_cluster_failure_bundle", _fake_build_bundle)
    monkeypatch.setattr(queue_module, "_decide_cluster_strategy", _fake_decide)
    monkeypatch.setattr(queue_module, "_minimize_cluster_counterexample", _fake_minimize)

    continued = await queue_module._retry_or_block_bug_lane(
        runner,
        feature,
        lane,
        reason="Drag flow is still noisy and flaky",
        failure_kind="lane-verify",
    )

    saved_report = queue_module.parse_model(
        artifacts.values[(feature.id, report_key(report.report_id))],
        queue_module.BugflowReportSnapshot,
    )
    assert continued is True
    assert minimized is True
    assert isinstance(saved_report, queue_module.BugflowReportSnapshot)
    assert saved_report.strategy_mode == "minimize_counterexample"
    assert saved_report.lane_id != lane.lane_id


@pytest.mark.asyncio
async def test_append_bug_fix_attempts_preserves_existing_entries():
    feature = _feature()
    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts, feature_store=_FeatureStore())

    existing = BugFixAttempt(
        bug_id="BUG-1",
        group_id="BG-1",
        source_verdict="integration",
        description="existing",
        root_cause="old",
        fix_applied="first fix",
        re_verify_result="PASS",
        attempt_number=1,
    )
    new_attempt = BugFixAttempt(
        bug_id="BUG-2",
        group_id="BG-2",
        source_verdict="integration",
        description="new",
        root_cause="new",
        fix_applied="second fix",
        re_verify_result="PASS",
        attempt_number=1,
    )
    await artifacts.put("bug-fix-attempts", "\n\n".join([json.dumps(existing.model_dump(mode="json"))]), feature=feature)

    await queue_module._append_bug_fix_attempts(runner, feature, [new_attempt])

    stored = queue_module._load_prior_attempts(await artifacts.get("bug-fix-attempts", feature=feature))
    assert [attempt.bug_id for attempt in stored] == ["BUG-1", "BUG-2"]


@pytest.mark.asyncio
async def test_append_decision_preserves_existing_entries():
    feature = _feature()
    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts, feature_store=_FeatureStore())

    await artifacts.put(
        "bugflow-decisions",
        json.dumps([{"decision_id": "D-1", "summary": "Existing"}]),
        feature=feature,
    )

    await queue_module._append_decision(
        runner,
        feature,
        BugflowDecisionRecord(
            decision_id="D-2",
            report_ids=["BR-2"],
            summary="New",
            approved=True,
        ),
    )

    stored = json.loads(await artifacts.get("bugflow-decisions", feature=feature) or "[]")
    assert [item["decision_id"] for item in stored] == ["D-1", "D-2"]


@pytest.mark.asyncio
async def test_write_queue_snapshot_tracks_active_and_promoting_lanes():
    feature = _feature()
    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts)

    reports = [
        _report("BR-1", status="active_fix", category="bug", summary="A"),
        _report("BR-2", status="pending_retriage", category="bug", summary="B"),
    ]
    lanes = [
        BugflowLaneSnapshot(
            lane_id="L-1",
            report_ids=["BR-1"],
            status="active_fix",
            lock_scope=["file:frontend/src/a.tsx"],
            workspace_root="/tmp/L-1",
        ),
        BugflowLaneSnapshot(
            lane_id="L-2",
            report_ids=["BR-2"],
            status="verified_pending_promotion",
            lock_scope=["file:backend/app.py"],
            workspace_root="/tmp/L-2",
        ),
        BugflowLaneSnapshot(
            lane_id="L-3",
            report_ids=["BR-3"],
            status="promoting",
            lock_scope=["file:backend/payments.py"],
            workspace_root="/tmp/L-3",
        ),
    ]
    clusters = []

    phase = queue_module.BugflowQueuePhase()
    queue = await phase._write_queue_snapshot(runner, feature, reports, lanes, clusters)

    assert queue.active_lane_ids == ["L-1"]
    assert queue.verified_pending_promotion_ids == ["L-2"]
    assert queue.promoting_lane_id == "L-3"
    assert queue.promotion_status_text == "Promoting L-3"
