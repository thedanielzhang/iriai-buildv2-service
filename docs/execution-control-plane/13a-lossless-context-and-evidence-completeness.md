# 13A. Lossless Context And Evidence Completeness

## Objective

Add the cross-cutting remediation required before governance and context-layer
work begins: every task prompt, verifier package, dashboard snapshot,
supervisor digest, and governance input must distinguish exact evidence from
display previews. Resource limits remain mandatory, but they are page/read
limits, not permission to silently truncate semantic context.

This slice is a **post-landing change-control remediation** for the
execution-control-plane implementation. It runs after Slices 00-12 have landed
and must not rewrite accepted slice plans or destabilize an active slice review
cycle. It is additive: it records the cross-cutting invariant, the
implementation remediation, and the acceptance tests needed before governance
or task-execute context can use exact/paged evidence as execution authority.

The invariant is:

> If a component can influence dispatch, verification, merge, checkpoint,
> routing, scheduler feedback, or policy recommendation, it must consume exact
> cited evidence or an exact paged manifest. Lossy summaries and previews are
> display-only.

## Implementation Status And Change-Control Rule

Before editing any plan or implementation file for this remediation, re-read:

1. The latest accepted-slice records in
   `docs/execution-control-plane/implementation-decisions.jsonl`.
2. The tail of `docs/execution-control-plane/implementation-journal.md`.
3. Current active reviewer findings and active subagent ownership.
4. Current `git status --short` for the files you intend to touch.

As of the status check that originally created this remediation, Slices 00-05
were accepted and Slice 06 was active but unaccepted. The remediation is now
numbered 13A so the current Slices 00-12 implementation can finish without a
new cross-cutting gate being inserted mid-flight. Therefore:

- Do not rewrite Slices 00-05 plans. Remediate them through this additive slice,
  compatibility tests, and change-control records.
- Do not patch Slice 06 plan or implementation solely for this remediation while
  Slice 06 has an active review/remediation loop. If Slice 06 independently
  adopts an exactness fix, it must do so through its own reviewer loop and
  decision-log entry.
- Slices 07-12 follow their existing accepted plans and their own review loops.
  If they independently adopt an exactness fix, it must be recorded in that
  slice's decision log, but 13A must not become a hidden prerequisite for their
  acceptance.
- Governance and context slices may draft or plan against existing evidence, but
  no governance recommendation, policy adapter, or task-execute context package
  may claim exact/paged execution authority until 13A has passed or has an
  explicit accepted deviation explaining why the context layer remains
  advisory/display-only.

## Current Code Citations

- Dispatcher prompt bundle shape: [PromptContextBundle](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/05-dispatcher-runtime-boundary.md:127).
- Dispatcher prompt boundary and exclusions: [Prompt And Context Boundaries](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/05-dispatcher-runtime-boundary.md:368).
- Gate read-budget model: [ReadBudgetReport](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/06-gates-and-verification-graph.md:186).
- Gate context read rules: [Read Rules](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/06-gates-and-verification-graph.md:500).
- Supervisor snapshot truncation fields: [ControlPlaneSnapshot](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/10-supervisor-dashboard-integration.md:210).
- Supervisor read rules and truncation metadata: [Read Model And Safety](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/10-supervisor-dashboard-integration.md:520).
- Atomic landing readiness gate: [Verification and routing](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:273).
- IriAI context layer exact paging model: [21-iriai-context-layer.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/21-iriai-context-layer.md:21).

## Current Failure Mode From `8ac124d6`

The workflow repeatedly lost time when a bounded or summary-shaped view became
confused with complete execution evidence: stale verifier context, stale
`dag-task:*` projections, worktree alias evidence, supervisor degraded messages,
and verifier/RCA prompts that lacked the exact canonical state needed to resolve
the issue on the first pass.

The first 12 slices already protect resource usage with bounded reads, but some
interfaces still encode `truncated`, `omitted`, `summary`, or `prompt_summary`
without a shared contract for whether the consumer may act on that partial view.
That is the class of bug this slice closes.

## Upstream Implementation Artifact Review

Before implementing this slice, review the complete Slice 00-12 implementation
bundle that exists at that time:

