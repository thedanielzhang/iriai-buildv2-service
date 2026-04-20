import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import (
    DecisionLedger,
    DecisionRecord,
    ReviewOutcome,
    RevisionPlan,
    Subfeature,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    Verdict,
)
from iriai_build_v2.services.artifacts import _key_to_path
from iriai_build_v2.services.markdown import to_markdown
from iriai_build_v2.workflows.planning._control import default_planning_control
from iriai_build_v2.workflows.planning._decisions import (
    GLOBAL_DECISIONS_KEY,
    build_decision_summary_text,
    compile_decision_ledger,
    parse_decision_ledger,
    refresh_decision_ledger,
    sync_compiled_decision_mirrors,
)
from iriai_build_v2.workflows.planning.phases.plan_review import (
    PlanReviewPhase,
    _build_sf_review_context,
    _persist_plan_review_decisions,
)
from iriai_build_v2.workflows.planning._stage_helpers import build_subfeature_context_text


class _Artifacts:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str, *, feature):
        del feature
        return self.values.get(key, "")

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.values[key] = value


class _Mirror:
    def __init__(self, base: Path) -> None:
        self.base = base

    def feature_dir(self, feature_id: str) -> Path:
        path = self.base / feature_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_artifact(self, feature_id: str, key: str, content: str) -> Path:
        path = self.feature_dir(feature_id) / _key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class _Runner:
    def __init__(self, *, artifacts, services=None, run_results=None) -> None:
        self.artifacts = artifacts
        self.services = services or {}
        self.feature_store = None
        self._run_results = list(run_results or [])

    async def run(self, task, feature, phase_name):
        del task, feature, phase_name
        if not self._run_results:
            raise AssertionError("Unexpected runner.run call")
        result = self._run_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_decision_artifact_paths_cover_root_broad_and_subfeature_ledgers():
    assert _key_to_path("decisions") == "decisions.md"
    assert _key_to_path("decisions:broad") == "broad/decisions.md"
    assert _key_to_path("decisions:global") == "global/decisions.md"
    assert _key_to_path("decisions:payments") == "subfeatures/payments/decisions.md"
    assert _key_to_path("decisions-summary:payments") == "subfeatures/payments/decisions-summary.md"


def test_decision_ledger_markdown_round_trip_and_summary():
    ledger = DecisionLedger(
        decisions=[
            DecisionRecord(id="D-1", statement="Use optimistic updates", source_phase="design"),
            DecisionRecord(
                id="D-2",
                statement="Retire the old polling endpoint",
                source_phase="plan-review",
                status="superseded",
                supersedes=["D-1"],
            ),
        ],
        complete=True,
    )

    markdown = to_markdown(ledger)
    parsed = parse_decision_ledger(markdown)
    summary = build_decision_summary_text(parsed, title="Decision Summary")

    assert [decision.id for decision in parsed.decisions] == ["D-1", "D-2"]
    assert parsed.decisions[1].status == "superseded"
    assert "D-1: Use optimistic updates" in summary


def test_review_outcome_drops_approval_when_new_decisions_exist():
    outcome = ReviewOutcome(
        approved=True,
        revision_plan=RevisionPlan(new_decisions=["Require signed URLs for export downloads."]),
        complete=True,
    )

    assert outcome.approved is False


@pytest.mark.asyncio
async def test_refresh_decision_ledger_allocates_ids_and_tracks_supersession(tmp_path):
    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        feature_store=None,
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    state = SimpleNamespace(metadata={})
    control = default_planning_control()

    await refresh_decision_ledger(
        runner,
        feature,
        ledger_key="decisions:broad",
        label="Broad Decision Ledger",
        source_phase="scoping",
        artifact_kind="scope",
        state=state,
        control=control,
        statements=["Use the existing tenant boundary model."],
    )
    await refresh_decision_ledger(
        runner,
        feature,
        ledger_key="decisions:broad",
        label="Broad Decision Ledger",
        source_phase="plan-review",
        artifact_kind="plan",
        state=state,
        control=control,
        statements=["Supersedes D-1: Use a shared multi-tenant boundary model."],
    )

    ledger = parse_decision_ledger(artifacts.values["decisions:broad"])

    assert control["decision_seq"] == 2
    assert [decision.id for decision in ledger.decisions] == ["D-1", "D-2"]
    assert ledger.decisions[0].status == "superseded"
    assert ledger.decisions[1].supersedes == ["D-1"]


