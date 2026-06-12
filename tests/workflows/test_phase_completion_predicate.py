"""(W-PR) FALSE-COMPLETE defect-class pins.

Live evidence (2026-06-11, feature 5b280bb4, develop12/13/14): a quiesced /
workflow_blocked implementation phase was reported as a completed run because

1. the phase persisted a final-looking ``implementation`` + ``handover`` pair
   unconditionally before checking ``terminal_state``;
2. ``resume_workflow`` deliberately swallows ``WorkflowQuiesced`` and the CLI
   printed "Workflow resume complete!" unconditionally afterwards.

These tests pin the additive guards:

- the per-task crash handler keeps retrying with budget remaining (the loop
  never exits on a first-attempt crash);
- the phase-completion predicate refuses a success-shaped dispatch-loop return
  that covers fewer tasks than the effective DAG order;
- the persisted handover carries ``phase_state`` ("in_progress" on every
  non-complete exit) and resume reads it as non-terminal; legacy g0-era rows
  (no marker) parse as UNKNOWN — never as completion proof;
- the legitimate full-completion path still completes;
- the CLI outcome banner never reports a quiesced workflow as complete.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose import Workspace

from iriai_build_v2.models.outputs import (
    HandoverDoc,
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
)
from iriai_build_v2.models.state import BuildState
from iriai_build_v2.workflows._runner import TrackedWorkflowRunner, WorkflowQuiesced
from iriai_build_v2.workflows.develop.phases import (
    implementation as implementation_module,
)


# ── Minimal stubs (mirrors tests/workflows/test_workflow_quiesce.py) ─────────


class _FeatureStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def transition_phase(self, feature_id: str, new_phase: str) -> None:
        del feature_id, new_phase

    async def log_event(
        self,
        feature_id: str,
        event_type: str,
        source: str,
        content: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        del feature_id
        self.events.append(
            {
                "event_type": event_type,
                "source": source,
                "content": content,
                "metadata": metadata or {},
            }
        )


class _Artifacts:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store = dict(initial or {})

    async def get(self, key: str, *, feature) -> str:
        del feature
        return self.store.get(key, "")

    async def get_record(self, key: str, *, feature):
        del feature
        value = self.store.get(key)
        if value is None:
            return None
        return {"id": 1, "value": value, "created_at": "now"}

    async def put(self, key: str, value: str, *, feature) -> None:
        del feature
        self.store[key] = value


class _ContextProvider:
    async def resolve(self, *_args, **_kwargs) -> str:
        return ""


class _Runtime:
    name = "fake"


def _feature(feature_id: str = "feat-wpr") -> SimpleNamespace:
    return SimpleNamespace(
        id=feature_id,
        workspace_id="main",
        name="Feature",
        slug="feat-wpr",
        metadata={},
    )


def _tracked_runner(artifacts: _Artifacts) -> TrackedWorkflowRunner:
    return TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        agent_runtime=_Runtime(),
        secondary_runtime=None,
        interaction_runtimes={"terminal": object()},
        artifacts=artifacts,
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )


class _LoopRunner:
    """Bare runner for driving `_implement_dag` directly (no workspace mgr)."""

    def __init__(self, artifacts: _Artifacts) -> None:
        self.artifacts = artifacts
        self.services: dict[str, object] = {}


def _dag(task_ids: list[str], execution_order: list[list[str]]) -> ImplementationDAG:
    return ImplementationDAG(
        tasks=[
            ImplementationTask(id=tid, name=tid, description=tid)
            for tid in task_ids
        ],
        execution_order=execution_order,
        complete=True,
    )


def _stub_dispatch_loop_collaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop_async(*args, **kwargs):
        del args, kwargs
        return None

    async def _approved_verify(runner, feature, group_idx, group_tasks, *a, **k):
        del runner, feature, group_idx, group_tasks, a, k
        return True, ""

    async def _no_enhancement(*args, **kwargs):
        del args, kwargs
        return ""

    async def _fake_compile_contracts(*args, **kwargs):
        del args, kwargs
        return implementation_module.TaskContractCompileOutcome()

    monkeypatch.setattr(
        implementation_module, "_ensure_task_worktrees", _noop_async
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_async)
    monkeypatch.setattr(
        implementation_module, "_run_enhancement_group", _no_enhancement
    )
    monkeypatch.setattr(
        implementation_module, "_verify_and_fix_group", _approved_verify
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _fake_compile_contracts,
    )
    monkeypatch.setattr(
        implementation_module,
        "_resolve_task_dispatch_repo_binding",
        lambda **_kwargs: implementation_module._TaskDispatchRepoBinding(
            repo_id="",
            repo_path="",
            ws_path="",
            source="test",
        ),
    )


# ── (i) crash with retries remaining keeps the per-task loop alive ──────────


@pytest.mark.asyncio
async def test_task_crash_with_retries_remaining_continues_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-attempt crash (TimeoutError with empty str — the observed
    develop14 class) must advance to the next attempt, not exit the loop.
    With the retry succeeding, the DAG completes cleanly."""
    dag = _dag(["T1"], [["T1"]])
    artifacts = _Artifacts({"dag": dag.model_dump_json()})
    runner = _LoopRunner(artifacts)
    _stub_dispatch_loop_collaborators(monkeypatch)

    dispatch_attempts: list[int] = []

    async def _fake_dispatch(*, runner, feature, task, attempt, **kwargs):
        del runner, feature, kwargs
        dispatch_attempts.append(attempt)
        if len(dispatch_attempts) == 1:
            raise TimeoutError("")
        return (
            ImplementationResult(task_id=task.id, summary="done"),
            SimpleNamespace(status="succeeded", attempt_id=99),
        )

    monkeypatch.setattr(
        implementation_module,
        "_dispatch_task_attempt_via_runtime_dispatcher",
        _fake_dispatch,
    )

    outcome = await implementation_module._implement_dag(
        runner, _feature(), dag
    )

    assert dispatch_attempts == [0, 1], (
        "the attempt-0 crash must continue the retry loop, not exit it"
    )
    assert outcome.failure == ""
    assert outcome.terminal_state == "complete"


