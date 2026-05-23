from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask, TaskAcceptanceCriterion, TaskFileScope
from iriai_build_v2.workflows.develop.execution.task_contracts import (
    ContractCompileError,
    ContractCompileRequest,
    ContractCompiler,
    ContractGroupCompileRequest,
    GateCommandSpec,
    PatchSummary,
)
from iriai_build_v2.workflows.develop.execution.workspace_authority import (
    CanonicalRepoRegistry,
    RepoIdentity,
    WorkspaceSnapshot,
)


FEATURE_ID = "feature-slice-03"
DAG_SHA = "dag-sha-03"
SOURCE_DAG_SHA = "source-dag-sha-03"


def _repo_identity(feature_root: Path, name: str = "app", repo_id: str = "repo-app") -> RepoIdentity:
    repo = feature_root / name
    repo.mkdir(parents=True, exist_ok=True)
    return RepoIdentity(
        repo_id=repo_id,
        repo_name=name,
        role="primary",
        workspace_relative_path=name,
        canonical_path=str(repo),
        identity_kind="source_path",
        identity_value=str(repo),
        safety_status="ok",
        identity_evidence_digest=f"identity:{repo_id}",
    )


def _registry(
    tmp_path: Path,
    *,
    aliases: dict[str, str] | None = None,
    repos: list[RepoIdentity] | None = None,
) -> tuple[CanonicalRepoRegistry, Path]:
    feature_root = tmp_path / "workspace" / ".iriai" / "features" / "slice-03" / "repos"
    feature_root.mkdir(parents=True, exist_ok=True)
    registry = CanonicalRepoRegistry(
        feature_id=FEATURE_ID,
        feature_slug="slice-03",
        feature_root=str(feature_root),
        repos=repos or [_repo_identity(feature_root)],
        aliases=aliases or {},
        registry_digest="registry:digest",
    )
    return registry, feature_root


def _request(
    registry: CanonicalRepoRegistry,
    task,
    **overrides,
) -> ContractCompileRequest:
    data = {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA,
        "source_dag_artifact_id": 42,
        "source_dag_sha256": SOURCE_DAG_SHA,
        "group_idx": 3,
        "task": task,
        "all_task_ids": ["TASK-0", getattr(task, "id", task.id if isinstance(task, dict) else "")],
        "workspace_registry": registry,
    }
    data.update(overrides)
    return ContractCompileRequest(**data)


def _task(
    *,
    task_id: str = "TASK-1",
    repo_path: str = "app",
    file_scope: list | None = None,
    files: list[str] | None = None,
    dependencies: list[str] | None = None,
    acceptance_criteria: list | None = None,
    verification_gates: list[str] | None = None,
    **extra,
):
    return SimpleNamespace(
        id=task_id,
        name="Initial name",
        description="Implement the scoped deliverable.",
        repo_path=repo_path,
        file_scope=file_scope if file_scope is not None else [],
        files=files if files is not None else [],
        dependencies=dependencies if dependencies is not None else [],
        acceptance_criteria=acceptance_criteria if acceptance_criteria is not None else [],
        counterexamples=extra.pop("counterexamples", []),
        security_concerns=extra.pop("security_concerns", []),
        non_goals=extra.pop("non_goals", []),
        verification_gates=verification_gates if verification_gates is not None else [],
        **extra,
    )


def _scope(path: str, action: str, **extra):
    return SimpleNamespace(path=path, action=action, **extra)


def _snapshot(repo: RepoIdentity, *, present_paths: list[str] | None = None) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA,
        group_idx=3,
        repo_id=repo.repo_id,
        canonical_path=repo.canonical_path,
        workspace_relative_path=repo.workspace_relative_path,
        case_sensitivity="case_sensitive",
        present_paths=present_paths or [],
    )


