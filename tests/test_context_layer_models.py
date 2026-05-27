from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import CompletenessState
from iriai_build_v2.workflows.develop.context_layer import (
    CodeSpanRef,
    ContextCompleteness,
    ContextEvidenceSnapshot,
    ContextLayerBudget,
    ContextLayerRequest,
    ContextLayerService,
    ContextPageStore,
    ProviderLineageRecord,
    ProviderStateRef,
    compute_context_package_digest,
    compute_provider_state_digest,
)


def _snapshot(**overrides: object) -> ContextEvidenceSnapshot:
    values = {
        "source_dag_artifact_id": 7,
        "dag_sha256": "dag-digest",
        "typed_journal_high_watermark": 99,
        "typed_evidence_digest": "typed-evidence-digest",
        "commit_proof_digest": "commit-proof-digest",
        "governance_snapshot_digest": "governance-snapshot-digest",
    }
    values.update(overrides)
    return ContextEvidenceSnapshot(**values)


def _request(**overrides: object) -> ContextLayerRequest:
    snapshot = overrides.pop("evidence_snapshot", _snapshot())
    values = {
        "feature_id": "feature-21",
        "source_dag_artifact_id": snapshot.source_dag_artifact_id,
        "dag_sha256": snapshot.dag_sha256,
        "evidence_snapshot": snapshot,
        "task_id": "task-1",
        "repo_ids": ["repo"],
        "spans": [
            CodeSpanRef(
                repo_id="repo",
                path="src/app.py",
                start_line=1,
                end_line=2,
            )
        ],
    }
    values.update(overrides)
    return ContextLayerRequest(**values)


def _provider_record(content_digest: str = "content-a") -> ProviderLineageRecord:
    span = CodeSpanRef(repo_id="repo", path="src/app.py", start_line=1, end_line=2)
    return ProviderLineageRecord(
        record_id="provider-record-1",
        provider="native_git",
        repo_id="repo",
        path="src/app.py",
        start_line=1,
        end_line=2,
        code_span=span,
        commit_hashes=["a" * 40],
        provider_refs=["git-commit:" + "a" * 40],
        provider_state_digest="provider-state",
        content_digest=content_digest,
        confidence=0.9,
        warnings=[],
    )


def test_context_completeness_is_shared_slice_13a_alias() -> None:
    assert get_args(ContextCompleteness) == get_args(CompletenessState)


def test_context_layer_budget_rejects_zero_caps() -> None:
    with pytest.raises(ValidationError):
        ContextLayerBudget(max_files=0)


def test_code_span_ref_rejects_absolute_or_escaping_paths() -> None:
    with pytest.raises(ValidationError):
        CodeSpanRef(repo_id="repo", path="/tmp/app.py", start_line=1, end_line=1)

    with pytest.raises(ValidationError):
        CodeSpanRef(repo_id="repo", path="../app.py", start_line=1, end_line=1)


def test_context_layer_request_identity_must_match_snapshot() -> None:
    snapshot = _snapshot(source_dag_artifact_id=10)

    with pytest.raises(ValidationError):
        ContextLayerRequest(
            feature_id="feature-21",
            source_dag_artifact_id=11,
            dag_sha256=snapshot.dag_sha256,
            evidence_snapshot=snapshot,
        )


def test_request_allows_oversized_span_for_package_paging() -> None:
    request = _request(
        budget=ContextLayerBudget(max_lines_per_span=1),
        spans=[
            CodeSpanRef(
                repo_id="repo",
                path="src/app.py",
                start_line=1,
                end_line=2,
            )
        ],
    )

    assert request.spans[0].line_count == 2


def test_provider_state_digest_includes_unavailable_provider_states() -> None:
    available = ProviderStateRef(
        provider="native_git",
        repo_id="repo",
        ref="HEAD",
        state_digest="native-state",
        indexed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        status="available",
    )
    disabled = ProviderStateRef(
        provider="git_ai",
        repo_id="repo",
        ref="disabled",
        state_digest="disabled-state",
        indexed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        status="disabled",
    )

    native_only = compute_provider_state_digest([available])
    with_optional_state = compute_provider_state_digest([available, disabled])

    assert native_only != with_optional_state


def test_provider_state_digest_excludes_observation_timestamp() -> None:
    first = ProviderStateRef(
        provider="native_git",
        repo_id="repo",
        ref="HEAD",
        state_digest="native-state",
        indexed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        status="available",
    )
    second = first.model_copy(
        update={"indexed_at": datetime(2026, 5, 28, tzinfo=timezone.utc)}
    )

    assert compute_provider_state_digest([first]) == compute_provider_state_digest([second])


def test_context_package_digest_changes_when_record_content_digest_changes() -> None:
    request = _request()
    record_a = _provider_record("content-a")
    record_b = _provider_record("content-b")

    digest_a = compute_context_package_digest(
        request=request,
        evidence_snapshot=request.evidence_snapshot,
        provider_state_digest="provider-state",
        provider_order=["native_git"],
        provider_records=[record_a],
        iriai_lineage=[],
        page_refs=[],
        omitted_counts={},
        completeness="complete",
        rendered_preview=None,
        incomplete_reason=None,
    )
    digest_b = compute_context_package_digest(
        request=request,
        evidence_snapshot=request.evidence_snapshot,
        provider_state_digest="provider-state",
        provider_order=["native_git"],
        provider_records=[record_b],
        iriai_lineage=[],
        page_refs=[],
        omitted_counts={},
        completeness="complete",
        rendered_preview=None,
        incomplete_reason=None,
    )

    assert digest_a != digest_b


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ContextEvidenceSnapshot(
            source_dag_artifact_id=1,
            dag_sha256="dag",
            typed_journal_high_watermark=0,
            typed_evidence_digest="typed",
            extra_field="nope",
        )


def test_public_context_layer_surface_has_no_runtime_mutation_authority() -> None:
    forbidden_prefixes = (
        "activate",
        "approve",
        "checkpoint",
        "delete",
        "execute",
        "insert",
        "merge",
        "migrate",
        "mutate",
        "persist",
        "rewrite",
        "save",
        "update",
        "write",
    )
    service_methods = [
        name
        for name, _ in inspect.getmembers(ContextLayerService, inspect.isfunction)
        if not name.startswith("_")
    ]
    store_methods = [
        name
        for name, _ in inspect.getmembers(ContextPageStore, inspect.isfunction)
        if not name.startswith("_")
    ]

    assert service_methods == ["build_context_package", "get_exact_page"]
    assert store_methods == ["get"]
    assert not any(name.startswith(forbidden_prefixes) for name in service_methods)
    assert not any(name.startswith(forbidden_prefixes) for name in store_methods)