- `implementation-journal.md` entries for Slices 01, 05, 06, 10, and 12.
- `implementation-decisions.jsonl` decisions about artifact slices, prompt
  context, verifier packaging, supervisor snapshots, and resource caps.
- Acceptance records for dispatcher resume, gate approval, supervisor/dash
  snapshots, and atomic landing.
- Reviewer findings that mention `summary`, `preview`, `bounded`, `truncated`,
  `omitted`, `budget`, `provider`, `stale context`, or `snapshot`.
- Any accepted deviations from Slices 05, 06, or 10. A deviation that lets a
  truncated or summary-only view drive an authoritative decision blocks this
  slice.
- The latest active-slice status. If a slice is in review, do not change its
  files as part of this remediation unless the slice owner/review loop explicitly
  accepts the change.

Compatible deviations:

- A component may expose compact summaries for display if every summary carries
  exact evidence refs and `display_only=True`.
- A model prompt may receive a preview if the authoritative package also carries
  exact page refs and completeness metadata.
- Supervisor/dashboard snapshots may be partial if their classifiers and action
  recommendations fail closed for any decision field whose page is incomplete.

Blocking deviations:

- `PromptContextBundle.truncation_notes` is the only indication that task
  context is incomplete.
- A verifier, gate, router, merge queue, scheduler, supervisor classifier, or
  governance recommender acts on a truncated list without fetching exact pages
  or marking the decision degraded/unknown.
- A deterministic summary is treated as satisfying required evidence unless it
  is a typed proof row with a digest and exact page refs back to the source.
- Provider/runtime output or a compatibility artifact projection becomes
  execution authority without typed evidence reconciliation.

## Proposed Interfaces And Types

Implement shared completeness models in the execution-control package so
dispatcher, gates, snapshots, governance, and the future context layer use the
same terms. If Slice 01 is already accepted, this is an additive 13A-owned
module and migration, not a retroactive Slice 01 plan change.

```python
CompletenessState = Literal[
    "complete",
    "paged",
    "preview_only",
    "unavailable",
]

EvidenceAuthority = Literal[
    "execution_authority",
    "gate_authority",
    "routing_authority",
    "advisory",
    "display_only",
]

class EvidencePageRef(BaseModel):
    ref_id: str
    source_kind: Literal[
        "typed_row",
        "artifact",
        "event",
        "file",
        "diff",
        "provider_record",
        "projection",
    ]
    source_id: int | str
    sha256: str
    start: int | None = None
    end: int | None = None
    item_count: int | None = None
    bytes: int | None = None
    reason: str

class EvidenceCompleteness(BaseModel):
    state: CompletenessState
    authority: EvidenceAuthority
    complete_for: list[str]
    missing_required_refs: list[EvidencePageRef] = Field(default_factory=list)
    page_refs: list[EvidencePageRef] = Field(default_factory=list)
    preview_ref: EvidencePageRef | None = None
    unavailable_reason: str | None = None
    completeness_digest: str

class ExactEvidenceManifest(BaseModel):
    manifest_id: str
    manifest_digest: str
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_ids: list[str]
    selection_scope: list[str]
    completeness: EvidenceCompleteness
    required_page_refs: list[EvidencePageRef]
    optional_page_refs: list[EvidencePageRef]
    display_preview_ref: EvidencePageRef | None = None
    advisory_only: bool

class AuthoritativeContextRef(BaseModel):
    manifest_id: str
    manifest_digest: str
    completeness_digest: str
    required_complete_for: list[str]
    authority: EvidenceAuthority
```

Add a 13A-owned compatibility wrapper for accepted Slice 05
`PromptContextBundle` records:

```python
class PromptContextBundle(BaseModel):
    prompt_ref: int
    prompt_sha256: str
    display_prompt_summary: str
    context_manifest_ref: AuthoritativeContextRef
    context_file_refs: list[int]
    context_file_paths: list[str]
    context_sha256: str
    included_contract_ids: list[int]
    included_evidence_ids: list[int]
    excluded_evidence_ids: list[int]
    excluded_evidence_refs: list[EvidencePageRef]
    completeness: EvidenceCompleteness
```

