from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose import Ask
from iriai_compose.workflow import Workspace

from iriai_build_v2.models.outputs import (
    DagPathDecision,
    DagPathResolution,
    ImplementationDAG,
    ImplementationTask,
    TaskAcceptanceCriterion,
    TaskFileScope,
    TaskReference,
)
from iriai_build_v2.workflows.planning.phases import task_planning as _tp
from iriai_build_v2.workflows.planning.phases.task_planning import TaskPlanningPhase


# ── Fixtures / fakes ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_repos_root(monkeypatch, tmp_path):
    # The seam resolves paths under the feature's per-repo checkout root
    # (feature_repos_root). In these tests the "checkout" is tmp_path, with files
    # written at <tmp_path>/<repo_path>/<path>, so point the resolver there.
    monkeypatch.setattr(_tp, "feature_repos_root", lambda runner, feature: str(tmp_path))


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
    """Minimal WorkflowRunner stand-in for the path-resolution seam.

    Records resolver Ask dispatches and serves a fixed resolution so tests can
    assert call-count (0 vs 1) and the corrected DAG."""

    def __init__(self, workspace_path: str, resolution: DagPathResolution | None = None) -> None:
        self.artifacts = _Artifacts()
        self.services: dict[str, object] = {}
        self._workspace = Workspace(id="ws", path=Path(workspace_path))
        self._resolution = resolution
        self.run_calls: list[tuple[str, str]] = []

    def get_workspace(self, workspace_id):
        del workspace_id
        return self._workspace

    async def run(self, task, feature, phase_name):
        del feature, phase_name
        if not isinstance(task, Ask):
            raise AssertionError(f"unexpected task type: {type(task).__name__}")
        self.run_calls.append((task.actor.name, task.prompt))
        if task.output_type is not DagPathResolution:
            raise AssertionError("resolver Ask must request DagPathResolution output")
        if self._resolution is None:
            raise AssertionError("resolver dispatched but no mock resolution configured")
        return self._resolution


def _feature():
    return SimpleNamespace(id="feat-1", workspace_id="ws", metadata={})


def _task(task_id="T1", *, file_scope=None, files=None, repo_path="repo"):
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
        complete=True,
    )


def _write(workspace: Path, repo_path: str, rel: str) -> str:
    """Create a real file under <workspace>/<repo_path>/<rel> and return rel."""
    target = workspace / repo_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("// real\n", encoding="utf-8")
    return rel


# ── flag-on: existence prepass skips the agent ───────────────────────────────


@pytest.mark.asyncio
async def test_flag_on_all_paths_exist_skips_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    repo = "repo"
    # Increment-A prepass joins workspace_root + repo_path + path, so the
    # task path is repo-relative (no repo_path prefix).
    rel = _write(tmp_path, repo, "src/real/Comp.tsx")
    dag = _dag(_task(file_scope=[(rel, "modify")], repo_path=repo))

    runner = _Runner(str(tmp_path))
    corrected, records, errors = await TaskPlanningPhase._resolve_dag_paths_for_persistence(
        runner,
        _feature(),
        dag,
        context="slice test",
        resolution_key="slug:slice-1",
    )

    assert runner.run_calls == []  # prepass skipped the agent
    assert corrected is dag  # unchanged
    assert records == []
    assert errors == []
    assert "dag-path-resolution:slug:slice-1" not in runner.artifacts.store


# ── flag-on: phantom path -> resolver invoked once, dag rewritten ────────────


