from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import (
    DagPathDecision,
    DagPathResolution,
    ImplementationDAG,
    ImplementationTask,
    TaskFileScope,
)
from iriai_build_v2.workflows.develop.phases import implementation as impl

PHANTOM = "src/vs/workbench/contrib/workflowTab/views/implementation/TaskRow.tsx"
CANON = (
    "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/views/"
    "implementation/TaskRow.tsx"
)
FEATURE = SimpleNamespace(id="8ac124d6", workspace_id="ws")


def _dag():
    return ImplementationDAG(
        tasks=[ImplementationTask(
            id="T1", name="T1", description="",
            file_scope=[TaskFileScope(path=PHANTOM, action="modify")],
            files=[PHANTOM], repo_path="iriai-studio",
        )],
        num_teams=1, execution_order=[["T1"]],
    )


def _correct_resolution():
    return DagPathResolution(
        decisions=[
            DagPathDecision(task_id="T1", field="file_scope[0].path",
                            original=PHANTOM, resolved=CANON, decision="correct"),
            DagPathDecision(task_id="T1", field="files[0]",
                            original=PHANTOM, resolved=CANON, decision="correct"),
        ],
        corrected_count=2,
    )


class _Artifacts:
    def __init__(self, store=None):
        self.store = dict(store or {})
        self.puts: list[str] = []

    async def get(self, key, *, feature=None):
        return self.store.get(key)

    async def put(self, key, val, *, feature=None):
        self.store[key] = val
        self.puts.append(key)


class _Runner:
    def __init__(self, resolution=None, store=None, ws_path="/ws"):
        self.artifacts = _Artifacts(store)
        self._resolution = resolution
        self._ws_path = ws_path
        self.runs = 0

    def get_workspace(self, workspace_id):
        return SimpleNamespace(path=self._ws_path) if self._ws_path else None

    async def run(self, ask, feature, phase_name=None):
        self.runs += 1
        return self._resolution


def _force_unresolved(monkeypatch):
    monkeypatch.setattr(impl, "unresolved_dag_paths", lambda dag, root: [
        {"task_id": "T1", "field": "file_scope[0].path", "path": PHANTOM, "action": "modify"},
        {"task_id": "T1", "field": "files[0]", "path": PHANTOM, "action": ""},
    ])


@pytest.mark.asyncio
async def test_migration_corrects_and_repersists_dag(monkeypatch):
    # AC6: persisted dag rewritten to canonical paths, zero phantom references.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    _force_unresolved(monkeypatch)
    runner = _Runner(resolution=_correct_resolution())
    out = await impl._migrate_persisted_dag_paths(runner, FEATURE, _dag())
    assert out.tasks[0].file_scope[0].path == CANON
    assert out.tasks[0].files[0] == CANON
    assert "dag" in runner.artifacts.puts
    persisted = runner.artifacts.store["dag"]
    assert CANON in persisted
    assert "contrib/workflowTab/views/implementation" not in persisted
    assert runner.runs == 1  # agent dispatched exactly once


@pytest.mark.asyncio
async def test_migration_is_noop_when_paths_resolve(monkeypatch):
    # AC9: idempotent — once paths resolve on disk the prepass is empty.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    monkeypatch.setattr(impl, "unresolved_dag_paths", lambda dag, root: [])
    runner = _Runner(resolution=_correct_resolution())
    dag = _dag()
    out = await impl._migrate_persisted_dag_paths(runner, FEATURE, dag)
    assert out is dag
    assert runner.runs == 0
    assert "dag" not in runner.artifacts.puts


@pytest.mark.asyncio
async def test_migration_reuses_persisted_resolution(monkeypatch):
    # Replay-stability: a persisted resolution is reused; the agent is NOT re-run.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    _force_unresolved(monkeypatch)
    store = {"dag-path-resolution:migration": _correct_resolution().model_dump_json()}
    runner = _Runner(resolution=None, store=store)
    out = await impl._migrate_persisted_dag_paths(runner, FEATURE, _dag())
    assert runner.runs == 0
    assert out.tasks[0].file_scope[0].path == CANON


@pytest.mark.asyncio
async def test_migration_ambiguous_leaves_dag_unchanged(monkeypatch):
    # Fail-safe: ambiguous resolution does not guess; dag is not re-persisted.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    _force_unresolved(monkeypatch)
    res = DagPathResolution(
        decisions=[DagPathDecision(task_id="T1", field="file_scope[0].path",
                                   original=PHANTOM, decision="ambiguous")],
        ambiguous_count=1,
    )
    runner = _Runner(resolution=res)
    dag = _dag()
    out = await impl._migrate_persisted_dag_paths(runner, FEATURE, dag)
    assert out is dag
    assert "dag" not in runner.artifacts.puts


@pytest.mark.asyncio
async def test_migration_flag_off_is_noop(monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    runner = _Runner(resolution=_correct_resolution())
    dag = _dag()
    out = await impl._migrate_persisted_dag_paths(runner, FEATURE, dag)
    assert out is dag
    assert runner.runs == 0
