from __future__ import annotations

import pytest

from iriai_build_v2.models.outputs import (
    DagPathDecision,
    DagPathResolution,
    ImplementationDAG,
    ImplementationTask,
    TaskFileScope,
)
from iriai_build_v2.workflows._common._dag_paths import (
    AmbiguousDagPath,
    apply_path_resolution,
    dag_path_agentic_resolver_enabled,
    unresolved_dag_paths,
)

PHANTOM = "src/vs/workbench/contrib/workflowTab/views/implementation/TaskRow.tsx"
CANON = (
    "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/views/"
    "implementation/TaskRow.tsx"
)


def _task(task_id="T1", *, file_scope=None, files=None, repo_path="iriai-studio"):
    return ImplementationTask(
        id=task_id,
        name=task_id,
        description="",
        file_scope=[TaskFileScope(path=p, action=a) for p, a in (file_scope or [])],
        files=list(files or []),
        repo_path=repo_path,
    )


def _dag(*tasks):
    return ImplementationDAG(
        tasks=list(tasks),
        num_teams=1,
        execution_order=[[t.id for t in tasks]],
    )


def test_apply_corrects_phantom_path():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")]))
    res = DagPathResolution(
        decisions=[DagPathDecision(
            task_id="T1", field="file_scope[0].path",
            original=PHANTOM, resolved=CANON, decision="correct", evidence="glob",
        )],
        corrected_count=1,
    )
    new_dag, rewrites = apply_path_resolution(dag, res)
    assert new_dag.tasks[0].file_scope[0].path == CANON
    assert len(rewrites) == 1 and rewrites[0].canonical == CANON
    # original DAG is not mutated
    assert dag.tasks[0].file_scope[0].path == PHANTOM


def test_apply_keep_and_create_ok_leave_unchanged():
    dag = _dag(_task(file_scope=[(CANON, "modify"), ("newdir/New.tsx", "create")]))
    res = DagPathResolution(decisions=[
        DagPathDecision(task_id="T1", field="file_scope[0].path",
                        original=CANON, resolved=CANON, decision="keep"),
        DagPathDecision(task_id="T1", field="file_scope[1].path",
                        original="newdir/New.tsx", resolved="newdir/New.tsx",
                        decision="create_ok"),
    ])
    new_dag, rewrites = apply_path_resolution(dag, res)
    assert rewrites == []
    assert new_dag is dag  # no-op returns the same object


def test_apply_ambiguous_raises_failsafe():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")]))
    res = DagPathResolution(
        decisions=[DagPathDecision(
            task_id="T1", field="file_scope[0].path",
            original=PHANTOM, decision="ambiguous",
        )],
        ambiguous_count=1,
    )
    with pytest.raises(AmbiguousDagPath):
        apply_path_resolution(dag, res)


def test_apply_is_idempotent_on_reapply():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")], files=[PHANTOM]))
    res = DagPathResolution(decisions=[
        DagPathDecision(task_id="T1", field="file_scope[0].path",
                        original=PHANTOM, resolved=CANON, decision="correct"),
        DagPathDecision(task_id="T1", field="files[0]",
                        original=PHANTOM, resolved=CANON, decision="correct"),
    ])
    once, r1 = apply_path_resolution(dag, res)
    twice, r2 = apply_path_resolution(once, res)  # original no longer matches -> no-op
    assert len(r1) == 2 and r2 == []
    assert once.model_dump() == twice.model_dump()
    assert once.tasks[0].file_scope[0].path == CANON
    assert once.tasks[0].files[0] == CANON


def test_apply_ignores_stale_original():
    dag = _dag(_task(file_scope=[(CANON, "modify")]))
    res = DagPathResolution(decisions=[DagPathDecision(
        task_id="T1", field="file_scope[0].path",
        original=PHANTOM, resolved="other", decision="correct",
    )])
    new_dag, rewrites = apply_path_resolution(dag, res)
    assert rewrites == []  # recorded original (PHANTOM) != live path (CANON)


def test_prepass_flags_missing_modify_path():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")]))
    unresolved = unresolved_dag_paths(
        dag, "/ws", exists=lambda p: False, isdir=lambda p: False,
    )
    assert len(unresolved) == 1 and unresolved[0]["path"] == PHANTOM


def test_prepass_empty_when_all_exist_skips_agent():
    dag = _dag(_task(file_scope=[(CANON, "modify")]))
    abs_canon = "/ws/iriai-studio/" + CANON
    unresolved = unresolved_dag_paths(
        dag, "/ws", exists=lambda p: p == abs_canon, isdir=lambda p: False,
    )
    assert unresolved == []


def test_prepass_create_requires_parent_dir():
    dag = _dag(_task(file_scope=[("src/new/Comp.tsx", "create")]))
    parent = "/ws/iriai-studio/src/new"
    assert unresolved_dag_paths(
        dag, "/ws", exists=lambda p: False, isdir=lambda p: p == parent,
    ) == []
    out = unresolved_dag_paths(
        dag, "/ws", exists=lambda p: False, isdir=lambda p: False,
    )
    assert len(out) == 1 and out[0]["action"] == "create"


def test_agentic_resolver_enabled_default_on(monkeypatch):
    monkeypatch.delenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", raising=False)
    assert dag_path_agentic_resolver_enabled() is True
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    assert dag_path_agentic_resolver_enabled() is False
