from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose import Phase, Workflow, Workspace
from pydantic import BaseModel

from iriai_build_v2.models.outputs import (
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
    Observation,
    ObservationReport,
    RootCauseAnalysis,
    Verdict,
)
from iriai_build_v2.models.state import BuildState
from iriai_build_v2.workflows._runner import TrackedWorkflowRunner, WorkflowQuiesced
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module
from iriai_build_v2.workflows.develop.phases import post_test_observation as post_test_module
from iriai_build_v2.workflows.develop.phases.post_test_observation import (
    PostTestObservationPhase,
)


class _FeatureStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.transitions: list[str] = []

    async def transition_phase(self, feature_id: str, new_phase: str) -> None:
        del feature_id
        self.transitions.append(new_phase)

    async def log_event(
        self,
        feature_id: str,
        event_type: str,
        source: str,
        content: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        del feature_id
        self.events.append({
            "event_type": event_type,
            "source": source,
            "content": content,
            "metadata": metadata or {},
        })


class _ContextProvider:
    async def resolve(self, *_args, **_kwargs) -> str:
        return ""


class _Artifacts:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store = dict(initial or {})

    async def get(self, key: str, *, feature) -> str:
        del feature
        return self.store.get(key, "")

    async def put(self, key: str, value: str, *, feature) -> None:
        del feature
        self.store[key] = value


class _Runtime:
    name = "fake"


def _feature(feature_id: str = "feat-quiesce") -> SimpleNamespace:
    return SimpleNamespace(id=feature_id, workspace_id="main", name="Feature", metadata={})


def _empty_workspace_tree_digest(feature_id: str = "feat-quiesce") -> str:
    payload = {
        "feature_id": feature_id,
        "feature_root": "",
        "repos": [],
    }
    return implementation_module.hashlib.sha256(
        implementation_module.json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def _always_fresh_source_push(*_args, **_kwargs) -> bool:
    return True


async def _always_fresh_group_checkpoint(*_args, **_kwargs) -> bool:
    return True


def _runner(feature_store: _FeatureStore, artifacts: _Artifacts | None = None) -> TrackedWorkflowRunner:
    return TrackedWorkflowRunner(
        feature_store=feature_store,
        agent_runtime=_Runtime(),
        secondary_runtime=None,
        interaction_runtimes={"terminal": object()},
        artifacts=artifacts or _Artifacts(),
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )


def _post_test_workflow_blocker(artifacts: _Artifacts, failure_type: str) -> dict[str, object]:
    return json.loads(artifacts.store[f"workflow-blocker:post-test:{failure_type}"])


def _post_dag_gate_artifacts() -> dict[str, str]:
    tree_digest = _empty_workspace_tree_digest()
    artifacts: dict[str, str] = {}
    for gate_key in post_test_module._POST_DAG_REQUIRED_GATE_KEYS:
        gate_name = gate_key.removeprefix("dag-gate:")
        artifacts[gate_key] = "approved"
        artifacts[implementation_module._post_dag_gate_proof_key(gate_name)] = (
            implementation_module.json.dumps(
                {
                    "artifact_schema": "dag-post-gate-proof-v1",
                    "gate": gate_name,
                    "approved": True,
                    "tree_digest": tree_digest,
                },
                sort_keys=True,
            )
        )
    artifacts[implementation_module._source_push_proof_key()] = (
        _source_push_proof_for_digest(tree_digest)
    )
    report = "<html>implementation report</html>"
    report_sha = implementation_module.hashlib.sha256(
        report.encode("utf-8")
    ).hexdigest()
    artifacts["implementation-report"] = report
    artifacts["implementation-report-metadata"] = implementation_module.json.dumps(
        {
            "artifact_schema": "implementation-report-metadata-v1",
            "tree_digest": tree_digest,
            "report_url": "",
            "backlog_url": "",
            "backlog_items": [],
            "report_body_sha256": report_sha,
            "publish_status": "complete",
        },
        sort_keys=True,
    )
    _add_notify_delivery_artifacts(artifacts, tree_digest)
    return artifacts


def _legacy_post_dag_gate_artifacts() -> dict[str, str]:
    tree_digest = _empty_workspace_tree_digest()
    artifacts: dict[str, str] = {}
    for gate_key in post_test_module._POST_DAG_REQUIRED_GATE_KEYS:
        gate_name = gate_key.removeprefix("dag-gate:")
        artifacts[gate_key] = (
            implementation_module.ImplementationResult(
                task_id="TEST-AUTHOR",
                summary="tests written",
            ).model_dump_json()
            if gate_name == "test-authoring"
            else "approved"
        )
    artifacts[implementation_module._source_push_proof_key()] = (
        _source_push_proof_for_digest(tree_digest)
    )
    return artifacts


def _legacy_group_checkpoint(*, task_id: str = "TASK-0", commit_hash: str = "head") -> str:
    return json.dumps(
        {
            "group_idx": 0,
            "task_ids": [task_id],
            "results": [
                implementation_module.ImplementationResult(
                    task_id=task_id,
                    summary="legacy task completed before control-plane adoption",
                ).model_dump()
            ],
            "verdict": "approved",
            "commit_hash": commit_hash,
        },
        sort_keys=True,
    )


def _source_push_proof_for_digest(
    tree_digest: str,
    *,
    repos: dict[str, object] | None = None,
) -> str:
    proof = implementation_module._finalize_source_push_proof(
        {
            "artifact_schema": "dag-source-push-proof-v1",
            "tree_digest": tree_digest,
            "repos_root": "",
            "expected_origins": {},
            "repos": repos if repos is not None else {
                "app": {
                    "status": "recovered",
                    "tree_digest": tree_digest,
                    "repo": "app",
                    "branch": "main",
                    "local_head": "head",
                    "remote_ref": "refs/heads/main",
                    "remote_before": "old-head",
                    "remote_after": "head",
                    "expected_origin": "",
                    "actual_origin": "",
                }
            },
        }
    )
    return implementation_module.json.dumps(proof, sort_keys=True)


def _add_notify_delivery_artifacts(
    artifacts: dict[str, str],
    tree_digest: str,
    *,
    feature_id: str = "feat-quiesce",
) -> None:
    notification_sha256 = implementation_module.hashlib.sha256(
        b"post-dag notification fixture"
    ).hexdigest()
    delivery_id = implementation_module.hashlib.sha256(
        implementation_module.json.dumps(
            {
                "feature_id": feature_id,
                "tree_digest": tree_digest,
                "notification_sha256": notification_sha256,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    artifacts["dag-notify-delivery"] = implementation_module.json.dumps(
        {
            "artifact_schema": "dag-notify-delivery-v1",
            "delivery_id": delivery_id,
            "tree_digest": tree_digest,
            "notification_sha256": notification_sha256,
            "status": "sent",
        },
        sort_keys=True,
    )
    proof_key = implementation_module._post_dag_gate_proof_key("notify")
    proof = implementation_module.json.loads(artifacts[proof_key])
    proof["delivery_id"] = delivery_id
    proof["notification_sha256"] = notification_sha256
    artifacts[proof_key] = implementation_module.json.dumps(proof, sort_keys=True)


def _post_dag_gate_artifacts_for_digest(
    tree_digest: str,
    *,
    omit: set[str] | None = None,
) -> dict[str, str]:
    omitted = set(omit or set())
    artifacts: dict[str, str] = {}
    for gate_key in post_test_module._POST_DAG_REQUIRED_GATE_KEYS:
        gate_name = gate_key.removeprefix("dag-gate:")
        if gate_name in omitted:
            continue
        artifacts[gate_key] = (
            implementation_module.ImplementationResult(
                task_id="TEST-AUTHOR",
                summary="tests written",
            ).model_dump_json()
            if gate_name == "test-authoring"
            else "approved"
        )
        artifacts[implementation_module._post_dag_gate_proof_key(gate_name)] = (
            implementation_module.json.dumps(
                {
                    "artifact_schema": "dag-post-gate-proof-v1",
                    "gate": gate_name,
                    "approved": True,
                    "tree_digest": tree_digest,
                },
                sort_keys=True,
            )
        )
    if "source-push" not in omitted:
        artifacts[implementation_module._source_push_proof_key()] = (
            _source_push_proof_for_digest(tree_digest)
        )
    if "implementation-report" not in omitted:
        report = "<html>implementation report</html>"
        report_sha = implementation_module.hashlib.sha256(
            report.encode("utf-8")
        ).hexdigest()
        artifacts["implementation-report"] = report
        artifacts["implementation-report-metadata"] = implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "tree_digest": tree_digest,
                "report_url": "",
                "backlog_url": "",
                "backlog_items": [],
                "report_body_sha256": report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        )
    if "notify" not in omitted:
        _add_notify_delivery_artifacts(artifacts, tree_digest)
    return artifacts


@pytest.mark.asyncio
async def test_execute_workflow_stops_on_quiesce_without_advancing() -> None:
    calls: list[str] = []

    class FirstPhase(Phase):
        name = "implementation"

        async def on_done(self, runner, feature, state) -> None:
            del runner, feature, state
            calls.append("first_done")

        async def execute(self, runner, feature, state: BaseModel) -> BaseModel:
            del runner, feature
            calls.append("first_execute")
            raise WorkflowQuiesced(
                phase_name=self.name,
                reason="operator boundary",
                metadata={"before_group_idx": 45},
            )

    class SecondPhase(Phase):
        name = "post-test-observation"

        async def execute(self, runner, feature, state: BaseModel) -> BaseModel:
            del runner, feature
            calls.append("second_execute")
            return state

    class TestWorkflow(Workflow):
        name = "test"

        def build_phases(self) -> list[type[Phase]]:
            return [FirstPhase, SecondPhase]

        async def on_done(self, runner, feature, state) -> None:
            del runner, feature, state
            calls.append("workflow_done")

    feature_store = _FeatureStore()
    runner = _runner(feature_store)
    state = BuildState()

    result = await runner.execute_workflow(TestWorkflow(), _feature(), state)

    assert result is state
    assert calls == ["first_execute", "first_done", "workflow_done"]
    assert feature_store.transitions == ["implementation"]
    event_types = [event["event_type"] for event in feature_store.events]
    assert "phase_execute_quiesced" in event_types
    assert "phase_execute_done" not in event_types
    assert "phase_execute_error" not in event_types
    assert feature_store.events[-1]["metadata"]["before_group_idx"] == 45
    assert runner.last_workflow_quiesce is not None
    assert runner.last_workflow_quiesce.phase_name == "implementation"


@pytest.mark.asyncio
async def test_resume_workflow_stops_on_quiesce_without_advancing() -> None:
    calls: list[str] = []

    class PlanningPhase(Phase):
        name = "planning"

        async def execute(self, runner, feature, state: BaseModel) -> BaseModel:
            del runner, feature
            calls.append("planning_execute")
            return state

    class ImplementationPhase(Phase):
        name = "implementation"

        async def on_done(self, runner, feature, state) -> None:
            del runner, feature, state
            calls.append("implementation_done")

        async def execute(self, runner, feature, state: BaseModel) -> BaseModel:
            del runner, feature
            calls.append("implementation_execute")
            raise WorkflowQuiesced(phase_name=self.name, reason="regroup missing")

    class ObservationPhase(Phase):
        name = "post-test-observation"

        async def execute(self, runner, feature, state: BaseModel) -> BaseModel:
            del runner, feature
            calls.append("observation_execute")
            return state

    class TestWorkflow(Workflow):
        name = "test"

        def build_phases(self) -> list[type[Phase]]:
            return [PlanningPhase, ImplementationPhase, ObservationPhase]

        async def on_done(self, runner, feature, state) -> None:
            del runner, feature, state
            calls.append("workflow_done")

    feature_store = _FeatureStore()
    runner = _runner(feature_store)
    state = BuildState()

    result = await runner.resume_workflow(
        TestWorkflow(),
        _feature(),
        state,
        resume_from_phase="implementation",
    )

    assert result is state
    assert calls == ["implementation_execute", "implementation_done", "workflow_done"]
    assert feature_store.transitions == ["implementation"]
    event_types = [event["event_type"] for event in feature_store.events]
    assert "phase_skipped" in event_types
    assert "phase_execute_quiesced" in event_types
    assert "phase_execute_done" not in event_types
    assert "phase_execute_error" not in event_types
    assert runner.last_workflow_quiesce is not None
    assert runner.last_workflow_quiesce.phase_name == "implementation"


@pytest.mark.asyncio
async def test_implementation_phase_raises_quiesced_after_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({"dag": dag.model_dump_json()})
    runner = _runner(_FeatureStore(), artifacts)

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="partial implementation",
            failure="DAG dispatch paused before group 45: regroup marker missing.",
            handover=implementation_module.HandoverDoc(),
            terminal_state="quiesced",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module.ImplementationPhase().execute(
            runner,
            _feature(),
            BuildState(),
        )

    assert exc_info.value.phase_name == "implementation"
    assert "regroup marker missing" in exc_info.value.reason
    assert artifacts.store["implementation"] == "partial implementation"
    assert "handover" in artifacts.store


@pytest.mark.asyncio
async def test_implementation_phase_verify_failed_workflow_blocker_quiesces_before_rca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({"dag": dag.model_dump_json()})
    runner = _runner(_FeatureStore(), artifacts)

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="partial implementation",
            failure="SANDBOX_WORKFLOW_BLOCKER: sandbox lease is terminal",
            handover=implementation_module.HandoverDoc(),
            terminal_state="verify_failed",
        )

    async def _must_not_diagnose(*_args, **_kwargs):
        raise AssertionError("workflow blockers must quiesce before RCA")

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_diagnose_and_fix", _must_not_diagnose)
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module.ImplementationPhase().execute(
            runner,
            _feature(),
            BuildState(),
        )

    assert exc_info.value.reason == "SANDBOX_WORKFLOW_BLOCKER: sandbox lease is terminal"
    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    assert artifacts.store["implementation"] == "partial implementation"
    assert "workflow-blocker:verify" in artifacts.store


@pytest.mark.asyncio
async def test_implementation_phase_test_authoring_gate_waits_for_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({"dag": dag.model_dump_json()})

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}
            self.output_types: list[type] = []

        async def run(self, ask, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(ask.output_type)
            if ask.output_type is Verdict:
                return Verdict(approved=True, summary="approved")
            if ask.output_type is implementation_module.ImplementationResult:
                return implementation_module.ImplementationResult(
                    task_id="TEST-AUTHOR",
                    summary="tests written",
                )
            raise AssertionError(f"unexpected ask: {ask!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _context(*_args, **_kwargs) -> str:
        return ""

    async def _commit_repos(*_args, **_kwargs):
        raise implementation_module.WorkflowCommitError(
            "test-authoring commit failed",
            [
                implementation_module.CommitRepoOutcome(
                    repo_path="/tmp/repo",
                    repo_name="repo",
                    message="test: add tests",
                    exit_code=1,
                    stderr="hook failed",
                    error="hook failed",
                )
            ],
        )

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_repos)

    with pytest.raises(implementation_module.WorkflowCommitError):
        await implementation_module.ImplementationPhase().execute(
            _Runner(),
            _feature(),
            BuildState(),
        )

    assert "test-authoring" in artifacts.store
    assert "dag-gate:test-authoring" not in artifacts.store


@pytest.mark.asyncio
async def test_implementation_phase_reruns_static_gates_after_test_authoring_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({"dag": dag.model_dump_json()})
    digest_state = {"value": "before"}
    prompts: list[str] = []
    implement_calls = 0

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, implementation_module.Notify):
                return None
            prompts.append(getattr(task, "prompt", ""))
            if getattr(task, "output_type", None) is Verdict:
                return Verdict(approved=True, summary="approved")
            if getattr(task, "output_type", None) is implementation_module.ImplementationResult:
                return implementation_module.ImplementationResult(
                    task_id="TEST-AUTHOR",
                    summary="tests written",
                )
            raise AssertionError(f"unexpected task: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        nonlocal implement_calls
        implement_calls += 1
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _commit_repos(*_args, **_kwargs):
        digest_state["value"] = "after"
        return "test-authoring-commit"

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _context(*_args, **_kwargs) -> str:
        return ""

    async def _push(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_repos)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _push)
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: digest_state["value"],
    )

    await implementation_module.ImplementationPhase().execute(
        _Runner(),
        _feature(),
        BuildState(),
    )

    assert implement_calls == 2
    assert sum("Review the implementation" in prompt for prompt in prompts) == 2
    assert sum("Audit the implementation" in prompt for prompt in prompts) == 2
    code_review_proof = implementation_module.json.loads(
        artifacts.store[implementation_module._post_dag_gate_proof_key("code-review")]
    )
    security_proof = implementation_module.json.loads(
        artifacts.store[implementation_module._post_dag_gate_proof_key("security")]
    )
    assert code_review_proof["tree_digest"] == "after"
    assert security_proof["tree_digest"] == "after"


@pytest.mark.asyncio
async def test_implementation_phase_source_push_stale_digest_quiesces_without_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        **_post_dag_gate_artifacts_for_digest(
            "before",
            omit={"source-push", "implementation-report", "notify"},
        ),
    })
    pushed = False

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            raise AssertionError(f"all earlier gates should be fresh: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _push(*_args, **_kwargs) -> None:
        nonlocal pushed
        pushed = True

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _context(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _push)
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "after" if pushed else "before",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module.ImplementationPhase().execute(
            _Runner(),
            _feature(),
            BuildState(),
        )

    assert exc_info.value.metadata["failure_type"] == "source_push_stale_gate_digest"
    body = implementation_module.json.loads(artifacts.store["dag-runtime-failure:source-push"])
    assert body["failure_type"] == "source_push_stale_gate_digest"
    assert "dag-gate:source-push" not in artifacts.store
    assert implementation_module._post_dag_gate_proof_key("source-push") not in artifacts.store


@pytest.mark.asyncio
async def test_source_push_gate_freshness_rejects_missing_feature_root_artifact_proof() -> None:
    tree_digest = "tree-digest-without-workspace"
    artifacts = _Artifacts({
        "dag-gate:source-push": "approved",
        implementation_module._post_dag_gate_proof_key("source-push"): (
            implementation_module.json.dumps(
                {
                    "artifact_schema": "dag-post-gate-proof-v1",
                    "gate": "source-push",
                    "approved": True,
                    "tree_digest": tree_digest,
                },
                sort_keys=True,
            )
        ),
        implementation_module._source_push_proof_key(): _source_push_proof_for_digest(
            tree_digest
        ),
    })

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

    assert await implementation_module._post_dag_gate_is_fresh(
        _Runner(),
        _feature(),
        "source-push",
        tree_digest,
    ) is False


@pytest.mark.asyncio
async def test_implementation_phase_report_metadata_resume_notifies_without_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    persisted_report = "<html>persisted report</html>"
    persisted_report_sha = implementation_module.hashlib.sha256(
        persisted_report.encode("utf-8")
    ).hexdigest()
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "implementation-report": persisted_report,
        "implementation-report-metadata": implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "tree_digest": "resume-digest",
                "report_url": "https://reports.example/report",
                "backlog_url": "https://reports.example/backlog",
                "backlog_items": [{"description": "polish later", "severity": "minor"}],
                "report_body_sha256": persisted_report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        ),
        **_post_dag_gate_artifacts_for_digest(
            "resume-digest",
            omit={"implementation-report", "notify"},
        ),
    })
    notifications: list[str] = []

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, implementation_module.Notify):
                notifications.append(task.message)
                assert task.delivery_id
                return None
            raise AssertionError(f"report resume should only notify: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_push(*_args, **_kwargs) -> None:
        raise AssertionError("fresh source-push proof should skip source push")

    async def _context(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _unexpected_push)
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "resume-digest",
    )

    await implementation_module.ImplementationPhase().execute(
        _Runner(),
        _feature(),
        BuildState(),
    )

    assert artifacts.store["implementation-report"] == "<html>persisted report</html>"
    assert "https://reports.example/report" in notifications[0]
    assert "https://reports.example/backlog" in notifications[0]
    assert artifacts.store["dag-gate:implementation-report"] == "approved"
    assert artifacts.store["dag-gate:notify"] == "approved"
    delivery = implementation_module.json.loads(artifacts.store["dag-notify-delivery"])
    assert delivery["status"] == "sent"


