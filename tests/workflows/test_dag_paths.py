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
    build_dag_path_resolver_prompt,
    dag_path_agentic_resolver_enabled,
    planned_new_file_paths,
    resolution_covers_unresolved,
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


NEW_MODEL = "shared_libs/kaya_db/kaya_db/models/submittals/submittal_record.py"


def test_backstop_converts_fragment_created_ambiguous_to_create_ok(caplog):
    # N-4 regression: TASK-1 creates the file; TASK-3 reads it; the resolver
    # flagged BOTH ambiguous ("not on disk"). The deterministic backstop must
    # convert both (path matches a create-action path in the SAME fragment),
    # WARN, and NOT raise — paths untouched.
    dag = _dag(
        _task("TASK-1", file_scope=[(NEW_MODEL, "create")], repo_path=""),
        _task("TASK-3", file_scope=[(NEW_MODEL, "read_only")], repo_path=""),
    )
    res = DagPathResolution(decisions=[
        DagPathDecision(task_id="TASK-1", field="file_scope[0].path",
                        original=NEW_MODEL, decision="ambiguous",
                        evidence="parent dir does not exist"),
        DagPathDecision(task_id="TASK-3", field="file_scope[0].path",
                        original=NEW_MODEL, decision="ambiguous",
                        evidence="Read-only path is not an existing file"),
    ], ambiguous_count=2)
    with caplog.at_level("WARNING", logger="iriai_build_v2.workflows._common._dag_paths"):
        new_dag, rewrites = apply_path_resolution(dag, res)  # default raise_on_ambiguous=True
    assert rewrites == []
    assert new_dag.tasks[0].file_scope[0].path == NEW_MODEL
    assert new_dag.tasks[1].file_scope[0].path == NEW_MODEL
    warn = [r for r in caplog.records if "planned NEW file" in r.getMessage()]
    assert len(warn) == 2 and all(r.levelname == "WARNING" for r in warn)


def test_backstop_matches_repo_path_joined_convention():
    # create entry is repo-internal (repo_path join) while the ambiguous read
    # scope cites the repo-prefixed form — exact match across both conventions.
    internal = "tests/submittals/test_share_link_defaults.py"
    prefixed = "supply-chain/tests/submittals/test_share_link_defaults.py"
    dag = _dag(
        _task("C", file_scope=[(internal, "create")], repo_path="supply-chain"),
        _task("R", file_scope=[(prefixed, "read_only")], repo_path=""),
    )
    assert prefixed in planned_new_file_paths(dag)
    res = DagPathResolution(decisions=[DagPathDecision(
        task_id="R", field="file_scope[0].path",
        original=prefixed, decision="ambiguous",
    )], ambiguous_count=1)
    new_dag, rewrites = apply_path_resolution(dag, res)  # must not raise
    assert rewrites == [] and new_dag.tasks[1].file_scope[0].path == prefixed


def test_backstop_genuinely_unknown_path_still_raises():
    # Nothing in the DAG creates this path -> the 199-loop fail-safe stays.
    dag = _dag(
        _task("C", file_scope=[(NEW_MODEL, "create")], repo_path=""),
        _task("R", file_scope=[("totally/unknown/elsewhere.py", "modify")], repo_path=""),
    )
    res = DagPathResolution(decisions=[DagPathDecision(
        task_id="R", field="file_scope[0].path",
        original="totally/unknown/elsewhere.py", decision="ambiguous",
    )], ambiguous_count=1)
    with pytest.raises(AmbiguousDagPath) as exc_info:
        apply_path_resolution(dag, res)
    assert "totally/unknown/elsewhere.py" in str(exc_info.value)