def test_compile_task_preserves_scope_acceptance_gates_and_stable_digest(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    task = _task(
        file_scope=[
            _scope("app/src/new.py", "create"),
            _scope("src/existing.py", "modify"),
            _scope("docs/reference.md", "read_only"),
        ],
        files=["app/src/new.py"],
        dependencies=["TASK-0"],
        acceptance_criteria=[
            SimpleNamespace(
                id="AC Fancy",
                description="Create the new module and update the existing one.",
                not_criteria="Do not rewrite the reference docs.",
            )
        ],
        counterexamples=["No unrelated package rewrites."],
        security_concerns=["Validate user-controlled paths."],
        non_goals=["No database migration."],
        verification_gates=["AC Fancy"],
    )

    contract = ContractCompiler().compile_task(
        _request(
            registry,
            task,
            manifest_expected_files=[
                {"path": "app/generated/out.json", "task_id": "TASK-1", "source": "TASK-1 manifest", "generated": True}
            ],
            generated_outputs=[
                {"path": "reports/summary.json", "source_path": "src/new.py", "criterion_ids": ["AC Fancy"]}
            ],
        )
    )

    assert contract.task_id == "TASK-1"
    assert contract.source_dag_artifact_id == 42
    assert contract.source_dag_sha256 == SOURCE_DAG_SHA
    assert contract.group_idx == 3
    assert contract.dependency_task_ids == ["TASK-0"]
    assert contract.repo_id == "repo-app"
    assert contract.repo_path == "app"
    assert {rule.path for rule in contract.required_paths} == {
        "generated/out.json",
        "src/existing.py",
        "src/new.py",
    }
    assert {rule.path for rule in contract.allowed_paths} == {
        "reports/summary.json",
        "src/existing.py",
        "src/new.py",
    }
    assert contract.read_only_paths[0].path == "docs/reference.md"
    assert {criterion.id for criterion in contract.acceptance_criteria} >= {"ac-fancy"}
    assert any(criterion.source_field == "not_criteria" for criterion in contract.acceptance_criteria)
    assert any(criterion.source_field == "counterexamples" for criterion in contract.acceptance_criteria)
    assert any(criterion.source_field == "security_concerns" for criterion in contract.acceptance_criteria)
    assert any(gate.gate_kind == "model_verifier" for gate in contract.verification_gates)
    assert any(
        evidence.path == "reports/summary.json" and evidence.kind == "path_exists"
        for gate in contract.verification_gates
        for evidence in gate.required_evidence
    )
    assert contract.execution_policy.write_set_mode == "declared"
    assert contract.execution_policy.sandbox_isolation == "group_shared"
    assert contract.execution_policy.merge_admission == "atomic_group"

    renamed_task = SimpleNamespace(**{**task.__dict__, "name": "Changed display name"})
    renamed_contract = ContractCompiler().compile_task(
        _request(
            registry,
            renamed_task,
            manifest_expected_files=[
                {"path": "app/generated/out.json", "task_id": "TASK-1", "source": "TASK-1 manifest", "generated": True}
            ],
            generated_outputs=[
                {"path": "reports/summary.json", "source_path": "src/new.py", "criterion_ids": ["AC Fancy"]}
            ],
        )
    )
    assert renamed_contract.contract_digest == contract.contract_digest

    changed_description = SimpleNamespace(**{**task.__dict__, "description": "Different material requirement."})
    changed_contract = ContractCompiler().compile_task(_request(registry, changed_description))
    assert changed_contract.contract_digest != contract.contract_digest


def test_acceptance_without_id_gets_deterministic_source_ordinal_id(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    task = ImplementationTask(
        id="TASK-1",
        name="task",
        description="task",
        repo_path="app",
        file_scope=[TaskFileScope(path="src/app.py", action="modify")],
        acceptance_criteria=[
            TaskAcceptanceCriterion(description="Existing criterion text."),
        ],
    )

    first = ContractCompiler().compile_task(_request(registry, task))
    second = ContractCompiler().compile_task(_request(registry, task))

    assert first.acceptance_criteria[0].id.startswith("ac-0-")
    assert first.acceptance_criteria[0].id == second.acceptance_criteria[0].id
    assert first.contract_digest == second.contract_digest


@pytest.mark.parametrize(
    ("path", "failure_type"),
    [
        ("/tmp/outside.py", "contract_invalid_path"),
        ("../outside.py", "contract_invalid_path"),
        ("app/../outside.py", "contract_invalid_path"),
    ],
)
def test_path_normalization_rejects_absolute_and_traversal_paths(
    tmp_path: Path,
    path: str,
    failure_type: str,
) -> None:
    registry, _feature_root = _registry(tmp_path)
    task = _task(file_scope=[_scope(path, "modify")])

    with pytest.raises(ContractCompileError) as exc_info:
        ContractCompiler().compile_task(_request(registry, task))

    assert exc_info.value.failure_type == failure_type


def test_path_normalization_rejects_aliases_and_symlink_escapes(tmp_path: Path) -> None:
    registry, feature_root = _registry(tmp_path)
    repo = registry.repos[0]
    alias_path = feature_root / "alias-app"
    registry.aliases[str(alias_path)] = str(Path(repo.canonical_path))
    alias_task = _task(file_scope=[_scope("alias-app/src/file.py", "modify")])

    with pytest.raises(ContractCompileError) as alias_error:
        ContractCompiler().compile_task(_request(registry, alias_task))
    assert alias_error.value.failure_type == "contract_invalid_path"
    assert alias_error.value.failure_class == "worktree_alias"
    assert alias_error.value.route == "run_canonicalization_repair"
    assert alias_error.value.violations[0]["failure_class"] == "worktree_alias"
    assert alias_error.value.violations[0]["route"] == "run_canonicalization_repair"

    alias_repo_task = _task(repo_path="alias-app", file_scope=[_scope("src/file.py", "modify")])
    with pytest.raises(ContractCompileError) as alias_repo_error:
        ContractCompiler().compile_task(_request(registry, alias_repo_task))
    assert alias_repo_error.value.failure_type == "contract_invalid_path"
    assert alias_repo_error.value.failure_class == "worktree_alias"
    assert alias_repo_error.value.route == "run_canonicalization_repair"

    absolute_alias_task = _task(file_scope=[_scope(str(alias_path / "src/file.py"), "modify")])
    with pytest.raises(ContractCompileError) as absolute_alias_error:
        ContractCompiler().compile_task(_request(registry, absolute_alias_task))
    assert absolute_alias_error.value.failure_type == "contract_invalid_path"
    assert absolute_alias_error.value.failure_class == "worktree_alias"
    assert absolute_alias_error.value.route == "run_canonicalization_repair"

    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(repo.canonical_path) / "escape").symlink_to(outside)
    symlink_task = _task(file_scope=[_scope("escape/secret.txt", "modify")])

    with pytest.raises(ContractCompileError) as symlink_error:
        ContractCompiler().compile_task(_request(registry, symlink_task))
    assert symlink_error.value.failure_type == "contract_invalid_path"


def test_legacy_files_can_fill_empty_scope_but_cannot_widen_declared_scope(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    legacy_only = _task(files=["src/legacy.py"])
    contract = ContractCompiler().compile_task(_request(registry, legacy_only))

    assert contract.unknown_write_set is False
    assert contract.required_paths == []
    assert contract.allowed_paths[0].path == "src/legacy.py"
    assert contract.allowed_paths[0].allow_create is True
    assert contract.allowed_paths[0].allow_modify is True

    widening = _task(
        file_scope=[_scope("src/a.py", "modify")],
        files=["src/b.py"],
        acceptance_criteria=[SimpleNamespace(description="Modify a only.")],
    )
    with pytest.raises(ContractCompileError) as exc_info:
        ContractCompiler().compile_task(_request(registry, widening))
    assert exc_info.value.failure_type == "contract_scope_conflict"
    assert "widens" in exc_info.value.warnings[0]


def test_gate_specs_reject_unknown_criteria_shell_strings_and_bad_command_repos(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    task = _task(
        file_scope=[_scope("src/app.py", "modify")],
        acceptance_criteria=[SimpleNamespace(id="AC-1", description="Pass the criterion.")],
    )

    with pytest.raises(ContractCompileError) as unknown:
        ContractCompiler().compile_task(
            _request(
                registry,
                task,
                verification_gates=[
                    {
                        "id": "gate:unknown",
                        "gate_kind": "model_verifier",
                        "name": "Unknown",
                        "source": "task_verification",
                        "criterion_ids": ["missing"],
                    }
                ],
            )
        )
    assert unknown.value.failure_type == "contract_unknown_criterion"

    with pytest.raises(ValueError):
        GateCommandSpec(
            id="cmd",
            command="pytest",
            cwd_repo_id="repo-app",
            timeout_seconds=30,
        )

    with pytest.raises(ContractCompileError) as shell_command:
        ContractCompiler().compile_task(
            _request(
                registry,
                task,
                verification_gates=[
                    {
                        "id": "gate:shell",
                        "gate_kind": "command",
                        "name": "Bad shell",
                        "source": "task_verification",
                        "criterion_ids": ["AC-1"],
                        "command": {
                            "id": "cmd",
                            "command": "pytest",
                            "cwd_repo_id": "repo-app",
                            "timeout_seconds": 30,
                        },
                    }
                ],
            )
        )
    assert shell_command.value.failure_type == "contract_invalid_gate"

    with pytest.raises(ContractCompileError) as bad_repo:
        ContractCompiler().compile_task(
            _request(
                registry,
                task,
                verification_gates=[
                    {
                        "id": "gate:cmd",
                        "gate_kind": "command",
                        "name": "Run command",
                        "source": "task_verification",
                        "criterion_ids": ["AC-1"],
                        "command": {
                            "id": "cmd",
                            "command": ["pytest"],
                            "cwd_repo_id": "missing-repo",
                            "timeout_seconds": 30,
                        },
                    }
                ],
            )
        )
    assert bad_repo.value.failure_type == "contract_invalid_path"


def test_manifest_forbidden_overrides_scope_and_directory_rules_match_descendants(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    forbidden_scope = _task(
        file_scope=[_scope("src/generated/old.py", "create")],
        acceptance_criteria=[SimpleNamespace(description="Create the file.")],
    )

    with pytest.raises(ContractCompileError) as exc_info:
        ContractCompiler().compile_task(
            _request(
                registry,
                forbidden_scope,
                manifest_forbidden_files=[{"path": "src/generated/", "match_kind": "directory"}],
            )
        )
    assert exc_info.value.failure_type == "contract_scope_conflict"

    allowed_sibling = _task(
        file_scope=[_scope("tmp/cache-extra", "modify")],
        acceptance_criteria=[SimpleNamespace(description="Modify the sibling.")],
    )
    contract = ContractCompiler().compile_task(
        _request(registry, allowed_sibling, manifest_forbidden_files=[{"path": "tmp/cache"}])
    )
    repo = registry.repos[0]
    sibling_verdict = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-1",
            repo_id=repo.repo_id,
            modified_paths=["tmp/cache-extra"],
            diff_sha256="digest",
        ),
        _snapshot(repo, present_paths=["tmp/cache-extra"]),
    )
    assert sibling_verdict.approved is True

    non_conflicting = _task(
        file_scope=[_scope("src/app.py", "modify")],
        acceptance_criteria=[SimpleNamespace(description="Modify app.")],
    )
    directory_contract = ContractCompiler().compile_task(
        _request(registry, non_conflicting, manifest_forbidden_files=[{"path": "tmp/cache/", "match_kind": "directory"}])
    )
    forbidden_verdict = ContractCompiler().validate_patch(
        directory_contract,
        PatchSummary(
            sandbox_id="sandbox-2",
            repo_id=repo.repo_id,
            modified_paths=["tmp/cache/output.json"],
            diff_sha256="digest",
        ),
        _snapshot(repo, present_paths=["src/app.py"]),
    )
    assert "forbidden_path_touched" in forbidden_verdict.violation_codes
    assert "modify_outside_allowed_paths" not in forbidden_verdict.violation_codes


def test_unknown_write_set_is_isolated_and_group_read_write_conflicts_fail(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    unknown = _task(task_id="TASK-unknown")
    contract = ContractCompiler().compile_task(_request(registry, unknown, all_task_ids=["TASK-unknown"]))

    assert contract.unknown_write_set is True
    assert contract.execution_policy.write_set_mode == "unknown_isolated"
    assert contract.execution_policy.sandbox_isolation == "per_task"
    assert contract.execution_policy.merge_admission == "single_task"

    reader = _task(
        task_id="TASK-reader",
        file_scope=[_scope("src/shared.py", "read_only")],
        dependencies=[],
    )
    writer = _task(
        task_id="TASK-writer",
        file_scope=[_scope("src/shared.py", "modify")],
        acceptance_criteria=[SimpleNamespace(description="Modify shared.")],
    )
    with pytest.raises(ContractCompileError) as exc_info:
        ContractCompiler().compile_group(
            ContractGroupCompileRequest(
                feature_id=FEATURE_ID,
                dag_sha256=DAG_SHA,
                source_dag_artifact_id=42,
                source_dag_sha256=SOURCE_DAG_SHA,
                group_idx=3,
                tasks=[reader, writer],
                all_task_ids=["TASK-reader", "TASK-writer"],
                workspace_registry=registry,
            )
        )
    assert exc_info.value.failure_type == "contract_scope_conflict"


def test_contract_digest_is_stable_under_path_rule_ordering(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    criteria = [SimpleNamespace(description="Modify both files.")]
    task_a = _task(
        file_scope=[_scope("src/b.py", "modify"), _scope("src/a.py", "modify")],
        acceptance_criteria=criteria,
    )
    task_b = _task(
        file_scope=[_scope("src/a.py", "modify"), _scope("src/b.py", "modify")],
        acceptance_criteria=criteria,
    )

    first = ContractCompiler().compile_task(_request(registry, task_a))
    second = ContractCompiler().compile_task(_request(registry, task_b))

    assert [rule.path for rule in first.allowed_paths] == ["src/a.py", "src/b.py"]
    assert first.contract_digest == second.contract_digest


def test_patch_validation_rejects_forbidden_before_allowed_read_only_and_bad_operations(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    repo = registry.repos[0]
    task = _task(
        file_scope=[
            _scope("src/app.py", "modify"),
            _scope("docs/reference.md", "read_only"),
        ],
        acceptance_criteria=[SimpleNamespace(description="Modify app only.")],
    )
    contract = ContractCompiler().compile_task(
        _request(registry, task, manifest_forbidden_files=[{"path": "secrets/"}])
    )
    snapshot = _snapshot(repo, present_paths=["src/app.py", "docs/reference.md"])

    forbidden = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-forbidden",
            repo_id=repo.repo_id,
            modified_paths=["secrets/token.txt"],
            diff_sha256="digest",
        ),
        snapshot,
    )
    assert forbidden.violation_codes == ["forbidden_path_touched"]

    read_only = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-readonly",
            repo_id=repo.repo_id,
            modified_paths=["docs/reference.md"],
            diff_sha256="digest",
        ),
        snapshot,
    )
    assert "read_only_path_touched" in read_only.violation_codes

    created = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-create",
            repo_id=repo.repo_id,
            created_paths=["src/app.py"],
            diff_sha256="digest",
        ),
        snapshot,
    )
    assert "create_outside_allowed_paths" in created.violation_codes

    renamed = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-rename",
            repo_id=repo.repo_id,
            renamed_paths={"src/app.py": "src/renamed.py"},
            diff_sha256="digest",
        ),
        snapshot,
    )
    assert "rename_from_outside_allowed_paths" in renamed.violation_codes
    assert "rename_to_outside_allowed_paths" in renamed.violation_codes