@pytest.mark.asyncio
async def test_sync_compiled_decision_mirrors_updates_plan_and_system_design(tmp_path):
    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        feature_store=None,
    )
    feature = SimpleNamespace(id="feat-1", metadata={})

    ledger = DecisionLedger(
        decisions=[DecisionRecord(id="D-1", statement="Use event sourcing", source_phase="plan")],
        complete=True,
    )
    artifacts.values["decisions"] = to_markdown(ledger)
    artifacts.values["plan"] = "# Technical Plan\n\n## Architecture\n\nPlan body.\n"
    artifacts.values["system-design"] = SystemDesign(
        title="System Design",
        overview="Overview",
        complete=True,
    ).model_dump_json(indent=2)

    plan_text, system_design_text = await sync_compiled_decision_mirrors(runner, feature)
    system_design = SystemDesign.model_validate(json.loads(system_design_text))

    assert "**D-1**: Use event sourcing" in plan_text
    assert system_design.decisions == ["D-1: Use event sourcing"]


@pytest.mark.asyncio
async def test_compile_decision_ledger_merges_global_and_migrates_legacy_compiled_decisions(tmp_path):
    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        feature_store=None,
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    state = SimpleNamespace(metadata={})
    control = default_planning_control()
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments")],
        complete=True,
    )

    artifacts.values["decisions:broad"] = to_markdown(
        DecisionLedger(
            decisions=[DecisionRecord(id="D-1", statement="Use Redis", source_phase="broad-prd")],
            complete=True,
        )
    )
    artifacts.values["decisions:payments"] = to_markdown(
        DecisionLedger(
            decisions=[DecisionRecord(id="D-2", statement="Expose payment intent ids", source_phase="subfeature-pm", subfeature_slug="payments")],
            complete=True,
        )
    )
    artifacts.values["decisions"] = to_markdown(
        DecisionLedger(
            decisions=[
                DecisionRecord(id="D-1", statement="Use Redis", source_phase="broad-prd"),
                DecisionRecord(id="D-2", statement="Expose payment intent ids", source_phase="subfeature-pm", subfeature_slug="payments"),
                DecisionRecord(id="D-3", statement="Require audit events for export jobs", source_phase="plan-review"),
            ],
            complete=True,
        )
    )

    compiled_text = await compile_decision_ledger(
        runner,
        feature,
        phase_name="subfeature",
        decomposition=decomposition,
        state=state,
        control=control,
    )
    rebuilt_text = await compile_decision_ledger(
        runner,
        feature,
        phase_name="subfeature",
        decomposition=decomposition,
        state=state,
        control=control,
    )

    global_ledger = parse_decision_ledger(artifacts.values[GLOBAL_DECISIONS_KEY])
    compiled_ledger = parse_decision_ledger(compiled_text)

    assert [decision.id for decision in global_ledger.decisions] == ["D-3"]
    assert [decision.id for decision in compiled_ledger.decisions] == ["D-1", "D-2", "D-3"]
    assert rebuilt_text == artifacts.values["decisions"]


@pytest.mark.asyncio
async def test_refresh_decision_ledger_supports_system_design_sources(tmp_path):
    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        feature_store=None,
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    state = SimpleNamespace(metadata={})
    control = default_planning_control()

    plan_text = TechnicalPlan(architecture="Plan", complete=True).model_dump_json(indent=2)
    system_design_text = SystemDesign(
        title="System Design",
        overview="Overview",
        decisions=["D-9: Publish webhook retries to the outbox"],
        complete=True,
    ).model_dump_json(indent=2)

    await refresh_decision_ledger(
        runner,
        feature,
        ledger_key="decisions:payments",
        label="Decision Ledger — payments",
        source_phase="subfeature-architecture",
        artifact_kind="plan",
        state=state,
        control=control,
        subfeature_slug="payments",
        source_artifacts=[("plan", plan_text), ("system-design", system_design_text)],
    )

    ledger = parse_decision_ledger(artifacts.values["decisions:payments"])
    assert [decision.statement for decision in ledger.decisions] == ["Publish webhook retries to the outbox"]


@pytest.mark.asyncio
async def test_plan_review_new_decisions_persist_to_global_and_compiled_ledgers(tmp_path):
    artifacts = _Artifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        feature_store=None,
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    state = SimpleNamespace(
        metadata={},
        plan="# Technical Plan\n\n## Architecture\n\nPlan body.\n",
        system_design=SystemDesign(title="System Design", overview="Overview", complete=True).model_dump_json(indent=2),
    )
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments")],
        complete=True,
    )

    await _persist_plan_review_decisions(
        runner,
        feature,
        state,
        decomposition,
        ["Require signed URLs for export downloads."],
    )

    global_ledger = parse_decision_ledger(artifacts.values[GLOBAL_DECISIONS_KEY])
    compiled_ledger = parse_decision_ledger(artifacts.values["decisions"])

    assert [decision.statement for decision in global_ledger.decisions] == ["Require signed URLs for export downloads."]
    assert [decision.statement for decision in compiled_ledger.decisions] == ["Require signed URLs for export downloads."]
    assert "**D-1**: Require signed URLs for export downloads." in state.plan
    assert "D-1: Require signed URLs for export downloads." in state.system_design


