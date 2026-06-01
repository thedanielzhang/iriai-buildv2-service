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


def test_apply_ambiguity_tolerant_applies_corrects_skips_ambiguous():
    # raise_on_ambiguous=False: the confident phantom correct lands while the
    # uncertain sibling create is left unchanged (never guessed, no raise).
    dag = _dag(_task(file_scope=[
        (PHANTOM, "modify"),
        ("src/new/Sibling.tsx", "create"),
    ]))
    res = DagPathResolution(
        decisions=[
            DagPathDecision(task_id="T1", field="file_scope[0].path",
                            original=PHANTOM, resolved=CANON, decision="correct"),
            DagPathDecision(task_id="T1", field="file_scope[1].path",
                            original="src/new/Sibling.tsx", decision="ambiguous"),
        ],
        corrected_count=1, ambiguous_count=1,
    )
    new_dag, rewrites = apply_path_resolution(dag, res, raise_on_ambiguous=False)
    assert len(rewrites) == 1
    assert new_dag.tasks[0].file_scope[0].path == CANON  # phantom corrected
    assert new_dag.tasks[0].file_scope[1].path == "src/new/Sibling.tsx"  # untouched


def test_apply_raise_on_ambiguous_default_still_raises():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")]))
    res = DagPathResolution(decisions=[DagPathDecision(
        task_id="T1", field="file_scope[0].path",
        original=PHANTOM, decision="ambiguous",
    )], ambiguous_count=1)
    with pytest.raises(AmbiguousDagPath):
        apply_path_resolution(dag, res)  # default raise_on_ambiguous=True


def test_apply_corrects_files_list_by_value_match():
    # A phantom that also appears in files[] (reference text) is fully corrected
    # even without an explicit files[] decision — the file_scope correct's
    # {original->resolved} map drives the files[] rewrite.
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")], files=[PHANTOM, "other.ts"]))
    res = DagPathResolution(decisions=[DagPathDecision(
        task_id="T1", field="file_scope[0].path",
        original=PHANTOM, resolved=CANON, decision="correct",
    )], corrected_count=1)
    new_dag, rewrites = apply_path_resolution(dag, res)
    assert new_dag.tasks[0].file_scope[0].path == CANON
    assert new_dag.tasks[0].files == [CANON, "other.ts"]
    # one file_scope rewrite + one files[] rewrite recorded
    fields = sorted(r.field for r in rewrites)
    assert fields == ["file_scope[0].path", "files[0]"]


def test_prepass_flags_missing_modify_path():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")]))
    unresolved = unresolved_dag_paths(
        dag, "/repos", exists=lambda p: False, isdir=lambda p: False,
    )
    assert len(unresolved) == 1 and unresolved[0]["path"] == PHANTOM


def test_prepass_empty_when_all_exist_skips_agent():
    dag = _dag(_task(file_scope=[(CANON, "modify")]))
    abs_canon = "/repos/iriai-studio/" + CANON  # resolves under <repos>/<repo_path>/<path>
    unresolved = unresolved_dag_paths(
        dag, "/repos", exists=lambda p: p == abs_canon, isdir=lambda p: False,
    )
    assert unresolved == []


def test_prepass_resolves_under_either_convention():
    # repo-internal path resolves via <repos>/<repo_path>/<path>;
    # repo-prefixed path resolves via <repos>/<path>. Either is "resolved".
    internal = "src/vs/workbench/foo.ts"
    prefixed = "iriai-studio/src/vs/workbench/bar.ts"
    dag = _dag(_task(file_scope=[(internal, "modify"), (prefixed, "modify")]))
    abs_internal = "/repos/iriai-studio/" + internal  # only the prefixed join exists
    abs_prefixed = "/repos/" + prefixed                # only the bare join exists
    out = unresolved_dag_paths(
        dag, "/repos",
        exists=lambda p: p in {abs_internal, abs_prefixed},
        isdir=lambda p: False,
    )
    assert out == []


def test_prepass_unreliable_repo_path_resolves_via_bare_join():
    # repo_path is unreliable: a path with its own repo-name prefix must still
    # resolve via <repos>/<path> even though <repos>/<repo_path>/<path> is wrong.
    prefixed = "iriai-studio-backend/iriai_studio_backend/app.py"
    dag = _dag(_task(file_scope=[(prefixed, "modify")], repo_path="iriai"))
    abs_bare = "/repos/" + prefixed
    out = unresolved_dag_paths(
        dag, "/repos", exists=lambda p: p == abs_bare, isdir=lambda p: False,
    )
    assert out == []


def test_prepass_flags_create_when_missing():
    # create files do not exist by definition -> flagged so the resolver decides.
    dag = _dag(_task(file_scope=[("src/new/Comp.tsx", "create")]))
    out = unresolved_dag_paths(
        dag, "/repos", exists=lambda p: False, isdir=lambda p: False,
    )
    assert len(out) == 1 and out[0]["action"] == "create"
    # ...but a create whose file already exists (under either join) resolves.
    abs_create = "/repos/iriai-studio/src/new/Comp.tsx"
    assert unresolved_dag_paths(
        dag, "/repos", exists=lambda p: p == abs_create, isdir=lambda p: False,
    ) == []


def test_prepass_ignores_legacy_files_list():
    # files[] entries are not flagged by the prepass even when missing.
    dag = _dag(_task(file_scope=[(CANON, "modify")], files=["src/legacy/old.ts"]))
    abs_canon = "/repos/iriai-studio/" + CANON
    out = unresolved_dag_paths(
        dag, "/repos", exists=lambda p: p == abs_canon, isdir=lambda p: False,
    )
    assert out == []  # only file_scope is considered, legacy files[] ignored


def test_agentic_resolver_enabled_default_on(monkeypatch):
    monkeypatch.delenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", raising=False)
    assert dag_path_agentic_resolver_enabled() is True
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    assert dag_path_agentic_resolver_enabled() is False
