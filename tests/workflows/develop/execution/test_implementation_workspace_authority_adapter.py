from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, TaskFileScope
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module
from iriai_build_v2.workflows.develop.phases.implementation import (
    _implement_dag,
    _run_workspace_authority_pre_dispatch_adapter,
)
from iriai_build_v2.workflows.develop.execution.task_contracts import (
    ContractCompileRequest,
    ContractCompiler,
)
from iriai_build_v2.workflows.develop.execution.workspace_authority import (
    CanonicalRepoRegistry,
    RepoIdentity,
)


class _Artifacts:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ids: dict[str, int] = {}
        self._next_id = 0

    async def put(self, key: str, value: str, *, feature) -> None:
        del feature
        self._next_id += 1
        self.store[key] = value
        self.ids[key] = self._next_id
        self.last_artifact_id = self._next_id

    async def get(self, key: str, *, feature) -> str | None:
        del feature
        return self.store.get(key)

    async def get_record(self, key: str, *, feature) -> dict[str, object] | None:
        del feature
        if key not in self.store:
            return None
        return {"id": self.ids.get(key, 0), "value": self.store[key]}


class _BridgeExecutionControlStore:
    def __init__(self, *, duplicate_nonterminal: bool = False) -> None:
        self.duplicate_nonterminal = duplicate_nonterminal
        self.contracts = []
        self.workspace_snapshots = []
        self.start_requests = []
        self.prompt_contexts = []
        self.runtime_invocations = []
        self.raw_outputs = []
        self.structured_outputs = []
        self.runtime_failures = []
        self.patch_summaries = []
        self.contract_verdicts = []
        self.finished = []
        self.projected_attempts = []
        self.revalidation_inputs = None
        self.revalidation_requests = []
        self.pending_merge_patch_evidence = None
        self.pending_merge_patch_requests = []
        self.runtime_failure_contexts = {}
        self.runtime_failure_context_requests = []
        self._next_contract_id = 100
        self._next_attempt_id = 0
        self._next_evidence_id = 1000
        self._next_failure_id = 2000

    def _evidence(self) -> SimpleNamespace:
        self._next_evidence_id += 1
        return SimpleNamespace(id=self._next_evidence_id)

    async def put_task_contract(self, contract):
        self._next_contract_id += 1
        stored = implementation_module.dataclasses.replace(
            contract,
            id=self._next_contract_id,
        )
        self.contracts.append(stored)
        return SimpleNamespace(contract=stored)

    async def record_workspace_snapshot(self, evidence):
        self.workspace_snapshots.append(evidence)
        return SimpleNamespace(snapshot=SimpleNamespace(id=self._evidence().id))

    async def start_dispatch_attempt(self, request):
        self.start_requests.append(request)
        self._next_attempt_id += 1
        row = SimpleNamespace(
            status="started",
            dispatcher_state="attempt_started",
            payload={},
            request_digest=request.request_digest,
        )
        return SimpleNamespace(
            attempt_id=self._next_attempt_id,
            created=not self.duplicate_nonterminal,
            attempt=row,
        )

    async def record_prompt_context(self, evidence):
        self.prompt_contexts.append(evidence)
        return SimpleNamespace(evidence=self._evidence())

    async def record_runtime_invocation(self, evidence):
        self.runtime_invocations.append(evidence)
        return SimpleNamespace(evidence=self._evidence())

    async def record_raw_output(self, evidence):
        self.raw_outputs.append(evidence)
        return SimpleNamespace(evidence=self._evidence())

    async def record_structured_output(self, evidence):
        self.structured_outputs.append(evidence)
        return SimpleNamespace(evidence=self._evidence())

    async def record_runtime_failure(self, evidence):
        self._next_failure_id += 1
        self.runtime_failures.append(evidence)
        return SimpleNamespace(failure_id=self._next_failure_id)

    async def record_patch_summary(self, summary):
        self.patch_summaries.append(summary)
        return SimpleNamespace(evidence=self._evidence())

    async def record_contract_verdict(self, verdict):
        self.contract_verdicts.append(verdict)
        return SimpleNamespace(evidence=self._evidence())

    async def project_task_result_from_attempt(self, projection):
        self.projected_attempts.append(projection)
        return SimpleNamespace(projection_links=[])

    async def get_pre_promotion_contract_revalidation_inputs(self, **kwargs):
        self.revalidation_requests.append(kwargs)
        return self.revalidation_inputs

    async def get_pending_durable_merge_patch_evidence(self, **kwargs):
        self.pending_merge_patch_requests.append(kwargs)
        return self.pending_merge_patch_evidence

    async def get_runtime_failure_context(self, **kwargs):
        self.runtime_failure_context_requests.append(kwargs)
        failure_id = int(kwargs.get("failure_id") or 0)
        return self.runtime_failure_contexts.get(failure_id)

    async def finish_dispatch_attempt(self, outcome):
        self.finished.append(outcome)
        return SimpleNamespace(
            compatibility_artifact_ids=list(outcome.compatibility_artifact_ids),
            raw_text_ref=outcome.raw_text_ref,
            runtime_failure_id=outcome.runtime_failure_id,
            typed_failure_id=outcome.typed_failure_id,
        )


def _feature() -> SimpleNamespace:
    return SimpleNamespace(id="feature-slice-02", slug="slice-02")


def _runner(
    workspace_root: Path,
    artifacts: _Artifacts,
    *,
    allow_sandbox_patch_promotion_bridge: bool = True,
    execution_control_store: object | None = None,
) -> SimpleNamespace:
    services = {"workspace_manager": SimpleNamespace(_base=workspace_root)}
    if allow_sandbox_patch_promotion_bridge:
        services["test_allow_sandbox_patch_promotion_bridge"] = True
    if execution_control_store is not None:
        services["execution_control_store"] = execution_control_store
    return SimpleNamespace(
        artifacts=artifacts,
        services=services,
    )


def _artifact_key_with_prefix(artifacts: _Artifacts, prefix: str) -> str:
    matches = sorted(key for key in artifacts.store if key.startswith(prefix))
    assert matches, f"missing artifact with prefix {prefix!r}"
    assert len(matches) == 1
    return matches[0]


def _assert_pending_merge_queue_blocker(outcome) -> None:
    assert outcome.terminal_state == "workflow_blocked"
    assert "SANDBOX_WORKFLOW_BLOCKER" in outcome.failure
    assert "durable merge queue" in outcome.failure
    # Slice 08e-2: the implementation worker path now enqueues onto the durable
    # merge queue instead of running the legacy canonical commit. Two blocker
    # families gate canonical mutation: the resume/marker path still reports
    # "canonical mutation requires the durable merge queue", and the
    # implementation-worker enqueue path (which fails closed when no Postgres
    # pool is configured in these unit fakes) reports that the "legacy
    # canonical commit is disabled". Either proves canonical mutation is gated.
    assert (
        "canonical mutation" in outcome.failure
        or "legacy canonical commit is disabled" in outcome.failure
    )


def _repo(feature_root: Path, name: str, *, agent_writable: bool = True) -> Path:
    repo = feature_root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "adapter@example.test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Adapter Test"],
        cwd=repo,
        check=True,
    )
    (repo / "src").mkdir()
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    if agent_writable:
        repo.chmod(0o777)
        (repo / ".git").chmod(0o777)
        (repo / "src").chmod(0o777)
    return repo


def _write_sandbox_file(ask, path: str, content: str) -> None:
    sandbox_cwd = Path(ask.actor.role.metadata["workspace_override"])
    target = sandbox_cwd / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    feature_root = workspace_root / ".iriai" / "features" / "slice-02" / "repos"
    feature_root.mkdir(parents=True)
    return workspace_root, feature_root


@pytest.mark.parametrize(
    "reason",
    [
        "provider_error",
        "process_failed",
        "timeout",
        "watchdog_stall",
        "context_materialization_failed",
        "structured_output_invalid",
    ],
)
def test_retryable_dispatch_terminal_reasons_advance_attempt(reason: str) -> None:
    outcome = SimpleNamespace(status="failed", runtime_terminal_reason=reason)

    assert implementation_module._should_retry_implementation_dispatch_outcome(
        outcome,
        attempt=0,
        max_retries=5,
    )


@pytest.mark.parametrize(
    "reason",
    [
        "sandbox_binding_failed",
        "prompt_too_large",
        "cancelled",
        "patch_capture_failed",
        "completed",
    ],
)
def test_non_retryable_dispatch_terminal_reasons_block(reason: str) -> None:
    outcome = SimpleNamespace(status="failed", runtime_terminal_reason=reason)

    assert not implementation_module._should_retry_implementation_dispatch_outcome(
        outcome,
        attempt=0,
        max_retries=5,
    )


def test_retryable_dispatch_terminal_reason_stops_at_budget() -> None:
    outcome = SimpleNamespace(
        status="failed",
        runtime_terminal_reason="provider_error",
    )

    assert not implementation_module._should_retry_implementation_dispatch_outcome(
        outcome,
        attempt=5,
        max_retries=5,
    )


def test_contract_workspace_snapshot_includes_tracked_required_paths(tmp_path: Path) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    del workspace_root
    repo = _repo(feature_root, "app")
    (repo / "tests" / "fixtures" / "catalog").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "catalog" / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add fixture"], cwd=repo, check=True)
    contract = SimpleNamespace(
        required_paths=[
            SimpleNamespace(path="tests/fixtures/catalog/.gitkeep"),
        ],
        allowed_paths=[SimpleNamespace(path="tests/conftest.py")],
        read_only_paths=[],
        generated_outputs=[],
    )

    snapshot = implementation_module._contract_workspace_snapshot(
        _feature(),
        "dag-sha",
        0,
        "implementation",
        "app",
        "app",
        repo,
        ["tests/conftest.py"],
        contract=contract,
        snapshots=[],
    )

    assert "tests/fixtures/catalog/.gitkeep" in snapshot.present_paths
    assert "tests/conftest.py" in snapshot.dirty_paths


@pytest.mark.asyncio
async def test_pre_promotion_contract_failure_revalidates_and_synthesizes_result(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "tests" / "fixtures" / "catalog").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "catalog" / ".gitkeep").write_text("", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text("# base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add test files"], cwd=repo, check=True)
    task = ImplementationTask(
        id="TASK-revalidate",
        name="revalidate",
        description="revalidate retained patch",
        repo_path="app",
        file_scope=[
            TaskFileScope(path="app/tests/conftest.py", action="modify"),
            TaskFileScope(path="app/tests/fixtures/catalog/.gitkeep", action="create"),
        ],
    )
    contract = _compiled_contract_for_task(feature_root, repo, task)
    store = _BridgeExecutionControlStore()
    store.revalidation_inputs = {
        "patch_summary": SimpleNamespace(
            id=701,
            payload={
                "sandbox_id": "sandbox-23",
                "contract_ids": [104],
                "repo_id": "app",
                "base_commit": "base",
                "changed_paths": ["tests/conftest.py"],
                "created_paths": [],
                "modified_paths": ["tests/conftest.py"],
                "deleted_paths": [],
                "renamed_paths": {},
                "diff_sha256": "diff",
                "diff_artifact_id": 9001,
            },
        ),
        "runtime_failure": SimpleNamespace(
            id=35,
            payload={},
            summary="Task contract validation failed before sandbox promotion",
        ),
        "contract_verdict": SimpleNamespace(id=34, payload={}, metadata={}),
    }
    artifacts = _Artifacts()
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = store

    result = await implementation_module._recover_pre_promotion_contract_revalidation(
        runner=runner,
        feature=_feature(),
        task=task,
        task_contract=contract,
        feature_root=feature_root,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        snapshots=[],
        repo_prefix="app",
        outcome=SimpleNamespace(attempt_id=23, runtime_terminal_reason="patch_capture_failed"),
    )

    assert result is not None
    assert result.status == "completed"
    assert result.files_modified == ["app/tests/conftest.py"]
    assert "patch_summary_ids=701" in result.notes
    assert json.loads(artifacts.store["dag-task:TASK-revalidate"])["status"] == "completed"
    assert store.contract_verdicts[-1].approved is True
    assert store.contract_verdicts[-1].metadata["revalidated_from_runtime_failure_id"] == 35
    assert store.contract_verdicts[-1].metadata["revalidated_from_contract_verdict_id"] == 34


@pytest.mark.asyncio
async def test_pre_promotion_contract_failure_revalidation_blocks_when_still_invalid(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "conftest.py").write_text("# base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add conftest"], cwd=repo, check=True)
    task = ImplementationTask(
        id="TASK-invalid-revalidate",
        name="invalid",
        description="invalid retained patch",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/tests/conftest.py", action="modify")],
    )
    contract = _compiled_contract_for_task(feature_root, repo, task)
    store = _BridgeExecutionControlStore()
    store.revalidation_inputs = {
        "patch_summary": SimpleNamespace(
            id=702,
            payload={
                "sandbox_id": "sandbox-24",
                "contract_ids": [104],
                "repo_id": "app",
                "changed_paths": ["src/outside.py"],
                "created_paths": [],
                "modified_paths": ["src/outside.py"],
                "deleted_paths": [],
                "renamed_paths": {},
                "diff_sha256": "diff",
            },
        ),
        "runtime_failure": SimpleNamespace(
            id=36,
            payload={},
            summary="Task contract validation failed before sandbox promotion",
        ),
        "contract_verdict": SimpleNamespace(id=37, payload={}, metadata={}),
    }
    artifacts = _Artifacts()
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = store

    result = await implementation_module._recover_pre_promotion_contract_revalidation(
        runner=runner,
        feature=_feature(),
        task=task,
        task_contract=contract,
        feature_root=feature_root,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        snapshots=[],
        repo_prefix="app",
        outcome=SimpleNamespace(attempt_id=24, runtime_terminal_reason="patch_capture_failed"),
    )

    assert result is None
    assert "dag-task:TASK-invalid-revalidate" not in artifacts.store
    assert store.contract_verdicts == []


def _authority_repo(
    feature_root: Path,
    name: str,
    repo_id: str,
    *,
    writable_task_ids: list[str] | None = None,
    read_only_task_ids: list[str] | None = None,
    task_ids: list[str] | None = None,
) -> RepoIdentity:
    repo = feature_root / name
    repo.mkdir(parents=True, exist_ok=True)
    return RepoIdentity(
        repo_id=repo_id,
        repo_name=name,
        role="execution",
        workspace_relative_path=name,
        canonical_path=str(repo),
        identity_kind="source_path",
        identity_value=str(repo),
        writable_task_ids=writable_task_ids or [],
        read_only_task_ids=read_only_task_ids or [],
        task_ids=task_ids or [],
        safety_status="ok",
        identity_evidence_digest=f"identity:{repo_id}",
    )