@pytest.mark.asyncio
async def test_implementation_phase_partial_report_metadata_regenerates_before_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    partial_report = "<html>partial report</html>"
    partial_sha = implementation_module.hashlib.sha256(
        partial_report.encode("utf-8")
    ).hexdigest()
    initial_artifacts = {
        "dag": dag.model_dump_json(),
        **_post_dag_gate_artifacts_for_digest(
            "resume-digest",
            omit={"notify"},
        ),
    }
    initial_artifacts["implementation-report"] = partial_report
    initial_artifacts["implementation-report-metadata"] = implementation_module.json.dumps(
        {
            "artifact_schema": "implementation-report-metadata-v1",
            "tree_digest": "resume-digest",
            "report_url": "https://reports.example/partial",
            "backlog_url": "",
            "backlog_items": [],
            "report_body_sha256": partial_sha,
            "publish_status": "report_published",
        },
        sort_keys=True,
    )
    artifacts = _Artifacts(initial_artifacts)
    notifications: list[str] = []

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, implementation_module.Notify):
                notifications.append(task.message)
                return None
            raise AssertionError(f"partial report resume should only notify: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_push(*_args, **_kwargs) -> None:
        raise AssertionError("fresh source-push proof should skip source push")

    async def _context(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _unexpected_push)
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "resume-digest",
    )

    await implementation_module.ImplementationPhase().execute(
        _Runner(),
        _feature(),
        BuildState(),
    )

    assert artifacts.store["implementation-report"] != partial_report
    metadata = implementation_module.json.loads(
        artifacts.store["implementation-report-metadata"]
    )
    assert metadata["publish_status"] == "complete"
    assert metadata["report_body_sha256"] == implementation_module.hashlib.sha256(
        artifacts.store["implementation-report"].encode("utf-8")
    ).hexdigest()
    assert artifacts.store["dag-gate:implementation-report"] == "approved"
    assert artifacts.store["dag-gate:notify"] == "approved"
    assert notifications


