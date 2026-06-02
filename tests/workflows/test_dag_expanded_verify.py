import hashlib
import itertools
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose import Ask

from iriai_build_v2.execution_control.atomic_landing import InFlightAdoptionRecord
from iriai_build_v2.models.outputs import (
    ArtifactRepairResult,
    ArtifactRepairUpdate,
    BugGroup,
    BugTriage,
    FindingLedger,
    FindingRecord,
    Gap,
    DerivedDAGArtifact,
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
    Issue,
    RootCauseAnalysis,
    TaskFileScope,
    Verdict,
)
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module


def _strict_adoption_marker(
    feature,
    *,
    completed_range: tuple[int, int],
    next_group: int,
) -> str:
    return InFlightAdoptionRecord(
        feature_id=str(feature.id),
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-artifact",
        legacy_root_dag_artifact_id=42,
        legacy_root_dag_sha256="f" * 64,
        completed_checkpoint_range=completed_range,
        next_effective_group_idx=next_group,
        projection_digest="p" * 64,
        adopted_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        pre_adoption_baseline={"test": "sealed"},
    ).model_dump_json()


def test_dag_expanded_verify_env_defaults_on(monkeypatch):
    monkeypatch.delenv(implementation_module.DAG_EXPANDED_VERIFY_ENV, raising=False)

    assert implementation_module._dag_expanded_verify_enabled() is True


def test_dag_expanded_verify_env_kill_switch(monkeypatch):
    monkeypatch.setenv(implementation_module.DAG_EXPANDED_VERIFY_ENV, "0")

    assert implementation_module._dag_expanded_verify_enabled() is False


def test_dag_repair_runtime_roles_are_fixed():
    assert implementation_module._dag_repair_runtime_for("dag-normal-verify") == "secondary"
    assert implementation_module._dag_repair_runtime_for("dag-final-verify") == "secondary"
    assert implementation_module._dag_repair_runtime_for("dag-rca") == "primary"
    assert implementation_module._dag_repair_runtime_for("dag-fix") == "primary"
    assert implementation_module._dag_repair_runtime_for("dag-contradiction-resolve") == "secondary"
    assert implementation_module._dag_repair_runtime_for("lens:acceptance-coverage") == "secondary"
    assert implementation_module._dag_repair_runtime_for("lens:contract-protocol") == "secondary"
    assert implementation_module._dag_repair_runtime_for("lens:build-dependency") == "primary"
    assert implementation_module._dag_repair_runtime_for("unknown", "fallback") == "fallback"


def test_sandbox_runtime_name_resolves_primary_claude_pool_instance():
    runner = SimpleNamespace(
        agent_runtime=SimpleNamespace(name="claude_pool"),
        secondary_runtime=SimpleNamespace(name="codex"),
    )

    assert implementation_module._sandbox_runtime_name("primary", runner) == "claude_pool"
    assert implementation_module._sandbox_runtime_name("secondary", runner) == "codex"


def test_dag_parallel_repair_kill_switch(monkeypatch):
    monkeypatch.setenv(implementation_module.DAG_PARALLEL_REPAIR_ENV, "0")

    assert implementation_module._dag_parallel_repair_enabled() is False


def test_dag_auto_resolve_contradictions_kill_switch(monkeypatch):
    monkeypatch.setenv(implementation_module.DAG_AUTO_RESOLVE_CONTRADICTIONS_ENV, "0")

    assert implementation_module._dag_auto_resolve_contradictions_enabled() is False


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )


def _write_forbidden_manifest(repo: Path, forbidden_path: str) -> None:
    config = repo / "scripts" / "verify-file-scope.expected-files.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({
            "expected_files": [],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "test-manifest",
                }
            ],
        }),
        encoding="utf-8",
    )


class _DurableGraphStore:
    def __init__(self) -> None:
        self.projections: list[dict[str, object]] = []
        self.projected_checkpoints: list[object] = []
        self.test_mirror_group_checkpoint_to_artifacts = True
        self.verified: dict[tuple[str, str, str, int, str, str], dict[str, object]] = {}
        self.nodes: list[SimpleNamespace] = []

    async def put_task_contract(self, *args, **kwargs):  # pragma: no cover - marker port
        del args, kwargs
        return None

    async def record_verification_graph_node(self, payload):
        node = SimpleNamespace(
            id=len(self.nodes) + 1,
            feature_id=str(payload.get("feature_id") or ""),
            kind=str(payload.get("kind") or ""),
            stage=str(payload.get("stage") or ""),
            group_idx=payload.get("group_idx"),
            status=str(payload.get("status") or "approved"),
            content_hash=str(payload.get("content_hash") or ""),
        )
        self.nodes.append(node)
        return SimpleNamespace(evidence=node, created=True)

    async def list_verification_graph_nodes(
        self,
        feature_id: str,
        *,
        dag_sha256: str = "",
        group_idx: int | None = None,
        stage: str = "",
        after_id: int = 0,
        limit: int = 100,
    ):
        del dag_sha256
        rows = [
            node for node in self.nodes
            if node.feature_id == feature_id
            and (group_idx is None or node.group_idx == group_idx)
            and (not stage or node.stage == stage)
            and node.id > after_id
        ]
        return rows[:limit]

    async def record_verification_graph_projection(self, payload):
        self.projections.append(payload)
        proof = payload.get("proof") if isinstance(payload, dict) else {}
        if not isinstance(proof, dict):
            proof = {}
        required_edge_ids = list(proof.get("required_edge_ids") or [])
        row_id = 10_000 + len(self.projections)
        graph_id = 20_000 + len(self.projections)
        proof_digest = str(proof.get("proof_digest") or "")
        graph_payload_digest = implementation_module._dag_verify_graph_store_payload_digest(
            payload
        )
        projection_payload = {
            "aggregate_evidence_node_id": payload.get("aggregate", {}).get("node_id"),
            "evidence_graph_id": graph_id,
            "graph_payload_digest": graph_payload_digest,
            "proof_digest": proof_digest,
            "required_edge_ids": required_edge_ids,
        }
        required_edges = [
            {
                "id": 50_000 + len(self.projections) + index,
                "graph_edge_id": str(edge_id),
                "evidence_graph_id": graph_id,
                "required": True,
            }
            for index, edge_id in enumerate(required_edge_ids)
        ]
        self.verified[
            (
                str(payload.get("feature_id") or ""),
                str(payload.get("projection_key") or ""),
                str(payload.get("dag_sha256") or ""),
                int(payload.get("group_idx")),
                str(payload.get("stage") or ""),
                proof_digest,
            )
        ] = {
            "graph": {
                "id": graph_id,
                "execution_journal_row_id": row_id,
                "aggregate_evidence_node_id": payload.get("aggregate", {}).get("node_id"),
                "projection_key": payload.get("projection_key"),
                "dag_sha256": payload.get("dag_sha256"),
                "group_idx": payload.get("group_idx"),
                "stage": payload.get("stage"),
                "proof_digest": proof_digest,
                "graph_payload_digest": graph_payload_digest,
                "required_edge_ids": required_edge_ids,
                "payload": payload,
            },
            "required_edges": required_edges,
            "projection_links": [
                {
                    "id": 30_000 + len(self.projections),
                    "artifact_id": 40_000 + len(self.projections),
                    "projection_owner": "verification_graph",
                    "projection_kind": "verify_result",
                    "projection_key": payload.get("projection_key"),
                    "payload": projection_payload,
                }
            ],
        }
        return SimpleNamespace(
            row=SimpleNamespace(id=row_id, payload=projection_payload),
            projection_links=[
                SimpleNamespace(
                    id=30_000 + len(self.projections),
                    artifact_id=40_000 + len(self.projections),
                    payload=projection_payload,
                )
            ],
            created=len(self.projections) == 1,
        )

    async def get_verified_verification_graph_projection(
        self,
        *,
        feature_id: str,
        projection_key: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
        proof_digest: str,
    ):
        return self.verified.get(
            (feature_id, projection_key, dag_sha256, group_idx, stage, proof_digest)
        )

    async def get_latest_verified_verification_graph_projection(
        self,
        *,
        feature_id: str,
        projection_key: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
    ):
        for key, verified in reversed(list(self.verified.items())):
            if key[:5] == (feature_id, projection_key, dag_sha256, group_idx, stage):
                return verified
        return None

    async def project_group_checkpoint(self, projection):
        self.projected_checkpoints.append(projection)
        key = str(getattr(projection, "projection_key", "") or "")
        checkpoint = getattr(projection, "checkpoint", {})
        payload = {
            "checkpoint": checkpoint,
            "group_idx": getattr(projection, "group_idx", None),
            "projection_key": key,
            "status": getattr(projection, "status", ""),
        }
        index = len(self.projected_checkpoints)
        return SimpleNamespace(
            row=SimpleNamespace(id=60_000 + index, payload=payload),
            projection_links=[
                SimpleNamespace(
                    id=61_000 + index,
                    artifact_id=62_000 + index,
                    projection_key=key,
                    payload=payload,
                )
            ],
            created=index == 1,
        )


def _install_durable_graph_store(runner) -> _DurableGraphStore:
    store = _DurableGraphStore()
    services = dict(getattr(runner, "services", {}) or {})
    services["execution_control_store"] = store
    runner.services = services
    return store


def _install_approved_lens_runtime(runner) -> None:
    async def _run(task, feature, phase_name=""):
        del feature, phase_name
        if getattr(task, "output_type", None) is Verdict:
            return Verdict(approved=True, summary="lens approved")
        raise AssertionError(f"unexpected task: {task!r}")

    runner.run = _run


def _allow_checkpoint_lenses_only(message: str):
    async def _fake(*args, **kwargs):
        if kwargs.get("record_graph") is False:
            collector = kwargs.get("lens_collector")
            if collector is not None:
                collector.extend(
                    (spec, Verdict(approved=True, summary=f"{spec.slug} approved"))
                    for spec in implementation_module._dag_verify_lens_specs()
                )
            return args[4]
        raise AssertionError(message)

    return _fake


async def _approved_graph_fixture(
    tmp_path,
    monkeypatch,
    *,
    feature_id: str,
    group_idx: int,
    dag_sha256: str,
):
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_ok(*args, **kwargs):
        del args, kwargs
        return Verdict(approved=True, summary="graph approved")

    async def _commit_ok(*args, **kwargs):
        del args, kwargs
        return "commit-graph-proof"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"test_allow_legacy_repair_without_sandbox": True},
    )
    store = _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    monkeypatch.setattr(implementation_module, "_commit_group", _commit_ok)

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        group_idx,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_ok,
        dag_sha256=dag_sha256,
    )
    assert approved is True, failure
    assert failure == ""
    return runner, feature, store


@pytest.mark.asyncio
async def test_commit_repos_in_root_hard_fails_on_pre_commit_hook(tmp_path):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "echo 'husky says no' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos_in_root(repos_root, "test: hook")

    exc = raised.value
    assert exc.failed_outcomes
    failure = exc.failed_outcomes[0]
    assert failure.repo_name == "app"
    assert failure.command == ["git", "commit", "-m", "test: hook"]
    assert failure.exit_code != 0
    assert "husky says no" in failure.stderr
    assert "README.md" in failure.status_before
    assert "README.md" in failure.status_after
    assert exc.to_payload()["failed_repo_count"] == 1


def test_commit_failure_issue_extracts_hook_file_and_line(tmp_path):
    exc = implementation_module.WorkflowCommitError(
        "commit failed",
        [
            implementation_module.CommitRepoOutcome(
                repo_path=str(tmp_path / "iriai-studio"),
                repo_name="iriai-studio",
                message="fix",
                dirty=True,
                command=["git", "commit", "-m", "fix"],
                exit_code=1,
                stderr=(
                    "src/webviews/projectSurface/src/chat/__tests__/"
                    "ChatSidepaneShell.test.tsx(149,29): unexpected unicode "
                    "character U+00A7"
                ),
                error="git commit failed",
            )
        ],
    )

    issue = implementation_module._commit_failure_issue(exc, stage="retry-0")

    assert issue.file == (
        "iriai-studio/src/webviews/projectSurface/src/chat/__tests__/"
        "ChatSidepaneShell.test.tsx"
    )
    assert issue.line == 149
    assert "unexpected unicode" in issue.description


def test_commit_failure_manifest_forbidden_status_routes_to_cleanup(tmp_path):
    repo = tmp_path / "iriai-studio"
    _init_git_repo(repo)
    forbidden_root = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    _write_forbidden_manifest(repo, forbidden_root)
    exc = implementation_module.WorkflowCommitError(
        "commit failed",
        [
            implementation_module.CommitRepoOutcome(
                repo_path=str(repo),
                repo_name="iriai-studio",
                message="fix",
                dirty=True,
                command=["git", "commit", "-m", "fix"],
                exit_code=1,
                stderr=(
                    f"{forbidden_root}/test/browser/cardVariantRegistry.test.ts:32:1 "
                    "Suites should include disposables leak checks"
                ),
                status_after=(
                    f"A  {forbidden_root}/cardVariantRegistry.ts\n"
                    f"A  {forbidden_root}/test/browser/cardVariantRegistry.test.ts"
                ),
                error="git commit failed",
            )
        ],
    )

    issue = implementation_module._commit_failure_issue(exc, stage="retry-0")
    payload = implementation_module._commit_failure_payload(exc)
    route = implementation_module._classify_dag_direct_repair_route(
        Verdict(approved=False, summary="commit failed", concerns=[issue])
    )

    assert issue.severity == "blocker"
    assert "manifest-forbidden product cleanup" in issue.description
    assert "Do not repair this by adding ignore/suppression rules" in issue.description
    assert issue.file == f"iriai-studio/{forbidden_root}/cardVariantRegistry.ts"
    assert payload["manifest_forbidden_matches"][0]["manifest_rule"] == forbidden_root
    assert route.route == "manifest_forbidden_product_cleanup"
    assert route.operator_required is False


def test_commit_failure_manifest_forbidden_bare_hook_path_does_not_suffix_match():
    entry = {"path": "src/generated/foo.ts", "source": "test-manifest"}

    assert implementation_module._commit_path_matches_forbidden_entry(
        "src/generated/foo.ts",
        entry,
    )
    assert not implementation_module._commit_path_matches_forbidden_entry("foo.ts", entry)
    assert not implementation_module._commit_path_matches_forbidden_entry(
        "generated/foo.ts",
        entry,
    )
    assert not implementation_module._commit_path_matches_forbidden_entry(
        "test/foo.ts",
        {"path": "src/generated/test/foo.ts", "source": "test-manifest"},
    )


def test_dag_manifest_forbidden_matcher_rejects_non_descendant_suffixes():
    forbidden = {"src/generated/foo.ts", "src/generated/test/foo.ts"}
    entries = [
        {"path": "src/generated/foo.ts", "source": "manifest"},
        {"path": "src/generated/test/foo.ts", "source": "manifest"},
    ]

    assert implementation_module._dag_path_matches_forbidden_file(
        "src/generated/foo.ts",
        forbidden,
    )
    assert implementation_module._dag_path_matches_forbidden_file(
        "repo/src/generated/foo.ts",
        forbidden,
    )
    assert not implementation_module._dag_path_matches_forbidden_file(
        "app/src/generated/foo.ts",
        forbidden,
    )
    assert not implementation_module._dag_path_matches_forbidden_file(
        "apps/src/generated/foo.ts",
        forbidden,
    )
    assert not implementation_module._dag_path_matches_forbidden_file(
        "components/src/generated/foo.ts",
        forbidden,
    )
    assert implementation_module._dag_path_matches_forbidden_file(
        "src/generated/test/foo.ts/child",
        forbidden,
    )
    assert not implementation_module._dag_path_matches_forbidden_file(
        "generated/foo.ts",
        forbidden,
    )
    assert not implementation_module._dag_path_matches_forbidden_file(
        "test/foo.ts",
        forbidden,
    )
    assert implementation_module._dag_forbidden_match("foo.ts", entries) is None
    assert implementation_module._dag_forbidden_match("test/foo.ts", entries) is None


def test_commit_failure_manifest_forbidden_reports_permission_normalization_need(
    tmp_path,
):
    repo = tmp_path / ".iriai" / "features" / "feat" / "repos" / "iriai-studio"
    _init_git_repo(repo)
    forbidden_root = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    _write_forbidden_manifest(repo, forbidden_root)
    forbidden_dir = repo / forbidden_root
    forbidden_dir.mkdir(parents=True)
    (forbidden_dir / "cardVariantRegistry.ts").write_text("export {};\n", encoding="utf-8")
    forbidden_dir.chmod(0o755)
    (repo / ".git" / "index").chmod(0o644)
    exc = implementation_module.WorkflowCommitError(
        "commit failed",
        [
            implementation_module.CommitRepoOutcome(
                repo_path=str(repo),
                repo_name="iriai-studio",
                message="fix",
                dirty=True,
                command=["git", "commit", "-m", "fix"],
                exit_code=1,
                stderr=f"{forbidden_root}/cardVariantRegistry.ts:1:1 warning",
                status_after=f"A  {forbidden_root}/cardVariantRegistry.ts",
                error="git commit failed",
            )
        ],
    )

    issue = implementation_module._commit_failure_issue(exc, stage="retry-0")
    route = implementation_module._classify_dag_direct_repair_route(
        Verdict(approved=False, summary="commit failed", concerns=[issue])
    )

    assert route.route == "manifest_forbidden_product_cleanup"
    assert route.operator_required is False
    assert "workspace permission normalization is required" in issue.description
    assert "git index is not writable by repair agent" in issue.description


@pytest.mark.asyncio
async def test_post_dag_gate_proof_invalidates_after_repair_commit(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-gate-fresh", slug="gate-fresh")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "app"
    _init_git_repo(repo)
    (repo / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={
            "workspace_manager": SimpleNamespace(_base=workspace_root),
            "execution_control_store": _DurableGraphStore(),
        },
    )
    old_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:code-review", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "code-review",
        old_digest,
    )
    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        old_digest,
    )

    (repo / "app.py").write_text("value = 'repair'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "repair"], cwd=repo, check=True)
    new_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)

    assert new_digest != old_digest
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        new_digest,
    )
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "code-review",
        new_digest,
    )
    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        new_digest,
    )


def test_post_dag_gate_tree_digest_binds_dirty_file_content(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-gate-dirty", slug="gate-dirty")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "app"
    _init_git_repo(repo)
    tracked = repo / "app.py"
    tracked.write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

    runner = SimpleNamespace(
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )

    tracked.write_text("value = 'dirty-one'\n", encoding="utf-8")
    first_dirty_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    tracked.write_text("value = 'dirty-two'\n", encoding="utf-8")
    second_dirty_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    untracked = repo / "notes.txt"
    untracked.write_text("note one\n", encoding="utf-8")
    first_untracked_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    untracked.write_text("note two\n", encoding="utf-8")
    second_untracked_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)

    assert first_dirty_digest != second_dirty_digest
    assert first_untracked_digest != second_untracked_digest


@pytest.mark.asyncio
async def test_post_dag_gate_tree_digest_includes_authorized_external_worktree_changes(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-gate-external", slug="gate-external")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "app"
    source = workspace_root / "source" / "app"
    _init_git_repo(source)
    subprocess.run(
        ["git", "worktree", "add", "-b", "post-dag-external", str(repo), "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="app",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-1"],
                source_path=str(source),
                destination_path=str(repo),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "worktree-registry": registry.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )

    tracked = repo / "README.md"
    tracked.write_text("dirty external digest one\n", encoding="utf-8")
    first_digest = await implementation_module._current_post_dag_gate_tree_digest(
        runner,
        feature,
    )
    tracked.write_text("dirty external digest two\n", encoding="utf-8")
    second_digest = await implementation_module._current_post_dag_gate_tree_digest(
        runner,
        feature,
    )

    assert first_digest != second_digest


@pytest.mark.asyncio
async def test_post_dag_gate_proof_requires_schema_and_gate_identity() -> None:
    feature = SimpleNamespace(id="feat-gate-identity", slug="gate-identity")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"execution_control_store": _DurableGraphStore()},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:source-push", "approved", feature=feature)
    await runner.artifacts.put(
        implementation_module._post_dag_gate_proof_key("source-push"),
        json.dumps(
            {
                "artifact_schema": "dag-post-gate-proof-v1",
                "gate": "notify",
                "approved": True,
                "tree_digest": tree_digest,
            },
            sort_keys=True,
        ),
        feature=feature,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )

    await runner.artifacts.put(
        implementation_module._post_dag_gate_proof_key("source-push"),
        json.dumps(
            {
                "gate": "source-push",
                "approved": True,
                "tree_digest": tree_digest,
            },
            sort_keys=True,
        ),
        feature=feature,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )

    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "source-push",
        tree_digest,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )
    source_push_proof = implementation_module._finalize_source_push_proof(
        {
            "artifact_schema": "dag-source-push-proof-v1",
            "tree_digest": tree_digest,
            "repos_root": "",
            "expected_origins": {},
            "repos": {
                "app": {
                    "status": "pushed",
                    "tree_digest": tree_digest,
                    "repo": "app",
                    "branch": "main",
                    "local_head": "local-head",
                    "remote_ref": "refs/heads/main",
                    "remote_before": "old-head",
                    "remote_after": "local-head",
                    "expected_origin": "",
                    "actual_origin": "",
                }
            },
        }
    )
    await runner.artifacts.put(
        implementation_module._source_push_proof_key(),
        json.dumps(source_push_proof, sort_keys=True),
        feature=feature,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )
    await runner.artifacts.put("dag-gate:code-review", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "code-review",
        tree_digest,
    )
    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_post_dag_gate_requires_typed_gate_evidence() -> None:
    feature = SimpleNamespace(id="feat-post-dag-typed", slug="post-dag-typed", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"execution_control_store": _DurableGraphStore()},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:code-review", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "code-review",
        tree_digest,
    )
    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )
    proof_key = implementation_module._post_dag_gate_proof_key("code-review")
    proof = json.loads(runner.artifacts.store[proof_key])
    proof["typed_gate"]["content_hash"] = "tampered"
    await runner.artifacts.put(proof_key, json.dumps(proof, sort_keys=True), feature=feature)
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_post_dag_gate_requires_typed_graph_approval_evidence() -> None:
    feature = SimpleNamespace(
        id="feat-post-dag-aggregate",
        slug="post-dag-aggregate",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"execution_control_store": _DurableGraphStore()},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:code-review", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "code-review",
        tree_digest,
    )

    proof_key = implementation_module._post_dag_gate_proof_key("code-review")
    proof = json.loads(runner.artifacts.store[proof_key])
    assert proof["typed_gate"]["persisted"] is True
    assert proof["graph_approval"]["persisted"] is True
    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )

    missing_graph_approval = dict(proof)
    missing_graph_approval.pop("graph_approval")
    await runner.artifacts.put(
        proof_key,
        json.dumps(missing_graph_approval, sort_keys=True),
        feature=feature,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )

    stale_graph_approval = dict(proof)
    stale_graph_approval["graph_approval"] = {
        **proof["graph_approval"],
        "content_hash": "stale-aggregate-proof",
    }
    await runner.artifacts.put(
        proof_key,
        json.dumps(stale_graph_approval, sort_keys=True),
        feature=feature,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_adopted_post_dag_gate_rejects_generic_no_store_proof() -> None:
    feature = SimpleNamespace(
        id="feat-adopted-post-dag",
        slug="adopted-post-dag",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:code-review", "approved", feature=feature)
    await runner.artifacts.put(
        implementation_module._post_dag_gate_proof_key("code-review"),
        json.dumps(
            {
                "artifact_schema": "dag-post-gate-proof-v1",
                "gate": "code-review",
                "approved": True,
                "tree_digest": tree_digest,
            },
            sort_keys=True,
        ),
        feature=feature,
    )

    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )

    await runner.artifacts.put(
        f"execution-control-adoption:{feature.id}",
        '{"status": "adopted", "feature_id": "feat-adopted-post-dag"}',
        feature=feature,
    )
    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_legacy_no_store_post_dag_gate_stays_fresh_after_store_appears() -> None:
    feature = SimpleNamespace(
        id="feat-legacy-no-store-post-dag",
        slug="legacy-no-store-post-dag",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"execution_control_store": _DurableGraphStore()},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:code-review", "approved", feature=feature)
    await runner.artifacts.put(
        implementation_module._post_dag_gate_proof_key("code-review"),
        json.dumps(
            {
                "artifact_schema": "dag-post-gate-proof-v1",
                "gate": "code-review",
                "approved": True,
                "tree_digest": tree_digest,
                "typed_gate": {
                    "persisted": False,
                    "failure_type": "missing_execution_control_store",
                },
            },
            sort_keys=True,
        ),
        feature=feature,
    )

    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "code-review",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_source_push_failure_raises_before_gate_can_be_recorded(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "remote-before\trefs/heads/main"
        if args == ("push", str(source), "HEAD:refs/heads/main"):
            raise RuntimeError("remote rejected")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="failed to push cloned repos to source"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            allowed_origin_root=tmp_path,
        )

    assert (repo, ("push", str(source), "HEAD:refs/heads/main")) in calls


@pytest.mark.asyncio
async def test_source_push_missing_or_unpushable_roots_fail_closed(
    tmp_path,
    monkeypatch,
):
    missing_root = tmp_path / "missing"
    with pytest.raises(RuntimeError, match="repos root does not exist"):
        await implementation_module._push_clones_to_source_root(missing_root)

    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="no current branch|no pushable"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            allowed_origin_root=tmp_path,
        )


@pytest.mark.asyncio
async def test_source_push_requires_canonical_authority_before_git_commands(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    (repo / ".git").mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        raise AssertionError("source push must fail closed before git commands")

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="canonical source authority"):
        await implementation_module._push_clones_to_source_root(repos_root)

    assert calls == []


@pytest.mark.asyncio
async def test_source_push_dirty_worktree_fails_closed_without_commit(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    (repo / ".git").mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return " M app.py"
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="dirty worktree before source push"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            allowed_origin_root=tmp_path,
        )

    assert (repo, ("push", "origin", "main")) not in calls
    assert not any(args[0] in {"add", "commit"} for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_later_preflight_failure_halts_before_any_push(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    app = repos_root / "app"
    docs = repos_root / "docs"
    source_app = tmp_path / "source" / "app"
    source_docs = tmp_path / "source" / "docs"
    for path in (app, docs, source_app, source_docs):
        path.mkdir(parents=True)
    (app / ".git").mkdir()
    (docs / ".git").mkdir()
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return " M stale.md" if cwd == docs else ""
        if args == ("rev-parse", "HEAD"):
            return "local-app" if cwd == app else "local-docs"
        if args == ("remote", "get-url", "origin"):
            return str(source_app if cwd == app else source_docs)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source_app if cwd == app else source_docs)
        if args == ("ls-remote", str(source_app), "refs/heads/main"):
            return "old-app\trefs/heads/main"
        if args[0] == "push":
            raise AssertionError("source push must not run until all repos preflight")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="docs: dirty worktree before source push"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            expected_origins={
                "app": str(source_app),
                "docs": str(source_docs),
            },
            allowed_origin_root=tmp_path / "source",
        )

    assert not any(args[0] == "push" for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_rejects_nested_or_stray_repos(tmp_path):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    nested = repo / "nested"
    (repo / ".git").mkdir(parents=True)
    (nested / ".git").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="nested or stray git repository"):
        await implementation_module._push_clones_to_source_root(repos_root)


@pytest.mark.asyncio
async def test_source_push_discovery_failure_halts_before_any_remote_mutation(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    nested = repo / "nested"
    (repo / ".git").mkdir(parents=True)
    (nested / ".git").mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        raise AssertionError("discovery failures must stop before git commands")

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="nested or stray git repository"):
        await implementation_module._push_clones_to_source_root(repos_root)

    assert calls == []


@pytest.mark.asyncio
async def test_source_push_rejects_gitdir_symlink_before_remote_mutation(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    external_git = tmp_path / "external-git"
    repo.mkdir(parents=True)
    external_git.mkdir()
    os.symlink(external_git, repo / ".git")
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        raise AssertionError("symlinked .git must stop before git commands")

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="workflow_repo_gitdir_symlink"):
        await implementation_module._push_clones_to_source_root(repos_root)

    assert calls == []


@pytest.mark.asyncio
async def test_source_push_rejects_origin_gitdir_outside_source_root_before_mutation(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source_root = tmp_path / "source"
    source = source_root / "app"
    outside_git = tmp_path / "outside-git"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    outside_git.mkdir()
    (source / ".git").write_text(f"gitdir: {outside_git}\n", encoding="utf-8")
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        raise AssertionError(f"origin metadata failure must precede remote mutation: {args}")

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="origin git metadata escapes"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            expected_origins={"app": str(source)},
            allowed_origin_root=source_root,
        )

    assert not any(args[0] in {"ls-remote", "push"} for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_rejects_origin_commondir_outside_source_root_before_mutation(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source_root = tmp_path / "source"
    source = source_root / "app"
    git_dir = source_root / "git-meta" / "app.git"
    outside_common_dir = tmp_path / "outside-common"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    git_dir.mkdir(parents=True)
    outside_common_dir.mkdir()
    (source / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    (git_dir / "commondir").write_text(str(outside_common_dir), encoding="utf-8")
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        raise AssertionError(f"origin common-dir failure must precede remote mutation: {args}")

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="origin git common-dir escapes"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            expected_origins={"app": str(source)},
            allowed_origin_root=source_root,
        )

    assert not any(args[0] in {"ls-remote", "push"} for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_rejects_actual_origin_symlink_component_before_mutation(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source_root = tmp_path / "source"
    real_parent = source_root / "real"
    real_source = real_parent / "app"
    link_parent = source_root / "link"
    (repo / ".git").mkdir(parents=True)
    (real_source / ".git").mkdir(parents=True)
    link_parent.symlink_to(real_parent, target_is_directory=True)
    actual_origin = link_parent / "app"
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(actual_origin)
        raise AssertionError(f"origin symlink failure must precede remote mutation: {args}")

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="origin path contains a symlinked component"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            expected_origins={"app": str(real_source)},
            allowed_origin_root=source_root,
        )

    assert not any(args[0] in {"ls-remote", "push"} for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_rejects_pushurl_outside_authority_before_mutation(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source_root = tmp_path / "source"
    source = source_root / "app"
    outside = tmp_path / "outside" / "app"
    (repo / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    outside.mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(outside)
        if args[0] in {"ls-remote", "push"}:
            raise AssertionError("untrusted pushurl must fail before remote mutation")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="push URL does not match canonical source authority"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            expected_origins={"app": str(source)},
            allowed_origin_root=source_root,
        )

    assert not any(args[0] in {"ls-remote", "push"} for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_rejects_legacy_sibling_pushurl_before_mutation(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    repos_root = workspace_root / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "app"
    source = workspace_root / "source" / "app"
    sibling_source = workspace_root / "sibling-source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    sibling_source.mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(sibling_source)
        if args[0] in {"ls-remote", "push"}:
            raise AssertionError("sibling pushurl must fail before remote mutation")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="push URL does not match current origin"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            expected_origins=None,
            allowed_origin_root=workspace_root,
        )

    assert not any(args[0] in {"ls-remote", "push"} for _cwd, args in calls)


@pytest.mark.asyncio
async def test_source_push_accepts_authorized_multi_segment_repo_path(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "services" / "newsvc"
    source = tmp_path / "source" / "newsvc"
    (repo / ".git").mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []
    remote_heads = iter([
        "old-head\trefs/heads/main",
        "local-head\trefs/heads/main",
    ])

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return next(remote_heads)
        if args == ("push", str(source), "HEAD:refs/heads/main"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    proof = await implementation_module._push_clones_to_source_root(
        repos_root,
        tree_digest="tree-digest",
        expected_origins={"services/newsvc": str(source)},
    )

    assert proof["repos"]["services/newsvc"]["status"] == "pushed"
    assert (repo, ("push", str(source), "HEAD:refs/heads/main")) in calls


@pytest.mark.asyncio
async def test_source_push_rejects_noop_remote_head_as_missing_proof(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "same-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "same-head\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="remote already at local HEAD"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            allowed_origin_root=tmp_path,
        )


@pytest.mark.asyncio
async def test_source_push_records_authority_bound_remote_proof(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    remote_heads = iter([
        "old-head\trefs/heads/main",
        "local-head\trefs/heads/main",
    ])
    proof_records: list[dict[str, object]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return next(remote_heads)
        if args == ("push", str(source), "HEAD:refs/heads/main"):
            return ""
        raise AssertionError(args)

    async def _persist(payload: dict[str, object]) -> None:
        proof_records.append(json.loads(json.dumps(payload, sort_keys=True)))

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    proof = await implementation_module._push_clones_to_source_root(
        repos_root,
        tree_digest="tree-digest",
        expected_origins={"app": str(source)},
        proof_callback=_persist,
    )

    assert proof["repos"]["app"]["status"] == "pushed"
    assert proof["repos"]["app"]["remote_before"] == "old-head"
    assert proof["repos"]["app"]["remote_after"] == "local-head"
    assert proof_records[0]["repos"]["app"]["status"] == "intent"
    assert proof_records[-1]["repos"]["app"]["status"] == "pushed"


@pytest.mark.asyncio
async def test_source_push_recovers_after_prior_successful_push_crash(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    proof_records: list[dict[str, object]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "local-head\trefs/heads/main"
        if args == ("push", str(source), "HEAD:refs/heads/main"):
            raise AssertionError("recovered source-push proof must not push again")
        raise AssertionError(args)

    async def _persist(payload: dict[str, object]) -> None:
        proof_records.append(json.loads(json.dumps(payload, sort_keys=True)))

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)
    prior = implementation_module._finalize_source_push_proof({
        "artifact_schema": "dag-source-push-proof-v1",
        "tree_digest": "tree-digest",
        "repos": {
            "app": {
                "status": "intent",
                "tree_digest": "tree-digest",
                "repo": "app",
                "branch": "main",
                "local_head": "local-head",
                "remote_ref": "refs/heads/main",
                "remote_before": "old-head",
                "remote_after": "",
                "expected_origin": str(source),
                "actual_origin": str(source),
            }
        },
    })

    proof = await implementation_module._push_clones_to_source_root(
        repos_root,
        tree_digest="tree-digest",
        expected_origins={"app": str(source)},
        prior_proof=prior,
        proof_callback=_persist,
    )

    assert proof["repos"]["app"]["status"] == "recovered"
    assert proof["repos"]["app"]["remote_after"] == "local-head"
    assert proof_records[-1]["repos"]["app"]["recovered_from_remote_head"] is True


@pytest.mark.asyncio
async def test_source_push_rejects_tampered_prior_proof_recovery(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "local-head\trefs/heads/main"
        if args == ("push", str(source), "HEAD:refs/heads/main"):
            raise AssertionError("tampered proof must not permit recovery or push")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)
    prior = implementation_module._finalize_source_push_proof({
        "artifact_schema": "dag-source-push-proof-v1",
        "tree_digest": "tree-digest",
        "repos": {
            "app": {
                "status": "intent",
                "tree_digest": "tree-digest",
                "repo": "app",
                "branch": "main",
                "local_head": "local-head",
                "remote_ref": "refs/heads/main",
                "remote_before": "old-head",
                "remote_after": "",
                "expected_origin": str(source),
                "actual_origin": str(source),
            }
        },
    })
    prior["proof_digest"] = "0" * 64

    with pytest.raises(RuntimeError, match="remote already at local HEAD"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            tree_digest="tree-digest",
            expected_origins={"app": str(source)},
            prior_proof=prior,
        )

    assert (repo, ("push", str(source), "HEAD:refs/heads/main")) not in calls


@pytest.mark.asyncio
async def test_source_push_rejects_malformed_prior_proof_recovery(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "local-head\trefs/heads/main"
        if args == ("push", str(source), "HEAD:refs/heads/main"):
            raise AssertionError("malformed proof must not permit recovery or push")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)
    prior = implementation_module._finalize_source_push_proof({
        "artifact_schema": "dag-source-push-proof-v1",
        "tree_digest": "tree-digest",
        "repos": {
            "app": {
                "status": "unknown",
                "tree_digest": "tree-digest",
                "repo": "app",
                "branch": "main",
                "local_head": "local-head",
                "remote_ref": "refs/heads/main",
                "remote_before": "old-head",
                "remote_after": "",
                "expected_origin": str(source),
                "actual_origin": str(source),
            }
        },
    })

    with pytest.raises(RuntimeError, match="remote already at local HEAD"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            tree_digest="tree-digest",
            expected_origins={"app": str(source)},
            prior_proof=prior,
        )

    assert (repo, ("push", str(source), "HEAD:refs/heads/main")) not in calls


@pytest.mark.asyncio
async def test_source_push_allows_authorized_read_only_noop_repos(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    app_repo = repos_root / "app"
    docs_repo = repos_root / "docs"
    app_source = tmp_path / "source" / "app"
    docs_source = tmp_path / "source" / "docs"
    (app_repo / ".git").mkdir(parents=True)
    (docs_repo / ".git").mkdir(parents=True)
    app_source.mkdir(parents=True)
    docs_source.mkdir(parents=True)
    calls: list[tuple[str, tuple[str, ...]]] = []
    app_ls_remote = iter([
        "old-head\trefs/heads/main",
        "app-head\trefs/heads/main",
    ])

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        rel = cwd.name
        calls.append((rel, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "app-head" if rel == "app" else "docs-head"
        if args == ("remote", "get-url", "origin"):
            return str(app_source if rel == "app" else docs_source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(app_source if rel == "app" else docs_source)
        if args == (
            "ls-remote",
            str(app_source if rel == "app" else docs_source),
            "refs/heads/main",
        ):
            if rel == "app":
                return next(app_ls_remote)
            return "docs-head\trefs/heads/main"
        if args == ("push", str(app_source), "HEAD:refs/heads/main"):
            assert rel == "app"
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    proof = await implementation_module._push_clones_to_source_root(
        repos_root,
        tree_digest="tree-digest",
        expected_origins={"app": str(app_source), "docs": str(docs_source)},
        optional_noop_repos={"docs"},
    )

    assert proof["repos"]["app"]["status"] == "pushed"
    assert proof["repos"]["docs"]["status"] == "unchanged"
    assert proof["repos"]["docs"]["mutation_required"] is False
    assert not any(rel == "docs" and args[0] == "push" for rel, args in calls)


@pytest.mark.asyncio
async def test_source_push_rejects_read_only_noop_repo_with_remote_behind(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    docs_repo = repos_root / "docs"
    docs_source = tmp_path / "source" / "docs"
    (docs_repo / ".git").mkdir(parents=True)
    docs_source.mkdir(parents=True)
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        rel = cwd.name
        calls.append((rel, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "docs-local-head"
        if args == ("remote", "get-url", "origin"):
            return str(docs_source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(docs_source)
        if args == ("ls-remote", str(docs_source), "refs/heads/main"):
            return "docs-remote-head\trefs/heads/main"
        if args == ("push", str(docs_source), "HEAD:refs/heads/main"):
            raise AssertionError("read-only optional-noop repo must not be pushed")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="optional-noop repo"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            tree_digest="tree-digest",
            expected_origins={"docs": str(docs_source)},
            optional_noop_repos={"docs"},
        )

    assert not any(rel == "docs" and args[0] == "push" for rel, args in calls)


@pytest.mark.asyncio
async def test_source_push_legacy_origin_accepts_nested_workspace_source(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    repos_root = workspace_root / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "api"
    nested_source = workspace_root / "apps" / "api"
    (repo / ".git").mkdir(parents=True)
    nested_source.mkdir(parents=True)
    remote_heads = iter([
        "old-head\trefs/heads/main",
        "local-head\trefs/heads/main",
    ])

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(nested_source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(nested_source)
        if args == ("ls-remote", str(nested_source), "refs/heads/main"):
            return next(remote_heads)
        if args == ("push", str(nested_source), "HEAD:refs/heads/main"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    proof = await implementation_module._push_clones_to_source_root(
        repos_root,
        tree_digest="tree-digest",
        expected_origins=None,
        allowed_origin_root=workspace_root,
    )

    assert proof["repos"]["api"]["status"] == "pushed"
    assert proof["repos"]["api"]["actual_origin"] == str(nested_source)


@pytest.mark.asyncio
async def test_source_push_rejects_origin_not_bound_to_canonical_authority(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    rogue = tmp_path / "rogue" / "app"
    (repo / ".git").mkdir(parents=True)
    source.mkdir(parents=True)
    rogue.mkdir(parents=True)

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(rogue)
        if args[0] in {"ls-remote", "push"}:
            raise AssertionError("untrusted origin must fail before remote mutation")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="origin does not match canonical"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            tree_digest="tree-digest",
            expected_origins={"app": str(source)},
        )


@pytest.mark.asyncio
async def test_source_push_rejects_registry_origin_outside_allowed_root(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    repos_root = workspace_root / "features" / "feat" / "repos"
    repo = repos_root / "app"
    rogue = tmp_path / "rogue" / "app"
    (repo / ".git").mkdir(parents=True)
    rogue.mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        calls.append((cwd, args))
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local-head"
        if args == ("remote", "get-url", "origin"):
            return str(rogue)
        if args[0] in {"ls-remote", "push"}:
            raise AssertionError("out-of-root registry origin must fail before remote mutation")
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    with pytest.raises(RuntimeError, match="origin is not under canonical workspace source root"):
        await implementation_module._push_clones_to_source_root(
            repos_root,
            tree_digest="tree-digest",
            expected_origins={"app": str(rogue)},
            allowed_origin_root=workspace_root,
        )

    assert not any(args[0] in {"ls-remote", "push"} for _cwd, args in calls)



@pytest.mark.asyncio
async def test_source_push_workflow_blocker_records_typed_failure() -> None:
    feature = SimpleNamespace(id="feat-source-push-failure", slug="source-push-failure")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    message = await implementation_module._record_source_push_workflow_blocker(
        runner,
        feature,
        reason="dirty worktree before source push",
        tree_digest_before="before",
        tree_digest_after="after",
    )

    assert "SANDBOX_WORKFLOW_BLOCKER" in message
    body = json.loads(runner.artifacts.store["dag-runtime-failure:source-push"])
    assert body["artifact_schema"] == "dag-source-push-failure-v1"
    assert body["failure_class"] == "runtime_context"
    assert body["failure_type"] == "source_push_failed"
    assert body["operator_required"] is False
    assert body["tree_digest_before"] == "before"
    assert body["tree_digest_after"] == "after"

    await implementation_module._record_source_push_workflow_blocker(
        runner,
        feature,
        reason="source push changed the post-DAG tree digest",
        tree_digest_before="before",
        tree_digest_after="after",
        failure_type="source_push_stale_gate_digest",
    )
    stale_body = json.loads(runner.artifacts.store["dag-runtime-failure:source-push"])
    assert stale_body["failure_type"] == "source_push_stale_gate_digest"


@pytest.mark.asyncio
async def test_source_push_requires_workspace_context() -> None:
    runner = SimpleNamespace(services={})
    feature = SimpleNamespace(id="feat-no-workspace", slug="no-workspace")

    with pytest.raises(RuntimeError, match="workspace_manager"):
        await implementation_module._push_clones_to_source(runner, feature)


@pytest.mark.asyncio
async def test_source_push_gate_freshness_skips_push_side_effect(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-source-push-skip", slug="source-push-skip")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "app"
    source = workspace_root / "source" / "app"
    _init_git_repo(repo)
    source.mkdir(parents=True)
    (repo / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    await runner.artifacts.put("dag-gate:source-push", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "source-push",
        tree_digest,
    )
    await runner.artifacts.put(
        implementation_module._source_push_proof_key(),
        json.dumps(
            implementation_module._finalize_source_push_proof(
                {
                    "artifact_schema": "dag-source-push-proof-v1",
                    "tree_digest": tree_digest,
                    "repos_root": str(feature_root),
                    "expected_origins": {},
                    "repos": {
                        "app": {
                            "status": "pushed",
                            "tree_digest": tree_digest,
                            "repo": "app",
                            "branch": "main",
                            "local_head": "head",
                            "remote_ref": "refs/heads/main",
                            "remote_before": "old-head",
                            "remote_after": "head",
                            "expected_origin": "",
                            "actual_origin": str(source),
                        }
                    },
                }
            ),
            sort_keys=True,
        ),
        feature=feature,
    )

    async def _unexpected_push(*args, **kwargs):
        del args, kwargs
        raise AssertionError("fresh source-push proof should skip push")

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "head\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_push_clones_to_source_root", _unexpected_push)
    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_source_push_gate_freshness_preserves_external_worktree_authority(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-source-push-external", slug="source-push-external")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "app"
    source = workspace_root / "source" / "app"
    _init_git_repo(source)
    subprocess.run(
        ["git", "worktree", "add", "-b", "source-push-external", str(repo), "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="app",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-1"],
                source_path=str(source),
                destination_path=str(repo),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "worktree-registry": registry.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    proof = implementation_module._finalize_source_push_proof(
        {
            "artifact_schema": "dag-source-push-proof-v1",
            "tree_digest": tree_digest,
            "repos_root": str(feature_root),
            "expected_origins": {"app": str(source)},
            "repos": {
                "app": {
                    "status": "pushed",
                    "tree_digest": tree_digest,
                    "repo": "app",
                    "branch": "main",
                    "local_head": "head",
                    "remote_ref": "refs/heads/main",
                    "remote_before": "old-head",
                    "remote_after": "head",
                    "expected_origin": str(source),
                    "actual_origin": str(source),
                }
            },
        }
    )
    await runner.artifacts.put("dag-gate:source-push", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "source-push",
        tree_digest,
    )
    await runner.artifacts.put(
        implementation_module._source_push_proof_key(),
        json.dumps(proof, sort_keys=True),
        feature=feature,
    )

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return str(source)
        if args == ("ls-remote", str(source), "refs/heads/main"):
            return "head\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    assert await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )


def test_source_push_rejects_unregistered_recursive_repo_without_registry(
    tmp_path,
):
    repos_root = tmp_path / "repos"
    nested = repos_root / "services" / "newsvc"
    _init_git_repo(nested)

    repo_dirs, failures = implementation_module._direct_source_push_repos(repos_root)

    assert repo_dirs == []
    assert any("not authorized without workspace registry authority" in item for item in failures)

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"services/newsvc"},
    )

    assert failures == []
    assert repo_dirs == [nested]


def test_checkpoint_no_dirty_proof_fails_closed_on_unregistered_nested_repo(
    tmp_path,
):
    repos_root = tmp_path / "repos"
    direct = repos_root / "app"
    nested = repos_root / "services" / "newsvc"
    _init_git_repo(direct)
    _init_git_repo(nested)
    feature = SimpleNamespace(id="feat-nested-proof", slug="nested-proof")
    runner = SimpleNamespace(artifacts=SimpleNamespace(), services={})

    proof = implementation_module._checkpoint_no_dirty_proof(
        runner,
        feature,
        feature_root=repos_root,
    )

    assert proof["clean"] is False
    assert proof["reason"] == "unauthorized_repo_discovery"
    assert any("not authorized without workspace registry authority" in item for item in proof["problems"])


@pytest.mark.asyncio
async def test_source_push_gate_freshness_revalidates_current_origin_authority(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-source-push-origin", slug="source-push-origin")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "app"
    source = workspace_root / "source" / "app"
    changed_source = workspace_root / "changed-source" / "app"
    _init_git_repo(repo)
    source.mkdir(parents=True)
    changed_source.mkdir(parents=True)
    registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="app",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-1"],
                source_path=str(changed_source),
                destination_path=str(repo),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "worktree-registry": registry.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )
    tree_digest = implementation_module._post_dag_gate_tree_digest(runner, feature)
    proof = implementation_module._finalize_source_push_proof(
        {
            "artifact_schema": "dag-source-push-proof-v1",
            "tree_digest": tree_digest,
            "repos_root": str(feature_root),
            "expected_origins": {"app": str(source)},
            "repos": {
                "app": {
                    "status": "pushed",
                    "tree_digest": tree_digest,
                    "repo": "app",
                    "branch": "main",
                    "local_head": "head",
                    "remote_ref": "refs/heads/main",
                    "remote_before": "old-head",
                    "remote_after": "head",
                    "expected_origin": str(source),
                    "actual_origin": str(source),
                }
            },
        }
    )
    await runner.artifacts.put("dag-gate:source-push", "approved", feature=feature)
    await implementation_module._record_post_dag_gate_proof(
        runner,
        feature,
        "source-push",
        tree_digest,
    )
    await runner.artifacts.put(
        implementation_module._source_push_proof_key(),
        json.dumps(proof, sort_keys=True),
        feature=feature,
    )

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "head"
        if args == ("remote", "get-url", "origin"):
            return str(source)
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return "head\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    assert not await implementation_module._post_dag_gate_is_fresh(
        runner,
        feature,
        "source-push",
        tree_digest,
    )


@pytest.mark.asyncio
async def test_verifier_runtime_failure_graph_preserves_provider_route() -> None:
    feature = SimpleNamespace(id="feat-runtime-route", slug="runtime-route")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    store = _install_durable_graph_store(runner)
    verdict = await implementation_module._record_dag_verifier_runtime_failure(
        runner,
        feature,
        0,
        "initial",
        RuntimeError("provider crashed"),
        runtime="codex",
        dag_sha256="d" * 64,
        projection_key="dag-verify:g0:initial",
    )

    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        "dag-verify:g0:initial",
        verdict,
        group_idx=0,
        dag_sha256="d" * 64,
    )
    payload = json.loads(runner.artifacts.store["dag-verify-graph:g0:initial"])

    raw_node = next(node for node in payload["nodes"] if node["kind"] == "raw_verifier")
    assert raw_node["status"] == "failed"
    assert raw_node["metadata"]["failure_class"] != "product_defect"
    assert raw_node["metadata"]["failure_type"] in {
        "provider_crash",
        "verifier_provider_crash",
    }
    assert payload["aggregate"]["blocking_failure_class"] == "verifier_provider"
    runtime_failure = json.loads(
        runner.artifacts.store["dag-runtime-failure:g0:verify-initial"]
    )
    assert runtime_failure["failure_class"] == "verifier_provider"
    assert runtime_failure["route"] == "retry_verifier"
    assert payload["durable_projection"]["persisted"] is True
    assert "dag-verify:g0:initial" not in runner.artifacts.store

    runner.artifacts.store.pop("dag-verify-graph:g0:initial")
    reloaded = await store.get_latest_verified_verification_graph_projection(
        feature_id=str(feature.id),
        projection_key="dag-verify:g0:initial",
        dag_sha256="d" * 64,
        group_idx=0,
        stage="initial",
    )
    assert reloaded is not None
    reloaded_payload = reloaded["graph"]["payload"]
    reloaded_raw_node = next(
        node for node in reloaded_payload["nodes"]
        if node["kind"] == "raw_verifier"
    )
    assert reloaded_raw_node["metadata"]["failure_class"] == "verifier_provider"


@pytest.mark.asyncio
async def test_expanded_lens_failure_graph_preserves_provider_route() -> None:
    feature = SimpleNamespace(id="feat-lens-runtime-route", slug="lens-runtime-route")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    payload = await implementation_module._record_dag_verification_graph_artifact(
        runner,
        feature,
        0,
        "dag-verify:g0:initial",
        Verdict(approved=True, summary="raw approved"),
        dag_sha256="d" * 64,
        required_lens_slugs=["acceptance-coverage"],
        lens_failures=[
            {
                "lens": "acceptance-coverage",
                "runtime": "codex",
                "error": "provider crashed",
            }
        ],
    )

    assert payload is not None
    lens_node = next(node for node in payload["nodes"] if node["kind"] == "expanded_lens")
    assert lens_node["status"] == "failed"
    assert lens_node["metadata"]["failure_class"] == "verifier_provider"
    assert lens_node["metadata"]["failure_type"] == "verifier_provider_crash"
    assert lens_node["metadata"]["route"] == "retry_verifier"
    assert payload["aggregate"]["blocking_failure_class"] == "verifier_provider"


@pytest.mark.asyncio
async def test_verification_graph_preserves_raw_lens_separation_on_lens_rejection() -> None:
    feature = SimpleNamespace(id="feat-raw-lens-separation", slug="raw-lens-separation")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    _install_durable_graph_store(runner)
    raw_verdict = Verdict(approved=True, summary="raw verifier approved")
    lens_verdict = Verdict(
        approved=False,
        summary="acceptance lens rejected",
        concerns=[
            Issue(
                severity="major",
                description="acceptance criteria evidence is missing",
            )
        ],
    )

    payload = await implementation_module._record_dag_verification_graph_artifact(
        runner,
        feature,
        0,
        "dag-verify:g0:initial",
        Verdict(
            approved=False,
            summary="merged lens rejection",
            concerns=list(lens_verdict.concerns),
        ),
        dag_sha256="d" * 64,
        raw_verifier_verdict=raw_verdict,
        lens_verdicts=[(SimpleNamespace(slug="acceptance-coverage"), lens_verdict)],
        required_lens_slugs=["acceptance-coverage"],
    )

    assert payload is not None
    raw_node = next(node for node in payload["nodes"] if node["name"] == "raw_verifier")
    lens_node = next(
        node for node in payload["nodes"]
        if node["name"] == "expanded_lens:acceptance-coverage"
    )
    assert raw_node["status"] == "approved"
    assert lens_node["status"] == "rejected"
    assert lens_node["metadata"]["failure_class"] == "product_defect"
    assert payload["aggregate"]["blocking_failure_class"] == "product_defect"
    assert payload["aggregate"]["raw_verdict_node_id"] == raw_node["id"]
    assert payload["aggregate"]["required_lens_node_ids"] == [lens_node["id"]]
    assert payload["aggregate_node"]["metadata"]["required_lens_slugs"] == [
        "acceptance-coverage"
    ]


@pytest.mark.asyncio
async def test_deterministic_preflight_failure_records_gate_not_raw_verifier() -> None:
    feature = SimpleNamespace(id="feat-preflight-fails", slug="preflight-fails")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    _install_durable_graph_store(runner)
    preflight_verdict = Verdict(
        approved=False,
        summary="Programmatic DAG preflight failed before model verification",
        concerns=[
            Issue(
                severity="blocker",
                description="duplicate task id detected before verifier dispatch",
            )
        ],
    )

    payload = await implementation_module._record_dag_verification_graph_artifact(
        runner,
        feature,
        0,
        "dag-verify:g0:initial",
        preflight_verdict,
        dag_sha256="d" * 64,
        deterministic_preflight_failed=True,
    )

    assert payload is not None
    preflight_node = next(
        node for node in payload["nodes"]
        if node["name"] == "programmatic_dag_preflight"
    )
    assert preflight_node["kind"] == "deterministic_gate"
    assert preflight_node["status"] == "rejected"
    assert preflight_node["output_hash"] == implementation_module._dag_verify_graph_digest(
        {
            "approved": False,
            "dispatch_verifier": False,
            "reason": "deterministic_preflight_failed",
        }
    )
    assert not any(node["kind"] == "raw_verifier" for node in payload["nodes"])
    assert payload["aggregate"]["raw_verdict_node_id"] is None
    assert payload["aggregate"]["blocking_failure_class"] == "deterministic_gate"
    assert payload["approved"] is False


@pytest.mark.asyncio
async def test_verification_graph_workflow_blocker_preserves_aggregate_route() -> None:
    feature = SimpleNamespace(id="feat-graph-blocker-route", slug="graph-blocker-route")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    graph_key = implementation_module._dag_verify_graph_artifact_key(0, "initial")
    await runner.artifacts.put(
        graph_key,
        json.dumps(
            {
                "approved": False,
                "aggregate": {
                    "approved": False,
                    "blocking_failure_class": "verifier_provider",
                },
                "aggregate_node": {
                    "status": "rejected",
                    "metadata": {
                        "blocking_failure_class": "verifier_provider",
                    },
                },
                "durable_projection": {"persisted": True},
                "nodes": [
                    {
                        "kind": "expanded_lens",
                        "name": "expanded_lens:acceptance-coverage",
                        "status": "failed",
                        "metadata": {
                            "failure_class": "verifier_provider",
                            "failure_type": "verifier_provider_crash",
                            "route": "retry_verifier",
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        feature=feature,
    )

    await implementation_module._record_dag_verification_graph_workflow_blocker(
        runner,
        feature,
        0,
        "dag-verify:g0:initial",
        "WORKFLOW_BLOCKER: graph aggregate rejected",
        dag_sha256="d" * 64,
    )

    blocker = json.loads(
        runner.artifacts.store["workflow-blocker:g0:verification-graph-initial"]
    )
    assert blocker["failure_class"] == "verifier_provider"
    assert blocker["failure_type"] == "verifier_provider_crash"
    assert blocker["route"] == "retry_verifier"
    assert blocker["retryable"] is True
    assert blocker["deterministic"] is False
    assert blocker["aggregate_blocking_failure_class"] == "verifier_provider"


@pytest.mark.asyncio
async def test_verification_graph_workflow_blocker_recovers_durable_rejected_graph_route() -> None:
    feature = SimpleNamespace(
        id="feat-graph-blocker-durable-recovery",
        slug="graph-blocker-durable-recovery",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    _install_durable_graph_store(runner)
    projection_key = "dag-verify:g0:initial"
    verdict = await implementation_module._record_dag_verifier_runtime_failure(
        runner,
        feature,
        0,
        "initial",
        RuntimeError("provider crashed"),
        runtime="codex",
        dag_sha256="d" * 64,
        projection_key=projection_key,
    )
    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        projection_key,
        verdict,
        group_idx=0,
        dag_sha256="d" * 64,
    )
    graph_key = implementation_module._dag_verify_graph_artifact_key(0, "initial")
    payload = json.loads(runner.artifacts.store[graph_key])
    assert payload["durable_projection"]["persisted"] is True
    runner.artifacts.store.pop(graph_key)

    await implementation_module._record_dag_verification_graph_workflow_blocker(
        runner,
        feature,
        0,
        projection_key,
        "WORKFLOW_BLOCKER: graph aggregate rejected",
        dag_sha256="d" * 64,
    )

    assert graph_key in runner.artifacts.store
    blocker = json.loads(
        runner.artifacts.store["workflow-blocker:g0:verification-graph-initial"]
    )
    assert blocker["failure_class"] == "verifier_provider"
    assert blocker["failure_type"] in {"provider_crash", "verifier_provider_crash"}
    assert blocker["route"] == "retry_verifier"
    assert blocker["retryable"] is True
    assert blocker["deterministic"] is False


@pytest.mark.asyncio
async def test_dispatch_port_surfaces_durable_duplicate_replay_recovery_evidence() -> None:
    recovery_evidence = {
        "durable": True,
        "heartbeat_stale": True,
        "heartbeat_evidence_id": 501,
    }

    class _Store:
        async def start_dispatch_attempt(self, request):
            row = SimpleNamespace(
                status="started",
                dispatcher_state="runtime_invoking",
                request_digest=request.request_digest,
                payload={
                    "metadata": {
                        "duplicate_replay_recovery_evidence": recovery_evidence
                    }
                },
            )
            return SimpleNamespace(attempt_id=42, attempt=row, created=False)

    request = SimpleNamespace(
        feature_id="feat-dispatch-recovery",
        dag_sha256="d" * 64,
        group_idx=1,
        task_id="TASK-1",
        task_name="Task",
        retry=1,
        retry_identity={},
        contract_ids=[],
        sandbox_id=None,
        workspace_snapshot_ids=[],
        base_commit_by_repo={},
        runtime_policy="codex",
        runtime_policy_digest="policy",
        actor_role="implementer",
        actor_metadata={},
        prior_evidence_ids=[],
        cancellation_token=None,
        request_digest="request-digest",
        idempotency_key="idem:dispatch:recovery",
    )

    record = await implementation_module._ExecutionControlDispatchJournalPort(
        _Store()
    ).start_dispatch_attempt(request)

    assert record.duplicate_replay_recovery_evidence == recovery_evidence


@pytest.mark.asyncio
async def test_dispatch_port_persists_recovery_evidence_from_runtime_failure_details() -> None:
    recovery_evidence = {
        "durable": True,
        "owner_stale": True,
        "owner_evidence_id": 601,
    }
    captured: list[object] = []

    class _Store:
        async def record_runtime_failure(self, evidence):
            captured.append(evidence)
            return SimpleNamespace(failure_id=77)

    class _Failure:
        failure_class = "runtime_provider"
        failure_type = "provider_replay_stale"
        retryable = True
        deterministic = False
        operator_required = False
        provider_request_id = "provider-replay"
        evidence_ids = [601]
        signature_hash = "runtime-replay-stale"
        runtime = "codex"
        provider_error_code = None
        terminal_reason = "provider_error"
        summary = "Recovered stale duplicate replay."
        details = {
            "duplicate_replay_recovery_evidence": recovery_evidence,
        }

        def model_copy(self, *, update):
            return SimpleNamespace(**update)

    await implementation_module._ExecutionControlDispatchJournalPort(
        _Store()
    ).record_runtime_failure(42, _Failure())

    assert captured
    payload = captured[0].payload
    assert payload["details"]["duplicate_replay_recovery_evidence"] == recovery_evidence
    assert payload["duplicate_replay_recovery_evidence"] == recovery_evidence


def test_commit_failure_deletion_only_forbidden_status_stays_commit_hygiene(tmp_path):
    repo = tmp_path / "iriai-studio"
    _init_git_repo(repo)
    forbidden_root = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    _write_forbidden_manifest(repo, forbidden_root)
    exc = implementation_module.WorkflowCommitError(
        "commit failed",
        [
            implementation_module.CommitRepoOutcome(
                repo_path=str(repo),
                repo_name="iriai-studio",
                message="fix",
                dirty=True,
                command=["git", "commit", "-m", "fix"],
                exit_code=1,
                stderr=f"{forbidden_root}/cardVariantRegistry.ts:1:1 stale deletion warning",
                status_after=f"D  {forbidden_root}/cardVariantRegistry.ts",
                error="git commit failed",
            )
        ],
    )

    issue = implementation_module._commit_failure_issue(exc, stage="retry-0")
    route = implementation_module._classify_dag_direct_repair_route(
        Verdict(approved=False, summary="commit failed", concerns=[issue])
    )

    assert "manifest-forbidden product cleanup" not in issue.description
    assert route.route == "commit_hygiene_focused"


def test_dag_direct_route_classifier_keeps_semantic_failures_on_normal_route():
    commit_only = Verdict(
        approved=False,
        summary="Group cannot checkpoint: commit failed",
        concerns=[
            Issue(
                severity="major",
                description="pre-commit/husky failed during retry-0",
                file="iriai-studio/src/App.test.tsx",
                line=12,
            )
        ],
    )
    mixed = Verdict(
        approved=False,
        summary="Group cannot checkpoint: commit failed and tests failed",
        concerns=[
            Issue(
                severity="major",
                description="pre-commit/husky failed during retry-0",
                file="iriai-studio/src/App.test.tsx",
                line=12,
            ),
            Issue(
                severity="major",
                description="acceptance coverage is fake-only",
                file="iriai-studio/src/App.test.tsx",
            ),
        ],
    )
    with_gap = Verdict(
        approved=False,
        summary="Group cannot checkpoint: commit failed",
        concerns=commit_only.concerns,
        gaps=[
            Gap(
                category="coverage",
                severity="major",
                description="AC coverage missing",
            )
        ],
    )
    repo_hygiene = Verdict(
        approved=False,
        summary="Group cannot checkpoint: commit failed",
        concerns=[
            Issue(
                severity="blocker",
                description="workflow repo hygiene blocker during retry-0",
                file="iriai-studio/src/webviews/dashboard/.git",
            )
        ],
    )

    assert (
        implementation_module._classify_dag_direct_repair_route(commit_only).route
        == "commit_hygiene_focused"
    )
    assert (
        implementation_module._classify_dag_direct_repair_route(mixed).route
        == "normal_verify_repair"
    )
    assert (
        implementation_module._classify_dag_direct_repair_route(with_gap).route
        == "normal_verify_repair"
    )
    repo_route = implementation_module._classify_dag_direct_repair_route(repo_hygiene)
    assert repo_route.route == "repo_hygiene_operator"
    assert repo_route.operator_required is True


def test_manifest_forbidden_cleanup_does_not_swallow_generic_product_defect():
    verdict = Verdict(
        approved=False,
        summary="Verifier found a product defect",
        concerns=[
            Issue(
                severity="major",
                description=(
                    "Product behavior is still wrong; see dag-task:TASK-7 "
                    "and source artifact:dag-task:TASK-7 for reproduction evidence."
                ),
                file="app/src/product.py",
            ),
            Issue(
                severity="major",
                description=(
                    "pre-commit/husky failed during retry-0; "
                    "manifest-forbidden product cleanup marker was found"
                ),
                file="app/src/generated/forbidden.py",
            ),
        ],
    )

    route = implementation_module._classify_dag_direct_repair_route(verdict)

    assert route.route == "normal_verify_repair"


@pytest.mark.asyncio
async def test_dag_direct_route_repeated_signature_blocks_spin():
    feature = SimpleNamespace(id="feat-repeat-route")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def write_artifact_bytes(self, key: str, data: bytes, metadata, *, feature=None):
            del feature
            self.store[key] = data.decode("utf-8", "surrogateescape")
            self.store[f"{key}.metadata"] = json.dumps(metadata, sort_keys=True)
            return len([name for name in self.store if name.startswith("dag-sandbox-patch:")])

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = Verdict(
        approved=False,
        summary="Group cannot checkpoint: commit failed",
        concerns=[
            Issue(
                severity="major",
                description="pre-commit/husky failed during retry-0",
                file="iriai-studio/src/App.test.tsx",
                line=12,
            )
        ],
    )
    route = implementation_module._classify_dag_direct_repair_route(verdict)
    await implementation_module._record_dag_direct_repair_route(
        runner,
        feature,
        31,
        0,
        route,
        status="selected",
        source_verdict_key="dag-verify:g31:retry-0",
        guardrail_decision="test",
    )
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g31:retry-0"]
    )
    assert route_payload["typed_failure_class"] == "commit_hygiene"
    assert route_payload["typed_failure_type"] == "commit_hook_failed"
    assert route_payload["typed_route_action"] == "run_commit_hygiene_repair"
    assert route_payload["route_decision"]["legacy_route"] == "commit_hygiene_focused"
    assert route_payload["route_decision"]["route"] == "run_commit_hygiene_repair"
    assert route_payload["retry_budget"]["remaining_attempts"] == 0
    await implementation_module._record_dag_direct_repair_route(
        runner,
        feature,
        31,
        1,
        route,
        status="blocked_repeat",
        source_verdict_key="dag-verify:g31:retry-1",
        guardrail_decision="test repeat",
    )
    repeated_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g31:retry-1"]
    )
    assert repeated_payload["typed_route_action"] == "quiesce"
    assert repeated_payload["route_decision"]["budget_exhausted"] is True

    assert await implementation_module._direct_route_repeated_signature(
        runner,
        feature,
        31,
        1,
        route,
    )
    repeated = implementation_module._repeated_direct_route_verdict(
        group_idx=31,
        retry=1,
        route=route,
    )
    assert repeated.approved is False
    assert repeated.concerns[0].severity == "blocker"
    assert repeated.concerns[0].file == "iriai-studio/src/App.test.tsx"
    assert repeated.concerns[0].line == 12


@pytest.mark.asyncio
async def test_unknown_legacy_direct_route_quiesces_in_typed_projection():
    feature = SimpleNamespace(id="feat-unknown-route")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    route = implementation_module.DagDirectRepairRoute(
        route="new_workflow_only_route",
        reason="fixture",
        signature="unknown-route-signature",
        target_files=["src/product.py"],
    )

    await implementation_module._record_dag_direct_repair_route(
        runner,
        feature,
        32,
        0,
        route,
        status="selected",
        source_verdict_key="dag-verify:g32:retry-0",
        guardrail_decision="test",
    )

    payload = json.loads(runner.artifacts.store["dag-direct-repair-route:g32:retry-0"])
    assert payload["typed_failure_class"] == "unknown"
    assert payload["typed_failure_type"] == "unclassified"
    assert payload["typed_route_action"] == "quiesce"


@pytest.mark.asyncio
async def test_manifest_direct_route_repeated_signature_blocks_retry_one():
    feature = SimpleNamespace(id="feat-repeat-manifest-route")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = Verdict(
        approved=False,
        summary="Group cannot checkpoint: commit failed",
        concerns=[
            Issue(
                severity="major",
                description=(
                    "pre-commit/husky failed during retry-0; "
                    "manifest-forbidden product cleanup marker was found"
                ),
                file="iriai-studio/src/generated/forbidden.ts",
            )
        ],
    )
    route = implementation_module._classify_dag_direct_repair_route(verdict)
    assert route.route == "manifest_forbidden_product_cleanup"
    await implementation_module._record_dag_direct_repair_route(
        runner,
        feature,
        31,
        0,
        route,
        status="selected",
        source_verdict_key="dag-verify:g31:retry-0",
        guardrail_decision="test",
    )
    payload = json.loads(runner.artifacts.store["dag-direct-repair-route:g31:retry-0"])
    assert payload["typed_failure_class"] == "contract_violation"
    assert payload["typed_failure_type"] == "forbidden_path_touched"
    assert payload["typed_route_action"] == "run_product_repair"
    assert payload["route_decision"]["repair_scope"]["source_verdict_key"] == (
        "dag-verify:g31:retry-0"
    )

    assert await implementation_module._direct_route_repeated_signature(
        runner,
        feature,
        31,
        1,
        route,
    )


@pytest.mark.asyncio
async def test_commit_repos_in_root_clean_repo_returns_empty_string(tmp_path):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)

    assert await implementation_module._commit_repos_in_root(repos_root, "noop") == ""


@pytest.mark.asyncio
async def test_commit_repos_in_root_dirty_repo_returns_commit_hash(tmp_path):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)
    (repo / "README.md").write_text("dirty success\n", encoding="utf-8")

    commit_hash = await implementation_module._commit_repos_in_root(
        repos_root,
        "test: commit dirty repo",
    )

    assert len(commit_hash) == 40
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert status == ""


@pytest.mark.asyncio
async def test_commit_repos_in_root_accepts_git_worktree_file_repo(tmp_path):
    feature_root = tmp_path / ".iriai" / "features" / "feat-worktree"
    source = feature_root / "source-repos" / "app"
    repos_root = feature_root / "lanes" / "lane-1" / "repos"
    worktree = repos_root / "app"
    _init_git_repo(source)
    subprocess.run(
        ["git", "worktree", "add", "-b", "lane-worktree", str(worktree), "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert (worktree / ".git").is_file()
    (worktree / "README.md").write_text("linked worktree dirty success\n", encoding="utf-8")

    commit_hash = await implementation_module._commit_repos_in_root(
        repos_root,
        "test: commit linked worktree repo",
        authorized_repos={"app"},
    )

    assert len(commit_hash) == 40
    assert implementation_module._discover_repo_roots_under(repos_root) == [worktree]
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert status == ""


@pytest.mark.asyncio
async def test_commit_repos_in_root_accepts_legacy_external_git_worktree_metadata(tmp_path):
    source = tmp_path / "legacy-source" / "app"
    repos_root = tmp_path / ".iriai" / "features" / "feat-legacy" / "lanes" / "lane-1" / "repos"
    worktree = repos_root / "app"
    _init_git_repo(source)
    subprocess.run(
        ["git", "worktree", "add", "-b", "legacy-lane-worktree", str(worktree), "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    (worktree / "README.md").write_text("legacy external worktree metadata\n", encoding="utf-8")

    commit_hash = await implementation_module._commit_repos_in_root(
        repos_root,
        "test: commit legacy external linked worktree repo",
        authorized_repos={"app"},
        authorized_source_roots={"app": str(source)},
    )

    assert len(commit_hash) == 40
    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"app"},
        authorized_source_roots={"app": str(source)},
    )
    assert failures == []
    assert repo_dirs == [worktree]


@pytest.mark.asyncio
async def test_external_git_worktree_metadata_requires_matching_source_authority(tmp_path):
    source = tmp_path / "legacy-source" / "app"
    wrong_source = tmp_path / "legacy-source" / "docs"
    repos_root = tmp_path / ".iriai" / "features" / "feat-legacy" / "lanes" / "lane-1" / "repos"
    worktree = repos_root / "app"
    _init_git_repo(source)
    _init_git_repo(wrong_source)
    subprocess.run(
        ["git", "worktree", "add", "-b", "legacy-lane-worktree", str(worktree), "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    (worktree / "README.md").write_text("valid but unauthorized external worktree\n", encoding="utf-8")

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"app"},
    )
    assert repo_dirs == []
    assert failures

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"app"},
        authorized_source_roots={"app": str(wrong_source)},
    )
    assert repo_dirs == []
    assert failures

    with pytest.raises(implementation_module.WorkflowCommitError):
        await implementation_module._commit_repos_in_root(
            repos_root,
            "test: reject unauthorized external linked worktree repo",
            authorized_repos={"app"},
            authorized_source_roots={"app": str(wrong_source)},
        )

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert "README.md" in status


def test_direct_source_push_repos_ignores_iriai_test_tmp_scratch(tmp_path):
    """A `.iriai-test-tmp/` scratch repo must not trip the workspace-authority
    guard.

    A concurrent test run (pytest basetemp rooted under a real feature root)
    transiently leaves unregistered git repos under `.iriai-test-tmp/`. The
    discovery must IGNORE them — otherwise `_feature_repos_clean_for_checkpoint_
    resume` reports a discovery failure, the resume freshness gate fails, and a
    validly-sealed group is judged "stale" and re-run forever (its `done`-
    replaced lanes can no longer re-enqueue). Observed on feature 8ac124d6
    group 78. Test scratch is never a canonical-mutation target, so ignoring it
    does not weaken the guard's protection of authorized canonical repos.
    """
    repos_root = tmp_path / "feature-root"
    app = repos_root / "app"
    _init_git_repo(app)
    # Transient scratch repo a concurrent backend pytest run left behind.
    scratch = repos_root / ".iriai-test-tmp" / "backend-pytest" / "sources" / "A"
    _init_git_repo(scratch)

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root, authorized_repos={"app"},
    )
    assert failures == []
    assert repo_dirs == [app]


@pytest.mark.asyncio
async def test_external_git_worktree_metadata_is_scoped_to_repo_rel(tmp_path):
    app_source = tmp_path / "source" / "app"
    docs_source = tmp_path / "source" / "docs"
    repos_root = tmp_path / ".iriai" / "features" / "feat-pooled" / "lanes" / "lane-1" / "repos"
    worktree = repos_root / "app"
    _init_git_repo(app_source)
    _init_git_repo(docs_source)
    docs_head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs_source,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "worktree", "add", "-b", "docs-linked-as-app", str(worktree), "HEAD"],
        cwd=docs_source,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    (worktree / "README.md").write_text("cross-rel external worktree must be rejected\n", encoding="utf-8")

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"app"},
        authorized_source_roots={
            "app": str(app_source),
            "docs": str(docs_source),
        },
    )

    assert repo_dirs == []
    assert failures

    with pytest.raises(implementation_module.WorkflowCommitError):
        await implementation_module._commit_repos_in_root(
            repos_root,
            "test: reject cross-rel external linked worktree repo",
            authorized_repos={"app"},
            authorized_source_roots={
                "app": str(app_source),
                "docs": str(docs_source),
            },
        )

    docs_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs_source,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert docs_head_after == docs_head_before


def test_git_worktree_file_requires_backlink_before_repo_discovery(tmp_path):
    repos_root = tmp_path / "repos"
    repo = repos_root / "app"
    outside_git_dir = tmp_path / "outside" / "app.git"
    repo.mkdir(parents=True)
    outside_git_dir.mkdir(parents=True)
    (outside_git_dir / "commondir").write_text(str(outside_git_dir), encoding="utf-8")
    (outside_git_dir / "gitdir").write_text(str(repo / ".git"), encoding="utf-8")
    (repo / ".git").write_text(f"gitdir: {outside_git_dir}\n", encoding="utf-8")

    assert not implementation_module._is_git_worktree_file(repo / ".git")

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"app"},
    )

    assert repo_dirs == []
    assert failures
    assert implementation_module._discover_repo_roots_under(repos_root) == []


def test_git_worktree_file_rejects_forged_external_metadata_layout(tmp_path):
    repos_root = tmp_path / ".iriai" / "features" / "feat-forged" / "lanes" / "lane-1" / "repos"
    repo = repos_root / "app"
    source = tmp_path / "source" / "app"
    forged_common = source / ".git"
    forged_git_dir = forged_common / "worktrees" / "app"
    repo.mkdir(parents=True)
    forged_git_dir.mkdir(parents=True)
    (forged_common / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (forged_common / "objects").mkdir(parents=True)
    (forged_git_dir / "commondir").write_text("../..", encoding="utf-8")
    (forged_git_dir / "gitdir").write_text(str(repo / ".git"), encoding="utf-8")
    (repo / ".git").write_text(f"gitdir: {forged_git_dir}\n", encoding="utf-8")

    repo_dirs, failures = implementation_module._direct_source_push_repos(
        repos_root,
        authorized_repos={"app"},
        authorized_source_roots={"app": str(source)},
    )

    assert repo_dirs == []
    assert failures


@pytest.mark.asyncio
async def test_commit_repos_in_root_commits_authorized_nested_repo(tmp_path):
    repos_root = tmp_path / "repos"
    nested = repos_root / "services" / "newsvc"
    _init_git_repo(nested)
    (nested / "README.md").write_text("nested dirty success\n", encoding="utf-8")

    commit_hash = await implementation_module._commit_repos_in_root(
        repos_root,
        "test: commit authorized nested repo",
        authorized_repos={"services/newsvc"},
    )

    assert len(commit_hash) == 40
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=nested,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert status == ""


@pytest.mark.asyncio
async def test_commit_repos_in_root_rejects_unregistered_nested_repo_before_mutation(tmp_path):
    repos_root = tmp_path / "repos"
    nested = repos_root / "services" / "newsvc"
    _init_git_repo(nested)
    (nested / "README.md").write_text("nested dirty blocked\n", encoding="utf-8")

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos_in_root(
            repos_root,
            "test: reject unregistered nested repo",
            authorized_repos={"app"},
        )

    failure = raised.value.failed_outcomes[0]
    assert failure.command == ["workflow-repo-authority-check"]
    assert "not registered in canonical workspace authority" in failure.stderr
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=nested,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert "README.md" in status


@pytest.mark.asyncio
async def test_commit_repos_in_root_rejects_dirty_optional_noop_repo_before_mutation(tmp_path):
    repos_root = tmp_path / "repos"
    docs = repos_root / "docs"
    _init_git_repo(docs)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (docs / "README.md").write_text("read-only dirty blocked\n", encoding="utf-8")

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos_in_root(
            repos_root,
            "test: reject read-only dirty repo",
            authorized_repos={"docs"},
            optional_noop_repos={"docs"},
        )

    failure = raised.value.failed_outcomes[0]
    assert failure.command == ["workflow-repo-authority-check"]
    assert "optional-noop repo is dirty" in failure.error
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert head_after == head_before


@pytest.mark.asyncio
async def test_commit_repos_in_root_preflights_all_noop_repos_before_any_commit(tmp_path):
    repos_root = tmp_path / "repos"
    app = repos_root / "app"
    docs = repos_root / "docs"
    _init_git_repo(app)
    _init_git_repo(docs)
    app_head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=app,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    docs_head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (app / "README.md").write_text("writable dirty should not commit yet\n", encoding="utf-8")
    (docs / "README.md").write_text("read-only dirty blocks all commits\n", encoding="utf-8")

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos_in_root(
            repos_root,
            "test: reject partial commit before noop failure",
            authorized_repos={"app", "docs"},
            optional_noop_repos={"docs"},
        )

    failure = raised.value.failed_outcomes[0]
    assert failure.command == ["workflow-repo-authority-check"]
    assert "optional-noop repo is dirty" in failure.error
    app_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=app,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    docs_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert app_head_after == app_head_before
    assert docs_head_after == docs_head_before
    app_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=app,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert "README.md" in app_status
    cached_diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=app,
    )
    assert cached_diff.returncode == 0


@pytest.mark.asyncio
async def test_commit_repos_in_root_rolls_back_prior_commit_when_later_repo_commit_fails(tmp_path):
    repos_root = tmp_path / "repos"
    app = repos_root / "app"
    docs = repos_root / "docs"
    _init_git_repo(app)
    _init_git_repo(docs)
    app_head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=app,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    docs_head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (app / "README.md").write_text("app commit must roll back\n", encoding="utf-8")
    (docs / "README.md").write_text("docs hook blocks transaction\n", encoding="utf-8")
    hook = docs / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "echo 'docs hook says no' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos_in_root(
            repos_root,
            "test: rollback partial multi-repo commit",
            authorized_repos={"app", "docs"},
        )

    assert raised.value.successful_hashes == []
    failure = raised.value.failed_outcomes[0]
    assert failure.repo_name == "docs"
    assert "docs hook says no" in failure.stderr
    app_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=app,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    docs_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert app_head_after == app_head_before
    assert docs_head_after == docs_head_before
    for repo in (app, docs):
        cached_diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo)
        assert cached_diff.returncode == 0
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        assert "README.md" in status


@pytest.mark.asyncio
async def test_commit_repos_uses_per_group_registry_before_mutating_read_only_repo(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-group-registry-noop", slug="group-registry-noop")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    docs = feature_root / "docs"
    source_docs = tmp_path / "sources" / "docs"
    _init_git_repo(docs)
    _init_git_repo(source_docs)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (docs / "README.md").write_text("read-only group dirty blocked\n", encoding="utf-8")
    registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="docs",
                action="read_only",
                role="source",
                read_only_task_ids=["TASK-1"],
                source_path=str(source_docs),
                destination_path=str(docs),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {"worktree-registry:g7": registry.model_dump_json()}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos(
            runner,
            feature,
            "test: read-only per-group registry blocks commit",
        )

    failure = raised.value.failed_outcomes[0]
    assert failure.command == ["workflow-repo-authority-check"]
    assert "optional-noop repo is dirty" in failure.error
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert head_after == head_before


@pytest.mark.asyncio
async def test_commit_repos_group_scope_does_not_use_other_group_writable_registry(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-cross-group-registry", slug="cross-group-registry")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    docs = feature_root / "docs"
    source_docs = tmp_path / "sources" / "docs"
    _init_git_repo(docs)
    _init_git_repo(source_docs)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (docs / "README.md").write_text("group 7 must remain read-only\n", encoding="utf-8")
    read_only_registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="docs",
                action="read_only",
                role="source",
                read_only_task_ids=["TASK-7"],
                source_path=str(source_docs),
                destination_path=str(docs),
            )
        ],
    )
    writable_registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="docs",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-8"],
                source_path=str(source_docs),
                destination_path=str(docs),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g7": read_only_registry.model_dump_json(),
                "worktree-registry:g8": writable_registry.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos(
            runner,
            feature,
            "test: group-scoped commit must not borrow writable authority",
            group_idx=7,
        )

    failure = raised.value.failed_outcomes[0]
    assert failure.command == ["workflow-repo-authority-check"]
    assert "optional-noop repo is dirty" in failure.error
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert head_after == head_before


@pytest.mark.asyncio
async def test_commit_repos_group_scope_ignores_global_writable_registry(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-global-registry", slug="global-registry")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    docs = feature_root / "docs"
    source_docs = tmp_path / "sources" / "docs"
    _init_git_repo(docs)
    _init_git_repo(source_docs)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    (docs / "README.md").write_text("group registry overrides global\n", encoding="utf-8")
    global_registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="docs",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-GLOBAL"],
                source_path=str(source_docs),
                destination_path=str(docs),
            )
        ],
    )
    group_registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="docs",
                action="read_only",
                role="source",
                read_only_task_ids=["TASK-7"],
                source_path=str(source_docs),
                destination_path=str(docs),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry": global_registry.model_dump_json(),
                "worktree-registry:g7": group_registry.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos(
            runner,
            feature,
            "test: group registry must override global writable authority",
            group_idx=7,
        )

    failure = raised.value.failed_outcomes[0]
    assert failure.command == ["workflow-repo-authority-check"]
    assert "optional-noop repo is dirty" in failure.error
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=docs,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert head_after == head_before


@pytest.mark.asyncio
async def test_commit_repos_commits_authorized_nested_repo_from_per_group_registry(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-group-registry-nested", slug="group-registry-nested")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    nested = feature_root / "services" / "newsvc"
    source_nested = tmp_path / "sources" / "newsvc"
    _init_git_repo(nested)
    _init_git_repo(source_nested)
    (nested / "README.md").write_text("nested group dirty success\n", encoding="utf-8")
    registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="services/newsvc",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-1"],
                source_path=str(source_nested),
                destination_path=str(nested),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {"worktree-registry:g7": registry.model_dump_json()}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)},
    )

    commit_hash = await implementation_module._commit_repos(
        runner,
        feature,
        "test: commit authorized per-group nested repo",
    )

    assert len(commit_hash) == 40
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=nested,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert status == ""


@pytest.mark.asyncio
async def test_commit_repos_in_root_rejects_symlinked_repos_root_before_mutation(tmp_path):
    outside_repos = tmp_path / "outside" / "repos"
    outside_repo = outside_repos / "app"
    _init_git_repo(outside_repo)
    (outside_repo / "README.md").write_text("outside dirty\n", encoding="utf-8")
    feature_root = tmp_path / ".iriai" / "features" / "symlink-feature"
    feature_root.mkdir(parents=True)
    repos_link = feature_root / "repos"
    repos_link.symlink_to(outside_repos, target_is_directory=True)

    with pytest.raises(implementation_module.WorkflowCommitError) as raised:
        await implementation_module._commit_repos_in_root(
            repos_link,
            "test: must not commit outside",
        )

    assert raised.value.failed_outcomes[0].command == ["workflow-repo-hygiene-check"]
    assert "workflow_repos_root_symlink" in raised.value.failed_outcomes[0].status_after
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=outside_repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert "README.md" in status


def test_checkpoint_no_dirty_proof_rejects_symlinked_repos_root(tmp_path):
    outside_repos = tmp_path / "outside" / "repos"
    outside_repo = outside_repos / "app"
    _init_git_repo(outside_repo)
    feature = SimpleNamespace(id="feat-symlink-proof", slug="symlink-proof")
    feature_root = tmp_path / ".iriai" / "features" / feature.slug
    feature_root.mkdir(parents=True)
    repos_link = feature_root / "repos"
    repos_link.symlink_to(outside_repos, target_is_directory=True)
    runner = SimpleNamespace(
        services={"workspace_manager": SimpleNamespace(_base=tmp_path)}
    )

    proof = implementation_module._checkpoint_no_dirty_proof(
        runner,
        feature,
        feature_root=repos_link,
    )

    assert proof["clean"] is False
    assert proof["reason"] == "unsafe_feature_root"
    assert proof["problems"][0]["reason"] == "workflow_repos_root_symlink"


@pytest.mark.asyncio
async def test_ensure_task_worktrees_rejects_nested_symlink_ancestor_before_scaffold(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(id="feat-nested-symlink", slug="nested-symlink")
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    link_parent = feature_root / "services"
    link_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (link_parent / "link").symlink_to(outside, target_is_directory=True)

    async def _fake_run_git(cwd: Path, *args: str) -> str:
        del cwd, args
        return ""

    monkeypatch.setattr(implementation_module, "_run_git", _fake_run_git)

    task = ImplementationTask(
        id="TASK-nested-symlink",
        name="Create escaped app",
        description="A nested repo path must not scaffold through a symlink ancestor.",
        repo_path="services/link/app",
        file_scope=[
            TaskFileScope(path="services/link/app/app.py", action="create"),
        ],
    )
    runner = SimpleNamespace(
        services={"workspace_manager": SimpleNamespace(_base=workspace_root)}
    )

    with pytest.raises(RuntimeError, match="symlink ancestor"):
        await implementation_module._ensure_task_worktrees(runner, feature, [task])

    assert not (outside / "app").exists()


@pytest.mark.asyncio
async def test_dag_checkpoint_commit_failure_blocks_group_and_writes_artifact(tmp_path):
    feature = SimpleNamespace(id="feat-commit-block", slug="commit-block")
    repos_root = tmp_path / ".iriai" / "features" / feature.slug / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "echo 'husky checkpoint failure' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    (repo / "README.md").write_text("dirty checkpoint\n", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key)

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=tmp_path)},
    )
    store = _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    task = ImplementationTask(id="TASK-1", name="A", description="A")
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=["README.md"],
    )

    async def _approved(*_args, **_kwargs):
        return Verdict(approved=True, summary="approved")

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        12,
        [task],
        [result],
        [result],
        implementation_module.HandoverDoc(),
        repos_root,
        "primary",
        "secondary",
        "primary",
        verify_fn=_approved,
    )

    assert approved is False
    assert "SANDBOX_WORKFLOW_BLOCKER" in failure
    assert "pre-commit/husky failed" in failure
    assert "husky checkpoint failure" in failure
    assert "dag-group:12" not in runner.artifacts.store
    payload = json.loads(runner.artifacts.store["dag-commit-failure:g12:checkpoint"])
    assert payload["failed_repo_count"] == 1
    assert "husky checkpoint failure" in payload["outcomes"][0]["stderr"]
    assert "dag-verify:g12:checkpoint-commit" not in runner.artifacts.store
    checkpoint_graph = json.loads(
        runner.artifacts.store["dag-verify-graph:g12:checkpoint-commit"]
    )
    assert checkpoint_graph["approved"] is False
    assert checkpoint_graph["durable_projection"]["persisted"] is True
    reloaded = await store.get_latest_verified_verification_graph_projection(
        feature_id=str(feature.id),
        projection_key="dag-verify:g12:checkpoint-commit",
        dag_sha256="",
        group_idx=12,
        stage="checkpoint-commit",
    )
    assert reloaded is not None
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g12:checkpoint-commit"]
    )
    assert route_payload["route"] == "commit_hygiene_focused"
    assert route_payload["status"] == "selected"


@pytest.mark.asyncio
async def test_dag_retry_commit_failure_skips_reverify_and_becomes_next_issue(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-retry-commit-block", slug="retry-commit-block")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def write_artifact_bytes(self, key: str, data: bytes, metadata, *, feature=None):
            del feature
            self.store[key] = data.decode("utf-8", "surrogateescape")
            self.store[f"{key}.metadata"] = json.dumps(metadata, sort_keys=True)
            return len([name for name in self.store if name.startswith("dag-sandbox-patch:")])

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="Pre-commit hygiene failed",
                    evidence=["commit hook output is deterministic"],
                    affected_files=["README.md"],
                    proposed_approach="Fix hygiene before committing",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="TASK-1",
                    summary="attempted hygiene repair",
                    files_modified=["README.md"],
                )
            raise AssertionError(f"unexpected task: {task!r}")

    verify_calls = 0

    async def _verify_once(*_args, **_kwargs):
        nonlocal verify_calls
        verify_calls += 1
        return Verdict(
            approved=False,
            summary="needs a fix",
            concerns=[Issue(severity="major", description="fix needed")],
        )

    async def _failing_commit(*_args, **_kwargs):
        raise implementation_module.WorkflowCommitError(
            "commit failed",
            [
                implementation_module.CommitRepoOutcome(
                    repo_path=str(tmp_path),
                    repo_name="repo",
                    message="fix",
                    dirty=True,
                    command=["git", "commit", "-m", "fix"],
                    exit_code=1,
                    stderr="husky retry failure",
                    status_before="M README.md",
                    status_after="M README.md",
                    error="git commit failed",
                )
            ],
        )

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_commit_repos", _failing_commit)

    runner = _Runner()
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        13,
        [ImplementationTask(id="TASK-1", name="A", description="A")],
        [ImplementationResult(task_id="TASK-1", summary="done", files_modified=["README.md"])],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_once,
    )

    assert approved is False
    assert verify_calls == 1
    assert "husky retry failure" in failure
    assert RootCauseAnalysis in runner.output_types
    assert ImplementationResult in runner.output_types
    retry_verdict = json.loads(runner.artifacts.store["dag-verify:g13:retry-0"])
    assert retry_verdict["approved"] is False
    assert "pre-commit/husky failed" in retry_verdict["concerns"][0]["description"]
    payload = json.loads(runner.artifacts.store["dag-commit-failure:g13:retry-0"])
    assert "husky retry failure" in payload["outcomes"][0]["stderr"]


@pytest.mark.asyncio
async def test_dag_verify_rca_runtime_failure_blocks_before_product_repair(
    tmp_path,
    monkeypatch,
):
    feature_root = tmp_path / ".iriai" / "features" / "feat-rca-runtime" / "repos"
    feature_root.mkdir(parents=True)
    feature = SimpleNamespace(id="feat-rca-runtime", slug="feat-rca-runtime")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "workspace_manager": SimpleNamespace(_base=tmp_path),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                raise RuntimeError("provider unavailable")
            if task.output_type is ImplementationResult:
                raise AssertionError("product repair must not run after RCA runtime failure")
            raise AssertionError(f"unexpected task: {task!r}")

    async def _verify_once(*_args, **_kwargs):
        return Verdict(
            approved=False,
            summary="needs a semantic fix",
            concerns=[Issue(severity="major", description="business logic is wrong")],
        )

    async def _legacy_repair_without_sandbox(*_args, **_kwargs):
        return None

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(
        implementation_module,
        "_bind_repair_sandbox",
        _legacy_repair_without_sandbox,
    )
    runner = _Runner()

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        14,
        [ImplementationTask(id="TASK-1", name="A", description="A")],
        [ImplementationResult(task_id="TASK-1", summary="done", files_modified=["README.md"])],
        [],
        implementation_module.HandoverDoc(),
        feature_root,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_once,
    )

    assert approved is False
    assert "SANDBOX_WORKFLOW_BLOCKER" in failure
    assert "RCA runtime failed before product repair dispatch" in failure
    assert RootCauseAnalysis in runner.output_types
    assert ImplementationResult not in runner.output_types
    payload = json.loads(runner.artifacts.store["dag-runtime-failure:g14:rca-retry-0"])
    assert payload["failure_type"] == "provider_internal_error"
    assert payload["legacy_failure_type"] == "provider_crash"
    assert payload["route"] == "retry_dispatch"
    assert payload["route_decision"]["failure_type"] == "provider_internal_error"
    assert payload["route_decision"]["legacy_failure_type"] == "provider_crash"
    assert payload["route_decision"]["route"] == "retry_dispatch"
    assert payload["retry_budget"]["remaining_attempts"] == 1
    assert payload["blocked_before_product_repair"] is True


@pytest.mark.asyncio
async def test_dag_verify_fix_runtime_failure_records_typed_blocker(
    tmp_path,
    monkeypatch,
):
    feature_root = tmp_path / ".iriai" / "features" / "feat-fix-runtime" / "repos"
    feature_root.mkdir(parents=True)
    feature = SimpleNamespace(id="feat-fix-runtime", slug="feat-fix-runtime")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "workspace_manager": SimpleNamespace(_base=tmp_path),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="semantic bug",
                    evidence=["verifier feedback"],
                    affected_files=["README.md"],
                    proposed_approach="fix semantic bug",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                raise RuntimeError("repair provider unavailable")
            raise AssertionError(f"unexpected task: {task!r}")

    async def _verify_once(*_args, **_kwargs):
        return Verdict(
            approved=False,
            summary="needs a semantic fix",
            concerns=[Issue(severity="major", description="business logic is wrong")],
        )

    async def _legacy_repair_without_sandbox(*_args, **_kwargs):
        return None

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(
        implementation_module,
        "_bind_repair_sandbox",
        _legacy_repair_without_sandbox,
    )
    runner = _Runner()

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        15,
        [ImplementationTask(id="TASK-1", name="A", description="A")],
        [ImplementationResult(task_id="TASK-1", summary="done", files_modified=["README.md"])],
        [],
        implementation_module.HandoverDoc(),
        feature_root,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_once,
    )

    assert approved is False
    assert "SANDBOX_WORKFLOW_BLOCKER" in failure
    assert "Repair runtime failed before product repair completion" in failure
    assert RootCauseAnalysis in runner.output_types
    assert ImplementationResult in runner.output_types
    payload = json.loads(runner.artifacts.store["dag-runtime-failure:g15:fix-retry-0"])
    assert payload["failure_class"] == "runtime_provider"
    assert payload["failure_type"] == "provider_internal_error"
    assert payload["legacy_failure_type"] == "provider_crash"
    assert payload["route"] == "retry_dispatch"
    assert payload["route_decision"]["failure_type"] == "provider_internal_error"
    assert payload["route_decision"]["route"] == "retry_dispatch"
    assert payload["blocked_during_product_repair"] is True
    assert "repair provider unavailable" in payload["error"]


@pytest.mark.asyncio
async def test_commit_only_retry_routes_directly_without_expanded_verify_or_rca(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-direct-commit-route", slug="direct-commit-route")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            self.prompts.append(task.prompt)
            if task.output_type is RootCauseAnalysis:
                raise AssertionError("commit-only route must skip RCA")
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="VERIFY-COMMIT-HYGIENE",
                    summary="removed forbidden unicode marker",
                    files_modified=[
                        "iriai-studio/src/webviews/projectSurface/src/chat/"
                        "__tests__/ChatSidepaneShell.test.tsx"
                    ],
                )
            raise AssertionError(f"unexpected task: {task!r}")

    task = ImplementationTask(id="TASK-1", name="Task", description="Task")
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=[
            "iriai-studio/src/webviews/projectSurface/src/chat/__tests__/"
            "ChatSidepaneShell.test.tsx"
        ],
    )
    verify_calls = 0

    async def _verify(*_args, **_kwargs):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 1:
            return Verdict(
                approved=False,
                summary="Group 38 cannot checkpoint: commit failed during retry-0",
                concerns=[
                    Issue(
                        severity="major",
                        description=(
                            "pre-commit/husky failed during retry-0; fix repo "
                            "hygiene before checkpoint. Hook/output excerpt: "
                            "unexpected unicode character U+00A7"
                        ),
                        file=(
                            "iriai-studio/src/webviews/projectSurface/src/chat/"
                            "__tests__/ChatSidepaneShell.test.tsx"
                        ),
                        line=149,
                    )
                ],
            )
        return Verdict(approved=True, summary="clean")

    async def _no_preflight(*_args, **_kwargs):
        return None

    async def _no_sanitize(*_args, **_kwargs):
        return _args[4]

    async def _no_result_reconcile(
        runner,
        feature,
        group_idx,
        retry_label,
        group_tasks,
        *,
        results,
        verify_results_context,
        all_results,
        repair_results,
        feature_root,
    ):
        del runner, feature, group_idx, retry_label, group_tasks, repair_results, feature_root
        return implementation_module.DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            {},
        )

    async def _no_spec_reconcile(*args, **kwargs):
        del kwargs
        return implementation_module.DagTaskSpecReconcileOutcome(args[4], {})

    _unexpected_expanded = _allow_checkpoint_lenses_only(
        "commit-only route must skip expanded verify before focused repair"
    )

    async def _unexpected_parallel(*args, **kwargs):
        del args, kwargs
        raise AssertionError("commit-only route must skip parallel repair")

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(implementation_module, "_run_dag_group_preflight", _no_preflight)
    monkeypatch.setattr(implementation_module, "_sanitize_dag_repair_results", _no_sanitize)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_results", _no_result_reconcile)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_specs", _no_spec_reconcile)
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _unexpected_expanded)
    monkeypatch.setattr(implementation_module, "_attempt_parallel_dag_repair", _unexpected_parallel)
    async def _commit_repos_success(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_repos_success)

    async def _checkpoint(*_args, **_kwargs):
        return "a" * 40

    monkeypatch.setattr(implementation_module, "_commit_group", _checkpoint)

    runner = _Runner()
    _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        38,
        [task],
        [result],
        [result],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
    )

    assert approved is True, failure
    assert failure == ""
    assert verify_calls == 2
    assert RootCauseAnalysis not in runner.output_types
    assert ImplementationResult in runner.output_types
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g38:retry-0"]
    )
    assert route_payload["route"] == "commit_hygiene_focused"
    assert route_payload["skip_expanded_verify"] is True
    assert "dag-repair-expanded-verify:g38:retry-0" not in runner.artifacts.store
    assert route_payload["target_files"] == [
        "iriai-studio/src/webviews/projectSurface/src/chat/__tests__/"
        "ChatSidepaneShell.test.tsx:149"
    ]
    assert "Read the context" in runner.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_verdict_key",
    [
        "dag-verify:g39:implementation-commit",
        "dag-verify:g39:enhancement-commit",
    ],
)
async def test_commit_verdict_routes_directly_without_initial_verify_and_projects_graph(
    tmp_path,
    monkeypatch,
    initial_verdict_key,
):
    feature = SimpleNamespace(id="feat-implementation-commit-route", slug="impl-commit")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                raise AssertionError("implementation commit route must skip RCA")
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="VERIFY-COMMIT-HYGIENE",
                    summary="fixed husky hygiene",
                    files_modified=[
                        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/"
                        "browser/workflowTab/chat/test/browser/cardVariantRegistry.test.ts"
                    ],
                )
            raise AssertionError(f"unexpected task: {task!r}")

    task = ImplementationTask(id="TASK-1", name="Task", description="Task")
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=[
            "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
            "workflowTab/chat/test/browser/cardVariantRegistry.test.ts"
        ],
    )
    initial_verdict = Verdict(
        approved=False,
        summary="Group 39 cannot checkpoint: commit failed during implementation",
        concerns=[
            Issue(
                severity="major",
                description=(
                    "pre-commit/husky failed during implementation; fix repo "
                    "hygiene before checkpoint."
                ),
                file=(
                    "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
                    "workflowTab/chat/test/browser/cardVariantRegistry.test.ts"
                ),
                line=32,
            )
        ],
    )
    verify_calls = 0
    preflight_calls = 0

    async def _verify(*_args, **_kwargs):
        nonlocal verify_calls
        verify_calls += 1
        return Verdict(approved=True, summary="clean")

    async def _preflight(*_args, **_kwargs):
        nonlocal preflight_calls
        preflight_calls += 1
        return None

    async def _no_sanitize(*_args, **_kwargs):
        return _args[4]

    async def _no_result_reconcile(
        runner,
        feature,
        group_idx,
        retry_label,
        group_tasks,
        *,
        results,
        verify_results_context,
        all_results,
        repair_results,
        feature_root,
    ):
        del runner, feature, group_idx, retry_label, group_tasks, repair_results, feature_root
        return implementation_module.DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            {},
        )

    async def _no_spec_reconcile(*args, **kwargs):
        del kwargs
        return implementation_module.DagTaskSpecReconcileOutcome(args[4], {})

    _unexpected_expanded = _allow_checkpoint_lenses_only(
        "implementation commit route must skip expanded verify before focused repair"
    )

    async def _unexpected_parallel(*args, **kwargs):
        del args, kwargs
        raise AssertionError("commit-only route must skip parallel repair")

    async def _commit_repos_success(*_args, **_kwargs):
        return ""

    async def _checkpoint(*_args, **_kwargs):
        return "b" * 40

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(implementation_module, "_run_dag_group_preflight", _preflight)
    monkeypatch.setattr(implementation_module, "_sanitize_dag_repair_results", _no_sanitize)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_results", _no_result_reconcile)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_specs", _no_spec_reconcile)
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _unexpected_expanded)
    monkeypatch.setattr(implementation_module, "_attempt_parallel_dag_repair", _unexpected_parallel)
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_repos_success)
    monkeypatch.setattr(implementation_module, "_commit_group", _checkpoint)
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": "",
        },
    )

    runner = _Runner()
    store = _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [task],
        [result],
        [result],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        initial_verdict=initial_verdict,
        initial_verdict_key=initial_verdict_key,
    )

    assert approved is True, failure
    assert failure == ""
    assert verify_calls == 1
    assert preflight_calls == 1
    assert RootCauseAnalysis not in runner.output_types
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g39:retry-0"]
    )
    assert route_payload["route"] == "commit_hygiene_focused"
    assert route_payload["source_verdict_key"] == initial_verdict_key
    assert initial_verdict_key not in runner.artifacts.store
    stage = initial_verdict_key.rsplit(":", 1)[-1]
    graph = json.loads(runner.artifacts.store[f"dag-verify-graph:g39:{stage}"])
    assert graph["projection_key"] == initial_verdict_key
    assert graph["durable_projection"]["persisted"] is True
    assert store.projections[0]["projection_key"] == initial_verdict_key


@pytest.mark.asyncio
async def test_manifest_forbidden_commit_route_prompts_cleanup_not_suppression(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-manifest-commit-route", slug="manifest-commit")
    repo = tmp_path / "iriai-studio"
    _init_git_repo(repo)
    forbidden_root = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    _write_forbidden_manifest(repo, forbidden_root)
    exc = implementation_module.WorkflowCommitError(
        "commit failed",
        [
            implementation_module.CommitRepoOutcome(
                repo_path=str(repo),
                repo_name="iriai-studio",
                message="fix",
                dirty=True,
                command=["git", "commit", "-m", "fix"],
                exit_code=1,
                stderr=f"{forbidden_root}/test/browser/cardVariantRegistry.test.ts:32:1 warning",
                status_after=f"A  {forbidden_root}/test/browser/cardVariantRegistry.test.ts",
                error="git commit failed",
            )
        ],
    )
    initial_verdict = implementation_module._commit_failure_verdict(
        exc,
        group_idx=39,
        stage="implementation",
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.prompts: list[str] = []
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                raise AssertionError("manifest-forbidden commit route must skip RCA")
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="VERIFY-MANIFEST-CLEANUP",
                    summary="ported coverage and removed forbidden file",
                    files_modified=["iriai-studio/src/webviews/projectSurface/src/chat/index.ts"],
                )
            raise AssertionError(f"unexpected task: {task!r}")

    async def _verify(*_args, **_kwargs):
        return Verdict(approved=True, summary="clean")

    async def _preflight(*_args, **_kwargs):
        return None

    async def _no_sanitize(*args, **_kwargs):
        return args[4]

    async def _no_result_reconcile(
        runner,
        feature,
        group_idx,
        retry_label,
        group_tasks,
        *,
        results,
        verify_results_context,
        all_results,
        repair_results,
        feature_root,
    ):
        del runner, feature, group_idx, retry_label, group_tasks, repair_results, feature_root
        return implementation_module.DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            {},
        )

    async def _no_spec_reconcile(*args, **kwargs):
        del kwargs
        return implementation_module.DagTaskSpecReconcileOutcome(args[4], {})

    _unexpected_expanded = _allow_checkpoint_lenses_only(
        "manifest-forbidden route must skip expanded verify before focused repair"
    )

    async def _unexpected_parallel(*args, **kwargs):
        del args, kwargs
        raise AssertionError("manifest-forbidden route must skip parallel repair")

    async def _commit_repos_success(*_args, **_kwargs):
        return ""

    async def _checkpoint(*_args, **_kwargs):
        return "c" * 40

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(implementation_module, "_run_dag_group_preflight", _preflight)
    monkeypatch.setattr(implementation_module, "_sanitize_dag_repair_results", _no_sanitize)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_results", _no_result_reconcile)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_specs", _no_spec_reconcile)
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _unexpected_expanded)
    monkeypatch.setattr(implementation_module, "_attempt_parallel_dag_repair", _unexpected_parallel)
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_repos_success)
    monkeypatch.setattr(implementation_module, "_commit_group", _checkpoint)
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": "",
        },
    )

    runner = _Runner()
    _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done", files_modified=[])],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        initial_verdict=initial_verdict,
        initial_verdict_key="dag-verify:g39:implementation-commit",
    )

    assert approved is True
    assert failure == ""
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g39:retry-0"]
    )
    assert route_payload["route"] == "manifest_forbidden_product_cleanup"
    assert route_payload["skip_expanded_verify"] is True
    assert route_payload["operator_required"] is False
    index_path = Path(runner.prompts[0].split("`")[1])
    prompt_context = "\n".join(
        path.read_text(encoding="utf-8")
        for path in index_path.parent.glob("g39-fix-0-*.md")
    )
    assert "Do NOT fix this by adding `.eslint-ignore`" in prompt_context
    assert "Suppression is invalid" in prompt_context


@pytest.mark.asyncio
async def test_implement_dag_blocks_canonical_mutation_until_durable_merge_queue(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-impl-commit-catch",
        slug="impl-commit-catch",
        metadata={},
    )
    repos_root = tmp_path / ".iriai" / "features" / feature.slug / "repos"
    _init_git_repo(repos_root / "app")
    (repos_root / "app" / "src").mkdir(exist_ok=True)
    (repos_root / "app" / "src" / "example.ts").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repos_root / "app", check=True)
    subprocess.run(["git", "commit", "-qm", "add example"], cwd=repos_root / "app", check=True)

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "workspace_manager": SimpleNamespace(_base=tmp_path),
                "test_allow_sandbox_patch_promotion_bridge": True,
            }

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is ImplementationResult:
                sandbox_cwd = Path(task.actor.role.metadata["workspace_override"])
                (sandbox_cwd / "src" / "example.ts").write_text(
                    "changed\n",
                    encoding="utf-8",
                )
                return ImplementationResult(
                    task_id="TASK-1",
                    summary="implemented task",
                    files_modified=["app/src/example.ts"],
                )
            raise AssertionError(f"unexpected task: {task!r}")

    async def _noop(*_args, **_kwargs):
        return None

    async def _commit_must_not_run(*_args, **_kwargs):
        raise AssertionError("implementation dispatch must wait for durable merge queue")

    async def _dirty_patch_evidence(_repo):
        status_text = " M src/example.ts\n"
        return (
            True,
            implementation_module.hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
            [],
            ["src/example.ts"],
            [],
            {},
            status_text,
            "",
        )

    routed: dict[str, object] = {}

    async def _fake_verify_and_fix_group(
        runner,
        feature,
        group_idx,
        group_tasks,
        results,
        all_results,
        handover,
        feature_root,
        impl_runtime,
        review_runtime,
        rca_runtime=None,
        **kwargs,
    ):
        del runner, feature, group_idx, group_tasks, results, all_results
        del handover, feature_root, impl_runtime, review_runtime, rca_runtime
        routed.update({
            "initial_verdict": kwargs.get("initial_verdict"),
            "initial_verdict_key": kwargs.get("initial_verdict_key"),
        })
        raise AssertionError("repair routing must not run before durable merge queue")

    monkeypatch.setattr(implementation_module, "dag_path_canonicalization_enabled", lambda: False)
    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop)
    monkeypatch.setattr(
        implementation_module,
        "_dag_workspace_writeability_problems",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_must_not_run)
    monkeypatch.setattr(implementation_module, "_git_patch_evidence", _dirty_patch_evidence)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _fake_verify_and_fix_group)

    dag = implementation_module.ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-1",
                name="Task",
                description="Task",
                repo_path="app",
                files=["app/src/example.ts"],
            )
        ],
        execution_order=[["TASK-1"]],
    )

    impl_text, failure, _handover = await implementation_module._implement_dag(
        _Runner(),
        feature,
        dag,
    )

    assert "implemented task" in impl_text
    # Slice 08e-2: the implementation worker no longer commits to canonical
    # repos — it enqueues onto the durable merge queue. With no Postgres pool
    # configured the enqueue fails closed (no silent fallback to the legacy
    # canonical commit); _commit_repos and repair routing are still skipped.
    assert "durable merge queue" in failure
    assert "legacy canonical commit is disabled" in failure
    assert routed == {}


@pytest.mark.asyncio
async def test_single_rca_fix_verify_commit_failure_records_artifact_not_crash(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-single-commit-block", slug="single-commit-block")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="Aria label does not match the plan",
                    evidence=["component and test pin a non-spec string"],
                    affected_files=["repo/src/ChatSidepaneShell.tsx"],
                    proposed_approach="Update the literal and test",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="VERIFY-FAIL-COMMIT",
                    summary="Updated the literal and test",
                    files_modified=[
                        "repo/src/ChatSidepaneShell.tsx",
                        "repo/src/ChatSidepaneShell.test.tsx",
                    ],
                )
            if task.output_type is Verdict:
                raise AssertionError("reverify must not run after commit failure")
            raise AssertionError(f"unexpected task: {task!r}")

    async def _failing_commit(*_args, **_kwargs):
        raise implementation_module.WorkflowCommitError(
            "commit failed",
            [
                implementation_module.CommitRepoOutcome(
                    repo_path=str(tmp_path),
                    repo_name="repo",
                    message="fix",
                    dirty=True,
                    command=["git", "commit", "-m", "fix"],
                    exit_code=1,
                    stderr="Unexpected unicode character",
                    status_before="M repo/src/ChatSidepaneShell.test.tsx",
                    status_after="M repo/src/ChatSidepaneShell.test.tsx",
                    error="git commit failed",
                )
            ],
        )

    monkeypatch.setattr(implementation_module, "_commit_repos", _failing_commit)

    runner = _Runner()
    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        "Verifier failed on ChatSidepaneShell aria-label",
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        "",
        bug_id="VERIFY-FAIL-COMMIT",
        attempt_number=1,
        workspace_root=None,
    )

    assert attempt.re_verify_result == "FAIL"
    assert "Commit failed before reverify" in attempt.fix_applied
    assert RootCauseAnalysis in runner.output_types
    assert ImplementationResult in runner.output_types
    assert Verdict not in runner.output_types
    artifact_key = "bug-commit-failure:verify:VERIFY-FAIL-COMMIT:attempt-1:fix"
    payload = json.loads(runner.artifacts.store[artifact_key])
    assert "Unexpected unicode character" in payload["outcomes"][0]["stderr"]
    reverify = json.loads(runner.artifacts.store["bug-reverify:verify:VERIFY-FAIL-COMMIT"])
    assert reverify["approved"] is False
    assert "pre-commit/husky failed" in reverify["concerns"][0]["description"]


@pytest.mark.asyncio
async def test_single_rca_fix_verify_runtime_failure_records_typed_blocker():
    feature = SimpleNamespace(id="feat-single-rca-runtime", slug="single-rca-runtime")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                raise RuntimeError("provider unavailable")
            if task.output_type is ImplementationResult:
                raise AssertionError("product repair must not run after RCA runtime failure")
            raise AssertionError(f"unexpected task: {task!r}")

    runner = _Runner()
    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        "Verifier failed before repair",
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        "",
        bug_id="VERIFY-RUNTIME",
        attempt_number=3,
        rca_runtime="primary",
    )

    assert attempt.re_verify_result == "INFRA_RETRY"
    assert "SANDBOX_WORKFLOW_BLOCKER" not in attempt.fix_applied
    assert RootCauseAnalysis in runner.output_types
    assert ImplementationResult not in runner.output_types
    payload = json.loads(
        runner.artifacts.store["bug-rca-runtime-failure:verify:VERIFY-RUNTIME"]
    )
    assert payload["failure_type"] == "provider_internal_error"
    assert payload["legacy_failure_type"] == "provider_crash"
    assert payload["route"] == "retry_dispatch"
    assert payload["route_decision"]["failure_type"] == "provider_internal_error"
    assert payload["route_decision"]["route"] == "retry_dispatch"
    assert payload["route_retry"] == 0
    assert payload["lane_id"].startswith("bug-rca:verify:bug-rca:")
    assert payload["blocked_before_product_repair"] is True
    assert "workflow-blocker:bug-rca:verify:VERIFY-RUNTIME" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_multi_issue_diagnose_triage_runtime_failure_records_typed_blocker():
    feature = SimpleNamespace(id="feat-triage-runtime", slug="triage-runtime")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                raise RuntimeError("triage provider unavailable")
            if task.output_type is ImplementationResult:
                raise AssertionError("product repair must not run after triage runtime failure")
            raise AssertionError(f"unexpected task: {task!r}")

    runner = _Runner()
    attempts = await implementation_module._diagnose_and_fix(
        runner,
        feature,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="alpha broken", file="pkg/a.py"),
                Issue(severity="major", description="beta broken", file="pkg/b.py"),
            ],
        ),
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        [],
        itertools.count(1),
        rca_runtime="primary",
    )

    assert len(attempts) == 1
    assert attempts[0].re_verify_result == "INFRA_RETRY"
    assert "SANDBOX_WORKFLOW_BLOCKER" not in attempts[0].fix_applied
    assert BugTriage in runner.output_types
    assert ImplementationResult not in runner.output_types
    payload = json.loads(
        runner.artifacts.store["bug-rca-runtime-failure:verify:VERIFY-TRIAGE-RUNTIME-1"]
    )
    assert payload["failure_type"] == "provider_internal_error"
    assert payload["legacy_failure_type"] == "provider_crash"
    assert payload["route"] == "retry_dispatch"
    assert payload["route_decision"]["failure_type"] == "provider_internal_error"
    assert payload["route_decision"]["route"] == "retry_dispatch"
    assert payload["blocked_before_product_repair"] is True
    assert payload["context"] == "bug-triage"
    assert "workflow-blocker:bug-rca:verify:VERIFY-TRIAGE-RUNTIME-1" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_multi_issue_diagnose_parallel_rca_runtime_failure_records_typed_blocker():
    feature = SimpleNamespace(id="feat-rca-runtime", slug="rca-runtime")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-A",
                        likely_root_cause="alpha",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-B",
                        likely_root_cause="beta",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is ImplementationResult:
                raise AssertionError("product repair must not run after RCA runtime failure")
            raise AssertionError(f"unexpected direct task: {task!r}")

        async def parallel(self, tasks, feature):
            del tasks, feature
            raise RuntimeError("rca provider unavailable")

    runner = _Runner()
    attempts = await implementation_module._diagnose_and_fix(
        runner,
        feature,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="alpha broken", file="pkg/a.py"),
                Issue(severity="major", description="beta broken", file="pkg/b.py"),
            ],
        ),
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        [],
        itertools.count(1),
        rca_runtime="primary",
    )

    assert len(attempts) == 1
    assert attempts[0].re_verify_result == "INFRA_RETRY"
    assert "SANDBOX_WORKFLOW_BLOCKER" not in attempts[0].fix_applied
    assert BugTriage in runner.output_types
    assert ImplementationResult not in runner.output_types
    payload = json.loads(
        runner.artifacts.store["bug-rca-runtime-failure:verify:VERIFY-RCA-RUNTIME-1"]
    )
    assert payload["failure_type"] == "provider_internal_error"
    assert payload["legacy_failure_type"] == "provider_crash"
    assert payload["route"] == "retry_dispatch"
    assert payload["route_decision"]["failure_type"] == "provider_internal_error"
    assert payload["route_decision"]["route"] == "retry_dispatch"
    assert payload["blocked_before_product_repair"] is True
    assert payload["context"] == "bug-rca-parallel"
    assert "workflow-blocker:bug-rca:verify:VERIFY-RCA-RUNTIME-1" not in runner.artifacts.store


def test_dag_expanded_verify_merges_and_dedupes_lens_findings():
    base = Verdict(
        approved=False,
        summary="normal failed",
        concerns=[
            Issue(
                severity="major",
                description="import fails",
                file="pkg/app.py",
            ),
        ],
    )
    lens_specs = implementation_module._dag_verify_lens_specs()
    lens_verdict = Verdict(
        approved=False,
        summary="lens found more",
        concerns=[
            Issue(
                severity="major",
                description="import fails",
                file="pkg/app.py",
            ),
            Issue(
                severity="blocker",
                description="runtime registration is missing",
                file="pkg/runtime.py",
            ),
        ],
        gaps=[
            Gap(
                category="coverage",
                severity="major",
                description="owned AC is not exercised",
            ),
        ],
    )

    merged = implementation_module._merge_dag_expanded_verify_verdicts(
        base,
        [(lens_specs[1], lens_verdict)],
    )

    assert merged.approved is False
    assert [concern.description for concern in merged.concerns] == [
        "import fails",
        "[Runtime Composition Lens] runtime registration is missing",
    ]
    assert merged.gaps[0].description == (
        "[Runtime Composition Lens] owned AC is not exercised"
    )


@pytest.mark.asyncio
async def test_run_expanded_dag_verify_lenses_records_successes_and_failures(tmp_path):
    feature = SimpleNamespace(id="feat-expanded-verify", slug="expanded-verify")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}
            self.prompts: list[str] = []
            self.actor_runtimes: dict[str, str | None] = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.prompts.append(task.prompt)
            self.actor_runtimes[task.actor.name] = task.actor.role.metadata.get("runtime")
            if "contract-protocol" in task.actor.name:
                raise RuntimeError("tooling timed out")
            if "acceptance-coverage" in task.actor.name:
                return Verdict(
                    approved=False,
                    summary="missing gate",
                    concerns=[
                        Issue(
                            severity="major",
                            description="AC-1 is not covered",
                            file="pkg/test_app.py",
                        ),
                    ],
                )
            return Verdict(approved=True, summary="clean")

    runner = _Runner()
    base = Verdict(
        approved=False,
        summary="normal failed",
        concerns=[Issue(severity="major", description="normal concern")],
    )

    merged = await implementation_module._run_expanded_dag_verify_lenses(
        runner,
        feature,
        3,
        0,
        base,
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="implemented",
                files_modified=["pkg/app.py"],
            ),
        ],
        ["pkg/app.py"],
        [
            ImplementationTask(
                id="TASK-1",
                name="Task",
                description="Do the thing",
                requirement_ids=["REQ-1"],
                verification_gates=["AC-1"],
            ),
        ],
        feature_root=tmp_path,
    )

    lens_keys = [
        key
        for key in runner.artifacts.store
        if key.startswith("dag-repair-lens:g3:")
    ]
    assert len(lens_keys) == 6
    assert "dag-repair-lens:g3:contract-protocol:retry-0" in runner.artifacts.store
    contract_payload = json.loads(
        runner.artifacts.store["dag-repair-lens:g3:contract-protocol:retry-0"]
    )
    assert contract_payload["status"] == "failed"
    assert contract_payload["runtime"] == "secondary"
    assert "tooling timed out" in contract_payload["error"]
    acceptance_payload = json.loads(
        runner.artifacts.store["dag-repair-lens:g3:acceptance-coverage:retry-0"]
    )
    assert acceptance_payload["status"] == "completed"
    assert acceptance_payload["runtime"] == "secondary"
    assert acceptance_payload["verdict"]["approved"] is False
    assert "dag-repair-expanded-verify:g3:retry-0" in runner.artifacts.store
    expanded_payload = json.loads(
        runner.artifacts.store["dag-repair-expanded-verify:g3:retry-0"]
    )
    successful_runtimes = {
        item["lens"]: item["runtime"]
        for item in expanded_payload["successful_lenses"]
    }
    assert successful_runtimes["build-dependency"] == "primary"
    assert successful_runtimes["runtime-composition"] == "primary"
    assert successful_runtimes["acceptance-coverage"] == "secondary"
    assert successful_runtimes["security-boundary"] == "primary"
    assert successful_runtimes["regression-downstream"] == "primary"
    assert merged.approved is False
    assert any(
        concern.description == "[Acceptance Coverage Lens] AC-1 is not covered"
        for concern in merged.concerns
    )
    assert len(runner.prompts) == 6
    assert all("read-only verifier lens" in prompt for prompt in runner.prompts)
    assert runner.actor_runtimes[
        "verifier-dag-lens-g3-r0-acceptance-coverage"
    ] == "secondary"
    assert runner.actor_runtimes[
        "verifier-dag-lens-g3-r0-build-dependency"
    ] == "primary"


@pytest.mark.asyncio
async def test_dag_group_preflight_reports_structural_blockers(tmp_path):
    feature = SimpleNamespace(id="feat-preflight", slug="preflight")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    task_a = ImplementationTask(
        id="TASK-1",
        name="A",
        description="A",
        dependencies=["TASK-2", "TASK-MISSING"],
        verification_gates=["AC-1", "AC-1", "BAD-GATE"],
    )
    task_b = ImplementationTask(id="TASK-2", name="B", description="B")
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=["missing.py"],
    )

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        4,
        "initial",
        [task_a, task_b],
        [result],
        feature_root=tmp_path,
        known_task_ids={"TASK-1", "TASK-2"},
    )

    assert isinstance(verdict, Verdict)
    assert verdict.approved is False
    assert any("same execution wave" in issue.description for issue in verdict.concerns)
    assert any("unknown dependency" in issue.description for issue in verdict.concerns)
    assert any("BAD-GATE" in issue.description for issue in verdict.concerns)
    assert any("missing.py" in issue.description for issue in verdict.concerns)
    payload = json.loads(runner.artifacts.store["dag-repair-preflight:g4:retry-initial"])
    assert payload["approved"] is False
    assert payload["repairs"][0]["field"] == "verification_gates"
    assert task_a.verification_gates == ["AC-1", "BAD-GATE"]


def _worktree_alias_registry(feature, feature_root: Path) -> str:
    return implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(feature_root.parent),
        feature_root=str(feature_root),
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="iriai-studio-backend",
                action="copy",
                role="execution",
                writable_task_ids=["TASK-1"],
                canonical_path=str(feature_root / "iriai-studio-backend"),
            )
        ],
        complete=True,
    ).model_dump_json()


def _make_alias_repos(
    feature_root: Path,
    *,
    same_content: bool = True,
    create_alias: bool = True,
) -> None:
    canonical = (
        feature_root
        / "iriai-studio-backend"
        / "iriai_studio_backend"
        / "workflow_worker"
        / "messaging"
    )
    alias = (
        feature_root
        / "iriai-studio-backend-wt"
        / "iriai_studio_backend"
        / "workflow_worker"
        / "messaging"
    )
    canonical.mkdir(parents=True)
    (feature_root / "iriai-studio-backend" / ".git").mkdir()
    (canonical / "store.py").write_text("canonical\n", encoding="utf-8")
    if create_alias:
        alias.mkdir(parents=True)
        (feature_root / "iriai-studio-backend-wt" / ".git").mkdir()
        (alias / "store.py").write_text(
            "canonical\n" if same_content else "alias divergent\n",
            encoding="utf-8",
        )


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_rewrites_registry_backed_worktree_alias(tmp_path):
    feature = SimpleNamespace(id="feat-alias-sanitize", slug="alias-sanitize")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root)

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root)
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=[
            "iriai-studio-backend-wt/iriai_studio_backend/"
            "workflow_worker/messaging/store.py"
        ],
    )

    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        48,
        0,
        [result],
        feature_root,
        "test",
    )

    assert sanitized[0].files_modified == [
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    ]
    payload = json.loads(
        runner.artifacts.store["dag-repair-result-sanitize:g48:retry-0"]
    )
    assert payload["worktree_alias_rewritten_count"] == 1
    assert payload["alias_map"] == {
        "iriai-studio-backend-wt": "iriai-studio-backend"
    }


@pytest.mark.asyncio
async def test_worktree_alias_guard_reconciles_stale_dag_task_metadata(tmp_path):
    feature = SimpleNamespace(id="feat-alias-guard", slug="alias-guard")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root)
    canonical_path = (
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    alias_path = (
        "iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    stale = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=[alias_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root),
                "dag-task:TASK-1": stale.model_dump_json(),
            }
            self.next_id = 1

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": self.next_id, "value": value}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.next_id += 1
            self.store[key] = value

    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
        files=[canonical_path],
    )
    runner = SimpleNamespace(artifacts=_Artifacts())

    approved, report = await implementation_module._run_worktree_alias_pre_dispatch_guard(
        runner,
        feature,
        48,
        [task],
        feature_root=feature_root,
    )

    assert approved is True
    assert report["dag_task_problems_before"][0]["reason"] == "worktree_alias_path"
    replacement = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-1"]
    )
    assert replacement.files_modified == [canonical_path]
    assert "dag-worktree-alias-preflight:g48:initial-dispatch" in runner.artifacts.store


@pytest.mark.asyncio
async def test_worktree_alias_guard_reconciles_metadata_when_alias_repo_absent(tmp_path):
    feature = SimpleNamespace(id="feat-alias-absent", slug="alias-absent")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, create_alias=False)
    canonical_path = (
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    alias_path = (
        "iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    stale = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=[alias_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root),
                "dag-task:TASK-1": stale.model_dump_json(),
            }
            self.next_id = 1

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": self.next_id, "value": value}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.next_id += 1
            self.store[key] = value

    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
        files=[canonical_path],
    )
    runner = SimpleNamespace(artifacts=_Artifacts())

    approved, report = await implementation_module._run_worktree_alias_pre_dispatch_guard(
        runner,
        feature,
        48,
        [task],
        feature_root=feature_root,
    )

    assert approved is True
    assert report["dag_task_problems_before"][0]["exists_on_disk"] is False
    replacement = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-1"]
    )
    assert replacement.files_modified == [canonical_path]


@pytest.mark.asyncio
async def test_worktree_alias_guard_rewrites_safe_task_spec_metadata_in_memory(tmp_path):
    feature = SimpleNamespace(id="feat-alias-task-spec", slug="alias-task-spec")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, create_alias=False)
    canonical_path = (
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    alias_path = (
        "iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py"
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root)
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            return None

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
        files=[alias_path],
    )
    runner = SimpleNamespace(artifacts=_Artifacts())

    approved, report = await implementation_module._run_worktree_alias_pre_dispatch_guard(
        runner,
        feature,
        48,
        [task],
        feature_root=feature_root,
    )

    assert approved is True
    assert task.files == [canonical_path]
    assert report["task_spec_repairs"][0]["canonical"] == canonical_path
    assert report["task_spec_problems_after"] == []


@pytest.mark.asyncio
async def test_worktree_alias_guard_blocks_divergent_alias_without_operator(tmp_path):
    feature = SimpleNamespace(id="feat-alias-divergent", slug="alias-divergent")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, same_content=False)
    canonical_path = (
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    alias_path = (
        "iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py"
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root)
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            return None

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
        files=[alias_path],
    )
    runner = SimpleNamespace(artifacts=_Artifacts())

    approved, report = await implementation_module._run_worktree_alias_pre_dispatch_guard(
        runner,
        feature,
        48,
        [task],
        feature_root=feature_root,
    )

    assert approved is False
    assert report["operator_required"] is False
    assert report["blockers"][0]["canonical_path"] == canonical_path
    assert report["blockers"][0]["worktree_alias_divergent"] is True


@pytest.mark.asyncio
async def test_worktree_alias_map_does_not_rewrite_legitimate_wt_repo(tmp_path):
    feature = SimpleNamespace(id="feat-real-wt", slug="real-wt")
    feature_root = tmp_path / "repos"
    repo = feature_root / "client-wt" / "src"
    repo.mkdir(parents=True)
    (feature_root / "client-wt" / ".git").mkdir()
    (repo / "index.ts").write_text("export {};\n", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g7": implementation_module.WorktreeRegistry(
                    feature_id=feature.id,
                    feature_slug=feature.slug,
                    workspace_root=str(feature_root.parent),
                    feature_root=str(feature_root),
                    repos=[
                        implementation_module.WorktreeRegistryRepo(
                            repo_path="client-wt",
                            action="copy",
                            role="execution",
                            writable_task_ids=["TASK-1"],
                        )
                    ],
                    complete=True,
                ).model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(artifacts=_Artifacts())
    alias_map = await implementation_module._worktree_alias_map_for_group(
        runner,
        feature,
        7,
        feature_root,
    )
    category, normalized = implementation_module._classify_dag_repair_path(
        "client-wt/src/index.ts",
        implementation_module._dag_candidate_file_roots(feature_root),
        feature_root,
        alias_map,
    )

    assert alias_map == {}
    assert category == "product"
    assert normalized == "client-wt/src/index.ts"


@pytest.mark.asyncio
async def test_worktree_alias_map_does_not_rewrite_registered_wt_sibling(tmp_path):
    feature = SimpleNamespace(id="feat-wt-collision", slug="wt-collision")
    feature_root = tmp_path / "repos"
    for repo_name in ("client", "client-wt"):
        repo = feature_root / repo_name / "src"
        repo.mkdir(parents=True)
        (feature_root / repo_name / ".git").mkdir()
        (repo / "index.ts").write_text(f"export const repo = '{repo_name}';\n", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g7": implementation_module.WorktreeRegistry(
                    feature_id=feature.id,
                    feature_slug=feature.slug,
                    workspace_root=str(feature_root.parent),
                    feature_root=str(feature_root),
                    repos=[
                        implementation_module.WorktreeRegistryRepo(
                            repo_path="client",
                            action="copy",
                            role="execution",
                            writable_task_ids=["TASK-1"],
                        ),
                        implementation_module.WorktreeRegistryRepo(
                            repo_path="client-wt",
                            action="copy",
                            role="execution",
                            writable_task_ids=["TASK-2"],
                        ),
                    ],
                    complete=True,
                ).model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(artifacts=_Artifacts())
    alias_map = await implementation_module._worktree_alias_map_for_group(
        runner,
        feature,
        7,
        feature_root,
    )

    assert alias_map == {}


@pytest.mark.asyncio
async def test_worktree_alias_map_ignores_traversal_registry_paths(tmp_path):
    feature = SimpleNamespace(id="feat-alias-traversal", slug="alias-traversal")
    feature_root = tmp_path / "repos"
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (tmp_path / "outside-wt").mkdir()

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g7": implementation_module.WorktreeRegistry(
                    feature_id=feature.id,
                    feature_slug=feature.slug,
                    workspace_root=str(tmp_path),
                    feature_root=str(feature_root),
                    repos=[
                        implementation_module.WorktreeRegistryRepo(
                            repo_path="../outside",
                            action="copy",
                            role="execution",
                            writable_task_ids=["TASK-1"],
                        )
                    ],
                    complete=True,
                ).model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(artifacts=_Artifacts())

    assert await implementation_module._worktree_alias_map_for_group(
        runner,
        feature,
        7,
        feature_root,
    ) == {}


@pytest.mark.asyncio
async def test_worktree_alias_map_ignores_absolute_registry_paths(tmp_path):
    feature = SimpleNamespace(id="feat-alias-absolute-registry", slug="alias-absolute-registry")
    feature_root = tmp_path / "repos"
    (feature_root / "client").mkdir(parents=True)
    (feature_root / "client-wt").mkdir()

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g7": implementation_module.WorktreeRegistry(
                    feature_id=feature.id,
                    feature_slug=feature.slug,
                    workspace_root=str(tmp_path),
                    feature_root=str(feature_root),
                    repos=[
                        implementation_module.WorktreeRegistryRepo(
                            repo_path="/client",
                            action="copy",
                            role="execution",
                            writable_task_ids=["TASK-1"],
                        )
                    ],
                    complete=True,
                ).model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(artifacts=_Artifacts())

    assert await implementation_module._worktree_alias_map_for_group(
        runner,
        feature,
        7,
        feature_root,
    ) == {}


def test_alias_existing_product_match_does_not_guess_by_basename(tmp_path):
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, create_alias=False)
    unrelated = feature_root / "other-repo" / "src"
    unrelated.mkdir(parents=True)
    (feature_root / "other-repo" / ".git").mkdir()
    (unrelated / "missing_store.py").write_text("wrong target\n", encoding="utf-8")
    roots = implementation_module._dag_candidate_file_roots(feature_root)
    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
    )
    stale = ImplementationResult(
        task_id="TASK-1",
        summary="stale",
        files_modified=[
            "iriai-studio-backend-wt/iriai_studio_backend/"
            "workflow_worker/messaging/missing_store.py"
        ],
    )

    candidate, records = implementation_module._dag_existing_product_match_candidate(
        task,
        stale,
        [stale],
        roots,
        feature_root,
        [],
        [],
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )

    assert candidate is None
    assert records[0]["status"] == "skipped_registry_alias_requires_exact_canonical_path"


def test_worktree_alias_path_info_rejects_traversal_suffix(tmp_path):
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, create_alias=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "store.py").write_text("outside\n", encoding="utf-8")

    info = implementation_module._worktree_alias_path_info(
        "iriai-studio-backend-wt/../../outside/store.py",
        implementation_module._dag_candidate_file_roots(feature_root),
        feature_root,
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )

    assert info is not None
    assert info.category == "worktree_alias_unresolved"
    absolute_info = implementation_module._worktree_alias_path_info(
        "/iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py",
        implementation_module._dag_candidate_file_roots(feature_root),
        feature_root,
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )
    assert absolute_info is None
    other = feature_root / "other-repo"
    other.mkdir()
    (other / "store.py").write_text("outside alias target\n", encoding="utf-8")
    absolute_traversal_info = implementation_module._worktree_alias_path_info(
        str(feature_root / "iriai-studio-backend-wt" / ".." / "other-repo" / "store.py"),
        implementation_module._dag_candidate_file_roots(feature_root),
        feature_root,
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )
    assert absolute_traversal_info is not None
    assert absolute_traversal_info.category == "worktree_alias_unresolved"
    current_dir_info = implementation_module._worktree_alias_path_info(
        "./iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py",
        implementation_module._dag_candidate_file_roots(feature_root),
        feature_root,
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )
    assert current_dir_info is not None
    assert current_dir_info.category == "worktree_alias_rewritten"
    repeated_separator_info = implementation_module._worktree_alias_path_info(
        ".//iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py",
        implementation_module._dag_candidate_file_roots(feature_root),
        feature_root,
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )
    assert repeated_separator_info is not None
    assert repeated_separator_info.category == "worktree_alias_rewritten"


def test_traversal_alias_origin_does_not_fall_through_to_basename_guess(tmp_path):
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, create_alias=False)
    unrelated = feature_root / "other-repo" / "outside"
    unrelated.mkdir(parents=True)
    (feature_root / "other-repo" / ".git").mkdir()
    (unrelated / "store.py").write_text("wrong target\n", encoding="utf-8")
    roots = implementation_module._dag_candidate_file_roots(feature_root)
    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
    )
    stale = ImplementationResult(
        task_id="TASK-1",
        summary="stale",
        files_modified=["iriai-studio-backend-wt/../../other-repo/outside/store.py"],
    )

    candidate, records = implementation_module._dag_existing_product_match_candidate(
        task,
        stale,
        [stale],
        roots,
        feature_root,
        [],
        [],
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )

    assert candidate is None
    assert records[0]["status"] == "skipped_registry_alias_requires_exact_canonical_path"

    absolute_dot = stale.model_copy(update={
        "files_modified": ["/./iriai-studio-backend-wt/../../other-repo/outside/store.py"]
    })
    candidate, records = implementation_module._dag_existing_product_match_candidate(
        task,
        absolute_dot,
        [absolute_dot],
        roots,
        feature_root,
        [],
        [],
        {"iriai-studio-backend-wt": "iriai-studio-backend"},
    )

    assert candidate is None
    assert records[0]["status"] == "skipped_registry_alias_requires_exact_canonical_path"


@pytest.mark.asyncio
async def test_worktree_alias_guard_keeps_divergent_dag_task_blocked(tmp_path):
    feature = SimpleNamespace(id="feat-divergent-dag-task", slug="divergent-dag-task")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, same_content=False)
    canonical_path = (
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    alias_path = (
        "iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    stale = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=[alias_path, canonical_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root),
                "dag-task:TASK-1": stale.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 1, "value": value}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
        files=[canonical_path],
    )
    runner = SimpleNamespace(artifacts=_Artifacts())

    approved, report = await implementation_module._run_worktree_alias_pre_dispatch_guard(
        runner,
        feature,
        48,
        [task],
        feature_root=feature_root,
    )

    assert approved is False
    assert report["operator_required"] is False
    assert report["non_reconcilable_dag_task_problems"][0]["worktree_alias_divergent"] is True
    assert ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-1"]
    ).files_modified == [alias_path, canonical_path]


@pytest.mark.asyncio
async def test_worktree_alias_guard_blocks_source_artifact_alias_before_dispatch(tmp_path):
    feature = SimpleNamespace(id="feat-source-alias", slug="source-alias")
    feature_root = tmp_path / "repos"
    _make_alias_repos(feature_root, create_alias=False)
    canonical_path = (
        "iriai-studio-backend/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    alias_path = (
        "iriai-studio-backend-wt/iriai_studio_backend/workflow_worker/messaging/store.py"
    )
    artifact_root = tmp_path / "artifact-root"
    fragment_dir = artifact_root / "subfeatures" / "sf" / "dag-fragments"
    fragment_dir.mkdir(parents=True)
    fragment_dir.joinpath("slice-1.json").write_text(
        json.dumps({
            "tasks": [
                {
                    "id": "TASK-1",
                    "name": "Task",
                    "description": "Task",
                    "repo_path": "iriai-studio-backend",
                    "files": [alias_path],
                }
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "worktree-registry:g48": _worktree_alias_registry(feature, feature_root)
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            return None

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Mirror:
        def feature_dir(self, feature_id: str) -> str:
            assert feature_id == feature.id
            return str(artifact_root)

    task = ImplementationTask(
        id="TASK-1",
        name="Task",
        description="Task",
        repo_path="iriai-studio-backend",
        subfeature_id="sf",
        files=[canonical_path],
    )
    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": _Mirror()},
    )

    approved, report = await implementation_module._run_worktree_alias_pre_dispatch_guard(
        runner,
        feature,
        48,
        [task],
        feature_root=feature_root,
    )

    assert approved is False
    assert report["operator_required"] is False
    assert report["source_artifact_problems"][0]["reason"] == "worktree_alias_source_artifact"
    assert report["blockers"][0]["canonical_path"] == canonical_path


@pytest.mark.asyncio
async def test_dag_group_preflight_uses_raw_verdict_for_checkpoint_gate(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-ledger-raw", slug="ledger-raw", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id="TASK-1", name="Task", description="Task")
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=["missing.py"],
    )
    resolved = FindingLedger(
        findings=[
            FindingRecord(
                id="F-001",
                source="verify",
                description=(
                    "TASK-1 reports changed file that is missing from the "
                    "feature workspace; source artifact: dag-task:TASK-1; "
                    "path: missing.py"
                ),
                file="missing.py",
                severity="major",
                status="resolved",
            )
        ]
    )
    runner.artifacts.store["finding-ledger"] = resolved.model_dump_json()

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 0)

    async def _unexpected_commit(*args, **kwargs):  # pragma: no cover - failure path
        del args, kwargs
        raise AssertionError("raw failing preflight must not checkpoint")

    async def _unexpected_verify(*args, **kwargs):  # pragma: no cover - failure path
        del args, kwargs
        raise AssertionError("stale-context preflight must block verifier dispatch")

    monkeypatch.setattr(implementation_module, "_commit_group", _unexpected_commit)
    monkeypatch.setattr(implementation_module, "_verify", _unexpected_verify)

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        37,
        [task],
        [result],
        [result],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        known_task_ids={"TASK-1"},
    )

    assert approved is False
    assert "missing.py" in failure
    assert "dag-group:37" not in runner.artifacts.store
    raw_verify = json.loads(runner.artifacts.store["dag-verify:g37:initial"])
    assert raw_verify["approved"] is False
    assert raw_verify["concerns"]
    preflight = json.loads(runner.artifacts.store["dag-repair-preflight:g37:retry-initial"])
    assert preflight["approved"] is False
    assert preflight["concerns"]


@pytest.mark.asyncio
async def test_dag_checkpoint_requires_approved_verification_graph_proof(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-graph-approved", slug="graph-approved", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_ok(*args, **kwargs):
        del args, kwargs
        return Verdict(approved=True, summary="graph approved")

    async def _commit_ok(*args, **kwargs):
        del args, kwargs
        return "commit-graph-proof"

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"test_allow_legacy_repair_without_sandbox": True},
    )
    store = _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    monkeypatch.setattr(implementation_module, "_commit_group", _commit_ok)

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        70,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_ok,
        dag_sha256="dag-sha-graph",
    )

    assert approved is True
    assert failure == ""
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g70:initial"])
    assert graph["approved"] is True
    assert graph["projection_key"] == "dag-verify:g70:initial"
    assert graph["compatibility_projection"]["source_kind"] == "aggregate_verdict"
    assert graph["proof"]["projection_keys"] == ["dag-verify:g70:initial"]
    assert graph["durable_projection"]["persisted"] is True
    assert graph["durable_projection"]["evidence_edge_ids"]
    assert graph["lineage"]["task_ids"] == ["TASK-1"]
    assert graph["lineage"]["result_refs"][0]["task_id"] == "TASK-1"
    assert graph["nodes"][0]["metadata"]["source"] == "slice06_checkpoint_preflight"
    assert graph["nodes"][0]["input_hash"]
    assert store.projections
    assert "dag-verify:g70:initial" not in runner.artifacts.store
    assert store.projected_checkpoints
    assert getattr(store.projected_checkpoints[0], "projection_key") == "dag-group:70"
    checkpoint = json.loads(runner.artifacts.store["dag-group:70"])
    assert checkpoint["commit_hash"] == "commit-graph-proof"
    gate_proof = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:70"])
    assert gate_proof["persisted"] is True
    assert gate_proof["merge_gate"]["persisted"] is True
    assert gate_proof["checkpoint_gate"]["persisted"] is True

    runner.artifacts.store.pop("dag-verify-graph:g70:initial")
    graph_recovery_failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        70,
        "dag-verify:g70:initial",
        Verdict(approved=True, summary="graph approved"),
        dag_sha256="dag-sha-graph",
    )

    assert graph_recovery_failure == ""
    recovered = json.loads(runner.artifacts.store["dag-verify-graph:g70:initial"])
    assert recovered["proof"]["proof_digest"] == graph["proof"]["proof_digest"]
    assert recovered["durable_projection"]["persisted"] is True


@pytest.mark.asyncio
async def test_checkpoint_gate_proof_failure_preserves_typed_route_metadata(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-checkpoint-route-metadata",
        slug="checkpoint-route-metadata",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_ok(*args, **kwargs):
        del args, kwargs
        return Verdict(approved=True, summary="checkpoint route approved")

    async def _commit_ok(*args, **kwargs):
        del args, kwargs
        return "f" * 40

    def _dirty_proof(*args, **kwargs):
        del args, kwargs
        return {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": False,
            "repo_heads": "",
            "reason": "dirty_generated_outputs",
            "problems": ["generated artifact changed after verifier approval"],
        }

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"test_allow_legacy_repair_without_sandbox": True},
    )
    _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    monkeypatch.setattr(implementation_module, "_commit_group", _commit_ok)
    monkeypatch.setattr(implementation_module, "_checkpoint_no_dirty_proof", _dirty_proof)

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        88,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_ok,
        dag_sha256="dag-sha-checkpoint-route-metadata",
    )

    assert approved is False
    assert "failure_class=checkpoint_gate" in failure
    assert "failure_type=checkpoint_dirty_worktree" in failure
    assert "route=commit_hygiene" in failure
    assert "retryable=True" in failure
    assert "operator_required=False" in failure
    assert "no_dirty_reason=dirty_generated_outputs" in failure
    gate = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:88"])
    assert gate["persisted"] is False
    assert gate["failure_type"] == "checkpoint_dirty_worktree"
    assert gate["route"] == "commit_hygiene"
    assert "dag-group:88" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_checkpoint_resume_accepts_real_committed_repo_head_vector(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-real-checkpoint-resume",
        slug="real-checkpoint-resume",
        metadata={},
    )
    repos_root = tmp_path / ".iriai" / "features" / feature.slug / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)
    (repo / "README.md").write_text("dirty checkpoint\n", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_ok(*args, **kwargs):
        del args, kwargs
        return Verdict(approved=True, summary="real checkpoint approved")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={
            "workspace_manager": SimpleNamespace(_base=tmp_path),
            "test_allow_legacy_repair_without_sandbox": True,
        },
    )
    store = _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-1",
                name="Task",
                description="Task",
                repo_path="app",
            )
        ],
        execution_order=[["TASK-1"]],
    )
    dag_sha256 = hashlib.sha256(dag.model_dump_json().encode("utf-8")).hexdigest()

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        0,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        repos_root,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_ok,
        dag_sha256=dag_sha256,
    )

    assert approved is True, failure
    assert failure == ""
    checkpoint = json.loads(runner.artifacts.store["dag-group:0"])
    proof = json.loads(runner.artifacts.store["dag-group-commit-proof:0"])
    commit_hash = checkpoint["commit_hash"]
    assert len(commit_hash) == 40
    assert ":" not in commit_hash
    assert proof["commit_hash"] == commit_hash
    assert proof["repo_heads"] == f"app:{commit_hash}"
    assert proof["no_dirty_proof"]["repo_heads"] == f"app:{commit_hash}"

    assert await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-1"],
        dag_sha256=dag_sha256,
        checkpoint=checkpoint,
    )
    runner.artifacts.store.pop("dag-verify-graph:g0:initial")
    assert await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-1"],
        dag_sha256=dag_sha256,
        checkpoint=checkpoint,
    )
    assert "dag-verify-graph:g0:initial" in runner.artifacts.store
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-1"],
        dag_sha256="overlay-dag-sha",
        checkpoint=checkpoint,
    )
    assert await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-1"],
        dag_sha256="overlay-dag-sha",
        checkpoint=checkpoint,
        accepted_dag_sha256s=[dag_sha256],
    )

    async def _unexpected_group_work(*args, **kwargs):
        del args, kwargs
        raise AssertionError("fresh committed checkpoint must skip group work")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _unexpected_group_work)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _unexpected_group_work)
    monkeypatch.setattr(implementation_module, "_commit_group", _unexpected_group_work)
    runner.artifacts.store[f"execution-control-adoption:{feature.id}"] = _strict_adoption_marker(
        feature,
        completed_range=(0, 0),
        next_group=1,
    )
    store.projected_checkpoints.clear()

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    outcome = await implementation_module._implement_dag(runner, feature, dag)
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    assert outcome.failure == ""
    assert outcome.terminal_state == "complete"
    assert head_after == head_before
    assert store.projected_checkpoints == []


@pytest.mark.asyncio
async def test_checkpoint_resume_recovers_group_marker_after_commit_crash(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-recover-checkpoint-marker",
        slug="recover-checkpoint-marker",
        metadata={},
    )
    repos_root = tmp_path / ".iriai" / "features" / feature.slug / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)
    (repo / "README.md").write_text("recoverable checkpoint\n", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_ok(*args, **kwargs):
        del args, kwargs
        return Verdict(approved=True, summary="recover checkpoint approved")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={
            "workspace_manager": SimpleNamespace(_base=tmp_path),
            "test_allow_legacy_repair_without_sandbox": True,
        },
    )
    _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-1",
                name="Task",
                description="Task",
                repo_path="app",
            )
        ],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    dag_sha256 = hashlib.sha256(dag.model_dump_json().encode("utf-8")).hexdigest()

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        0,
        [dag.tasks[0]],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        repos_root,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_ok,
        dag_sha256=dag_sha256,
    )
    assert approved is True, failure
    runner.artifacts.store.pop("dag-group:0")

    async def _unexpected_group_work(*args, **kwargs):
        del args, kwargs
        raise AssertionError("durable commit/gate proofs must reconstruct the checkpoint")

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _unexpected_group_work)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _unexpected_group_work)
    monkeypatch.setattr(implementation_module, "_commit_group", _unexpected_group_work)

    outcome = await implementation_module._implement_dag(runner, feature, dag)

    assert outcome.failure == ""
    assert outcome.terminal_state == "complete"
    recovered = json.loads(runner.artifacts.store["dag-group:0"])
    assert recovered["commit_hash"]
    assert recovered["task_ids"] == ["TASK-1"]
    assert recovered["results"][0]["task_id"] == "TASK-1"


@pytest.mark.asyncio
async def test_checkpoint_resume_preserves_registry_authority_for_nested_repo(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspace"
    feature = SimpleNamespace(
        id="feat-nested-checkpoint-resume",
        slug="nested-checkpoint-resume",
        metadata={},
    )
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "services" / "newsvc"
    source = workspace_root / "source" / "newsvc"
    _init_git_repo(repo)
    source.mkdir(parents=True)
    (repo / "README.md").write_text("authorized nested checkpoint\n", encoding="utf-8")
    registry = implementation_module.WorktreeRegistry(
        feature_id=feature.id,
        feature_slug=feature.slug,
        workspace_root=str(workspace_root),
        feature_root=str(feature_root),
        complete=True,
        repos=[
            implementation_module.WorktreeRegistryRepo(
                repo_path="services/newsvc",
                action="normal",
                role="execution",
                writable_task_ids=["TASK-1"],
                source_path=str(source),
                destination_path=str(repo),
            )
        ],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "worktree-registry": registry.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_ok(*args, **kwargs):
        del args, kwargs
        return Verdict(approved=True, summary="authorized nested checkpoint approved")

    async def _commit_nested_repo(*args, **kwargs):
        del args, kwargs
        subprocess.run(["git", "add", "--all", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "authorized nested checkpoint"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={
            "workspace_manager": SimpleNamespace(_base=workspace_root),
            "test_allow_legacy_repair_without_sandbox": True,
        },
    )
    _install_durable_graph_store(runner)
    _install_approved_lens_runtime(runner)
    monkeypatch.setattr(implementation_module, "_commit_group", _commit_nested_repo)
    task = ImplementationTask(
        id="TASK-1",
        name="Nested task",
        description="Touch nested service",
        repo_path="services/newsvc",
    )
    result = ImplementationResult(
        task_id="TASK-1",
        summary="done",
        files_modified=["services/newsvc/README.md"],
    )
    dag_sha256 = "dag-sha-authorized-nested-checkpoint"

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        85,
        [task],
        [result],
        [result],
        implementation_module.HandoverDoc(),
        feature_root,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_ok,
        dag_sha256=dag_sha256,
    )

    assert approved is True, failure
    checkpoint = json.loads(runner.artifacts.store["dag-group:85"])
    proof = json.loads(runner.artifacts.store["dag-group-commit-proof:85"])
    gate = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:85"])
    assert proof["no_dirty_proof"]["clean"] is True
    assert proof["no_dirty_proof"]["repos"][0]["path"] == "services/newsvc"
    assert proof["repo_heads"].startswith("services/newsvc:")
    assert gate["no_dirty_proof"]["repos"][0]["path"] == "services/newsvc"

    runner.artifacts.store.pop("dag-verify-graph:g85:initial")
    assert await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=85,
        group_task_ids=["TASK-1"],
        dag_sha256=dag_sha256,
        checkpoint=checkpoint,
    )
    assert "dag-verify-graph:g85:initial" in runner.artifacts.store


@pytest.mark.asyncio
async def test_checkpoint_recovery_rejects_tampered_gate_result_mirror(
    tmp_path,
    monkeypatch,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-tampered-checkpoint-results",
        group_idx=80,
        dag_sha256="dag-sha-tampered-checkpoint-results",
    )
    runner.artifacts.store.pop("dag-group:80")
    gate = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:80"])
    gate["checkpoint_results"] = [
        ImplementationResult(
            task_id="TASK-1",
            summary="tampered artifact mirror result",
        ).model_dump()
    ]
    gate["checkpoint_results_digest"] = implementation_module._dag_verify_graph_digest({
        "checkpoint_results": gate["checkpoint_results"],
        "group_idx": 80,
        "task_ids": ["TASK-1"],
    })
    await runner.artifacts.put(
        "dag-checkpoint-gate-proof:80",
        json.dumps(gate, sort_keys=True),
        feature=feature,
    )

    assert await implementation_module._recover_dag_group_checkpoint_from_proofs(
        runner,
        feature,
        group_idx=80,
        group_task_ids=["TASK-1"],
        dag_sha256="dag-sha-tampered-checkpoint-results",
    ) is None
    assert "dag-group:80" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_active_regroup_resume_does_not_accept_base_hash_for_boundary_group(
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-regroup-boundary", slug="regroup-boundary", metadata={})

    class _Artifacts:
        def __init__(self, initial: dict[str, str]) -> None:
            self.store = dict(initial)

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    tasks = [
        ImplementationTask(id=f"TASK-{idx}", name=f"Task {idx}", description="Task")
        for idx in range(46)
    ]
    base_dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[[task.id] for task in tasks],
        complete=True,
    )
    effective_dag = base_dag.model_copy(deep=True)
    base_sha = hashlib.sha256(base_dag.model_dump_json().encode("utf-8")).hexdigest()
    artifacts = {
        implementation_module.DAG_REGROUP_ACTIVE_KEY: json.dumps(
            {"status": "active", "base_dag_sha256": base_sha},
            sort_keys=True,
        ),
        f"execution-control-adoption:{feature.id}": _strict_adoption_marker(
            feature,
            completed_range=(0, 43),
            next_group=44,
        ),
    }
    for idx, task in enumerate(tasks):
        artifacts[f"dag-group:{idx}"] = json.dumps(
            {
                "group_idx": idx,
                "task_ids": [task.id],
                "results": [
                    ImplementationResult(task_id=task.id, summary="done").model_dump()
                ],
                "verdict": "approved",
                "commit_hash": f"commit-{idx}",
            },
            sort_keys=True,
        )
    runner = SimpleNamespace(artifacts=_Artifacts(artifacts), services={})
    checkpoint_calls: list[tuple[int, list[str]]] = []

    async def _fake_resolve(*_args, **_kwargs):
        return effective_dag, "", {"applied": True}

    async def _checkpoint_fresh(*_args, **kwargs):
        checkpoint_calls.append(
            (
                int(kwargs["group_idx"]),
                list(kwargs.get("accepted_dag_sha256s") or []),
            )
        )
        return True

    monkeypatch.setattr(
        implementation_module,
        "_resolve_active_regroup_before_group_dispatch",
        _fake_resolve,
    )
    monkeypatch.setattr(
        implementation_module,
        "_dag_group_checkpoint_is_fresh",
        _checkpoint_fresh,
    )

    outcome = await implementation_module._implement_dag(runner, feature, base_dag)

    assert outcome.failure == ""
    assert dict(checkpoint_calls)[44] == [base_sha]
    assert dict(checkpoint_calls)[45] == []


@pytest.mark.asyncio
async def test_mid_group_resume_skips_completed_dag_task_markers(
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-mid-group-resume", slug="mid-group-resume", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "dag-task:TASK-1": ImplementationResult(
                    task_id="TASK-1",
                    summary="completed before crash",
                    status="completed",
                ).model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-1", name="Task 1", description="Task 1"),
            ImplementationTask(id="TASK-2", name="Task 2", description="Task 2"),
        ],
        execution_order=[["TASK-1", "TASK-2"]],
        complete=True,
    )
    dispatched: list[str] = []

    async def _dispatch(*, task, **_kwargs):
        dispatched.append(task.id)
        return (
            ImplementationResult(
                task_id=task.id,
                summary="dispatched after resume",
                status="completed",
            ),
            SimpleNamespace(attempt_id=1, status="succeeded"),
        )

    async def _verify_group(*_args, **kwargs):
        results = list(_args[4])
        del kwargs
        assert sorted(result.task_id for result in results) == ["TASK-1", "TASK-2"]
        return True, ""

    async def _empty_string(*_args, **_kwargs):
        return ""

    async def _none(*_args, **_kwargs):
        return None

    async def _alias_ok(*_args, **_kwargs):
        return True, {}

    async def _contracts_ok(*_args, **_kwargs):
        return SimpleNamespace(approved=True, contracts_by_task_id={})

    async def _acl_ok(*_args, **_kwargs):
        return {}

    async def _contract_verdict_ok(*_args, **_kwargs):
        return SimpleNamespace(approved=True)

    monkeypatch.setattr(implementation_module, "_maybe_quiesce_before_group_dispatch", _empty_string)
    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _none)
    monkeypatch.setattr(implementation_module, "_run_worktree_alias_pre_dispatch_guard", _alias_ok)
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _contracts_ok,
    )
    monkeypatch.setattr(implementation_module, "_normalize_dag_workspace_acl", _acl_ok)
    monkeypatch.setattr(implementation_module, "_dag_workspace_writeability_problems", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(implementation_module, "_record_precommit_contract_verdicts", _contract_verdict_ok)
    monkeypatch.setattr(implementation_module, "_commit_repos", _empty_string)
    monkeypatch.setattr(implementation_module, "_dispatch_task_attempt_via_runtime_dispatcher", _dispatch)
    monkeypatch.setattr(
        implementation_module,
        "_resolve_task_dispatch_repo_binding",
        lambda **_kwargs: implementation_module._TaskDispatchRepoBinding(
            repo_id="",
            repo_path="",
            ws_path="",
            source="test",
        ),
    )
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _verify_group)
    monkeypatch.setattr(implementation_module, "_run_enhancement_group", _empty_string)

    outcome = await implementation_module._implement_dag(runner, feature, dag)

    assert outcome.failure == ""
    assert outcome.terminal_state == "complete"
    assert dispatched == ["TASK-2"]
    assert "completed before crash" in outcome.implementation_text


def test_execution_control_store_for_runner_builds_from_live_pool() -> None:
    class _Pool:
        def acquire(self):  # pragma: no cover - marker only
            raise AssertionError("helper must not acquire during construction")

    pool = _Pool()
    runner = SimpleNamespace(services={"pool": pool})

    store = implementation_module._execution_control_store_for_runner(runner)

    assert store is not None
    assert getattr(store, "_pool") is pool
    assert runner.services["execution_control_store"] is store
    assert implementation_module._execution_control_store_for_runner(runner) is store


def test_checkpoint_commit_matches_multi_repo_legacy_bare_vector():
    sha_a = "a" * 40
    sha_b = "b" * 40
    sha_c = "c" * 40

    assert implementation_module._checkpoint_commit_matches_current_heads(
        f"{sha_a},{sha_b}",
        f"app:{sha_a},lib:{sha_b}",
    )
    assert implementation_module._checkpoint_commit_matches_current_heads(
        f"{sha_b},{sha_a}",
        f"app:{sha_a},lib:{sha_b}",
    )
    assert not implementation_module._checkpoint_commit_matches_current_heads(
        f"{sha_a},{sha_c}",
        f"app:{sha_a},lib:{sha_b}",
    )


@pytest.mark.asyncio
async def test_unmarked_legacy_checkpoint_resume_without_slice06_proofs_is_stale(tmp_path):
    feature = SimpleNamespace(id="feat-legacy-resume", slug="feat-legacy-resume", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    repos_root = tmp_path / ".iriai" / "features" / feature.slug / "repos"
    repo = repos_root / "app"
    _init_git_repo(repo)
    commit_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"workspace_manager": SimpleNamespace(_base=tmp_path), "pool": object()},
    )
    checkpoint = {
        "group_idx": 0,
        "task_ids": ["TASK-legacy"],
        "results": [
            ImplementationResult(
                task_id="TASK-legacy",
                summary="legacy group already completed",
            ).model_dump()
        ],
        "verdict": "approved",
        "commit_hash": commit_hash,
    }

    assert implementation_module._execution_control_store_for_runner(runner) is None
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )

    await runner.artifacts.put(
        f"execution-control-legacy:{feature.id}",
        '{"status": "legacy-in-flight", "feature_id": "other-feature"}',
        feature=feature,
    )
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )

    await runner.artifacts.put(
        f"execution-control-legacy:{feature.id}",
        '{"status": "adopted", "feature_id": "feat-legacy-resume"}',
        feature=feature,
    )
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )

    await runner.artifacts.put(
        f"execution-control-legacy:{feature.id}",
        '{"status": "legacy-in-flight", "feature_id": "feat-legacy-resume"}',
        feature=feature,
    )
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )

    runner.services["execution_control_store"] = _DurableGraphStore()
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )

    (repo / "README.md").write_text("legacy head advanced\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "advance legacy head"], cwd=repo, check=True)
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )

    await runner.artifacts.put(
        f"execution-control-adoption:{feature.id}",
        '{"status": "adopted", "feature_id": "feat-legacy-resume"}',
        feature=feature,
    )
    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=0,
        group_task_ids=["TASK-legacy"],
        dag_sha256="legacy-dag-sha",
        checkpoint=checkpoint,
    )


@pytest.mark.asyncio
async def test_execution_control_adoption_requires_durable_marker() -> None:
    feature = SimpleNamespace(
        id="feat-explicit-adoption",
        slug="feat-explicit-adoption",
        metadata={
            "execution_control_adopted": True,
            "execution_control_required": True,
            "control_plane_required": True,
        },
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={
            "execution_control_store": _DurableGraphStore(),
            "execution_control_required": True,
            "control_plane_required": True,
        },
    )

    assert not await implementation_module._feature_requires_execution_control_proofs(
        runner,
        feature,
    )

    await runner.artifacts.put(
        f"execution-control-adoption:{feature.id}",
        '{"status": "adopted", "feature_id": "other-feature"}',
        feature=feature,
    )
    assert not await implementation_module._feature_requires_execution_control_proofs(
        runner,
        feature,
    )

    await runner.artifacts.put(
        f"execution-control-adoption:{feature.id}",
        '{"feature_id": "feat-explicit-adoption"}',
        feature=feature,
    )
    assert not await implementation_module._feature_requires_execution_control_proofs(
        runner,
        feature,
    )

    await runner.artifacts.put(
        f"execution-control-adoption:{feature.id}",
        '{"status": "legacy-in-flight", "feature_id": "feat-explicit-adoption"}',
        feature=feature,
    )
    assert not await implementation_module._feature_requires_execution_control_proofs(
        runner,
        feature,
    )

    await runner.artifacts.put(
        f"execution-control-adoption:{feature.id}",
        '{"status": "adopted", "feature_id": "feat-explicit-adoption"}',
        feature=feature,
    )
    assert await implementation_module._feature_requires_execution_control_proofs(
        runner,
        feature,
    )


@pytest.mark.asyncio
async def test_verification_graph_records_preflight_before_raw_verifier(
    tmp_path,
    monkeypatch,
):
    runner, _feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-preflight-order",
        group_idx=75,
        dag_sha256="dag-sha-preflight-order",
    )
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g75:initial"])
    names = [node["name"] for node in graph["nodes"]]
    expected_preflight = [
        "candidate_manifest",
        "gate_request",
        "workspace_snapshot_freshness",
        "contract_closure",
        "artifact_freshness",
        "path_scope_and_projection",
        "patch_integrity",
        "raw_gate_approval_requirements",
        "bounded_verifier_context",
        "raw_verifier",
    ]
    expected = [
        *expected_preflight,
        "expanded_lens:acceptance-coverage",
        "expanded_lens:contract-protocol",
        "expanded_lens:build-dependency",
        "aggregate_verdict",
    ]
    for item in expected:
        assert item in names
    assert [names.index(item) for item in expected_preflight] == sorted(
        names.index(item) for item in expected_preflight
    )
    raw_index = names.index("raw_verifier")
    aggregate_index = names.index("aggregate_verdict")
    assert raw_index < aggregate_index
    for lens_name in (
        "expanded_lens:acceptance-coverage",
        "expanded_lens:contract-protocol",
        "expanded_lens:build-dependency",
    ):
        assert raw_index < names.index(lens_name) < aggregate_index
    proof = graph["proof"]
    context_id = next(node["id"] for node in graph["nodes"] if node["name"] == "bounded_verifier_context")
    raw_id = graph["aggregate"]["raw_verdict_node_id"]
    assert context_id in proof["required_lineage_node_ids"]
    assert raw_id in proof["required_lineage_node_ids"]
    assert any(
        edge["from_node_id"] == context_id
        and edge["to_node_id"] == raw_id
        and edge["id"] in proof["required_edge_ids"]
        for edge in graph["edges"]
    )


@pytest.mark.asyncio
async def test_verify_and_fix_group_records_bounded_read_budget_in_production_graph(
    tmp_path,
    monkeypatch,
):
    runner, _feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-production-bounded-read",
        group_idx=82,
        dag_sha256="dag-sha-production-bounded-read",
    )
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g82:initial"])
    read_budget = graph["read_budget"]
    assert read_budget["blocked_unbounded_read_count"] == 0
    assert read_budget["omitted_required_refs"] == []
    context_node = next(
        node for node in graph["nodes"]
        if node["name"] == "bounded_verifier_context"
    )
    assert context_node["status"] == "approved"
    assert context_node["metadata"]["read_budget_digest"] == read_budget["budget_digest"]
    assert context_node["input_hash"] == implementation_module._dag_verify_graph_digest(
        {
            "projection_key": "dag-verify:g82:initial",
            "read_budget": read_budget,
        }
    )
    query_sources = {query["source"] for query in read_budget["bounded_queries"]}
    query_kinds = {query["lookup_kind"] for query in read_budget["bounded_queries"]}
    assert "artifact" in query_sources
    assert "bounded_feature" not in query_kinds
    assert all(query.get("limit") == 1 for query in read_budget["bounded_queries"])
    assert graph["aggregate"]["approved"] is True


@pytest.mark.asyncio
async def test_verify_and_fix_group_persists_runtime_failure_graph_before_repair(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-raw-verifier-crash",
        slug="feat-raw-verifier-crash",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _verify_crashes(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("provider transport crashed")

    async def _commit_must_not_run(*args, **kwargs):
        del args, kwargs
        raise AssertionError("commit must not run after raw verifier crash")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"test_allow_legacy_repair_without_sandbox": True},
    )
    store = _install_durable_graph_store(runner)
    monkeypatch.setattr(implementation_module, "_commit_group", _commit_must_not_run)

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        83,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_crashes,
        dag_sha256="dag-sha-raw-crash",
    )

    assert approved is False
    assert "Verifier provider/runtime failed before product repair dispatch" in failure
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g83:initial"])
    raw_node = next(node for node in graph["nodes"] if node["name"] == "raw_verifier")
    assert graph["approved"] is False
    assert raw_node["metadata"]["failure_class"] == "verifier_provider"
    assert raw_node["metadata"]["route"] == "retry_verifier"
    assert store.projections
    assert "dag-verify:g83:initial" not in runner.artifacts.store
    reloaded = await store.get_latest_verified_verification_graph_projection(
        feature_id=str(feature.id),
        projection_key="dag-verify:g83:initial",
        dag_sha256="dag-sha-raw-crash",
        group_idx=83,
        stage="initial",
    )
    assert reloaded is not None
    reloaded_payload = reloaded["graph"]["payload"]
    reloaded_raw_node = next(
        node for node in reloaded_payload["nodes"]
        if node["name"] == "raw_verifier"
    )
    assert reloaded_raw_node["metadata"]["failure_class"] == "verifier_provider"

    approved_retry, failure_retry = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        83,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        verify_fn=_verify_crashes,
        dag_sha256="dag-sha-raw-crash",
    )

    assert approved_retry is False
    assert "Verifier provider/runtime failed before product repair dispatch" in failure_retry


@pytest.mark.asyncio
async def test_final_reverify_after_parallel_repair_records_provider_failure_graph(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-final-reverify-crash",
        slug="final-reverify-crash",
        metadata={},
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}

        async def run(self, *args, **kwargs):  # pragma: no cover - patched path
            del args, kwargs
            raise AssertionError("parallel repair is patched; broad agent repair must not run")

    verify_calls = 0

    async def _verify(*args, **kwargs):
        nonlocal verify_calls
        del args, kwargs
        verify_calls += 1
        if verify_calls == 1:
            return Verdict(
                approved=False,
                summary="needs product repair",
                concerns=[Issue(severity="major", description="business logic still wrong")],
            )
        raise RuntimeError("final verifier provider unavailable")

    async def _pass_expanded(*args, **kwargs):
        del kwargs
        return args[4]

    async def _no_preflight(*args, **kwargs):
        del args, kwargs
        return None

    async def _no_authority_repair(*args, **kwargs):
        del args, kwargs
        return implementation_module.DagAuthorityGateOutcome()

    async def _parallel_repair(*args, **kwargs):
        del args, kwargs
        return [
            ImplementationResult(
                task_id="TASK-1",
                summary="parallel fix applied",
                files_modified=["README.md"],
            )
        ]

    async def _no_sanitize(*args, **kwargs):
        del kwargs
        return args[4]

    async def _no_result_reconcile(
        runner,
        feature,
        group_idx,
        retry_label,
        group_tasks,
        *,
        results,
        verify_results_context,
        all_results,
        repair_results,
        feature_root,
    ):
        del runner, feature, group_idx, retry_label, group_tasks, repair_results, feature_root
        return implementation_module.DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            {},
        )

    async def _no_spec_reconcile(*args, **kwargs):
        del kwargs
        return implementation_module.DagTaskSpecReconcileOutcome(args[4], {})

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(implementation_module, "_run_dag_group_preflight", _no_preflight)
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _pass_expanded)
    monkeypatch.setattr(implementation_module, "_attempt_dag_authority_gate_repair", _no_authority_repair)
    monkeypatch.setattr(implementation_module, "_attempt_parallel_dag_repair", _parallel_repair)
    monkeypatch.setattr(implementation_module, "_sanitize_dag_repair_results", _no_sanitize)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_results", _no_result_reconcile)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_specs", _no_spec_reconcile)

    runner = _Runner()
    store = _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        86,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        dag_sha256="dag-sha-final-reverify-crash",
    )

    assert approved is False
    assert verify_calls == 2
    assert "Verifier provider/runtime failed before product repair dispatch" in failure
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g86:retry-0"])
    raw_node = next(node for node in graph["nodes"] if node["name"] == "raw_verifier")
    assert raw_node["metadata"]["failure_class"] == "verifier_provider"
    assert raw_node["metadata"]["route"] == "retry_verifier"
    reloaded = await store.get_latest_verified_verification_graph_projection(
        feature_id=str(feature.id),
        projection_key="dag-verify:g86:retry-0",
        dag_sha256="dag-sha-final-reverify-crash",
        group_idx=86,
        stage="retry-0",
    )
    assert reloaded is not None


@pytest.mark.asyncio
async def test_final_reverify_after_parallel_repair_runs_checkpoint_lenses(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(
        id="feat-final-reverify-lenses",
        slug="final-reverify-lenses",
        metadata={},
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}

        async def run(self, *args, **kwargs):  # pragma: no cover - patched path
            del args, kwargs
            raise AssertionError("parallel repair is patched; broad agent repair must not run")

    verify_calls = 0
    lens_calls: list[str] = []

    async def _verify(*args, **kwargs):
        nonlocal verify_calls
        del args, kwargs
        verify_calls += 1
        if verify_calls == 1:
            return Verdict(
                approved=False,
                summary="needs product repair",
                concerns=[Issue(severity="major", description="business logic still wrong")],
            )
        return Verdict(approved=True, summary="final repair approved")

    async def _pass_expanded(*args, **kwargs):
        del kwargs
        return args[4]

    async def _checkpoint_lenses(
        runner,
        feature,
        group_idx,
        stage_label,
        verdict,
        results,
        files,
        tasks,
        *,
        runtime,
        feature_root,
        projection_key,
        dag_sha256,
    ):
        del runner, feature, group_idx, results, files, tasks, runtime, feature_root, dag_sha256
        lens_calls.append(f"{stage_label}:{projection_key}")
        return (
            verdict,
            [
                (
                    SimpleNamespace(slug="acceptance-coverage"),
                    Verdict(approved=True, summary="acceptance lens approved"),
                )
            ],
            [],
        )

    async def _no_preflight(*args, **kwargs):
        del args, kwargs
        return None

    async def _no_authority_repair(*args, **kwargs):
        del args, kwargs
        return implementation_module.DagAuthorityGateOutcome()

    async def _parallel_repair(*args, **kwargs):
        del args, kwargs
        return [
            ImplementationResult(
                task_id="TASK-1",
                summary="parallel fix applied",
                files_modified=["README.md"],
            )
        ]

    async def _no_sanitize(*args, **kwargs):
        del kwargs
        return args[4]

    async def _no_result_reconcile(
        runner,
        feature,
        group_idx,
        retry_label,
        group_tasks,
        *,
        results,
        verify_results_context,
        all_results,
        repair_results,
        feature_root,
    ):
        del runner, feature, group_idx, retry_label, group_tasks, repair_results, feature_root
        return implementation_module.DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            {},
        )

    async def _no_spec_reconcile(*args, **kwargs):
        del kwargs
        return implementation_module.DagTaskSpecReconcileOutcome(args[4], {})

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(implementation_module, "_run_dag_group_preflight", _no_preflight)
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _pass_expanded)
    monkeypatch.setattr(
        implementation_module,
        "_run_checkpoint_required_dag_verify_lenses",
        _checkpoint_lenses,
    )
    monkeypatch.setattr(
        implementation_module,
        "_dag_verify_required_lens_slugs",
        lambda: ["acceptance-coverage"],
    )
    monkeypatch.setattr(implementation_module, "_attempt_dag_authority_gate_repair", _no_authority_repair)
    monkeypatch.setattr(implementation_module, "_attempt_parallel_dag_repair", _parallel_repair)
    monkeypatch.setattr(implementation_module, "_sanitize_dag_repair_results", _no_sanitize)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_results", _no_result_reconcile)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_specs", _no_spec_reconcile)

    runner = _Runner()
    store = _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        87,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        dag_sha256="dag-sha-final-reverify-lenses",
    )

    graph = json.loads(runner.artifacts.store["dag-verify-graph:g87:retry-0"])
    assert approved is True, failure
    assert failure == ""
    assert lens_calls == ["retry-0:dag-verify:g87:retry-0"]
    assert any(
        node["name"] == "expanded_lens:acceptance-coverage"
        and node["status"] == "approved"
        for node in graph["nodes"]
    )
    assert graph["aggregate_node"]["metadata"]["required_lens_slugs"] == ["acceptance-coverage"]
    reloaded = await store.get_latest_verified_verification_graph_projection(
        feature_id=str(feature.id),
        projection_key="dag-verify:g87:retry-0",
        dag_sha256="dag-sha-final-reverify-lenses",
        group_idx=87,
        stage="retry-0",
    )
    assert reloaded is not None


@pytest.mark.asyncio
async def test_rejected_verifier_rerun_supersedes_prior_approved_projection(
    tmp_path,
    monkeypatch,
):
    del tmp_path, monkeypatch
    feature = SimpleNamespace(
        id="feat-rejected-rerun",
        slug="feat-rejected-rerun",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    _install_durable_graph_store(runner)
    projection_key = "dag-verify:g84:initial"

    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        projection_key,
        Verdict(approved=True, summary="approved before rerun"),
        group_idx=84,
        dag_sha256="dag-sha-rejected-rerun",
    )
    await runner.artifacts.put(
        projection_key,
        "approved before rerun",
        feature=feature,
    )
    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        projection_key,
        Verdict(
            approved=False,
            summary="rejected after rerun",
            concerns=[Issue(severity="blocker", description="still broken")],
        ),
        group_idx=84,
        dag_sha256="dag-sha-rejected-rerun",
    )

    assert "rejected after rerun" in runner.artifacts.store[projection_key]
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g84:initial"])
    assert graph["approved"] is False
    raw_nodes = [node for node in graph["nodes"] if node["name"] == "raw_verifier"]
    assert len(raw_nodes) >= 2
    assert any(node["metadata"].get("supersedes_node_id") for node in raw_nodes)


@pytest.mark.asyncio
async def test_approved_verifier_rerun_supersedes_prior_raw_projection(
    tmp_path,
    monkeypatch,
):
    del tmp_path, monkeypatch
    feature = SimpleNamespace(
        id="feat-approved-rerun",
        slug="feat-approved-rerun",
        metadata={},
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    _install_durable_graph_store(runner)
    projection_key = "dag-verify:g87:initial"
    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        projection_key,
        Verdict(
            approved=False,
            summary="rejected before approved rerun",
            concerns=[Issue(severity="blocker", description="not fixed yet")],
        ),
        group_idx=87,
        dag_sha256="dag-sha-approved-rerun",
        required_lens_slugs=[],
    )
    await runner.artifacts.put(
        projection_key,
        "rejected before approved rerun",
        feature=feature,
    )

    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        projection_key,
        Verdict(approved=True, summary="approved after rerun"),
        group_idx=87,
        dag_sha256="dag-sha-approved-rerun",
        required_lens_slugs=[],
    )

    assert "approved after rerun" in runner.artifacts.store[projection_key]
    assert "rejected before approved rerun" not in runner.artifacts.store[projection_key]
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g87:initial"])
    assert graph["approved"] is True
    assert any(
        node["name"] == "raw_verifier" and node["status"] == "rejected"
        for node in graph["nodes"]
    )
    raw_node = next(
        node for node in graph["nodes"]
        if node["name"] == "raw_verifier" and node["status"] == "approved"
    )
    assert raw_node["status"] == "approved"


@pytest.mark.asyncio
async def test_merge_queue_writes_merge_gate_node_before_checkpoint(
    tmp_path,
    monkeypatch,
):
    runner, _feature, store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-merge-before-checkpoint",
        group_idx=76,
        dag_sha256="dag-sha-merge-before-checkpoint",
    )
    kinds = [node.kind for node in store.nodes]
    assert "merge_gate" in kinds
    assert "checkpoint_gate" in kinds
    assert kinds.index("merge_gate") < kinds.index("checkpoint_gate")
    assert "dag-group:76" in runner.artifacts.store


@pytest.mark.asyncio
async def test_checkpoint_gate_requires_merge_gate_for_each_queue_item(
    tmp_path,
    monkeypatch,
):
    runner, feature, store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-missing-merge-gate",
        group_idx=77,
        dag_sha256="dag-sha-missing-merge-gate",
    )
    checkpoint = json.loads(runner.artifacts.store["dag-group:77"])
    proof = json.loads(runner.artifacts.store["dag-group-commit-proof:77"])
    gate = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:77"])
    commit_hash = checkpoint["commit_hash"]
    proof["repo_heads"] = commit_hash
    proof["no_dirty_proof"]["repo_heads"] = commit_hash
    gate["no_dirty_proof"]["repo_heads"] = commit_hash
    store.nodes = [node for node in store.nodes if node.kind != "merge_gate"]
    await runner.artifacts.put(
        "dag-group-commit-proof:77",
        json.dumps(proof, sort_keys=True),
        feature=feature,
    )
    await runner.artifacts.put(
        "dag-checkpoint-gate-proof:77",
        json.dumps(gate, sort_keys=True),
        feature=feature,
    )
    monkeypatch.setattr(
        implementation_module,
        "_feature_repos_clean_for_checkpoint_resume",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        implementation_module,
        "_current_feature_repo_heads",
        lambda *_args, **_kwargs: commit_hash,
    )
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": commit_hash,
        },
    )

    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=77,
        group_task_ids=["TASK-1"],
        dag_sha256="dag-sha-missing-merge-gate",
        checkpoint=checkpoint,
    )


@pytest.mark.asyncio
async def test_checkpoint_gate_ignores_queue_payload_json_mirrors(
    tmp_path,
    monkeypatch,
):
    runner, feature, store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-tampered-gate-mirror",
        group_idx=78,
        dag_sha256="dag-sha-tampered-gate-mirror",
    )
    checkpoint = json.loads(runner.artifacts.store["dag-group:78"])
    proof = json.loads(runner.artifacts.store["dag-group-commit-proof:78"])
    gate = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:78"])
    commit_hash = checkpoint["commit_hash"]
    proof["repo_heads"] = commit_hash
    proof["no_dirty_proof"]["repo_heads"] = commit_hash
    gate["no_dirty_proof"]["repo_heads"] = commit_hash
    gate["merge_gate"]["content_hash"] = "tampered"
    await runner.artifacts.put(
        "dag-group-commit-proof:78",
        json.dumps(proof, sort_keys=True),
        feature=feature,
    )
    await runner.artifacts.put(
        "dag-checkpoint-gate-proof:78",
        json.dumps(gate, sort_keys=True),
        feature=feature,
    )
    monkeypatch.setattr(implementation_module, "_feature_repos_clean_for_checkpoint_resume", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(implementation_module, "_current_feature_repo_heads", lambda *_args, **_kwargs: commit_hash)
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": commit_hash,
        },
    )

    assert not await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=78,
        group_task_ids=["TASK-1"],
        dag_sha256="dag-sha-tampered-gate-mirror",
        checkpoint=checkpoint,
    )
    assert any(node.kind == "merge_gate" for node in store.nodes)


@pytest.mark.asyncio
async def test_checkpoint_resume_rejects_empty_or_mismatched_results(
    tmp_path,
    monkeypatch,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-result-coverage",
        group_idx=79,
        dag_sha256="dag-sha-result-coverage",
    )
    checkpoint = json.loads(runner.artifacts.store["dag-group:79"])
    proof = json.loads(runner.artifacts.store["dag-group-commit-proof:79"])
    gate = json.loads(runner.artifacts.store["dag-checkpoint-gate-proof:79"])
    commit_hash = checkpoint["commit_hash"]
    proof["repo_heads"] = commit_hash
    proof["no_dirty_proof"]["repo_heads"] = commit_hash
    gate["no_dirty_proof"]["repo_heads"] = commit_hash
    await runner.artifacts.put("dag-group-commit-proof:79", json.dumps(proof, sort_keys=True), feature=feature)
    await runner.artifacts.put("dag-checkpoint-gate-proof:79", json.dumps(gate, sort_keys=True), feature=feature)
    monkeypatch.setattr(implementation_module, "_feature_repos_clean_for_checkpoint_resume", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(implementation_module, "_current_feature_repo_heads", lambda *_args, **_kwargs: commit_hash)
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": commit_hash,
        },
    )

    assert await implementation_module._dag_group_checkpoint_is_fresh(
        runner,
        feature,
        group_idx=79,
        group_task_ids=["TASK-1"],
        dag_sha256="dag-sha-result-coverage",
        checkpoint=checkpoint,
    )
    empty = {**checkpoint, "results": []}
    wrong = {**checkpoint, "results": [{**checkpoint["results"][0], "task_id": "OTHER"}]}
    invalid = {**checkpoint, "results": [{"not": "an implementation result"}]}
    for candidate in (empty, wrong, invalid):
        assert not await implementation_module._dag_group_checkpoint_is_fresh(
            runner,
            feature,
            group_idx=79,
            group_task_ids=["TASK-1"],
            dag_sha256="dag-sha-result-coverage",
            checkpoint=candidate,
        )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("stale_dag", "artifact payload differs"),
        ("non_approved_required_node", "artifact payload differs"),
        ("missing_required_lens", "artifact payload differs"),
        ("proof_digest_mismatch", "returned no typed proof"),
        ("graph_payload_digest_mismatch", "artifact payload differs"),
    ],
)
@pytest.mark.asyncio
async def test_checkpoint_rejects_tampered_dag_verification_graph_payloads(
    tmp_path,
    monkeypatch,
    case,
    expected,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id=f"feat-tamper-{case}",
        group_idx=72,
        dag_sha256="dag-sha-tamper",
    )
    graph_key = "dag-verify-graph:g72:initial"
    projection_key = "dag-verify:g72:initial"
    graph = json.loads(runner.artifacts.store[graph_key])
    if case == "stale_dag":
        graph["dag_sha256"] = "stale-dag"
    elif case == "non_approved_required_node":
        raw_node_id = graph["aggregate"]["raw_verdict_node_id"]
        for node in graph["nodes"]:
            if node.get("id") == raw_node_id:
                node["status"] = "rejected"
    elif case == "missing_required_lens":
        graph["aggregate"]["required_lens_node_ids"] = []
        graph["aggregate_node"]["metadata"]["required_lens_slugs"] = []
    elif case == "proof_digest_mismatch":
        graph["proof"]["proof_digest"] = "0" * 64
    elif case == "graph_payload_digest_mismatch":
        graph["proof"]["graph_payload_digest"] = "stale-payload"
    await runner.artifacts.put(
        graph_key,
        json.dumps(graph, sort_keys=True),
        feature=feature,
    )

    failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        72,
        projection_key,
        Verdict(approved=True, summary="approved"),
        dag_sha256="dag-sha-tamper",
    )

    assert expected in failure
    assert "WORKFLOW_BLOCKER" in failure


@pytest.mark.asyncio
async def test_checkpoint_rejects_missing_durable_graph_projection_without_store(
    tmp_path,
    monkeypatch,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-missing-durable",
        group_idx=73,
        dag_sha256="dag-sha-missing-durable",
    )
    graph_key = "dag-verify-graph:g73:initial"
    graph = json.loads(runner.artifacts.store[graph_key])
    graph.pop("durable_projection", None)
    await runner.artifacts.put(graph_key, json.dumps(graph, sort_keys=True), feature=feature)
    runner.services.pop("execution_control_store", None)

    failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        73,
        "dag-verify:g73:initial",
        Verdict(approved=True, summary="approved"),
        dag_sha256="dag-sha-missing-durable",
    )

    assert "not durably persisted" in failure
    assert "WORKFLOW_BLOCKER" in failure


@pytest.mark.asyncio
async def test_checkpoint_rejects_summary_only_approval_without_projection_key(
    tmp_path,
    monkeypatch,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-summary-only",
        group_idx=77,
        dag_sha256="dag-sha-summary-only",
    )

    failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        77,
        None,
        Verdict(approved=True, summary="summary only"),
        dag_sha256="dag-sha-summary-only",
    )

    assert "Summary-only approval cannot checkpoint" in failure
    assert "WORKFLOW_BLOCKER" in failure


@pytest.mark.asyncio
async def test_checkpoint_rejects_durable_edge_reload_mismatch(
    tmp_path,
    monkeypatch,
):
    runner, feature, store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-edge-mismatch",
        group_idx=74,
        dag_sha256="dag-sha-edge-mismatch",
    )
    verified = next(iter(store.verified.values()))
    verified["required_edges"] = [
        {
            **edge,
            "graph_edge_id": "999",
        }
        for edge in verified["required_edges"]
    ]

    failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        74,
        "dag-verify:g74:initial",
        Verdict(approved=True, summary="approved"),
        dag_sha256="dag-sha-edge-mismatch",
    )

    assert "durable edges do not match" in failure
    assert "WORKFLOW_BLOCKER" in failure


@pytest.mark.asyncio
async def test_merge_queue_requires_approved_aggregate_node(
    tmp_path,
    monkeypatch,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-aggregate-required",
        group_idx=80,
        dag_sha256="dag-sha-aggregate-required",
    )
    graph_key = "dag-verify-graph:g80:initial"
    graph = json.loads(runner.artifacts.store[graph_key])
    graph["aggregate"]["approved"] = False
    graph["aggregate_node"]["status"] = "rejected"
    await runner.artifacts.put(graph_key, json.dumps(graph, sort_keys=True), feature=feature)

    failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        80,
        "dag-verify:g80:initial",
        Verdict(approved=True, summary="approved"),
        dag_sha256="dag-sha-aggregate-required",
    )

    assert "artifact payload differs" in failure
    assert "WORKFLOW_BLOCKER" in failure


@pytest.mark.asyncio
async def test_canonical_apply_context_hash_change_invalidates_aggregate(
    tmp_path,
    monkeypatch,
):
    runner, feature, _store = await _approved_graph_fixture(
        tmp_path,
        monkeypatch,
        feature_id="feat-context-hash-invalidates",
        group_idx=81,
        dag_sha256="dag-sha-context-hash",
    )
    graph_key = "dag-verify-graph:g81:initial"
    graph = json.loads(runner.artifacts.store[graph_key])
    for node in graph["nodes"]:
        if node.get("name") == "bounded_verifier_context":
            node["output_hash"] = "stale-context-hash"
            break
    await runner.artifacts.put(graph_key, json.dumps(graph, sort_keys=True), feature=feature)

    failure = await implementation_module._require_dag_verification_graph_approval(
        runner,
        feature,
        81,
        "dag-verify:g81:initial",
        Verdict(approved=True, summary="approved"),
        dag_sha256="dag-sha-context-hash",
    )

    assert "artifact payload differs" in failure
    assert "WORKFLOW_BLOCKER" in failure


@pytest.mark.asyncio
async def test_legacy_raw_dag_verify_artifact_without_graph_cannot_checkpoint(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-raw-only", slug="raw-only", metadata={})
    raw_key = "dag-verify:g71:initial"

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                raw_key: Verdict(approved=True, summary="legacy raw approval").model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    async def _unexpected_commit(*args, **kwargs):  # pragma: no cover - failure path
        del args, kwargs
        raise AssertionError("raw dag-verify approval without graph proof must not commit")

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"test_allow_legacy_repair_without_sandbox": True},
    )
    monkeypatch.setattr(implementation_module, "_commit_group", _unexpected_commit)
    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 0)

    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        71,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK-1", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        initial_verdict=Verdict(approved=True, summary="legacy raw approval"),
        initial_verdict_key=raw_key,
        dag_sha256="dag-sha-raw-only",
    )

    assert approved is False
    assert (
        "raw dag-verify compatibility projection alone cannot approve" in failure
        or "checkpoint requires aggregate graph proof" in failure
    )
    assert "dag-group:71" not in runner.artifacts.store
    graph = json.loads(runner.artifacts.store["dag-verify-graph:g71:initial"])
    assert graph["approved"] is False
    assert graph["durable_projection"]["persisted"] is False
    blocker = json.loads(
        runner.artifacts.store["workflow-blocker:g71:verification-graph-initial"]
    )
    assert blocker["failure_class"] == "verification_graph"
    assert blocker["deterministic_workflow_blocker"] is True
    assert blocker["blocked_before_checkpoint"] is True


@pytest.mark.asyncio
async def test_dag_preflight_distinguishes_staged_and_unstaged_forbidden_deletes(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-git-state", slug="git-state", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    forbidden_path = "src/webviews/dashboard/README.md"
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True)
    forbidden.write_text("old", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [],
            "forbidden_files": [
                {"path": "src/webviews/dashboard", "source": "retired dashboard"}
            ],
        }),
        encoding="utf-8",
    )

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE)
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "seed")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    task = ImplementationTask(id="TASK-1", name="Task", description="Task")

    forbidden.unlink()
    forbidden.parent.rmdir()
    unstaged = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        37,
        "unstaged",
        [task],
        [],
        feature_root=feature_root,
    )

    assert unstaged is not None
    assert any("stage the deletion" in issue.description for issue in unstaged.concerns)
    unstaged_report = json.loads(
        runner.artifacts.store["dag-repair-preflight:g37:retry-unstaged"]
    )
    unstaged_problem = unstaged_report["path_problems"][0]
    assert unstaged_problem["git_state"] == "unstaged_delete"
    assert unstaged_problem["tracked_or_staged"] is True

    git("add", "-u", forbidden_path)
    staged = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        37,
        "staged",
        [task],
        [],
        feature_root=feature_root,
    )

    assert staged is None
    staged_report = json.loads(
        runner.artifacts.store["dag-repair-preflight:g37:retry-staged"]
    )
    assert staged_report["approved"] is True
    assert staged_report["path_problems"] == []


@pytest.mark.asyncio
async def test_dag_preflight_blocks_repo_hygiene_leaks(tmp_path):
    feature = SimpleNamespace(id="feat-hygiene", slug="hygiene", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    (repo / "src/webviews/dashboard/.git").mkdir(parents=True)
    (repo / "_pending_orchestrator.py").write_text("parked", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        38,
        "initial",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [],
        feature_root=feature_root,
    )

    assert verdict is not None
    descriptions = [issue.description for issue in verdict.concerns]
    assert any("embedded .git directory" in description for description in descriptions)
    assert any("parked implementation fallback" in description for description in descriptions)
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g38:retry-initial"])
    reasons = {problem["reason"] for problem in report["path_problems"]}
    assert {"embedded_git", "parked_implementation_file"} <= reasons


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_ignores_context_and_rewrites_legacy_paths(tmp_path):
    feature = SimpleNamespace(id="feat-sanitize", slug="sanitize")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("ok", encoding="utf-8")
    backend_path = tmp_path / "iriai-studio-backend" / "iriai_studio_backend"
    backend_path.mkdir(parents=True)
    (backend_path / "paths.py").write_text("paths", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=[
            "pkg/ok.py",
            ".iriai-context/context.md",
            ".iriai/artifacts/features/feat/compile-sources-dag.md",
            "src/iriai_studio_backend/paths.py",
            "missing.py",
        ],
    )

    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        26,
        1,
        [result],
        tmp_path,
        context_label="test",
    )

    assert sanitized[0].files_modified == [
        "pkg/ok.py",
        "iriai-studio-backend/iriai_studio_backend/paths.py",
        "missing.py",
    ]
    report = json.loads(runner.artifacts.store["dag-repair-result-sanitize:g26:retry-1"])
    assert report["ignored_path_count"] == 2
    assert report["rewritten_path_count"] == 1
    assert report["invalid_product_path_count"] == 1
    assert report["has_invalid_product_paths"] is True


def test_dag_task_prompt_uses_canonical_backend_paths_for_retired_prefixes():
    task = ImplementationTask(
        id="TASK-S20-01",
        name="Security hooks",
        description="Implement hook-disable controls",
        repo_path="iriai-studio-backend",
        file_scope=[
            {
                "path": "iriai-studio-backend/src-py/iriai_studio_backend/paths.py",
                "action": "modify",
            },
            {
                "path": "iriai-studio-backend/src/iriai_studio_backend/security/hooks_disable.py",
                "action": "create",
            },
        ],
        files=["src/iriai_studio_backend/security/__init__.py"],
    )

    canonical_tasks, rewrites = implementation_module.canonicalize_implementation_tasks([task])
    prompt = implementation_module._build_task_prompt(
        canonical_tasks[0],
        repo_prefix="iriai-studio-backend/",
    )

    assert len(rewrites) == 3
    assert "`iriai_studio_backend/paths.py`" in prompt
    assert "`iriai_studio_backend/security/hooks_disable.py`" in prompt
    assert "src/iriai_studio_backend" not in prompt
    assert "src-py/iriai_studio_backend" not in prompt
    assert task.file_scope[0].path == (
        "iriai-studio-backend/src-py/iriai_studio_backend/paths.py"
    )


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_rewrites_retired_existing_source_before_product_acceptance(tmp_path):
    feature = SimpleNamespace(id="feat-stale-source-sanitize", slug="stale-source-sanitize")
    stale_path = (
        tmp_path
        / "iriai-studio-backend"
        / "src"
        / "iriai_studio_backend"
        / "security"
    )
    stale_path.mkdir(parents=True)
    (stale_path / "hooks_disable.py").write_text("dead copy", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=[
            "iriai-studio-backend/src/iriai_studio_backend/security/hooks_disable.py",
        ],
    )

    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        26,
        0,
        [result],
        tmp_path,
        context_label="test",
    )

    assert sanitized[0].files_modified == [
        "iriai-studio-backend/iriai_studio_backend/security/hooks_disable.py",
    ]
    report = json.loads(runner.artifacts.store["dag-repair-result-sanitize:g26:retry-0"])
    assert report["rewritten_path_count"] == 1
    assert report["paths"][0]["category"] == "rewritten_product"

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        26,
        "0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        sanitized,
        feature_root=tmp_path,
        known_task_ids={"TASK-1"},
    )

    assert isinstance(verdict, Verdict)
    assert any(
        "iriai-studio-backend/iriai_studio_backend/security/hooks_disable.py"
        in issue.description
        for issue in verdict.concerns
    )


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_prevents_context_paths_from_blocking_preflight(tmp_path):
    feature = SimpleNamespace(id="feat-context-sanitize", slug="context-sanitize")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("ok", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=["pkg/ok.py", ".iriai-context/context.md"],
    )
    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        6,
        0,
        [result],
        tmp_path,
        context_label="test",
    )

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        6,
        "0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        sanitized,
        feature_root=tmp_path,
        known_task_ids={"TASK-1"},
    )

    assert verdict is None
    assert sanitized[0].files_modified == ["pkg/ok.py"]


@pytest.mark.asyncio
async def test_dag_repair_sanitizer_preserves_missing_product_paths(tmp_path):
    feature = SimpleNamespace(id="feat-invalid-sanitize", slug="invalid-sanitize")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    result = ImplementationResult(
        task_id="FIX-1",
        summary="done",
        files_modified=["missing.py"],
    )
    sanitized = await implementation_module._sanitize_dag_repair_results(
        runner,
        feature,
        7,
        0,
        [result],
        tmp_path,
        context_label="test",
    )

    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        7,
        "0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        sanitized,
        feature_root=tmp_path,
        known_task_ids={"TASK-1"},
    )

    assert isinstance(verdict, Verdict)
    assert any("missing.py" in issue.description for issue in verdict.concerns)


@pytest.mark.asyncio
async def test_parallel_dag_repair_runs_scheduled_fixes_with_primary_runtime(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-parallel-repair", slug="parallel-repair")
    workspace_root = tmp_path / "workspace"
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "pkg"
    _init_git_repo(repo)
    (repo / "a.py").write_text("a = 'base'\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add repair targets"], cwd=repo, check=True)

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "workspace_manager": SimpleNamespace(_base=workspace_root),
                "test_allow_sandbox_patch_promotion_bridge": True,
            }
            self.parallel_batches: list[list[str]] = []
            self.actor_runtimes: dict[str, str | None] = {}
            self.sandbox_cwds: list[Path] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.actor_runtimes[task.actor.name] = task.actor.role.metadata.get("runtime")
            if task.output_type is BugTriage:
                return BugTriage(
                    groups=[
                        BugGroup(
                            group_id="BG-1",
                            likely_root_cause="alpha",
                            issue_indices=[0],
                            severity="major",
                            affected_files_hint=["pkg/a.py"],
                        ),
                        BugGroup(
                            group_id="BG-2",
                            likely_root_cause="beta",
                            issue_indices=[1],
                            severity="major",
                            affected_files_hint=["pkg/b.py"],
                        ),
                    ],
                )
            if task.output_type is RootCauseAnalysis:
                suffix = "a.py" if "BG-1" in task.actor.name else "b.py"
                return RootCauseAnalysis(
                    hypothesis=f"fix {suffix}",
                    affected_files=[f"pkg/{suffix}"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                suffix = "a.py" if "BG-1" in task.actor.name else "b.py"
                metadata = task.actor.role.metadata
                assert metadata["sandbox_required"] is True
                binding = metadata["runtime_workspace_binding"]
                cwd = Path(binding["cwd"])
                self.sandbox_cwds.append(cwd)
                assert cwd != repo
                assert workspace_root in cwd.parents
                assert (repo / suffix).read_text(encoding="utf-8") == f"{suffix[0]} = 'base'\n"
                (cwd / suffix).write_text(f"{suffix[0]} = 'fixed'\n", encoding="utf-8")
                return ImplementationResult(
                    task_id=f"FIX-{suffix}",
                    summary=f"fixed {suffix}",
                    files_modified=[f"pkg/{suffix}"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        assert (repo / "a.py").read_text(encoding="utf-8") == "a = 'fixed'\n"
        assert (repo / "b.py").read_text(encoding="utf-8") == "b = 'fixed'\n"
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    verdict = Verdict(
        approved=False,
        summary="failed",
        concerns=[
            Issue(severity="major", description="alpha broken", file="pkg/a.py"),
            Issue(severity="major", description="beta broken", file="pkg/b.py"),
        ],
    )

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        5,
        0,
        verdict,
        [
            ImplementationTask(
                id="TASK-1",
                name="Task",
                description="Task",
                repo_path="pkg",
                file_scope=[
                    TaskFileScope(path="pkg/a.py", action="modify"),
                    TaskFileScope(path="pkg/b.py", action="modify"),
                ],
            )
        ],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert {result.task_id for result in results} == {"FIX-a.py", "FIX-b.py"}
    assert "dag-repair-triage:g5:retry-0" in runner.artifacts.store
    assert "dag-repair-rca:g5:BG-1:retry-0" in runner.artifacts.store
    assert "dag-repair-dispatch:g5:retry-0" in runner.artifacts.store
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g5:retry-0"])
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-1", "BG-2"]}]
    assert {
        "implementer-dag-g5-r0-fix-BG-1",
        "implementer-dag-g5-r0-fix-BG-2",
    }.issubset(runner.actor_runtimes)
    assert runner.actor_runtimes["bug-triager-dag-g5-r0-triage"] == "primary"
    assert runner.actor_runtimes["root-cause-analyst-dag-g5-r0-rca-BG-1"] == "primary"
    assert runner.actor_runtimes["implementer-dag-g5-r0-fix-BG-1"] == "primary"
    assert runner.actor_runtimes[
        "verifier-dag-g5-r0-focused-reverify-BG-1"
    ] == "primary"
    assert len(runner.sandbox_cwds) == 2
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 'fixed'\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 'fixed'\n"


@pytest.mark.asyncio
async def test_parallel_dag_repair_runtime_failure_records_typed_blocker(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(implementation_module.DAG_PARALLEL_REPAIR_ENV, raising=False)
    feature = SimpleNamespace(id="feat-parallel-runtime", slug="parallel-runtime")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                raise RuntimeError("triage provider unavailable")
            if task.output_type is ImplementationResult:
                raise AssertionError("normal repair must not run after RCA runtime failure")
            raise AssertionError(f"unexpected task: {task!r}")

    runner = _Runner()
    verdict = Verdict(
        approved=False,
        summary="failed",
        concerns=[
            Issue(severity="major", description="alpha broken", file="pkg/a.py"),
            Issue(severity="major", description="beta broken", file="pkg/b.py"),
        ],
    )

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        7,
        2,
        verdict,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert len(results) == 1
    assert results[0].status == "blocked"
    assert "SANDBOX_WORKFLOW_BLOCKER" in results[0].summary
    assert "Parallel DAG repair triage/RCA runtime failed" in results[0].summary
    assert ImplementationResult not in runner.output_types
    payload = json.loads(
        runner.artifacts.store["dag-runtime-failure:g7:parallel-rca-retry-2"]
    )
    assert payload["failure_type"] == "provider_internal_error"
    assert payload["legacy_failure_type"] == "provider_crash"
    assert payload["route"] == "quiesce"
    assert payload["route_decision"]["failure_type"] == "provider_internal_error"
    assert payload["route_decision"]["route"] == "quiesce"
    assert payload["blocked_before_product_repair"] is True


@pytest.mark.asyncio
async def test_post_dag_single_repair_runs_in_sandbox_before_commit(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-post-dag-repair", slug="post-dag-repair")
    workspace_root = tmp_path / "workspace"
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    repo = feature_root / "pkg"
    _init_git_repo(repo)
    (repo / "fix.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add fix target"], cwd=repo, check=True)

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def write_artifact_bytes(self, key: str, data: bytes, metadata, *, feature=None):
            del feature
            self.store[key] = data.decode("utf-8", "surrogateescape")
            self.store[f"{key}.metadata"] = json.dumps(metadata, sort_keys=True)
            return len([name for name in self.store if name.startswith("dag-sandbox-patch:")])

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "workspace_manager": SimpleNamespace(_base=workspace_root),
                "test_allow_sandbox_patch_promotion_bridge": True,
            }
            self.fix_actor_metadata: dict[str, object] = {}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="fix product code",
                    affected_files=["pkg/fix.py"],
                    proposed_approach="change the value",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                metadata = task.actor.role.metadata
                self.fix_actor_metadata = dict(metadata)
                assert metadata["sandbox_required"] is True
                binding = metadata["runtime_workspace_binding"]
                sandbox_cwd = Path(binding["cwd"])
                assert sandbox_cwd != repo
                assert workspace_root in sandbox_cwd.parents
                assert (repo / "fix.py").read_text(encoding="utf-8") == "value = 'base'\n"
                (sandbox_cwd / "fix.py").write_text("value = 'fixed'\n", encoding="utf-8")
                return ImplementationResult(
                    task_id="BUG-1",
                    summary="fixed in sandbox",
                    files_modified=["pkg/fix.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

    async def _no_commit(*_args, **_kwargs):
        assert (repo / "fix.py").read_text(encoding="utf-8") == "value = 'fixed'\n"
        return "commit"

    async def _no_regression(*_args, **_kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    monkeypatch.setattr(implementation_module, "_run_regression", _no_regression)
    runner = _Runner()

    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        "review failed",
        "review",
        implementation_module.verifier,
        implementation_module.implementer,
        "",
        bug_id="BUG-1",
        attempt_number=1,
    )

    assert attempt.re_verify_result == "PASS"
    assert runner.fix_actor_metadata["sandbox_required"] is True
    assert (repo / "fix.py").read_text(encoding="utf-8") == "value = 'fixed'\n"
    assert any(key.startswith("workspace-authority-snapshot:") for key in runner.artifacts.store)
    assert any(key.startswith("dag-sandbox-patch:") for key in runner.artifacts.store)


@pytest.mark.asyncio
async def test_parallel_dag_repair_autonomous_resolves_decision_only_contradiction(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-contradiction", slug="contradiction", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "decisions": "HUGE_DECISION_ARTIFACT_START" + ("x" * 200_000),
                "decisions:global": "global decision",
                "dag:strategy": "strategy",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.parallel_batches: list[list[str]] = []
            self.actor_runtimes: dict[str, str | None] = {}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            assert isinstance(task, Ask)
            self.prompts.append(task.prompt)
            self.actor_runtimes[task.actor.name] = task.actor.role.metadata.get("runtime")
            if task.output_type is BugTriage:
                return BugTriage(
                    groups=[
                        BugGroup(
                            group_id="BG-FIX",
                            likely_root_cause="ordinary bug",
                            issue_indices=[0],
                            severity="major",
                            affected_files_hint=["pkg/fix.py"],
                        ),
                        BugGroup(
                            group_id="BG-CONTRA",
                            likely_root_cause="event names conflict",
                            issue_indices=[1],
                            severity="major",
                            affected_files_hint=["pkg/events.py"],
                        ),
                    ],
                )
            if task.output_type is RootCauseAnalysis:
                if "BG-CONTRA" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="bare name conflicts with @v1",
                        evidence=["pkg/events.py:1 uses project_updated@v1"],
                        affected_files=["pkg/events.py"],
                        proposed_approach="ratify @v1",
                        confidence="contradiction",
                        contradiction_detail="bare vs @v1",
                    )
                return RootCauseAnalysis(
                    hypothesis="fix bug",
                    evidence=["pkg/fix.py"],
                    affected_files=["pkg/fix.py"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                assert "Read the context index first" in task.prompt
                assert "HUGE_DECISION_ARTIFACT_START" not in task.prompt
                assert len(task.prompt) < 10_000
                return implementation_module.DagContradictionResolution(
                    resolution="`@v1` event names are authoritative wire tokens.",
                    authoritative_sources=["pkg/events.py:1"],
                    superseded_expectation="bare event names are prose labels",
                    implementation_direction="Do not rename events.",
                    requires_code_change=False,
                    needs_human=False,
                    confidence="high",
                    rationale="Tests and consumers use @v1.",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-BG-FIX",
                    summary="fixed ordinary bug",
                    files_modified=["pkg/fix.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    verdict = Verdict(
        approved=False,
        summary="failed",
        concerns=[
            Issue(severity="major", description="ordinary bug", file="pkg/fix.py"),
            Issue(severity="major", description="event name conflict", file="pkg/events.py"),
        ],
    )

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        26,
        0,
        verdict,
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert {result.task_id for result in results} == {
        "CONTRADICTION-g26-r0-BG-CONTRA",
        "FIX-BG-FIX",
    }
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g26:retry-0"])
    assert dispatch["fixable_group_count"] == 1
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["human_needed_contradiction_count"] == 0
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-FIX"]}]
    assert implementation_module.CONTRADICTION_DECISIONS_KEY in runner.artifacts.store
    assert (
        "contradiction:dag-repair:g26:retry-0:BG-CONTRA"
        in runner.artifacts.store
    )
    assert runner.actor_runtimes[
        "root-cause-analyst-dag-g26-r0-contradiction-BG-CONTRA"
    ] == "secondary"
    assert runner.actor_runtimes["implementer-dag-g26-r0-fix-BG-FIX"] == "primary"


@pytest.mark.asyncio
async def test_parallel_dag_repair_contradiction_requiring_code_change_joins_schedule(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-contradiction-code", slug="contradiction-code", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.parallel_batches: list[list[str]] = []
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="conflict",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="conflict",
                    evidence=["pkg/code.py"],
                    affected_files=["pkg/code.py"],
                    proposed_approach="decide",
                    confidence="contradiction",
                    contradiction_detail="A vs B",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Source B wins.",
                    authoritative_sources=["pkg/spec.md:1"],
                    implementation_direction="Change code to Source B.",
                    requires_code_change=True,
                    needs_human=False,
                    confidence="high",
                    rationale="B is newer.",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-CONTRA",
                    summary="fixed contradiction code",
                    files_modified=["pkg/code.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        6,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[Issue(severity="major", description="conflict")],
            gaps=[Gap(category="coverage", severity="major", description="extra")],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["FIX-CONTRA"]
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g6:retry-0"])
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["fixable_group_count"] == 1
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-CONTRA"]}]
    assert "implementer-dag-g6-r0-fix-BG-CONTRA" in runner.run_actors


@pytest.mark.asyncio
async def test_parallel_dag_repair_mixed_contradiction_runs_artifact_and_code_tracks(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-mixed-contradiction", slug="mixed", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "autonomous_remainder": True,
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-MIXED",
                        likely_root_cause="spec and implementation conflict",
                        issue_indices=[0],
                        gap_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="spec artifact and code both need repair",
                    evidence=[".iriai-context/task-specs.md:1", "pkg/code.py:1"],
                    affected_files=[".iriai-context/task-specs.md", "pkg/code.py"],
                    proposed_approach="repair spec artifact, then wire code",
                    confidence="contradiction",
                    contradiction_detail="old spec vs canonical code",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Track 1 artifact_repair plus Track 2 code fixes.",
                    resolution_kind="mixed_repair",
                    authoritative_sources=[".iriai-context/task-specs.md:1"],
                    artifact_paths=[".iriai-context/task-specs.md"],
                    implementation_direction="Update pkg/code.py to match canonical contract.",
                    requires_code_change=True,
                    confidence="high",
                    rationale="Both layers are wrong.",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-MIXED",
                    group_id="BG-MIXED",
                    summary="artifact repaired",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            target_ref=".iriai-context/task-specs.md",
                            content="canonical specs\n",
                            summary="fix specs",
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-MIXED",
                    summary="fixed code",
                    files_modified=["pkg/code.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()

    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        65,
        1,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[Issue(severity="major", description="spec conflict")],
            gaps=[Gap(category="coverage", severity="major", description="code gap")],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert {result.task_id for result in results} == {"ARTIFACT-MIXED", "FIX-MIXED"}
    assert any("artifact-repair-BG-MIXED" in actor for actor in runner.run_actors)
    assert "implementer-dag-g65-r1-fix-BG-MIXED" in runner.run_actors
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g65:retry-1"])
    assert dispatch["resolved_contradiction_count"] == 1
    handoff = json.loads(
        runner.artifacts.store["dag-contradiction-handoff:g65:retry-1:BG-MIXED"]
    )
    assert handoff["resolution_kind"] == "mixed_repair"
    assert handoff["artifact_track"] is True
    assert handoff["code_track"] is True


@pytest.mark.asyncio
async def test_closed_gate_contradiction_artifact_recovers_without_lead_review(
    tmp_path,
):
    from iriai_build_v2.services.artifacts import _key_to_path

    feature = SimpleNamespace(id="feat-g65", slug="g65", metadata={})
    artifact_key = "contradiction:verify:dag-g65-r1"

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Mirror:
        def feature_dir(self, feature_id: str) -> Path:
            return tmp_path / "artifacts" / "features" / feature_id

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(),
                "autonomous_remainder": True,
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.run_called = False

        async def run(self, *args, **kwargs):
            del args, kwargs
            self.run_called = True
            raise AssertionError("closed gate recovery should not invoke an actor")

    mirror_root = _Mirror().feature_dir(feature.id)
    closed_path = mirror_root / ".staging" / _key_to_path(artifact_key)
    closed_path.parent.mkdir(parents=True, exist_ok=True)
    closed_path.write_text(
        "# Contradiction Resolution — verify:dag-g65-r1\n\n"
        "**Status:** Resolved — gate closed and artifact finalized.\n\n"
        "Track 1 — artifact_repair of `.iriai-context/g65-expanded-verify-r1-task-specs.md`.\n"
        "Track 2 — code fixes for `pkg/code.py`.\n",
        encoding="utf-8",
    )
    runner = _Runner()
    planned = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="dag-g65-r1",
            likely_root_cause="mixed contradiction",
            severity="blocker",
        ),
        rca=RootCauseAnalysis(
            hypothesis="mixed contradiction",
            evidence=["verifier:g65"],
            affected_files=[".iriai-context/g65-expanded-verify-r1-task-specs.md", "pkg/code.py"],
            proposed_approach="use closed gate",
            confidence="contradiction",
            contradiction_detail="spec vs code",
        ),
        issue_text="mixed contradiction",
        rca_key="dag-verify-rca:g65:retry-1",
    )

    outcome = await implementation_module._resolve_or_recover_dag_contradiction(
        runner,
        feature,
        65,
        1,
        planned,
        group_tasks=[ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        runtime="secondary",
        feedback="failed",
    )

    assert runner.run_called is False
    assert outcome.resolution is not None
    assert outcome.resolution.resolution_kind == "mixed_repair"
    assert (
        "contradiction:dag-repair:g65:retry-1:dag-g65-r1"
        in runner.artifacts.store
    )
    handoff = json.loads(
        runner.artifacts.store["dag-contradiction-handoff:g65:retry-1:dag-g65-r1"]
    )
    assert handoff["status"] == "accepted"
    assert handoff["skipped_lead_review_reason"] == "closed_gate_recovered_before_lead_review"


@pytest.mark.asyncio
async def test_parallel_dag_repair_normalizes_legacy_contradiction_confidence(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-legacy-confidence", slug="legacy-confidence", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="stale expectation",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="old expectation conflicts with current tests",
                    evidence=["pkg/test_current.py"],
                    affected_files=["pkg/current.py"],
                    proposed_approach="current tests win",
                    confidence="contradiction",
                    contradiction_detail="old vs current",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Current tests prove the finding is stale.",
                    resolution_kind="stale_not_reproducing",
                    authoritative_sources=["pkg/test_current.py:10"],
                    requires_code_change=False,
                    needs_human=False,
                    confidence="contradiction",
                    rationale="Resolver used legacy RCA confidence spelling.",
                )
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        9,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="stale env finding"),
                Issue(severity="major", description="same stale finding"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == [
        "CONTRADICTION-g9-r0-BG-CONTRA"
    ]
    accepted = json.loads(
        runner.artifacts.store["contradiction:dag-repair:g9:retry-0:BG-CONTRA"]
    )
    assert accepted["resolution_kind"] == "stale_not_reproducing"
    assert accepted["confidence"] == "medium"
    assert accepted["requires_code_change"] is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g9:retry-0"])
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["rejected_contradiction_count"] == 0
    assert dispatch["human_needed_contradiction_count"] == 0


@pytest.mark.asyncio
async def test_parallel_dag_repair_artifact_repair_uses_dedicated_lane(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-artifact-repair", slug="artifact-repair", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "autonomous_remainder": True,
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-ARTIFACT",
                        likely_root_cause="manifest path drift",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="manifest path drift",
                    evidence=[".iriai-context/changed-files.md"],
                    affected_files=[
                        "src/vs/workbench/contrib/iriaiStudio/browser/views/"
                        "components/WorkflowCard.tsx"
                    ],
                    proposed_approach="normalize manifest",
                    confidence="contradiction",
                    contradiction_detail="manifest vs repo",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Manifest paths should be normalized to the canonical layout.",
                    resolution_kind="artifact_repair",
                    authoritative_sources=["repo/pyproject.toml:1"],
                    artifact_paths=[".iriai-context/changed-files.md", "context"],
                    implementation_direction=(
                        "Patch .iriai-context/changed-files.md so it no longer "
                        "points at stale product paths."
                    ),
                    requires_code_change=False,
                    needs_human=False,
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-FIX",
                    group_id="BG-ARTIFACT",
                    summary="normalized artifact",
                    artifacts_modified=[".iriai-context/changed-files.md"],
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="context",
                            content="normalized artifact context",
                            summary="removed stale surface path",
                        )
                    ],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        10,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="manifest path drift"),
                Issue(severity="major", description="preflight blocked"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-FIX"]
    assert "implementer-dag-g10-r0-artifact-repair-BG-ARTIFACT" in runner.run_actors
    assert "implementer-dag-g10-r0-fix-BG-ARTIFACT" not in runner.run_actors
    assert runner.artifacts.store["context"] == "normalized artifact context"
    repair = json.loads(
        runner.artifacts.store["dag-artifact-repair:g10:BG-ARTIFACT:retry-0"]
    )
    assert repair["target_refs"] == [
        ".iriai-context/changed-files.md",
        "context",
    ]
    applied_update = repair["artifact_update_application"]["applied_updates"][0]
    assert applied_update["artifact_key"] == "context"
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g10:retry-0"])
    assert dispatch["resolved_contradiction_count"] == 1
    assert dispatch["artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_metadata_only_high_confidence_routes_artifact_lane(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-meta-route", slug="meta-route", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "autonomous_remainder": True,
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.run_actors: list[str] = []
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-META",
                        likely_root_cause=(
                            "manifest drift in orchestration metadata"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                        affected_files_hint=[
                            ".iriai/artifacts/features/feat-meta-route/"
                            ".iriai-context/g28-changed-files.md",
                            "repo/src/live-code.ts",
                        ],
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "This is not a code defect; metadata-only repair is "
                        "needed for the changed-files artifact."
                    ),
                    evidence=[
                        ".iriai/artifacts/features/feat-meta-route/"
                        ".iriai-context/g28-changed-files.md:1",
                        "repo/src/live-code.ts is evidence only",
                    ],
                    affected_files=[
                        ".iriai/artifacts/features/feat-meta-route/"
                        ".iriai-context/g28-changed-files.md",
                        "repo/src/live-code.ts",
                    ],
                    proposed_approach=(
                        "Do not touch source. Replace the stale changed-files "
                        "artifact with the current metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-META",
                    group_id="BG-META",
                    summary="repaired metadata",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag:strategy",
                            content='{"ok": true}',
                            summary="store update",
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
                return ImplementationResult(task_id="BAD-FIX", summary="bad")
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        13,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="stale metadata"),
                Issue(severity="major", description="preflight blocked"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-META"]
    assert runner.fix_attempted is False
    assert "implementer-dag-g13-r0-artifact-repair-BG-META" in runner.run_actors
    assert "implementer-dag-g13-r0-fix-BG-META" not in runner.run_actors
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g13:retry-0"])
    assert dispatch["metadata_artifact_repair_group_count"] == 1
    assert dispatch["artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_blocked_metadata_fix_reroutes_artifact_lane(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-blocked-route", slug="blocked-route", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "autonomous_remainder": True,
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.run_actors: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.run_actors.append(task.actor.name)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-BLOCKED-META",
                        likely_root_cause="context file stale",
                        issue_indices=[0],
                        severity="major",
                        affected_files_hint=[".iriai-context/g28-results.md"],
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="stale context file",
                    affected_files=[".iriai-context/g28-results.md"],
                    proposed_approach="Patch the context file.",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                return ImplementationResult(
                    task_id="FIX-BG-BLOCKED-META",
                    summary="blocked by boundary",
                    status="blocked",
                    notes=(
                        ".iriai-context/g28-results.md is outside workspace "
                        "write boundary for this implementer."
                    ),
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-BLOCKED-META",
                    group_id="BG-BLOCKED-META",
                    summary="repaired context",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag:strategy",
                            content='{"rerouted": true}',
                        )
                    ],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        14,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="stale context"),
                Issue(severity="major", description="preflight blocked"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-BLOCKED-META"]
    assert "implementer-dag-g14-r0-fix-BG-BLOCKED-META" in runner.run_actors
    assert (
        "implementer-dag-g14-r0-artifact-repair-BG-BLOCKED-META"
        in runner.run_actors
    )
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g14:retry-0"])
    assert dispatch["metadata_artifact_repair_group_count"] == 0
    assert dispatch["artifact_repair_group_count"] == 1


@pytest.mark.asyncio
async def test_artifact_repair_update_writes_allowed_target_ref(tmp_path):
    feature = SimpleNamespace(id="feat-target-ref", slug="target-ref", metadata={})
    feature_root = tmp_path / "repos"
    feature_root.mkdir()

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    result = ArtifactRepairResult(
        task_id="ARTIFACT-TARGET",
        group_id="BG-TARGET",
        summary="target refs",
        artifact_updates=[
            ArtifactRepairUpdate(
                target_ref="repo/.iriai-context/handover.md",
                content="fixed handover",
                summary="allowed context",
            ),
            ArtifactRepairUpdate(
                target_ref="repo/src/app.ts",
                content="bad product edit",
                summary="blocked product",
            ),
        ],
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        result,
        feature_root,
    )

    assert (
        feature_root / "repo/.iriai-context/handover.md"
    ).read_text(encoding="utf-8") == "fixed handover"
    assert record["applied_target_updates"][0]["target_ref"] == (
        "repo/.iriai-context/handover.md"
    )
    assert record["skipped_updates"][0]["target_ref"] == "repo/src/app.ts"
    assert record["skipped_updates"][0]["reason"] == (
        "target_ref_not_artifact_context"
    )


@pytest.mark.asyncio
async def test_artifact_repair_update_writes_valid_dag_task_artifact(tmp_path):
    feature = SimpleNamespace(id="feat-dag-task", slug="dag-task", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    for path in [
        "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
        "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        "src/webviews/projectSurface/src/styles/dashboard.css",
    ]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    task_result = ImplementationResult(
        task_id="TASK-S18-3",
        summary="canonical D-GR-1 paths",
        files_created=[
            "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
            "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        ],
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G-STale",
            summary="repair dag task",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-S18-3",
                    content=task_result.model_dump_json(),
                    summary="replace stale task row",
                )
            ],
        ),
        feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-S18-3"]
    )
    assert stored.files_created == task_result.files_created
    assert stored.files_modified == task_result.files_modified
    assert record["applied_updates"][0]["artifact_kind"] == "dag_task"
    assert record["applied_updates"][0]["task_id"] == "TASK-S18-3"


@pytest.mark.asyncio
async def test_artifact_repair_update_writes_derived_dag_without_active_overwrite(tmp_path):
    feature = SimpleNamespace(id="feat-derived-dag", slug="derived-dag", metadata={})
    feature_root = tmp_path / "repos"
    feature_root.mkdir()

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "dag:accounts": '{"active": true}',
            }

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    candidate = DerivedDAGArtifact(
        artifact_key="derived-dag:accounts:worker-b",
        source_dag_key="dag:accounts",
        dag=ImplementationDAG(
            tasks=[
                ImplementationTask(
                    id="TASK-1",
                    name="Task",
                    description="Task",
                )
            ],
            execution_order=[["TASK-1"]],
            complete=True,
        ),
        derivation_reason="stage activation plan",
        activation_plan=["quiesce after group 44", "activate before group 45"],
        complete=True,
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="DERIVED-DAG",
            group_id="G-DERIVED",
            summary="stage derived dag",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="derived-dag:accounts:worker-b",
                    content=candidate.model_dump_json(),
                    summary="candidate only",
                )
            ],
        ),
        feature_root,
    )

    assert runner.artifacts.store["dag:accounts"] == '{"active": true}'
    stored = DerivedDAGArtifact.model_validate_json(
        runner.artifacts.store["derived-dag:accounts:worker-b"]
    )
    assert stored.source_dag_key == "dag:accounts"
    assert record["applied_updates"][0]["artifact_kind"] == "derived_dag"


def test_derived_dag_validation_rejects_active_dag_key():
    derived, reason, _validation = (
        implementation_module._validate_derived_dag_artifact_update(
            "dag:accounts",
            "{}",
        )
    )

    assert derived is None
    assert reason == "not_derived_dag_artifact"


def _regroup_candidate(**updates) -> DerivedDAGArtifact:
    payload = {
        "artifact_key": "dag-regroup:g45-g73",
        "source_dag_key": "dag",
        "base_dag_artifact_id": 123,
        "checkpointed_group": 44,
        "group_idx_offset": 45,
        "original_execution_order": [["TASK-A"], ["TASK-B"]],
        "original_to_new_group_mapping": {"45": [45], "46": [46]},
        "activation_contract": ["check dag-group:45 does not exist before activation"],
        "rollback_plan": ["restore original G45-G73 order"],
        "dag": ImplementationDAG(
            tasks=[
                ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                ImplementationTask(
                    id="TASK-B",
                    name="Task B",
                    description="Task B",
                    dependencies=["TASK-A"],
                ),
            ],
            execution_order=[["TASK-A"], ["TASK-B"]],
            complete=True,
        ),
        "complete": True,
    }
    payload.update(updates)
    return DerivedDAGArtifact(**payload)


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (
            _regroup_candidate(group_idx_offset=46),
            "dag_regroup_offset_mismatch",
        ),
        (
            _regroup_candidate(original_to_new_group_mapping={}),
            "dag_regroup_missing_original_mapping",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                        ImplementationTask(
                            id="TASK-B",
                            name="Task B",
                            description="Task B",
                            dependencies=["TASK-A"],
                        ),
                    ],
                    execution_order=[["TASK-A", "TASK-B"]],
                    complete=True,
                )
            ),
            "derived_dag_dependency_order_invalid",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                        ImplementationTask(id="TASK-B", name="Task B", description="Task B"),
                    ],
                    execution_order=[["TASK-A"]],
                    complete=True,
                )
            ),
            "derived_dag_execution_order_mismatch",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                        ImplementationTask(
                            id="TASK-B",
                            name="Task B",
                            description="Task B",
                            dependencies=["TASK-A"],
                        ),
                    ],
                    execution_order=[["TASK-B"], ["TASK-A"]],
                    complete=True,
                )
            ),
            "derived_dag_dependency_order_invalid",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                        ImplementationTask(id="TASK-B", name="Task B", description="Task B"),
                    ],
                    execution_order=[["TASK-A", "TASK-B"]],
                    complete=True,
                ),
                write_sets={
                    "TASK-A": ["src/shared.ts"],
                    "TASK-B": ["src/shared.ts"],
                },
            ),
            "derived_dag_write_set_conflict",
        ),
    ],
)
def test_regroup_validation_rejects_invalid_derived_dags(candidate, reason):
    derived, actual_reason, _validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
    )

    assert derived is None
    assert actual_reason == reason


def _base_dag_for_regroup() -> ImplementationDAG:
    return ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
            ImplementationTask(
                id="TASK-B",
                name="Task B",
                description="Task B",
                dependencies=["TASK-A"],
            ),
        ],
        execution_order=[*([[]] * 45), ["TASK-A"], ["TASK-B"]],
        complete=True,
    )


@pytest.mark.asyncio
async def test_artifact_repair_update_rejects_regroup_with_dropped_dependency(tmp_path):
    feature = SimpleNamespace(id="feat-regroup-apply", slug="regroup-apply", metadata={})
    feature_root = tmp_path / "repos"
    feature_root.mkdir()
    base_dag = _base_dag_for_regroup()
    invalid_candidate = _regroup_candidate(
        dag=ImplementationDAG(
            tasks=[
                ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                ImplementationTask(id="TASK-B", name="Task B", description="Task B"),
            ],
            execution_order=[["TASK-A", "TASK-B"]],
            complete=True,
        )
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"dag": base_dag.model_dump_json()}
            self.puts: list[tuple[str, str]] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 123, "created_at": "now", "value": value}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.puts.append((key, value))
            self.store[key] = value

    artifacts = _Artifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="REGROUP",
            group_id="G-REGROUP",
            summary="attempt invalid regroup",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-regroup:g45-g73",
                    content=invalid_candidate.model_dump_json(),
                )
            ],
        ),
        feature_root,
    )

    assert artifacts.puts == []
    assert record["applied_updates"] == []
    assert record["skipped_updates"][0]["reason"] == (
        "dag_regroup_dependency_preservation_mismatch"
    )


def test_regroup_validation_accepts_preserved_tasks_with_base_context():
    candidate = _regroup_candidate()

    derived, reason, _validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=_base_dag_for_regroup(),
        base_dag_artifact_id=123,
        require_regroup_context=True,
    )

    assert reason == ""
    assert derived is not None


def test_regroup_validation_accepts_merged_independent_original_groups():
    base_dag = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-A",
                name="Task A",
                description="Task A",
                files=["src/a.ts"],
            ),
            ImplementationTask(
                id="TASK-C",
                name="Task C",
                description="Task C",
                files=["src/c.ts"],
            ),
        ],
        execution_order=[*([[]] * 45), ["TASK-A"], ["TASK-C"]],
        complete=True,
    )
    candidate = _regroup_candidate(
        original_execution_order=[["TASK-A"], ["TASK-C"]],
        original_to_new_group_mapping={"45": [45], "46": [45]},
        dag=ImplementationDAG(
            tasks=[
                ImplementationTask(
                    id="TASK-A",
                    name="Task A",
                    description="Task A",
                    files=["src/a.ts"],
                ),
                ImplementationTask(
                    id="TASK-C",
                    name="Task C",
                    description="Task C",
                    files=["src/c.ts"],
                ),
            ],
            execution_order=[["TASK-A", "TASK-C"]],
            complete=True,
        ),
    )

    derived, reason, _validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base_dag,
        base_dag_artifact_id=123,
        require_regroup_context=True,
    )

    assert reason == ""
    assert derived is not None


def test_regroup_validation_rejects_merged_groups_without_write_set_coverage():
    base_dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
            ImplementationTask(id="TASK-C", name="Task C", description="Task C"),
        ],
        execution_order=[*([[]] * 45), ["TASK-A"], ["TASK-C"]],
        complete=True,
    )
    candidate = _regroup_candidate(
        original_execution_order=[["TASK-A"], ["TASK-C"]],
        original_to_new_group_mapping={"45": [45], "46": [45]},
        dag=ImplementationDAG(
            tasks=[
                ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                ImplementationTask(id="TASK-C", name="Task C", description="Task C"),
            ],
            execution_order=[["TASK-A", "TASK-C"]],
            complete=True,
        ),
    )

    derived, reason, validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base_dag,
        base_dag_artifact_id=123,
        require_regroup_context=True,
    )

    assert derived is None
    assert reason == "dag_regroup_missing_write_set_coverage"
    assert validation[0]["missing_task_ids"] == ["TASK-A", "TASK-C"]


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[ImplementationTask(id="TASK-A", name="Task A", description="Task A")],
                    execution_order=[["TASK-A"]],
                    complete=True,
                )
            ),
            "dag_regroup_task_preservation_mismatch",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                        ImplementationTask(id="TASK-B", name="Task B", description="Task B"),
                        ImplementationTask(id="TASK-C", name="Task C", description="Task C"),
                    ],
                    execution_order=[["TASK-A"], ["TASK-B"], ["TASK-C"]],
                    complete=True,
                ),
                original_to_new_group_mapping={"45": [45], "46": [46]},
            ),
            "dag_regroup_task_preservation_mismatch",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Task A"),
                        ImplementationTask(id="TASK-B", name="Task B", description="Task B"),
                    ],
                    execution_order=[["TASK-A", "TASK-B"]],
                    complete=True,
                )
            ),
            "dag_regroup_dependency_preservation_mismatch",
        ),
        (
            _regroup_candidate(
                dag=ImplementationDAG(
                    tasks=[
                        ImplementationTask(id="TASK-A", name="Task A", description="Mutated Task A"),
                        ImplementationTask(
                            id="TASK-B",
                            name="Task B",
                            description="Task B",
                            dependencies=["TASK-A"],
                        ),
                    ],
                    execution_order=[["TASK-A"], ["TASK-B"]],
                    complete=True,
                )
            ),
            "dag_regroup_task_definition_mismatch",
        ),
        (
            _regroup_candidate(original_execution_order=[["TASK-A", "TASK-B"]]),
            "dag_regroup_original_execution_order_mismatch",
        ),
        (
            _regroup_candidate(original_to_new_group_mapping={"45": [45]}),
            "dag_regroup_original_mapping_mismatch",
        ),
        (
            _regroup_candidate(original_to_new_group_mapping={"45": [45], "46": [99]}),
            "dag_regroup_original_mapping_invalid",
        ),
        (
            _regroup_candidate(original_to_new_group_mapping={"45": [45], "46": [46], "foo": [45]}),
            "dag_regroup_original_mapping_invalid",
        ),
        (
            _regroup_candidate(original_to_new_group_mapping={"45": [46], "46": [45]}),
            "dag_regroup_original_mapping_task_mismatch",
        ),
        (
            _regroup_candidate(base_dag_artifact_id=999),
            "dag_regroup_base_dag_artifact_mismatch",
        ),
    ],
)
def test_regroup_validation_rejects_context_mismatches(candidate, reason):
    derived, actual_reason, _validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=_base_dag_for_regroup(),
        base_dag_artifact_id=123,
        require_regroup_context=True,
    )

    assert derived is None
    assert actual_reason == reason


def test_regroup_validation_rejects_existing_boundary_checkpoint():
    candidate = _regroup_candidate()

    derived, reason, validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=_base_dag_for_regroup(),
        base_dag_artifact_id=123,
        boundary_checkpoint_exists=True,
        require_regroup_context=True,
    )

    assert derived is None
    assert reason == "dag_regroup_boundary_checkpoint_exists"
    assert validation[0]["checkpoint_key"] == "dag-group:45"


@pytest.mark.asyncio
async def test_artifact_repair_update_rejects_active_dag_key(tmp_path):
    feature = SimpleNamespace(id="feat-active-dag-reject", slug="active-dag-reject", metadata={})
    feature_root = tmp_path / "repos"
    feature_root.mkdir()

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {"dag:accounts": '{"active": true}'}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ACTIVE-DAG",
            group_id="G-ACTIVE",
            summary="attempted active dag overwrite",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag:accounts",
                    content='{"active": false}',
                    summary="unsafe active dag overwrite",
                )
            ],
        ),
        feature_root,
    )

    assert runner.artifacts.store["dag:accounts"] == '{"active": true}'
    assert record["applied_updates"] == []
    assert record["skipped_updates"][0]["reason"] == "unsafe_artifact_key"


@pytest.mark.asyncio
async def test_quiesce_hook_runs_after_group_44_before_group_45(monkeypatch):
    monkeypatch.delenv(implementation_module.DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)
    feature = SimpleNamespace(id="feat-quiesce", slug="quiesce", metadata={})
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-45", name="Task 45", description="Task 45")
        ],
        execution_order=[*([] for _ in range(45)), ["TASK-45"]],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "dag-group:44": json.dumps({"group_idx": 44}),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    calls: list[dict[str, int]] = []

    def _hook(**kwargs):
        calls.append({
            "after": kwargs["after_group_idx"],
            "before": kwargs["before_group_idx"],
        })
        return {"approved": True, "status": "ready"}

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"dag_quiesce_hook": _hook},
    )

    failure = await implementation_module._maybe_quiesce_before_group_dispatch(
        runner,
        feature,
        dag,
        group_idx=45,
    )

    assert failure == ""
    assert calls == [{"after": 44, "before": 45}]
    marker = json.loads(runner.artifacts.store["dag-quiesce:g44-before-g45"])
    assert marker["status"] == "complete"
    assert marker["hook_result"]["status"] == "ready"
    assert marker["next_group_task_ids"] == ["TASK-45"]
    assert marker["completed_checkpoint_range"] == list(range(45))
    assert marker["dag_sha256"]


@pytest.mark.asyncio
async def test_implement_dag_quiesce_returns_terminal_state_without_dispatch(monkeypatch):
    monkeypatch.delenv(implementation_module.DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)
    feature = SimpleNamespace(id="feat-quiesce-blocked", slug="quiesce-blocked", metadata={})
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="TASK-45", name="Task 45", description="Task 45")
        ],
        execution_order=[*([] for _ in range(45)), ["TASK-45"]],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-group:{idx}": json.dumps({"group_idx": idx, "results": []})
                for idx in range(45)
            }
            self.store[f"execution-control-adoption:{feature.id}"] = (
                _strict_adoption_marker(
                    feature,
                    completed_range=(0, 44),
                    next_group=45,
                )
            )

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    dispatches: list[object] = []

    def _hook(**kwargs):
        del kwargs
        return {"approved": False, "status": "paused", "reason": "operator regroup boundary"}

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"dag_quiesce_hook": _hook}

        async def parallel(self, tasks, feature):
            dispatches.extend(tasks)
            return []

        async def run(self, *args, **kwargs):
            raise AssertionError("quiesced DAG must not enter repair or verifier execution")

    async def _fresh_checkpoint(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        implementation_module,
        "_dag_group_checkpoint_is_fresh",
        _fresh_checkpoint,
    )

    outcome = await implementation_module._implement_dag(_Runner(), feature, dag)

    assert outcome.terminal_state == "quiesced"
    assert "operator regroup boundary" in outcome.failure
    assert dispatches == []


@pytest.mark.asyncio
async def test_quiesce_marker_with_stale_identity_runs_hook_again(monkeypatch):
    monkeypatch.delenv(implementation_module.DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)
    feature = SimpleNamespace(id="feat-quiesce-stale", slug="quiesce-stale", metadata={})
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-45", name="Task 45", description="Task 45")],
        execution_order=[*([] for _ in range(45)), ["TASK-45"]],
        complete=True,
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                "dag-group:44": json.dumps({"group_idx": 44}),
                "dag-quiesce:g44-before-g45": json.dumps(
                    {
                        "status": "complete",
                        "dag_sha256": "stale",
                        "next_group_task_ids": ["OLD-TASK"],
                    }
                ),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    calls: list[str] = []

    def _hook(**kwargs):
        del kwargs
        calls.append("hook")
        return {"approved": True, "status": "ready"}

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"dag_quiesce_hook": _hook})

    failure = await implementation_module._maybe_quiesce_before_group_dispatch(
        runner,
        feature,
        dag,
        group_idx=45,
    )

    assert failure == ""
    assert calls == ["hook"]
    marker = json.loads(runner.artifacts.store["dag-quiesce:g44-before-g45"])
    assert marker["dag_sha256"] != "stale"
    assert marker["next_group_task_ids"] == ["TASK-45"]


@pytest.mark.asyncio
async def test_artifact_repair_update_rejects_invalid_dag_task_artifacts(tmp_path):
    feature = SimpleNamespace(id="feat-dag-task-reject", slug="dag-task-reject", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    existing = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    existing.parent.mkdir(parents=True)
    existing.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/iriaiStudio/browser/"
                        "views/components/WorkflowCard.tsx"
                    ),
                    "source": "D-GR-1",
                }
            ],
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    def _result(task_id: str, *, status: str = "completed", path: str | None = None) -> str:
        return ImplementationResult(
            task_id=task_id,
            summary="candidate",
            status=status,
            files_modified=[path or "src/webviews/projectSurface/src/styles/dashboard.css"],
        ).model_dump_json()

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G-STale",
            summary="bad dag task repairs",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-MISMATCH",
                    content=_result("OTHER"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-BLOCKED",
                    content=_result("TASK-BLOCKED", status="blocked"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-BADJSON",
                    content="{not json",
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-EMPTY",
                    content=ImplementationResult(
                        task_id="TASK-EMPTY",
                        summary="empty",
                        files_created=[],
                        files_modified=[],
                    ).model_dump_json(),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-MISSING",
                    content=_result("TASK-MISSING", path="src/missing.ts"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-CONTEXT",
                    content=_result("TASK-CONTEXT", path=".iriai-context/report.md"),
                ),
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-FORBIDDEN",
                    content=_result(
                        "TASK-FORBIDDEN",
                        path=(
                            "src/vs/workbench/contrib/iriaiStudio/browser/"
                            "views/components/WorkflowCard.tsx"
                        ),
                    ),
                ),
            ],
        ),
        feature_root,
    )

    assert runner.artifacts.store == {}
    assert [item["reason"] for item in record["skipped_updates"]] == [
        "dag_task_id_mismatch",
        "dag_task_status_not_completed_or_partial",
        "invalid_dag_task_result_json",
        "dag_task_no_reported_files",
        "dag_task_invalid_product",
        "dag_task_artifact_context",
        "dag_task_forbidden_path",
    ]


def test_collect_files_dedupes_preserving_first_seen_order():
    files = implementation_module._collect_files([
        ImplementationResult(
            task_id="A",
            summary="a",
            files_created=["pkg/a.py", "pkg/shared.py"],
            files_modified=["pkg/shared.py", "pkg/b.py"],
        ),
        ImplementationResult(
            task_id="B",
            summary="b",
            files_modified=["pkg/a.py", "pkg/c.py"],
        ),
    ])

    assert files == ["pkg/a.py", "pkg/shared.py", "pkg/b.py", "pkg/c.py"]


@pytest.mark.asyncio
async def test_dag_preflight_labels_missing_forbidden_reported_files(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-preflight", slug="forbidden", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                "src/vs/workbench/contrib/iriaiStudio/browser/views/components/"
                "WorkflowCard.tsx"
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="reported stale path",
                files_modified=[
                    "iriai-studio/src/vs/workbench/contrib/iriaiStudio/"
                    "browser/views/components/WorkflowCard.tsx"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" in (
        verdict.concerns[0].description
    )


@pytest.mark.asyncio
async def test_dag_preflight_reads_dict_shaped_forbidden_files(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-dict", slug="forbidden-dict", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": "src/webviews/projectSurface/src/styles/dashboard.css",
                    "source": "TASK-1 canonical D-GR-1",
                }
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/iriaiStudio/browser/"
                        "styles/workflow-card.css"
                    ),
                    "source": "D-GR-1",
                }
            ]
        }),
        encoding="utf-8",
    )
    canonical = (
        tmp_path
        / "iriai-studio/src/webviews/projectSurface/src/styles/dashboard.css"
    )
    (tmp_path / "iriai-studio/.git").mkdir(parents=True, exist_ok=True)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="reported stale path",
                files_modified=[
                    "src/vs/workbench/contrib/iriaiStudio/browser/"
                    "styles/workflow-card.css"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" in (
        verdict.concerns[0].description
    )
    report = json.loads(next(iter(runner.artifacts.store.values())))
    path_problem = report["path_problems"][0]
    assert path_problem["forbidden_source"] == "D-GR-1"
    assert path_problem["candidate_evidence"][0]["path"] == (
        "src/webviews/projectSurface/src/styles/dashboard.css"
    )
    assert path_problem["candidate_evidence"][0]["exists"] is True


@pytest.mark.asyncio
async def test_dag_preflight_does_not_suffix_match_manifest_forbidden_paths(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-suffix", slug="forbidden-suffix", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": "src/generated/test/foo.ts",
                    "source": "D-GR-retired-generated",
                }
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        31,
        "retry-0",
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-1",
                summary="reported unrelated missing path",
                files_modified=["test/foo.ts"],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" not in (
        verdict.concerns[0].description
    )
    assert "reports changed file that is missing" in verdict.concerns[0].description
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g31:retry-retry-0"])
    problem = report["path_problems"][0]
    assert problem["reason"] == "missing"


@pytest.mark.asyncio
async def test_dag_preflight_rejects_absolute_outside_result_path(tmp_path):
    feature = SimpleNamespace(id="feat-absolute-outside", slug="absolute-outside", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    outside_file = tmp_path / "outside" / "leak.py"
    outside_file.parent.mkdir()
    outside_file.write_text("outside\n", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        32,
        "retry-0",
        [ImplementationTask(id="TASK-ABS", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-ABS",
                summary="reported host path",
                files_modified=[str(outside_file)],
            )
        ],
        feature_root=feature_root,
    )

    assert verdict is not None
    assert "outside-root" in verdict.concerns[0].description
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g32:retry-retry-0"])
    problem = report["path_problems"][0]
    assert problem["reason"] == "outside_root"
    assert problem["path"] == str(outside_file)
    assert problem["outside_root_exists"] is True
    assert problem["exists_on_disk"] is False
    assert problem["noncanonical_path"] is True
    assert problem["repair_route"] == "artifact_only"


@pytest.mark.asyncio
async def test_dag_preflight_matches_forbidden_directory_descendants(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-dir", slug="forbidden-dir", metadata={})
    config_path = tmp_path / "iriai-studio/scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": "src/vs/workbench/contrib/iriaiStudio",
                    "source": "D-GR-1-retired-tree",
                }
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        29,
        "retry-1",
        [ImplementationTask(id="TASK-S18-4", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-S18-4",
                summary="reported retired test tree",
                files_created=[
                    "iriai-studio/src/vs/workbench/contrib/iriaiStudio/"
                    "test/integration/reconnect.test.ts"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "forbidden/stale by verify-file-scope.expected-files.json" in (
        verdict.concerns[0].description
    )
    assert "dag-task:TASK-S18-4" in verdict.concerns[0].description


@pytest.mark.asyncio
async def test_dag_preflight_fails_forbidden_task_spec_path(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-spec", slug="forbidden-spec", metadata={})
    repo = tmp_path / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts",
                    "source": "TASK-SH2-1 canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "D-GR-1 retired chat tree",
                }
            ],
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        30,
        "retry-0",
        [
            ImplementationTask.model_validate({
                "id": task_id,
                "name": "Dedup",
                "description": "Dedup",
                "file_scope": [
                    {
                        "path": (
                            "iriai-studio/src/vs/workbench/contrib/"
                            "studioWorkflow/browser/workflowTab/chat/util/"
                            "eventDeduplicator.ts"
                        ),
                        "action": "create",
                    }
                ],
                "repo_path": "iriai-studio",
                "subfeature_id": "chat-sidepane-shell",
            })
        ],
        [
            ImplementationResult(
                task_id=task_id,
                summary="canonical result",
                files_modified=[
                    "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "task spec file_scope[0].path references" in verdict.concerns[0].description
    assert "dag-fragment:chat-sidepane-shell:slice-3" in (
        verdict.concerns[0].description
    )
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g30:retry-retry-0"])
    problem = report["path_problems"][0]
    assert problem["reason"] == "forbidden_task_spec"
    assert problem["repair_route"] == "artifact_only"
    assert problem["source_artifact_ref"] == "dag-fragment:chat-sidepane-shell:slice-3"


@pytest.mark.asyncio
async def test_dag_preflight_fails_manifest_forbidden_file_on_disk(tmp_path):
    feature = SimpleNamespace(id="feat-forbidden-disk", slug="forbidden-disk", metadata={})
    repo = tmp_path / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old", encoding="utf-8")
    canonical = repo / "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts",
                    "source": "TASK-chat-util-dedup canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "D-GR-1 retired workflowTab chat util",
                }
            ],
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    verdict = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        30,
        "retry-0",
        [ImplementationTask(id="TASK-chat-util-dedup", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-chat-util-dedup",
                summary="canonical",
                files_modified=[
                    "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
                ],
            )
        ],
        feature_root=tmp_path,
    )

    assert verdict is not None
    assert "manifest-forbidden path exists" in verdict.concerns[0].description
    report = json.loads(runner.artifacts.store["dag-repair-preflight:g30:retry-retry-0"])
    problem = report["path_problems"][0]
    assert problem["reason"] == "forbidden_workspace_path"
    assert problem["repair_route"] == "product_cleanup_required"
    assert problem["exists_on_disk"] is True


def test_workspace_permission_repair_makes_forbidden_cleanup_agent_writable(tmp_path):
    repos_root = tmp_path / "repos"
    repo = repos_root / "iriai-studio"
    _init_git_repo(repo)
    forbidden_root = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    _write_forbidden_manifest(repo, forbidden_root)
    forbidden_dir = repo / forbidden_root
    forbidden_dir.mkdir(parents=True, exist_ok=True)
    target = forbidden_dir / "cardVariantRegistry.ts"
    target.write_text("export {};\n", encoding="utf-8")
    forbidden_dir.chmod(0o755)

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        [f"iriai-studio/{forbidden_root}/cardVariantRegistry.ts"],
        reason="test",
    )

    assert report["operator_required"] is False
    mode = forbidden_dir.stat().st_mode
    assert mode & stat.S_IWGRP
    assert mode & stat.S_ISGID


def test_workspace_permission_repair_rejects_symlinked_feature_root_before_candidates(
    tmp_path,
    monkeypatch,
):
    feature_parent = tmp_path / ".iriai" / "features"
    outside_feature = tmp_path / "outside-feature"
    outside_repo = outside_feature / "repos" / "iriai-studio"
    (outside_repo / ".git").mkdir(parents=True)
    locked_dir = outside_repo / "src" / "workflow-tab"
    locked_dir.mkdir(parents=True)
    locked_dir.chmod(0o755)
    (feature_parent).mkdir(parents=True)
    (feature_parent / "feat").symlink_to(outside_feature, target_is_directory=True)
    repos_root = feature_parent / "feat" / "repos"
    before_mode = locked_dir.stat().st_mode

    def _fail_candidate_discovery(*_args, **_kwargs):
        raise AssertionError("candidate discovery must not run through symlinked roots")

    monkeypatch.setattr(
        implementation_module,
        "_cleanup_permission_candidates",
        _fail_candidate_discovery,
    )

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        ["iriai-studio/src/workflow-tab/cardVariantRegistry.ts"],
        reason="test",
        allow_missing_targets=True,
    )

    assert report["operator_required"] is True
    assert report["changed"] == []
    assert report["failed"] == []
    assert report["skipped"][0]["reason"] == "unsafe_feature_workspace_root"
    assert (
        report["skipped"][0]["problems"][0]["reason"]
        == "workflow_repos_root_symlink_ancestor"
    )
    assert locked_dir.stat().st_mode == before_mode


def test_dag_writeability_uses_agent_group_not_operator_owner(tmp_path):
    repos_root = tmp_path / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    locked_dir = repo / "src" / "workflow-tab" / "impl"
    locked_dir.mkdir(parents=True)
    locked_dir.chmod(0o755)
    task = ImplementationTask(
        id="TASK",
        name="Task",
        description="Task",
        repo_path="iriai-studio",
        files=["src/workflow-tab/impl/bridge/catalogBindings.ts"],
    )

    problems = implementation_module._dag_workspace_writeability_problems(
        repos_root,
        [task],
    )

    assert problems
    assert problems[0]["reason"] == "writeability_denied"

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        ["iriai-studio/src/workflow-tab/impl/bridge/catalogBindings.ts"],
        reason="test",
        allow_missing_targets=True,
    )

    assert report["operator_required"] is False
    assert locked_dir.stat().st_mode & stat.S_IWGRP
    assert locked_dir.stat().st_mode & stat.S_ISGID
    assert implementation_module._dag_workspace_writeability_problems(
        repos_root,
        [task],
    ) == []


def test_workspace_acl_chmod_failure_is_nonfatal_when_path_already_agent_writable(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "iriai-studio-backend"
    (repo / ".git").mkdir(parents=True)
    target_dir = repo / "docs"
    target_dir.mkdir(parents=True)
    target_dir.chmod(0o775)

    original_chmod = os.chmod

    def _fail_chmod(path, mode):
        if Path(path) == target_dir:
            raise PermissionError("operation not permitted")
        return original_chmod(path, mode)

    monkeypatch.setattr(implementation_module, "_agent_shared_gid", lambda: target_dir.stat().st_gid)
    monkeypatch.setattr(implementation_module.os, "chmod", _fail_chmod)

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        ["iriai-studio-backend/docs/bridge-event-catalog.md"],
        reason="test",
        allow_missing_targets=True,
    )

    assert report["operator_required"] is False
    assert report["failed"] == []
    assert any(
        "already agent-writable" in str(item.get("error", ""))
        for item in report["skipped"]
    )


def test_workspace_acl_replaces_regular_file_when_owner_chmod_is_denied(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "iriai-studio-backend"
    (repo / ".git").mkdir(parents=True)
    worker_dir = repo / "iriai_studio_backend" / "worker"
    worker_dir.mkdir(parents=True)
    worker_dir.chmod(0o2775)
    target = worker_dir / "checkpoint_emit.py"
    target.write_text("print('checkpoint')\n", encoding="utf-8")
    target.chmod(0o644)
    before_inode = target.stat().st_ino

    original_chmod = os.chmod

    def _fail_target_chmod(path, mode):
        if Path(path) == target:
            raise PermissionError("operation not permitted")
        return original_chmod(path, mode)

    monkeypatch.setattr(implementation_module, "_agent_shared_gid", lambda: worker_dir.stat().st_gid)
    monkeypatch.setattr(implementation_module.os, "chmod", _fail_target_chmod)

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        ["iriai-studio-backend/iriai_studio_backend/worker/checkpoint_emit.py"],
        reason="test",
        allow_missing_targets=True,
    )

    assert report["operator_required"] is False
    assert report["failed"] == []
    assert target.read_text(encoding="utf-8") == "print('checkpoint')\n"
    assert target.stat().st_mode & stat.S_IWGRP
    assert target.stat().st_ino != before_inode
    assert any(
        item.get("method") == "atomic_file_replacement_after_chmod_failure"
        for item in report["changed"]
    )


def test_feature_workspace_group_write_fails_closed_without_shared_group(
    tmp_path,
    monkeypatch,
):
    repos_root = tmp_path / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "iriai-studio"
    target_dir = repo / "src"
    target_dir.mkdir(parents=True)
    target_dir.chmod(0o775)
    monkeypatch.setattr(implementation_module, "_agent_shared_gid", lambda: None)

    assert implementation_module._path_agent_writable(
        target_dir,
        repo_path=repo,
    ) is False


def test_dag_writeability_rejects_outside_root_and_symlink_targets(tmp_path):
    repos_root = tmp_path / ".iriai" / "features" / "feat" / "repos"
    repo = repos_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    actual = repo / "actual"
    actual.mkdir()
    (repo / "src" / "linked").symlink_to(actual)

    outside_task = ImplementationTask(
        id="OUTSIDE",
        name="Outside",
        description="Outside",
        repo_path="iriai-studio",
        files=["../outside/file.ts"],
    )
    symlink_task = ImplementationTask(
        id="SYMLINK",
        name="Symlink",
        description="Symlink",
        repo_path="iriai-studio",
        files=["src/linked/file.ts"],
    )

    problems = implementation_module._dag_workspace_writeability_problems(
        repos_root,
        [outside_task, symlink_task],
    )

    assert {problem["reason"] for problem in problems} == {
        "writeability_target_outside_repo",
        "writeability_symlink_ancestor",
    }


def test_workspace_permission_normalization_requires_all_targets_to_match_repo(
    tmp_path,
):
    repos_root = tmp_path / "repos"
    repo = repos_root / "iriai-studio"
    _init_git_repo(repo)
    (repo / "src").mkdir()

    report = implementation_module._normalize_feature_workspace_cleanup_permissions(
        repos_root,
        [
            "iriai-studio/src/new-file.ts",
            str(tmp_path / "outside" / "bad.ts"),
        ],
        reason="test",
        allow_missing_targets=True,
    )

    assert report["operator_required"] is True
    assert any("no direct workflow repo matched target" in reason for reason in report["operator_reasons"])


def test_workspace_repo_path_rejects_escape_segments(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    feature_root = workspace_root / ".iriai" / "features" / "feat" / "repos"
    feature_root.mkdir(parents=True)

    safe_path, rejected = implementation_module._normalize_workspace_repo_path(
        "../../outside",
        workspace_root,
        feature_root=feature_root,
    )

    assert safe_path == ""
    assert rejected == "../../outside"


@pytest.mark.asyncio
async def test_manifest_forbidden_preflight_routes_to_focused_cleanup_after_permission_repair(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-g39-preflight-cleanup", slug="g39-cleanup")
    repos_root = tmp_path / "repos"
    repo = repos_root / "iriai-studio"
    _init_git_repo(repo)
    forbidden_root = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    _write_forbidden_manifest(repo, forbidden_root)
    forbidden_dir = repo / forbidden_root
    forbidden_dir.mkdir(parents=True, exist_ok=True)
    forbidden_file = forbidden_dir / "cardVariantRegistry.ts"
    forbidden_file.write_text("export {};\n", encoding="utf-8")
    forbidden_dir.chmod(0o755)
    canonical = repo / "src/webviews/projectSurface/src/chat/cardVariantRegistry.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("export {};\n", encoding="utf-8")

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}
            self.prompts: list[str] = []
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                raise AssertionError("preflight product cleanup route must skip RCA")
            if task.output_type is ImplementationResult:
                assert forbidden_dir.stat().st_mode & stat.S_IWGRP
                forbidden_file.unlink()
                forbidden_dir.rmdir()
                return ImplementationResult(
                    task_id="VERIFY-MANIFEST-PREFLIGHT-CLEANUP",
                    summary="removed forbidden subtree and preserved canonical registry",
                    files_modified=[
                        "iriai-studio/src/webviews/projectSurface/src/chat/cardVariantRegistry.ts"
                    ],
                )
            raise AssertionError(f"unexpected task: {task!r}")

    async def _verify(*_args, **_kwargs):
        return Verdict(approved=True, summary="clean")

    async def _no_result_reconcile(
        runner,
        feature,
        group_idx,
        retry_label,
        group_tasks,
        *,
        results,
        verify_results_context,
        all_results,
        repair_results,
        feature_root,
    ):
        del runner, feature, group_idx, retry_label, group_tasks, repair_results, feature_root
        return implementation_module.DagTaskReconcileOutcome(
            results,
            verify_results_context,
            all_results,
            {},
        )

    async def _no_spec_reconcile(*args, **kwargs):
        del kwargs
        return implementation_module.DagTaskSpecReconcileOutcome(args[4], {})

    _unexpected_expanded = _allow_checkpoint_lenses_only(
        "manifest-forbidden preflight route must skip expanded verify before focused repair"
    )

    async def _unexpected_parallel(*args, **kwargs):
        del args, kwargs
        raise AssertionError("manifest-forbidden preflight route must skip parallel repair")

    async def _commit_repos_success(*_args, **_kwargs):
        return ""

    async def _checkpoint(*_args, **_kwargs):
        return "d" * 40

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_results", _no_result_reconcile)
    monkeypatch.setattr(implementation_module, "_reconcile_dag_task_specs", _no_spec_reconcile)
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _unexpected_expanded)
    monkeypatch.setattr(implementation_module, "_attempt_parallel_dag_repair", _unexpected_parallel)
    monkeypatch.setattr(implementation_module, "_commit_repos", _commit_repos_success)
    monkeypatch.setattr(implementation_module, "_commit_group", _checkpoint)
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": "",
        },
    )

    runner = _Runner()
    _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [ImplementationTask(id="TASK-csps-10-2", name="Task", description="Task")],
        [
            ImplementationResult(
                task_id="TASK-csps-10-2",
                summary="canonical",
                files_modified=[
                    "iriai-studio/src/webviews/projectSurface/src/chat/cardVariantRegistry.ts"
                ],
            )
        ],
        [],
        implementation_module.HandoverDoc(),
        repos_root,
        "primary",
        "secondary",
        "primary",
    )

    assert approved is True
    assert failure == ""
    assert RootCauseAnalysis not in runner.output_types
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g39:retry-0"]
    )
    assert route_payload["route"] == "manifest_forbidden_product_cleanup"
    assert route_payload["operator_required"] is False
    assert route_payload["skip_expanded_verify"] is True
    permission_payload = json.loads(
        runner.artifacts.store[
            "dag-workspace-permission-repair:g39:retry-0:direct-route"
        ]
    )
    assert permission_payload["operator_required"] is False
    gate_payload = json.loads(
        runner.artifacts.store["dag-manifest-cleanup-gate:g39:retry-0"]
    )
    assert gate_payload["approved"] is True
    prompt_context_path = Path(runner.prompts[0].split("`")[1])
    prompt_context = "\n".join(
        path.read_text(encoding="utf-8")
        for path in prompt_context_path.parent.glob("g39-fix-0-*.md")
    )
    assert "Do NOT fix this by adding `.eslint-ignore`" in prompt_context


@pytest.mark.asyncio
async def test_manifest_forbidden_cleanup_blocks_when_permission_repair_fails(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-permission-block", slug="permission-block")
    verdict = Verdict(
        approved=False,
        summary="preflight failed",
        concerns=[
            Issue(
                severity="major",
                description=(
                    "manifest-forbidden product cleanup required; "
                    "manifest-forbidden path exists in the feature workspace"
                ),
                file=(
                    "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
                    "workflowTab/chat/cardVariantRegistry.ts"
                ),
            )
        ],
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}

        async def run(self, *_args, **_kwargs):
            raise AssertionError("operator-blocked cleanup must not dispatch implementer")

    def _permission_block(*_args, **_kwargs):
        return {
            "enabled": True,
            "changed": [],
            "already_ok": [],
            "skipped": [],
            "failed": [
                {
                    "path": "/tmp/forbidden",
                    "error": "chmod failed",
                }
            ],
            "operator_reasons": ["chmod failed"],
            "operator_required": True,
        }

    async def _unexpected_expanded(*args, **kwargs):
        del args, kwargs
        raise AssertionError("operator-blocked cleanup must skip expanded verify")

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(
        implementation_module,
        "_normalize_feature_workspace_cleanup_permissions",
        _permission_block,
    )
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _unexpected_expanded)

    runner = _Runner()
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [ImplementationTask(id="TASK", name="Task", description="Task")],
        [ImplementationResult(task_id="TASK", summary="done")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        initial_verdict=verdict,
        initial_verdict_key="dag-verify:g39:initial",
    )

    assert approved is False
    assert "operator_required=true" in failure
    route_payload = json.loads(
        runner.artifacts.store["dag-direct-repair-route:g39:retry-0"]
    )
    assert route_payload["route"] == "manifest_forbidden_product_cleanup"
    assert route_payload["operator_required"] is True
    assert route_payload["status"] == "operator_blocked"


@pytest.mark.asyncio
async def test_normal_verify_acl_block_stops_before_repair_dispatch(
    tmp_path,
    monkeypatch,
):
    feature = SimpleNamespace(id="feat-acl-block", slug="acl-block")
    verdict = Verdict(
        approved=False,
        summary="canonical file missing",
        concerns=[
            Issue(
                severity="major",
                description="canonical bridge binding file was not created",
                file="iriai-studio/src/workflow-tab/impl/bridge/catalogBindings.ts",
            )
        ],
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}

        async def run(self, *_args, **_kwargs):
            raise AssertionError("ACL-blocked repair must not dispatch implementer")

    def _permission_block(*_args, **_kwargs):
        return {
            "enabled": True,
            "target_files": [
                "iriai-studio/src/workflow-tab/impl/bridge/catalogBindings.ts"
            ],
            "changed": [],
            "already_ok": [],
            "skipped": [],
            "failed": [{"path": "/tmp/impl", "error": "chmod failed"}],
            "operator_reasons": ["parent directory is not writable by repair agent"],
            "operator_required": True,
        }

    async def _unexpected_expanded(*args, **kwargs):
        del args, kwargs
        raise AssertionError("ACL-blocked repair must skip expanded verify")

    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(
        implementation_module,
        "_normalize_feature_workspace_cleanup_permissions",
        _permission_block,
    )
    monkeypatch.setattr(implementation_module, "_run_expanded_dag_verify_lenses", _unexpected_expanded)

    runner = _Runner()
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        48,
        [
            ImplementationTask(
                id="TASK",
                name="Task",
                description="Task",
                repo_path="iriai-studio",
                files=["src/workflow-tab/impl/bridge/catalogBindings.ts"],
            )
        ],
        [ImplementationResult(task_id="TASK", summary="blocked")],
        [],
        implementation_module.HandoverDoc(),
        tmp_path,
        "primary",
        "secondary",
        "primary",
        initial_verdict=verdict,
        initial_verdict_key="dag-verify:g48:initial",
    )

    assert approved is False
    assert "operator_required=true" in failure
    payload = json.loads(
        runner.artifacts.store["dag-writeability-preflight:g48:retry-0"]
    )
    assert payload["operator_required"] is True


@pytest.mark.asyncio
async def test_dag_task_artifact_repair_clears_preflight_stale_row(tmp_path):
    feature = SimpleNamespace(id="feat-preflight-repair", slug="preflight-repair", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    for path in [
        "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
        "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        "src/webviews/projectSurface/src/styles/dashboard.css",
    ]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/iriaiStudio/browser/"
                        "views/components/WorkflowCard.tsx"
                    ),
                    "source": "D-GR-1",
                }
            ]
        }),
        encoding="utf-8",
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id="TASK-S18-3", name="Task", description="Task")
    stale_result = ImplementationResult(
        task_id="TASK-S18-3",
        summary="stale",
        files_modified=[
            "src/vs/workbench/contrib/iriaiStudio/browser/"
            "views/components/WorkflowCard.tsx"
        ],
    )
    before = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-0",
        [task],
        [stale_result],
        feature_root=feature_root,
    )
    assert before is not None

    corrected = ImplementationResult(
        task_id="TASK-S18-3",
        summary="corrected",
        files_created=[
            "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx",
            "src/webviews/projectSurface/src/dashboard/hooks/useLastActivityTick.ts",
        ],
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )
    await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G-S18",
            summary="repair persisted task result",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key="dag-task:TASK-S18-3",
                    content=corrected.model_dump_json(),
                )
            ],
        ),
        feature_root,
    )
    latest = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-S18-3"]
    )
    after = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        15,
        "retry-1",
        [task],
        [latest],
        feature_root=feature_root,
    )

    assert after is None


@pytest.mark.asyncio
async def test_authority_gate_repairs_artifact_only_dag_task_when_parallel_disabled(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-authority-g39", slug="authority-g39", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    stale_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "slices/slice10.ts"
    )
    canonical_repo_path = "src/webviews/projectSurface/src/chat/slices/slice10.ts"
    canonical_path = f"iriai-studio/{canonical_repo_path}"
    canonical = repo / canonical_repo_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("export {};\n", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat subtree",
                }
            ]
        }),
        encoding="utf-8",
    )
    task_id = "chat-sidepane-shell-slice-10-TASK-csps-10-2"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=[stale_path],
    )
    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {
                "id": len(self.store),
                "created_at": "now",
                "value": value,
            }

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is ArtifactRepairResult:
                raise AssertionError(
                    "unique existing product candidate should not require agent repair"
                )
            raise AssertionError(f"unexpected agent output type {task.output_type}")

    verify_calls = 0

    async def _verify(*_args, **_kwargs):
        nonlocal verify_calls
        verify_calls += 1
        return Verdict(approved=True, summary="semantic verifier clean")

    _unexpected_expanded = _allow_checkpoint_lenses_only(
        "artifact-only authority repair must skip expanded verify before focused repair"
    )

    async def _unexpected_parallel(*_args, **_kwargs):
        raise AssertionError("authority gate must not depend on parallel repair")

    async def _checkpoint(*_args, **_kwargs):
        return "e" * 40

    monkeypatch.setenv(implementation_module.DAG_PARALLEL_REPAIR_ENV, "0")
    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(
        implementation_module,
        "_run_expanded_dag_verify_lenses",
        _unexpected_expanded,
    )
    monkeypatch.setattr(
        implementation_module,
        "_attempt_parallel_dag_repair",
        _unexpected_parallel,
    )
    monkeypatch.setattr(implementation_module, "_commit_group", _checkpoint)
    monkeypatch.setattr(
        implementation_module,
        "_checkpoint_no_dirty_proof",
        lambda *_args, **_kwargs: {
            "artifact_schema": "dag-checkpoint-no-dirty-proof-v1",
            "clean": True,
            "repo_heads": "",
        },
    )

    runner = _Runner()
    _install_durable_graph_store(runner)
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        [stale],
        [stale],
        implementation_module.HandoverDoc(),
        feature_root,
        "primary",
        "secondary",
        "primary",
    )

    assert approved is True
    assert failure == ""
    assert verify_calls == 1
    assert ArtifactRepairResult not in runner.output_types
    assert ImplementationResult not in runner.output_types
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_modified == [canonical_path]
    reconcile_key = next(
        key for key in runner.artifacts.store
        if key.startswith("dag-task-reconcile:g39:retry-")
    )
    reconcile = json.loads(runner.artifacts.store[reconcile_key])
    assert reconcile["applied"][0]["source"] == "existing_product_match"
    assert reconcile["applied"][0]["action"] == "appended_dag_task_row"
    resolution = reconcile["candidate_resolution"][0]
    assert resolution["status"] == "accepted"
    assert resolution["accepted_path"] == canonical_path
    assert "dag-repair-expanded-verify:g39:retry-0" not in runner.artifacts.store
    assert "dag-repair-dispatch:g39:retry-0" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_dag_task_reconciler_resolves_g39_slice12_test_browser_to_tests(tmp_path):
    feature = SimpleNamespace(id="feat-g39-slice12", slug="g39-slice12", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    stale_module = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "cardVariantRegistry.ts"
    )
    stale_test = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "test/browser/cardVariantRegistry.test.ts"
    )
    canonical_module = (
        "iriai-studio/src/webviews/projectSurface/src/chat/"
        "cardVariantRegistry.ts"
    )
    canonical_test = (
        "iriai-studio/src/webviews/projectSurface/src/chat/"
        "__tests__/cardVariantRegistry.test.ts"
    )
    for path in (canonical_module, canonical_test):
        target = feature_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export {};\n", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat subtree",
                }
            ],
            "expected_files": [],
        }),
        encoding="utf-8",
    )
    task_id = "chat-sidepane-shell-slice-12-T-csp-s12-1"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale slice 12",
        status="completed",
        files_created=[stale_module, stale_test],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 1, "created_at": "before", "value": value}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts())
    task = ImplementationTask(id=task_id, name="Slice 12", description="Slice 12")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        39,
        "0-authority-reconcile",
        [task],
        results=[stale],
        verify_results_context=[stale],
        all_results=[stale],
        repair_results=[],
        feature_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_created == [canonical_module, canonical_test]
    assert stored.files_modified == []
    assert outcome.report["applied"][0]["source"] == "existing_product_match"
    assert outcome.report["applied"][0]["action"] == "appended_dag_task_row"
    accepted = [
        record for record in outcome.report["candidate_resolution"]
        if record["status"] == "accepted"
    ]
    assert [record["accepted_path"] for record in accepted] == [
        canonical_module,
        canonical_test,
    ]
    assert "dag-task-reconcile:g39:retry-0-authority-reconcile" in runner.artifacts.store


@pytest.mark.asyncio
async def test_authority_gate_blocks_invalid_dag_task_artifact_schema(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-authority-schema", slug="authority-schema", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    stale_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "slices/slice12.ts"
    )
    canonical_path = "src/webviews/projectSurface/src/chat/slices/not-slice12.ts"
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("export {};\n", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat subtree",
                }
            ]
        }),
        encoding="utf-8",
    )
    task_id = "chat-sidepane-shell-slice-12-T-csp-s12-1"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=[stale_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {
                "id": len(self.store),
                "created_at": "now",
                "value": value,
            }

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="AUTHORITY-ARTIFACT-REPAIR",
                    group_id="g39-r0-dag-task-result-drift",
                    summary="used wrong nested schema",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key=f"dag-task:{task_id}",
                            content=json.dumps({
                                "task_id": task_id,
                                "summary": "wrong schema",
                                "status": "completed",
                                "artifacts_modified": [canonical_path],
                            }),
                        )
                    ],
                )
            raise AssertionError(f"unexpected agent output type {task.output_type}")

    async def _verify(*_args, **_kwargs):
        raise AssertionError("invalid authority repair must not run semantic verifier")

    async def _unexpected_expanded(*_args, **_kwargs):
        raise AssertionError("invalid authority repair must skip expanded verify")

    async def _unexpected_parallel(*_args, **_kwargs):
        raise AssertionError("invalid authority repair must not enter parallel repair")

    monkeypatch.setenv(implementation_module.DAG_PARALLEL_REPAIR_ENV, "0")
    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(
        implementation_module,
        "_run_expanded_dag_verify_lenses",
        _unexpected_expanded,
    )
    monkeypatch.setattr(
        implementation_module,
        "_attempt_parallel_dag_repair",
        _unexpected_parallel,
    )

    runner = _Runner()
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        [stale],
        [stale],
        implementation_module.HandoverDoc(),
        feature_root,
        "primary",
        "secondary",
        "primary",
    )

    assert approved is False
    assert "DAG authority gate blocked broad repair" in failure
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_modified == [stale_path]
    gate = json.loads(runner.artifacts.store["dag-authority-gate:g39:retry-0"])
    assert gate["route"] == "db_task_result_drift"
    assert gate["status"] == "blocked_artifact_repair_no_applied_updates"
    repair_record = gate["artifact_repair"]["result"]
    assert repair_record["status"] == "blocked"
    skipped = json.dumps(gate["artifact_repair"])
    assert "dag_task_no_reported_files" in skipped
    assert "artifacts_modified" in skipped
    assert "dag-repair-expanded-verify:g39:retry-0" not in runner.artifacts.store
    assert "dag-repair-dispatch:g39:retry-0" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_authority_gate_blocks_partial_artifact_repair_coverage(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-authority-partial", slug="authority-partial", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat subtree",
                }
            ]
        }),
        encoding="utf-8",
    )
    stale_prefix = "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    task_1 = "chat-sidepane-shell-slice-10-TASK-csps-10-2"
    task_2 = "chat-sidepane-shell-slice-12-T-csp-s12-1"
    stale_1 = ImplementationResult(
        task_id=task_1,
        summary="stale 1",
        files_modified=[f"{stale_prefix}/slice10.ts"],
    )
    stale_2 = ImplementationResult(
        task_id=task_2,
        summary="stale 2",
        files_modified=[f"{stale_prefix}/slice12.ts"],
    )
    canonical_1 = repo / "src/webviews/projectSurface/src/chat/slice10.ts"
    canonical_1.parent.mkdir(parents=True, exist_ok=True)
    canonical_1.write_text("export {};\n", encoding="utf-8")
    corrected_1 = ImplementationResult(
        task_id=task_1,
        summary="corrected wrong target",
        files_modified=["iriai-studio/src/webviews/projectSurface/src/chat/slice10.ts"],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_1}": stale_1.model_dump_json(),
                f"dag-task:{task_2}": stale_2.model_dump_json(),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": len(self.store), "created_at": "now", "value": value}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="AUTHORITY-ARTIFACT-REPAIR",
                    group_id="g39-r0-dag-task-result-drift",
                    summary="updated the wrong target only",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key=f"dag-task:{task_1}",
                            content=corrected_1.model_dump_json(),
                        )
                    ],
                )
            raise AssertionError(f"unexpected output type {task.output_type}")

    async def _verify(*_args, **_kwargs):
        raise AssertionError("partial authority repair must not run semantic verifier")

    async def _unexpected_expanded(*_args, **_kwargs):
        raise AssertionError("partial authority repair must skip expanded verify")

    async def _unexpected_parallel(*_args, **_kwargs):
        raise AssertionError("partial authority repair must not enter parallel repair")

    monkeypatch.setenv(implementation_module.DAG_PARALLEL_REPAIR_ENV, "0")
    monkeypatch.setattr(implementation_module, "VERIFY_RETRIES", 1)
    monkeypatch.setattr(implementation_module, "_verify", _verify)
    monkeypatch.setattr(
        implementation_module,
        "_run_expanded_dag_verify_lenses",
        _unexpected_expanded,
    )
    monkeypatch.setattr(
        implementation_module,
        "_attempt_parallel_dag_repair",
        _unexpected_parallel,
    )

    runner = _Runner()
    approved, failure = await implementation_module._verify_and_fix_group(
        runner,
        feature,
        39,
        [
            ImplementationTask(id=task_1, name="Task 1", description="Task 1"),
            ImplementationTask(id=task_2, name="Task 2", description="Task 2"),
        ],
        [stale_1, stale_2],
        [stale_1, stale_2],
        implementation_module.HandoverDoc(),
        feature_root,
        "primary",
        "secondary",
        "primary",
    )

    assert approved is False
    assert "partial_authority_repair" in failure
    gate_key = next(
        key for key in runner.artifacts.store
        if key.startswith("dag-authority-gate:g39:retry-")
    )
    gate = json.loads(runner.artifacts.store[gate_key])
    assert gate["status"] == "blocked_partial_authority_repair"
    coverage = gate["post_artifact_target_coverage"]
    assert coverage["complete"] is False
    assert coverage["missing_refs"] == [f"dag-task:{task_2}"]
    assert "dag-repair-expanded-verify:g39:retry-0" not in runner.artifacts.store
    assert "dag-repair-dispatch:g39:retry-0" not in runner.artifacts.store


@pytest.mark.asyncio
async def test_verify_failure_routes_stale_dag_task_to_artifact_repair(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-verify-artifact", slug="verify-artifact", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("ok", encoding="utf-8")
    corrected = ImplementationResult(
        task_id="TASK-S18-3",
        summary="corrected",
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "stale persisted dag-task ImplementationResult row "
                        "still has old files_created/files_modified metadata"
                    ),
                    affected_files=["dag-task:TASK-S18-3"],
                    proposed_approach=(
                        "Repair the DB-backed artifact row through artifact_updates."
                    ),
                    confidence="contradiction",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-REPAIR-VERIFY",
                    group_id="VERIFY",
                    summary="updated dag task",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key="dag-task:TASK-S18-3",
                            content=corrected.model_dump_json(),
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                raise AssertionError("normal implementer should not run")
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="verified")
            raise AssertionError(f"unexpected output type {task.output_type}")

    async def _fail_commit(*args, **kwargs):
        raise AssertionError("artifact-only repair should not commit repos")

    monkeypatch.setattr(implementation_module, "_commit_repos", _fail_commit)
    monkeypatch.setattr(implementation_module, "_commit_repos_in_root", _fail_commit)
    runner = _Runner()
    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        "preflight failed on stale dag-task row",
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        "",
        bug_id="VERIFY-FAIL-DB",
        attempt_number=1,
        workspace_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store["dag-task:TASK-S18-3"]
    )
    assert stored.files_modified == corrected.files_modified
    assert "bug-artifact-repair:verify:VERIFY-FAIL-DB" in runner.artifacts.store
    assert attempt.re_verify_result == "PASS"
    assert ImplementationResult not in runner.output_types


@pytest.mark.asyncio
async def test_verify_failure_routes_existing_forbidden_dag_task_to_product_cleanup(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-verify-product-cleanup", slug="verify-product-cleanup", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.ts"
    )
    canonical_path = (
        "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old", encoding="utf-8")
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": canonical_path,
                    "source": f"{task_id} canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "D-GR-1 retired workflowTab chat util",
                }
            ],
        }),
        encoding="utf-8",
    )
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=[forbidden_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "artifact_mirror": _Mirror(
                    tmp_path / ".iriai" / "artifacts" / "features" / feature.id
                ),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "stale persisted dag-task ImplementationResult row "
                        "still points at a manifest-forbidden product file"
                    ),
                    evidence=["the forbidden workflowTab util still exists on disk"],
                    affected_files=[f"dag-task:{task_id}", forbidden_path],
                    proposed_approach=(
                        "Clean up the retired product file, port coverage, then "
                        "let the host append corrected dag-task metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                raise AssertionError("artifact repair must not run while forbidden file exists")
            if task.output_type is ImplementationResult:
                forbidden.unlink()
                return ImplementationResult(
                    task_id="VERIFY-PRODUCT-CLEANUP",
                    summary="removed retired util and kept canonical implementation",
                    files_modified=[canonical_path],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="verified")
            raise AssertionError(f"unexpected output type {task.output_type}")

    async def _noop_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_commit)
    monkeypatch.setattr(implementation_module, "_commit_repos_in_root", _noop_commit)
    runner = _Runner()
    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        f"preflight failed on dag-task:{task_id} and {forbidden_path}",
        "verify",
        implementation_module.qa_engineer,
        implementation_module.implementer,
        "",
        bug_id="VERIFY-FAIL-PRODUCT-DRIFT",
        attempt_number=1,
        workspace_root=feature_root,
        skip_regression=True,
    )

    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.task_id == task_id
    assert stored.files_modified == [canonical_path]
    assert attempt.re_verify_result == "PASS"
    assert ArtifactRepairResult not in runner.output_types
    assert ImplementationResult in runner.output_types
    reconcile = json.loads(
        runner.artifacts.store[
            "dag-task-product-reconcile:verify:VERIFY-FAIL-PRODUCT-DRIFT"
        ]
    )
    assert reconcile["applied"][0]["action"] == "appended_dag_task_row"


@pytest.mark.asyncio
async def test_parallel_dag_repair_infers_dag_task_artifact_from_preflight_issue(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-g29-route", slug="g29-route", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    for path in [
        "src/vs/workbench/contrib/studioBridge/test/browser/reconnect.integrationTest.ts",
        "src/vs/workbench/contrib/studioBridge/test/browser/fixtures/reconnectFixtures.ts",
    ]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")

    task_id = "project-and-launcher-slice-18-TASK-SF4-S18-4"
    corrected = ImplementationResult(
        task_id=task_id,
        summary="canonical studioBridge reconnect test metadata",
        files_created=[
            "src/vs/workbench/contrib/studioBridge/test/browser/"
            "reconnect.integrationTest.ts",
            "src/vs/workbench/contrib/studioBridge/test/browser/fixtures/"
            "reconnectFixtures.ts",
        ],
    )

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
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="stale-iriaiStudio-test-paths",
                        likely_root_cause=(
                            "stale ImplementationResult files_created metadata"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "The implementation is already in studioBridge, but the "
                        "stale ImplementationResult still reports retired "
                        "iriaiStudio files_created paths."
                    ),
                    evidence=[
                        "Canonical reconnect files exist under studioBridge/test/browser.",
                        "The old iriaiStudio tree is retired by D-GR-1.",
                    ],
                    affected_files=[
                        "src/vs/workbench/contrib/iriaiStudio/test/integration/"
                        "reconnect.test.ts"
                    ],
                    proposed_approach=(
                        "Append a corrected ImplementationResult with canonical "
                        "studioBridge paths; do not create the retired files."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-REPAIR-G29",
                    group_id="stale-iriaiStudio-test-paths",
                    summary="updated stale task metadata",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            artifact_key=f"dag-task:{task_id}",
                            content=corrected.model_dump_json(),
                        )
                    ],
                )
            if task.output_type is ImplementationResult:
                raise AssertionError("normal implementer should not run")
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _fail_commit(*args, **kwargs):
        raise AssertionError("artifact-only repair should not commit repos")

    monkeypatch.setattr(implementation_module, "_commit_repos", _fail_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        29,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} reports changed file that is forbidden/stale "
                        "by verify-file-scope.expected-files.json; repair stale "
                        "task metadata instead of creating this path: "
                        "src/vs/workbench/contrib/iriaiStudio/test/integration/"
                        "reconnect.test.ts"
                    ),
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-REPAIR-G29"]
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_created == corrected.files_created
    assert ImplementationResult not in runner.output_types
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g29:retry-1"])
    assert dispatch["dag_task_artifact_repair_group_count"] == 1
    assert dispatch["artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_routes_existing_forbidden_dag_task_to_product_cleanup(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-g30-product-cleanup", slug="g30-product-cleanup", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.test.ts"
    )
    canonical_path = (
        "src/webviews/projectSurface/src/chat/stores/__tests__/"
        "EventDeduplicator.test.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old test", encoding="utf-8")
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("canonical test", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": canonical_path,
                    "source": f"{task_id} AC-31 AC-32 canonical",
                }
            ],
            "forbidden_files": [
                {
                    "path": forbidden_path,
                    "source": "D-GR-1 retired workflowTab chat util tests",
                }
            ],
        }),
        encoding="utf-8",
    )
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale workflowTab test metadata",
        files_created=[forbidden_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="stale-workflowtab-test-on-disk",
                        likely_root_cause=(
                            "stale ImplementationResult metadata plus retired "
                            "workflowTab test file still in the product tree"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "The latest dag-task row points at a manifest-forbidden "
                        "workflowTab test file that still exists on disk."
                    ),
                    evidence=[
                        "The canonical projectSurface EventDeduplicator test exists.",
                        "The retired workflowTab test must be cleaned up first.",
                    ],
                    affected_files=[f"dag-task:{task_id}", forbidden_path],
                    proposed_approach=(
                        "Remove the retired test after preserving AC-31/AC-32 "
                        "coverage in the canonical projectSurface test, then "
                        "reconcile dag-task metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ArtifactRepairResult:
                raise AssertionError("artifact repair must not run before product cleanup")
            if task.output_type is ImplementationResult:
                forbidden.unlink()
                return ImplementationResult(
                    task_id="PRODUCT-CLEANUP-G30",
                    summary="ported acceptance coverage and removed retired test",
                    files_modified=[canonical_path],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _noop_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        30,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} reports changed file that is forbidden/stale "
                        "by verify-file-scope.expected-files.json; source artifact: "
                        f"dag-task:{task_id}; repair stale task metadata instead "
                        f"of creating this path: {forbidden_path}"
                    ),
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["PRODUCT-CLEANUP-G30"]
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_modified == [canonical_path]
    assert ArtifactRepairResult not in runner.output_types
    assert ImplementationResult in runner.output_types
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g30:retry-1"])
    assert dispatch["dag_task_artifact_repair_group_count"] == 0
    assert dispatch["dag_task_product_cleanup_group_count"] == 1
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["stale-workflowtab-test-on-disk"]}]


@pytest.mark.asyncio
async def test_parallel_dag_repair_synthesizes_artifact_route_when_triage_empty(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-empty-triage", slug="empty-triage", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat tree",
                }
            ]
        }),
        encoding="utf-8",
    )
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    snapshot = artifact_root / ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    snapshot.write_text(stale_path, encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": _Mirror(artifact_root)}
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[], rationale="missed deterministic metadata drift")
            if task.output_type is ArtifactRepairResult:
                return ArtifactRepairResult(
                    task_id="ARTIFACT-SNAPSHOT-DELETE",
                    group_id="dag-task-spec-projection-drift",
                    summary="deleted stale generated snapshot",
                    artifacts_deleted=[
                        ".iriai-context/g30-expanded-verify-r1-task-specs.md"
                    ],
                )
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        30,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} task spec file_scope[0].path references a "
                        "manifest-forbidden/stale path; source artifacts: "
                        f"dag-task:{task_id}, dag-fragment:chat-sidepane-shell:slice-1; "
                        "repair the DAG/source artifact instead of recreating "
                        f"this path: {stale_path}"
                    ),
                    file=stale_path,
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == ["ARTIFACT-SNAPSHOT-DELETE"]
    assert ArtifactRepairResult in runner.output_types
    assert ImplementationResult not in runner.output_types
    assert not snapshot.exists()
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g30:retry-1"])
    assert dispatch["group_count"] == 1
    assert dispatch["metadata_artifact_repair_group_count"] == 1
    assert dispatch["schedule"] == []


@pytest.mark.asyncio
async def test_parallel_dag_repair_runs_source_artifact_followup_after_product_cleanup(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(
        id="feat-g30-source-followup",
        slug="g30-source-followup",
        metadata={},
    )
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    artifact_root = (
        tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    )
    fragment = (
        artifact_root
        / "subfeatures/chat-sidepane-shell/dag-fragments/slice-3.json"
    )
    fragment.parent.mkdir(parents=True, exist_ok=True)
    fragment.write_text('{"tasks": "stale"}', encoding="utf-8")

    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    forbidden_path = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat/"
        "util/eventDeduplicator.ts"
    )
    canonical_path = (
        "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    )
    forbidden = repo / forbidden_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("old", encoding="utf-8")
    canonical = repo / canonical_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {
                    "path": canonical_path,
                    "source": "TASK-SH2-1 canonical EventDeduplicator",
                }
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "D-GR-1 retired workflowTab chat tree",
                }
            ],
        }),
        encoding="utf-8",
    )
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale workflowTab metadata",
        files_created=[forbidden_path],
    )
    corrected = ImplementationResult(
        task_id=task_id,
        summary="source artifact retired duplicate task to canonical EventDeduplicator",
        files_modified=[canonical_path],
    )

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {
                f"dag-task:{task_id}": stale.model_dump_json()
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {
                "autonomous_remainder": True,
                "artifact_mirror": _Mirror(),
                "test_allow_legacy_repair_without_sandbox": True,
            }
            self.output_types: list[object] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(task.output_type)
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="stale-source-fragment",
                        likely_root_cause=(
                            "stale dag-fragment task spec and stale dag-task row "
                            "resurrect retired workflowTab chat files"
                        ),
                        issue_indices=[0],
                        severity="blocker",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis=(
                        "The source dag-fragment file_scope still points at the "
                        "forbidden workflowTab chat util path, while the product "
                        "tree also contains the stale file."
                    ),
                    evidence=[f"{fragment}:9 still contains the forbidden path"],
                    affected_files=[
                        str(fragment),
                        f"dag-task:{task_id}",
                        forbidden_path,
                    ],
                    proposed_approach=(
                        "Delete the retired product file first, then repair the "
                        "DAG source artifact and append corrected dag-task metadata."
                    ),
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                forbidden.unlink()
                for parent in [
                    forbidden.parent,
                    forbidden.parent.parent,
                    forbidden.parent.parent.parent,
                ]:
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
                return ImplementationResult(
                    task_id="PRODUCT-CLEANUP-G30",
                    summary=(
                        "removed retired workflowTab file; canonical "
                        "EventDeduplicator already exists"
                    ),
                    files_modified=[],
                )
            if task.output_type is ArtifactRepairResult:
                assert not forbidden.exists()
                return ArtifactRepairResult(
                    task_id="ARTIFACT-SOURCE-FOLLOWUP",
                    group_id="stale-source-fragment",
                    summary="repaired source fragment and task row",
                    artifact_updates=[
                        ArtifactRepairUpdate(
                            target_ref=str(fragment),
                            content='{"tasks": "fixed"}',
                            summary="retired stale slice-3 task spec",
                        ),
                        ArtifactRepairUpdate(
                            artifact_key=f"dag-task:{task_id}",
                            content=corrected.model_dump_json(),
                            summary="append corrected task metadata",
                        ),
                    ],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _noop_commit(*args, **kwargs):
        return None

    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        30,
        1,
        Verdict(
            approved=False,
            summary="Programmatic DAG preflight failed",
            concerns=[
                Issue(
                    severity="major",
                    description=(
                        f"{task_id} reports changed file that is forbidden/stale "
                        "by verify-file-scope.expected-files.json; source artifact: "
                        f"dag-task:{task_id}; repair stale task metadata instead "
                        f"of creating this path: {forbidden_path}"
                    ),
                ),
                Issue(severity="major", description="second issue keeps DAG repair path"),
            ],
        ),
        [ImplementationTask(id=task_id, name="Task", description="Task")],
        feature_root=feature_root,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="preflight failed",
    )

    assert results is not None
    assert [result.task_id for result in results] == [
        "ARTIFACT-SOURCE-FOLLOWUP",
        "PRODUCT-CLEANUP-G30",
    ]
    assert fragment.read_text(encoding="utf-8") == '{"tasks": "fixed"}'
    stored = ImplementationResult.model_validate_json(
        runner.artifacts.store[f"dag-task:{task_id}"]
    )
    assert stored.files_modified == [canonical_path]
    assert ArtifactRepairResult in runner.output_types
    product_reconcile = json.loads(
        runner.artifacts.store[
            "dag-task-product-reconcile:dag-repair:g30:retry-1:"
            "stale-source-fragment"
        ]
    )
    assert product_reconcile["skipped"][0]["reason"] == (
        "no_canonical_product_files_reported_by_product_repair"
    )
    repair = json.loads(
        runner.artifacts.store[
            "dag-artifact-repair:g30:stale-source-fragment:retry-1"
        ]
    )
    assert repair["artifact_update_application"]["applied_target_updates"][0][
        "path"
    ] == str(fragment)
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g30:retry-1"])
    assert dispatch["dag_task_product_cleanup_group_count"] == 1
    assert dispatch["dag_task_product_cleanup_artifact_followup_count"] == 1


def test_dag_artifact_closure_scans_real_g30_artifact_shapes(tmp_path):
    feature = SimpleNamespace(id="feat-g30-closure", slug="g30-closure", metadata={})
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    stale_text = f"stale file_scope path {stale_path}\n"
    for rel in [
        "subfeatures/chat-sidepane-shell/dag.md",
        "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json",
        "subfeatures/chat-sidepane-shell/dag-fragments/slice-4.json",
        "subfeatures/chat-sidepane-shell/plan.md",
        "dag.md",
        "dag-ws-WS-E-chat-sidepane-shell-slice-7.md",
        "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md",
        "outputs/dag-ws-WS-E-chat-sidepane-shell-slice-2-target-only.md",
        "compile-sources-dag-chunk-11.md",
        ".iriai-context/g30-expanded-verify-r1-task-specs.md",
        "subfeatures/chat-sidepane-shell/system-design-source.md",
    ]:
        target = artifact_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(stale_text, encoding="utf-8")

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    runner = SimpleNamespace(services={"artifact_mirror": _Mirror()})
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    scan = implementation_module._dag_artifact_closure_scan(
        runner,
        feature,
        30,
        [
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        [
            {
                "task_id": task_id,
                "artifact_key": f"dag-task:{task_id}",
                "path": stale_path,
                "reason": "forbidden_task_spec",
                "forbidden_rule": (
                    "src/vs/workbench/contrib/studioWorkflow/browser/"
                    "workflowTab/chat"
                ),
                "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
            }
        ],
    )

    blocking = {item["relative_path"] for item in scan.blocking_targets}
    assert "subfeatures/chat-sidepane-shell/dag.md" in blocking
    assert "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json" in blocking
    assert "subfeatures/chat-sidepane-shell/dag-fragments/slice-4.json" in blocking
    assert "subfeatures/chat-sidepane-shell/plan.md" in blocking
    assert "dag.md" in blocking
    assert "dag-ws-WS-E-chat-sidepane-shell-slice-7.md" in blocking
    assert "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md" in blocking
    assert "outputs/dag-ws-WS-E-chat-sidepane-shell-slice-2-target-only.md" in blocking
    assert "compile-sources-dag-chunk-11.md" in blocking
    assert ".iriai-context/g30-expanded-verify-r1-task-specs.md" in blocking
    assert scan.target_refs()
    advisory = {item["relative_path"] for item in scan.advisory_residuals}
    assert "subfeatures/chat-sidepane-shell/system-design-source.md" in advisory


def test_dag_artifact_closure_ignores_canonical_candidate_paths(tmp_path):
    feature = SimpleNamespace(id="feat-g30-canonical-closure", slug="g30-canonical", metadata={})
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    retired_prefix = (
        "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
    )
    canonical_paths = [
        "src/webviews/projectSurface/src/chat/index.ts",
        "src/webviews/projectSurface/src/chat/stores/index.ts",
        "iriai_studio_backend/lifecycle/events.py",
    ]
    stale_fragment = artifact_root / "subfeatures/chat-sidepane-shell/dag.md"
    stale_fragment.parent.mkdir(parents=True, exist_ok=True)
    stale_fragment.write_text(
        f"retired {stale_path}\ncanonical {canonical_paths[0]}",
        encoding="utf-8",
    )
    canonical_fragment = (
        artifact_root / "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json"
    )
    canonical_fragment.parent.mkdir(parents=True, exist_ok=True)
    canonical_fragment.write_text("\n".join(canonical_paths), encoding="utf-8")
    context_manifest = (
        artifact_root / ".iriai-context/g30-expanded-verify-r0-context-manifest.md"
    )
    context_manifest.parent.mkdir(parents=True, exist_ok=True)
    context_manifest.write_text(
        ".iriai-context/g30-expanded-verify-r0-task-specs.md\n"
        + "\n".join(canonical_paths),
        encoding="utf-8",
    )

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    runner = SimpleNamespace(services={"artifact_mirror": _Mirror()})
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    scan = implementation_module._dag_artifact_closure_scan(
        runner,
        feature,
        30,
        [
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        [
            {
                "task_id": task_id,
                "artifact_key": f"dag-task:{task_id}",
                "path": stale_path,
                "reason": "forbidden_task_spec",
                "forbidden_rule": retired_prefix,
                "forbidden_path": retired_prefix,
                "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
                "candidate_evidence": [
                    {"path": path, "exists": True, "source": "canonical"}
                    for path in canonical_paths[:2]
                ],
            },
            {
                "path": canonical_paths[2],
                "reason": "missing",
                "candidate_evidence": [],
            },
        ],
    )

    assert [item["relative_path"] for item in scan.blocking_targets] == [
        "subfeatures/chat-sidepane-shell/dag.md"
    ]
    blocking_signatures = {
        signature
        for item in scan.blocking_targets
        for signature in item["stale_signatures"]
    }
    assert all(
        "projectSurface" not in signature for signature in blocking_signatures
    )
    assert all(
        "iriai_studio_backend" not in signature
        for signature in blocking_signatures
    )
    ignored_paths = {item["relative_path"] for item in scan.ignored_matches}
    assert "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json" in ignored_paths
    assert ".iriai-context/g30-expanded-verify-r0-context-manifest.md" in ignored_paths
    assert any(
        record["kind"] == "candidate_evidence" and record["blocking"] is False
        for record in scan.signature_records
    )


def test_dag_closure_path_problems_are_scoped_to_planned_group():
    stale_problem = {
        "task_id": "chat-sidepane-shell-slice-1-T-sf11-s1-003",
        "artifact_key": "dag-task:chat-sidepane-shell-slice-1-T-sf11-s1-003",
        "path": (
            "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
            "workflowTab/chat/index.ts"
        ),
        "reason": "forbidden_task_spec",
        "forbidden_rule": (
            "src/vs/workbench/contrib/studioWorkflow/browser/workflowTab/chat"
        ),
        "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
    }
    backend_problem = {
        "path": "iriai_studio_backend/lifecycle/events.py",
        "reason": "missing",
        "repair_route": "artifact_only",
    }
    group_tasks = [
        ImplementationTask(
            id="chat-sidepane-shell-slice-1-T-sf11-s1-003",
            name="Task",
            description="Task",
            subfeature_id="chat-sidepane-shell",
        )
    ]
    changed_files = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="changed-files-backend-path-prefix",
            likely_root_cause="changed-files evidence used a backend prefix",
            issue_indices=[0],
            severity="major",
        ),
        rca=RootCauseAnalysis(
            hypothesis="The changed-files context mentions the backend file.",
            affected_files=["iriai_studio_backend/lifecycle/events.py"],
            proposed_approach="Repair the changed-files context only.",
            confidence="contradiction",
        ),
        issue_text="iriai_studio_backend/lifecycle/events.py is in changed-files.",
        rca_key="rca:changed-files",
    )
    stale_group = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="dag-stale-forbidden-paths",
            likely_root_cause="stale forbidden DAG task paths",
            issue_indices=[1],
            severity="blocker",
        ),
        rca=RootCauseAnalysis(
            hypothesis="Stale DAG artifacts report retired chat paths.",
            affected_files=[],
            proposed_approach="Repair all stale DAG artifacts.",
            confidence="contradiction",
        ),
        issue_text="Retired chat task specs are stale.",
        rca_key="rca:stale",
    )

    scoped = implementation_module._dag_closure_path_problems_for_planned(
        changed_files,
        [stale_problem, backend_problem],
        group_tasks,
    )
    assert scoped == [backend_problem]
    assert implementation_module._dag_closure_blocking_signatures(
        implementation_module._dag_closure_signature_records_from_path_problems(scoped)
    ) == []

    umbrella = implementation_module._dag_closure_path_problems_for_planned(
        stale_group,
        [stale_problem, backend_problem],
        group_tasks,
    )
    assert umbrella == [stale_problem, backend_problem]


@pytest.mark.asyncio
async def test_dag_artifact_repair_closure_blocks_partial_repair(tmp_path):
    feature = SimpleNamespace(id="feat-g30-partial-closure", slug="g30-partial", metadata={})
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    fragment = artifact_root / "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json"
    downstream = artifact_root / "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md"
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    for path in [fragment, downstream]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"stale {stale_path}", encoding="utf-8")

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"artifact_mirror": _Mirror()}
            self.prompts: list[str] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            self.prompts.append(task.prompt)
            return ArtifactRepairResult(
                task_id="ARTIFACT-PARTIAL",
                group_id="dag-fragment-stale-paths",
                summary="only repaired first fragment",
                artifact_updates=[
                    ArtifactRepairUpdate(
                        target_ref=str(fragment),
                        content='{"tasks": "fixed"}',
                    )
                ],
            )

    runner = _Runner()
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    planned = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="dag-fragment-stale-paths",
            likely_root_cause="stale DAG artifact paths",
            issue_indices=[0],
            severity="major",
        ),
        rca=RootCauseAnalysis(
            hypothesis="source DAG artifacts still contain retired chat paths",
            affected_files=[str(fragment)],
            proposed_approach="repair stale DAG source artifacts",
            confidence="contradiction",
        ),
        issue_text="stale DAG fragment",
        rca_key="rca:g30",
    )
    result, synthetic, record = await implementation_module._run_dag_artifact_repair_lane(
        runner,
        feature,
        30,
        1,
        planned,
        implementation_module.DagContradictionResolution(
            resolution="repair stale artifact",
            resolution_kind="artifact_repair",
            authoritative_sources=["preflight"],
            artifact_paths=[str(fragment)],
            confidence="high",
        ),
        {"artifact_key": "contradiction:g30"},
        group_tasks=[
            ImplementationTask(
                id=task_id,
                name="Task",
                description="Task",
                subfeature_id="chat-sidepane-shell",
            )
        ],
        feature_root=tmp_path / "repos",
        runtime="primary",
        feedback="preflight failed",
        fix_context="",
        closure_path_problems=[
            {
                "task_id": task_id,
                "artifact_key": f"dag-task:{task_id}",
                "path": stale_path,
                "reason": "forbidden_task_spec",
                "forbidden_rule": (
                    "src/vs/workbench/contrib/studioWorkflow/browser/"
                    "workflowTab/chat"
                ),
                "source_artifact_ref": "dag-fragment:chat-sidepane-shell:slice-1",
            }
        ],
    )

    assert result.status == "blocked"
    assert synthetic.status == "blocked"
    assert fragment.read_text(encoding="utf-8") == '{"tasks": "fixed"}'
    closure = json.loads(
        runner.artifacts.store[
            "dag-artifact-closure:g30:retry-1:dag-fragment-stale-paths"
        ]
    )
    assert closure["status"] == "blocked"
    assert closure["blocking_residuals"][0]["relative_path"] == (
        "dag/dag-ws-WS-E-chat-sidepane-shell-slice-14-tasks.md"
    )
    assert str(downstream) in runner.prompts[0]
    assert record["closure_target_refs"]


@pytest.mark.asyncio
async def test_dag_artifact_repair_without_mirror_fails_closed_before_actor(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-no-mirror-artifact", slug="no-mirror", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}

        async def run(self, task, feature, phase_name=""):
            del task, feature, phase_name
            raise AssertionError("artifact repair must not dispatch without mirror")

    planned = implementation_module.PlannedBugGroup(
        group=BugGroup(
            group_id="artifact-no-mirror",
            likely_root_cause="stale artifact",
            issue_indices=[0],
            severity="major",
        ),
        rca=RootCauseAnalysis(
            hypothesis="artifact needs repair",
            affected_files=[".iriai-context/task-spec.md"],
            proposed_approach="repair context",
            confidence="contradiction",
        ),
        issue_text="stale artifact",
        rca_key="rca:no-mirror",
    )
    runner = _Runner()

    result, synthetic, record = await implementation_module._run_dag_artifact_repair_lane(
        runner,
        feature,
        31,
        0,
        planned,
        implementation_module.DagContradictionResolution(
            resolution="repair stale artifact",
            resolution_kind="artifact_repair",
            authoritative_sources=["preflight"],
            artifact_paths=[".iriai-context/task-spec.md"],
            confidence="high",
        ),
        {"artifact_key": "contradiction:g31"},
        group_tasks=[ImplementationTask(id="TASK-31", name="Task", description="Task")],
        feature_root=tmp_path / "repos",
        runtime="primary",
        feedback="preflight failed",
        fix_context="",
    )

    assert result.status == "blocked"
    assert synthetic.status == "blocked"
    assert record["failure_type"] == "artifact_mirror_unavailable"
    assert "dag-artifact-repair:g31:artifact-no-mirror:retry-0" in runner.artifacts.store


@pytest.mark.asyncio
async def test_artifact_repair_deletes_generated_context_snapshot_only(tmp_path):
    feature = SimpleNamespace(id="feat-delete-context", slug="delete-context", metadata={})
    feature_root = tmp_path / "repos"
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    generated = artifact_root / ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("stale snapshot", encoding="utf-8")
    source_dag = artifact_root / "dag.md"
    source_dag.write_text("source dag", encoding="utf-8")
    product = feature_root / "iriai-studio/src/product.ts"
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_text("product", encoding="utf-8")

    class _Artifacts:
        async def put(self, key: str, value: str, *, feature):
            del key, value, feature

    class _Mirror:
        def feature_dir(self, feature_id: str):
            assert feature_id == feature.id
            return artifact_root

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"artifact_mirror": _Mirror()},
    )
    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-DELETE",
            group_id="G30",
            summary="delete generated snapshot",
            artifacts_deleted=[
                ".iriai-context/g30-expanded-verify-r1-task-specs.md",
                "dag.md",
                "iriai-studio/src/product.ts",
            ],
        ),
        feature_root,
    )

    assert not generated.exists()
    assert source_dag.exists()
    assert product.exists()
    assert record["deleted_artifacts"][0]["normalized_ref"] == (
        ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    )
    assert [item["reason"] for item in record["skipped_deletes"]] == [
        "target_ref_delete_not_generated_or_staging_artifact",
        "target_ref_not_artifact_context"
    ]


class _RecordingArtifacts:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, object]]] = {}
        self.next_id = 1

    async def get(self, key: str, *, feature):
        del feature
        rows = self.rows.get(key, [])
        return rows[-1]["value"] if rows else ""

    async def get_record(self, key: str, *, feature):
        del feature
        rows = self.rows.get(key, [])
        if not rows:
            return None
        row = rows[-1]
        value = str(row["value"])
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "value": value,
            "sha256": __import__("hashlib").sha256(
                value.encode("utf-8")
            ).hexdigest(),
        }

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.rows.setdefault(key, []).append({
            "id": self.next_id,
            "created_at": f"t{self.next_id}",
            "value": value,
        })
        self.next_id += 1


class _Mirror:
    def __init__(self, artifact_root):
        self.artifact_root = artifact_root

    def feature_dir(self, feature_id: str):
        del feature_id
        return self.artifact_root


@pytest.mark.asyncio
async def test_dag_task_reconciler_appends_full_id_row_from_expected_files(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-g29", slug="reconcile-g29", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    expected_paths = [
        "src/vs/workbench/contrib/studioBridge/test/browser/reconnect.integrationTest.ts",
        "src/vs/workbench/contrib/studioBridge/test/browser/fixtures/reconnectFixtures.ts",
    ]
    for path in expected_paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {"path": path, "source": "TASK-SF4-S18-4 canonical"}
                for path in expected_paths
            ],
            "forbidden_files": [
                {"path": "src/vs/workbench/contrib/iriaiStudio", "source": "retired"}
            ],
        }),
        encoding="utf-8",
    )

    task_id = "project-and-launcher-slice-18-TASK-SF4-S18-4"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_created=[
            "src/vs/workbench/contrib/iriaiStudio/test/integration/reconnect.test.ts",
            "src/vs/workbench/contrib/iriaiStudio/test/integration/fixtures/"
            "reconnectFixtures.ts",
        ],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", stale.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        29,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale],
        all_results=[stale],
        repair_results=[],
        feature_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        await artifacts.get(f"dag-task:{task_id}", feature=feature)
    )
    assert stored.task_id == task_id
    assert stored.files_created == expected_paths
    assert len(artifacts.rows[f"dag-task:{task_id}"]) == 2
    assert outcome.results == [stored]
    assert outcome.verify_results_context == [stored]
    assert outcome.all_results == [stored]
    assert outcome.report["applied"][0]["action"] == "appended_dag_task_row"

    preflight = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        29,
        "retry-2",
        [task],
        outcome.verify_results_context,
        feature_root=feature_root,
    )
    assert preflight is None


@pytest.mark.asyncio
async def test_dag_task_spec_reconciler_rehydrates_canonical_fragment_and_deletes_snapshots(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-spec-reconcile", slug="spec-reconcile", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    stale_path = (
        "iriai-studio/src/vs/workbench/contrib/studioWorkflow/browser/"
        "workflowTab/chat/index.ts"
    )
    canonical_path = "iriai-studio/src/webviews/projectSurface/src/chat/index.ts"
    canonical_file = repo / "src/webviews/projectSurface/src/chat/index.ts"
    canonical_file.parent.mkdir(parents=True, exist_ok=True)
    canonical_file.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {"path": "src/webviews/projectSurface/src/chat/index.ts", "source": "TASK-SH1 canonical"}
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat tree",
                }
            ],
        }),
        encoding="utf-8",
    )
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    fragment = artifact_root / "subfeatures/chat-sidepane-shell/dag-fragments/slice-1.json"
    fragment.parent.mkdir(parents=True, exist_ok=True)
    task_id = "chat-sidepane-shell-slice-1-T-sf11-s1-003"
    fragment.write_text(
        json.dumps({
            "tasks": [
                {
                    "id": task_id,
                    "name": "Chat barrel",
                    "description": "Canonical chat barrel",
                    "subfeature_id": "chat-sidepane-shell",
                    "repo_path": "iriai-studio",
                    "file_scope": [{"path": canonical_path, "action": "create"}],
                    "files": [canonical_path],
                }
            ],
            "execution_order": [[task_id]],
        }),
        encoding="utf-8",
    )
    snapshot = artifact_root / ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(f"stale task spec {stale_path}", encoding="utf-8")

    artifacts = _RecordingArtifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(artifact_root)},
    )
    stale_task = ImplementationTask(
        id=task_id,
        name="Chat barrel",
        description="Stale projection",
        subfeature_id="chat-sidepane-shell",
        repo_path="iriai-studio",
        file_scope=[{"path": stale_path, "action": "create"}],
        files=[stale_path],
    )

    outcome = await implementation_module._reconcile_dag_task_specs(
        runner,
        feature,
        30,
        "retry-1",
        [stale_task],
        feature_root=feature_root,
    )

    assert outcome.tasks[0].file_scope[0].path == canonical_path
    assert outcome.tasks[0].files == [canonical_path]
    assert outcome.report["applied"][0]["action"] == "rehydrated_from_source_fragment"
    assert not snapshot.exists()
    assert outcome.report["deleted_generated_snapshots"][0]["relative_path"] == (
        ".iriai-context/g30-expanded-verify-r1-task-specs.md"
    )

    preflight = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        30,
        "retry-2",
        outcome.tasks,
        [],
        feature_root=feature_root,
    )
    assert preflight is None


@pytest.mark.asyncio
async def test_dag_task_spec_reconciler_retired_fragment_task_clears_stale_scope(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-spec-retired", slug="spec-retired", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat"
                    ),
                    "source": "retired chat tree",
                }
            ]
        }),
        encoding="utf-8",
    )
    artifact_root = tmp_path / ".iriai" / "artifacts" / "features" / feature.id
    fragment = artifact_root / "subfeatures/chat-sidepane-shell/dag-fragments/slice-3.json"
    fragment.parent.mkdir(parents=True, exist_ok=True)
    task_id = "chat-sidepane-shell-slice-3-TASK-chat-util-dedup"
    fragment.write_text(
        json.dumps({
            "tasks": [],
            "_retired_tasks": [
                {
                    "id": task_id,
                    "retired_reason": "Duplicate of slice-2 TASK-SH2-1.",
                    "canonical_paths": [
                        "iriai-studio/src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts"
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )
    stale_task = ImplementationTask(
        id=task_id,
        name="Dedup",
        description="Stale retired task",
        subfeature_id="chat-sidepane-shell",
        file_scope=[
            {
                "path": (
                    "iriai-studio/src/vs/workbench/contrib/studioWorkflow/"
                    "browser/workflowTab/chat/util/eventDeduplicator.ts"
                ),
                "action": "create",
            }
        ],
        files=[
            (
                "iriai-studio/src/vs/workbench/contrib/studioWorkflow/"
                "browser/workflowTab/chat/util/eventDeduplicator.ts"
            )
        ],
    )
    artifacts = _RecordingArtifacts()
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"artifact_mirror": _Mirror(artifact_root)},
    )

    outcome = await implementation_module._reconcile_dag_task_specs(
        runner,
        feature,
        30,
        "retry-1",
        [stale_task],
        feature_root=feature_root,
    )

    assert outcome.tasks[0].file_scope == []
    assert outcome.tasks[0].files == []
    assert "Retired task projection" in outcome.tasks[0].description
    assert outcome.report["applied"][0]["action"] == "retired_task_projection"
    preflight = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        30,
        "retry-2",
        outcome.tasks,
        [],
        feature_root=feature_root,
    )
    assert preflight is None


@pytest.mark.asyncio
async def test_dag_task_reconciler_replaces_stale_and_current_same_task_results(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-memory", slug="reconcile-memory", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {
                    "path": "src/vs/workbench/contrib/iriaiStudio",
                    "source": "retired",
                }
            ]
        }),
        encoding="utf-8",
    )

    task_id = "TASK-S18-3"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=[
            "src/vs/workbench/contrib/iriaiStudio/browser/styles/workflow-card.css"
        ],
    )
    corrected = ImplementationResult(
        task_id=task_id,
        summary="corrected",
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", stale.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        28,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale, corrected],
        all_results=[stale, corrected],
        repair_results=[corrected],
        feature_root=feature_root,
    )

    assert outcome.verify_results_context == [corrected]
    assert outcome.results == [corrected]
    assert outcome.all_results == [corrected]


@pytest.mark.asyncio
async def test_artifact_repair_update_normalizes_short_task_alias(tmp_path):
    feature = SimpleNamespace(id="feat-short-alias", slug="short-alias", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/vs/workbench/contrib/studioBridge/test/browser/reconnect.integrationTest.ts"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")

    artifacts = _RecordingArtifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    full_task_id = "project-and-launcher-slice-18-TASK-SF4-S18-4"
    short_result = ImplementationResult(
        task_id="TASK-SF4-S18-4",
        summary="short alias",
        files_created=[
            "src/vs/workbench/contrib/studioBridge/test/browser/"
            "reconnect.integrationTest.ts"
        ],
    )

    record = await implementation_module._apply_dag_artifact_repair_updates(
        runner,
        feature,
        ArtifactRepairResult(
            task_id="ARTIFACT-REPAIR",
            group_id="G29",
            summary="repair short alias",
            artifact_updates=[
                ArtifactRepairUpdate(
                    artifact_key=f"dag-task:{full_task_id}",
                    content=short_result.model_dump_json(),
                )
            ],
        ),
        feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        await artifacts.get(f"dag-task:{full_task_id}", feature=feature)
    )
    assert stored.task_id == full_task_id
    assert record["applied_updates"][0]["task_id"] == full_task_id


@pytest.mark.asyncio
async def test_dag_task_reconciler_rejects_forbidden_existing_candidate(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-forbidden", slug="reconcile-forbidden", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    forbidden = repo / "src/vs/workbench/contrib/iriaiStudio/test/integration/reconnect.test.ts"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("bad", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "forbidden_files": [
                {"path": "src/vs/workbench/contrib/iriaiStudio", "source": "retired"}
            ]
        }),
        encoding="utf-8",
    )

    task_id = "TASK-S18-4"
    candidate = ImplementationResult(
        task_id=task_id,
        summary="forbidden",
        files_created=[
            "src/vs/workbench/contrib/iriaiStudio/test/integration/reconnect.test.ts"
        ],
    )
    artifacts = _RecordingArtifacts()
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        29,
        "retry-1",
        [task],
        results=[candidate],
        verify_results_context=[candidate],
        all_results=[candidate],
        repair_results=[candidate],
        feature_root=feature_root,
    )

    assert f"dag-task:{task_id}" not in artifacts.rows
    assert outcome.report["skipped"]


@pytest.mark.asyncio
async def test_dag_task_reconciler_idempotent_when_latest_row_is_valid(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-idempotent", slug="reconcile-idempotent", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical = repo / "src/webviews/projectSurface/src/styles/dashboard.css"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("ok", encoding="utf-8")
    task_id = "TASK-S18-3"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_modified=["src/missing.css"],
    )
    current = ImplementationResult(
        task_id=task_id,
        summary="current",
        files_modified=["src/webviews/projectSurface/src/styles/dashboard.css"],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", current.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        28,
        "initial",
        [task],
        results=[stale],
        verify_results_context=[stale],
        all_results=[stale],
        repair_results=[],
        feature_root=feature_root,
    )

    assert len(artifacts.rows[f"dag-task:{task_id}"]) == 1
    assert outcome.results == [current]
    assert outcome.report["applied"][0]["action"] == "already_current"


@pytest.mark.asyncio
async def test_dag_task_reconciler_latest_valid_db_wins_over_competing_candidates(
    tmp_path,
):
    feature = SimpleNamespace(
        id="feat-reconcile-latest-wins",
        slug="reconcile-latest-wins",
        metadata={},
    )
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    canonical_paths = [
        "src/webviews/projectSurface/src/chat/stores/types.ts",
        "src/webviews/projectSurface/src/chat/stores/EventDeduplicator.ts",
        "src/webviews/projectSurface/src/chat/stores/useChatStreamStore.ts",
        "src/webviews/projectSurface/src/chat/stores/index.ts",
        "src/webviews/projectSurface/src/chat/stores/__tests__/"
        "EventDeduplicator.test.ts",
        "src/webviews/projectSurface/src/chat/stores/__tests__/"
        "useChatStreamStore.test.ts",
    ]
    for path in canonical_paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    config_path = repo / "scripts/verify-file-scope.expected-files.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({
            "expected_files": [
                {"path": path, "source": "TASK-SH2-1 canonical"}
                for path in canonical_paths
            ],
            "forbidden_files": [
                {
                    "path": (
                        "src/vs/workbench/contrib/studioWorkflow/browser/"
                        "workflowTab/chat/stores"
                    ),
                    "source": "retired chat stores",
                }
            ],
        }),
        encoding="utf-8",
    )

    task_id = "chat-sidepane-shell-slice-2-TASK-SH2-1"
    stale_paths = [
        path.replace(
            "src/webviews/projectSurface/src/chat/stores",
            "src/vs/workbench/contrib/studioWorkflow/browser/"
            "workflowTab/chat/stores",
        )
        for path in canonical_paths
    ]
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale",
        files_created=stale_paths,
    )
    latest_db = ImplementationResult(
        task_id=task_id,
        summary="latest canonical DB row",
        files_created=canonical_paths,
    )
    competing_repair = ImplementationResult(
        task_id=task_id,
        summary="repair with equivalent repo-prefixed paths",
        files_created=[f"iriai-studio/{path}" for path in canonical_paths],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(
        f"dag-task:{task_id}",
        latest_db.model_dump_json(),
        feature=feature,
    )
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        29,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale, competing_repair],
        all_results=[stale, competing_repair],
        repair_results=[competing_repair],
        feature_root=feature_root,
    )

    assert len(artifacts.rows[f"dag-task:{task_id}"]) == 1
    assert outcome.results == [latest_db]
    assert outcome.verify_results_context == [latest_db]
    assert outcome.all_results == [latest_db]
    assert outcome.report["blockers"] == []
    assert outcome.report["applied"][0]["source"] == "latest_db"
    assert outcome.report["applied"][0]["action"] == "already_current"

    preflight = await implementation_module._run_dag_group_preflight(
        runner,
        feature,
        29,
        "retry-2",
        [task],
        outcome.verify_results_context,
        feature_root=feature_root,
    )
    assert preflight is None


@pytest.mark.asyncio
async def test_dag_task_reconciler_preserves_valid_original_files_with_replacement(tmp_path):
    feature = SimpleNamespace(id="feat-reconcile-merge", slug="reconcile-merge", metadata={})
    feature_root = tmp_path / "repos"
    repo = feature_root / "iriai-studio"
    (repo / ".git").mkdir(parents=True)
    original_path = "src/webviews/projectSurface/src/dashboard/components/WorkflowCard.tsx"
    replacement_path = "src/webviews/projectSurface/src/styles/dashboard.css"
    for path in (original_path, replacement_path):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")

    task_id = "TASK-MERGE"
    stale = ImplementationResult(
        task_id=task_id,
        summary="stale with one valid path",
        files_modified=[
            original_path,
            "src/vs/workbench/contrib/iriaiStudio/browser/styles/workflow-card.css",
        ],
    )
    replacement = ImplementationResult(
        task_id=task_id,
        summary="replacement evidence",
        files_modified=[replacement_path],
    )
    artifacts = _RecordingArtifacts()
    await artifacts.put(f"dag-task:{task_id}", stale.model_dump_json(), feature=feature)
    runner = SimpleNamespace(artifacts=artifacts, services={"test_allow_legacy_repair_without_sandbox": True})
    task = ImplementationTask(id=task_id, name="Task", description="Task")

    outcome = await implementation_module._reconcile_dag_task_results(
        runner,
        feature,
        30,
        "retry-1",
        [task],
        results=[stale],
        verify_results_context=[stale, replacement],
        all_results=[stale, replacement],
        repair_results=[replacement],
        feature_root=feature_root,
    )

    stored = ImplementationResult.model_validate_json(
        await artifacts.get(f"dag-task:{task_id}", feature=feature)
    )
    assert stored.files_modified == [original_path, replacement_path]
    assert outcome.results == [stored]
    assert outcome.report["applied"][0]["action"] == "appended_dag_task_row"


@pytest.mark.asyncio
async def test_parallel_dag_repair_rejects_unsafe_artifact_repair_and_persists_reason(
    tmp_path,
):
    feature = SimpleNamespace(id="feat-rejected-artifact", slug="rejected-artifact", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-BAD-ARTIFACT",
                        likely_root_cause="unsafe artifact repair",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="claims artifact repair but names source",
                    affected_files=["pkg/source.py"],
                    proposed_approach="patch source as artifact repair",
                    confidence="contradiction",
                    contradiction_detail="bad layer",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="Patch source through artifact repair.",
                    resolution_kind="artifact_repair",
                    authoritative_sources=["pkg/source.py:1"],
                    implementation_direction="Patch pkg/source.py.",
                    confidence="high",
                    needs_human=False,
                )
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    result = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        11,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="unsafe artifact repair"),
                Issue(severity="major", description="same root cause"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert result is None
    rejected = json.loads(
        runner.artifacts.store[
            "contradiction-rejected:dag-repair:g11:retry-0:BG-BAD-ARTIFACT"
        ]
    )
    assert "artifact_repair_has_non_artifact_paths" in rejected["rejection_reasons"]
    assert rejected["raw_resolution"]["resolution_kind"] == "artifact_repair"
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g11:retry-0"])
    assert dispatch["rejected_contradiction_count"] == 1
    assert dispatch["fallback_reason"] == "unresolved_contradiction"


@pytest.mark.asyncio
async def test_parallel_dag_repair_human_needed_contradiction_fails_closed(tmp_path):
    feature = SimpleNamespace(id="feat-human-needed", slug="human-needed", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="conflict",
                        issue_indices=[0],
                        severity="major",
                    )
                ])
            if task.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="conflict",
                    affected_files=["pkg/code.py"],
                    proposed_approach="decide",
                    confidence="contradiction",
                    contradiction_detail="A vs B",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="",
                    authoritative_sources=[],
                    requires_code_change=False,
                    needs_human=True,
                    confidence="low",
                    rationale="Ambiguous.",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    result = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        7,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="conflict"),
                Issue(severity="major", description="second issue"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert result is None
    assert runner.fix_attempted is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g7:retry-0"])
    assert dispatch["fallback_reason"] == "unresolved_contradiction"
    assert dispatch["human_needed_contradiction_count"] == 1


@pytest.mark.asyncio
async def test_parallel_dag_repair_human_needed_does_not_block_unrelated_fix(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-human-plus-fix", slug="human-plus-fix", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-HUMAN",
                        likely_root_cause="ambiguous product decision",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FIX",
                        likely_root_cause="ordinary bug",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-HUMAN" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="ambiguous product behavior",
                        affected_files=["pkg/decision.py"],
                        proposed_approach="ask human",
                        confidence="contradiction",
                        contradiction_detail="A vs B",
                    )
                return RootCauseAnalysis(
                    hypothesis="ordinary bug",
                    affected_files=["pkg/fix.py"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="A human must choose behavior.",
                    resolution_kind="needs_human",
                    authoritative_sources=["pkg/spec_a.md", "pkg/spec_b.md"],
                    needs_human=True,
                    confidence="medium",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
                return ImplementationResult(
                    task_id="FIX-BG-FIX",
                    summary="fixed unrelated bug",
                    files_modified=["pkg/fix.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        12,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="ambiguous behavior"),
                Issue(severity="major", description="ordinary bug"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert runner.fix_attempted is True
    assert results is not None
    assert [result.task_id for result in results] == ["FIX-BG-FIX"]
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g12:retry-0"])
    assert dispatch["fallback_reason"] == ""
    assert dispatch["human_needed_contradiction_count"] == 1
    assert dispatch["blocked_fix_group_ids"] == []
    assert dispatch["schedule"] == [{"round": 0, "group_ids": ["BG-FIX"]}]


@pytest.mark.asyncio
async def test_parallel_dag_repair_human_needed_blocks_overlapping_fix(tmp_path):
    feature = SimpleNamespace(id="feat-human-overlap", slug="human-overlap", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-HUMAN",
                        likely_root_cause="ambiguous shared file",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FIX",
                        likely_root_cause="ordinary bug same file",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-HUMAN" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="ambiguous product behavior",
                        affected_files=["pkg/shared.py"],
                        proposed_approach="ask human",
                        confidence="contradiction",
                        contradiction_detail="A vs B",
                    )
                return RootCauseAnalysis(
                    hypothesis="ordinary bug same file",
                    affected_files=["pkg/shared.py"],
                    proposed_approach="patch it",
                    confidence="high",
                )
            if task.output_type is implementation_module.DagContradictionResolution:
                return implementation_module.DagContradictionResolution(
                    resolution="A human must choose behavior.",
                    resolution_kind="needs_human",
                    authoritative_sources=["pkg/spec_a.md", "pkg/spec_b.md"],
                    needs_human=True,
                    confidence="medium",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        13,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="ambiguous behavior"),
                Issue(severity="major", description="ordinary bug same file"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is None
    assert runner.fix_attempted is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g13:retry-0"])
    assert dispatch["fallback_reason"] == "unresolved_contradiction"
    assert dispatch["blocked_fix_group_ids"] == ["BG-FIX"]


@pytest.mark.asyncio
async def test_parallel_dag_repair_records_failed_fix_agent_without_crashing(
    monkeypatch,
    tmp_path,
):
    feature = SimpleNamespace(id="feat-fix-agent-error", slug="fix-agent-error", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"autonomous_remainder": True, "test_allow_legacy_repair_without_sandbox": True}
            self.parallel_batches: list[list[str]] = []

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-OK",
                        likely_root_cause="ordinary bug",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FAIL",
                        likely_root_cause="quota failure while fixing",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-FAIL" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="quota prone fix",
                        affected_files=["pkg/fail.py"],
                        proposed_approach="patch fail",
                        confidence="high",
                    )
                return RootCauseAnalysis(
                    hypothesis="ordinary bug",
                    affected_files=["pkg/ok.py"],
                    proposed_approach="patch ok",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                if "BG-FAIL" in task.actor.name:
                    raise RuntimeError("Claude pool quota exhausted")
                return ImplementationResult(
                    task_id="FIX-BG-OK",
                    summary="fixed ok bug",
                    files_modified=["pkg/ok.py"],
                )
            if task.output_type is Verdict:
                return Verdict(approved=True, summary="focused clean")
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            self.parallel_batches.append([task.actor.name for task in tasks])
            return [await self.run(task, feature) for task in tasks]

    async def _no_commit(*args, **kwargs):
        return "commit"

    monkeypatch.setattr(implementation_module, "_commit_repos", _no_commit)
    runner = _Runner()
    results = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        14,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="ordinary bug"),
                Issue(severity="major", description="quota-prone bug"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert results is not None
    by_id = {result.task_id: result for result in results}
    assert by_id["FIX-BG-OK"].status == "completed"
    failed = by_id["DAG-REPAIR-FAILED-g14-r0-BG-FAIL"]
    assert failed.status == "blocked"
    assert "Claude pool quota exhausted" in failed.summary
    error_key = "dag-repair-fix-error:g14:BG-FAIL:retry-0:round-0"
    assert error_key in runner.artifacts.store
    error_record = json.loads(runner.artifacts.store[error_key])
    assert error_record["status"] == "blocked"


@pytest.mark.asyncio
async def test_dag_repair_runtime_crash_retains_sandbox_patch_evidence(tmp_path):
    feature = SimpleNamespace(id="feat-repair-crash", slug="repair-crash", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()

        async def run(self, task, feature, phase_name=""):
            del task, feature, phase_name
            raise RuntimeError("provider crashed mid-repair")

    class _SandboxRunner:
        def __init__(self) -> None:
            self.captured = False
            self.released: list[str] = []

        async def capture_patch(self, lease):
            del lease
            self.captured = True
            return SimpleNamespace(
                sandbox_id="sandbox-crash",
                patch_summary_ids=[44],
                repo_patches=[],
                empty=False,
                clean_after_capture=True,
            )

        async def release(self, lease, disposition):
            del lease
            self.released.append(disposition)

    sandbox_runner = _SandboxRunner()
    binding = implementation_module.RuntimeSandboxTaskBinding(
        runner=sandbox_runner,
        lease=SimpleNamespace(sandbox_id="sandbox-crash", root=str(tmp_path)),
        binding=SimpleNamespace(cwd=str(tmp_path)),
    )
    ask = Ask(
        actor=implementation_module.implementer,
        prompt="fix",
        output_type=ImplementationResult,
    )
    runner = _Runner()

    results = await implementation_module._run_dag_repair_fix_tasks(
        runner,
        feature,
        14,
        0,
        0,
        ["BG-FAIL"],
        [(ask, binding)],
    )

    assert sandbox_runner.captured is True
    assert sandbox_runner.released == ["retain"]
    failed = results[0]
    assert isinstance(failed, ImplementationResult)
    assert failed.status == "blocked"
    assert "SANDBOX_WORKFLOW_BLOCKER" in failed.summary
    assert "provider crashed mid-repair" in failed.summary
    assert "dag-repair-fix-error:g14:BG-FAIL:retry-0:round-0" in runner.artifacts.store


@pytest.mark.asyncio
async def test_dag_repair_runtime_crash_retains_real_sandbox_diff_artifact(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature_root = workspace_root / ".iriai" / "features" / "repair-real" / "repos"
    repo = feature_root / "app"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "fix.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "repair@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Repair Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    feature = SimpleNamespace(id="feat-repair-real", slug="repair-real", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}
            self.diff_artifacts: list[tuple[str, bytes, dict[str, object]]] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def write_artifact_bytes(self, key, data, metadata, *, feature):
            del feature
            self.diff_artifacts.append((key, data, metadata))
            return SimpleNamespace(id=900 + len(self.diff_artifacts))

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"workspace_manager": SimpleNamespace(_base=workspace_root)}

        async def run(self, ask, feature, phase_name=""):
            del feature, phase_name
            cwd = Path(ask.actor.role.metadata["workspace_override"])
            (cwd / "src" / "fix.py").write_text("value = 'sandbox edit'\n", encoding="utf-8")
            raise RuntimeError("provider crashed after editing sandbox")

    runner = _Runner()
    task = ImplementationTask(
        id="TASK-repair-real",
        name="Repair real",
        description="Repair real",
        repo_path="app",
        file_scope=[TaskFileScope(path="app/src/fix.py", action="modify")],
    )
    binding = await implementation_module._bind_repair_sandbox(
        runner,
        feature,
        workspace_root=workspace_root,
        feature_root=feature_root,
        dag_sha256="d" * 64,
        group_idx=14,
        retry=0,
        repair_idx=0,
        repair_id="DAG-REPAIR-g14-r0-BG-REAL",
        group_tasks=[task],
        contracts_by_task_id=None,
        ws_path=str(repo),
        snapshots=[],
        runtime="primary",
    )
    assert binding is not None
    ask = Ask(
        actor=implementation_module._make_parallel_actor(
            implementation_module.implementer,
            "repair-real",
            workspace_path=str(binding.binding.cwd),
            runtime_workspace_binding=binding.binding,
            sandbox_required=True,
        ),
        prompt="fix",
        output_type=ImplementationResult,
    )

    results = await implementation_module._run_dag_repair_fix_tasks(
        runner,
        feature,
        14,
        0,
        0,
        ["BG-REAL"],
        [(ask, binding)],
    )

    failed = results[0]
    assert isinstance(failed, ImplementationResult)
    assert failed.status == "blocked"
    assert "provider crashed after editing sandbox" in failed.summary
    assert runner.artifacts.diff_artifacts
    diff_text = runner.artifacts.diff_artifacts[0][1].decode("utf-8")
    assert "sandbox edit" in diff_text
    assert (repo / "src" / "fix.py").read_text(encoding="utf-8") == "value = 'base'\n"


@pytest.mark.asyncio
async def test_single_rca_fix_verify_runtime_crash_retains_real_sandbox_diff_artifact(tmp_path):
    workspace_root = tmp_path / "workspace"
    feature_root = workspace_root / ".iriai" / "features" / "single-repair" / "repos"
    repo = feature_root / "app"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "fix.py").write_text("value = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "repair@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Repair Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    feature = SimpleNamespace(id="feat-single-repair", slug="single-repair", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}
            self.diff_artifacts: list[tuple[str, bytes, dict[str, object]]] = []

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

        async def write_artifact_bytes(self, key, data, metadata, *, feature):
            del feature
            self.diff_artifacts.append((key, data, metadata))
            return SimpleNamespace(id=950 + len(self.diff_artifacts))

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"workspace_manager": SimpleNamespace(_base=workspace_root)}
            self.output_types: list[type] = []

        async def run(self, ask, feature, phase_name=""):
            del feature, phase_name
            self.output_types.append(ask.output_type)
            if ask.output_type is RootCauseAnalysis:
                return RootCauseAnalysis(
                    hypothesis="Fix the app file",
                    evidence=["app/src/fix.py is wrong"],
                    affected_files=["app/src/fix.py"],
                    proposed_approach="Change the value",
                    confidence="high",
                )
            if ask.output_type is ImplementationResult:
                cwd = Path(ask.actor.role.metadata["workspace_override"])
                (cwd / "src" / "fix.py").write_text(
                    "value = 'sandbox single edit'\n",
                    encoding="utf-8",
                )
                raise RuntimeError("provider crashed after single repair edit")
            if ask.output_type is Verdict:
                raise AssertionError("single repair crash must not reverify")
            raise AssertionError(f"unexpected task: {ask!r}")

    runner = _Runner()

    attempt = await implementation_module._single_rca_fix_verify(
        runner,
        feature,
        "verdict",
        "verifier",
        implementation_module.verifier,
        implementation_module.implementer,
        prior_context="",
        bug_id="BUG-SINGLE-CRASH",
        attempt_number=1,
        skip_regression=True,
        workspace_root=feature_root,
        rca_runtime="primary",
    )

    assert attempt.re_verify_result == "FAIL"
    assert "SANDBOX_WORKFLOW_BLOCKER" in attempt.fix_applied
    assert "provider crashed after single repair edit" in attempt.fix_applied
    assert runner.artifacts.diff_artifacts
    diff_text = runner.artifacts.diff_artifacts[0][1].decode("utf-8")
    assert "sandbox single edit" in diff_text
    assert (repo / "src" / "fix.py").read_text(encoding="utf-8") == "value = 'base'\n"
    assert RootCauseAnalysis in runner.output_types
    assert Verdict not in runner.output_types


@pytest.mark.asyncio
async def test_parallel_dag_repair_non_autonomous_preserves_manual_fallback(tmp_path):
    feature = SimpleNamespace(id="feat-manual", slug="manual", metadata={})

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del key, feature
            return ""

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {"test_allow_legacy_repair_without_sandbox": True}
            self.fix_attempted = False

        async def run(self, task, feature, phase_name=""):
            del feature, phase_name
            if task.output_type is BugTriage:
                return BugTriage(groups=[
                    BugGroup(
                        group_id="BG-CONTRA",
                        likely_root_cause="conflict",
                        issue_indices=[0],
                        severity="major",
                    ),
                    BugGroup(
                        group_id="BG-FIX",
                        likely_root_cause="fix",
                        issue_indices=[1],
                        severity="major",
                    ),
                ])
            if task.output_type is RootCauseAnalysis:
                if "BG-CONTRA" in task.actor.name:
                    return RootCauseAnalysis(
                        hypothesis="conflict",
                        affected_files=["pkg/code.py"],
                        proposed_approach="decide",
                        confidence="contradiction",
                        contradiction_detail="A vs B",
                    )
                return RootCauseAnalysis(
                    hypothesis="fix",
                    affected_files=["pkg/fix.py"],
                    proposed_approach="patch",
                    confidence="high",
                )
            if task.output_type is ImplementationResult:
                self.fix_attempted = True
            raise AssertionError(f"unexpected output type {task.output_type}")

        async def parallel(self, tasks, feature):
            return [await self.run(task, feature) for task in tasks]

    runner = _Runner()
    result = await implementation_module._attempt_parallel_dag_repair(
        runner,
        feature,
        8,
        0,
        Verdict(
            approved=False,
            summary="failed",
            concerns=[
                Issue(severity="major", description="conflict"),
                Issue(severity="major", description="fixable"),
            ],
        ),
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        feature_root=tmp_path,
        impl_runtime="primary",
        rca_runtime="primary",
        feedback="failed",
    )

    assert result is None
    assert runner.fix_attempted is False
    dispatch = json.loads(runner.artifacts.store["dag-repair-dispatch:g8:retry-0"])
    assert dispatch["fallback_reason"] == "manual_contradiction_resolution_required"
    assert dispatch["fixable_group_count"] == 1


@pytest.mark.asyncio
async def test_contradiction_decisions_pack_includes_relevant_legacy_group():
    feature = SimpleNamespace(id="feat-legacy", slug="legacy", metadata={})
    dag = implementation_module.ImplementationDAG(
        execution_order=[[f"TASK-{idx}"] for idx in range(27)]
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "dag":
                return dag.model_dump_json()
            if key == "contradiction:verify:dag-g26-r0":
                return json.dumps({
                    "revision_plan": {
                        "requests": [
                            {"description": "Ratify @v1 event names for group 26."}
                        ],
                        "new_decisions": ["D-GR-X: @v1 names are authoritative."],
                    }
                })
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})

    context = await implementation_module._format_contradiction_decisions_context(
        runner,
        feature,
        group_idx=26,
        retry=0,
        context_text="Current verifier references D-GR-X.",
        file_stem="test-g26",
    )

    assert "Current relevant decision pack" in context
    current_path = re.search(r"Current relevant decision pack: `([^`]+)`", context)
    assert current_path is not None
    current_text = Path(current_path.group(1)).read_text(encoding="utf-8")
    assert "contradiction:verify:dag-g26-r0" in current_text
    assert "Ratify @v1 event names for group 26." in current_text
    assert "D-GR-X: @v1 names are authoritative." in current_text


@pytest.mark.asyncio
async def test_contradiction_decisions_pack_indexes_unrelated_history_without_inlining():
    feature = SimpleNamespace(id="feat-g77", slug="g77", metadata={})
    dag = implementation_module.ImplementationDAG(
        execution_order=[[f"TASK-{idx}"] for idx in range(80)]
    )

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del feature
            if key == "dag":
                return dag.model_dump_json()
            if key == implementation_module.CONTRADICTION_DECISIONS_KEY:
                return json.dumps({
                    "decisions": [
                        {
                            "artifact_key": f"contradiction:dag-repair:g26:retry-0:old-{idx}",
                            "source": "dag-repair",
                            "resolution": f"Historical unrelated decision {idx}",
                            "implementation_direction": "Do an old thing.",
                        }
                        for idx in range(459)
                    ] + [
                        {
                            "artifact_key": "contradiction:dag-repair:g77:retry-0:bridge-catalog",
                            "source": "dag-repair",
                            "resolution": "D-GR-77 requires command_ack@v1 catalog wiring.",
                            "implementation_direction": "Update bridge-event-catalog.md.",
                        }
                    ]
                })
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts(), services={"test_allow_legacy_repair_without_sandbox": True})
    context = await implementation_module._format_contradiction_decisions_context(
        runner,
        feature,
        group_idx=77,
        retry=0,
        context_text="The current failure cites D-GR-77 and command_ack@v1.",
        file_stem="test-g77",
    )

    current_path = re.search(r"Current relevant decision pack: `([^`]+)`", context)
    index_path = re.search(r"Compact historical index: `([^`]+)`", context)
    assert current_path is not None
    assert index_path is not None
    current_text = Path(current_path.group(1)).read_text(encoding="utf-8")
    index_text = Path(index_path.group(1)).read_text(encoding="utf-8")

    assert "contradiction:dag-repair:g77:retry-0:bridge-catalog" in current_text
    assert "D-GR-77 requires command_ack@v1 catalog wiring" in current_text
    assert "Historical unrelated decision 1" not in current_text
    assert "contradiction:dag-repair:g26:retry-0:old-1" in index_text
    assert len(current_text) < 120_000
