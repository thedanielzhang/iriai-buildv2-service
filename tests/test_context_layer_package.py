from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import pytest

from iriai_build_v2.workflows.develop.context_layer import (
    CodeSpanRef,
    ContextEvidenceSnapshot,
    ContextLayerBudget,
    ContextLayerRequest,
    ContextLayerService,
    ContextPageStore,
    IriAILineagePlugin,
    IriAILineageSourceRecord,
    ProviderAvailability,
    ProviderIndexResult,
    ProviderLineageRecord,
    ProviderStateRef,
)
from iriai_build_v2.workflows.develop.execution.workspace_authority import RepoIdentity
from iriai_build_v2.workflows.develop.governance.models import GovernanceEvidenceRef


class FakeProvider:
    name = "native_git"

    def __init__(self, records: Sequence[ProviderLineageRecord]) -> None:
        self.records = list(records)
        self.queried = False

    async def available(self) -> ProviderAvailability:
        return ProviderAvailability(
            provider=self.name,
            status="available",
            version="fake",
            checked_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            state_digest="fake-provider",
            timeout_ms=10,
        )

    async def index_repo(
        self,
        repo: RepoIdentity,
        *,
        budget: ContextLayerBudget,
    ) -> ProviderIndexResult:
        del budget
        return ProviderIndexResult(
            provider=self.name,
            repo_id=repo.repo_id,
            state_ref=ProviderStateRef(
                provider=self.name,
                repo_id=repo.repo_id,
                ref="HEAD",
                state_digest="fake-state",
                indexed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
                status="available",
            ),
            indexed=True,
            warnings=[],
            omitted_counts={},
        )

    async def query_spans(
        self,
        repo: RepoIdentity,
        spans: Sequence[CodeSpanRef],
        *,
        budget: ContextLayerBudget,
    ) -> list[ProviderLineageRecord]:
        del repo, spans, budget
        self.queried = True
        return list(self.records)


class DisabledOptionalProvider:
    name = "git_ai"

    def __init__(self) -> None:
        self.queried = False

    async def available(self) -> ProviderAvailability:
        return ProviderAvailability(
            provider=self.name,
            status="disabled",
            checked_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            state_digest="git-ai-disabled",
            timeout_ms=10,
            message="not configured",
        )

    async def index_repo(
        self,
        repo: RepoIdentity,
        *,
        budget: ContextLayerBudget,
    ) -> ProviderIndexResult:
        raise AssertionError("disabled provider must not index repos")

    async def query_spans(
        self,
        repo: RepoIdentity,
        spans: Sequence[CodeSpanRef],
        *,
        budget: ContextLayerBudget,
    ) -> list[ProviderLineageRecord]:
        self.queried = True
        raise AssertionError("disabled provider must not query spans")


class SlowOptionalProvider:
    name = "git_ai"

    async def available(self) -> ProviderAvailability:
        import asyncio

        await asyncio.sleep(0.05)
        raise AssertionError("availability timeout should fire first")

    async def index_repo(
        self,
        repo: RepoIdentity,
        *,
        budget: ContextLayerBudget,
    ) -> ProviderIndexResult:
        raise AssertionError("timed-out provider must not index repos")

    async def query_spans(
        self,
        repo: RepoIdentity,
        spans: Sequence[CodeSpanRef],
        *,
        budget: ContextLayerBudget,
    ) -> list[ProviderLineageRecord]:
        raise AssertionError("timed-out provider must not query spans")


def _repo() -> RepoIdentity:
    return RepoIdentity(
        repo_id="repo",
        repo_name="repo",
        canonical_path="/tmp/repo",
        workspace_relative_path="repo",
        safety_status="ok",
    )


def _span(path: str = "src/app.py", start: int = 1, end: int = 1) -> CodeSpanRef:
    return CodeSpanRef(repo_id="repo", path=path, start_line=start, end_line=end)


def _snapshot() -> ContextEvidenceSnapshot:
    return ContextEvidenceSnapshot(
        source_dag_artifact_id=21,
        dag_sha256="dag-digest",
        typed_journal_high_watermark=200,
        typed_evidence_digest="typed-evidence",
    )


def _request(**overrides: object) -> ContextLayerRequest:
    snapshot = _snapshot()
    values = {
        "feature_id": "feature-21",
        "source_dag_artifact_id": snapshot.source_dag_artifact_id,
        "dag_sha256": snapshot.dag_sha256,
        "evidence_snapshot": snapshot,
        "task_id": "task-1",
        "group_idx": 3,
        "repo_ids": ["repo"],
        "spans": [_span()],
    }
    values.update(overrides)
    return ContextLayerRequest(**values)


def _record(record_id: str, content_digest: str = "content") -> ProviderLineageRecord:
    span = _span()
    return ProviderLineageRecord(
        record_id=record_id,
        provider="native_git",
        repo_id="repo",
        path=span.path,
        start_line=span.start_line,
        end_line=span.end_line,
        code_span=span,
        commit_hashes=["a" * 40],
        provider_refs=["git-commit:" + "a" * 40],
        provider_state_digest="fake-state",
        content_digest=content_digest,
        confidence=0.9,
        warnings=[],
    )