# ── (ii) the completion predicate rejects partial coverage ──────────────────


@pytest.mark.asyncio
async def test_predicate_rejects_result_covering_fewer_tasks() -> None:
    dag = _dag(["T1", "T2", "T3"], [["T1", "T2", "T3"]])
    runner = _LoopRunner(_Artifacts())
    all_results: list[object] = [
        ImplementationResult(task_id="T1", summary="done"),
    ]

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, all_results
    )

    assert gap.startswith("phase_completion_predicate: 2/3 tasks")
    assert "T2" in gap and "T3" in gap and "T1" not in gap.split("missing:")[1]


@pytest.mark.asyncio
async def test_predicate_accepts_durable_dag_task_markers() -> None:
    dag = _dag(["T1", "T2"], [["T1", "T2"]])
    marker = ImplementationResult(task_id="T2", summary="done").model_dump_json()
    runner = _LoopRunner(_Artifacts({"dag-task:T2": marker}))
    all_results: list[object] = [
        ImplementationResult(task_id="T1", summary="done"),
    ]

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, all_results
    )

    assert gap == ""


@pytest.mark.asyncio
async def test_predicate_rejects_non_completed_marker_and_pending_merge() -> None:
    dag = _dag(["T1", "T2"], [["T1", "T2"]])
    blocked_marker = ImplementationResult(
        task_id="T2", summary="blocked", status="blocked"
    ).model_dump_json()
    artifacts = _Artifacts(
        {
            "dag-task:T2": blocked_marker,
            # Uncovered task with a stranded pending-merge marker: named in
            # the gap.
            implementation_module._pending_merge_queue_marker_key("T2"): "pending",
            # Covered task with a lingering post-seal marker (best-effort
            # sweep miss): warn-only, never a block.
            implementation_module._pending_merge_queue_marker_key("T1"): "pending",
        }
    )
    runner = _LoopRunner(artifacts)
    all_results: list[object] = [
        ImplementationResult(task_id="T1", summary="done"),
    ]

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, all_results
    )

    assert "1/2 tasks lack terminal-success state" in gap
    assert "T2" in gap
    assert "dag-task-pending-merge" in gap
    assert "T1" not in gap