@pytest.mark.asyncio
async def test_implementation_phase_report_failure_quiesces_without_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        **_post_dag_gate_artifacts_for_digest(
            "resume-digest",
            omit={"implementation-report", "notify"},
        ),
    })

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            raise AssertionError(f"report failure should quiesce before notify: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_push(*_args, **_kwargs) -> None:
        raise AssertionError("fresh source-push proof should skip source push")

    async def _context(*_args, **_kwargs) -> str:
        return ""

    async def _report_failure(*_args, **_kwargs):
        raise RuntimeError("hosting unavailable")

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _unexpected_push)
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_generate_and_publish_implementation_report",
        _report_failure,
    )
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "resume-digest",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module.ImplementationPhase().execute(
            _Runner(),
            _feature(),
            BuildState(),
        )

    assert exc_info.value.metadata["failure_type"] == "implementation_report_failed"
    assert "dag-gate:implementation-report" not in artifacts.store
    body = implementation_module.json.loads(
        artifacts.store["dag-runtime-failure:implementation-report"]
    )
    assert body["failure_class"] == "runtime_context"
    assert body["operator_required"] is False
    assert "hosting unavailable" in body["error"]


@pytest.mark.asyncio
async def test_implementation_phase_notify_sent_resume_backfills_gate_without_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    report = "<html>persisted report</html>"
    report_sha = implementation_module.hashlib.sha256(report.encode("utf-8")).hexdigest()
    feature = _feature()
    notification = (
        "All quality gates passed. Implementation complete.\n\n"
        "**[View Implementation Report](https://reports.example/report)**\n\n"
        "The report contains journey evidence, gate verdicts, "
        "bug fix history, and artifact references."
    )
    delivery_id = implementation_module._notify_delivery_id(
        feature,
        "resume-digest",
        notification,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "implementation-report": report,
        "implementation-report-metadata": implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "tree_digest": "resume-digest",
                "report_url": "https://reports.example/report",
                "backlog_url": "",
                "backlog_items": [],
                "report_body_sha256": report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        ),
        "dag-notify-delivery": implementation_module.json.dumps(
            {
                "artifact_schema": "dag-notify-delivery-v1",
                "delivery_id": delivery_id,
                "tree_digest": "resume-digest",
                "notification_sha256": implementation_module.hashlib.sha256(
                    notification.encode("utf-8")
                ).hexdigest(),
                "status": "sent",
            },
            sort_keys=True,
        ),
        **_post_dag_gate_artifacts_for_digest(
            "resume-digest",
            omit={"implementation-report", "notify"},
        ),
    })

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, implementation_module.Notify):
                raise AssertionError("sent delivery resume must not notify again")
            raise AssertionError(f"unexpected task on sent notify resume: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_push(*_args, **_kwargs) -> None:
        raise AssertionError("fresh source-push proof should skip source push")

    async def _context(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _unexpected_push)
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "resume-digest",
    )

    await implementation_module.ImplementationPhase().execute(
        _Runner(),
        feature,
        BuildState(),
    )

    assert artifacts.store["dag-gate:notify"] == "approved"
    assert implementation_module._post_dag_gate_proof_key("notify") in artifacts.store
    delivery = implementation_module.json.loads(artifacts.store["dag-notify-delivery"])
    assert delivery["status"] == "sent"