def _authority_registry(
    feature_root: Path,
    repos: list[RepoIdentity],
) -> CanonicalRepoRegistry:
    return CanonicalRepoRegistry(
        feature_id="feature-slice-02",
        feature_slug="slice-02",
        feature_root=str(feature_root),
        repos=repos,
        registry_digest="registry:digest",
    )


def _compiled_contract_for_task(
    feature_root: Path,
    repo: Path,
    task: ImplementationTask,
    *,
    contract_id: int = 104,
):
    registry = CanonicalRepoRegistry(
        feature_id="feature-slice-02",
        feature_slug="slice-02",
        feature_root=str(feature_root),
        repos=[
            RepoIdentity(
                repo_id=repo.name,
                repo_name=repo.name,
                role="execution",
                workspace_relative_path=repo.name,
                canonical_path=str(repo),
                identity_kind="source_path",
                identity_value=str(repo),
                safety_status="ok",
                identity_evidence_digest=f"identity:{repo.name}",
            )
        ],
        registry_digest="registry:digest",
    )
    contract = ContractCompiler().compile_task(
        ContractCompileRequest(
            feature_id="feature-slice-02",
            dag_sha256="dag-sha",
            source_dag_artifact_id=42,
            source_dag_sha256="source-dag-sha",
            group_idx=0,
            task=task,
            all_task_ids=[task.id],
            workspace_registry=registry,
        )
    )
    return contract.model_copy(update={"id": contract_id})


async def _compile_single_contract_with_registry(
    workspace_root: Path,
    feature_root: Path,
    artifacts: _Artifacts,
    registry: CanonicalRepoRegistry,
    task: ImplementationTask,
) -> implementation_module.TaskContractCompileOutcome:
    dag = implementation_module.ImplementationDAG(
        tasks=[task],
        execution_order=[[task.id]],
        complete=True,
    )
    return await implementation_module._compile_task_contracts_for_group(
        _runner(workspace_root, artifacts),
        _feature(),
        dag,
        0,
        [task],
        registry=registry,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )


def _patch_git_evidence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    created: list[str] | None = None,
    modified: list[str] | None = None,
    deleted: list[str] | None = None,
    renamed: dict[str, str] | None = None,
) -> None:
    created_paths = list(created or [])
    modified_paths = list(modified or [])
    deleted_paths = list(deleted or [])
    renamed_paths = dict(renamed or {})
    status_text = "".join(
        [*(f"?? {path}\n" for path in created_paths)]
        + [*(f" M {path}\n" for path in modified_paths)]
        + [*(f" D {path}\n" for path in deleted_paths)]
        + [*(f"R  {old} -> {new}\n" for old, new in renamed_paths.items())]
    )
    digest = implementation_module.hashlib.sha256(
        status_text.encode("utf-8")
    ).hexdigest()

    async def _git_evidence(_repo: Path):
        return (
            True,
            digest,
            created_paths,
            modified_paths,
            deleted_paths,
            renamed_paths,
            status_text,
            "",
        )

    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _git_evidence)


def test_dispatch_repo_binding_uses_contract_when_task_repo_path_empty(tmp_path: Path) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(
                feature_root,
                "app",
                "repo-app",
                writable_task_ids=["TASK-contract"],
            )
        ],
    )
    task = ImplementationTask(
        id="TASK-contract",
        name="contract",
        description="contract",
        repo_path="",
        file_scope=[TaskFileScope(path="app/src/example.py", action="modify")],
    )
    contract = SimpleNamespace(repo_id="repo-app", repo_path="app")

    binding = implementation_module._resolve_task_dispatch_repo_binding(
        task=task,
        task_contract=contract,
        registry=registry,
        feature_root=feature_root,
    )

    assert workspace_root  # keeps the workspace fixture shape explicit
    assert binding.repo_id == "repo-app"
    assert binding.repo_path == "app"
    assert binding.ws_path == str(repo)
    assert binding.source == "contract"


def test_dispatch_repo_binding_uses_unique_registry_owner(tmp_path: Path) -> None:
    _workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(
                feature_root,
                "app",
                "repo-app",
                writable_task_ids=["TASK-registry"],
            )
        ],
    )
    task = ImplementationTask(
        id="TASK-registry",
        name="registry",
        description="registry",
        repo_path="",
        file_scope=[TaskFileScope(path="app/src/example.py", action="modify")],
    )

    binding = implementation_module._resolve_task_dispatch_repo_binding(
        task=task,
        task_contract=None,
        registry=registry,
        feature_root=feature_root,
    )

    assert binding.repo_id == "repo-app"
    assert binding.repo_path == "app"
    assert binding.ws_path == str(repo)
    assert binding.source == "registry"


def test_dispatch_repo_binding_fails_on_contract_registry_mismatch(tmp_path: Path) -> None:
    _workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(
                feature_root,
                "app",
                "repo-app",
                writable_task_ids=["TASK-mismatch"],
            )
        ],
    )
    task = ImplementationTask(
        id="TASK-mismatch",
        name="mismatch",
        description="mismatch",
        repo_path="",
        file_scope=[TaskFileScope(path="app/src/example.py", action="modify")],
    )
    contract = SimpleNamespace(repo_id="repo-app", repo_path="other")

    with pytest.raises(implementation_module.SandboxWorkflowBlocker, match="repo_path mismatch"):
        implementation_module._resolve_task_dispatch_repo_binding(
            task=task,
            task_contract=contract,
            registry=registry,
            feature_root=feature_root,
        )


def test_dispatch_repo_binding_fails_on_missing_git_worktree(tmp_path: Path) -> None:
    _workspace_root, feature_root = _workspace(tmp_path)
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(
                feature_root,
                "app",
                "repo-app",
                writable_task_ids=["TASK-nongit"],
            )
        ],
    )
    task = ImplementationTask(
        id="TASK-nongit",
        name="nongit",
        description="nongit",
        repo_path="",
        file_scope=[TaskFileScope(path="app/src/example.py", action="modify")],
    )
    contract = SimpleNamespace(repo_id="repo-app", repo_path="app")

    with pytest.raises(implementation_module.SandboxWorkflowBlocker, match="not a Git worktree"):
        implementation_module._resolve_task_dispatch_repo_binding(
            task=task,
            task_contract=contract,
            registry=registry,
            feature_root=feature_root,
        )


@pytest.mark.asyncio
async def test_implementation_prompt_context_materializes_positive_prompt_ref(
    tmp_path: Path,
) -> None:
    workspace_root, _feature_root = _workspace(tmp_path)
    artifacts = _Artifacts()
    feature = _feature()
    runner = _runner(workspace_root, artifacts)
    task = ImplementationTask(
        id="TASK-prompt",
        name="prompt",
        description="prompt",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/example.py", action="modify")],
    )
    builder = implementation_module._ImplementationPromptBuilder(
        runner=runner,
        feature=feature,
        task=task,
        repo_prefix="app/",
        task_contract=None,
        handover_context="",
        inline_prompt="Do it.",
        log_label="Prompt",
    )

    result = await builder.build_prompt_context(
        SimpleNamespace(group_idx=7, request_digest="d" * 64),
    )

    assert result.bundle.prompt_ref > 0
    assert _artifact_key_with_prefix(artifacts, "dag-dispatch-prompt:g7:TASK-prompt:")


@pytest.mark.asyncio
async def test_context_layer_uses_bounded_review_source_id_without_typed_store(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifacts = _Artifacts()
    feature = _feature()
    runner = _runner(workspace_root, artifacts)
    contract = SimpleNamespace(
        source_dag_artifact_id=0,
        source_dag_sha256="source-sha",
        dag_sha256="dag-sha",
        task_id="TASK-context",
    )

    first = await implementation_module._source_dag_artifact_id_for_context_layer(
        runner,
        feature,
        contract,
    )
    second = await implementation_module._source_dag_artifact_id_for_context_layer(
        runner,
        feature,
        contract,
    )

    assert first > 0
    assert second == first
    keys = [
        key
        for key in artifacts.store
        if key.startswith("review:context-source-dag:")
    ]
    assert len(keys) == 1
    payload = json.loads(artifacts.store[keys[0]])
    assert payload["artifact_schema"] == "context-source-dag-compatibility-v1"
    assert payload["source_dag_artifact_id"] == first
    assert payload["source"] == "artifact_compatibility"


@pytest.mark.asyncio
async def test_context_layer_boolean_source_id_uses_review_source_artifact(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifacts = _Artifacts()
    feature = _feature()
    runner = _runner(workspace_root, artifacts)
    contract = SimpleNamespace(
        source_dag_artifact_id=True,
        source_dag_sha256="source-sha",
        dag_sha256="dag-sha",
        task_id="TASK-context",
    )

    source_id = await implementation_module._source_dag_artifact_id_for_context_layer(
        runner,
        feature,
        contract,
    )

    assert source_id > 1
    keys = [
        key
        for key in artifacts.store
        if key.startswith("review:context-source-dag:")
    ]
    assert len(keys) == 1


@pytest.mark.asyncio
async def test_context_layer_uses_review_source_id_when_typed_store_lacks_source_row(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifacts = _Artifacts()
    feature = _feature()
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = _BridgeExecutionControlStore()
    contract = SimpleNamespace(
        source_dag_artifact_id=0,
        source_dag_sha256="source-sha",
        dag_sha256="dag-sha",
        task_id="TASK-context",
    )

    source_id = await implementation_module._source_dag_artifact_id_for_context_layer(
        runner,
        feature,
        contract,
    )

    assert source_id > 0
    keys = [
        key
        for key in artifacts.store
        if key.startswith("review:context-source-dag:")
    ]
    assert len(keys) == 1
    payload = json.loads(artifacts.store[keys[0]])
    assert payload["source_dag_artifact_id"] == source_id


def _legacy_completed_group_checkpoint(
    *,
    group_idx: int,
    task_id: str,
    status: str = "completed",
) -> str:
    result = implementation_module.ImplementationResult(
        task_id=task_id,
        summary=f"legacy completed {task_id}",
        status=status,
    )
    return json.dumps(
        {
            "group_idx": group_idx,
            "task_ids": [task_id],
            "results": [result.model_dump()],
            "verdict": "approved",
            "commit_hash": "",
        },
        sort_keys=True,
    )


async def _noop_worktrees(*_args, **_kwargs) -> None:
    return None


async def _alias_guard_ok(*_args, **_kwargs):
    return True, {"blockers": []}


@pytest.mark.asyncio
async def test_legacy_completed_checkpoint_skips_before_contract_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    artifacts.store["dag-group:0"] = _legacy_completed_group_checkpoint(
        group_idx=0,
        task_id="TASK-old",
    )
    feature = _feature()
    compiled_groups: list[int] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-old", name="old", description="old"),
            ImplementationTask(id="TASK-next", name="next", description="next"),
        ],
        execution_order=[["TASK-old"], ["TASK-next"]],
        complete=True,
    )

    async def _compile_contracts(*args, **_kwargs):
        compiled_groups.append(args[3])
        return implementation_module.TaskContractCompileOutcome(
            approved=False,
            failure=f"stop at g{args[3]}",
            failure_class="contract_compile",
            failure_type="contract_invalid_path",
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _compile_contracts,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    outcome = await _implement_dag(runner, feature, dag)

    assert compiled_groups == [1]
    assert "stop at g1" in outcome.failure
    assert "legacy completed TASK-old" in outcome.implementation_text


@pytest.mark.asyncio
async def test_legacy_approved_checkpoint_skips_partial_result_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    artifacts.store["dag-group:0"] = _legacy_completed_group_checkpoint(
        group_idx=0,
        task_id="TASK-partial-old",
        status="partial",
    )
    compiled_groups: list[int] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-partial-old",
                name="partial old",
                description="partial old",
            ),
            ImplementationTask(id="TASK-next", name="next", description="next"),
        ],
        execution_order=[["TASK-partial-old"], ["TASK-next"]],
        complete=True,
    )

    async def _compile_contracts(*args, **_kwargs):
        compiled_groups.append(args[3])
        return implementation_module.TaskContractCompileOutcome(
            approved=False,
            failure=f"stop at g{args[3]}",
            failure_class="contract_compile",
            failure_type="contract_invalid_path",
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _compile_contracts,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    outcome = await _implement_dag(runner, _feature(), dag)

    assert compiled_groups == [1]
    assert "stop at g1" in outcome.failure
    assert "legacy completed TASK-partial-old" in outcome.implementation_text


@pytest.mark.asyncio
async def test_legacy_completed_checkpoint_reruns_after_execution_control_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    feature = _feature()
    artifacts.store["dag-group:0"] = _legacy_completed_group_checkpoint(
        group_idx=0,
        task_id="TASK-old",
    )
    artifacts.store[f"execution-control-adoption:{feature.id}"] = json.dumps(
        {"status": "adopted", "feature_id": feature.id},
        sort_keys=True,
    )
    compiled_groups: list[int] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-old", name="old", description="old")],
        execution_order=[["TASK-old"]],
        complete=True,
    )

    async def _compile_contracts(*args, **_kwargs):
        compiled_groups.append(args[3])
        return implementation_module.TaskContractCompileOutcome(
            approved=False,
            failure="adopted group must revalidate",
            failure_class="contract_compile",
            failure_type="contract_invalid_path",
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _compile_contracts,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    outcome = await _implement_dag(runner, feature, dag)

    assert compiled_groups == [0]
    assert "adopted group must revalidate" in outcome.failure
    assert "legacy completed TASK-old" not in outcome.implementation_text


@pytest.mark.asyncio
async def test_incomplete_legacy_checkpoint_does_not_skip_contract_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    artifacts.store["dag-group:0"] = _legacy_completed_group_checkpoint(
        group_idx=0,
        task_id="TASK-missing",
    )
    checkpoint = json.loads(artifacts.store["dag-group:0"])
    checkpoint["results"] = []
    artifacts.store["dag-group:0"] = json.dumps(checkpoint, sort_keys=True)
    compiled_groups: list[int] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-missing", name="missing", description="missing")],
        execution_order=[["TASK-missing"]],
        complete=True,
    )

    async def _compile_contracts(*args, **_kwargs):
        compiled_groups.append(args[3])
        return implementation_module.TaskContractCompileOutcome(
            approved=False,
            failure="incomplete group must rerun",
            failure_class="contract_compile",
            failure_type="contract_invalid_path",
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _compile_contracts,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    outcome = await _implement_dag(runner, _feature(), dag)

    assert compiled_groups == [0]
    assert "incomplete group must rerun" in outcome.failure
    assert "legacy completed TASK-missing" not in outcome.implementation_text


@pytest.mark.asyncio
async def test_mismatched_legacy_checkpoint_does_not_skip_contract_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    artifacts.store["dag-group:0"] = _legacy_completed_group_checkpoint(
        group_idx=0,
        task_id="OTHER",
    )
    checkpoint = json.loads(artifacts.store["dag-group:0"])
    checkpoint["task_ids"] = ["TASK-mismatch"]
    artifacts.store["dag-group:0"] = json.dumps(checkpoint, sort_keys=True)
    compiled_groups: list[int] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-mismatch",
                name="mismatch",
                description="mismatch",
            )
        ],
        execution_order=[["TASK-mismatch"]],
        complete=True,
    )

    async def _compile_contracts(*args, **_kwargs):
        compiled_groups.append(args[3])
        return implementation_module.TaskContractCompileOutcome(
            approved=False,
            failure="mismatched group must rerun",
            failure_class="contract_compile",
            failure_type="contract_invalid_path",
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _compile_contracts,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    outcome = await _implement_dag(runner, _feature(), dag)

    assert compiled_groups == [0]
    assert "mismatched group must rerun" in outcome.failure
    assert "legacy completed OTHER" not in outcome.implementation_text


@pytest.mark.asyncio
async def test_legacy_completed_checkpoints_resume_at_group_77(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _Artifacts()
    task_ids = [f"TASK-{idx}" for idx in range(78)]
    for group_idx, task_id in enumerate(task_ids[:77]):
        artifacts.store[f"dag-group:{group_idx}"] = _legacy_completed_group_checkpoint(
            group_idx=group_idx,
            task_id=task_id,
        )
    compiled_groups: list[int] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(id=task_id, name=task_id, description=task_id)
            for task_id in task_ids
        ],
        execution_order=[[task_id] for task_id in task_ids],
        complete=True,
    )

    async def _compile_contracts(*args, **_kwargs):
        compiled_groups.append(args[3])
        return implementation_module.TaskContractCompileOutcome(
            approved=False,
            failure=f"reached g{args[3]}",
            failure_class="contract_compile",
            failure_type="contract_invalid_path",
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _compile_contracts,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    outcome = await _implement_dag(runner, _feature(), dag)

    assert compiled_groups == [77]
    assert "reached g77" in outcome.failure
    assert "legacy completed TASK-76" in outcome.implementation_text


@pytest.mark.asyncio
async def test_contract_compile_resolves_missing_task_repo_from_writable_registry(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    artifacts = _Artifacts()
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(
                feature_root,
                "app",
                "repo-app",
                writable_task_ids=["TASK-registry"],
            ),
            _authority_repo(feature_root, "lib", "repo-lib"),
        ],
    )
    task = ImplementationTask(
        id="TASK-registry",
        name="registry",
        description="registry-backed contract",
        file_scope=[TaskFileScope(path="src/main.py", action="create")],
    )

    outcome = await _compile_single_contract_with_registry(
        workspace_root,
        feature_root,
        artifacts,
        registry,
        task,
    )

    assert outcome.approved is True
    projection = json.loads(artifacts.store["dag-task-contract:TASK-registry"])
    assert projection["repo_id"] == "repo-app"
    assert projection["path_counts"]["allowed_paths"] == 1