@pytest.mark.asyncio
async def test_predicate_warns_but_passes_stale_marker_on_covered_task() -> None:
    dag = _dag(["T1"], [["T1"]])
    artifacts = _Artifacts(
        {implementation_module._pending_merge_queue_marker_key("T1"): "pending"}
    )
    runner = _LoopRunner(artifacts)
    all_results: list[object] = [
        ImplementationResult(task_id="T1", summary="done"),
    ]

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, all_results
    )

    assert gap == ""


@pytest.mark.asyncio
async def test_predicate_trusts_unlisted_legacy_group_seals() -> None:
    """Strict-adoption / legacy seals project empty-bodied `dag-group:*`
    checkpoints (no results, no task_ids). The dispatch loop already trusts
    those seals to skip the group, so the predicate accepts them as
    terminal-success evidence for the group's tasks."""
    dag = _dag(["T1", "T2"], [["T1", "T2"]])
    artifacts = _Artifacts(
        {"dag-group:0": json.dumps({"group_idx": 0, "results": []})}
    )
    runner = _LoopRunner(artifacts)

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, []
    )

    assert gap == ""


@pytest.mark.asyncio
async def test_predicate_rejects_listing_checkpoint_that_omits_a_task() -> None:
    """A checkpoint that DOES list per-task coverage and omits a task is not
    evidence for it — the stale/partial rehydration class."""
    dag = _dag(["T1", "T2"], [["T1", "T2"]])
    checkpoint = json.dumps(
        {
            "group_idx": 0,
            "results": [
                ImplementationResult(task_id="T1", summary="done").model_dump()
            ],
        }
    )
    artifacts = _Artifacts({"dag-group:0": checkpoint})
    runner = _LoopRunner(artifacts)

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, []
    )

    assert "1/2 tasks lack terminal-success state" in gap
    assert "T2" in gap


@pytest.mark.asyncio
async def test_predicate_fails_closed_on_store_error() -> None:
    dag = _dag(["T1"], [["T1"]])

    class _BrokenArtifacts(_Artifacts):
        async def get(self, key: str, *, feature) -> str:
            raise TimeoutError("store unavailable")

    runner = _LoopRunner(_BrokenArtifacts())

    gap = await implementation_module._phase_completion_predicate_gap(
        runner, _feature(), dag, []
    )

    assert "phase_completion_predicate" in gap
    assert "refusing to conclude" in gap