@pytest.mark.asyncio
async def test_plan_review_execute_persists_decisions_before_live_approval_break(tmp_path, monkeypatch):
    artifacts = _Artifacts()
    runner = _Runner(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        run_results=[
            SimpleNamespace(
                output=ReviewOutcome.model_construct(
                    approved=True,
                    revision_plan=RevisionPlan(
                        new_decisions=["Require signed URLs for export downloads."]
                    ),
                    complete=True,
                )
            ),
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
        ],
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments")],
        complete=True,
    )
    state = SimpleNamespace(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        prd="",
        design="",
        plan="# Technical Plan\n\n## Architecture\n\nPlan body.\n",
        system_design=SystemDesign(title="System Design", overview="Overview", complete=True).model_dump_json(indent=2),
    )
    artifacts.values["plan-review-cycle-1"] = "# Review Report\n\nFindings remain.\n"

    async def _return_state(self, runner_arg, feature_arg, state_arg, decomposition_arg):
        del self, runner_arg, feature_arg, decomposition_arg
        return state_arg

    monkeypatch.setattr(PlanReviewPhase, "_run_gates", _return_state)

    result = await PlanReviewPhase().execute(runner, feature, state)

    global_ledger = parse_decision_ledger(artifacts.values[GLOBAL_DECISIONS_KEY])
    compiled_ledger = parse_decision_ledger(artifacts.values["decisions"])

    assert result is state
    assert [decision.statement for decision in global_ledger.decisions] == ["Require signed URLs for export downloads."]
    assert [decision.statement for decision in compiled_ledger.decisions] == ["Require signed URLs for export downloads."]


@pytest.mark.asyncio
async def test_plan_review_execute_recovers_markdown_decisions_before_approved_break(tmp_path, monkeypatch):
    artifacts = _Artifacts()
    runner = _Runner(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        run_results=[
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
        ],
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments")],
        complete=True,
    )
    state = SimpleNamespace(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        prd="",
        design="",
        plan="# Technical Plan\n\n## Architecture\n\nPlan body.\n",
        system_design=SystemDesign(title="System Design", overview="Overview", complete=True).model_dump_json(indent=2),
    )
    artifacts.values["plan-review-cycle-1"] = "# Review Report\n\nFindings remain.\n"
    artifacts.values["plan-review-discussion-1"] = (
        "**Outcome:** No changes needed\n\n"
        "### New Decisions\n"
        "- Require signed URLs for export downloads.\n"
    )

    async def _return_state(self, runner_arg, feature_arg, state_arg, decomposition_arg):
        del self, runner_arg, feature_arg, decomposition_arg
        return state_arg

    monkeypatch.setattr(PlanReviewPhase, "_run_gates", _return_state)

    await PlanReviewPhase().execute(runner, feature, state)
    await PlanReviewPhase().execute(runner, feature, state)

    global_ledger = parse_decision_ledger(artifacts.values[GLOBAL_DECISIONS_KEY])
    compiled_ledger = parse_decision_ledger(artifacts.values["decisions"])

    assert runner._run_results == []
    assert [decision.id for decision in global_ledger.decisions] == ["D-1"]
    assert [decision.statement for decision in global_ledger.decisions] == ["Require signed URLs for export downloads."]
    assert [decision.id for decision in compiled_ledger.decisions] == ["D-1"]