@pytest.mark.asyncio
async def test_contract_compile_resolves_missing_task_repo_from_read_only_registry(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    artifacts = _Artifacts()
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(feature_root, "app", "repo-app"),
            _authority_repo(
                feature_root,
                "docs",
                "repo-docs",
                read_only_task_ids=["TASK-read"],
            ),
        ],
    )
    task = ImplementationTask(
        id="TASK-read",
        name="read",
        description="read-only contract",
        file_scope=[TaskFileScope(path="reference.md", action="read_only")],
    )

    outcome = await _compile_single_contract_with_registry(
        workspace_root,
        feature_root,
        artifacts,
        registry,
        task,
    )

    assert outcome.approved is True
    projection = json.loads(artifacts.store["dag-task-contract:TASK-read"])
    assert projection["repo_id"] == "repo-docs"
    assert projection["path_counts"]["read_only_paths"] == 1


@pytest.mark.asyncio
async def test_contract_compile_missing_registry_mapping_keeps_invalid_path_blocker(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    artifacts = _Artifacts()
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(feature_root, "app", "repo-app"),
            _authority_repo(feature_root, "lib", "repo-lib"),
        ],
    )
    task = ImplementationTask(
        id="TASK-unmapped",
        name="unmapped",
        description="unmapped contract",
        file_scope=[TaskFileScope(path="src/main.py", action="create")],
    )

    outcome = await _compile_single_contract_with_registry(
        workspace_root,
        feature_root,
        artifacts,
        registry,
        task,
    )

    assert outcome.approved is False
    assert outcome.failure_class == "contract_compile"
    assert outcome.failure_type == "contract_invalid_path"
    failure = json.loads(artifacts.store["dag-task-contract:compile-failure:g0"])
    assert failure["failure_type"] == "contract_invalid_path"
    assert "repo_id or repo_path is required" in failure["error"]


@pytest.mark.asyncio
async def test_contract_compile_ambiguous_registry_mapping_blocks_without_guessing(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    artifacts = _Artifacts()
    registry = _authority_registry(
        feature_root,
        [
            _authority_repo(
                feature_root,
                "app",
                "repo-app",
                writable_task_ids=["TASK-ambiguous"],
            ),
            _authority_repo(
                feature_root,
                "lib",
                "repo-lib",
                read_only_task_ids=["TASK-ambiguous"],
            ),
        ],
    )
    task = ImplementationTask(
        id="TASK-ambiguous",
        name="ambiguous",
        description="ambiguous contract",
        file_scope=[TaskFileScope(path="src/main.py", action="create")],
    )

    outcome = await _compile_single_contract_with_registry(
        workspace_root,
        feature_root,
        artifacts,
        registry,
        task,
    )

    assert outcome.approved is False
    assert outcome.failure_class == "contract_compile"
    assert outcome.failure_type == "contract_invalid_path"
    failure = json.loads(artifacts.store["dag-task-contract:compile-failure:g0"])
    assert failure["ambiguous_repo_ids_by_task"] == {
        "TASK-ambiguous": ["repo-app", "repo-lib"]
    }
    assert "ambiguous" in failure["error"]


@pytest.mark.asyncio
async def test_authority_preflight_artifacts_are_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    runner = _runner(workspace_root, artifacts)
    task = ImplementationTask(
        id="TASK-bounded",
        name="bounded",
        description="bounded",
        repo_path="app",
        file_scope=[
            TaskFileScope(path=f"app/src/file_{idx}.py", action="modify")
            for idx in range(60)
        ],
    )

    first = await _run_workspace_authority_pre_dispatch_adapter(
        runner,
        feature,
        7,
        [task],
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )
    first_preflight = artifacts.store["workspace-authority-preflight:g7:initial-dispatch"]
    first_registry = artifacts.store["workspace-authority-registry:g7"]
    second = await _run_workspace_authority_pre_dispatch_adapter(
        runner,
        feature,
        7,
        [task],
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )

    assert first.approved is True
    assert second.approved is True
    assert first_registry == artifacts.store["workspace-authority-registry:g7"]
    assert first_preflight == artifacts.store["workspace-authority-preflight:g7:initial-dispatch"]
    payload = json.loads(first_preflight)
    assert payload["authoritative_mode"] == "compatibility_projection"
    assert len(payload["targets"]) == 51
    assert payload["targets"][-1] == {"bounded": "list_truncated", "omitted": 10}
    assert len(payload["preflight"]["resolutions"]) == 51


@pytest.mark.asyncio
async def test_authority_unavailable_fails_closed_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    task = ImplementationTask(
        id="TASK-unavailable",
        name="unavailable",
        description="unavailable",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
    )
    monkeypatch.setattr(implementation_module, "WorkspaceAuthority", None)

    outcome = await _run_workspace_authority_pre_dispatch_adapter(
        _runner(workspace_root, artifacts),
        _feature(),
        7,
        [task],
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )

    assert outcome.approved is False
    assert outcome.operator_required is False
    assert outcome.unavailable_reason == "workspace_authority_unavailable"
    preflight = json.loads(artifacts.store["workspace-authority-preflight:g7:initial-dispatch"])
    routes = json.loads(artifacts.store["workspace-authority-routes:g7:initial-dispatch"])
    snapshot = json.loads(artifacts.store["workspace-authority-snapshot:g7:initial-dispatch"])
    assert preflight["approved"] is False
    assert preflight["status"] == "failed"
    assert preflight["failure_type"] == "workspace_authority_unavailable"
    assert routes["routes"][0]["deterministic_workflow_blocker"] is True
    assert routes["routes"][0]["payload"]["blocked_before_dispatch"] is True
    assert snapshot["status"] == "blocked"


@pytest.mark.asyncio
async def test_dirty_workspace_snapshot_blocks_dispatch(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'dirty'\n", encoding="utf-8")
    artifacts = _Artifacts()
    task = ImplementationTask(
        id="TASK-dirty",
        name="dirty",
        description="dirty",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
    )

    outcome = await _run_workspace_authority_pre_dispatch_adapter(
        _runner(workspace_root, artifacts),
        _feature(),
        7,
        [task],
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )

    assert outcome.approved is False
    assert outcome.operator_required is False
    assert outcome.routes[0].failure_type == "dirty_snapshot_before_dispatch"
    routes = json.loads(artifacts.store["workspace-authority-routes:g7:initial-dispatch"])
    assert routes["routes"][0]["failure_class"] == "workspace_dirty"
    assert routes["routes"][0]["status"] == "blocked"
    assert routes["routes"][0]["deterministic_workflow_blocker"] is True
    assert routes["routes"][0]["payload"]["dirty_paths"] == ["src/main.py"]


@pytest.mark.asyncio
async def test_unresolved_workspace_ambiguity_blocks_with_typed_evidence(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "backend")
    _repo(feature_root, "frontend")
    artifacts = _Artifacts()
    task = ImplementationTask(
        id="TASK-ambiguous",
        name="ambiguous",
        description="ambiguous",
        file_scope=[TaskFileScope(path="src/shared_name.py", action="modify")],
    )

    outcome = await _run_workspace_authority_pre_dispatch_adapter(
        _runner(workspace_root, artifacts),
        _feature(),
        8,
        [task],
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )

    assert outcome.approved is False
    assert outcome.operator_required is True
    routes = json.loads(artifacts.store["workspace-authority-routes:g8:initial-dispatch"])
    assert routes["routes"][0]["source"] == "workspace_authority"
    assert routes["routes"][0]["operator_required"] is True
    assert routes["routes"][0]["payload"]["reason"] == "ambiguous_relative_path"


@pytest.mark.asyncio
async def test_live_dag_dispatch_stops_before_agent_run_on_authority_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "backend")
    _repo(feature_root, "frontend")
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _unexpected_alias_guard(*_args, **_kwargs):
        raise AssertionError("legacy alias guard should not run after authority blocks")

    async def _unexpected_run(*_args, **_kwargs):
        raise AssertionError("implementer runtime must not start after authority blocks")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _unexpected_alias_guard,
    )
    runner = _runner(workspace_root, artifacts)
    runner.run = _unexpected_run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-ambiguous",
                name="ambiguous",
                description="ambiguous",
                file_scope=[TaskFileScope(path="src/shared_name.py", action="modify")],
            )
        ],
        execution_order=[["TASK-ambiguous"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "quiesced"
    assert "WorkspaceAuthority pre-dispatch guard blocked" in outcome.failure
    assert "workspace-authority-preflight:g0:initial-dispatch" in artifacts.store
    assert "workspace-authority-routes:g0:initial-dispatch" in artifacts.store
    assert "workspace-authority-snapshot:g0:initial-dispatch" in artifacts.store


@pytest.mark.asyncio
async def test_live_dag_dispatch_blocks_repairable_authority_routes_before_legacy_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _repairable_authority(*_args, **_kwargs):
        return implementation_module.WorkspaceAuthorityCompatibilityOutcome(
            approved=False,
            operator_required=False,
            routes=[
                SimpleNamespace(
                    failure_class="worktree_alias",
                    failure_type="alias_canonical_divergent",
                    route="run_canonicalization_repair",
                    operator_required=False,
                )
            ],
        )

    async def _unexpected_alias_guard(*_args, **_kwargs):
        raise AssertionError("typed repairable authority route must block before legacy guard")

    async def _unexpected_run(*_args, **_kwargs):
        raise AssertionError("implementer runtime must wait for legacy repair guard")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_workspace_authority_pre_dispatch_adapter",
        _repairable_authority,
    )
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _unexpected_alias_guard,
    )
    runner = _runner(workspace_root, artifacts)
    runner.run = _unexpected_run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-alias",
                name="alias",
                description="alias",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/file.py", action="modify")],
            )
        ],
        execution_order=[["TASK-alias"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "WorkspaceAuthority pre-dispatch guard blocked" in outcome.failure
    assert "run_canonicalization_repair" in outcome.failure


@pytest.mark.asyncio
async def test_resolvable_acl_normalization_does_not_escalate_to_operator(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app", agent_writable=False)
    (repo / "src").chmod(0o755)
    artifacts = _Artifacts()
    task = ImplementationTask(
        id="TASK-acl",
        name="acl",
        description="acl",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/new_file.py", action="create")],
    )

    outcome = await _run_workspace_authority_pre_dispatch_adapter(
        _runner(workspace_root, artifacts),
        _feature(),
        9,
        [task],
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="dag-sha",
    )

    assert outcome.approved is True
    assert outcome.operator_required is False
    assert not outcome.routes
    acl = json.loads(artifacts.store["workspace-authority-acl:g9:initial-dispatch"])
    assert acl["acl_normalization"]["approved"] is True
    assert acl["acl_normalization"]["repair_route"] is None
    assert acl["acl_normalization"]["changed"]


@pytest.mark.asyncio
async def test_live_dag_dispatch_persists_contract_before_runtime_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    prompts: list[str] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_noop(*_args, **_kwargs):
        if subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout:
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "test commit"], cwd=repo, check=True)
        return ""

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        prompts.append(ask.prompt)
        _write_sandbox_file(ask, "src/main.py", "value = 'new'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-contract",
            summary="done",
            status="completed",
            files_modified=["app/src/main.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/main.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    projection = json.loads(artifacts.store["dag-task-contract:TASK-contract"])
    assert projection["contract_digest"]
    assert projection["path_counts"]["allowed_paths"] == 1
    _artifact_key_with_prefix(artifacts, "dag-sandbox-patch:g0:attempt-0:repo-")
    verdict = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:TASK-contract:canonical-precommit-g0-implementation-repo-",
    )])
    assert verdict["approved"] is True
    assert prompts
    assert "## Deliverable Contract" in prompts[0]
    assert "Contract digest" in prompts[0]
    assert "## File Scope" not in prompts[0]
    assert (
        subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
        == ""
    )


