# 21. IriAI Context Layer

## Objective

Define the IriAI Context Layer that provides agents with exact, line-aware,
workflow-aware context at task execution time. This layer sits above commit/line
provenance and governance evidence. It gives agents answers such as:

- Why does this line exist?
- Which feature, group, task, artifact, verifier, repair, RCA, checkpoint, and
  commit introduced or modified it?
- Which prior workflow failures or governance findings are relevant to this file
  or task?
- Which evidence should the agent inspect, and which evidence remains available
  behind exact refs or paginated follow-up reads?

The context layer is a standalone workflow service plus agent-consumable tool
surface. It is not a custom runtime replacement. Runtime adapters receive
read-safe context manifests from the dispatcher; they do not own context
discovery.

The core design principle is **lossless selection, not lossy truncation**. The
context layer may page results, return a manifest, or require a follow-up exact
fetch, but it must not silently truncate semantic fields and then present the
result as complete task context.

## Current Code Citations

- Dispatcher prompt context boundary: [PromptContextBundle](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/execution/dispatcher.py:220).
- Dispatcher context export: [dispatcher.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/execution/dispatcher.py:1401).
- Gate context package builder: [ContextPackageBuilder](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/execution/gates.py:505).
- Gate context budget model: [ContextBudget](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/execution/gates.py:352).
- Task contract lineage sources: [task_contracts.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/execution/task_contracts.py:265).
- Stored prompt context evidence: [PromptContextEvidence](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/execution_control/models.py:305).
- Existing implementation prompt-context bridge: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5221).
- Commit/line provenance slice: [14-commit-and-line-provenance.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/14-commit-and-line-provenance.md:112).
- Governance agent context slice: [19-governance-agent-and-reporting.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/19-governance-agent-and-reporting.md:87).

## Current Failure Mode From `8ac124d6`

Agents and reviewers repeatedly had to reconstruct code context from stale
`dag-task:*` artifacts, verifier output, Slack messages, canonical-vs-alias
paths, and Git commits. That reconstruction was expensive and error-prone. The
workflow needs a single context layer that can assemble cited, line-aware,
exact-or-paged context from providers and IriAI lineage instead of relying on
each prompt builder to rediscover provenance.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 03 contract implementation logs for task lineage, path rules, required
  files, allowed files, forbidden files, and acceptance criteria.
- Slice 05 dispatcher logs for prompt-context bundle shape and runtime adapter
  boundaries.
- Slice 06 gate logs for `ContextReadRef`, context budget behavior, stale-context
  checks, and prompt-context evidence storage.
- Slice 08 merge queue logs for commit proof, checkpoint proof, no-dirty proof,
  and multi-repo commit coverage.
- Slice 13 governance evidence logs for evidence refs and read budgets.
- Slice 14 commit/line provenance logs for provider-ready line provenance.
- Slice 19 reporting logs for agent-context budgets and advisory-only policy
  guidance.

Compatible deviations:

- Provider availability may differ by machine. The context layer must work with
  `NativeGitProvider` alone.
- GitAI, Engram, H5i, Semantica, Oobo, or other local provenance adapters may be
  read-only wrappers around installed CLI tools, local databases, Git refs, or
  future library APIs, as long as the provider contract, citation model, and
  completeness semantics are preserved.

Blocking deviations:

- Context materialization requires a provider that is not installed.
- Context provider output can override task contracts, gates, failure-router
  policy, merge-queue policy, or activated policy artifacts.
- Context reads are unbounded or embed full artifact bodies by default.
- Context packages silently truncate semantic fields while claiming to be
  complete.
- Provider output is trusted without IriAI lineage reconciliation.

## Proposed Interfaces And Types

