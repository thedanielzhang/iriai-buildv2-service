# 13. Governance Evidence Model

## Objective

Define the canonical evidence model for the governance tool that runs after the
execution control plane lands. The governance tool analyzes typed execution
state, compatibility projections, commit/merge proof, task contracts, scheduler
feedback, supervisor observations, resource metrics, and implementation
journals. It produces evidence sets that later slices use for metrics,
findings, policy recommendations, replay, reporting, and governance acceptance.

The governance evidence model is analytical. It does not own executor mutation,
merge authority, checkpoint authority, or policy activation.

## Current Code Citations

- Typed journal planned interface: [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:37).
- Legacy projection ownership: [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:886).
- Commit and checkpoint proof projections: [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:902).
- Current artifact writes and dashboard mirroring: [PostgresArtifactStore.put](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:379).
- Current feature event writes: [PostgresFeatureStore.log_event](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:55).
- Bounded supervisor observation model: [SupervisorObservationDigest](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:253).
- Static `8ac124d6` replay fixture contract: [test_execution_control_plane_fixture_replay.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_execution_control_plane_fixture_replay.py:13).
- Implementation journal source: [implementation-journal.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/implementation-journal.md:1).
- Decision log source: [implementation-decisions.jsonl](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/implementation-decisions.jsonl:1).

## Current Failure Mode From `8ac124d6`

The current evidence surface is spread across `events`, `artifacts`, Slack
messages, supervisor snapshots, Git state, and manual implementation notes. That
made workflow drag visible only after reconstruction. The governance tool must
turn that reconstruction into a first-class, typed, bounded evidence set.

The implementation of Slices 00-12 adds another evidence surface: the persistent
implementation journal and decision log. Governance must ingest those logs
because plan-vs-actual drift, reviewer findings, accepted deviations, and test
evidence are themselves workflow quality signals.

## Upstream Implementation Artifact Review

Before implementing this slice, review these artifacts from Slices 00-12:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- [implementation-journal.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/implementation-journal.md:1): confirm every Slice 00-12 execution brief, worker dispatch, reviewer dispatch, P1/P2 finding, patch, test result, acceptance record, and open P3 follow-up is present.
- [implementation-decisions.jsonl](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/implementation-decisions.jsonl:1): validate JSONL, confirm each decision row has timestamp, slice, event, summary, files, evidence, decision, alternatives rejected, and next action.
- Slice 00 fixtures under `/Users/danielzhang/src/iriai/iriai-build-v2/tests/fixtures/execution_control_plane/feature_8ac124d6/`: confirm they remain bounded summaries and selected slices only.
- Slice 01 projection tests and acceptance records: confirm typed rows and legacy projections are synchronous.
- Slice 08 merge/checkpoint tests and acceptance records: confirm commit proof, no-dirty proof, and `dag-group:*` projection evidence exists.
- Slice 12 landing result: confirm the complete control plane is green before governance implementation begins.

Compatible deviations:

- Renamed internal modules are allowed if the journal names the new owner and the
  compatibility projections remain stable.
- Extra nonblocking P3 follow-ups are allowed only when the owning slice accepted
  them explicitly and they do not affect governance evidence correctness.

Blocking deviations:

- Missing acceptance for any Slice 00-12 slice.
- Any unresolved P1/P2 reviewer finding.
- Any typed success without synchronous compatibility projection visibility.
- Any journal gap that prevents reconstructing what implementation actually
  changed.

## Proposed Interfaces And Types

Create a governance package after Slice 12, for example
`src/iriai_build_v2/workflows/develop/governance/`.

```python
EvidenceAuthority = Literal[
    "typed_journal",
    "compatibility_projection",
    "git_provenance",
    "implementation_journal",
    "implementation_decision_log",
    "supervisor_digest",
    "resource_snapshot",
    "legacy_event",
    "legacy_artifact_summary",
]

EvidenceQuality = Literal["canonical", "derived", "sampled", "advisory", "stale", "insufficient"]
CompletenessState = Literal["complete", "paged", "preview_only", "unavailable"]

class GovernanceReadBudget(BaseModel):
    max_event_rows: int = 500
    max_artifact_summary_rows: int = 5_000
    max_ref_resolutions: int = 20
    max_chars_per_ref: int = 40_000
    max_serialized_output_bytes: int = 2_000_000
    statement_timeout_ms: int = 10_000

class GovernanceEvidenceRef(BaseModel):
    authority: EvidenceAuthority
    ref_id: str
    feature_id: str | None = None
    slice_id: str | None = None
    artifact_id: int | None = None
    event_id: int | None = None
    commit_hash: str | None = None
    journal_anchor: str | None = None
    created_at: datetime | None = None
    digest: str
    quality: EvidenceQuality
    completeness: CompletenessState
    page_refs: list["GovernanceEvidencePageRef"] = Field(default_factory=list)
    preview_only: bool = False

class GovernanceEvidencePageRef(BaseModel):
    page_ref_id: str
    authority: EvidenceAuthority
    source_ref_id: str
    byte_start: int | None = None
    byte_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    item_start: int | None = None
    item_end: int | None = None
    digest: str
    completeness: CompletenessState
    exact: bool
    stale_check: dict[str, Any]

class GovernanceEvidenceSet(BaseModel):
    idempotency_key: str
    feature_id: str | None
    corpus_id: str
    generated_at: datetime
    source_window: dict[str, Any]
    refs: list[GovernanceEvidenceRef]
    omitted_refs: list[GovernanceEvidencePageRef]
    completeness: CompletenessState
    source_mix: dict[EvidenceAuthority, int] = Field(default_factory=dict)
    read_budget: GovernanceReadBudget
    read_budget_exhausted: bool = False
    quality: EvidenceQuality
    blockers: list[str]

class ImplementationArtifactAnchor(BaseModel):
    slice_id: str
    journal_path: str
    line_start: int | None
    decision_log_line: int | None
    event: str
    accepted: bool
    open_findings: list[str]
```