@pytest.mark.asyncio
async def test_large_dag_prompt_context_is_sandbox_local_and_not_captured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    prompts: list[str] = []
    sandbox_refs: list[Path] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_noop(*_args, **_kwargs):
        if subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout:
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "test commit"], cwd=repo, check=True)
        return ""

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        prompts.append(ask.prompt)
        metadata = ask.actor.role.metadata
        sandbox_cwd = Path(metadata["workspace_override"])
        binding = metadata["runtime_workspace_binding"]
        binding_env = binding.get("env", {}) if isinstance(binding, dict) else binding.env
        sandbox_root = Path(binding_env["IRIAI_SANDBOX_ROOT"])
        refs_path = sandbox_root / ".iriai-context" / "TASK-prompt" / "refs.md"
        sandbox_refs.append(refs_path)
        assert refs_path.exists()
        assert sandbox_cwd != repo
        assert sandbox_root != repo
        assert not (repo / ".iriai-context").exists()
        assert ".iriai-context" in ask.prompt
        assert "TASK-prompt/refs.md" in ask.prompt
        _write_sandbox_file(ask, "src/main.py", "value = 'sandboxed'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-prompt",
            summary="done",
            status="completed",
            files_modified=["app/src/main.py"],
        )

    monkeypatch.setattr(implementation_module, "PROMPT_FILE_THRESHOLD", 1)
    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/main.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-prompt",
                name="prompt",
                description="prompt",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
                reference_material=[
                    {
                        "source": "Spec",
                        "content": "important implementation context " * 20,
                    }
                ],
            )
        ],
        execution_order=[["TASK-prompt"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert prompts
    assert sandbox_refs
    assert not (repo / ".iriai-context").exists()
    patch_projection = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-sandbox-patch:g0:",
    )])
    assert patch_projection["changed_paths"] == ["src/main.py"]


def test_sandbox_prompt_context_sanitizes_task_id_path_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    task = ImplementationTask(
        id="TASK/../../src/generated\n*.py",
        name="unsafe",
        description="unsafe",
        reference_material=[
            {
                "source": "Spec",
                "content": "unsafe path context " * 20,
            }
        ],
    )
    inline_prompt = implementation_module._build_task_prompt(task)

    monkeypatch.setattr(implementation_module, "PROMPT_FILE_THRESHOLD", 1)
    prompt = implementation_module._build_task_prompt_with_optional_sandbox_context(
        task,
        repo_prefix="",
        task_contract=None,
        handover_context="",
        inline_prompt=inline_prompt,
        context_base=repo,
        log_label="unsafe task",
    )

    refs_paths = list((repo / ".iriai-context").glob("*/refs.md"))
    assert len(refs_paths) == 1
    assert refs_paths[0].is_relative_to(repo / ".iriai-context")
    assert not (repo / "src").exists()
    prompt_ref = next(
        part for part in prompt.split("`")
        if part.startswith(".iriai-context/") and part.endswith("/refs.md")
    )
    assert "TASK/../../" not in prompt_ref
    assert "*.py" not in prompt_ref
    assert "\n" not in prompt_ref
    exclude_text = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "TASK/../../" not in exclude_text
    assert "*.py" not in exclude_text


def test_sandbox_prompt_context_rejects_symlinked_context_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    product_target = repo / "src"
    product_target.mkdir()
    (repo / ".iriai-context").symlink_to(product_target, target_is_directory=True)
    task = ImplementationTask(
        id="TASK-safe",
        name="safe",
        description="safe",
        reference_material=[
            {
                "source": "Spec",
                "content": "context " * 20,
            }
        ],
    )
    inline_prompt = implementation_module._build_task_prompt(task)

    monkeypatch.setattr(implementation_module, "PROMPT_FILE_THRESHOLD", 1)
    with pytest.raises(implementation_module.SandboxWorkflowBlocker):
        implementation_module._build_task_prompt_with_optional_sandbox_context(
            task,
            repo_prefix="",
            task_contract=None,
            handover_context="",
            inline_prompt=inline_prompt,
            context_base=repo,
            log_label="safe task",
        )

    assert not (product_target / "TASK-safe" / "refs.md").exists()


@pytest.mark.asyncio
async def test_live_dag_dispatch_captures_sandbox_patch_before_durable_merge_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    events: list[str] = []

    async def _commit_noop(*_args, **_kwargs):
        events.append("commit")
        assert any(
            key.startswith(
                "dag-contract-verdict:g0:TASK-sandbox:canonical-precommit-g0-implementation-repo-"
            )
            for key in artifacts.store
        )
        return ""

    async def _verify_ok(*_args, **_kwargs):
        events.append("verify")
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        sandbox_cwd = Path(ask.actor.role.metadata["workspace_override"])
        assert sandbox_cwd != repo
        assert ask.actor.role.metadata["sandbox_required"] is True
        (sandbox_cwd / "src").mkdir(exist_ok=True)
        (sandbox_cwd / "src" / "generated.py").write_text(
            "print('sandboxed')\n",
            encoding="utf-8",
        )
        assert not (repo / "src" / "generated.py").exists()
        return implementation_module.ImplementationResult(
            task_id="TASK-sandbox",
            summary="created from sandbox",
            status="completed",
            files_created=["app/src/generated.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-sandbox",
                name="sandbox",
                description="sandbox",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-sandbox"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert not (repo / "src" / "generated.py").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    assert status == ""
    patch_projection = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-sandbox-patch:g0:",
    )])
    assert patch_projection["changed_paths"] == ["src/generated.py"]
    verdict = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:TASK-sandbox:canonical-precommit-g0-implementation-repo-",
    )])
    assert verdict["approved"] is True
    assert verdict["patch_summary_id"]
    assert verdict["metadata"]["captured_patch_summary_id"] == verdict["patch_summary_id"]
    assert verdict["metadata"]["diff_artifact_id"]
    assert verdict["metadata"]["capture_validated_before_promotion"] is True
    assert verdict["metadata"]["promotion_order"] == "verdict_before_promotion"
    assert events == []


@pytest.mark.asyncio
async def test_live_dag_dispatch_captures_patch_without_production_promotion_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_must_not_run(*_args, **_kwargs):
        raise AssertionError("commit must wait for durable merge queue promotion")

    async def _verify_must_not_run(*_args, **_kwargs):
        raise AssertionError("verify must wait for durable merge queue promotion")

    async def _run(ask, *_args, **_kwargs):
        sandbox_cwd = Path(ask.actor.role.metadata["workspace_override"])
        (sandbox_cwd / "src").mkdir(exist_ok=True)
        (sandbox_cwd / "src" / "generated.py").write_text(
            "print('sandboxed')\n",
            encoding="utf-8",
        )
        return implementation_module.ImplementationResult(
            task_id="TASK-sandbox",
            summary="created from sandbox",
            status="completed",
            files_created=["app/src/generated.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_must_not_run)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_must_not_run)
    runner = _runner(
        workspace_root,
        artifacts,
        allow_sandbox_patch_promotion_bridge=False,
    )
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-sandbox",
                name="sandbox",
                description="sandbox",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-sandbox"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert not (repo / "src" / "generated.py").exists()
    _artifact_key_with_prefix(artifacts, "dag-sandbox-patch:g0:")
    verdict = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:TASK-sandbox:canonical-precommit-g0-implementation-repo-",
    )])
    assert verdict["approved"] is True
    assert verdict["patch_summary_id"]