def test_patch_presence_generated_output_and_digest_mismatch_validation(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    repo = registry.repos[0]
    task = _task(
        file_scope=[_scope("src/app.py", "modify")],
        acceptance_criteria=[SimpleNamespace(id="AC-1", description="Update app and summary.")],
    )
    contract = ContractCompiler().compile_task(
        _request(
            registry,
            task,
            generated_outputs=[
                {"path": "reports/summary.json", "source_path": "src/app.py", "criterion_ids": ["AC-1"]}
            ],
        )
    )
    snapshot = _snapshot(repo, present_paths=["src/app.py"])

    missing = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-missing",
            repo_id=repo.repo_id,
            modified_paths=["src/app.py"],
            diff_sha256="digest",
        ),
        snapshot,
    )
    assert "generated_output_missing" in missing.violation_codes

    present = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-present",
            repo_id=repo.repo_id,
            modified_paths=["src/app.py"],
            created_paths=["reports/summary.json"],
            diff_sha256="digest",
        ),
        snapshot,
    )
    assert present.approved is True

    mismatch = ContractCompiler().validate_patch(
        contract,
        PatchSummary(
            sandbox_id="sandbox-mismatch",
            repo_id=repo.repo_id,
            modified_paths=["src/app.py"],
            created_paths=["reports/summary.json"],
            diff_sha256="summary-digest",
            diff_artifact_sha256="artifact-digest",
        ),
        snapshot,
    )
    assert "payload_digest_mismatch" in mismatch.violation_codes

    presence = ContractCompiler().validate_presence(contract, _snapshot(repo, present_paths=["src/app.py"]))
    assert "generated_output_missing" in presence.violation_codes

    with pytest.raises(ContractCompileError) as missing_absence_evidence:
        ContractCompiler().compile_task(
            _request(
                registry,
                task,
                generated_outputs=[
                    {"path": "reports/obsolete.json", "source_path": "src/app.py", "absent": True}
                ],
            )
        )
    assert missing_absence_evidence.value.failure_type == "contract_missing_absence_evidence"