@pytest.mark.asyncio
async def test_flag_on_phantom_path_resolver_rewrites_dag(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    repo = "repo"
    phantom = f"{repo}/src/phantom/Comp.tsx"
    canon_rel = _write(tmp_path, repo, "src/real/browser/Comp.tsx")
    canon = f"{repo}/{canon_rel}"
    dag = _dag(_task(file_scope=[(phantom, "modify")], repo_path=repo))

    resolution = DagPathResolution(
        decisions=[
            DagPathDecision(
                task_id="T1",
                field="file_scope[0].path",
                original=phantom,
                resolved=canon,
                decision="correct",
                evidence="glob: src/real/browser/Comp.tsx",
            )
        ],
        corrected_count=1,
    )
    runner = _Runner(str(tmp_path), resolution=resolution)

    corrected, records, errors = await TaskPlanningPhase._resolve_dag_paths_for_persistence(
        runner,
        _feature(),
        dag,
        context="slice test",
        resolution_key="slug:slice-1",
    )

    assert len(runner.run_calls) == 1  # resolver invoked exactly once
    actor_name, prompt = runner.run_calls[0]
    assert actor_name == "dag-path-resolver-slug:slice-1"
    assert phantom in prompt and repo in prompt  # unresolved path + repo embedded
    assert errors == []
    assert corrected.tasks[0].file_scope[0].path == canon
    assert dag.tasks[0].file_scope[0].path == phantom  # original not mutated
    assert len(records) == 1 and records[0]["canonical"] == canon
    # resolution persisted for replay stability
    assert "dag-path-resolution:slug:slice-1" in runner.artifacts.store


# ── flag-on: ambiguous -> non-retryable failure, dag unchanged ───────────────


@pytest.mark.asyncio
async def test_flag_on_ambiguous_fails_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    repo = "repo"
    phantom = f"{repo}/src/phantom/Comp.tsx"
    dag = _dag(_task(file_scope=[(phantom, "modify")], repo_path=repo))

    resolution = DagPathResolution(
        decisions=[
            DagPathDecision(
                task_id="T1",
                field="file_scope[0].path",
                original=phantom,
                decision="ambiguous",
                evidence="no unique match found",
            )
        ],
        ambiguous_count=1,
    )
    runner = _Runner(str(tmp_path), resolution=resolution)

    corrected, records, errors = await TaskPlanningPhase._resolve_dag_paths_for_persistence(
        runner,
        _feature(),
        dag,
        context="slice test",
        resolution_key="slug:slice-1",
    )

    assert len(runner.run_calls) == 1
    assert errors  # non-empty -> call sites surface non-retryable failure
    assert corrected is dag  # unchanged
    assert records == []


# ── replay-stability: persisted resolution reused, no re-dispatch ────────────


@pytest.mark.asyncio
async def test_replay_stability_reuses_persisted_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    repo = "repo"
    phantom = f"{repo}/src/phantom/Comp.tsx"
    canon_rel = _write(tmp_path, repo, "src/real/browser/Comp.tsx")
    canon = f"{repo}/{canon_rel}"
    dag = _dag(_task(file_scope=[(phantom, "modify")], repo_path=repo))

    resolution = DagPathResolution(
        decisions=[
            DagPathDecision(
                task_id="T1",
                field="file_scope[0].path",
                original=phantom,
                resolved=canon,
                decision="correct",
                evidence="glob",
            )
        ],
        corrected_count=1,
    )
    runner = _Runner(str(tmp_path), resolution=resolution)
    feature = _feature()

    first, _records1, _errs1 = await TaskPlanningPhase._resolve_dag_paths_for_persistence(
        runner, feature, dag, context="slice test", resolution_key="slug:slice-1",
    )
    assert len(runner.run_calls) == 1
    assert first.tasks[0].file_scope[0].path == canon

    # Second pass over the same DAG: must reuse the persisted artifact, not redispatch.
    runner._resolution = None  # any new dispatch would now raise
    second, _records2, _errs2 = await TaskPlanningPhase._resolve_dag_paths_for_persistence(
        runner, feature, dag, context="slice test", resolution_key="slug:slice-1",
    )
    assert len(runner.run_calls) == 1  # NOT re-invoked
    assert second.tasks[0].file_scope[0].path == canon
    assert second.model_dump() == first.model_dump()  # identical corrected DAG


# ── flag-off: legacy static shim path, resolver never invoked ────────────────


@pytest.mark.asyncio
async def test_flag_off_uses_legacy_canonicalize(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "0")
    # A retired backend prefix the legacy shim rewrites; no agent involvement.
    task = _task(
        task_id="T-backend-1",
        file_scope=[
            (
                "iriai-studio-backend/src/iriai_studio_backend/security/hooks.py",
                "create",
            ),
        ],
        repo_path="iriai-studio-backend",
    )
    dag = _dag(task)
    runner = _Runner(str(tmp_path))  # no resolution -> any dispatch raises

    corrected, records, errors = await TaskPlanningPhase._resolve_dag_paths_for_persistence(
        runner,
        _feature(),
        dag,
        context="slice test",
        resolution_key="slug:slice-1",
    )

    assert runner.run_calls == []  # resolver never invoked under flag-off
    assert errors == []
    assert corrected.tasks[0].file_scope[0].path == (
        "iriai-studio-backend/iriai_studio_backend/security/hooks.py"
    )
    assert records and records[0]["rule"].startswith("backend-src")


# ── call-site wiring: through _validate_slice_fragment (slice seam) ──────────


@pytest.mark.asyncio
async def test_validate_slice_fragment_threads_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_DAG_PATH_AGENTIC_RESOLVER", "1")
    from iriai_build_v2.workflows.planning.phases import task_planning as tp

    repo = "repo"
    phantom = "src/phantom/Comp.tsx"
    canon = _write(tmp_path, repo, "src/real/browser/Comp.tsx")

    slice_info = tp.TaskPlanningSlice(
        slice_id="slice-1",
        step_ids=["STEP-1"],
        requirement_ids=[],
        acceptance_criterion_ids=[],
        strict_acceptance_criteria=False,
    )
    task = ImplementationTask(
        id="T-accounts-1",
        name="Implement accounts",
        description="accounts task",
        subfeature_id="accounts",
        step_ids=["STEP-1"],
        requirement_ids=[],
        repo_path=repo,
        file_scope=[TaskFileScope(path=phantom, action="modify")],
        acceptance_criteria=[
            TaskAcceptanceCriterion(description="accounts acceptance criterion"),
        ],
        reference_material=[
            TaskReference(source="Plan STEP-1", content="accounts reference material"),
        ],
        verification_gates=[],
    )
    dag = _dag(task)

    resolution = DagPathResolution(
        decisions=[
            DagPathDecision(
                task_id="T-accounts-1",
                field="file_scope[0].path",
                original=phantom,
                resolved=canon,
                decision="correct",
                evidence="glob",
            )
        ],
        corrected_count=1,
    )
    runner = _Runner(str(tmp_path), resolution=resolution)

    validated, error, retryable = await TaskPlanningPhase._validate_slice_fragment(
        runner,
        _feature(),
        "accounts",
        slice_info,
        dag,
    )

    assert error is None and retryable is False
    assert validated is not None
    assert validated.tasks[0].file_scope[0].path == canon
    assert len(runner.run_calls) == 1
    assert runner.run_calls[0][0] == "dag-path-resolver-accounts:slice-1"