@pytest.mark.asyncio
async def test_live_dag_dispatch_uses_dispatcher_not_direct_runner_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    dispatcher_calls: list[object] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_run(*_args, **_kwargs):
        raise AssertionError("implementation task bypassed RuntimeDispatcher")

    class _FakeRuntimeDispatcher:
        def __init__(self, **kwargs):
            dispatcher_calls.append(kwargs)
            self._normalizer = kwargs["output_normalizer"]

        async def dispatch(self, request):
            self._normalizer.result = implementation_module.ImplementationResult(
                task_id=request.task_id,
                summary="done by dispatcher",
                status="completed",
                files_created=["app/src/generated.py"],
            )
            return implementation_module.DispatcherOutcome(
                attempt_id=77,
                state="succeeded",
                status="succeeded",
                runtime_terminal_reason="completed",
                structured_result_evidence_id=301,
                raw_text_ref=None,
                patch_summary_ids=[701],
                compatibility_artifact_ids=[401],
                runtime_failure_id=None,
                typed_failure_id=None,
                idempotency_key=request.idempotency_key,
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", _FakeRuntimeDispatcher)
    runner = _runner(workspace_root, artifacts)
    runner.run = _unexpected_run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-dispatcher",
                name="dispatcher",
                description="dispatcher",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-dispatcher"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert dispatcher_calls


@pytest.mark.asyncio
async def test_implementation_sandbox_port_uses_dispatcher_attempt_id(
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    runner = _runner(workspace_root, _Artifacts())
    feature = _feature()
    task = ImplementationTask(
        id="TASK-dispatch-attempt-sandbox",
        name="dispatch attempt sandbox",
        description="dispatch attempt sandbox",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
    )
    port = implementation_module._ImplementationSandboxPort(
        runner=runner,
        feature=feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="d" * 64,
        group_idx=77,
        task_idx=2,
        attempt=0,
        task=task,
        task_contract=None,
        repo_id="app",
        ws_path=str(repo),
        snapshots=[],
        runtime="codex",
        stage="implementation",
    )

    await port.bind_runtime(SimpleNamespace(), 39)

    assert port.task_binding is not None
    assert port.task_binding.lease.attempt_no == 39
    assert Path(port.task_binding.lease.root).name == "attempt-39"


@pytest.mark.asyncio
async def test_live_dag_dispatch_retries_retryable_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    dispatch_requests: list[object] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    class _RetryRuntimeDispatcher:
        def __init__(self, **kwargs):
            self._normalizer = kwargs["output_normalizer"]

        async def dispatch(self, request):
            dispatch_requests.append(request)
            if request.retry == 0:
                return implementation_module.DispatcherOutcome(
                    attempt_id=100,
                    state="failed",
                    status="failed",
                    runtime_terminal_reason="provider_error",
                    structured_result_evidence_id=None,
                    raw_text_ref=501,
                    patch_summary_ids=[],
                    compatibility_artifact_ids=[],
                    runtime_failure_id=200,
                    typed_failure_id=200,
                    idempotency_key=request.idempotency_key,
                )
            self._normalizer.result = implementation_module.ImplementationResult(
                task_id=request.task_id,
                summary="done after retry",
                status="completed",
                files_created=["app/src/generated.py"],
            )
            return implementation_module.DispatcherOutcome(
                attempt_id=101,
                state="succeeded",
                status="succeeded",
                runtime_terminal_reason="completed",
                structured_result_evidence_id=301,
                raw_text_ref=None,
                patch_summary_ids=[701],
                compatibility_artifact_ids=[],
                runtime_failure_id=None,
                typed_failure_id=None,
                idempotency_key=request.idempotency_key,
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", _RetryRuntimeDispatcher)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-retry",
                name="retry",
                description="retry",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-retry"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert [request.retry for request in dispatch_requests] == [0, 1]
    assert dispatch_requests[0].idempotency_key != dispatch_requests[1].idempotency_key
    assert dispatch_requests[0].sandbox_id != dispatch_requests[1].sandbox_id


@pytest.mark.asyncio
async def test_live_dag_dispatch_retries_legacy_sandbox_path_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    store = _BridgeExecutionControlStore()
    store.runtime_failure_contexts[49] = {
        "summary": "Sandbox binding failed",
        "details": {
            "message": (
                "sandbox path already belongs to a different lease: "
                "/tmp/workspace/.iriai/features/feature/sandboxes/g77/attempt-2"
            ),
        },
    }
    dispatch_requests: list[object] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    class _RetryRuntimeDispatcher:
        def __init__(self, **kwargs):
            self._normalizer = kwargs["output_normalizer"]

        async def dispatch(self, request):
            dispatch_requests.append(request)
            if request.retry == 0:
                return implementation_module.DispatcherOutcome(
                    attempt_id=39,
                    state="failed",
                    status="failed",
                    runtime_terminal_reason="sandbox_binding_failed",
                    structured_result_evidence_id=None,
                    raw_text_ref=None,
                    patch_summary_ids=[],
                    compatibility_artifact_ids=[],
                    runtime_failure_id=49,
                    typed_failure_id=49,
                    idempotency_key=request.idempotency_key,
                )
            self._normalizer.result = implementation_module.ImplementationResult(
                task_id=request.task_id,
                summary="done after lease-collision retry",
                status="completed",
                files_created=["app/src/generated.py"],
            )
            return implementation_module.DispatcherOutcome(
                attempt_id=40,
                state="succeeded",
                status="succeeded",
                runtime_terminal_reason="completed",
                structured_result_evidence_id=301,
                raw_text_ref=None,
                patch_summary_ids=[701],
                compatibility_artifact_ids=[],
                runtime_failure_id=None,
                typed_failure_id=None,
                idempotency_key=request.idempotency_key,
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", _RetryRuntimeDispatcher)
    runner = _runner(workspace_root, artifacts, execution_control_store=store)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-lease-collision",
                name="lease collision",
                description="lease collision",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-lease-collision"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert [request.retry for request in dispatch_requests] == [0, 1]
    assert store.runtime_failure_context_requests == [
        {"feature_id": feature.id, "failure_id": 49}
    ]


@pytest.mark.asyncio
async def test_live_dag_dispatch_retries_stale_context_failure_with_positive_prompt_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()

    class _ReplayContextFailureStore(_BridgeExecutionControlStore):
        async def start_dispatch_attempt(self, request):
            self.start_requests.append(request)
            if request.retry == 0:
                outcome = implementation_module.DispatcherOutcome(
                    attempt_id=900,
                    state="failed",
                    status="failed",
                    runtime_terminal_reason="context_materialization_failed",
                    structured_result_evidence_id=None,
                    raw_text_ref=None,
                    patch_summary_ids=[],
                    compatibility_artifact_ids=[],
                    runtime_failure_id=901,
                    typed_failure_id=901,
                    idempotency_key=request.idempotency_key,
                )
                return SimpleNamespace(
                    attempt_id=900,
                    created=False,
                    attempt=SimpleNamespace(
                        status="failed",
                        dispatcher_state="failed",
                        request_digest=request.request_digest,
                        payload={"dispatch_outcome": outcome.model_dump(mode="json")},
                    ),
                )
            return SimpleNamespace(
                attempt_id=900 + request.retry,
                created=True,
                attempt=SimpleNamespace(
                    status="started",
                    dispatcher_state="attempt_started",
                    payload={},
                    request_digest=request.request_digest,
                ),
            )

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _run(ask, *_args, **_kwargs):
        _write_sandbox_file(ask, "src/generated.py", "value = 'retry'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-context-retry",
            summary="completed after context retry",
            status="completed",
            files_created=["app/src/generated.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = _ReplayContextFailureStore()
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-context-retry",
                name="context retry",
                description="context retry",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-context-retry"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    store = runner.services["execution_control_store"]
    assert [request.retry for request in store.start_requests] == [0, 1]
    assert store.prompt_contexts
    assert store.prompt_contexts[-1].prompt_ref > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_reason", ["sandbox_binding_failed", "prompt_too_large"])
async def test_live_dag_dispatch_does_not_retry_non_retryable_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    terminal_reason: str,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    dispatch_requests: list[object] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    class _NonRetryRuntimeDispatcher:
        def __init__(self, **_kwargs):
            pass

        async def dispatch(self, request):
            dispatch_requests.append(request)
            return implementation_module.DispatcherOutcome(
                attempt_id=100,
                state="failed",
                status="failed",
                runtime_terminal_reason=terminal_reason,
                structured_result_evidence_id=None,
                raw_text_ref=None,
                patch_summary_ids=[],
                compatibility_artifact_ids=[],
                runtime_failure_id=200,
                typed_failure_id=200,
                idempotency_key=request.idempotency_key,
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", _NonRetryRuntimeDispatcher)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-no-retry",
                name="no retry",
                description="no retry",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-no-retry"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert terminal_reason in outcome.failure
    assert [request.retry for request in dispatch_requests] == [0]


@pytest.mark.asyncio
async def test_live_dag_dispatch_retryable_terminal_outcome_exhausts_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    dispatch_requests: list[object] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    class _AlwaysFailRuntimeDispatcher:
        def __init__(self, **_kwargs):
            pass

        async def dispatch(self, request):
            dispatch_requests.append(request)
            failure_id = 200 + int(request.retry)
            return implementation_module.DispatcherOutcome(
                attempt_id=100 + int(request.retry),
                state="failed",
                status="failed",
                runtime_terminal_reason="provider_error",
                structured_result_evidence_id=None,
                raw_text_ref=500 + int(request.retry),
                patch_summary_ids=[],
                compatibility_artifact_ids=[],
                runtime_failure_id=failure_id,
                typed_failure_id=failure_id,
                idempotency_key=request.idempotency_key,
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", _AlwaysFailRuntimeDispatcher)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-exhaust",
                name="exhaust",
                description="exhaust",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-exhaust"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert [request.retry for request in dispatch_requests] == [0, 1, 2, 3, 4, 5]
    assert "typed_failure_id=205" in outcome.failure
    assert "runtime_failure_id=205" in outcome.failure
    assert "dispatcher_attempt_id=105" in outcome.failure


@pytest.mark.asyncio
async def test_execution_control_bridge_records_typed_runtime_failure_without_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    store = _BridgeExecutionControlStore()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_commit(*_args, **_kwargs):
        raise AssertionError("commit must not run after typed runtime failure")

    async def _run(ask, *_args, **_kwargs):
        _write_sandbox_file(ask, "src/partial.py", "partial\n")
        raise RuntimeError("provider exploded after start")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _unexpected_commit)
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = store
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-failure",
                name="failure",
                description="failure",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/partial.py", action="create")],
            )
        ],
        execution_order=[["TASK-failure"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "provider exploded after start" in outcome.failure
    assert store.runtime_failures
    failure = store.runtime_failures[0]
    assert failure.failure_class == "runtime_provider"
    assert failure.failure_type == "provider_internal_error"
    assert failure.terminal_reason == "provider_error"
    assert failure.operator_required is False
    assert store.finished
    assert store.finished[-1].runtime_failure_id == store.finished[-1].typed_failure_id
    assert store.finished[-1].runtime_failure_id is not None
    assert not store.projected_attempts
    assert not any(key.startswith("dag-task:TASK-failure") for key in artifacts.store)
    assert not (repo / "src" / "partial.py").exists()


@pytest.mark.asyncio
async def test_execution_control_bridge_duplicate_nonterminal_replays_to_finish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    store = _BridgeExecutionControlStore(duplicate_nonterminal=True)

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _run(ask, *_args, **_kwargs):
        _write_sandbox_file(ask, "src/generated.py", "value = 'replayed'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-idempotent",
            summary="replayed attempt finished",
            status="completed",
            files_created=["app/src/generated.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, created=["src/generated.py"])
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = store
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-idempotent",
                name="idempotent",
                description="idempotent",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/generated.py", action="create")],
            )
        ],
        execution_order=[["TASK-idempotent"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert not store.runtime_failures
    assert store.prompt_contexts
    assert store.runtime_invocations
    assert store.structured_outputs
    assert store.finished
    assert not (repo / "src" / "generated.py").exists()


@pytest.mark.asyncio
async def test_dispatch_bridge_reconstructs_terminal_success_from_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    artifacts.store["dag-task:TASK-replay"] = implementation_module.ImplementationResult(
        task_id="TASK-replay",
        summary="replayed success",
        status="completed",
        files_modified=["app/src/replayed.py"],
    ).model_dump_json()

    class _ReplayRuntimeDispatcher:
        def __init__(self, **_kwargs):
            pass

        async def dispatch(self, request):
            return implementation_module.DispatcherOutcome(
                attempt_id=88,
                state="succeeded",
                status="succeeded",
                runtime_terminal_reason="completed",
                structured_result_evidence_id=301,
                raw_text_ref=901,
                patch_summary_ids=[701],
                compatibility_artifact_ids=[401],
                runtime_failure_id=None,
                typed_failure_id=None,
                idempotency_key=request.idempotency_key,
            )

    monkeypatch.setattr(implementation_module, "RuntimeDispatcher", _ReplayRuntimeDispatcher)
    runner = _runner(workspace_root, artifacts)
    task = ImplementationTask(
        id="TASK-replay",
        name="replay",
        description="replay",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/replayed.py", action="modify")],
    )

    result, outcome = await implementation_module._dispatch_task_attempt_via_runtime_dispatcher(
        runner=runner,
        feature=feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="d" * 64,
        group_idx=0,
        task_idx=0,
        attempt=0,
        task=task,
        task_contract=None,
        ws_path=str(feature_root / "app"),
        snapshots=[],
        runtime_hint="codex",
        runtime_policy=implementation_module.DEFAULT_RUNTIME_POLICY,
        repo_prefix="app",
        inline_prompt=implementation_module._build_task_prompt(task, repo_prefix="app/"),
        handover_context="",
        stage="implementation",
        actor_suffix="replay",
        log_label="Replay",
    )

    assert outcome.status == "succeeded"
    assert result.summary == "replayed success"
    assert "dispatcher_attempt_id=88" in result.notes
    assert "canonical_mutation=pending_durable_merge_queue" in result.notes


def test_output_normalizer_persists_patch_summary_ids_before_projection() -> None:
    normalizer = implementation_module._ImplementationOutputNormalizer(
        task=ImplementationTask(
            id="TASK-normalize",
            name="normalize",
            description="normalize",
        ),
        repo_prefix="app",
    )

    record = normalizer.normalize(
        request=SimpleNamespace(task_id="TASK-normalize"),
        response=SimpleNamespace(
            structured_output={
                "task_id": "TASK-normalize",
                "summary": "done",
                "status": "completed",
            },
            raw_text_ref=None,
            raw_artifact_id=None,
        ),
        schema_name="ImplementationResult",
        schema_digest="digest",
        patch_capture=SimpleNamespace(
            patch_summary_ids=[702, 701, 701],
            changed_paths=["src/generated.py"],
        ),
    )

    projected = json.loads(record.projection_body)
    assert "patch_summary_ids=701,702" in projected["notes"]
    assert "canonical_mutation=pending_durable_merge_queue" in projected["notes"]
    assert projected["files_modified"] == ["app/src/generated.py"]


@pytest.mark.asyncio
async def test_dispatch_journal_port_converts_store_idempotency_conflict() -> None:
    class _ConflictStore:
        async def start_dispatch_attempt(self, _request):
            raise implementation_module.StoredIdempotencyConflict("digest mismatch")

    request = SimpleNamespace(
        feature_id="feature-bridge",
        dag_sha256="d" * 64,
        group_idx=0,
        task_id="TASK-conflict",
        task_name="conflict",
        retry=0,
        retry_identity={"task_id": "TASK-conflict", "retry": 0},
        contract_ids=[101],
        sandbox_id="sandbox-conflict",
        workspace_snapshot_ids=[201],
        base_commit_by_repo={"app": "abc123"},
        runtime_policy=implementation_module.DEFAULT_RUNTIME_POLICY,
        runtime_policy_digest="policy-digest",
        actor_role="implementer",
        actor_metadata={"runtime": "codex"},
        prior_evidence_ids=[],
        cancellation_token="",
        request_digest="requested-digest",
        idempotency_key="idem:dispatch-conflict:TASK-conflict",
    )
    port = implementation_module._ExecutionControlDispatchJournalPort(_ConflictStore())

    with pytest.raises(implementation_module.DispatcherDispatchIdempotencyConflict) as excinfo:
        await port.start_dispatch_attempt(request)

    assert excinfo.value.idempotency_key == request.idempotency_key
    assert excinfo.value.requested_digest == request.request_digest


@pytest.mark.asyncio
async def test_partial_resume_uses_original_task_index_for_sandbox_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    artifacts.store["dag-task:TASK-0"] = implementation_module.ImplementationResult(
        task_id="TASK-0",
        summary="already done",
        status="completed",
        files_modified=["app/src/zero.py"],
    ).model_dump_json()
    recorded: list[tuple[str, int]] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _projection_matches(*_args, **_kwargs):
        return True

    async def _approved_verdict_exists(*_args, **_kwargs):
        return True

    async def _bind_sandbox(*_args, **kwargs):
        task = kwargs["task"]
        recorded.append((task.id, kwargs["task_idx"]))
        raise implementation_module.SandboxWorkflowBlocker("simulated crashed running lease")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_projection_matches",
        _projection_matches,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_approved_verdict_exists",
        _approved_verdict_exists,
    )
    monkeypatch.setattr(implementation_module, "_bind_task_sandbox", _bind_sandbox)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-0",
                name="zero",
                description="zero",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/zero.py", action="modify")],
            ),
            ImplementationTask(
                id="TASK-1",
                name="one",
                description="one",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/one.py", action="create")],
            ),
        ],
        execution_order=[["TASK-0", "TASK-1"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert "simulated crashed running lease" in outcome.failure
    assert ("TASK-0", 0) not in recorded
    assert ("TASK-1", 1) in recorded


@pytest.mark.asyncio
async def test_legacy_artifact_only_completed_task_marker_reruns_for_patch_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    artifacts.store["dag-task:TASK-0"] = implementation_module.ImplementationResult(
        task_id="TASK-0",
        summary="legacy already done",
        status="completed",
        files_modified=["app/src/zero.py"],
    ).model_dump_json()
    recorded: list[tuple[str, int]] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _projection_matches(*_args, **_kwargs):
        return False

    async def _approved_verdict_exists(*_args, **_kwargs):
        return False

    async def _bind_sandbox(*_args, **kwargs):
        task = kwargs["task"]
        recorded.append((task.id, kwargs["task_idx"]))
        raise implementation_module.SandboxWorkflowBlocker("simulated pending task")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_projection_matches",
        _projection_matches,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_approved_verdict_exists",
        _approved_verdict_exists,
    )
    monkeypatch.setattr(implementation_module, "_bind_task_sandbox", _bind_sandbox)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-0",
                name="zero",
                description="zero",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/zero.py", action="modify")],
            ),
            ImplementationTask(
                id="TASK-1",
                name="one",
                description="one",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/one.py", action="create")],
            ),
        ],
        execution_order=[["TASK-0", "TASK-1"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert "simulated pending task" in outcome.failure
    assert ("TASK-0", 0) in recorded
    assert ("TASK-1", 1) in recorded


@pytest.mark.asyncio
async def test_typed_completed_task_marker_reruns_for_fresh_patch_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    artifacts.store["dag-task:TASK-0"] = implementation_module.ImplementationResult(
        task_id="TASK-0",
        summary="already done",
        status="completed",
        files_modified=["app/src/zero.py"],
    ).model_dump_json()
    artifacts.store[implementation_module._task_contract_projection_key("TASK-0")] = json.dumps(
        {
            "artifact_schema": "dag-task-contract-projection-v1",
            "task_id": "TASK-0",
            "contract_digest": "previous-contract-digest",
        },
        sort_keys=True,
    )
    recorded: list[tuple[str, int]] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _projection_matches(*_args, **_kwargs):
        return True

    async def _bind_sandbox(*_args, **kwargs):
        task = kwargs["task"]
        recorded.append((task.id, kwargs["task_idx"]))
        raise implementation_module.SandboxWorkflowBlocker("simulated crashed running lease")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_projection_matches",
        _projection_matches,
    )
    monkeypatch.setattr(implementation_module, "_bind_task_sandbox", _bind_sandbox)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-0",
                name="zero",
                description="zero",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/zero.py", action="modify")],
            ),
            ImplementationTask(
                id="TASK-1",
                name="one",
                description="one",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/one.py", action="create")],
            ),
        ],
        execution_order=[["TASK-0", "TASK-1"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert "SANDBOX_WORKFLOW_BLOCKER" in outcome.failure
    assert "simulated crashed running lease" in outcome.failure
    assert ("TASK-0", 0) in recorded


@pytest.mark.asyncio
async def test_stale_group_checkpoint_marker_reruns_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    recorded: list[tuple[str, int]] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-0",
                name="zero",
                description="zero",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/zero.py", action="modify")],
            ),
            ImplementationTask(
                id="TASK-1",
                name="one",
                description="one",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/one.py", action="create")],
            ),
        ],
        execution_order=[["TASK-0"], ["TASK-1"]],
        complete=True,
    )
    dag_sha256 = implementation_module.hashlib.sha256(
        dag.model_dump_json().encode("utf-8")
    ).hexdigest()
    stale_result = implementation_module.ImplementationResult(
        task_id="TASK-0",
        summary="stale checkpoint result",
        status="completed",
        files_modified=["app/src/zero.py"],
    )
    artifacts.store["dag-group:0"] = json.dumps(
        {
            "group_idx": 0,
            "task_ids": ["TASK-0"],
            "results": [stale_result.model_dump()],
            "verdict": "approved",
            "commit_hash": "stale-head",
            "dag_sha256": dag_sha256,
        },
        sort_keys=True,
    )
    artifacts.store["dag-group-commit-proof:0"] = json.dumps(
        {
            "artifact_schema": "dag-group-commit-proof-v1",
            "group_idx": 0,
            "task_ids": ["TASK-0"],
            "dag_sha256": dag_sha256,
            "stage": "checkpoint",
            "commit_hash": "stale-head",
            "repo_heads": "stale-head",
        },
        sort_keys=True,
    )

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _projection_matches(*_args, **_kwargs):
        return True

    async def _bind_sandbox(*_args, **kwargs):
        task = kwargs["task"]
        recorded.append((task.id, kwargs["task_idx"]))
        raise implementation_module.SandboxWorkflowBlocker("stale checkpoint rerun")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_projection_matches",
        _projection_matches,
    )
    monkeypatch.setattr(implementation_module, "_bind_task_sandbox", _bind_sandbox)
    monkeypatch.setattr(
        implementation_module,
        "_current_feature_repo_heads",
        lambda *_args, **_kwargs: "current-head",
    )
    monkeypatch.setattr(
        implementation_module,
        "_feature_repos_clean_for_checkpoint_resume",
        lambda *_args, **_kwargs: True,
    )

    outcome = await _implement_dag(_runner(workspace_root, artifacts), feature, dag)

    assert "stale checkpoint rerun" in outcome.failure
    assert ("TASK-0", 0) in recorded
    assert "stale checkpoint result" not in outcome.implementation_text


@pytest.mark.asyncio
async def test_dirty_group_checkpoint_marker_reruns_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    recorded: list[tuple[str, int]] = []
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-0",
                name="zero",
                description="zero",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/zero.py", action="modify")],
            )
        ],
        execution_order=[["TASK-0"]],
        complete=True,
    )
    dag_sha256 = implementation_module.hashlib.sha256(
        dag.model_dump_json().encode("utf-8")
    ).hexdigest()
    runner = _runner(workspace_root, artifacts)
    repo_heads = implementation_module._current_feature_repo_heads(runner, feature)
    stale_result = implementation_module.ImplementationResult(
        task_id="TASK-0",
        summary="checkpoint result before dirty workspace",
        status="completed",
        files_modified=["app/src/zero.py"],
    )
    artifacts.store["dag-group:0"] = json.dumps(
        {
            "group_idx": 0,
            "task_ids": ["TASK-0"],
            "results": [stale_result.model_dump()],
            "verdict": "approved",
            "commit_hash": repo_heads,
            "dag_sha256": dag_sha256,
        },
        sort_keys=True,
    )
    artifacts.store["dag-group-commit-proof:0"] = json.dumps(
        {
            "artifact_schema": "dag-group-commit-proof-v1",
            "group_idx": 0,
            "task_ids": ["TASK-0"],
            "dag_sha256": dag_sha256,
            "stage": "checkpoint",
            "commit_hash": repo_heads,
            "repo_heads": repo_heads,
        },
        sort_keys=True,
    )
    (repo / "README.md").write_text("dirty after checkpoint\n", encoding="utf-8")

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _projection_matches(*_args, **_kwargs):
        return True

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_projection_matches",
        _projection_matches,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert "workspace_dirty" in outcome.failure
    assert "dirty_snapshot_before_dispatch" in outcome.failure
    assert recorded == []
    assert "checkpoint result before dirty workspace" not in outcome.implementation_text


