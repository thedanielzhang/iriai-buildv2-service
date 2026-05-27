# 15. Governance Metrics And Scoring

## Objective

Define normalized metrics and scoring rules that let the governance tool compare
features, waves, lanes, runtimes, verifier policies, scheduler policies, and
implementation-plan quality without overreacting to noisy or incomplete data.

Metrics are throughput-oriented but correctness-gated. They must never encourage
policy changes that violate dependencies, write-set safety, sandbox isolation,
merge proof, checkpoint proof, or bounded resource use.

## Current Code Citations

- Existing sizing metric aggregation: [_lane_stats_from_metrics](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:952).
- Existing process improvement builder: [identify_process_improvements](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1011).
- Bounded event metrics query: [_fetch_metric_events](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1514).
- Bounded artifact summary metrics query: [_fetch_artifact_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1534).
- Metrics build path: [_build_sizing_outputs](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1554).
- Scheduler feedback data flow: [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:527).
- Atomic landing metric model: [WorkflowImprovementMetrics](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:120).

## Current Failure Mode From `8ac124d6`

Post-G44 regrouping improved some checkpoint behavior while normalized
throughput sometimes looked worse. The workflow needs better metrics that
separate product complexity, wave size, verifier cost, repair cycles, commit
drag, provider failures, and governance confidence. Otherwise the scheduler can
optimize for a visible metric while worsening overall delivery.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 00 fixture metrics and replay tests.
- Slice 05 dispatcher logs for attempt duration and runtime/provider failure
  semantics.
- Slice 06 gate logs for verify duration, stale-context detection, and verifier
  crash classification.
- Slice 07 failure-router logs for taxonomy and retry-budget decisions.
- Slice 08 merge queue logs for merge, commit, no-dirty, and checkpoint
  durations.
- Slice 09 scheduler feedback logs and acceptance tests.
- Slice 12 acceptance metrics and landing go/no-go record.

Compatible deviations:

- Metric names may be renamed if the journal records a migration map and old
  review artifacts remain readable.
- Additional metrics are allowed when they cite evidence quality and do not feed
  policy decisions until calibrated.

Blocking deviations:

- Metrics rely on artifact timestamps when typed attempt/queue/gate timing
  exists.
- Active incomplete work is included in completed-throughput averages.
- Any metric lacks data-quality and confidence fields.

## Proposed Interfaces And Types

```python
MetricScopeKind = Literal["feature", "effective_group", "task", "lane", "repo", "runtime", "verifier", "policy"]

class GovernanceMetricDefinition(BaseModel):
    name: str
    version: str
    scope_kind: MetricScopeKind
    numerator: str
    denominator: str
    required_evidence_kinds: list[str]
    active_work_policy: Literal["exclude", "status_only", "separate"]
    confidence_rule: str

class GovernanceMetricValue(BaseModel):
    definition_name: str
    definition_version: str
    scope: dict[str, str]
    value: float | int | None
    unit: str
    confidence: float
    data_quality: EvidenceQuality
    source_mix: dict[str, int] = Field(default_factory=dict)
    evidence_refs: list[GovernanceEvidenceRef]
    exclusions: list[str]

class GovernanceScorecard(BaseModel):
    corpus_id: str
    generated_at: datetime
    metrics: list[GovernanceMetricValue]
    baseline_refs: list[GovernanceEvidenceRef]
    incomplete_scopes: list[dict[str, Any]]
    warnings: list[str]
```

Required v1 metrics:

- `tasks_per_hour`
- `complexity_adjusted_tasks_per_hour`
- `hours_per_task`
- `repair_cycles_per_task`
- `verification_cost_per_task`
- `commit_failures_per_task`
- `stale_context_events_per_task`
- `workspace_unblocks_per_task`
- `runtime_failures_per_attempt`
- `merge_queue_wait_hours`
- `checkpoint_duration_hours`
- `workflow_drag_hours`
- `operator_required_escalations`
- `plan_deviation_count`
- `resolved_p1_p2_review_findings`

## Refactoring Steps

1. Move existing sizing metric definitions into typed governance metric
   definitions after Slice 12.
2. Build a metric extractor over Slice 13 evidence sets, not raw broad artifact
   scans.
3. Define active-work handling per metric. Completed-throughput averages exclude
   active work; status views may include it separately.
4. Add complexity adjustment from pre-execution task-shape inputs only: task
   count, contract path breadth, repo count, barrier type, dependency depth,
   planned verifier-gate count, and declared write-set uncertainty. Do not
   include observed failure classes such as stale projection, commit hygiene,
   provider instability, or queue drag in complexity adjustment; those remain
   workflow-drag metrics.
5. Add confidence scoring from evidence completeness, sample count, freshness,
   typed-vs-legacy source mix, and implementation-log completeness.
6. Store metric scorecards as typed governance rows and bounded review
   projections such as `review:governance-metrics:{corpus_id}`.
7. Add calibration fixtures for `8ac124d6` and at least one simpler feature once
   available.

## Persistence And Artifact Compatibility

- Governance metrics are derived rows. They do not change execution state.
- Metrics cite evidence-set refs and implementation-log anchors, not raw bodies.
- Existing `review:dag-sizing:*` artifacts remain readable and may be imported
  as legacy metric evidence with `data_quality="derived"`.
- Scorecards must include metric definition versions so later changes do not
  silently rewrite historical meaning.

## Edge Cases And Failure Handling

- Insufficient samples: emit metric with `value=None` or conservative confidence,
  and block policy recommendations that require the metric.