@pytest.mark.asyncio
async def test_implementation_phase_notify_pending_resume_quiesces_without_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    report = "<html>persisted report</html>"
    report_sha = implementation_module.hashlib.sha256(report.encode("utf-8")).hexdigest()
    feature = _feature()
    notification = (
        "All quality gates passed. Implementation complete.\n\n"
        "**[View Implementation Report](https://reports.example/report)**\n\n"
        "The report contains journey evidence, gate verdicts, "
        "bug fix history, and artifact references."
    )
    delivery_id = implementation_module._notify_delivery_id(
        feature,
        "resume-digest",
        notification,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "implementation-report": report,
        "implementation-report-metadata": implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "tree_digest": "resume-digest",
                "report_url": "https://reports.example/report",
                "backlog_url": "",
                "backlog_items": [],
                "report_body_sha256": report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        ),
        "dag-notify-delivery": implementation_module.json.dumps(
            {
                "artifact_schema": "dag-notify-delivery-v1",
                "delivery_id": delivery_id,
                "tree_digest": "resume-digest",
                "notification_sha256": implementation_module.hashlib.sha256(
                    notification.encode("utf-8")
                ).hexdigest(),
                "status": "pending",
            },
            sort_keys=True,
        ),
        **_post_dag_gate_artifacts_for_digest(
            "resume-digest",
            omit={"implementation-report", "notify"},
        ),
    })

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, implementation_module.Notify):
                raise AssertionError("pending delivery resume must not notify again")
            raise AssertionError(f"unexpected task on pending notify resume: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_push(*_args, **_kwargs) -> None:
        raise AssertionError("fresh source-push proof should skip source push")

    async def _context(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _unexpected_push)
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "resume-digest",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module.ImplementationPhase().execute(
            _Runner(),
            feature,
            BuildState(),
        )

    assert exc_info.value.metadata["failure_type"] == "notify_delivery_ambiguous"
    assert "dag-gate:notify" not in artifacts.store
    assert implementation_module._post_dag_gate_proof_key("notify") not in artifacts.store
    failure = implementation_module.json.loads(artifacts.store["dag-runtime-failure:notify"])
    assert failure["delivery_id"] == delivery_id
    assert failure["operator_required"] is False


@pytest.mark.asyncio
async def test_implementation_phase_notify_send_failure_quiesces_without_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-1", name="Task 1", description="Task 1")],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    report = "<html>persisted report</html>"
    report_sha = implementation_module.hashlib.sha256(report.encode("utf-8")).hexdigest()
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "implementation-report": report,
        "implementation-report-metadata": implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "tree_digest": "resume-digest",
                "report_url": "https://reports.example/report",
                "backlog_url": "",
                "backlog_items": [],
                "report_body_sha256": report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        ),
        **_post_dag_gate_artifacts_for_digest(
            "resume-digest",
            omit={"implementation-report", "notify"},
        ),
    })

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if isinstance(task, implementation_module.Notify):
                raise RuntimeError("missing channel")
            raise AssertionError(f"unexpected task on notify failure: {task!r}")

    async def _fake_implement_dag(*_args, **_kwargs):
        return implementation_module.DagExecutionOutcome(
            implementation_text="implementation complete",
            failure="",
            handover=implementation_module.HandoverDoc(),
            terminal_state="completed",
        )

    async def _no_refresh(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_push(*_args, **_kwargs) -> None:
        raise AssertionError("fresh source-push proof should skip source push")

    async def _context(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(implementation_module, "_implement_dag", _fake_implement_dag)
    monkeypatch.setattr(implementation_module, "_push_clones_to_source", _unexpected_push)
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _no_refresh)
    monkeypatch.setattr(implementation_module, "_build_prompt_context_package", _context)
    monkeypatch.setattr(implementation_module, "_context_package_prompt", lambda _package: "")
    monkeypatch.setattr(
        implementation_module,
        "_post_dag_gate_tree_digest",
        lambda *_args, **_kwargs: "resume-digest",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module.ImplementationPhase().execute(
            _Runner(),
            _feature(),
            BuildState(),
        )

    assert exc_info.value.metadata["failure_type"] == "notify_delivery_failed"
    assert "dag-gate:notify" not in artifacts.store
    delivery = implementation_module.json.loads(artifacts.store["dag-notify-delivery"])
    assert delivery["status"] == "pending"
    failure = implementation_module.json.loads(artifacts.store["dag-runtime-failure:notify"])
    assert failure["failure_type"] == "notify_delivery_failed"
    assert "missing channel" in failure["error"]


@pytest.mark.asyncio
async def test_post_test_observation_quiesces_when_dag_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-0", name="Task 0", description="Task 0"),
            ImplementationTask(id="TASK-1", name="Task 1", description="Task 1"),
        ],
        execution_order=[["TASK-0"], ["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must not collect when DAG is incomplete")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_dag_incomplete"
    assert exc_info.value.metadata["first_missing_group"] == 1
    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    assert exc_info.value.metadata["failure_class"] == "stale_projection"
    blocker = _post_test_workflow_blocker(artifacts, "dag_incomplete")
    assert blocker["operator_required"] is False
    assert blocker["failure_class"] == "stale_projection"


@pytest.mark.asyncio
async def test_post_dag_workflow_blocker_verdict_quiesces_before_repair() -> None:
    artifacts = _Artifacts()
    runner = _runner(_FeatureStore(), artifacts)
    verdict = Verdict(
        approved=False,
        summary="SANDBOX_WORKFLOW_BLOCKER: repair sandbox binding is required",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await implementation_module._quiesce_on_workflow_blocker_verdict(
            runner,
            _feature(),
            verdict,
            phase_name="implementation",
            source="verifier",
        )

    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    assert exc_info.value.metadata["operator_required"] is False
    assert "workflow-blocker:verifier" in artifacts.store


@pytest.mark.asyncio
async def test_post_test_observation_quiesces_when_post_dag_gate_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        **{
            key: value
            for key, value in _post_dag_gate_artifacts().items()
            if key != "dag-gate:test-authoring"
        },
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must wait for post-DAG gates")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_incomplete"
    assert exc_info.value.metadata["first_missing_gate"] == "dag-gate:test-authoring"
    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    blocker = _post_test_workflow_blocker(artifacts, "post_dag_gates_incomplete")
    assert blocker["failure_class"] == "stale_projection"


@pytest.mark.asyncio
async def test_post_test_observation_waits_for_source_push_report_and_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        **{
            key: value
            for key, value in _post_dag_gate_artifacts().items()
            if key != "dag-gate:source-push"
            and key != implementation_module._post_dag_gate_proof_key("source-push")
        },
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must wait for source push evidence")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_incomplete"
    assert exc_info.value.metadata["first_missing_gate"] == "dag-gate:source-push"


@pytest.mark.asyncio
async def test_post_test_observation_rejects_notify_gate_without_delivery_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        **_post_dag_gate_artifacts(),
    })
    artifacts.store.pop("dag-notify-delivery", None)
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must wait for notify delivery proof")

    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(
        post_test_module,
        "_dag_group_checkpoint_is_fresh",
        _always_fresh_group_checkpoint,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_stale"
    assert "dag-gate:notify" in exc_info.value.metadata["stale_gates"]
    assert exc_info.value.metadata["failure_class"] == "stale_projection"
    blocker = _post_test_workflow_blocker(artifacts, "post_dag_gates_stale")
    assert blocker["deterministic_workflow_blocker"] is True


@pytest.mark.asyncio
async def test_post_test_observation_rejects_stale_group_checkpoint_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "task_ids": ["OTHER"], "results": [], "verdict": "approved"}',
        **_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must wait for fresh checkpoint proof")

    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_dag_checkpoint_stale"
    assert exc_info.value.metadata["first_stale_group"] == 0
    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    blocker = _post_test_workflow_blocker(artifacts, "dag_checkpoint_stale")
    assert blocker["failure_class"] == "stale_projection"


@pytest.mark.asyncio
async def test_post_test_observation_rejects_unmarked_artifact_only_legacy_gates_without_slice06_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        **_legacy_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["pool"] = object()

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must wait for explicit legacy marker")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_incomplete"
    assert exc_info.value.metadata["observed_control_plane_proofs"] == 0


@pytest.mark.asyncio
async def test_post_test_observation_allows_marked_artifact_only_legacy_gates_without_slice06_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )
    collected: list[int] = []

    async def _collect(*_args, **_kwargs):
        collected.append(1)
        return ObservationReport(observations=[])

    monkeypatch.setattr(
        implementation_module,
        "_feature_repos_clean_for_checkpoint_resume",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        implementation_module,
        "_current_feature_repo_heads",
        lambda *_args, **_kwargs: "app:head",
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)

    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert collected == [1]