Existing `truncation_notes` remains readable for compatibility, but it is
display metadata only. New authoritative consumers must read the 13A
`EvidenceCompleteness`/`AuthoritativeContextRef` wrapper.

Add a 13A-owned exactness companion for Slice 06 `ReadBudgetReport` records:

```python
class ReadBudgetReport(BaseModel):
    bounded_queries: list[BoundedQuery]
    aggregate_bytes: int
    exact_manifest_ref: AuthoritativeContextRef
    required_ref_completeness: dict[str, EvidenceCompleteness]
    optional_ref_completeness: dict[str, EvidenceCompleteness]
    blocked_unbounded_read_count: int = 0
    budget_digest: str
```

`omitted_required_refs` may exist only on rejected packages in the 13A companion
record. An approved gate package must have no missing required refs unless the
current active Slice 06 implementation has separately accepted an equivalent
typed proof through its own review loop.

Add a 13A-owned completeness companion for Slice 10 snapshots:

```python
class SnapshotListField(BaseModel):
    field_name: str
    completeness: EvidenceCompleteness
    items: list[Any]
    next_page_ref: EvidencePageRef | None = None

class ControlPlaneSnapshot(BaseModel):
    feature_id: str
    snapshot_version: str
    generated_at: datetime
    source: Literal["typed", "legacy_fallback", "mixed"]
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
    actionability: Literal["complete", "degraded_display_only", "unknown"]
    fields: dict[str, SnapshotListField]
```

Supervisor classifiers may recommend action only from fields whose
`EvidenceCompleteness.authority` and `complete_for` cover the classifier rule.

## Refactoring Steps

1. Re-check implementation status and record a 13A start decision before
   touching code or docs. The decision must state which slices are accepted,
   active, or not started.
2. Add `completeness.py` under the execution-control package with the shared
   models above plus digest helpers. This is 13A-owned if Slice 01 is already
   accepted.
3. Add compatibility adapters that derive `EvidenceCompleteness` and
   `AuthoritativeContextRef` from existing Slice 05 prompt-context records
   without changing accepted Slice 05 interfaces in-place.
4. Update the prompt/context builder through the 13A adapter so a large prompt
   emits a compact preview plus
   exact page refs. If `required_complete_for` cannot be satisfied, dispatch
   records `runtime_context/context_incomplete` and does not invoke a runtime.
5. Add a 13A gate companion record so model verifier input is either complete
   for the gate scope or exactly paged. A gate may not approve from
   `preview_only` evidence after 13A is enabled.
6. Replace any deterministic-summary escape hatch in post-13A gates with
   explicit typed proof rows. A summary can satisfy a required gate only if the
   proof row states the exact source digest, page refs, proof algorithm, and
   verification time.
7. Add a 13A snapshot companion so every list field carries field-level
   completeness. Partial snapshots are allowed for display but classifier rules
   fail closed unless their required fields are complete.
8. Add a 13A acceptance artifact and README index entry instead of rewriting
   accepted Slice 00-12 plan docs.
9. Update governance Slices 13-20 and context Slice 21 to depend on this shared
   completeness model instead of redefining authority semantics locally.

## Persistence And Artifact Compatibility

- Store `ExactEvidenceManifest`, `EvidenceCompleteness`, and page refs as typed
  rows owned by 13A if Slice 01 has already been accepted; otherwise Slice 01 may
  own the tables only if the journal confirms Slice 01 has not yet started.
- Compatibility artifacts may include previews, but must project
  `manifest_id`, `manifest_digest`, `completeness_digest`, `state`, and
  `authority`.
- Legacy `dag-task:*`, `dag-verify:*`, and dashboard artifacts remain readable,
  but their summaries are never authority unless linked to a typed manifest.
- Page refs must be stable across resume. Re-fetching a page by ref must return
  the same digest or fail as stale/corrupt.
- Full artifact bodies remain spill-backed or slice-backed; this slice does not
  allow broad body hydration to avoid pagination.

## Edge Cases And Failure Handling

- Required evidence exceeds page size: return `state="paged"` with exact page
  refs. Do not drop the evidence.