- Mixed legacy and typed evidence: set `data_quality="derived"`, add
  `source_mix={"typed": n, "legacy": n}` metadata, lower confidence when typed
  evidence is incomplete, and prefer typed rows where possible.
- Provider outage: count as runtime/provider failure, not product repair.
- Overlapping failures: allocate lost time to one primary class plus secondary
  contributing classes to avoid double-counting.
- Incomplete implementation journal: plan-deviation and governance-confidence
  metrics are insufficient until the journal gap is resolved.

## Tests

- Completed groups are included in throughput averages; active groups are
  excluded or marked status-only.
- `8ac124d6` fixture metrics reproduce known baseline classes without broad
  artifact body reads.
- Complexity-adjusted throughput changes when repo count, contract count, and
  verifier gate count change.
- Confidence drops for missing typed evidence, stale projection lineage, and
  incomplete implementation logs.
- Metric version changes do not overwrite historical scorecards.
- Policy consumers cannot read metrics with insufficient confidence as
  executable recommendations.

## Acceptance Criteria

- Every metric has a definition, version, unit, evidence refs, confidence, and
  active-work policy.
- Metrics can compare pre-change and post-change windows without misleading
  active work.
- Metrics distinguish product complexity from workflow drag.
- Implementation journal/log quality affects governance confidence.
- No metric depends on unbounded artifact/event body scans.

## Rollout And Rollback Notes

This is an analytical slice. Rollback disables metric generation and keeps
historical scorecards as review artifacts. It must not change scheduler caps,
failure routing, or workflow policy without Slice 17 policy artifacts.

## Cross-Slice Dependencies

- Slice 13 provides evidence sets.
- Slice 14 provides commit and line provenance metrics.
- Slice 05 provides runtime attempt evidence.
- Slice 06 provides gate/verifier evidence.
- Slice 07 provides failure taxonomy.
- Slice 08 provides merge/commit/checkpoint timing.
- Slice 09 provides scheduler feedback baselines.
- Slice 12 provides acceptance metrics and landing records.

## Slice 13A Shared Completeness Model Dependency

Per **doc-13a:285-287 § Refactoring Steps step 9** — *"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally."* — this
slice's "evidence completeness" confidence inputs (see
`15-governance-metrics-and-scoring.md` § Refactoring Step 5, line 131:
*"Add confidence scoring from evidence completeness, sample count,
freshness, typed-vs-legacy source mix, and implementation-log
completeness."*) and the metric scorecard's typed-vs-legacy source-mix
discrimination depend on the Slice 13A shared completeness model.

Source-of-truth modules:

- `src/iriai_build_v2/execution_control/completeness.py` (Slice 13A
  2nd sub-slice) — `CompletenessState`, `EvidenceCompleteness`,
  `AuthoritativeContextRef`, `EvidencePageRef`, `ExactEvidenceManifest`,
  `compute_completeness_digest`.
- The shared `EvidenceCompleteness` Pydantic model is the source-of-truth
  shape that metric extractors consume to compute the
  evidence-completeness confidence input. Metric extractors must not
  re-derive completeness from raw artifact bodies or compatibility
  projections alone; they must consume the typed `EvidenceCompleteness`
  attached to the governance evidence-set refs.
- The shared `ExactEvidenceManifest` is the source-of-truth shape for
  the "typed-vs-legacy source mix" discrimination: rows backed by an
  `ExactEvidenceManifest` are typed; rows backed only by a
  compatibility-projection summary count as legacy/derived for the
  scorecard's `source_mix` dict (per Slice 13's
  `GovernanceEvidenceSet.source_mix: dict[EvidenceAuthority, int]`).
- The shared `AuthoritativeContextRef` is the source-of-truth shape
  for metric scorecards that cite a specific evidence ref's
  completeness; the metric extractor must not store a bare ref id
  without the typed completeness wrapper.

Per-purpose adapter modules consumed (READ-ONLY references):

- `src/iriai_build_v2/execution_control/dispatcher_prompt_context.py`
  (Slice 13A 4th sub-slice) — the metric extractor must not consume
  prompt-context evidence whose typed
  `AuthoritativePromptContextRouting` reports
  `runtime_context/context_incomplete`; such evidence is excluded
  from confidence scoring per the fail-closed rule at
  doc-13a:269-272.
- `src/iriai_build_v2/execution_control/gate_companion.py` (Slice 13A
  5th sub-slice) — gate-derived metric inputs (gate verdict timing,
  retry counts, proof rows) must consume the typed
  `AuthoritativeGateCompanionRecord` + `AuthoritativeGateProofRow`
  shapes; summary-only gate metrics cannot feed confidence scoring.
- `src/iriai_build_v2/execution_control/snapshot_companion.py`
  (Slice 13A 6th sub-slice) — snapshot-derived metric inputs
  (resource snapshots, scheduler-feedback snapshots) must consume the
  typed `AuthoritativeSnapshotListFieldCompleteness` shape so the
  per-list-field completeness disciplines confidence scoring.

Per P3-13A-6-3 and Slice 19A source-of-truth
`19a-governance-implementation-reassessment.md` (`19A-P2-001`), the current
dashboard wrapper is display/advisory-only and does not let metric extractors
consume 13A typed completeness as execution authority for confidence scoring.
Authority use must wait for a future source-of-truth slice that wires an actual
authoritative consumer with durable failure observation.

This dependency-reconciliation reference was added by
**Slice 13A 8th sub-slice 13An-1** (this iteration) per
doc-13a:285-287 step 9.