def test_backstop_mixed_converts_planned_raises_unknown():
    dag = _dag(
        _task("C", file_scope=[(NEW_MODEL, "create")], repo_path=""),
        _task("R", file_scope=[
            (NEW_MODEL, "read_only"),
            ("totally/unknown/elsewhere.py", "modify"),
        ], repo_path=""),
    )
    res = DagPathResolution(decisions=[
        DagPathDecision(task_id="R", field="file_scope[0].path",
                        original=NEW_MODEL, decision="ambiguous"),
        DagPathDecision(task_id="R", field="file_scope[1].path",
                        original="totally/unknown/elsewhere.py", decision="ambiguous"),
    ], ambiguous_count=2)
    with pytest.raises(AmbiguousDagPath) as exc_info:
        apply_path_resolution(dag, res)
    # only the genuinely-unknown path remains in the raise
    assert "totally/unknown" in str(exc_info.value)
    assert NEW_MODEL not in str(exc_info.value)


def test_prompt_contains_planned_new_file_set_and_instruction():
    dag = _dag(
        _task("TASK-1", file_scope=[(NEW_MODEL, "create")], repo_path=""),
        _task("TASK-3", file_scope=[(NEW_MODEL, "read_only")], repo_path=""),
    )
    unresolved = unresolved_dag_paths(dag, "/repos", exists=lambda p: False)
    prompt = build_dag_path_resolver_prompt(dag, unresolved, "/repos")
    assert "PLANNED NEW FILES" in prompt
    assert NEW_MODEL in prompt
    assert "create_ok" in prompt and "NOT `ambiguous`" in prompt


def test_prompt_omits_planned_section_when_no_creates():
    dag = _dag(_task(file_scope=[(PHANTOM, "modify")]))
    unresolved = unresolved_dag_paths(dag, "/repos", exists=lambda p: False)
    prompt = build_dag_path_resolver_prompt(dag, unresolved, "/repos")
    assert "PLANNED NEW FILES" not in prompt


# ---- CREATE-vs-MODIFY disposition branch (operator-analysis refinement) ----

E2E_SETUP = "spend-client/e2e/global-setup.ts"
E2E_SPEC = "spend-client/e2e/submittals.spec.ts"


def _ambiguous(task_id, field, original):
    return DagPathDecision(
        task_id=task_id, field=field, original=original, decision="ambiguous",
    )


