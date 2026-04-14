from __future__ import annotations

from types import SimpleNamespace

import pytest
from iriai_compose import Ask

from iriai_build_v2.models.outputs import (
    HandoverDoc,
    ImplementationResult,
    RootCauseAnalysis,
    TaskOutcome,
    Verdict,
)
from iriai_build_v2.roles import implementer, qa_engineer
from iriai_build_v2.workflows.develop.phases.implementation import (
    _run_regression,
    _single_rca_fix_verify,
)


class _Artifacts:
    def __init__(self) -> None:
        self.records: list[tuple[str, object]] = []

    async def get(self, key: str, *, feature: object):
        del key, feature
        return None

    async def put(self, key: str, value: object, *, feature: object) -> None:
        del feature
        self.records.append((key, value))


class _Runner:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.artifacts = _Artifacts()
        self.services: dict[str, object] = {}
        self.tasks: list[object] = []

    async def run(self, _task, _feature, *, phase_name: str = "") -> object:
        del phase_name
        self.tasks.append(_task)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_single_rca_fix_verify_preserves_full_verdict_text(
    monkeypatch: pytest.MonkeyPatch,
):
    long_verdict = "Verifier detail " * 120
    long_fix_summary = "Applied detailed fix " * 120
    runner = _Runner([
        RootCauseAnalysis(
            hypothesis="A long-form hypothesis",
            evidence=["Evidence 1", "Evidence 2"],
            affected_files=["repo/service.py"],
            proposed_approach="Adjust the service behavior",
            confidence="high",
        ),
        ImplementationResult(
            task_id="task-1",
            summary=long_fix_summary,
            files_modified=["repo/service.py"],
        ),
        Verdict(approved=True, summary="Looks good"),
    ])

    async def _fake_commit_repos(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._commit_repos",
        _fake_commit_repos,
    )

    attempt = await _single_rca_fix_verify(
        runner,
        SimpleNamespace(id="feat-1", slug="feat-1", workspace_id="main"),
        long_verdict,
        "verify",
        qa_engineer,
        implementer,
        prior_context="",
        bug_id="VERIFY-FAIL-1",
        attempt_number=1,
        skip_regression=True,
    )

    assert attempt.description == long_verdict
    assert attempt.fix_applied == long_fix_summary


@pytest.mark.asyncio
async def test_single_rca_fix_verify_uses_workspace_override_for_rca(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    runner = _Runner([
        RootCauseAnalysis(
            hypothesis="Lane-local root cause",
            evidence=["Checked lane copy"],
            affected_files=["repo/service.py"],
            proposed_approach="Adjust lane-local service behavior",
            confidence="high",
        ),
        ImplementationResult(
            task_id="task-1",
            summary="Applied lane-local fix",
            files_modified=["repo/service.py"],
        ),
        Verdict(approved=True, summary="Looks good"),
    ])

    async def _fake_commit_repos_in_root(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._commit_repos_in_root",
        _fake_commit_repos_in_root,
    )

    lane_root = tmp_path / "lane" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)

    await _single_rca_fix_verify(
        runner,
        SimpleNamespace(id="feat-1", slug="feat-1", workspace_id="main"),
        "Verifier detail",
        "verify",
        qa_engineer,
        implementer,
        prior_context="",
        bug_id="VERIFY-FAIL-2",
        attempt_number=1,
        skip_regression=True,
        workspace_root=lane_root,
        rca_runtime="secondary",
    )

    rca_task = runner.tasks[0]
    assert isinstance(rca_task, Ask)
    assert rca_task.actor.role.metadata.get("workspace_override") == str(lane_root)
    assert rca_task.actor.role.metadata.get("runtime") == "secondary"