@pytest.mark.asyncio
async def test_checkpoint_resume_rejects_missing_feature_root() -> None:
    feature = _feature()
    artifacts = _Artifacts()
    dag_sha256 = "d" * 64
    artifacts.store["dag-group-commit-proof:0"] = json.dumps(
        {
            "artifact_schema": "dag-group-commit-proof-v1",
            "group_idx": 0,
            "task_ids": ["TASK-0"],
            "dag_sha256": dag_sha256,
            "stage": "checkpoint",
            "commit_hash": "app:head",
            "repo_heads": "app:head",
        },
        sort_keys=True,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={})

    assert await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-0"],
        dag_sha256=dag_sha256,
        checkpoint={
            "group_idx": 0,
            "task_ids": ["TASK-0"],
            "results": [],
            "verdict": "approved",
            "commit_hash": "app:head",
            "dag_sha256": dag_sha256,
        },
    ) is False


def test_checkpoint_resume_rejects_git_status_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    runner = _runner(workspace_root, _Artifacts())

    def _status_unavailable(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=128,
            stdout="",
            stderr="fatal: index unavailable",
        )

    monkeypatch.setattr(implementation_module.subprocess, "run", _status_unavailable)

    assert implementation_module._feature_repos_clean_for_checkpoint_resume(
        runner,
        _feature(),
    ) is False


@pytest.mark.asyncio
async def test_live_dag_contract_violation_blocks_before_sandbox_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "allowed.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add allowed target"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_must_not_run(*_args, **_kwargs):
        raise AssertionError("commit must not run after pre-promotion contract failure")

    async def _verify_must_not_run(*_args, **_kwargs):
        raise AssertionError("verify must not run after pre-promotion contract failure")

    async def _run(ask, *_args, **_kwargs):
        sandbox_cwd = Path(ask.actor.role.metadata["workspace_override"])
        assert sandbox_cwd != repo
        (sandbox_cwd / "src" / "forbidden.py").write_text(
            "value = 'forbidden'\n",
            encoding="utf-8",
        )
        assert not (repo / "src" / "forbidden.py").exists()
        return implementation_module.ImplementationResult(
            task_id="TASK-guard",
            summary="created out of scope",
            status="completed",
            files_created=["app/src/forbidden.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_must_not_run)
    monkeypatch.setattr(
        implementation_module,
        "_verify_and_fix_group",
        _verify_must_not_run,
    )
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-guard",
                name="guard",
                description="guard",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/allowed.py", action="modify")],
            )
        ],
        execution_order=[["TASK-guard"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "Task contract validation failed before sandbox promotion" in outcome.failure
    assert not (repo / "src" / "forbidden.py").exists()
    assert subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout == ""
    verdict = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:TASK-guard:canonical-precommit-g0-implementation-repo-",
    )])
    assert verdict["approved"] is False
    assert "create_outside_allowed_paths" in verdict["violation_codes"]


@pytest.mark.asyncio
async def test_live_dag_runtime_crash_retains_sandbox_patch_without_product_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_commit(*_args, **_kwargs):
        raise AssertionError("commit must not run after runtime crash")

    async def _unexpected_verdict(*_args, **_kwargs):
        raise AssertionError("contract verdict must not run after runtime crash")

    async def _run(ask, *_args, **_kwargs):
        sandbox_cwd = Path(ask.actor.role.metadata["workspace_override"])
        (sandbox_cwd / "src").mkdir(exist_ok=True)
        (sandbox_cwd / "src" / "crash.py").write_text("partial\n", encoding="utf-8")
        raise RuntimeError("provider crashed after writing")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _unexpected_commit)
    monkeypatch.setattr(
        implementation_module,
        "_record_precommit_contract_verdicts",
        _unexpected_verdict,
    )
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-crash",
                name="crash",
                description="crash",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/crash.py", action="create")],
            )
        ],
        execution_order=[["TASK-crash"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "SANDBOX_WORKFLOW_BLOCKER" in outcome.failure
    assert "provider crashed after writing" in outcome.failure
    assert not (repo / "src" / "crash.py").exists()
    retained_patches = sorted(
        (workspace_root / ".iriai" / "artifacts" / "sandbox").glob("**/*.patch")
    )
    assert retained_patches
    assert all("src/crash.py" in path.read_text(encoding="utf-8") for path in retained_patches)


@pytest.mark.asyncio
async def test_live_dag_dispatch_blocks_invalid_git_root_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = feature_root / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_run(*_args, **_kwargs):
        raise AssertionError("runtime must not start without a valid sandbox worktree")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    runner = _runner(workspace_root, artifacts)
    runner.run = _unexpected_run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-invalid",
                name="invalid",
                description="invalid",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/file.py", action="create")],
            )
        ],
        execution_order=[["TASK-invalid"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "WorkspaceAuthority pre-dispatch guard blocked" in outcome.failure
    assert "status_unavailable" in outcome.failure


@pytest.mark.asyncio
async def test_live_dag_dispatch_reruns_completed_task_without_matching_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    artifacts.store["dag-task:TASK-contract"] = implementation_module.ImplementationResult(
        task_id="TASK-contract",
        summary="old",
        status="completed",
    ).model_dump_json()
    artifacts.store["dag-task-contract:TASK-contract"] = json.dumps({
        "contract_digest": "stale",
    })
    feature = _feature()
    run_count = 0

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_noop(*_args, **_kwargs):
        if subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout:
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "test commit"], cwd=repo, check=True)
        return ""

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        nonlocal run_count
        run_count += 1
        _write_sandbox_file(ask, "src/main.py", "value = 'new'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-contract",
            summary="new",
            status="completed",
            files_modified=["app/src/main.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/main.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(outcome)
    assert run_count == 1
    assert json.loads(artifacts.store["dag-task:TASK-contract"])["summary"] == "new"


@pytest.mark.asyncio
async def test_live_dag_dispatch_reruns_completed_task_without_approved_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    run_count = 0

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_noop(*_args, **_kwargs):
        if subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout:
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "test commit"], cwd=repo, check=True)
        return ""

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        nonlocal run_count
        run_count += 1
        _write_sandbox_file(ask, "src/main.py", f"value = 'new {run_count}'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-contract",
            summary="new",
            status="completed",
            files_modified=["app/src/main.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/main.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )
    first = await _implement_dag(runner, feature, dag)
    _assert_pending_merge_queue_blocker(first)
    first_run_count = run_count
    verdict_key = _artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:TASK-contract:canonical-precommit-g0-implementation-repo-",
    )
    del artifacts.store[verdict_key]
    artifacts.store.pop("dag-task-pending-merge:TASK-contract", None)
    artifacts.store["dag-task:TASK-contract"] = implementation_module.ImplementationResult(
        task_id="TASK-contract",
        summary="new",
        status="completed",
        files_modified=["app/src/main.py"],
    ).model_dump_json()

    second = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(second)
    assert run_count == first_run_count + 1


@pytest.mark.asyncio
async def test_live_dag_dispatch_blocks_completed_task_pending_durable_merge_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    run_count = 0

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_noop(*_args, **_kwargs):
        if subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout:
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "test commit"], cwd=repo, check=True)
        return ""

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        nonlocal run_count
        run_count += 1
        _write_sandbox_file(ask, "src/main.py", f"value = 'new {run_count}'\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-contract",
            summary="new",
            status="completed",
            files_modified=["app/src/main.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/main.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )
    first = await _implement_dag(runner, feature, dag)
    _assert_pending_merge_queue_blocker(first)
    first_run_count = run_count
    assert "canonical_mutation=pending_durable_merge_queue" in artifacts.store[
        "dag-task:TASK-contract"
    ]
    artifacts.store.pop("dag-task-pending-merge:TASK-contract", None)

    second = await _implement_dag(runner, feature, dag)

    _assert_pending_merge_queue_blocker(second)
    assert run_count == first_run_count


@pytest.mark.asyncio
async def test_completed_pending_merge_task_with_patch_ids_enters_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    queued_results = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _authority_ok(*_args, **_kwargs):
        return implementation_module.WorkspaceAuthorityCompatibilityOutcome(
            approved=True,
            registry=_authority_registry(
                feature_root,
                [
                    _authority_repo(
                        feature_root,
                        "app",
                        "app",
                        writable_task_ids=["TASK-contract"],
                        task_ids=["TASK-contract"],
                    )
                ],
            ),
            snapshots=[],
        )

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _acl_ok(*_args, **_kwargs):
        return {"operator_required": False, "problems": []}

    async def _unexpected_runtime(*_args, **_kwargs):
        raise AssertionError("completed pending-queue marker must not invoke runtime")

    async def _enqueue(_runner, _feature, pending_results, **_kwargs):
        queued_results.extend(pending_results)
        return [501]

    async def _drain(*_args, **_kwargs):
        return [SimpleNamespace(succeeded=True, item_id=501, terminal_status="integrated")]

    async def _checkpoint(*_args, **_kwargs):
        return SimpleNamespace(
            checkpointed=True,
            done_queue_item_ids=[501],
            result_commit="abc123",
            detail="",
            routed_failure={},
        )

    async def _noop_refresh(*_args, **_kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_workspace_authority_pre_dispatch_adapter",
        _authority_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(implementation_module, "_normalize_dag_workspace_acl", _acl_ok)
    monkeypatch.setattr(
        implementation_module,
        "_dag_workspace_writeability_problems",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        implementation_module,
        "_enqueue_durable_merge_queue_for_results",
        _enqueue,
    )
    monkeypatch.setattr(
        implementation_module,
        "_drain_durable_merge_queue_for_feature",
        _drain,
    )
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_durable_merge_queue_group",
        _checkpoint,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _noop_refresh)
    runner = _runner(workspace_root, artifacts)
    runner.run = _unexpected_runtime
    artifacts.store["dag-task:TASK-contract"] = implementation_module.ImplementationResult(
        task_id="TASK-contract",
        summary="sandbox evidence captured",
        status="completed",
        files_modified=["app/src/main.py"],
        notes=(
            "patch_summary_ids=701\n"
            "canonical_mutation=pending_durable_merge_queue"
        ),
    ).model_dump_json()
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "complete"
    assert [result.task_id for result in queued_results] == ["TASK-contract"]
    assert "patch_summary_ids=701" in queued_results[0].notes


@pytest.mark.asyncio
async def test_completed_pending_merge_task_without_patch_ids_rehydrates_from_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    store = _BridgeExecutionControlStore()
    store.pending_merge_patch_evidence = {
        "dispatch_attempt_id": 88,
        "patch_summary_ids": [701],
        "structured_result_evidence_id": 301,
    }
    queued_results = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _authority_ok(*_args, **_kwargs):
        return implementation_module.WorkspaceAuthorityCompatibilityOutcome(
            approved=True,
            registry=_authority_registry(
                feature_root,
                [
                    _authority_repo(
                        feature_root,
                        "app",
                        "app",
                        writable_task_ids=["TASK-contract"],
                        task_ids=["TASK-contract"],
                    )
                ],
            ),
            snapshots=[],
        )

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _enqueue(_runner, _feature, pending_results, **_kwargs):
        queued_results.extend(pending_results)
        return [501]

    async def _drain(*_args, **_kwargs):
        return [SimpleNamespace(succeeded=True, item_id=501, terminal_status="integrated")]

    async def _checkpoint(*_args, **_kwargs):
        return SimpleNamespace(
            checkpointed=True,
            done_queue_item_ids=[501],
            result_commit="abc123",
            detail="",
            routed_failure={},
        )

    async def _noop_refresh(*_args, **_kwargs):
        return None

    class _UnexpectedRuntimeDispatcher:
        def __init__(self, **_kwargs):
            raise AssertionError(
                "stored pending-queue evidence must not redispatch runtime"
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_workspace_authority_pre_dispatch_adapter",
        _authority_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_enqueue_durable_merge_queue_for_results",
        _enqueue,
    )
    monkeypatch.setattr(
        implementation_module,
        "_drain_durable_merge_queue_for_feature",
        _drain,
    )
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_durable_merge_queue_group",
        _checkpoint,
    )
    monkeypatch.setattr(implementation_module, "enqueue_public_exhibit_refresh", _noop_refresh)
    monkeypatch.setattr(
        implementation_module,
        "RuntimeDispatcher",
        _UnexpectedRuntimeDispatcher,
    )
    runner = _runner(workspace_root, artifacts, execution_control_store=store)

    async def _unexpected_runtime(*_args, **_kwargs):
        raise AssertionError("rehydration must use store evidence, not runner.run")

    runner.run = _unexpected_runtime
    artifacts.store["dag-task:TASK-contract"] = implementation_module.ImplementationResult(
        task_id="TASK-contract",
        summary="sandbox evidence captured",
        status="completed",
        files_modified=["app/src/main.py"],
        notes="canonical_mutation=pending_durable_merge_queue",
    ).model_dump_json()
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "complete"
    assert len(store.pending_merge_patch_requests) == 1
    assert store.pending_merge_patch_requests[0]["feature_id"] == feature.id
    assert store.pending_merge_patch_requests[0]["group_idx"] == 0
    assert store.pending_merge_patch_requests[0]["task_id"] == "TASK-contract"
    assert [result.task_id for result in queued_results] == ["TASK-contract"]
    assert "dispatcher_attempt_id=88" in queued_results[0].notes
    assert "patch_summary_ids=701" in queued_results[0].notes


@pytest.mark.asyncio
async def test_completed_pending_merge_task_without_rehydratable_patch_ids_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add main"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    store = _BridgeExecutionControlStore()
    queued_results = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _authority_ok(*_args, **_kwargs):
        return implementation_module.WorkspaceAuthorityCompatibilityOutcome(
            approved=True,
            registry=_authority_registry(
                feature_root,
                [
                    _authority_repo(
                        feature_root,
                        "app",
                        "app",
                        writable_task_ids=["TASK-contract"],
                        task_ids=["TASK-contract"],
                    )
                ],
            ),
            snapshots=[],
        )

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _enqueue(_runner, _feature, pending_results, **_kwargs):
        queued_results.extend(pending_results)
        return [501]

    class _UnexpectedRuntimeDispatcher:
        def __init__(self, **_kwargs):
            raise AssertionError(
                "missing stored patch evidence must fail closed without redispatch"
            )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_workspace_authority_pre_dispatch_adapter",
        _authority_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_enqueue_durable_merge_queue_for_results",
        _enqueue,
    )
    monkeypatch.setattr(
        implementation_module,
        "RuntimeDispatcher",
        _UnexpectedRuntimeDispatcher,
    )
    runner = _runner(workspace_root, artifacts, execution_control_store=store)
    artifacts.store["dag-task:TASK-contract"] = implementation_module.ImplementationResult(
        task_id="TASK-contract",
        summary="sandbox evidence captured",
        status="completed",
        files_modified=["app/src/main.py"],
        notes="canonical_mutation=pending_durable_merge_queue",
    ).model_dump_json()
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="modify")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "missing_patch_evidence" in outcome.failure
    assert "patch_summary_ids" in outcome.failure
    assert len(store.pending_merge_patch_requests) == 1
    assert queued_results == []