```python
ContextProviderName = Literal["git_ai", "engram", "h5i", "native_git"]
ContextPackageKind = Literal["manifest", "exact", "preview"]
ContextCompleteness = Literal["complete", "paged", "preview_only", "unavailable"]

class ContextLayerBudget(BaseModel):
    # Read/page limits, not semantic truncation permission.
    max_files: int = 12
    max_spans_per_file: int = 20
    max_lines_per_span: int = 80
    max_commits: int = 50
    max_provider_records: int = 120
    max_provider_refs_per_record: int = 20
    max_provider_warnings_per_record: int = 10
    max_lineage_records: int = 120
    max_task_ids_per_lineage: int = 20
    max_artifact_ids_per_lineage: int = 40
    max_evidence_refs_per_lineage: int = 40
    max_governance_findings_per_lineage: int = 20
    max_omitted_refs: int = 200
    max_provider_payload_bytes: int = 512_000
    max_rendered_preview_chars: int = 20_000
    timeout_ms: int = 10_000

class CodeSpanRef(BaseModel):
    repo_id: str
    path: str
    start_line: int
    end_line: int
    ref: str = "HEAD"

class ProviderLineageRecord(BaseModel):
    record_id: str
    provider: ContextProviderName
    repo_id: str
    path: str
    start_line: int
    end_line: int
    code_span: CodeSpanRef
    commit_hashes: list[str]
    provider_refs: list[str]
    provider_state_digest: str
    content_digest: str
    confidence: float
    warnings: list[str]

class IriAILineageRecord(BaseModel):
    lineage_record_id: str
    feature_id: str
    group_idx: int | None
    effective_group_idx: int | None
    code_span: CodeSpanRef
    provider_record_ids: list[str]
    commit_hashes: list[str]
    task_ids: list[str]
    artifact_ids: list[int]
    verify_evidence_ids: list[int]
    rca_evidence_ids: list[int]
    repair_evidence_ids: list[int]
    checkpoint_artifact_ids: list[int]
    commit_proof_evidence_ids: list[int]
    governance_finding_ids: list[str]
    evidence_refs: list[GovernanceEvidenceRef]
    content_digest: str
    linkage_confidence: float
    gaps: list[str]

ProviderStatus = Literal[
    "available",
    "disabled",
    "unavailable",
    "timed_out",
    "error",
]

class ProviderStateRef(BaseModel):
    provider: ContextProviderName
    repo_id: str
    ref: str
    state_digest: str
    indexed_at: datetime | None = None
    status: ProviderStatus

class ProviderAvailability(BaseModel):
    provider: ContextProviderName
    status: ProviderStatus
    version: str | None = None
    checked_at: datetime
    state_digest: str | None = None
    timeout_ms: int
    message: str | None = None

class ProviderIndexResult(BaseModel):
    provider: ContextProviderName
    repo_id: str
    state_ref: ProviderStateRef | None
    indexed: bool
    warnings: list[str]
    omitted_counts: dict[str, int]

class ContextEvidenceSnapshot(BaseModel):
    source_dag_artifact_id: int
    dag_sha256: str
    typed_journal_high_watermark: int
    typed_evidence_digest: str
    commit_proof_digest: str | None = None
    governance_snapshot_digest: str | None = None

class ContextLayerRequest(BaseModel):
    feature_id: str
    source_dag_artifact_id: int
    dag_sha256: str
    evidence_snapshot: ContextEvidenceSnapshot
    task_id: str | None = None
    group_idx: int | None = None
    repo_ids: list[str] = Field(default_factory=list)
    spans: list[CodeSpanRef] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    include_governance: bool = True
    require_complete: bool = True
    budget: ContextLayerBudget = Field(default_factory=ContextLayerBudget)

class ContextLayerPackage(BaseModel):
    package_id: str
    package_digest: str
    generated_at: datetime
    package_kind: ContextPackageKind
    completeness: ContextCompleteness
    request: ContextLayerRequest
    source_dag_artifact_id: int
    dag_sha256: str
    evidence_snapshot: ContextEvidenceSnapshot
    provider_state_refs: list[ProviderStateRef]
    provider_state_digest: str
    provider_order: list[ContextProviderName]
    provider_records: list[ProviderLineageRecord]
    iriai_lineage: list[IriAILineageRecord]
    rendered_preview: str | None = None
    page_refs: list[GovernanceEvidenceRef]
    omitted_refs: list[GovernanceEvidenceRef]
    omitted_counts: dict[str, int]
    incomplete_reason: str | None = None
    advisory_only: Literal[True] = True
```