@pytest.mark.asyncio
async def test_single_rca_fix_verify_uses_actor_factory_for_nested_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    runner = _Runner([
        RootCauseAnalysis(
            hypothesis="Lane-local root cause",
            evidence=["Checked lane copy"],
            affected_files=["repo/service.py"],
            proposed_approach="Adjust lane-local service behavior",
            confidence="high",
        ),
        ImplementationResult(
            task_id="task-1",
            summary="Applied lane-local fix",
            files_modified=["repo/service.py"],
        ),
        Verdict(approved=True, summary="Looks good"),
    ])

    async def _fake_commit_repos_in_root(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(
        "iriai_build_v2.workflows.develop.phases.implementation._commit_repos_in_root",
        _fake_commit_repos_in_root,
    )

    lane_root = tmp_path / "lane" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)
    thread_runtime = SimpleNamespace(name="thread-runtime")

    def _thread_factory(base, suffix, *, runtime=None, workspace_path=None):
        metadata = dict(base.role.metadata)
        if runtime:
            metadata["runtime"] = runtime
        if workspace_path:
            metadata["workspace_override"] = workspace_path
        metadata["runtime_instance"] = thread_runtime
        role = base.role.model_copy(update={"metadata": metadata})
        return base.model_copy(update={"name": f"{base.name}-{suffix}", "role": role})

    await _single_rca_fix_verify(
        runner,
        SimpleNamespace(id="feat-1", slug="feat-1", workspace_id="main"),
        "Verifier detail",
        "verify",
        qa_engineer,
        implementer,
        prior_context="",
        bug_id="VERIFY-FAIL-3",
        attempt_number=1,
        skip_regression=True,
        workspace_root=lane_root,
        rca_runtime="secondary",
        actor_factory=_thread_factory,
    )

    for task in runner.tasks[:3]:
        assert isinstance(task, Ask)
        assert task.actor.role.metadata.get("runtime_instance") is thread_runtime


@pytest.mark.asyncio
async def test_run_regression_uses_actor_factory_for_all_regression_agents(tmp_path):
    runner = _Runner([
        Verdict(approved=True, summary="Regression clean"),
        Verdict(approved=True, summary="Integration regression clean"),
    ])
    thread_runtime = SimpleNamespace(name="thread-runtime")

    def _thread_factory(base, suffix, *, runtime=None, workspace_path=None):
        metadata = dict(base.role.metadata)
        if runtime:
            metadata["runtime"] = runtime
        if workspace_path:
            metadata["workspace_override"] = workspace_path
        metadata["runtime_instance"] = thread_runtime
        role = base.role.model_copy(update={"metadata": metadata})
        return base.model_copy(update={"name": f"{base.name}-{suffix}", "role": role})

    lane_root = tmp_path / "lane" / "repos"
    lane_root.mkdir(parents=True, exist_ok=True)

    await _run_regression(
        runner,
        SimpleNamespace(id="feat-1", slug="feat-1", workspace_id="main"),
        ["repo/service.py"],
        handover_context="Journey coverage",
        workspace_root=lane_root,
        regression_runtime="secondary",
        integration_runtime="secondary",
        actor_factory=_thread_factory,
    )

    assert len(runner.tasks) == 2
    for task in runner.tasks:
        assert isinstance(task, Ask)
        assert task.actor.role.metadata.get("runtime") == "secondary"
        assert task.actor.role.metadata.get("runtime_instance") is thread_runtime


def test_handover_compress_keeps_full_file_lists() -> None:
    handover = HandoverDoc(
        completed=[
            TaskOutcome(
                task_id="t1",
                summary="done " * 200,
                files_changed=[f"file-{i}.py" for i in range(10)],
            ),
            TaskOutcome(
                task_id="t2",
                summary="done " * 200,
                files_changed=["other.py"],
            ),
            TaskOutcome(
                task_id="t3",
                summary="done " * 200,
                files_changed=["third.py"],
            ),
        ],
    )

    handover.compress(max_chars=1, keep_recent=1)

    assert "file-0.py" in handover.summary_of_prior_work
    assert "file-9.py" in handover.summary_of_prior_work