@pytest.mark.asyncio
async def test_live_dag_dispatch_blocks_contract_compile_defect_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_run(*_args, **_kwargs):
        raise AssertionError("implementer runtime must not start after contract compile failure")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    runner = _runner(workspace_root, artifacts)
    runner.run = _unexpected_run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/main.py", action="delete")],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert "Task deliverable contract compilation failed" in outcome.failure
    failure = json.loads(artifacts.store["dag-task-contract:compile-failure:g0"])
    assert failure["failure_class"] == "contract_compile"
    assert failure["failure_type"] == "contract_missing_deletion_evidence"


@pytest.mark.asyncio
async def test_live_dag_dispatch_blocks_read_only_contract_violation_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "main.py").write_text("value = 'base'\n", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "reference.md").write_text("base docs\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add contract targets"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    commit_called = False

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_commit(*_args, **_kwargs):
        nonlocal commit_called
        commit_called = True
        raise AssertionError("commit must wait for contract verdict approval")

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        _write_sandbox_file(ask, "docs/reference.md", "changed docs\n")
        return implementation_module.ImplementationResult(
            task_id="TASK-contract",
            summary="changed read-only docs",
            status="completed",
            files_modified=["app/docs/reference.md"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["docs/reference.md"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _unexpected_commit)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-contract",
                name="contract",
                description="contract",
                repo_path="app",
                file_scope=[
                    TaskFileScope(path="app/src/main.py", action="modify"),
                    TaskFileScope(path="app/docs/reference.md", action="read_only"),
                ],
            )
        ],
        execution_order=[["TASK-contract"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert commit_called is False
    assert "Task contract validation failed before sandbox promotion" in outcome.failure
    assert "read_only_path_touched" in outcome.failure
    verdict = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:TASK-contract:canonical-precommit-g0-implementation-repo-",
    )])
    assert verdict["approved"] is False
    assert "read_only_path_touched" in verdict["violation_codes"]


@pytest.mark.asyncio
async def test_live_dag_dispatch_validates_each_contract_not_repo_union(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "a.py").write_text("a = 'base'\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("b = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add contract targets"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    commit_called = False

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _unexpected_commit(*_args, **_kwargs):
        nonlocal commit_called
        commit_called = True
        raise AssertionError("commit must wait for per-contract verdicts")

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        if "Contract ID" not in ask.prompt:
            raise AssertionError("contract prompt missing")
        task_id = "TASK-b" if "Task B" in ask.prompt else "TASK-a"
        _write_sandbox_file(ask, "src/a.py", f"a = '{task_id}'\n")
        return implementation_module.ImplementationResult(
            task_id=task_id,
            summary="done",
            status="completed",
            files_modified=(
                ["app/src/a.py"] if task_id == "TASK-b" else ["app/src/a.py"]
            ),
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/a.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _unexpected_commit)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-a",
                name="Task A",
                description="Task A",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/a.py", action="modify")],
            ),
            ImplementationTask(
                id="TASK-b",
                name="Task B",
                description="Task B",
                repo_path="app",
                file_scope=[TaskFileScope(path="app/src/b.py", action="modify")],
            ),
        ],
        execution_order=[["TASK-a", "TASK-b"]],
        complete=True,
    )

    outcome = await _implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "workflow_blocked"
    assert commit_called is False
    assert "TASK-b" in outcome.failure
    assert "modify_outside_allowed_paths" in outcome.failure