@pytest.mark.asyncio
async def test_plan_review_execute_recovers_markdown_decisions_from_malformed_nested_json(tmp_path, monkeypatch):
    artifacts = _Artifacts()
    runner = _Runner(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(tmp_path)},
        run_results=[
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
        ],
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments")],
        complete=True,
    )
    state = SimpleNamespace(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        prd="",
        design="",
        plan="# Technical Plan\n\n## Architecture\n\nPlan body.\n",
        system_design=SystemDesign(title="System Design", overview="Overview", complete=True).model_dump_json(indent=2),
    )
    artifacts.values["plan-review-cycle-1"] = "# Review Report\n\nFindings remain.\n"
    artifacts.values["plan-review-discussion-1"] = (
        "```json\n"
        "{\n"
        '  "output": {\n'
        '    "approved": false,\n'
        '    "revision_plan": {\n'
        '      "new_decisions": "Require signed URLs for export downloads."\n'
        "    },\n"
        '    "complete": true\n'
        "  }\n"
        "}\n"
        "```\n\n"
        "### New Decisions\n"
        "- Require signed URLs for export downloads.\n"
    )

    async def _return_state(self, runner_arg, feature_arg, state_arg, decomposition_arg):
        del self, runner_arg, feature_arg, decomposition_arg
        return state_arg

    monkeypatch.setattr(PlanReviewPhase, "_run_gates", _return_state)

    await PlanReviewPhase().execute(runner, feature, state)

    global_ledger = parse_decision_ledger(artifacts.values[GLOBAL_DECISIONS_KEY])
    compiled_ledger = parse_decision_ledger(artifacts.values["decisions"])

    assert runner._run_results == []
    assert [decision.statement for decision in global_ledger.decisions] == ["Require signed URLs for export downloads."]
    assert [decision.statement for decision in compiled_ledger.decisions] == ["Require signed URLs for export downloads."]


@pytest.mark.asyncio
async def test_plan_review_execute_mirror_only_recovery_is_idempotent(tmp_path, monkeypatch):
    artifacts = _Artifacts()
    mirror = _Mirror(tmp_path)
    runner = _Runner(
        artifacts=artifacts,
        services={"artifact_mirror": mirror},
        run_results=[
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
            Verdict(approved=True, summary="Looks good"),
        ],
    )
    feature = SimpleNamespace(id="feat-1", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments")],
        complete=True,
    )
    state = SimpleNamespace(
        metadata={},
        decomposition=decomposition.model_dump_json(indent=2),
        prd="",
        design="",
        plan="# Technical Plan\n\n## Architecture\n\nPlan body.\n",
        system_design=SystemDesign(title="System Design", overview="Overview", complete=True).model_dump_json(indent=2),
    )
    artifacts.values["plan-review-cycle-1"] = "# Review Report\n\nFindings remain.\n"
    mirror.write_artifact(
        feature.id,
        "plan-review-discussion-1",
        "**Outcome:** No changes needed\n\n"
        "### New Decisions\n"
        "- Require signed URLs for export downloads.\n",
    )

    async def _return_state(self, runner_arg, feature_arg, state_arg, decomposition_arg):
        del self, runner_arg, feature_arg, decomposition_arg
        return state_arg

    monkeypatch.setattr(PlanReviewPhase, "_run_gates", _return_state)

    await PlanReviewPhase().execute(runner, feature, state)
    await PlanReviewPhase().execute(runner, feature, state)

    global_ledger = parse_decision_ledger(artifacts.values[GLOBAL_DECISIONS_KEY])
    compiled_ledger = parse_decision_ledger(artifacts.values["decisions"])

    assert runner._run_results == []
    assert "plan-review-discussion-1" in artifacts.values
    assert [decision.id for decision in global_ledger.decisions] == ["D-1"]
    assert [decision.id for decision in compiled_ledger.decisions] == ["D-1"]


@pytest.mark.asyncio
async def test_plan_review_context_uses_canonical_decisions_over_stale_summaries():
    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts)
    feature = SimpleNamespace(id="feat-1", metadata={})
    decomposition = SubfeatureDecomposition(
        subfeatures=[
            Subfeature(id="SF-1", slug="payments", name="Payments", description="Payments"),
            Subfeature(id="SF-2", slug="exports", name="Exports", description="Exports"),
        ],
        complete=True,
    )
    artifacts.values.update(
        {
            "prd:payments": "payments prd",
            "decisions:payments": "payments decisions",
            "decisions": "canonical decisions",
            "decisions-summary:exports": "stale summary",
        }
    )

    context = await _build_sf_review_context(runner, feature, "payments", decomposition)

    assert "## CANONICAL DECISIONS" in context
    assert "canonical decisions" in context
    assert "stale summary" not in context


def test_build_subfeature_context_text_accepts_decision_sections():
    context = build_subfeature_context_text(
        SimpleNamespace(edges=[]),
        "payments",
        broad_sections=[("Broad PRD", "Broad text")],
        own_sections=[("Current Subfeature PRD", "Own text")],
        stage_artifacts={},
        stage_summaries={},
        decision_sections=[("Broad Decision Ledger", "- D-1: Use optimistic updates")],
    )

    assert "## Broad Decision Ledger" in context
    assert "D-1: Use optimistic updates" in context