@pytest.mark.asyncio
async def test_post_test_observation_derives_legacy_downstream_gates_from_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    legacy_gates = {
        key: value
        for key, value in _legacy_post_dag_gate_artifacts().items()
        if key
        not in {
            "dag-gate:source-push",
            implementation_module._source_push_proof_key(),
            "dag-gate:implementation-report",
            "dag-gate:notify",
        }
    }
    report = "<html>legacy implementation report</html>"
    report_sha = implementation_module.hashlib.sha256(
        report.encode("utf-8")
    ).hexdigest()
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        implementation_module._source_push_proof_key(): _source_push_proof_for_digest(
            _empty_workspace_tree_digest()
        ),
        "implementation-report": report,
        "implementation-report-metadata": implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "report_body_sha256": report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        ),
        "dag-notify-delivery": implementation_module.json.dumps(
            {
                "artifact_schema": "dag-notify-delivery-v1",
                "delivery_id": "legacy-delivery",
                "status": "sent",
            },
            sort_keys=True,
        ),
        **legacy_gates,
    })
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )
    collected: list[int] = []

    async def _collect(*_args, **_kwargs):
        collected.append(1)
        return ObservationReport(observations=[])

    monkeypatch.setattr(
        implementation_module,
        "_feature_repos_clean_for_checkpoint_resume",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        implementation_module,
        "_current_feature_repo_heads",
        lambda *_args, **_kwargs: "app:head",
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)

    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert collected == [1]


@pytest.mark.asyncio
async def test_post_test_observation_rejects_marked_legacy_missing_source_push_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    legacy_gates = {
        key: value
        for key, value in _legacy_post_dag_gate_artifacts().items()
        if key
        not in {
            "dag-gate:source-push",
            implementation_module._source_push_proof_key(),
            "dag-gate:implementation-report",
            "dag-gate:notify",
        }
    }
    report = "<html>legacy implementation report</html>"
    report_sha = implementation_module.hashlib.sha256(
        report.encode("utf-8")
    ).hexdigest()
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        "dag-gate:source-push": "approved",
        "implementation-report": report,
        "implementation-report-metadata": implementation_module.json.dumps(
            {
                "artifact_schema": "implementation-report-metadata-v1",
                "report_body_sha256": report_sha,
                "publish_status": "complete",
            },
            sort_keys=True,
        ),
        "dag-notify-delivery": implementation_module.json.dumps(
            {
                "artifact_schema": "dag-notify-delivery-v1",
                "delivery_id": "legacy-delivery",
                "status": "sent",
            },
            sort_keys=True,
        ),
        **legacy_gates,
    })
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("legacy post-test must require source-push evidence")

    monkeypatch.setattr(
        implementation_module,
        "_feature_repos_clean_for_checkpoint_resume",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        implementation_module,
        "_current_feature_repo_heads",
        lambda *_args, **_kwargs: "app:head",
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_incomplete"
    assert exc_info.value.metadata["first_missing_gate"] == "dag-gate:source-push"


@pytest.mark.asyncio
async def test_post_test_observation_rejects_marked_legacy_empty_source_push_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    current_tree = _empty_workspace_tree_digest()
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    artifacts.store[implementation_module._source_push_proof_key()] = (
        _source_push_proof_for_digest(current_tree, repos={})
    )
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("legacy post-test must reject empty source-push proof")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_stale"
    assert exc_info.value.metadata["first_stale_gate"] == "dag-gate:source-push"


@pytest.mark.asyncio
async def test_post_test_observation_rejects_marked_legacy_stale_source_push_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    artifacts.store[implementation_module._source_push_proof_key()] = (
        _source_push_proof_for_digest("stale-legacy-tree")
    )
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("legacy post-test must reject stale source-push proof")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_stale"
    assert exc_info.value.metadata["first_stale_gate"] == "dag-gate:source-push"


@pytest.mark.asyncio
async def test_post_test_observation_rejects_marked_legacy_malformed_source_push_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    malformed = implementation_module.json.loads(
        _source_push_proof_for_digest(_empty_workspace_tree_digest())
    )
    malformed["proof_digest"] = "not-the-proof-digest"
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    artifacts.store[implementation_module._source_push_proof_key()] = (
        implementation_module.json.dumps(malformed, sort_keys=True)
    )
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("legacy post-test must reject malformed source-push proof")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_incomplete"
    assert exc_info.value.metadata["first_missing_gate"] == "dag-gate:source-push"


@pytest.mark.asyncio
async def test_post_test_observation_rejects_marked_legacy_weak_unchanged_source_push_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    current_tree = _empty_workspace_tree_digest()
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": _legacy_group_checkpoint(),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    artifacts.store[implementation_module._source_push_proof_key()] = (
        _source_push_proof_for_digest(
            current_tree,
            repos={
                "app": {
                    "status": "unchanged",
                    "tree_digest": current_tree,
                    "repo": "app",
                    "mutation_required": False,
                }
            },
        )
    )
    runner = _runner(_FeatureStore(), artifacts)
    runner.services["execution_control_store"] = SimpleNamespace(
        put_task_contract=lambda *_args, **_kwargs: None
    )

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("legacy post-test must reject weak unchanged proof")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_stale"
    assert exc_info.value.metadata["first_stale_gate"] == "dag-gate:source-push"


@pytest.mark.asyncio
async def test_legacy_source_push_status_uses_durable_freshness_with_feature_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_tree = _empty_workspace_tree_digest()
    artifacts = _Artifacts({
        implementation_module._source_push_proof_key(): _source_push_proof_for_digest(
            current_tree
        )
    })
    runner = _runner(_FeatureStore(), artifacts)
    calls: list[str] = []

    async def _not_fresh(_runner, _feature, tree_digest):
        calls.append(tree_digest)
        return False

    monkeypatch.setattr(
        post_test_module,
        "_get_feature_root",
        lambda *_args, **_kwargs: Path("/tmp/feature-root"),
    )
    monkeypatch.setattr(
        post_test_module,
        "_source_push_durable_proof_is_fresh",
        _not_fresh,
    )

    status = await post_test_module._legacy_source_push_gate_status(
        runner,
        _feature(),
        current_tree,
    )

    assert status == "stale"
    assert calls == [current_tree]


@pytest.mark.asyncio
async def test_post_test_observation_rejects_marked_legacy_gates_with_stale_checkpoint_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must validate legacy checkpoint bodies")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_dag_checkpoint_stale"
    assert exc_info.value.metadata["first_stale_group"] == 0


@pytest.mark.asyncio
async def test_post_test_observation_rejects_adopted_artifact_only_gates_without_slice06_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    feature = _feature("feat-adopted")
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        f"execution-control-adoption:{feature.id}": '{"status": "adopted"}',
        **_legacy_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must wait for Slice 06 gate proofs")

    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, feature, BuildState())

    assert exc_info.value.reason == "post_test_blocked_post_dag_gates_incomplete"
    assert exc_info.value.metadata["observed_control_plane_proofs"] == 0


