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
FEATURE = SimpleNamespace(id="8ac124d6", slug="8ac124d6", workspace_id="ws")


def _group_tasks(*, files_phantom=True):
    """A single group containing T1 carrying the phantom path."""
    return [
        ImplementationTask(
            id="T1", name="T1", description="",
            file_scope=[TaskFileScope(path=PHANTOM, action="modify")],
            files=[PHANTOM] if files_phantom else [],
            repo_path="iriai-studio",
        ),
    ]


def _correct_resolution():
    # Only the file_scope phantom is decided; the files[] phantom is corrected by
    # value-match in apply_path_resolution (no explicit files[] decision needed).
    return DagPathResolution(
        decisions=[
            DagPathDecision(task_id="T1", field="file_scope[0].path",
                            original=PHANTOM, resolved=CANON, decision="correct"),
        ],
        corrected_count=1,
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


def _force_unresolved(monkeypatch, *, repos_root="/ws/repos", unresolved=None):
    """Stub the repo root + prepass. The prepass returns the supplied unresolved
    entries (default: the T1 file_scope phantom)."""
    monkeypatch.setattr(
        impl, "feature_repos_root", lambda runner, feature: repos_root
    )

    entries = unresolved if unresolved is not None else [
        {"task_id": "T1", "field": "file_scope[0].path",
         "path": PHANTOM, "action": "modify"},
    ]

    def _prepass(dag, root):
        # The scoped DAG must only contain this group's tasks.
        assert root == repos_root
        return list(entries)

    monkeypatch.setattr(impl, "unresolved_dag_paths", _prepass)


@pytest.mark.asyncio
async def test_group_resolution_prepass_skip_when_paths_resolve(monkeypatch):
    # (a) Existence-prepass skip: every path resolves -> no dispatch, tasks returned as-is.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    monkeypatch.setattr(impl, "feature_repos_root", lambda runner, feature: "/ws/repos")
    monkeypatch.setattr(impl, "unresolved_dag_paths", lambda dag, root: [])
    runner = _Runner(resolution=_correct_resolution())
    tasks = _group_tasks()
    out = await impl._resolve_group_task_paths(
        runner, FEATURE, tasks, group_idx=7
    )
    assert out is tasks  # unchanged identity
    assert runner.runs == 0
    assert runner.artifacts.puts == []


@pytest.mark.asyncio
async def test_group_resolution_corrects_phantom_in_memory(monkeypatch):
    # (b) A group with a phantom file_scope path -> agent invoked once, group_tasks
    # corrected in-memory (incl files[] value-match). The dag artifact is NOT
    # re-persisted — only the resolution is persisted.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    _force_unresolved(monkeypatch)
    runner = _Runner(resolution=_correct_resolution())
    out = await impl._resolve_group_task_paths(
        runner, FEATURE, _group_tasks(), group_idx=12
    )
    t1 = next(t for t in out if t.id == "T1")
    assert t1.file_scope[0].path == CANON
    assert t1.files[0] == CANON  # files[] corrected by value-match
    assert runner.runs == 1  # agent dispatched exactly once
    # Only the resolution is persisted; the dag artifact is never rewritten.
    assert runner.artifacts.puts == ["dag-path-resolution:g12"]
    assert "dag" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_group_resolution_reuses_persisted_resolution(monkeypatch):
    # (c) Replay reuse of dag-path-resolution:g{idx}: a persisted resolution is
    # reused; the agent is NOT re-dispatched.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    _force_unresolved(monkeypatch)
    store = {
        "dag-path-resolution:g3": _correct_resolution().model_dump_json(),
    }
    runner = _Runner(resolution=None, store=store)
    out = await impl._resolve_group_task_paths(
        runner, FEATURE, _group_tasks(), group_idx=3
    )
    assert runner.runs == 0
    t1 = next(t for t in out if t.id == "T1")
    assert t1.file_scope[0].path == CANON
    assert t1.files[0] == CANON
    # No re-persist of the reused resolution.
    assert runner.artifacts.puts == []


@pytest.mark.asyncio
async def test_group_resolution_ambiguity_tolerant(monkeypatch):
    # (d) Ambiguity-tolerant: the confident `correct` lands, the ambiguous sibling
    # is left untouched, and NO exception is raised.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    sibling = "src/new/Sibling.tsx"
    _force_unresolved(monkeypatch, unresolved=[
        {"task_id": "T1", "field": "file_scope[0].path",
         "path": PHANTOM, "action": "modify"},
        {"task_id": "T1", "field": "file_scope[1].path",
         "path": sibling, "action": "create"},
    ])
    tasks = [
        ImplementationTask(
            id="T1", name="T1", description="",
            file_scope=[
                TaskFileScope(path=PHANTOM, action="modify"),
                TaskFileScope(path=sibling, action="create"),
            ],
            files=[PHANTOM], repo_path="iriai-studio",
        ),
    ]
    res = DagPathResolution(
        decisions=[
            DagPathDecision(task_id="T1", field="file_scope[0].path",
                            original=PHANTOM, resolved=CANON, decision="correct"),
            DagPathDecision(task_id="T1", field="file_scope[1].path",
                            original=sibling, decision="ambiguous"),
        ],
        corrected_count=1, ambiguous_count=1,
    )
    runner = _Runner(resolution=res)
    out = await impl._resolve_group_task_paths(
        runner, FEATURE, tasks, group_idx=0
    )
    assert out[0].file_scope[0].path == CANON  # confident fix landed
    assert out[0].file_scope[1].path == sibling  # ambiguous left unchanged
    assert out[0].files[0] == CANON  # files[] value-match corrected
    assert runner.runs == 1
    assert runner.artifacts.puts == ["dag-path-resolution:g0"]


@pytest.mark.asyncio
async def test_group_resolution_all_ambiguous_leaves_tasks_unchanged(monkeypatch):
    # When the ONLY decision is ambiguous, nothing is corrected; the tasks are
    # returned unchanged (no guess) and no resolution write churns the dag.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    _force_unresolved(monkeypatch)
    res = DagPathResolution(
        decisions=[DagPathDecision(task_id="T1", field="file_scope[0].path",
                                   original=PHANTOM, decision="ambiguous")],
        ambiguous_count=1,
    )
    runner = _Runner(resolution=res)
    tasks = _group_tasks()
    out = await impl._resolve_group_task_paths(
        runner, FEATURE, tasks, group_idx=5
    )
    assert out is tasks  # no rewrites -> original list returned
    t1 = out[0]
    assert t1.file_scope[0].path == PHANTOM
    assert "dag" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_group_resolution_skips_without_repos_root(monkeypatch):
    # No on-disk checkout root -> skip resolution rather than mis-resolve.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    monkeypatch.setattr(impl, "feature_repos_root", lambda runner, feature: "")
    runner = _Runner(resolution=_correct_resolution())
    tasks = _group_tasks()
    out = await impl._resolve_group_task_paths(
        runner, FEATURE, tasks, group_idx=2
    )
    assert out is tasks
    assert runner.runs == 0


@pytest.mark.asyncio
async def test_group_resolution_flag_off_is_noop(monkeypatch):
    # (e) Flag-off (IRIAI_DAG_PATH_AGENTIC_RESOLVER=0): the per-group hook never
    # calls the helper, so calling it directly should still be a clean no-op path
    # is not exercised by the hook. We assert the hook gate observes the flag.
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    from iriai_build_v2.workflows._common._dag_paths import (
        dag_path_agentic_resolver_enabled,
    )

    assert dag_path_agentic_resolver_enabled() is False
