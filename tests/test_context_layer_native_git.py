from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.context_layer import (
    CodeSpanRef,
    ContextLayerBudget,
    ContextEvidenceSnapshot,
    ContextLayerRequest,
    ContextLayerService,
    IriAILineagePlugin,
    IriAILineageSourceRecord,
    NativeGitProvider,
)
from iriai_build_v2.workflows.develop.execution.workspace_authority import RepoIdentity


def _run(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _make_repo(tmp_path: Path) -> tuple[Path, str]:
    if shutil.which("git") is None:
        pytest.skip("git is required for NativeGitProvider fixture")
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init")
    _run(repo, "config", "user.email", "slice21@example.invalid")
    _run(repo, "config", "user.name", "Slice 21")
    (repo / "app.py").write_text("alpha = 1\nbeta = alpha + 1\n", encoding="utf-8")
    _run(repo, "add", "app.py")
    _run(
        repo,
        "commit",
        "-m",
        "Add app",
        "-m",
        "IriAI-Feature: feature-21\nIriAI-Task: task-1",
    )
    commit = _run(repo, "rev-parse", "HEAD")
    _run(repo, "notes", "--ref=iriai", "add", "-m", "dag-commit-proof:5000", commit)
    return repo, commit


def _repo_identity(repo: Path) -> RepoIdentity:
    return RepoIdentity(
        repo_id="repo",
        repo_name="repo",
        canonical_path=str(repo),
        workspace_relative_path="repo",
        safety_status="ok",
    )


def test_native_git_provider_maps_line_range_to_git_and_commit_proof_refs(tmp_path: Path) -> None:
    repo, commit = _make_repo(tmp_path)
    provider = NativeGitProvider(
        [_repo_identity(repo)],
        commit_proof_refs={commit: ["dag-commit-proof:5000"]},
    )
    span = CodeSpanRef(repo_id="repo", path="app.py", start_line=1, end_line=1)

    availability = asyncio.run(provider.available())
    record = asyncio.run(
        provider.query_spans(
            _repo_identity(repo),
            [span],
            budget=ContextLayerRequest(
                feature_id="feature-21",
                source_dag_artifact_id=1,
                dag_sha256="dag",
                evidence_snapshot=ContextEvidenceSnapshot(
                    source_dag_artifact_id=1,
                    dag_sha256="dag",
                    typed_journal_high_watermark=1,
                    typed_evidence_digest="typed",
                ),
            ).budget,
        )
    )[0]

    assert availability.status == "available"
    assert commit in record.commit_hashes
    assert "git-commit:" + commit in record.provider_refs
    assert any(ref.startswith(f"git-trailer:{commit}:IriAI-Task=") for ref in record.provider_refs)
    assert any(ref.startswith(f"git-notes:refs/notes/iriai:{commit}:") for ref in record.provider_refs)
    assert "dag-commit-proof:5000" in record.provider_refs
    assert "line_provenance_gap:missing_commit_proof" not in record.warnings


@pytest.mark.asyncio
async def test_native_git_provider_alone_builds_context_package(tmp_path: Path) -> None:
    repo, commit = _make_repo(tmp_path)
    provider = NativeGitProvider(
        [_repo_identity(repo)],
        commit_proof_refs={commit: ["dag-commit-proof:5000"]},
    )
    span = CodeSpanRef(repo_id="repo", path="app.py", start_line=1, end_line=1)
    snapshot = ContextEvidenceSnapshot(
        source_dag_artifact_id=1,
        dag_sha256="dag",
        typed_journal_high_watermark=1,
        typed_evidence_digest="typed",
        commit_proof_digest="commit-proof",
    )
    request = ContextLayerRequest(
        feature_id="feature-21",
        source_dag_artifact_id=1,
        dag_sha256="dag",
        evidence_snapshot=snapshot,
        task_id="task-1",
        repo_ids=["repo"],
        spans=[span],
    )
    source_record = IriAILineageSourceRecord(
        feature_id="feature-21",
        group_idx=1,
        effective_group_idx=None,
        code_span=span,
        commit_hashes=[commit],
        task_ids=["task-1"],
        commit_proof_evidence_ids=[5000],
        content_digest="typed-lineage",
    )
    service = ContextLayerService(
        [provider],
        repos=[_repo_identity(repo)],
        lineage_plugin=IriAILineagePlugin([source_record]),
    )

    package = await service.build_context_package(request)

    assert package.completeness == "complete"
    assert package.provider_order == ["native_git"]
    assert package.provider_records[0].provider == "native_git"
    assert package.iriai_lineage[0].task_ids == ["task-1"]
    assert package.iriai_lineage[0].commit_proof_evidence_ids == [5000]
    assert package.advisory_only is True
    assert package.review_ref == f"review:context-package:{package.package_id}"


@pytest.mark.asyncio
async def test_native_git_provider_payload_budget_fails_closed_without_whole_file_body(
    tmp_path: Path,
) -> None:
    repo, _ = _make_repo(tmp_path)
    (repo / "large.py").write_text("value = '" + ("x" * 4096) + "'\n", encoding="utf-8")
    _run(repo, "add", "large.py")
    _run(repo, "commit", "-m", "Add large line")
    provider = NativeGitProvider([_repo_identity(repo)])
    span = CodeSpanRef(repo_id="repo", path="large.py", start_line=1, end_line=1)
    snapshot = ContextEvidenceSnapshot(
        source_dag_artifact_id=1,
        dag_sha256="dag",
        typed_journal_high_watermark=1,
        typed_evidence_digest="typed",
    )
    request = ContextLayerRequest(
        feature_id="feature-21",
        source_dag_artifact_id=1,
        dag_sha256="dag",
        evidence_snapshot=snapshot,
        repo_ids=["repo"],
        spans=[span],
        budget=ContextLayerBudget(max_provider_payload_bytes=128),
    )
    service = ContextLayerService([provider], repos=[_repo_identity(repo)])

    record = (
        await provider.query_spans(
            _repo_identity(repo),
            [span],
            budget=request.budget,
        )
    )[0]
    package = await service.build_context_package(request)

    assert "provider_payload_budget_exhausted:git_blame" in record.warnings
    assert package.completeness == "unavailable"
    assert package.incomplete_reason == "provider_payload_budget_exhausted"
    assert package.provider_records == []
    assert package.rendered_preview is None


@pytest.mark.asyncio
async def test_native_git_package_digest_is_reproducible_for_same_inputs(
    tmp_path: Path,
) -> None:
    repo, commit = _make_repo(tmp_path)
    provider = NativeGitProvider(
        [_repo_identity(repo)],
        commit_proof_refs={commit: ["dag-commit-proof:5000"]},
    )
    span = CodeSpanRef(repo_id="repo", path="app.py", start_line=1, end_line=1)
    snapshot = ContextEvidenceSnapshot(
        source_dag_artifact_id=1,
        dag_sha256="dag",
        typed_journal_high_watermark=1,
        typed_evidence_digest="typed",
        commit_proof_digest="commit-proof",
    )
    request = ContextLayerRequest(
        feature_id="feature-21",
        source_dag_artifact_id=1,
        dag_sha256="dag",
        evidence_snapshot=snapshot,
        task_id="task-1",
        repo_ids=["repo"],
        spans=[span],
    )
    service = ContextLayerService(
        [provider],
        repos=[_repo_identity(repo)],
        lineage_plugin=IriAILineagePlugin(
            [
                IriAILineageSourceRecord(
                    feature_id="feature-21",
                    group_idx=1,
                    effective_group_idx=None,
                    code_span=span,
                    commit_hashes=[commit],
                    task_ids=["task-1"],
                    commit_proof_evidence_ids=[5000],
                    content_digest="typed-lineage",
                )
            ]
        ),
    )

    first = await service.build_context_package(request)
    second = await service.build_context_package(request)

    assert first.package_id == second.package_id
    assert first.package_digest == second.package_digest
    assert first.provider_state_digest == second.provider_state_digest