@pytest.mark.asyncio
async def test_post_test_observation_checks_effective_regroup_dag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-0", name="Task 0", description="Task 0"),
            ImplementationTask(id="TASK-1", name="Task 1", description="Task 1"),
        ],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    effective_dag = base_dag.model_copy(deep=True)
    effective_dag.execution_order = [["TASK-0"], ["TASK-1"]]
    artifacts = _Artifacts({
        "dag": base_dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        implementation_module.DAG_REGROUP_ACTIVE_KEY: '{"status": "active"}',
    })
    runner = _runner(_FeatureStore(), artifacts)

    async def _fake_resolve(*_args, **_kwargs):
        return effective_dag, "", {"applied": True}

    async def _must_not_collect(*_args, **_kwargs):
        raise AssertionError("post-test observation must not collect when effective DAG is incomplete")

    monkeypatch.setattr(
        post_test_module,
        "_resolve_active_regroup_before_group_dispatch",
        _fake_resolve,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _must_not_collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert exc_info.value.reason == "post_test_blocked_dag_incomplete"
    assert exc_info.value.metadata["first_missing_group"] == 1
    assert exc_info.value.metadata["total_group_count"] == 2
    blocker = _post_test_workflow_blocker(artifacts, "dag_incomplete")
    assert blocker["deterministic_workflow_blocker"] is True


@pytest.mark.asyncio
async def test_post_test_observation_does_not_accept_base_hash_for_regroup_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = [
        ImplementationTask(id=f"TASK-{idx}", name=f"Task {idx}", description="Task")
        for idx in range(46)
    ]
    base_dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[[task.id] for task in tasks],
        complete=True,
    )
    base_sha = implementation_module.hashlib.sha256(
        base_dag.model_dump_json().encode("utf-8")
    ).hexdigest()
    artifacts_payload = {
        "dag": base_dag.model_dump_json(),
        implementation_module.DAG_REGROUP_ACTIVE_KEY: implementation_module.json.dumps(
            {"status": "active", "base_dag_sha256": base_sha},
            sort_keys=True,
        ),
        "execution-control-legacy:feat-quiesce": '{"status": "legacy-in-flight"}',
        **_legacy_post_dag_gate_artifacts(),
    }
    for idx, task in enumerate(tasks):
        artifacts_payload[f"dag-group:{idx}"] = implementation_module.json.dumps(
            {
                "group_idx": idx,
                "task_ids": [task.id],
                "results": [
                    implementation_module.ImplementationResult(
                        task_id=task.id,
                        summary="done",
                    ).model_dump()
                ],
                "verdict": "approved",
                "commit_hash": f"commit-{idx}",
            },
            sort_keys=True,
        )
    runner = _runner(_FeatureStore(), _Artifacts(artifacts_payload))
    checkpoint_calls: list[tuple[int, list[str]]] = []
    collected: list[int] = []

    async def _fake_resolve(*_args, **_kwargs):
        return base_dag, "", {"applied": True}

    async def _checkpoint_fresh(*_args, **kwargs):
        checkpoint_calls.append(
            (
                int(kwargs["group_idx"]),
                list(kwargs.get("accepted_dag_sha256s") or []),
            )
        )
        return True

    async def _collect(*_args, **_kwargs):
        collected.append(1)
        return ObservationReport(observations=[])

    monkeypatch.setattr(
        post_test_module,
        "_resolve_active_regroup_before_group_dispatch",
        _fake_resolve,
    )
    monkeypatch.setattr(
        post_test_module,
        "_dag_group_checkpoint_is_fresh",
        _checkpoint_fresh,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)

    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert collected == [1]
    assert dict(checkpoint_calls)[44] == [base_sha]
    assert dict(checkpoint_calls)[45] == []


@pytest.mark.asyncio
async def test_post_test_observation_allows_completed_legacy_g45_without_active_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = [
        ImplementationTask(id=f"TASK-{idx}", name=f"Task {idx}", description=f"Task {idx}")
        for idx in range(46)
    ]
    dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[[task.id] for task in tasks],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        **{
            f"dag-group:{idx}": f'{{"group_idx": {idx}, "results": []}}'
            for idx in range(46)
        },
        **_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)
    collected: list[int] = []

    async def _must_not_resolve(*_args, **_kwargs):
        raise AssertionError("legacy completed post-G45 DAG must not require active regroup marker")

    async def _collect(*_args, **_kwargs):
        collected.append(1)
        return ObservationReport(observations=[])

    monkeypatch.setattr(
        post_test_module,
        "_resolve_active_regroup_before_group_dispatch",
        _must_not_resolve,
    )
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    checkpoint_calls: list[dict[str, object]] = []

    async def _checkpoint_fresh(*_args, **kwargs):
        checkpoint_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        post_test_module,
        "_dag_group_checkpoint_is_fresh",
        _checkpoint_fresh,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)

    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert collected == [1]
    assert checkpoint_calls


@pytest.mark.asyncio
async def test_post_test_observation_collects_when_effective_regroup_dag_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    effective_dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-0", name="Task 0", description="Task 0"),
            ImplementationTask(id="TASK-1", name="Task 1", description="Task 1"),
        ],
        execution_order=[["TASK-0"], ["TASK-1"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": base_dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        "dag-group:1": '{"group_idx": 1, "results": []}',
        implementation_module.DAG_REGROUP_ACTIVE_KEY: '{"status": "active"}',
        **_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)
    collected: list[int] = []

    async def _fake_resolve(*_args, **_kwargs):
        return effective_dag, "", {"applied": True}

    async def _collect(*_args, **_kwargs):
        collected.append(1)
        return ObservationReport(observations=[])

    monkeypatch.setattr(
        post_test_module,
        "_resolve_active_regroup_before_group_dispatch",
        _fake_resolve,
    )
    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(
        post_test_module,
        "_dag_group_checkpoint_is_fresh",
        _always_fresh_group_checkpoint,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)

    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert collected == [1]


@pytest.mark.asyncio
async def test_post_test_observation_republishes_after_fixed_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        **_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)
    observation = Observation(
        id="OBS-1",
        category="bug",
        severity="major",
        title="Button is stale",
        description="The post-test button state is stale.",
    )
    reports = [
        ObservationReport(observations=[observation], complete=True),
        ObservationReport(observations=[], complete=True),
    ]
    republished: list[dict[str, object]] = []

    async def _collect(*_args, **_kwargs):
        return reports.pop(0)

    async def _dispatch(_runner, _feature, obs, *_args, **_kwargs):
        return {
            "observation": obs,
            "status": "FIXED",
            "summary": "Patched stale button state.",
        }

    async def _republish(_runner, _feature, **kwargs):
        republished.append(kwargs)

    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(
        post_test_module,
        "_dag_group_checkpoint_is_fresh",
        _always_fresh_group_checkpoint,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)
    monkeypatch.setattr(post_test_module, "_dispatch_observation", _dispatch)
    monkeypatch.setattr(post_test_module, "_republish_post_test_fixes", _republish)

    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert len(republished) == 1
    assert republished[0]["cycle"] == 1
    assert [result["status"] for result in republished[0]["flat_results"]] == ["FIXED"]
    assert "Patched stale button state" in str(republished[0]["prior_fix_summary"])


@pytest.mark.asyncio
async def test_post_test_observation_resume_after_republish_quiesce_does_not_replay_fixed_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-0", name="Task 0", description="Task 0")],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    artifacts = _Artifacts({
        "dag": dag.model_dump_json(),
        "dag-group:0": '{"group_idx": 0, "results": []}',
        **_post_dag_gate_artifacts(),
    })
    runner = _runner(_FeatureStore(), artifacts)
    observation = Observation(
        id="OBS-1",
        category="bug",
        severity="major",
        title="Button is stale",
        description="The post-test button state is stale.",
    )
    reports = [
        ObservationReport(observations=[observation], complete=True),
        ObservationReport(observations=[], complete=True),
    ]
    dispatch_count = 0
    republish_retry_count = 0

    async def _collect(*_args, **_kwargs):
        return reports.pop(0)

    async def _dispatch(_runner, _feature, obs, *_args, **_kwargs):
        nonlocal dispatch_count
        dispatch_count += 1
        return {
            "observation": obs,
            "status": "FIXED",
            "summary": "Patched stale button state.",
        }

    async def _quiescing_republish(_runner, _feature, **_kwargs):
        raise WorkflowQuiesced(
            phase_name=PostTestObservationPhase.name,
            reason="post_test_blocked_source_push",
            metadata={
                "failure_class": "runtime_context",
                "failure_type": "post_test_source_push_failed",
                "deterministic_workflow_blocker": True,
            },
        )

    async def _noop_republish(*_args, **_kwargs):
        nonlocal republish_retry_count
        republish_retry_count += 1
        artifacts.store.update(_post_dag_gate_artifacts())
        return None

    monkeypatch.setattr(
        implementation_module,
        "_source_push_durable_proof_is_fresh",
        _always_fresh_source_push,
    )
    monkeypatch.setattr(
        post_test_module,
        "_dag_group_checkpoint_is_fresh",
        _always_fresh_group_checkpoint,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)
    monkeypatch.setattr(post_test_module, "_dispatch_observation", _dispatch)
    monkeypatch.setattr(post_test_module, "_republish_post_test_fixes", _quiescing_republish)

    with pytest.raises(WorkflowQuiesced):
        await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert dispatch_count == 1
    assert artifacts.store["observations-checkpoint:1"] == ""
    assert artifacts.store["observation-cycle-counter"] == "1"
    assert artifacts.store[post_test_module._POST_TEST_REPUBLISH_PENDING_KEY]
    artifacts.store.pop("dag-gate:source-push", None)
    artifacts.store.pop(implementation_module._post_dag_gate_proof_key("source-push"), None)

    monkeypatch.setattr(post_test_module, "_republish_post_test_fixes", _noop_republish)
    await PostTestObservationPhase().execute(runner, _feature(), BuildState())

    assert dispatch_count == 1
    assert republish_retry_count == 1
    assert artifacts.store[post_test_module._POST_TEST_REPUBLISH_PENDING_KEY] == ""