Freshness invariant:

- `package_digest` is computed over the request, `source_dag_artifact_id`,
  `dag_sha256`, `ContextEvidenceSnapshot`, `provider_state_digest`, canonical
  provider record content digests, canonical lineage record content digests,
  omitted counts, and rendered context digest.
- A package is stale if any of these fields differ from the current dispatcher
  view for the same feature/task. Stale packages are never reused silently.
- `provider_state_digest` is a deterministic digest of the ordered
  `ProviderStateRef` list. Disabled, unavailable, timed-out, and error provider
  states are included in the digest so retries cannot accidentally compare only
  successful providers.

Completeness and paging invariant:

- `ContextLayerBudget` is a resource-safety and page-size contract, not a
  permission to truncate meaning. If the requested exact context exceeds a page
  limit, the builder returns `completeness="paged"` with `page_refs` that can be
  fetched exactly.
- `rendered_preview` is optional and explicitly non-authoritative. It may be
  compacted for prompt convenience, but agents and gates must treat
  `provider_records`, `iriai_lineage`, and `page_refs` as the citeable context.
- If `require_complete=True` and the package cannot provide complete exact
  selected context or exact page refs, the package is `unavailable` and task
  dispatch must either fetch narrower spans or proceed without context. It must
  not proceed with a lossy package labeled complete.
- The builder must never store broad artifact bodies or unbounded typed-journal
  fanout in the package itself.

Provider interface:

```python
class ProvenanceProvider(Protocol):
    name: ContextProviderName
    async def available(self) -> ProviderAvailability: ...
    async def index_repo(
        self,
        repo: RepoIdentity,
        *,
        budget: ContextLayerBudget,
    ) -> ProviderIndexResult: ...
    async def query_spans(
        self,
        repo: RepoIdentity,
        spans: Sequence[CodeSpanRef],
        *,
        budget: ContextLayerBudget,
    ) -> list[ProviderLineageRecord]: ...
```

Provider availability semantics:

- `disabled` means the provider is not configured and should not be retried in
  that package.
- `unavailable` means it is configured but cannot currently answer.
- `timed_out` means the provider exceeded `ContextLayerBudget.timeout_ms`.
- `error` means the provider failed unexpectedly. The package records the error
  as advisory context metadata.
- None of these optional-provider states can block dispatch, merge, checkpoint,
  or failure routing. Only stale or malformed `ContextReadRef` materialization
  from Slice 06 can block, and that check validates package identity, not
  optional-provider success.

Provider implementations:

- `GitAIProvider`: generic adapter for Git-based AI provenance tools that write
  Git notes, sidecar refs, local indexes, or commit-attached context. Use when
  available and when repo provenance refs match IriAI commit proof.
- `EngramProvider`: adapter for local transcript/span provenance. Use for
  conversation-to-code context when available.
- `H5iProvider`: optional adapter for context DAGs, claims, memory refs, and
  commit-linked reasoning sidecars if installed and explicitly enabled.
- `SemanticaProvider` or `OoboProvider`: optional future adapters for local
  AI-attribution or commit-memory tools when they can provide stable, local,
  citeable refs without becoming workflow authority.
- `NativeGitProvider`: fallback using `git blame`, `git log`, commit trailers,
  Git notes/refs, and typed `dag-commit-proof:*` evidence. This provider is
  mandatory and must pass all core tests.

IriAI lineage plugin:

```python
class IriAILineagePlugin:
    async def map_provider_records(
        self,
        request: ContextLayerRequest,
        records: Sequence[ProviderLineageRecord],
        *,
        evidence_snapshot: ContextEvidenceSnapshot,
        provider_state_refs: Sequence[ProviderStateRef],
        budget: ContextLayerBudget,
    ) -> list[IriAILineageRecord]: ...

    async def build_context_package(
        self,
        request: ContextLayerRequest,
    ) -> ContextLayerPackage: ...
```

The plugin maps commits and spans to IriAI typed evidence:

`commit -> commit_proof -> merge_queue_item -> task_contract -> attempt ->
verify/RCA/repair/gate evidence -> checkpoint -> governance findings`.

`map_provider_records` uses the full request, not just `feature_id`, so a
multi-task commit or multi-span provider record can be resolved against the
specific group/task/span scope that asked for context. Each `IriAILineageRecord`
must carry the `CodeSpanRef`, provider record ids, commit hashes, and evidence
refs that justify the mapping.

## Refactoring Steps

1. Add a new context-layer module after Slice 20, for example
   `src/iriai_build_v2/workflows/develop/context_layer/`, with provider
   protocol, native Git provider, lineage plugin, package renderer, and tests.
2. Implement `NativeGitProvider` first. It must answer span queries from Git
   blame/log plus Slice 14 commit provenance and typed commit proof. It must not
   require GitAI, Engram, or H5i.
3. Implement optional provider adapters behind availability checks. Missing
   optional providers return unavailable status and never block task dispatch.
4. Implement provider reconciliation. Provider records are advisory until the
   IriAI lineage plugin links them to typed evidence or records a provenance gap.
   Provider records that cannot be tied to a code span and typed evidence remain
   visible only as low-confidence advisory notes.
5. Add context package rendering with strict read/page limits. Rendered previews
   include compact line/task summaries and evidence refs, not full artifacts.
   Exact provider and lineage records are either complete for the selected scope
   or paged through explicit refs.
6. Integrate with dispatcher prompt construction after Slice 05:
   `RuntimeDispatcher` requests a `ContextLayerPackage` before task execution and
   records its package id/digest in `PromptContextBundle`.
7. Integrate with gates after Slice 06: context packages become
   `ContextReadRef`/`PromptContextEvidence` inputs and stale-context checks
   verify package digest, feature id, task id, source DAG artifact id, DAG sha,
   typed evidence snapshot digest, and provider state digest.
8. Integrate with governance reporting after Slice 19: agent context endpoint
   uses the same `ContextLayerPackage`, preserving `advisory_only=True`.
9. Add CLI commands:

   ```bash
   python -m iriai_build_v2.workflows.develop.context_layer explain --feature-id <id> --repo-id <repo> --path <path> --line <n>
   python -m iriai_build_v2.workflows.develop.context_layer package --feature-id <id> --task-id <task>
   python -m iriai_build_v2.workflows.develop.context_layer providers
   ```

## Persistence And Artifact Compatibility

- Context packages are stored as typed governance/context rows and projected as
  bounded review artifacts such as `review:context-package:{package_id}`.
- `PromptContextBundle` gains only package id/digest/ref fields; it does not
  embed provider payloads.
- Existing `dag-task:*`, `dag-verify:*`, `dag-group:*`, and
  `dag-commit-proof:*` shapes are not changed by this slice.
- Optional provider indexes are local supporting caches. Postgres typed evidence
  remains canonical for IriAI lineage.
- Provider-specific data is never required for resume, checkpoint, merge, or
  route decisions.
- Package identity fields are persisted with the package and with the
  dispatcher/gate references that consumed the package. This makes stale-context
  rejection possible after process restart without rehydrating provider payloads.
- Exact context pages are fetched by package id and page ref. Pages are stable
  projections over provider records and typed IriAI evidence, not regenerated
  summaries.

## Edge Cases And Failure Handling