@pytest.mark.asyncio
async def test_contract_guard_blocks_dirty_uncontracted_sibling_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    app = _repo(feature_root, "app")
    lib = _repo(feature_root, "lib")
    artifacts = _Artifacts()
    feature = _feature()

    contract = implementation_module.CompiledTaskDeliverableContract(
        id=101,
        feature_id=feature.id,
        dag_sha256="dag-sha",
        source_dag_artifact_id=0,
        group_idx=0,
        task_id="TASK-contract",
        repo_id="app",
        repo_path="app",
        source_dag_sha256="source",
        required_paths=[],
        allowed_paths=[
            {
                "repo_id": "app",
                "path": "src/main.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            }
        ],
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy={
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        non_goals=[],
        dependency_task_ids=[],
        compile_warnings=[],
        unknown_write_set=False,
        normalized_contract_json={},
        contract_digest="digest",
        status="active",
        idempotency_key="contract",
    )
    result = implementation_module.ImplementationResult(
        task_id="TASK-contract",
        summary="done",
        status="completed",
        files_modified=["app/src/main.py"],
    )

    def _repo_roots(_root: Path):
        return [app, lib]

    async def _git_evidence(repo: Path):
        if repo.name == "lib":
            return True, "b" * 64, [], ["src/lib.py"], [], {}, " M src/lib.py\n", ""
        return True, "a" * 64, [], ["src/main.py"], [], {}, " M src/main.py\n", ""

    monkeypatch.setattr(implementation_module, "_discover_repo_roots_under", _repo_roots)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _git_evidence)

    outcome = await implementation_module._record_precommit_contract_verdicts(
        _runner(workspace_root, artifacts),
        feature,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        feature_root=feature_root,
        contracts_by_task_id={"TASK-contract": contract},
        results=[result],
    )

    assert outcome.approved is False
    assert "uncontracted_dirty_repo" in outcome.violation_codes
    assert "lib" in outcome.failure


@pytest.mark.asyncio
async def test_contract_guard_rejects_reported_paths_without_patch_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    app = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    contract = implementation_module.CompiledTaskDeliverableContract(
        id=102,
        feature_id=feature.id,
        dag_sha256="dag-sha",
        source_dag_artifact_id=0,
        group_idx=0,
        task_id="TASK-claimed",
        repo_id="app",
        repo_path="app",
        source_dag_sha256="source",
        required_paths=[],
        allowed_paths=[
            {
                "repo_id": "app",
                "path": "src/main.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            }
        ],
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy={
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        non_goals=[],
        dependency_task_ids=[],
        compile_warnings=[],
        unknown_write_set=False,
        normalized_contract_json={},
        contract_digest="digest",
        status="active",
        idempotency_key="contract",
    )
    result = implementation_module.ImplementationResult(
        task_id="TASK-claimed",
        summary="claimed",
        status="completed",
        files_modified=["app/src/main.py"],
    )

    def _repo_roots(_root: Path):
        return [app]

    async def _clean_git_evidence(_repo: Path):
        return (
            True,
            implementation_module.hashlib.sha256(b"").hexdigest(),
            [],
            [],
            [],
            {},
            "",
            "",
        )

    monkeypatch.setattr(implementation_module, "_discover_repo_roots_under", _repo_roots)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _clean_git_evidence)

    outcome = await implementation_module._record_precommit_contract_verdicts(
        _runner(workspace_root, artifacts),
        feature,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        feature_root=feature_root,
        contracts_by_task_id={"TASK-claimed": contract},
        results=[result],
    )

    assert outcome.approved is False
    assert "missing_patch_evidence" in outcome.violation_codes
    assert "reported paths have no git patch evidence" in outcome.failure
    assert not any(key.startswith("dag-sandbox-patch:") for key in artifacts.store)


@pytest.mark.asyncio
async def test_contract_guard_uses_multi_segment_repo_identity_for_patch_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    nested_repo = _repo(feature_root, "services/newsvc")
    artifacts = _Artifacts()
    feature = _feature()
    contract = implementation_module.CompiledTaskDeliverableContract(
        id=112,
        feature_id=feature.id,
        dag_sha256="dag-sha",
        source_dag_artifact_id=0,
        group_idx=0,
        task_id="TASK-nested",
        repo_id="services/newsvc",
        repo_path="services/newsvc",
        source_dag_sha256="source",
        required_paths=[],
        allowed_paths=[
            {
                "repo_id": "services/newsvc",
                "path": "src/main.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            }
        ],
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy={
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        non_goals=[],
        dependency_task_ids=[],
        compile_warnings=[],
        unknown_write_set=False,
        normalized_contract_json={},
        contract_digest="digest",
        status="active",
        idempotency_key="contract",
    )
    result = implementation_module.ImplementationResult(
        task_id="TASK-nested",
        summary="claimed nested repo patch",
        status="completed",
        files_modified=["services/newsvc/src/main.py"],
    )

    def _repo_roots(_root: Path):
        return [nested_repo]

    async def _git_evidence(_repo: Path):
        status_text = " M src/main.py\n"
        return (
            True,
            implementation_module.hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
            [],
            ["src/main.py"],
            [],
            {},
            status_text,
            "",
        )

    monkeypatch.setattr(implementation_module, "_discover_repo_roots_under", _repo_roots)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _git_evidence)

    outcome = await implementation_module._record_precommit_contract_verdicts(
        _runner(workspace_root, artifacts),
        feature,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        feature_root=feature_root,
        contracts_by_task_id={"TASK-nested": contract},
        results=[result],
    )

    assert outcome.approved is True
    assert "missing_patch_evidence" not in outcome.violation_codes
    assert "reported_path_without_patch_evidence" not in outcome.violation_codes


@pytest.mark.asyncio
async def test_contract_guard_does_not_record_approved_verdict_for_partial_patch_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    app = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    contract = implementation_module.CompiledTaskDeliverableContract(
        id=103,
        feature_id=feature.id,
        dag_sha256="dag-sha",
        source_dag_artifact_id=0,
        group_idx=0,
        task_id="TASK-partial",
        repo_id="app",
        repo_path="app",
        source_dag_sha256="source",
        required_paths=[],
        allowed_paths=[
            {
                "repo_id": "app",
                "path": "src/main.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            },
            {
                "repo_id": "app",
                "path": "src/ghost.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            },
        ],
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy={
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        non_goals=[],
        dependency_task_ids=[],
        compile_warnings=[],
        unknown_write_set=False,
        normalized_contract_json={},
        contract_digest="digest",
        status="active",
        idempotency_key="contract",
    )
    result = implementation_module.ImplementationResult(
        task_id="TASK-partial",
        summary="claimed partial",
        status="completed",
        files_modified=["app/src/main.py", "app/src/ghost.py"],
    )

    def _repo_roots(_root: Path):
        return [app]

    async def _partial_git_evidence(_repo: Path):
        status_text = " M src/main.py\n"
        return (
            True,
            implementation_module.hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
            [],
            ["src/main.py"],
            [],
            {},
            status_text,
            "",
        )

    monkeypatch.setattr(implementation_module, "_discover_repo_roots_under", _repo_roots)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _partial_git_evidence)

    outcome = await implementation_module._record_precommit_contract_verdicts(
        _runner(workspace_root, artifacts),
        feature,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        feature_root=feature_root,
        contracts_by_task_id={"TASK-partial": contract},
        results=[result],
    )

    assert outcome.approved is False
    assert "reported_path_without_patch_evidence" in outcome.violation_codes
    assert "src/ghost.py" in outcome.failure
    assert not any(key.startswith("dag-contract-verdict:") for key in artifacts.store)


@pytest.mark.asyncio
async def test_contract_guard_binds_patch_summary_to_workspace_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    app = _repo(feature_root, "app")
    artifacts = _Artifacts()
    feature = _feature()
    store = _BridgeExecutionControlStore()
    contract = implementation_module.CompiledTaskDeliverableContract(
        id=104,
        feature_id=feature.id,
        dag_sha256="dag-sha",
        source_dag_artifact_id=0,
        group_idx=0,
        task_id="TASK-snapshot",
        repo_id="app",
        repo_path="app",
        source_dag_sha256="source",
        required_paths=[],
        allowed_paths=[
            {
                "repo_id": "app",
                "path": "src/main.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            }
        ],
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy={
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        non_goals=[],
        dependency_task_ids=[],
        compile_warnings=[],
        unknown_write_set=False,
        normalized_contract_json={},
        contract_digest="digest",
        status="active",
        idempotency_key="contract",
    )
    result = implementation_module.ImplementationResult(
        task_id="TASK-snapshot",
        summary="claimed",
        status="completed",
        files_modified=["app/src/main.py"],
    )

    def _repo_roots(_root: Path):
        return [app]

    async def _git_evidence(_repo: Path):
        status_text = " M src/main.py\n"
        return (
            True,
            implementation_module.hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
            [],
            ["src/main.py"],
            [],
            {},
            status_text,
            "",
        )

    monkeypatch.setattr(implementation_module, "_discover_repo_roots_under", _repo_roots)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _git_evidence)
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = store

    outcome = await implementation_module._record_precommit_contract_verdicts(
        runner,
        feature,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        feature_root=feature_root,
        contracts_by_task_id={"TASK-snapshot": contract},
        results=[result],
        workspace_snapshots=[SimpleNamespace(repo_id="app", id=201)],
    )

    assert outcome.approved is True
    assert store.patch_summaries
    patch_summary = store.patch_summaries[0]
    assert patch_summary.metadata["workspace_snapshot_id"] == 201
    assert patch_summary.metadata["base_snapshot_id"] == 201
    assert patch_summary.metadata["base_snapshot_ids"] == [201]
    assert patch_summary.payload["workspace_snapshot_id"] == 201
    assert store.contract_verdicts[0].metadata["workspace_snapshot_id"] == 201


@pytest.mark.asyncio
async def test_contract_guard_refreshes_patch_lineage_before_approved_verdict_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    app = _repo(feature_root, "app")
    artifacts = _Artifacts()
    store = _BridgeExecutionControlStore()
    feature = _feature()
    contract = implementation_module.CompiledTaskDeliverableContract(
        id=104,
        feature_id=feature.id,
        dag_sha256="dag-sha",
        source_dag_artifact_id=0,
        group_idx=0,
        task_id="TASK-stale",
        repo_id="app",
        repo_path="app",
        source_dag_sha256="source",
        required_paths=[],
        allowed_paths=[
            {
                "repo_id": "app",
                "path": "src/main.py",
                "match_kind": "file",
                "intent": "modify",
                "required": False,
                "allow_create": False,
                "allow_modify": True,
                "allow_delete": False,
                "source": "test",
            }
        ],
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy={
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        non_goals=[],
        dependency_task_ids=[],
        compile_warnings=[],
        unknown_write_set=False,
        normalized_contract_json={},
        contract_digest="digest",
        status="active",
        idempotency_key="contract",
    )
    stale_key = implementation_module._contract_verdict_projection_key(
        contract,
        group_idx=0,
        stage="implementation",
    )
    artifacts.store[stale_key] = json.dumps(
        {
            "artifact_schema": "dag-contract-verdict-compatibility-v1",
            "approved": True,
            "contract_id": 104,
            "contract_digest": implementation_module._task_contract_digest(contract),
            "patch_summary_id": 999,
        },
        sort_keys=True,
    )
    result = implementation_module.ImplementationResult(
        task_id="TASK-stale",
        summary="claimed",
        status="completed",
        files_modified=["app/src/main.py"],
    )

    def _repo_roots(_root: Path):
        return [app]

    async def _git_evidence(_repo: Path):
        status_text = " M src/main.py\n"
        return (
            True,
            implementation_module.hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
            [],
            ["src/main.py"],
            [],
            {},
            status_text,
            "",
        )

    monkeypatch.setattr(implementation_module, "_discover_repo_roots_under", _repo_roots)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _git_evidence)
    runner = _runner(workspace_root, artifacts)
    runner.services["execution_control_store"] = store

    outcome = await implementation_module._record_precommit_contract_verdicts(
        runner,
        feature,
        dag_sha256="dag-sha",
        group_idx=0,
        stage="implementation",
        feature_root=feature_root,
        contracts_by_task_id={"TASK-stale": contract},
        results=[result],
        workspace_snapshots=[SimpleNamespace(repo_id="app", id=301)],
    )

    assert outcome.approved is True
    assert store.patch_summaries
    assert store.patch_summaries[0].metadata["workspace_snapshot_id"] == 301
    assert store.contract_verdicts, "stale projection must not skip fresh verdict"
    assert store.contract_verdicts[0].patch_summary_id != 999
    assert store.contract_verdicts[0].metadata["workspace_snapshot_id"] == 301


@pytest.mark.asyncio
async def test_enhancement_group_uses_contract_prompt_resume_and_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    repo = _repo(feature_root, "app")
    (repo / "src" / "harden.py").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add harden target"], cwd=repo, check=True)
    artifacts = _Artifacts()
    feature = _feature()
    prompts: list[str] = []

    backlog = implementation_module.EnhancementBacklog(
        items=[
            implementation_module.EnhancementItem(
                source="review",
                severity="minor",
                description="Tighten hardening path",
                file="app/src/harden.py",
            )
        ]
    )
    decomposition = implementation_module.EnhancementDecomposition(
        tasks=[
            {"repo_path": "app", "item_indices": [0], "summary": "hardening"}
        ],
        already_resolved=[],
    )
    artifacts.store["enhancement-backlog"] = backlog.model_dump_json()
    artifacts.store["enhancement-decomposition"] = decomposition.model_dump_json()

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _commit_noop(*_args, **_kwargs):
        return ""

    async def _verify_ok(*_args, **_kwargs):
        return True, ""

    async def _run(ask, *_args, **_kwargs):
        prompts.append(ask.prompt)
        metadata = ask.actor.role.metadata
        sandbox_cwd = Path(metadata["workspace_override"])
        assert metadata["sandbox_required"] is True
        assert metadata["runtime_workspace_binding"]["cwd"] == str(sandbox_cwd)
        assert metadata["runtime_workspace_binding"]["workspace_override"] == str(sandbox_cwd)
        assert sandbox_cwd != feature_root / "app"
        (sandbox_cwd / "src").mkdir(exist_ok=True)
        (sandbox_cwd / "src" / "harden.py").write_text("hardened\n", encoding="utf-8")
        return implementation_module.ImplementationResult(
            task_id="enhancement-app",
            summary="hardened",
            status="completed",
            files_modified=["app/src/harden.py"],
        )

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    _patch_git_evidence(monkeypatch, modified=["src/harden.py"])
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_noop)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_ok)
    runner = _runner(workspace_root, artifacts)
    runner.run = _run
    dag = implementation_module.ImplementationDAG(tasks=[], execution_order=[], complete=True)

    failure = await implementation_module._run_enhancement_group(
        runner,
        feature,
        dag,
        [],
        implementation_module.HandoverDoc(),
    )

    assert "SANDBOX_WORKFLOW_BLOCKER" in failure
    assert "durable merge queue" in failure
    assert "canonical mutation" in failure
    assert prompts
    assert "## Deliverable Contract" in prompts[0]
    assert "dag-task-contract:enhancement-app" in artifacts.store
    _artifact_key_with_prefix(artifacts, "dag-sandbox-patch:g0:attempt-0:repo-")
    verdict = json.loads(artifacts.store[_artifact_key_with_prefix(
        artifacts,
        "dag-contract-verdict:g0:enhancement-app:canonical-precommit-g0-enhancement-implementation-repo-",
    )])
    assert verdict["approved"] is True


@pytest.mark.asyncio
async def test_enhancement_partial_resume_uses_original_task_index_for_sandbox_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    _repo(feature_root, "app")
    _repo(feature_root, "lib")
    artifacts = _Artifacts()
    feature = _feature()
    backlog = implementation_module.EnhancementBacklog(
        items=[
            implementation_module.EnhancementItem(
                source="review",
                severity="minor",
                description="Tighten app",
                file="app/src/app.py",
            ),
            implementation_module.EnhancementItem(
                source="review",
                severity="minor",
                description="Tighten lib",
                file="lib/src/lib.py",
            ),
        ]
    )
    decomposition = implementation_module.EnhancementDecomposition(
        tasks=[
            {"repo_path": "app", "item_indices": [0], "summary": "app"},
            {"repo_path": "lib", "item_indices": [1], "summary": "lib"},
        ],
        already_resolved=[],
    )
    artifacts.store["enhancement-backlog"] = backlog.model_dump_json()
    artifacts.store["enhancement-decomposition"] = decomposition.model_dump_json()
    artifacts.store["dag-task:enhancement-app"] = implementation_module.ImplementationResult(
        task_id="enhancement-app",
        summary="already done",
        status="completed",
        files_modified=["app/src/app.py"],
    ).model_dump_json()
    artifacts.store["dag-group:0"] = json.dumps(
        {
            "group_idx": 0,
            "task_ids": ["enhancement-app"],
            "results": [],
            "verdict": "approved",
            "commit_hash": "stale-enhancement-commit",
        },
        sort_keys=True,
    )
    recorded: list[tuple[str, int]] = []

    async def _noop_worktrees(*_args, **_kwargs) -> None:
        return None

    async def _alias_guard_ok(*_args, **_kwargs):
        return True, {"blockers": []}

    async def _projection_matches(*_args, **_kwargs):
        return True

    async def _approved_verdict_exists(*_args, **_kwargs):
        return True

    async def _checkpoint_fresh(*_args, **_kwargs):
        return False

    async def _bind_sandbox(*_args, **kwargs):
        task = kwargs["task"]
        recorded.append((task.id, kwargs["task_idx"]))
        raise implementation_module.SandboxWorkflowBlocker("simulated enhancement lease recovery")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_worktrees)
    monkeypatch.setattr(
        implementation_module,
        "_run_worktree_alias_pre_dispatch_guard",
        _alias_guard_ok,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_projection_matches",
        _projection_matches,
    )
    monkeypatch.setattr(
        implementation_module,
        "_task_contract_approved_verdict_exists",
        _approved_verdict_exists,
    )
    monkeypatch.setattr(implementation_module, "_dag_group_checkpoint_is_fresh", _checkpoint_fresh)
    monkeypatch.setattr(implementation_module, "_bind_task_sandbox", _bind_sandbox)
    runner = _runner(workspace_root, artifacts)
    dag = implementation_module.ImplementationDAG(tasks=[], execution_order=[], complete=True)

    failure = await implementation_module._run_enhancement_group(
        runner,
        feature,
        dag,
        [],
        implementation_module.HandoverDoc(),
    )

    assert "simulated enhancement lease recovery" in failure
    assert ("enhancement-app", 0) in recorded
    assert ("enhancement-lib", 1) in recorded


@pytest.mark.asyncio
async def test_prior_attempt_context_offload_avoids_canonical_feature_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root, feature_root = _workspace(tmp_path)
    artifacts = _Artifacts()
    feature = _feature()
    runner = _runner(workspace_root, artifacts)
    captured_contexts: list[str] = []

    async def _single_rca_fix_verify(*args, **kwargs):
        del kwargs
        captured_contexts.append(args[6])
        return implementation_module.BugFixAttempt(
            bug_id="BUG-1",
            source_verdict="review",
            description="fixed",
            root_cause="root",
            fix_applied="patch",
            files_modified=[],
            re_verify_result="PASS",
        )

    monkeypatch.setitem(
        implementation_module._offload_if_large.__globals__,
        "PROMPT_FILE_THRESHOLD",
        1,
    )
    monkeypatch.setattr(
        implementation_module,
        "_single_rca_fix_verify",
        _single_rca_fix_verify,
    )
    prior_attempts = [
        implementation_module.BugFixAttempt(
            bug_id="BUG-OLD",
            source_verdict="review",
            description="old failure " * 20,
            root_cause="old cause " * 20,
            fix_applied="old fix " * 20,
            files_modified=["app/src/main.py"],
            re_verify_result="FAIL",
        )
    ]

    attempts = await implementation_module._diagnose_and_fix(
        runner,
        feature,
        "still failing",
        "review",
        implementation_module.reviewer,
        implementation_module.implementer,
        prior_attempts,
        implementation_module.itertools.count(1),
    )

    assert attempts[0].re_verify_result == "PASS"
    assert captured_contexts
    assert "prior-fix-attempts.md" in captured_contexts[0]
    context_path = Path(captured_contexts[0].split("`")[1])
    assert context_path.exists()
    assert feature_root.resolve(strict=False) not in [
        context_path.resolve(strict=False),
        *context_path.resolve(strict=False).parents,
    ]
    assert not (feature_root / ".iriai-context").exists()