@pytest.mark.asyncio
async def test_post_test_observation_write_agents_fail_closed_without_sandbox_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature = _feature("feat-post-test-sandbox")
    captured: dict[str, dict[str, object]] = {}

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services: dict[str, object] = {}

        async def parallel(self, asks, _feature):
            for ask in asks:
                captured[ask.actor.name] = dict(ask.actor.role.metadata)
            return [
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
            ]

        async def run(self, ask, _feature, phase_name=""):
            del phase_name
            captured[ask.actor.name] = dict(ask.actor.role.metadata)
            if ask.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="OBS-1",
                    summary="Patched safely.",
                    files_modified=["README.md"],
                )
            if ask.output_type is Verdict:
                return Verdict(approved=True, summary="Observation fixed.")
            raise AssertionError(f"unexpected output type: {ask.output_type!r}")

    async def _requires_control_plane(*_args, **_kwargs) -> bool:
        return True

    async def _noop_commit(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        post_test_module,
        "_post_test_requires_control_plane_proofs",
        _requires_control_plane,
    )
    monkeypatch.setattr(post_test_module, "_commit_observation_repos", _noop_commit)

    observation = Observation(
        id="OBS-1",
        category="bug",
        severity="major",
        title="Button is stale",
        description="The post-test button state is stale.",
    )

    result = await post_test_module._dispatch_observation(
        _Runner(),
        feature,
        observation,
        observation_context="",
        phase_name="post-test-observation",
        workspace_root=tmp_path,
    )

    assert result["status"] == "FIXED"
    impl_metadata = captured["implementer-obs-impl-OBS-1"]
    assert impl_metadata["sandbox_required"] is True
    assert "workspace_override" not in impl_metadata