- Required evidence cannot be paged exactly: return `state="unavailable"` and
  route `runtime_context/context_incomplete` or
  `verifier_context/context_incomplete`.
- Optional evidence exceeds page size: include optional page refs and
  `preview_only` display text if useful. Optional previews never satisfy
  required gates.
- Snapshot query hits a cap: field-level completeness becomes `paged`; dashboard
  may render the first page, but supervisor classifiers depending on that field
  return degraded/unknown until exact pages are fetched.
- Provider output is larger than a page: store provider records behind exact
  page refs. Provider previews remain advisory until the IriAI lineage plugin
  reconciles them to typed evidence.
- Existing legacy feature lacks typed manifests: governance/reporting labels the
  evidence `legacy_display_only` and cannot use it for policy conclusions unless
  replay reconstructs exact typed manifests.

## Tests

- `test_prompt_context_large_required_evidence_pages_not_truncates`: large
  required evidence returns page refs and no semantic truncation notes.
- `test_prompt_context_incomplete_required_evidence_blocks_dispatch`: missing
  exact page refs records `runtime_context/context_incomplete` and no runtime
  starts.
- `test_prompt_preview_is_display_only`: preview text cannot satisfy contracts,
  gates, routing, merge, checkpoint, scheduler, or supervisor rules.
- `test_gate_approval_requires_complete_or_paged_manifest`: raw verifier and
  expanded lenses cannot approve from `preview_only` context.
- `test_gate_summary_requires_typed_equivalence_proof`: deterministic summaries
  satisfy required evidence only with source digest, proof algorithm, and exact
  page refs.
- `test_snapshot_partial_field_blocks_classifier_action`: supervisor returns
  degraded/unknown when a classifier needs a paged field it has not fetched.
- `test_snapshot_partial_field_can_render_dashboard`: dashboard can display a
  partial field with cursor/page refs and visible degraded metadata.
- `test_page_ref_digest_stable_across_resume`: exact page refs survive process
  restart and stale/corrupt pages are rejected.
- `test_legacy_projection_summary_is_display_only`: legacy artifact summaries
  cannot become authority without linked typed manifests.
- `test_provider_payload_pages_are_advisory_until_reconciled`: provider pages
  do not affect execution decisions before IriAI lineage reconciliation.

## Acceptance Criteria

- No authoritative decision path can consume a lossy summary, preview, or
  truncated list as if it were complete evidence.
- Prompt, gate, snapshot, governance, and future context-layer payloads share
  the same completeness and page-ref model.
- Resource limits remain enforced through exact paging, fail-closed unavailable
  states, or display-only previews.
- Supervisor/dashboard can render partial views without turning them into
  restart, repair, or operator recommendations.
- 13A cannot pass unless exact-or-paged context tests are green. This is a
  post-00-12 gate for governance/context authority, not a retroactive Slice 12
  atomic landing gate.

## Rollout And Rollback Notes

This slice lands after the all-at-once Slices 00-12 control-plane bundle. It
does not delay or redefine the current 00-12 atomic landing. Rollback disables
13A-owned exactness authority refs and leaves the accepted 00-12 workflow
intact.

If this slice fails after landing, rollback must clear any active context
manifest authority refs or mark them stale before resuming legacy execution.

## Cross-Slice Dependencies

- Slice 01 supplies the store foundation; 13A owns any additive completeness rows
  if Slice 01 is already accepted.
- Slice 05 supplies prompt context records; 13A consumes them through
  compatibility adapters if Slice 05 is already accepted.
- Slice 06 supplies verifier/gate records; 13A must not interrupt an active
  Slice 06 review loop.
- Slice 07 routes incomplete context as workflow/context failure, not product
  defect.
- Slice 08 accepts merge/checkpoint proof only from complete gate manifests.
- Slice 09 consumes metrics only from complete or explicitly degraded fields.
- Slice 10 uses field-level completeness for dashboard/supervisor snapshots.
- Slice 12 supplies the accepted atomic landing baseline; 13A must not rewrite
  its acceptance criteria after the fact.
- Slices 13-21 must depend on this shared model before they enable
  governance/context execution authority. Planning and read-only analysis may
  proceed with degraded/advisory semantics if 13A has not landed yet.