- No optional providers installed: use `NativeGitProvider`.
- Provider timeout: return partial context with provider warning, lower
  confidence, omitted refs, and a `timed_out` provider state. Do not block
  dispatch, verification, merge, checkpoint, or routing because an optional
  provider timed out.
- Provider output conflicts with IriAI lineage: prefer typed IriAI evidence,
  record `governance_evidence_conflict`, and mark the provider record advisory.
- Commit lacks provenance: return Git blame/log evidence plus
  `line_provenance_gap`; do not fabricate task lineage.
- File has moved or been renamed: follow Git rename detection within budget and
  cite lineage gaps when history exceeds caps.
- Generated or vendored files: allow task contract policy to suppress noisy line
  context and provide generator/source-task context instead.
- Prompt preview budget exhausted: include a manifest with exact page refs and
  either omit `rendered_preview` or mark `completeness="preview_only"`. Do not
  mark lossy preview text as complete context.

## Tests

- `NativeGitProvider` maps a small fixture repo line range to commit hashes,
  trailers, notes/refs, and IriAI commit proof.
- Missing GitAI/Engram/H5i does not fail context package generation.
- Optional provider availability checks are bounded and do not shell out during
  import.
- Provider conflict with typed lineage records a governance evidence conflict and
  keeps typed lineage authoritative.
- Context package respects max files, spans, lines, commits, lineage records,
  provider records, provider refs, provider warnings, task ids, artifact ids,
  evidence refs, governance finding ids, omitted refs, payload bytes, prompt
  chars, and timeout.
- Context package paging tests prove that records over the first page remain
  retrievable through exact `page_refs`, not dropped.
- `require_complete=True` tests fail closed when exact context or page refs
  cannot be produced.
- Context package digest changes when canonical provider record content or
  lineage record content changes, even if generated record ids are reused.
- Dispatcher integration records package id/digest in `PromptContextBundle`
  without embedding full provider payloads.
- Gate stale-context checks reject a package built for a different feature, task,
  source DAG artifact id, DAG sha, typed evidence snapshot digest, or provider
  state digest.
- Multi-span and multi-task commit fixtures map lineage records back to the
  exact `CodeSpanRef`, provider record ids, commit hashes, and task/evidence refs
  that justified the context.
- Agent context output is advisory-only and cannot override contracts, gates,
  router policy, merge queue policy, or activated policy artifacts.

## Acceptance Criteria

- The context layer works with `NativeGitProvider` alone.
- GitAI, Engram, and H5i integrations are optional provider adapters, not
  hard dependencies.
- IriAI lineage plugin maps provider code-span evidence to typed workflow
  evidence and records explicit gaps.
- Task-execute prompts receive read-safe context packages with package ids,
  digests, source DAG ids, evidence snapshot digests, provider state digests,
  exact page refs, completeness status, and advisory-only policy markers.
- Context packages are reproducible for the same request, provider state, and
  typed evidence snapshot.
- No authoritative context path relies on silent truncation. Prompt previews are
  allowed only when labeled preview-only or backed by exact page refs.
- No context-layer output mutates workflow state or changes execution authority.

## Rollout And Rollback Notes

This slice lands after governance acceptance and remains advisory. Rollback
disables context package generation and provider adapters while leaving existing
context package review artifacts as audit evidence. Runtime dispatch falls back
to the existing prompt context bundle path without changing task contracts,
gates, merge queue, or checkpoints.

## Cross-Slice Dependencies

- Slice 03 supplies task contracts and task lineage sources.
- Slice 05 supplies dispatcher prompt-context boundaries.
- Slice 06 supplies context budgets, stale-context checks, and prompt-context
  evidence.
- Slice 08 supplies commit proof and checkpoint proof.
- Slice 13 supplies governance evidence refs and read budgets.
- Slice 14 supplies commit/line provenance.
- Slice 16 supplies provenance-gap and evidence-conflict findings.
- Slice 19 supplies agent-context reporting constraints.
- Slice 20 governs all-at-once governance acceptance before this slice becomes
  available.
