"""Context package builder and IriAI lineage reconciliation for Slice 21."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iriai_build_v2.workflows.develop.execution.workspace_authority import RepoIdentity
from iriai_build_v2.workflows.develop.governance.models import GovernanceEvidenceRef

from .models import (
    CodeSpanRef,
    ContextCompleteness,
    ContextLayerBudget,
    ContextLayerPackage,
    ContextLayerRequest,
    ContextPageContent,
    ContextProviderName,
    IriAILineageRecord,
    ProviderAvailability,
    ProviderLineageRecord,
    ProviderStateRef,
    compute_context_package_digest,
    compute_provider_state_digest,
    digest_payload,
    provisional_context_package_digest,
)
from .providers import ProvenanceProvider


_REQUEST_STATE_REPO_ID = "__request__"


class IriAILineageSourceRecord(BaseModel):
    """Typed source row the lineage plugin uses to reconcile provider output."""

    model_config = ConfigDict(extra="forbid")

    feature_id: str
    group_idx: int | None = None
    effective_group_idx: int | None = None
    code_span: CodeSpanRef
    commit_hashes: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[int] = Field(default_factory=list)
    verify_evidence_ids: list[int] = Field(default_factory=list)
    rca_evidence_ids: list[int] = Field(default_factory=list)
    repair_evidence_ids: list[int] = Field(default_factory=list)
    checkpoint_artifact_ids: list[int] = Field(default_factory=list)
    commit_proof_evidence_ids: list[int] = Field(default_factory=list)
    governance_finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    content_digest: str
    linkage_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    expected_provider_content_digest: str | None = None
    gaps: list[str] = Field(default_factory=list)

    @field_validator("feature_id", "content_digest")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must be non-empty")
        return value


class ContextPageStore:
    """Exact page registry for packages built by the context layer."""

    def __init__(
        self,
        pages: Sequence[ContextPageContent] = (),
        *,
        root: Path | str | None = None,
    ) -> None:
        self._pages: dict[tuple[str, str], ContextPageContent] = {}
        self._root = Path(root) if root is not None else None
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)
        for page in pages:
            self._remember(page)

    @property
    def durable(self) -> bool:
        return self._root is not None

    def _remember(self, page: ContextPageContent) -> None:
        self._pages[(page.package_id, page.page_ref.ref_id)] = page
        if self._root is not None:
            self._page_path(page.package_id, page.page_ref.ref_id).write_text(
                page.model_dump_json(),
                encoding="utf-8",
            )

    def get(self, package_id: str, page_ref_id: str) -> ContextPageContent | None:
        cached = self._pages.get((package_id, page_ref_id))
        if cached is not None:
            return cached
        if self._root is None:
            return None
        path = self._page_path(package_id, page_ref_id)
        if not path.exists():
            return None
        page = ContextPageContent.model_validate_json(path.read_text(encoding="utf-8"))
        if page.package_id != package_id or page.page_ref.ref_id != page_ref_id:
            raise ValueError("persisted context page identity does not match request")
        self._pages[(package_id, page_ref_id)] = page
        return page

    def _page_path(self, package_id: str, page_ref_id: str) -> Path:
        if self._root is None:
            raise RuntimeError("context page store has no durable root")
        filename = digest_payload(
            {"package_id": package_id, "page_ref_id": page_ref_id}
        )
        return self._root / f"{filename}.json"


class IriAILineagePlugin:
    """Map provider code-span records onto typed IriAI workflow evidence."""

    def __init__(self, source_records: Sequence[IriAILineageSourceRecord] = ()) -> None:
        self._source_records = list(source_records)

    async def map_provider_records(
        self,
        request: ContextLayerRequest,
        records: Sequence[ProviderLineageRecord],
        *,
        evidence_snapshot: object,
        provider_state_refs: Sequence[ProviderStateRef],
        budget: ContextLayerBudget,
    ) -> list[IriAILineageRecord]:
        del evidence_snapshot, provider_state_refs, budget
        mapped: list[IriAILineageRecord] = []
        for record in records:
            matches = self._matching_sources(request, record)
            if not matches:
                mapped.append(_gap_lineage_record(request, record))
                continue
            merged = _merge_source_records(request, record, matches)
            mapped.append(merged)
        return mapped

    def _matching_sources(
        self,
        request: ContextLayerRequest,
        record: ProviderLineageRecord,
    ) -> list[IriAILineageSourceRecord]:
        matches: list[IriAILineageSourceRecord] = []
        record_commits = set(record.commit_hashes)
        for source in self._source_records:
            if source.feature_id != request.feature_id:
                continue
            if request.group_idx is not None and request.group_idx not in (
                source.group_idx,
                source.effective_group_idx,
            ):
                continue
            if request.task_id is not None and request.task_id not in source.task_ids:
                continue
            if source.code_span.repo_id != record.repo_id:
                continue
            if source.code_span.path != record.path:
                continue
            if not _spans_overlap(source.code_span, record.code_span):
                continue
            source_commits = set(source.commit_hashes)
            if record_commits and source_commits and not (record_commits & source_commits):
                continue
            matches.append(source)
        return matches


class ContextLayerService:
    """Build advisory, exact-or-paged context packages from providers."""

    def __init__(
        self,
        providers: Sequence[ProvenanceProvider],
        *,
        repos: Sequence[RepoIdentity] = (),
        lineage_plugin: IriAILineagePlugin | None = None,
        page_store: ContextPageStore | None = None,
        allow_paging: bool = True,
    ) -> None:
        if not providers:
            raise ValueError("ContextLayerService requires at least one provider")
        provider_names = [provider.name for provider in providers]
        if len(set(provider_names)) != len(provider_names):
            raise ValueError("context providers must have unique names")
        self._providers = list(providers)
        self._repos = {repo.repo_id: repo for repo in repos}
        self._lineage_plugin = lineage_plugin or IriAILineagePlugin()
        self._page_store = page_store or ContextPageStore()
        self._allow_paging = allow_paging

    async def build_context_package(self, request: ContextLayerRequest) -> ContextLayerPackage:
        provider_order = [provider.name for provider in self._providers]
        repo_ids = _requested_repo_ids(request)
        availability_by_provider: dict[ContextProviderName, ProviderAvailability] = {}
        provider_state_refs: list[ProviderStateRef] = []
        provider_records: list[ProviderLineageRecord] = []
        blockers: list[str] = []
        omitted_counts: dict[str, int] = {}
        oversized_spans = [
            span
            for span in request.spans
            if span.line_count > request.budget.max_lines_per_span
        ]
        if oversized_spans:
            omitted_counts["oversized_spans"] = len(oversized_spans)
            blockers.append("span_exceeds_max_lines_per_span")

        for provider in self._providers:
            availability = await _provider_availability(provider, request.budget)
            availability_by_provider[provider.name] = availability
            if availability.status != "available":
                provider_state_refs.extend(
                    _state_refs_for_unavailable_provider(availability, repo_ids)
                )
                if provider.name == "native_git" and request.spans:
                    blockers.append(f"provider_unavailable:{provider.name}")
                continue

            for repo_id in repo_ids:
                repo = self._repo_for_provider(provider, repo_id)
                if repo is None:
                    provider_state_refs.append(
                        _unavailable_repo_state_ref(provider.name, repo_id)
                    )
                    if provider.name == "native_git":
                        blockers.append(f"repo_unavailable:{repo_id}")
                    continue
                try:
                    index = await asyncio.wait_for(
                        provider.index_repo(repo, budget=request.budget),
                        timeout=request.budget.timeout_ms / 1000,
                    )
                except TimeoutError:
                    provider_state_refs.append(_timeout_state_ref(provider.name, repo_id))
                    if provider.name == "native_git":
                        blockers.append(f"provider_index_timed_out:{provider.name}")
                    continue
                except Exception as exc:  # provider errors are advisory metadata
                    provider_state_refs.append(
                        _error_state_ref(provider.name, repo_id, str(exc))
                    )
                    if provider.name == "native_git":
                        blockers.append(f"provider_index_error:{provider.name}")
                    continue
                if index.state_ref is not None:
                    provider_state_refs.append(index.state_ref)
                for key, count in index.omitted_counts.items():
                    omitted_counts[key] = omitted_counts.get(key, 0) + count
                repo_spans = [
                    span
                    for span in request.spans
                    if span.repo_id == repo_id
                    and span.line_count <= request.budget.max_lines_per_span
                ]
                if not repo_spans:
                    continue
                try:
                    provider_records.extend(
                        await asyncio.wait_for(
                            provider.query_spans(
                                repo, repo_spans, budget=request.budget
                            ),
                            timeout=request.budget.timeout_ms / 1000,
                        )
                    )
                except TimeoutError:
                    provider_state_refs.append(_timeout_state_ref(provider.name, repo_id))
                    if provider.name == "native_git":
                        blockers.append(f"provider_query_timed_out:{provider.name}")
                except Exception as exc:
                    provider_state_refs.append(
                        _error_state_ref(provider.name, repo_id, str(exc))
                    )
                    if provider.name == "native_git":
                        blockers.append(f"provider_query_error:{provider.name}")

        if not provider_state_refs:
            provider_state_refs = [
                _availability_state_ref(
                    availability,
                    _REQUEST_STATE_REPO_ID,
                )
                for availability in availability_by_provider.values()
            ]
        provider_state_digest = compute_provider_state_digest(provider_state_refs)
        iriai_lineage = await self._lineage_plugin.map_provider_records(
            request,
            provider_records,
            evidence_snapshot=request.evidence_snapshot,
            provider_state_refs=provider_state_refs,
            budget=request.budget,
        )
        if _has_unpaged_provider_payload_gap(provider_records):
            blockers.append("provider_payload_budget_exhausted")

        capped_records, record_pages, record_counts = self._cap_provider_records(
            provider_records,
            request.budget,
        )
        capped_lineage, lineage_pages, lineage_counts = self._cap_lineage_records(
            iriai_lineage,
            request.budget,
        )
        for key, count in {**record_counts, **lineage_counts}.items():
            omitted_counts[key] = omitted_counts.get(key, 0) + count

        rendered_preview = _render_preview(
            capped_records,
            capped_lineage,
            max_chars=request.budget.max_rendered_preview_chars,
        )
        if rendered_preview is None and (capped_records or capped_lineage):
            omitted_counts["rendered_preview_chars"] = (
                omitted_counts.get("rendered_preview_chars", 0) + 1
            )

        page_specs = [*record_pages, *lineage_pages]
        effective_allow_paging = self._allow_paging and (
            not page_specs or self._page_store.durable
        )
        omitted_without_pages = bool(
            oversized_spans
            or (record_counts and not record_pages)
            or (lineage_counts and not lineage_pages)
        )

        completeness, incomplete_reason = _select_completeness(
            request,
            blockers,
            page_count=len(page_specs),
            omitted_without_pages=omitted_without_pages,
            allow_paging=effective_allow_paging,
        )
        if completeness == "unavailable":
            page_specs = []
            capped_records = []
            capped_lineage = []
            rendered_preview = None

        provisional_digest = provisional_context_package_digest(
            request=request,
            evidence_snapshot=request.evidence_snapshot,
            provider_state_digest=provider_state_digest,
            provider_order=provider_order,
            all_provider_records=provider_records,
            all_iriai_lineage=iriai_lineage,
            omitted_counts=omitted_counts,
            rendered_preview=rendered_preview,
            completeness=completeness,
            incomplete_reason=incomplete_reason,
        )
        package_id = f"ctxpkg-{provisional_digest[:16]}"
        page_refs, pages = (
            _materialize_pages(package_id, page_specs)
            if effective_allow_paging and completeness != "unavailable"
            else ([], [])
        )
        if len(page_refs) > request.budget.max_omitted_refs:
            if request.require_complete:
                completeness = "unavailable"
                incomplete_reason = "max_omitted_refs_exceeded"
                page_refs = []
                pages = []
            else:
                overflow = len(page_refs) - request.budget.max_omitted_refs
                page_refs = page_refs[: request.budget.max_omitted_refs]
                pages = pages[: request.budget.max_omitted_refs]
                omitted_counts["omitted_refs"] = omitted_counts.get("omitted_refs", 0) + overflow

        package_digest = compute_context_package_digest(
            request=request,
            evidence_snapshot=request.evidence_snapshot,
            provider_state_digest=provider_state_digest,
            provider_order=provider_order,
            provider_records=capped_records,
            iriai_lineage=capped_lineage,
            page_refs=page_refs,
            omitted_counts=omitted_counts,
            completeness=completeness,
            rendered_preview=rendered_preview,
            incomplete_reason=incomplete_reason,
        )
        package = ContextLayerPackage(
            package_id=package_id,
            package_digest=package_digest,
            generated_at=datetime.now(timezone.utc),
            package_kind=_package_kind(completeness),
            completeness=completeness,
            request=request,
            source_dag_artifact_id=request.source_dag_artifact_id,
            dag_sha256=request.dag_sha256,
            evidence_snapshot=request.evidence_snapshot,
            provider_state_refs=provider_state_refs,
            provider_state_digest=provider_state_digest,
            provider_order=provider_order,
            provider_records=capped_records,
            iriai_lineage=capped_lineage,
            rendered_preview=rendered_preview,
            page_refs=page_refs,
            omitted_refs=page_refs,
            omitted_counts=omitted_counts,
            incomplete_reason=incomplete_reason,
            advisory_only=True,
        )
        for page in pages:
            self._page_store._remember(page)
        return package

    def get_exact_page(self, package_id: str, page_ref_id: str) -> ContextPageContent | None:
        return self._page_store.get(package_id, page_ref_id)

    def _repo_for_provider(
        self,
        provider: ProvenanceProvider,
        repo_id: str,
    ) -> RepoIdentity | None:
        if repo_id in self._repos:
            return self._repos[repo_id]
        repo_identity = getattr(provider, "repo_identity", None)
        if callable(repo_identity):
            return repo_identity(repo_id)
        return None

    def _cap_provider_records(
        self,
        records: Sequence[ProviderLineageRecord],
        budget: ContextLayerBudget,
    ) -> tuple[list[ProviderLineageRecord], list[tuple[str, list[dict[str, Any]]]], dict[str, int]]:
        capped: list[ProviderLineageRecord] = []
        pages: list[tuple[str, list[dict[str, Any]]]] = []
        counts: dict[str, int] = {}
        inline_records = list(records[: budget.max_provider_records])
        omitted_records = list(records[budget.max_provider_records :])
        if omitted_records:
            counts["provider_records"] = len(omitted_records)
            if self._allow_paging:
                pages.append(
                    (
                        "provider_records",
                        [record.model_dump(mode="json") for record in omitted_records],
                    )
                )
        for record in inline_records:
            full_record = record
            refs = record.provider_refs
            warnings = record.warnings
            needs_detail_page = False
            if len(refs) > budget.max_provider_refs_per_record:
                counts["provider_refs"] = counts.get("provider_refs", 0) + (
                    len(refs) - budget.max_provider_refs_per_record
                )
                refs = refs[: budget.max_provider_refs_per_record]
                needs_detail_page = True
            if len(warnings) > budget.max_provider_warnings_per_record:
                counts["provider_warnings"] = counts.get("provider_warnings", 0) + (
                    len(warnings) - budget.max_provider_warnings_per_record
                )
                warnings = warnings[: budget.max_provider_warnings_per_record]
                needs_detail_page = True
            if needs_detail_page and self._allow_paging:
                pages.append(("provider_record_details", [full_record.model_dump(mode="json")]))
            capped.append(record.model_copy(update={"provider_refs": refs, "warnings": warnings}))
        return capped, pages, counts

    def _cap_lineage_records(
        self,
        records: Sequence[IriAILineageRecord],
        budget: ContextLayerBudget,
    ) -> tuple[list[IriAILineageRecord], list[tuple[str, list[dict[str, Any]]]], dict[str, int]]:
        capped: list[IriAILineageRecord] = []
        pages: list[tuple[str, list[dict[str, Any]]]] = []
        counts: dict[str, int] = {}
        inline_records = list(records[: budget.max_lineage_records])
        omitted_records = list(records[budget.max_lineage_records :])
        if omitted_records:
            counts["iriai_lineage"] = len(omitted_records)
            if self._allow_paging:
                pages.append(
                    (
                        "iriai_lineage",
                        [record.model_dump(mode="json") for record in omitted_records],
                    )
                )
        for record in inline_records:
            updates: dict[str, Any] = {}
            for field_name, cap, count_key in (
                ("task_ids", budget.max_task_ids_per_lineage, "task_ids"),
                ("artifact_ids", budget.max_artifact_ids_per_lineage, "artifact_ids"),
                (
                    "verify_evidence_ids",
                    budget.max_evidence_refs_per_lineage,
                    "verify_evidence_ids",
                ),
                (
                    "rca_evidence_ids",
                    budget.max_evidence_refs_per_lineage,
                    "rca_evidence_ids",
                ),
                (
                    "repair_evidence_ids",
                    budget.max_evidence_refs_per_lineage,
                    "repair_evidence_ids",
                ),
                (
                    "checkpoint_artifact_ids",
                    budget.max_evidence_refs_per_lineage,
                    "checkpoint_artifact_ids",
                ),
                (
                    "commit_proof_evidence_ids",
                    budget.max_evidence_refs_per_lineage,
                    "commit_proof_evidence_ids",
                ),
                (
                    "governance_finding_ids",
                    budget.max_governance_findings_per_lineage,
                    "governance_finding_ids",
                ),
                ("evidence_refs", budget.max_evidence_refs_per_lineage, "evidence_refs"),
            ):
                value = getattr(record, field_name)
                if len(value) > cap:
                    counts[count_key] = counts.get(count_key, 0) + (len(value) - cap)
                    updates[field_name] = value[:cap]
            if updates and self._allow_paging:
                pages.append(("iriai_lineage", [record.model_dump(mode="json")]))
            capped.append(record.model_copy(update=updates) if updates else record)
        return capped, pages, counts


def _requested_repo_ids(request: ContextLayerRequest) -> list[str]:
    ordered = [*request.repo_ids, *(span.repo_id for span in request.spans)]
    seen: set[str] = set()
    repo_ids: list[str] = []
    for repo_id in ordered:
        if repo_id in seen:
            continue
        seen.add(repo_id)
        repo_ids.append(repo_id)
    return repo_ids or [_REQUEST_STATE_REPO_ID]


async def _provider_availability(
    provider: ProvenanceProvider,
    budget: ContextLayerBudget,
) -> ProviderAvailability:
    try:
        return await asyncio.wait_for(
            provider.available(),
            timeout=budget.timeout_ms / 1000,
        )
    except TimeoutError:
        return ProviderAvailability(
            provider=provider.name,
            status="timed_out",
            checked_at=datetime.now(timezone.utc),
            timeout_ms=budget.timeout_ms,
            message="provider availability timed out",
        )
    except Exception as exc:
        return ProviderAvailability(
            provider=provider.name,
            status="error",
            checked_at=datetime.now(timezone.utc),
            timeout_ms=budget.timeout_ms,
            message=str(exc),
        )


def _state_refs_for_unavailable_provider(
    availability: ProviderAvailability,
    repo_ids: Sequence[str],
) -> list[ProviderStateRef]:
    return [_availability_state_ref(availability, repo_id) for repo_id in repo_ids]


def _availability_state_ref(
    availability: ProviderAvailability,
    repo_id: str,
) -> ProviderStateRef:
    return ProviderStateRef(
        provider=availability.provider,
        repo_id=repo_id,
        ref=availability.status,
        state_digest=availability.state_digest
        or digest_payload(
            {
                "provider": availability.provider,
                "repo_id": repo_id,
                "status": availability.status,
                "message": availability.message,
            }
        ),
        indexed_at=availability.checked_at,
        status=availability.status,
    )


def _unavailable_repo_state_ref(
    provider: ContextProviderName,
    repo_id: str,
) -> ProviderStateRef:
    return ProviderStateRef(
        provider=provider,
        repo_id=repo_id,
        ref="repo-unavailable",
        state_digest=digest_payload(
            {"provider": provider, "repo_id": repo_id, "status": "unavailable"}
        ),
        indexed_at=datetime.now(timezone.utc),
        status="unavailable",
    )


def _error_state_ref(
    provider: ContextProviderName,
    repo_id: str,
    message: str,
) -> ProviderStateRef:
    return ProviderStateRef(
        provider=provider,
        repo_id=repo_id,
        ref="provider-error",
        state_digest=digest_payload(
            {
                "provider": provider,
                "repo_id": repo_id,
                "status": "error",
                "message": message,
            }
        ),
        indexed_at=datetime.now(timezone.utc),
        status="error",
    )


def _timeout_state_ref(
    provider: ContextProviderName,
    repo_id: str,
) -> ProviderStateRef:
    return ProviderStateRef(
        provider=provider,
        repo_id=repo_id,
        ref="provider-timed-out",
        state_digest=digest_payload(
            {"provider": provider, "repo_id": repo_id, "status": "timed_out"}
        ),
        indexed_at=datetime.now(timezone.utc),
        status="timed_out",
    )


def _select_completeness(
    request: ContextLayerRequest,
    blockers: Sequence[str],
    *,
    page_count: int,
    omitted_without_pages: bool,
    allow_paging: bool,
) -> tuple[ContextCompleteness, str | None]:
    if blockers and request.require_complete:
        return "unavailable", ";".join(blockers)
    if blockers:
        return "preview_only", None
    if omitted_without_pages and request.require_complete:
        return "unavailable", "exact_page_refs_unavailable"
    if omitted_without_pages:
        return "preview_only", None
    if (page_count or omitted_without_pages) and not allow_paging and request.require_complete:
        return "unavailable", "exact_page_refs_unavailable"
    if (page_count or omitted_without_pages) and not allow_paging:
        return "preview_only", None
    if page_count and allow_paging:
        return "paged", None
    return "complete", None


def _package_kind(completeness: ContextCompleteness) -> Literal["manifest", "exact", "preview"]:
    if completeness == "complete":
        return "exact"
    if completeness == "preview_only":
        return "preview"
    return "manifest"


def _render_preview(
    provider_records: Sequence[ProviderLineageRecord],
    lineage_records: Sequence[IriAILineageRecord],
    *,
    max_chars: int,
) -> str | None:
    lines: list[str] = []
    for record in provider_records:
        commits = ",".join(record.commit_hashes[:3]) or "none"
        lines.append(
            f"{record.path}:{record.start_line}-{record.end_line} "
            f"provider={record.provider} commits={commits}"
        )
    for record in lineage_records:
        tasks = ",".join(record.task_ids[:3]) or "none"
        gaps = ",".join(record.gaps[:2]) or "none"
        evidence_refs = ",".join(ref.ref_id for ref in record.evidence_refs[:3]) or "none"
        lines.append(
            f"{record.code_span.path}:{record.code_span.start_line}-"
            f"{record.code_span.end_line} tasks={tasks} evidence_refs={evidence_refs} "
            f"gaps={gaps}"
        )
    preview = "\n".join(lines)
    if not preview:
        return None
    if len(preview) > max_chars:
        return None
    return preview


def _materialize_pages(
    package_id: str,
    page_specs: Sequence[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[GovernanceEvidenceRef], list[ContextPageContent]]:
    refs: list[GovernanceEvidenceRef] = []
    pages: list[ContextPageContent] = []
    for index, (page_kind, records) in enumerate(page_specs):
        content_digest = digest_payload(
            {"package_id": package_id, "page_kind": page_kind, "records": records}
        )
        ref = GovernanceEvidenceRef(
            authority="git_provenance",
            ref_id=(
                f"review:context-package:{package_id}:"
                f"page:{index}:{content_digest[:16]}"
            ),
            feature_id=None,
            slice_id="21-iriai-context-layer",
            artifact_id=None,
            event_id=None,
            commit_hash=None,
            journal_anchor=None,
            created_at=None,
            digest=content_digest,
            quality="derived",
            completeness="complete",
            page_refs=[],
            preview_only=False,
        )
        refs.append(ref)
        pages.append(
            ContextPageContent(
                package_id=package_id,
                page_ref=ref,
                page_kind=page_kind,  # type: ignore[arg-type]
                records=records,
                content_digest=content_digest,
            )
        )
    return refs, pages


def _has_unpaged_provider_payload_gap(
    records: Sequence[ProviderLineageRecord],
) -> bool:
    return any(
        warning.startswith("provider_payload_budget_exhausted:")
        for record in records
        for warning in record.warnings
    )


def _merge_source_records(
    request: ContextLayerRequest,
    provider_record: ProviderLineageRecord,
    matches: Sequence[IriAILineageSourceRecord],
) -> IriAILineageRecord:
    first = matches[0]
    gaps: list[str] = []
    for match in matches:
        gaps.extend(match.gaps)
        if (
            match.expected_provider_content_digest is not None
            and match.expected_provider_content_digest != provider_record.content_digest
        ):
            gaps.append(f"governance_evidence_conflict:{provider_record.record_id}")
    commit_hashes = _dedupe([*provider_record.commit_hashes, *flatten(m.commit_hashes for m in matches)])
    content_digest = digest_payload(
        {
            "feature_id": request.feature_id,
            "provider_record_id": provider_record.record_id,
            "typed_content_digests": [match.content_digest for match in matches],
            "commit_hashes": commit_hashes,
            "gaps": gaps,
        }
    )
    confidence = min(
        [provider_record.confidence, *[match.linkage_confidence for match in matches]]
    )
    if any(gap.startswith("governance_evidence_conflict:") for gap in gaps):
        confidence = min(confidence, 0.5)
    return IriAILineageRecord(
        lineage_record_id=f"iriai-lineage:{digest_payload({'record': provider_record.record_id, 'typed': [m.content_digest for m in matches]})[:16]}",
        feature_id=request.feature_id,
        group_idx=first.group_idx,
        effective_group_idx=first.effective_group_idx,
        code_span=provider_record.code_span,
        provider_record_ids=[provider_record.record_id],
        commit_hashes=commit_hashes,
        task_ids=_dedupe(flatten(match.task_ids for match in matches)),
        artifact_ids=_dedupe_int(flatten(match.artifact_ids for match in matches)),
        verify_evidence_ids=_dedupe_int(flatten(match.verify_evidence_ids for match in matches)),
        rca_evidence_ids=_dedupe_int(flatten(match.rca_evidence_ids for match in matches)),
        repair_evidence_ids=_dedupe_int(flatten(match.repair_evidence_ids for match in matches)),
        checkpoint_artifact_ids=_dedupe_int(
            flatten(match.checkpoint_artifact_ids for match in matches)
        ),
        commit_proof_evidence_ids=_dedupe_int(
            flatten(match.commit_proof_evidence_ids for match in matches)
        ),
        governance_finding_ids=_dedupe(
            flatten(match.governance_finding_ids for match in matches)
        ),
        evidence_refs=_dedupe_evidence_refs(
            flatten(match.evidence_refs for match in matches)
        ),
        content_digest=content_digest,
        linkage_confidence=confidence,
        gaps=_dedupe(gaps),
    )


def _gap_lineage_record(
    request: ContextLayerRequest,
    provider_record: ProviderLineageRecord,
) -> IriAILineageRecord:
    content_digest = digest_payload(
        {
            "feature_id": request.feature_id,
            "provider_record_id": provider_record.record_id,
            "gaps": ["line_provenance_gap"],
        }
    )
    return IriAILineageRecord(
        lineage_record_id=f"iriai-lineage-gap:{digest_payload({'record': provider_record.record_id})[:16]}",
        feature_id=request.feature_id,
        group_idx=request.group_idx,
        effective_group_idx=None,
        code_span=provider_record.code_span,
        provider_record_ids=[provider_record.record_id],
        commit_hashes=provider_record.commit_hashes,
        task_ids=[],
        artifact_ids=[],
        verify_evidence_ids=[],
        rca_evidence_ids=[],
        repair_evidence_ids=[],
        checkpoint_artifact_ids=[],
        commit_proof_evidence_ids=[],
        governance_finding_ids=[],
        evidence_refs=[],
        content_digest=content_digest,
        linkage_confidence=min(provider_record.confidence, 0.35),
        gaps=[f"line_provenance_gap:{provider_record.record_id}"],
    )


def _spans_overlap(left: CodeSpanRef, right: CodeSpanRef) -> bool:
    return left.start_line <= right.end_line and right.start_line <= left.end_line


def flatten(values: Iterable[Iterable[Any]]) -> list[Any]:
    flattened: list[Any] = []
    for value in values:
        flattened.extend(value)
    return flattened


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dedupe_int(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    deduped: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dedupe_evidence_refs(values: Iterable[GovernanceEvidenceRef]) -> list[GovernanceEvidenceRef]:
    seen: set[str] = set()
    deduped: list[GovernanceEvidenceRef] = []
    for value in values:
        if value.ref_id in seen:
            continue
        seen.add(value.ref_id)
        deduped.append(value)
    return deduped


__all__ = [
    "ContextLayerService",
    "ContextPageStore",
    "IriAILineagePlugin",
    "IriAILineageSourceRecord",
]