@pytest.mark.asyncio
async def test_dispatch_loop_blocks_stale_partial_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Occurrence-1/2 class: checkpoint rehydration marks every group done but
    covers only a fraction of the DAG's tasks. The loop must return a typed
    workflow blocker, never the success-shaped outcome, and must not reach
    the enhancement group."""
    dag = _dag(["T1", "T2"], [["T1", "T2"]])
    checkpoint = json.dumps(
        {
            "group_idx": 0,
            "commit_hash": "abc123",
            "results": [
                ImplementationResult(task_id="T1", summary="done").model_dump()
            ],
        }
    )
    artifacts = _Artifacts(
        {"dag": dag.model_dump_json(), "dag-group:0": checkpoint}
    )
    runner = _LoopRunner(artifacts)
    _stub_dispatch_loop_collaborators(monkeypatch)

    async def _none_first_existing(*args, **kwargs):
        del args, kwargs
        return None

    async def _fresh(*args, **kwargs):
        del args, kwargs
        return True

    async def _projected(*args, **kwargs):
        del args, kwargs
        return True, ""

    async def _must_not_run_enhancement(*args, **kwargs):
        raise AssertionError(
            "enhancement group must not run on a failed completion predicate"
        )

    monkeypatch.setattr(
        implementation_module,
        "_first_existing_dag_group_idx",
        _none_first_existing,
    )
    monkeypatch.setattr(
        implementation_module, "_dag_group_checkpoint_is_fresh", _fresh
    )
    monkeypatch.setattr(
        implementation_module,
        "_ensure_dag_group_checkpoint_projection_for_resume",
        _projected,
    )
    monkeypatch.setattr(
        implementation_module,
        "_run_enhancement_group",
        _must_not_run_enhancement,
    )

    outcome = await implementation_module._implement_dag(
        runner, _feature(), dag
    )

    assert outcome.terminal_state == "workflow_blocked"
    assert "phase_completion_predicate" in outcome.failure
    assert "1/2 tasks" in outcome.failure
    assert "T2" in outcome.failure


# ── (iv) the legitimate full-completion path still completes ─────────────────


@pytest.mark.asyncio
async def test_dispatch_loop_completes_when_checkpoints_cover_all_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = _dag(["T1", "T2"], [["T1", "T2"]])
    checkpoint = json.dumps(
        {
            "group_idx": 0,
            "commit_hash": "abc123",
            "results": [
                ImplementationResult(task_id="T1", summary="done").model_dump(),
                ImplementationResult(task_id="T2", summary="done").model_dump(),
            ],
        }
    )
    artifacts = _Artifacts(
        {"dag": dag.model_dump_json(), "dag-group:0": checkpoint}
    )
    runner = _LoopRunner(artifacts)
    _stub_dispatch_loop_collaborators(monkeypatch)

    async def _none_first_existing(*args, **kwargs):
        del args, kwargs
        return None

    async def _fresh(*args, **kwargs):
        del args, kwargs
        return True

    async def _projected(*args, **kwargs):
        del args, kwargs
        return True, ""

    monkeypatch.setattr(
        implementation_module,
        "_first_existing_dag_group_idx",
        _none_first_existing,
    )
    monkeypatch.setattr(
        implementation_module, "_dag_group_checkpoint_is_fresh", _fresh
    )
    monkeypatch.setattr(
        implementation_module,
        "_ensure_dag_group_checkpoint_projection_for_resume",
        _projected,
    )

    outcome = await implementation_module._implement_dag(
        runner, _feature(), dag
    )

    assert outcome.failure == ""
    assert outcome.terminal_state == "complete"


# ── (iii) in-progress persist shape + resume reads it as non-terminal ───────


def test_handover_payload_phase_state_roundtrip() -> None:
    handover = HandoverDoc()
    handover.record_success(
        ImplementationResult(task_id="T1", summary="done")
    )

    payload = implementation_module._handover_artifact_payload(
        handover,
        phase_state="in_progress",
        terminal_state="workflow_blocked",
        tasks_total=53,
    )

    state = implementation_module._handover_artifact_phase_state(payload)
    assert state == "in_progress"
    assert state != "complete"  # resume must treat it as non-terminal
    data = json.loads(payload)
    assert data["phase_progress"] == {
        "terminal_state": "workflow_blocked",
        "tasks_completed": 1,
        "tasks_total": 53,
    }
    # Existing readers still parse the artifact as a HandoverDoc (extras
    # ignored) — the artifact key/shape stays compatible.
    parsed = HandoverDoc.model_validate_json(payload)
    assert parsed.completed[0].task_id == "T1"

    complete_payload = implementation_module._handover_artifact_payload(
        handover,
        phase_state="complete",
        terminal_state="complete",
        tasks_total=1,
    )
    assert (
        implementation_module._handover_artifact_phase_state(complete_payload)
        == "complete"
    )


def test_handover_phase_state_legacy_rows_are_unknown_not_complete() -> None:
    # g0-era persisted handover rows: plain HandoverDoc JSON, no marker.
    legacy = HandoverDoc().model_dump_json(indent=2)
    assert implementation_module._handover_artifact_phase_state(legacy) == ""
    assert implementation_module._handover_artifact_phase_state("") == ""
    assert implementation_module._handover_artifact_phase_state(None) == ""
    assert implementation_module._handover_artifact_phase_state("not json") == ""
    assert implementation_module._handover_artifact_phase_state("[1, 2]") == ""


def test_phase_state_for_terminal_state_mapping() -> None:
    assert (
        implementation_module._phase_state_for_terminal_state("complete")
        == "complete"
    )
    for non_terminal in ("quiesced", "workflow_blocked", "verify_failed", ""):
        assert (
            implementation_module._phase_state_for_terminal_state(non_terminal)
            == "in_progress"
        )


@pytest.mark.asyncio
async def test_execute_persists_in_progress_phase_state_on_quiesce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = _dag(["T1"], [["T1"]])
    artifacts = _Artifacts({"dag": dag.model_dump_json()})
    runner = _tracked_runner(artifacts)

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="partial implementation",
            failure="SANDBOX_WORKFLOW_BLOCKER: Sandbox binding failed",
            handover=implementation_module.HandoverDoc(),
            terminal_state="workflow_blocked",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        implementation_module, "_implement_dag", _fake_implement_dag
    )
    monkeypatch.setattr(
        implementation_module, "enqueue_public_exhibit_refresh", _no_refresh
    )

    with pytest.raises(WorkflowQuiesced):
        await implementation_module.ImplementationPhase().execute(
            runner, _feature(), BuildState()
        )

    assert artifacts.store["implementation"] == "partial implementation"
    assert (
        implementation_module._handover_artifact_phase_state(
            artifacts.store["handover"]
        )
        == "in_progress"
    )


@pytest.mark.asyncio
async def test_execute_persists_complete_phase_state_on_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = _dag(["T1"], [["T1"]])
    artifacts = _Artifacts({"dag": dag.model_dump_json()})
    runner = _tracked_runner(artifacts)

    class _Sentinel(Exception):
        pass

    async def _fake_implement_dag(*_args, **_kwargs):
        handover = implementation_module.HandoverDoc()
        handover.record_success(
            ImplementationResult(task_id="T1", summary="done")
        )
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=handover,
            terminal_state="complete",
        )

    async def _stop_after_persist(*_args, **_kwargs) -> None:
        # First call happens immediately AFTER the implementation/handover
        # persist in execute(); raising here pins the persisted shape
        # without driving the full post-DAG gate machinery.
        raise _Sentinel()

    monkeypatch.setattr(
        implementation_module, "_implement_dag", _fake_implement_dag
    )
    monkeypatch.setattr(
        implementation_module,
        "enqueue_public_exhibit_refresh",
        _stop_after_persist,
    )

    with pytest.raises(_Sentinel):
        await implementation_module.ImplementationPhase().execute(
            runner, _feature(), BuildState()
        )

    assert (
        implementation_module._handover_artifact_phase_state(
            artifacts.store["handover"]
        )
        == "complete"
    )


# ── CLI seam: a quiesced workflow is never reported complete ────────────────


def test_cli_banner_complete_path_unchanged(capsys: pytest.CaptureFixture) -> None:
    from iriai_build_v2.interfaces.cli.app import _print_workflow_outcome

    runner = SimpleNamespace(last_workflow_quiesce=None)
    _print_workflow_outcome(runner, label="Workflow resume")

    out = capsys.readouterr().out
    assert "Workflow resume complete!" in out
    assert "NOT COMPLETE" not in out


def test_cli_banner_quiesce_reports_not_complete_and_exits_nonzero(
    capsys: pytest.CaptureFixture,
) -> None:
    from iriai_build_v2.interfaces.cli.app import (
        WORKFLOW_QUIESCED_EXIT_CODE,
        _print_workflow_outcome,
    )

    runner = SimpleNamespace(
        last_workflow_quiesce=SimpleNamespace(
            phase_name="implementation",
            reason=(
                "SANDBOX_WORKFLOW_BLOCKER: Sandbox binding failed for task "
                "TASK-RCAN-00-UPSTREAM-GATES"
            ),
            metadata={"terminal_state": "workflow_blocked"},
        )
    )

    with pytest.raises(SystemExit) as exc_info:
        _print_workflow_outcome(runner, label="Workflow resume")

    assert exc_info.value.code == WORKFLOW_QUIESCED_EXIT_CODE
    out = capsys.readouterr().out
    assert "QUIESCED — NOT COMPLETE" in out
    assert "complete!" not in out
    assert "implementation" in out
    assert "workflow_blocked" in out
    assert "TASK-RCAN-00-UPSTREAM-GATES" in out