@pytest.mark.asyncio
async def test_post_test_observation_missing_sandbox_binding_routes_typed_workflow_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature = _feature("feat-post-test-sandbox-route")
    artifacts = _Artifacts()

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services: dict[str, object] = {}

        async def parallel(self, asks, _feature):
            del asks, _feature
            return [
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
            ]

        async def run(self, ask, _feature, phase_name=""):
            del ask, _feature, phase_name
            raise RuntimeError(
                "Runtime workspace binding required for sandbox-required write role "
                "implementer-obs-impl-OBS-typed"
            )

    async def _requires_control_plane(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(
        post_test_module,
        "_post_test_requires_control_plane_proofs",
        _requires_control_plane,
    )

    observation = Observation(
        id="OBS-typed",
        category="bug",
        severity="major",
        title="Button is stale",
        description="The post-test button state is stale.",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._dispatch_observation(
            _Runner(),
            feature,
            observation,
            observation_context="",
            phase_name="post-test-observation",
            workspace_root=tmp_path,
        )

    assert exc_info.value.reason == "post_test_blocked_runtime_workspace_binding_missing"
    assert exc_info.value.metadata["failure_class"] == "sandbox_binding"
    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    blocker = _post_test_workflow_blocker(
        artifacts,
        "runtime_workspace_binding_missing",
    )
    assert blocker["operator_required"] is False
    assert blocker["actor_role"] == "implementer"


@pytest.mark.asyncio
async def test_post_test_observation_test_author_missing_sandbox_binding_routes_typed_workflow_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature = _feature("feat-post-test-author-sandbox-route")
    artifacts = _Artifacts()

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services: dict[str, object] = {}

        async def parallel(self, asks, _feature):
            del asks, _feature
            return [
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
            ]

        async def run(self, ask, _feature, phase_name=""):
            del _feature, phase_name
            if ask.actor.name.startswith("implementer-obs-impl-"):
                return ImplementationResult(
                    task_id="OBS-test-author",
                    summary="Patched safely.",
                    files_modified=["README.md"],
                )
            raise RuntimeError(
                "Runtime workspace binding required for sandbox-required write role "
                "test-author-obs-test-OBS-test-author"
            )

    async def _requires_control_plane(*_args, **_kwargs) -> bool:
        return True

    async def _noop_commit(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        post_test_module,
        "_post_test_requires_control_plane_proofs",
        _requires_control_plane,
    )
    monkeypatch.setattr(post_test_module, "_commit_observation_repos", _noop_commit)

    observation = Observation(
        id="OBS-test-author",
        category="missing_test",
        severity="major",
        title="Coverage is missing",
        description="The post-test coverage is missing.",
    )

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._dispatch_observation(
            _Runner(),
            feature,
            observation,
            observation_context="",
            phase_name="post-test-observation",
            workspace_root=tmp_path,
        )

    assert exc_info.value.reason == "post_test_blocked_runtime_workspace_binding_missing"
    assert exc_info.value.metadata["failure_class"] == "sandbox_binding"
    assert exc_info.value.metadata["deterministic_workflow_blocker"] is True
    blocker = _post_test_workflow_blocker(
        artifacts,
        "runtime_workspace_binding_missing",
    )
    assert blocker["operator_required"] is False
    assert blocker["actor_role"] == "test_author"


@pytest.mark.asyncio
async def test_post_test_observation_execute_propagates_sandbox_binding_quiesce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature = _feature("feat-post-test-sandbox-execute")
    artifacts = _Artifacts()
    observation = Observation(
        id="OBS-execute",
        category="bug",
        severity="major",
        title="Button is stale",
        description="The post-test button state is stale.",
    )

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = artifacts
            self.services: dict[str, object] = {}

        async def parallel(self, asks, _feature):
            del asks, _feature
            return [
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
                RootCauseAnalysis(
                    hypothesis="Observation needs a fix",
                    evidence=["reported after tests"],
                    affected_files=["README.md"],
                    proposed_approach="Patch safely",
                    confidence="high",
                ),
            ]

        async def run(self, ask, _feature, phase_name=""):
            del ask, _feature, phase_name
            raise RuntimeError(
                "Runtime workspace binding required for sandbox-required write role "
                "implementer-obs-impl-OBS-execute"
            )

    async def _requires_control_plane(*_args, **_kwargs) -> bool:
        return True

    async def _skip_preflight(*_args, **_kwargs) -> None:
        return None

    async def _collect(*_args, **_kwargs) -> ObservationReport:
        return ObservationReport(observations=[observation])

    monkeypatch.setattr(
        post_test_module,
        "_post_test_requires_control_plane_proofs",
        _requires_control_plane,
    )
    monkeypatch.setattr(
        post_test_module,
        "_raise_if_dag_incomplete_before_post_test",
        _skip_preflight,
    )
    monkeypatch.setattr(
        post_test_module,
        "_get_feature_root",
        lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(PostTestObservationPhase, "_collect_observations", _collect)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await PostTestObservationPhase().execute(_Runner(), feature, BuildState())

    assert exc_info.value.reason == "post_test_blocked_runtime_workspace_binding_missing"
    assert "workflow-blocker:post-test:runtime_workspace_binding_missing" in artifacts.store
    assert "observation-results:cycle-1" not in artifacts.store


@pytest.mark.asyncio
async def test_republish_post_test_fixes_refreshes_source_report_and_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    feature = _feature()
    calls: list[str] = []

    async def _run(task, _feature, *, phase_name: str):
        del _feature
        calls.append(f"notify:{phase_name}:{task.delivery_id}")

    runner = SimpleNamespace(artifacts=artifacts, services={}, run=_run)
    observation = Observation(
        id="OBS-2",
        category="bug",
        severity="major",
        title="Refresh needed",
        description="A post-test refresh is needed.",
    )

    async def _not_fresh(*_args, **_kwargs):
        return False

    async def _push(_runner, _feature, *, tree_digest: str):
        calls.append(f"push:{tree_digest}")

    async def _report(_runner, _feature, **kwargs):
        calls.append(f"report:{kwargs['tree_digest']}")
        await _runner.artifacts.put(
            "dag-gate:implementation-report",
            "approved",
            feature=_feature,
        )
        return "https://example.test/report", "", SimpleNamespace(items=[])

    async def _proof(_runner, _feature, gate_name: str, tree_digest: str, **kwargs):
        del kwargs
        calls.append(f"proof:{gate_name}:{tree_digest}")

    monkeypatch.setattr(post_test_module, "_post_dag_gate_tree_digest", lambda *_args: "tree-1")
    monkeypatch.setattr(post_test_module, "_post_dag_gate_is_fresh", _not_fresh)
    monkeypatch.setattr(post_test_module, "_push_clones_to_source", _push)
    monkeypatch.setattr(post_test_module, "_generate_and_publish_implementation_report", _report)
    monkeypatch.setattr(post_test_module, "_record_post_dag_gate_proof", _proof)

    await post_test_module._republish_post_test_fixes(
        runner,
        feature,
        cycle=2,
        flat_results=[
            {
                "observation": observation,
                "status": "FIXED",
                "summary": "Refreshed stale post-test state.",
            }
        ],
        prior_fix_summary="OBS-2 fixed",
    )

    assert "push:tree-1" in calls
    assert "report:tree-1" in calls
    assert any(call.startswith("notify:post-test-observation:") for call in calls)
    assert "dag-gate:source-push" in artifacts.store
    assert "dag-gate:implementation-report" in artifacts.store
    assert "dag-gate:notify" in artifacts.store
    delivery = implementation_module.json.loads(artifacts.store["dag-notify-delivery"])
    assert delivery["status"] == "sent"


@pytest.mark.asyncio
async def test_republish_post_test_fixes_quiesces_when_source_push_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    feature = _feature()
    runner = SimpleNamespace(artifacts=artifacts, services={}, run=None)
    observation = Observation(
        id="OBS-source-fail",
        category="bug",
        severity="major",
        title="Source push fails",
        description="A source push failure should quiesce deterministically.",
    )

    async def _not_fresh(*_args, **_kwargs):
        return False

    async def _push(*_args, **_kwargs):
        raise RuntimeError("remote rejected push")

    async def _report(*_args, **_kwargs):
        raise AssertionError("report must not run after source-push failure")

    monkeypatch.setattr(post_test_module, "_post_dag_gate_tree_digest", lambda *_args: "tree-source-fail")
    monkeypatch.setattr(post_test_module, "_post_dag_gate_is_fresh", _not_fresh)
    monkeypatch.setattr(post_test_module, "_push_clones_to_source", _push)
    monkeypatch.setattr(post_test_module, "_generate_and_publish_implementation_report", _report)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._republish_post_test_fixes(
            runner,
            feature,
            cycle=3,
            flat_results=[{"observation": observation, "status": "FIXED"}],
            prior_fix_summary="OBS-source-fail fixed",
        )

    assert exc_info.value.metadata["failure_type"] == "post_test_source_push_failed"
    assert "dag-gate:source-push" not in artifacts.store
    failure = json.loads(artifacts.store["dag-runtime-failure:source-push"])
    assert failure["failure_type"] == "post_test_source_push_failed"


@pytest.mark.asyncio
async def test_republish_post_test_fixes_quiesces_on_stale_post_push_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    feature = _feature()
    runner = SimpleNamespace(artifacts=artifacts, services={}, run=None)
    observation = Observation(
        id="OBS-stale-push",
        category="bug",
        severity="major",
        title="Stale source push",
        description="A stale source push digest should quiesce deterministically.",
    )
    digests = iter(["tree-before", "tree-after"])

    async def _not_fresh(*_args, **_kwargs):
        return False

    async def _push(*_args, **_kwargs):
        return None

    monkeypatch.setattr(post_test_module, "_post_dag_gate_tree_digest", lambda *_args: next(digests))
    monkeypatch.setattr(post_test_module, "_post_dag_gate_is_fresh", _not_fresh)
    monkeypatch.setattr(post_test_module, "_push_clones_to_source", _push)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._republish_post_test_fixes(
            runner,
            feature,
            cycle=3,
            flat_results=[{"observation": observation, "status": "FIXED"}],
            prior_fix_summary="OBS-stale-push fixed",
        )

    assert exc_info.value.metadata["failure_type"] == "post_test_source_push_stale_gate_digest"
    assert "dag-gate:source-push" not in artifacts.store
    failure = json.loads(artifacts.store["dag-runtime-failure:source-push"])
    assert failure["tree_digest_before"] == "tree-before"
    assert failure["tree_digest_after"] == "tree-after"


@pytest.mark.asyncio
async def test_republish_post_test_fixes_quiesces_when_report_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    feature = _feature()
    runner = SimpleNamespace(artifacts=artifacts, services={}, run=None)
    observation = Observation(
        id="OBS-report-fail",
        category="bug",
        severity="major",
        title="Report publish fails",
        description="A report failure should quiesce deterministically.",
    )

    async def _fresh(*_args, **_kwargs):
        return True

    async def _report(*_args, **_kwargs):
        raise RuntimeError("report store unavailable")

    monkeypatch.setattr(post_test_module, "_post_dag_gate_tree_digest", lambda *_args: "tree-report")
    monkeypatch.setattr(post_test_module, "_post_dag_gate_is_fresh", _fresh)
    monkeypatch.setattr(post_test_module, "_generate_and_publish_implementation_report", _report)

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._republish_post_test_fixes(
            runner,
            feature,
            cycle=3,
            flat_results=[{"observation": observation, "status": "FIXED"}],
            prior_fix_summary="OBS-report-fail fixed",
        )

    assert exc_info.value.metadata["failure_type"] == "post_test_implementation_report_failed"
    failure = json.loads(artifacts.store["dag-runtime-failure:implementation-report"])
    assert failure["failure_type"] == "post_test_implementation_report_failed"
    assert "dag-gate:notify" not in artifacts.store


@pytest.mark.asyncio
async def test_republish_post_test_fixes_quiesces_on_pending_notify_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts({
        "dag-notify-delivery": json.dumps(
            {
                "delivery_id": "delivery-pending",
                "tree_digest": "tree-notify",
                "status": "pending",
            }
        )
    })
    feature = _feature()
    runner = SimpleNamespace(artifacts=artifacts, services={}, run=None)
    observation = Observation(
        id="OBS-pending-notify",
        category="bug",
        severity="major",
        title="Pending notify",
        description="A pending notify after restart should quiesce.",
    )

    async def _fresh(*_args, **_kwargs):
        return True

    async def _report(*_args, **_kwargs):
        return "https://example.test/report", "", SimpleNamespace(items=[])

    monkeypatch.setattr(post_test_module, "_post_dag_gate_tree_digest", lambda *_args: "tree-notify")
    monkeypatch.setattr(post_test_module, "_post_dag_gate_is_fresh", _fresh)
    monkeypatch.setattr(post_test_module, "_generate_and_publish_implementation_report", _report)
    monkeypatch.setattr(post_test_module, "_notify_delivery_id", lambda *_args: "delivery-pending")

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._republish_post_test_fixes(
            runner,
            feature,
            cycle=3,
            flat_results=[{"observation": observation, "status": "FIXED"}],
            prior_fix_summary="OBS-pending-notify fixed",
        )

    assert exc_info.value.metadata["failure_type"] == "post_test_notify_delivery_ambiguous"
    assert "dag-gate:notify" not in artifacts.store
    failure = json.loads(artifacts.store["dag-runtime-failure:notify"])
    assert failure["delivery_id"] == "delivery-pending"


@pytest.mark.asyncio
async def test_republish_post_test_fixes_quiesces_when_notify_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    feature = _feature()

    async def _run(task, _feature, *, phase_name: str):
        del task, _feature, phase_name
        raise RuntimeError("slack unavailable")

    runner = SimpleNamespace(artifacts=artifacts, services={}, run=_run)
    observation = Observation(
        id="OBS-notify-fail",
        category="bug",
        severity="major",
        title="Notify fails",
        description="A notify send failure should leave pending delivery evidence.",
    )

    async def _fresh(*_args, **_kwargs):
        return True

    async def _report(*_args, **_kwargs):
        return "https://example.test/report", "", SimpleNamespace(items=[])

    monkeypatch.setattr(post_test_module, "_post_dag_gate_tree_digest", lambda *_args: "tree-notify-fail")
    monkeypatch.setattr(post_test_module, "_post_dag_gate_is_fresh", _fresh)
    monkeypatch.setattr(post_test_module, "_generate_and_publish_implementation_report", _report)
    monkeypatch.setattr(post_test_module, "_notify_delivery_id", lambda *_args: "delivery-failed")

    with pytest.raises(WorkflowQuiesced) as exc_info:
        await post_test_module._republish_post_test_fixes(
            runner,
            feature,
            cycle=3,
            flat_results=[{"observation": observation, "status": "FIXED"}],
            prior_fix_summary="OBS-notify-fail fixed",
        )

    assert exc_info.value.metadata["failure_type"] == "post_test_notify_delivery_failed"
    assert "dag-gate:notify" not in artifacts.store
    delivery = json.loads(artifacts.store["dag-notify-delivery"])
    assert delivery["delivery_id"] == "delivery-failed"
    assert delivery["status"] == "pending"
    failure = json.loads(artifacts.store["dag-runtime-failure:notify"])
    assert failure["failure_type"] == "post_test_notify_delivery_failed"