def _evidence_ref() -> GovernanceEvidenceRef:
    return GovernanceEvidenceRef(
        authority="typed_journal",
        ref_id="typed-row:123",
        feature_id="feature-21",
        slice_id="21-iriai-context-layer",
        artifact_id=None,
        event_id=123,
        commit_hash=None,
        journal_anchor=None,
        created_at=None,
        digest="typed-ref-digest",
        quality="canonical",
        completeness="complete",
        page_refs=[],
        preview_only=False,
    )


@pytest.mark.asyncio
async def test_missing_optional_provider_does_not_fail_package_generation() -> None:
    native = FakeProvider([_record("record-1")])
    optional = DisabledOptionalProvider()
    service = ContextLayerService([optional, native], repos=[_repo()])

    package = await service.build_context_package(_request())

    assert package.completeness == "complete"
    assert package.advisory_only is True
    assert package.provider_order == ["git_ai", "native_git"]
    assert any(ref.provider == "git_ai" and ref.status == "disabled" for ref in package.provider_state_refs)
    assert native.queried is True
    assert optional.queried is False


@pytest.mark.asyncio
async def test_optional_provider_timeout_is_bounded_and_non_blocking() -> None:
    native = FakeProvider([_record("record-1")])
    service = ContextLayerService([SlowOptionalProvider(), native], repos=[_repo()])

    package = await service.build_context_package(
        _request(budget=ContextLayerBudget(timeout_ms=1))
    )

    assert package.completeness == "complete"
    assert any(
        ref.provider == "git_ai" and ref.status == "timed_out"
        for ref in package.provider_state_refs
    )
    assert native.queried is True


@pytest.mark.asyncio
async def test_context_package_pages_over_budget_records_with_exact_refs(tmp_path) -> None:
    page_store = ContextPageStore(root=tmp_path / "context-pages")
    service = ContextLayerService(
        [FakeProvider([_record("record-1"), _record("record-2")])],
        repos=[_repo()],
        page_store=page_store,
    )
    request = _request(budget=ContextLayerBudget(max_provider_records=1))

    package = await service.build_context_package(request)

    assert package.completeness == "paged"
    assert len(package.provider_records) == 1
    assert package.provider_records[0].record_id == "record-1"
    assert package.omitted_counts["provider_records"] == 1
    assert len(package.page_refs) == 1
    page = service.get_exact_page(package.package_id, package.page_refs[0].ref_id)
    assert page is not None
    assert page.records[0]["record_id"] == "record-2"
    assert page.page_ref.digest == page.content_digest
    assert page.content_digest[:16] in package.page_refs[0].ref_id


@pytest.mark.asyncio
async def test_exact_page_refs_can_be_rehydrated_after_restart(tmp_path) -> None:
    provider_records = [_record("record-1"), _record("record-2")]
    page_root = tmp_path / "context-pages"
    first_service = ContextLayerService(
        [FakeProvider(provider_records)],
        repos=[_repo()],
        page_store=ContextPageStore(root=page_root),
    )
    second_service = ContextLayerService(
        [FakeProvider(provider_records)],
        repos=[_repo()],
        page_store=ContextPageStore(root=page_root),
    )
    request = _request(budget=ContextLayerBudget(max_provider_records=1))

    first_package = await first_service.build_context_package(request)
    second_package = await second_service.build_context_package(request)
    first_page = first_service.get_exact_page(
        first_package.package_id,
        first_package.page_refs[0].ref_id,
    )
    assert first_page is not None
    restarted_store = ContextPageStore(root=page_root)
    restarted_service = ContextLayerService(
        [FakeProvider([])],
        repos=[_repo()],
        page_store=restarted_store,
    )

    restarted_page = restarted_service.get_exact_page(
        first_package.package_id,
        first_package.page_refs[0].ref_id,
    )

    assert first_package.package_id == second_package.package_id
    assert first_package.page_refs[0].ref_id == second_package.page_refs[0].ref_id
    assert first_package.page_refs[0].digest == second_package.page_refs[0].digest
    assert restarted_page == first_page


@pytest.mark.asyncio
async def test_require_complete_fails_closed_when_page_refs_are_process_local() -> None:
    service = ContextLayerService(
        [FakeProvider([_record("record-1"), _record("record-2")])],
        repos=[_repo()],
    )
    request = _request(budget=ContextLayerBudget(max_provider_records=1))

    package = await service.build_context_package(request)

    assert package.completeness == "unavailable"
    assert package.incomplete_reason == "exact_page_refs_unavailable"
    assert package.page_refs == []
    assert service.get_exact_page(package.package_id, "missing") is None


@pytest.mark.asyncio
async def test_require_complete_fails_closed_when_page_refs_cannot_be_produced() -> None:
    service = ContextLayerService(
        [FakeProvider([_record("record-1"), _record("record-2")])],
        repos=[_repo()],
        allow_paging=False,
    )
    request = _request(budget=ContextLayerBudget(max_provider_records=1))

    package = await service.build_context_package(request)

    assert package.completeness == "unavailable"
    assert package.incomplete_reason == "exact_page_refs_unavailable"
    assert package.provider_records == []
    assert package.page_refs == []