def test_backstop_create_class_converts_when_parent_dir_exists_on_disk():
    # spend-client/playwright.config.ts case: parent dir is real, file is new.
    path = "spend-client/playwright.config.ts"
    dag = _dag(_task("C", file_scope=[(path, "create")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("C", "file_scope[0].path", path)], ambiguous_count=1,
    )
    new_dag, rewrites = apply_path_resolution(
        dag, res, repos_root="/repos",
        exists=lambda p: p == "/repos/spend-client",
    )
    assert rewrites == [] and new_dag.tasks[0].file_scope[0].path == path
    assert res.decisions[0].decision == "create_ok"


def test_backstop_create_class_converts_when_parent_created_by_fragment():
    # global-setup.ts CREATE-class: parent e2e/ is not on disk but a sibling
    # planned-new spec grounds it; same-basename matches elsewhere are
    # irrelevant for create-class (ambiguity checks apply only to modify).
    dag = _dag(
        _task("C", file_scope=[(E2E_SETUP, "create"), (E2E_SPEC, "create")], repo_path=""),
    )
    res = DagPathResolution(
        decisions=[_ambiguous("C", "file_scope[0].path", E2E_SETUP)], ambiguous_count=1,
    )
    apply_path_resolution(
        dag, res, repos_root="/repos", exists=lambda p: False,
        find_basename_matches=lambda name: 3,  # must NOT bite for create-class
    )
    assert res.decisions[0].decision == "create_ok"


def test_backstop_create_class_phantom_parent_still_raises():
    # Solo new file in a directory that neither exists nor is created by the
    # fragment -> conservative fail-safe (never guess a location).
    path = "phantom-repo/lib/widget.py"
    dag = _dag(_task("C", file_scope=[(path, "create")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("C", "file_scope[0].path", path)], ambiguous_count=1,
    )
    with pytest.raises(AmbiguousDagPath):
        apply_path_resolution(dag, res, repos_root="/repos", exists=lambda p: False)


def test_backstop_modify_class_with_basename_matches_still_raises():
    # global-setup.ts TRUE-ambiguity subcase: a MODIFY entry whose path matches
    # a planned-new file but with 3 same-basename files on disk must STILL
    # flag — never guess between an existing file and a planned one.
    dag = _dag(
        _task("C", file_scope=[(E2E_SETUP, "create")], repo_path=""),
        _task("M", file_scope=[(E2E_SETUP, "modify")], repo_path=""),
    )
    res = DagPathResolution(
        decisions=[_ambiguous("M", "file_scope[0].path", E2E_SETUP)], ambiguous_count=1,
    )
    with pytest.raises(AmbiguousDagPath) as exc_info:
        apply_path_resolution(
            dag, res, repos_root="/repos",
            exists=lambda p: p == "/repos/spend-client",
            find_basename_matches=lambda name: 3,
        )
    assert E2E_SETUP in str(exc_info.value)


def test_backstop_modify_class_zero_basename_matches_converts():
    # Intra-fragment dependency: a later MODIFY of a file the fragment creates,
    # with NO same-basename candidates on disk, is unambiguous -> create_ok.
    dag = _dag(
        _task("C", file_scope=[(NEW_MODEL, "create")], repo_path=""),
        _task("M", file_scope=[(NEW_MODEL, "modify")], repo_path=""),
    )
    res = DagPathResolution(
        decisions=[_ambiguous("M", "file_scope[0].path", NEW_MODEL)], ambiguous_count=1,
    )
    apply_path_resolution(
        dag, res, repos_root="/repos", exists=lambda p: False,
        find_basename_matches=lambda name: 0,
    )
    assert res.decisions[0].decision == "create_ok"


def test_backstop_read_only_planned_path_with_basename_matches_is_non_fatal():
    # N-7 (resume48): a READ-ONLY reference exactly matching a path a sibling
    # task CREATES (e2e/fixtures/auth.ts) must not be failed by unrelated
    # same-basename files elsewhere on disk — non-fatal pointer, path and
    # decision left untouched.
    dag = _dag(
        _task("C", file_scope=[(E2E_SETUP, "create")], repo_path=""),
        _task("R", file_scope=[(E2E_SETUP, "read_only")], repo_path=""),
    )
    res = DagPathResolution(
        decisions=[_ambiguous("R", "file_scope[0].path", E2E_SETUP)], ambiguous_count=1,
    )
    apply_path_resolution(
        dag, res, repos_root="/repos",
        exists=lambda p: p == "/repos/spend-client",
        find_basename_matches=lambda name: 3,
    )
    # Not converted, not raised: a pointer, never an edit target.
    assert res.decisions[0].decision == "ambiguous"
    assert dag.tasks[1].file_scope[0].path == E2E_SETUP


def test_backstop_read_only_unplanned_path_with_basename_matches_still_raises():
    # The relaxation is ONLY for exact planned-new matches: a read-only entry
    # NOT in the planned set with on-disk basename matches still raises
    # (picking context wrong is real).
    dag = _dag(_task("R", file_scope=[(PHANTOM, "read_only")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("R", "file_scope[0].path", PHANTOM)], ambiguous_count=1,
    )
    with pytest.raises(AmbiguousDagPath):
        apply_path_resolution(
            dag, res, repos_root="/repos", exists=lambda p: False,
            find_basename_matches=lambda name: 2,
        )


def test_backstop_extra_planned_grounds_cross_subfeature_modify():
    # N-8 (resume49): a MODIFY of a file an EARLIER subfeature's DAG creates
    # (every SF appends to S1's router) — not in THIS fragment's planned set,
    # zero on-disk basename matches. extra_planned supplies the upstream
    # planned paths and the modify-class conversion applies.
    dag = _dag(_task("M", file_scope=[(CANON, "modify")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("M", "file_scope[0].path", CANON)], ambiguous_count=1,
    )
    apply_path_resolution(
        dag, res, repos_root="/repos", exists=lambda p: False,
        find_basename_matches=lambda name: 0,
        extra_planned={CANON},
    )
    assert res.decisions[0].decision == "create_ok"


def test_backstop_extra_planned_modify_with_basename_matches_still_raises():
    # The never-guess rule survives extra_planned: existing same-basename
    # files on disk keep a cross-SF modify ambiguous.
    dag = _dag(_task("M", file_scope=[(CANON, "modify")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("M", "file_scope[0].path", CANON)], ambiguous_count=1,
    )
    with pytest.raises(AmbiguousDagPath):
        apply_path_resolution(
            dag, res, repos_root="/repos", exists=lambda p: False,
            find_basename_matches=lambda name: 2,
            extra_planned={CANON},
        )


def test_backstop_create_class_grounds_via_workspace_root():
    # Workspace-level deliverable (authored-not-executed migration doc under
    # docs/): the parent never exists under repos_root, but exists under the
    # WORKSPACE base — grounded via the workspace_root fallback.
    doc = "docs/submittal-prd/slices/migrations/S2-foo.NEVER-EXECUTE.sql"
    dag = _dag(_task("C", file_scope=[(doc, "create")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("C", "file_scope[0].path", doc)], ambiguous_count=1,
    )
    apply_path_resolution(
        dag, res, repos_root="/repos",
        exists=lambda p: p == "/ws/docs/submittal-prd",
        find_basename_matches=lambda name: 0,
        workspace_root="/ws",
    )
    assert res.decisions[0].decision == "create_ok"


def test_backstop_create_class_wholly_novel_tree_still_raises_with_workspace_root():
    # The conservative typo-guard survives: no ancestor within two levels
    # exists under EITHER root -> still ambiguous.
    doc = "totally/new/tree/depth/file.sql"
    dag = _dag(_task("C", file_scope=[(doc, "create")], repo_path=""))
    res = DagPathResolution(
        decisions=[_ambiguous("C", "file_scope[0].path", doc)], ambiguous_count=1,
    )
    with pytest.raises(AmbiguousDagPath):
        apply_path_resolution(
            dag, res, repos_root="/repos", exists=lambda p: False,
            find_basename_matches=lambda name: 0,
            workspace_root="/ws",
        )


def test_backstop_skips_stale_ambiguous_decision():
    # Persisted resolution reused after a re-plan: the decision's entry no
    # longer exists in the DAG -> skipped (no raise), nothing converted.
    dag = _dag(_task(file_scope=[(CANON, "modify")]))
    res = DagPathResolution(
        decisions=[_ambiguous("T-OLD", "file_scope[0].path", PHANTOM)],
        ambiguous_count=1,
    )
    new_dag, rewrites = apply_path_resolution(dag, res)  # must not raise
    assert rewrites == []
    assert res.decisions[0].decision == "ambiguous"  # left as-is, just skipped


def test_resolution_covers_unresolved_gate():
    unresolved = [
        {"task_id": "T1", "field": "file_scope[0].path", "path": PHANTOM, "action": "modify"},
    ]
    covering = DagPathResolution(decisions=[DagPathDecision(
        task_id="T1", field="file_scope[0].path", original=PHANTOM, decision="keep",
    )])
    stale = DagPathResolution(decisions=[DagPathDecision(
        task_id="T-OLD", field="file_scope[0].path", original="other.py", decision="ambiguous",
    )])
    assert resolution_covers_unresolved(covering, unresolved) is True
    assert resolution_covers_unresolved(stale, unresolved) is False
    assert resolution_covers_unresolved(stale, []) is True  # nothing to cover


def test_prompt_branches_disposition_rules_per_action():
    dag = _dag(
        _task("C", file_scope=[(E2E_SETUP, "create")], repo_path=""),
        _task("M", file_scope=[(E2E_SETUP, "modify")], repo_path=""),
    )
    unresolved = unresolved_dag_paths(dag, "/repos", exists=lambda p: False)
    prompt = build_dag_path_resolver_prompt(dag, unresolved, "/repos")
    assert "DISPOSITION RULES" in prompt
    assert "PARENT DIRECTORY" in prompt        # create-class validation
    assert "same-basename" in prompt           # modify-class exception bound
    assert "NEVER guess" in prompt


def test_agentic_resolver_enabled_default_on(monkeypatch):
    monkeypatch.delenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", raising=False)
    assert dag_path_agentic_resolver_enabled() is True
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    assert dag_path_agentic_resolver_enabled() is False