def test_empty_patch_only_passes_for_read_only_or_verification_contracts(tmp_path: Path) -> None:
    registry, _feature_root = _registry(tmp_path)
    repo = registry.repos[0]
    read_only_task = _task(
        file_scope=[_scope("docs/reference.md", "read_only")],
        acceptance_criteria=[SimpleNamespace(description="Verify docs only.")],
    )
    read_only_contract = ContractCompiler().compile_task(_request(registry, read_only_task))
    read_only_verdict = ContractCompiler().validate_patch(
        read_only_contract,
        PatchSummary(sandbox_id="sandbox-ro", repo_id=repo.repo_id, diff_sha256="digest"),
        _snapshot(repo, present_paths=["docs/reference.md"]),
    )
    assert read_only_verdict.approved is True

    writable_task = _task(
        file_scope=[_scope("src/app.py", "modify")],
        acceptance_criteria=[SimpleNamespace(description="Modify app.")],
    )
    writable_contract = ContractCompiler().compile_task(_request(registry, writable_task))
    empty_writable = ContractCompiler().validate_patch(
        writable_contract,
        PatchSummary(sandbox_id="sandbox-write", repo_id=repo.repo_id, diff_sha256="digest"),
        _snapshot(repo, present_paths=["src/app.py"]),
    )
    assert "empty_patch_requires_mutation" in empty_writable.violation_codes