`GovernanceEvidenceIngestor` must expose bounded methods:

```python
class GovernanceEvidenceIngestor:
    async def ingest_feature_window(
        self,
        feature_id: str,
        window: GovernanceWindow,
        *,
        budget: GovernanceReadBudget,
    ) -> GovernanceEvidenceSet: ...
    async def ingest_implementation_artifacts(
        self,
        slice_ids: list[str],
        *,
        budget: GovernanceReadBudget,
    ) -> GovernanceEvidenceSet: ...
    async def resolve_ref(self, ref: GovernanceEvidenceRef, *, max_chars: int) -> GovernanceEvidenceSlice: ...
```

Mixed typed/legacy evidence is encoded as `quality="derived"` plus
`source_mix`, not as a separate `EvidenceQuality` literal. Confidence scoring in
Slice 15 uses `source_mix` to penalize legacy-heavy or incomplete typed evidence.

## Refactoring Steps

1. Add the governance package with pure model definitions and no executor hooks.
2. Add bounded readers over typed journal summaries, compatibility projection
   summaries, supervisor digests, resource snapshots, and implementation logs.
3. Add an implementation-journal parser that produces anchors from markdown
   headings, bullet lines, subagent IDs, test result lines, and acceptance notes.
4. Add a JSONL decision-log parser that rejects malformed rows and records line
   numbers as evidence anchors.
5. Add evidence-set digesting from sorted canonical JSON. The digest must include
   source ids and content digests, not full artifact bodies.
6. Store governance evidence sets as typed rows once the Slice 01 store exists,
   and project bounded review artifacts such as
   `review:governance-evidence:{corpus_id}`.
7. Keep legacy event/artifact ingestion read-only and bounded. Use summaries and
   selected slices only.

## Persistence And Artifact Compatibility

- Postgres typed journal remains canonical for execution state.
- Implementation journal and decision log are canonical for implementation
  process evidence.
- Git provenance is a durable projection for line and commit context, not a
  replacement for typed execution rows.
- Governance evidence sets may project review artifacts, but no `dag-*`
  execution, checkpoint, regroup activation, or merge artifact is written by
  this slice.

## Edge Cases And Failure Handling

- Missing implementation journal: mark the evidence set `insufficient` and block
  governance acceptance.
- Malformed JSONL decision row: record a `governance_evidence_gap` finding and
  block metrics that depend on plan-vs-actual analysis.
- Active feature with incomplete typed state: include status evidence, but mark
  incomplete work excluded from completed-throughput metrics.
- Spill-backed or large artifacts: store only summary refs; detail resolution
  must use exact page/slice APIs with source ranges and digests.
- Read budget exhausted: return the partial evidence set with
  `read_budget_exhausted=True`, populate `omitted_refs` as exact page refs when
  possible, mark quality `insufficient` or `derived`, and mark completeness
  `paged` or `unavailable`. Downstream metrics, findings, reports, acceptance
  checks, and recommendations can consume only the complete subset they can prove
  by exact refs; otherwise they must fail closed or render display-only output.
- Duplicate compatibility projections: keep all refs but identify the typed
  projection link that owns the authoritative row.
- Slack or prose-only supervisor evidence: mark advisory unless linked to typed
  observation or decision records.

## Tests

- Unit test implementation-journal parsing with accepted slices, open findings,
  worker IDs, reviewer IDs, and test-result anchors.
- Unit test decision-log parsing rejects malformed JSONL, missing required
  fields, and duplicate non-idempotent decisions.
- Ingest `8ac124d6` fixtures without broad artifact bodies.
- Ingest methods enforce `GovernanceReadBudget` row, character, byte, and
  timeout limits and report exhausted budgets explicitly.
- Exact page refs include source ranges, digests, and stale-check fields; stale
  or missing pages make the evidence set incomplete.
- Partial evidence sets cannot feed metrics, findings, recommendations, reports,
  or acceptance checks unless the consumer proves its required refs are complete
  or exactly paged. Otherwise the output is display-only/degraded.
- Ingest typed journal stubs plus compatibility projections and verify evidence
  refs cite typed ids first.
- Verify `resolve_ref` enforces max character budgets.
- Verify governance evidence projection never writes `dag-*` authority keys.

## Acceptance Criteria

- Every governance evidence set cites stable typed ids, compatibility projection
  ids, Git provenance refs, or implementation-log anchors.
- Evidence quality is explicit and conservative.
- No default governance path calls unbounded artifact/event body APIs.
- Implementation journals/logs are first-class evidence for plan-vs-actual
  analysis.
- Missing Slice 00-12 acceptance or unresolved P1/P2 findings blocks governance
  acceptance.

## Rollout And Rollback Notes

This slice begins only after Slice 12 acceptance. It lands with the governance
tool feature, not as a self-healing runtime change. Rollback disables governance
ingestion and leaves already-written governance review artifacts as audit
history. It must not mutate execution-control rows, checkpoints, or Git commits.

## Cross-Slice Dependencies

- Slice 00 provides the `8ac124d6` bounded replay corpus.
- Slice 01 provides typed journal and projection link authority.
- Slices 02-08 provide workspace, contract, sandbox, dispatch, gate, failure,
  merge, and commit evidence.
- Slice 09 provides scheduler feedback evidence.
- Slice 10 provides bounded supervisor/dashboard summaries.
- Slice 12 provides the landing record and acceptance metrics.