@pytest.mark.asyncio
async def test_oversized_span_fails_closed_without_exact_page_content(tmp_path) -> None:
    page_store = ContextPageStore(root=tmp_path / "context-pages")
    service = ContextLayerService(
        [FakeProvider([])],
        repos=[_repo()],
        page_store=page_store,
    )
    request = _request(
        budget=ContextLayerBudget(max_lines_per_span=1),
        spans=[_span(start=1, end=2)],
    )

    package = await service.build_context_package(request)

    assert package.completeness == "unavailable"
    assert package.incomplete_reason == "span_exceeds_max_lines_per_span"
    assert package.provider_records == []
    assert package.omitted_counts["oversized_spans"] == 1
    assert package.page_refs == []


@pytest.mark.asyncio
async def test_oversized_span_is_preview_only_when_complete_context_not_required(tmp_path) -> None:
    service = ContextLayerService(
        [FakeProvider([])],
        repos=[_repo()],
        page_store=ContextPageStore(root=tmp_path / "context-pages"),
    )
    request = _request(
        budget=ContextLayerBudget(max_lines_per_span=1),
        spans=[_span(start=1, end=2)],
        require_complete=False,
    )

    package = await service.build_context_package(request)

    assert package.completeness == "preview_only"
    assert package.provider_records == []
    assert package.omitted_counts["oversized_spans"] == 1
    assert package.page_refs == []


@pytest.mark.asyncio
async def test_lineage_plugin_records_conflict_and_keeps_typed_lineage_authoritative() -> None:
    provider_record = _record("record-1", content_digest="provider-content")
    source = IriAILineageSourceRecord(
        feature_id="feature-21",
        group_idx=3,
        effective_group_idx=4,
        code_span=provider_record.code_span,
        commit_hashes=provider_record.commit_hashes,
        task_ids=["task-1"],
        artifact_ids=[10],
        verify_evidence_ids=[20],
        rca_evidence_ids=[30],
        repair_evidence_ids=[40],
        checkpoint_artifact_ids=[50],
        commit_proof_evidence_ids=[60],
        governance_finding_ids=["finding-1"],
        evidence_refs=[_evidence_ref()],
        content_digest="typed-content",
        expected_provider_content_digest="different-provider-content",
    )
    service = ContextLayerService(
        [FakeProvider([provider_record])],
        repos=[_repo()],
        lineage_plugin=IriAILineagePlugin([source]),
    )

    package = await service.build_context_package(_request())

    assert package.completeness == "complete"
    assert package.iriai_lineage[0].task_ids == ["task-1"]
    assert package.iriai_lineage[0].commit_proof_evidence_ids == [60]
    assert package.iriai_lineage[0].evidence_refs == [_evidence_ref()]
    assert "evidence_refs=typed-row:123" in (package.rendered_preview or "")
    assert any(
        gap.startswith("governance_evidence_conflict:")
        for gap in package.iriai_lineage[0].gaps
    )
    assert package.iriai_lineage[0].linkage_confidence <= 0.5


@pytest.mark.asyncio
async def test_lineage_mapping_filters_by_request_task_and_group_scope() -> None:
    provider_record = _record("record-1", content_digest="provider-content")
    matching_source = IriAILineageSourceRecord(
        feature_id="feature-21",
        group_idx=3,
        effective_group_idx=None,
        code_span=provider_record.code_span,
        commit_hashes=provider_record.commit_hashes,
        task_ids=["task-1"],
        commit_proof_evidence_ids=[60],
        content_digest="typed-content-match",
    )
    wrong_task = matching_source.model_copy(
        update={
            "task_ids": ["task-other"],
            "commit_proof_evidence_ids": [61],
            "content_digest": "typed-content-wrong-task",
        }
    )
    wrong_group = matching_source.model_copy(
        update={
            "group_idx": 4,
            "commit_proof_evidence_ids": [62],
            "content_digest": "typed-content-wrong-group",
        }
    )
    service = ContextLayerService(
        [FakeProvider([provider_record])],
        repos=[_repo()],
        lineage_plugin=IriAILineagePlugin([wrong_task, wrong_group, matching_source]),
    )

    package = await service.build_context_package(_request(task_id="task-1", group_idx=3))

    assert package.completeness == "complete"
    assert len(package.iriai_lineage) == 1
    assert package.iriai_lineage[0].task_ids == ["task-1"]
    assert package.iriai_lineage[0].group_idx == 3
    assert package.iriai_lineage[0].commit_proof_evidence_ids == [60]


@pytest.mark.asyncio
async def test_package_digest_changes_when_reused_record_id_content_changes() -> None:
    service_a = ContextLayerService([FakeProvider([_record("record-1", "content-a")])], repos=[_repo()])
    service_b = ContextLayerService([FakeProvider([_record("record-1", "content-b")])], repos=[_repo()])
    request = _request()

    package_a = await service_a.build_context_package(request)
    package_b = await service_b.build_context_package(request)

    assert package_a.provider_records[0].record_id == package_b.provider_records[0].record_id
    assert package_a.package_digest != package_b.package_digest
