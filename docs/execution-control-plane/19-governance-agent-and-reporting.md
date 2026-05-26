# 19. Governance Agent And Reporting

## Objective

Define the governance CLI/API, dashboard integration, Slack/report output, and
agent-readable summaries. The main consumer is the workflow itself, so structured
governance records are primary and prose reports are secondary.

Governance reporting must be bounded, reproducible, evidence-cited, and
compatible with the supervisor/dashboard read-only contract.

## Current Code Citations

- Supervisor observation input model: [SupervisorObservation](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:201).
- Supervisor compact persisted digest: [SupervisorObservationDigest](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:253).
- Supervisor agent assessment record: [SupervisorAgentAssessmentRecord](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:365).
- Supervisor classifier order: [SupervisorClassifier.classify](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:23).
- Dashboard/supervisor integration objective: [10-supervisor-dashboard-integration.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/10-supervisor-dashboard-integration.md:3).
- Landing record requirements: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:260).

## Current Failure Mode From `8ac124d6`

Supervisor messages were useful but sometimes repetitive, degraded, or focused
on immediate state rather than longer-run workflow governance. Governance
reporting must provide compact structured context for agents and clear human
summaries without becoming another noisy Slack channel or broad-read risk.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 10 implementation logs for bounded snapshot APIs, MCP constraints,
  Slack dedupe, read-only supervisor guarantees, and dashboard payload budgets.
- Slice 12 acceptance logs for operational go/no-go records and report
  expectations.
- Slice 13-18 governance logs for evidence, metrics, findings,
  recommendations, and replay result shapes.
- Actual implementation journal entries for subagent review loops and accepted
  deviations so reports can explain plan-vs-actual evidence.

Compatible deviations:

- CLI names may differ if discoverability and typed output contracts are
  preserved.
- Human prose can be customized by channel, but structured JSON output must stay
  stable.

Blocking deviations:

- Reporting reads full artifact bodies by default.
- Governance agent can mutate executor or product state.
- Slack/report dedupe is missing for repeated identical findings.

## Proposed Interfaces And Types

CLI commands:

```bash
python -m iriai_build_v2.workflows.develop.governance analyze --feature-id <id>
python -m iriai_build_v2.workflows.develop.governance report --feature-id <id>
python -m iriai_build_v2.workflows.develop.governance explain-line --repo-id <repo> --path <path> --line <n>
python -m iriai_build_v2.workflows.develop.governance compare --baseline <corpus> --candidate <corpus>
```

API models:

```python
class GovernanceSnapshot(BaseModel):
    corpus_id: str
    snapshot_version: str
    snapshot_digest: str
    generated_at: datetime
    scorecard_id: str
    max_response_bytes: int
    truncated: bool
    omitted_counts: dict[str, int]
    completeness: CompletenessState
    page_refs: list[GovernanceEvidencePageRef]
    next_cursor: str | None = None
    top_findings: list[GovernanceFinding]
    recommendations: list[GovernancePolicyRecommendation]
    replay_results: list[CounterfactualResult]
    evidence_quality: EvidenceQuality
    blocked_by: list[str]

class ContextLayerPackageSummary(BaseModel):
    package_id: str
    package_digest: str
    package_ref: GovernanceEvidenceRef
    source_dag_artifact_id: int
    dag_sha256: str
    typed_evidence_digest: str
    provider_state_digest: str
    advisory_only: Literal[True] = True
    omitted_counts: dict[str, int]
    page_refs: list[GovernanceEvidencePageRef]
    completeness: CompletenessState
    truncated: bool

class GovernanceAgentContext(BaseModel):
    task_id: str | None
    repo_id: str | None
    context_package: ContextLayerPackageSummary | None = None
    relevant_findings: list[GovernanceFinding]
    relevant_line_provenance: list[LineProvenanceResult]
    policy_guidance: list[GovernancePolicyRecommendation]
    policy_guidance_authority: Literal["advisory_only"] = "advisory_only"
    omitted_detail_refs: list[GovernanceEvidencePageRef]
    omitted_counts: dict[str, int]
    completeness: CompletenessState
    page_refs: list[GovernanceEvidencePageRef]
    truncated: bool
    max_prompt_chars: int
```

Default response budgets:

- Governance snapshot: 256 KB serialized JSON, 20 findings, 10 recommendations,
  10 replay results, and exact page-ref pagination for additional rows.
- Slack digest: 40 KB serialized Block Kit payload and 5 top findings.
- Agent context: `max_prompt_chars` from caller, hard-capped at 20,000 chars,
  with omitted refs instead of full evidence bodies. After Slice 21, this
  response must include `ContextLayerPackageSummary` so agents and gates can
  cite and stale-check the exact context package they consumed.
- Reporting budgets are preview/display budgets. Any truncated snapshot or
  agent context must include exact `GovernanceEvidencePageRef` rows plus
  `completeness`; without those refs the response is display-only and cannot
  feed acceptance, recommendations, policy guidance, or task-execute context.
- Dashboard detail panes fetch bounded slices by evidence ref and never expand
  full artifact bodies by default.

Reporting surfaces:

- Dashboard governance tab with evidence quality, top findings, trend metrics,
  recommendation status, replay confidence, and implementation-plan deviation
  summary.
- Slack digest with dedupe key from `snapshot_digest`, not only corpus id or top
  ids, so material changes in evidence quality, replay confidence, omitted
  detail counts, or implementation-deviation summaries are not suppressed.
- Agent context endpoint that returns compact governance context for a task,
  repo, file, or line range. After Slice 21, this endpoint is backed by the
  IriAI Context Layer package API rather than independently assembling
  provider-specific context.

## Refactoring Steps

1. Add governance CLI with JSON output first and prose rendering second.
2. Add typed snapshot API that reads governance rows and bounded evidence refs.
   The API computes `snapshot_digest` from bounded row ids, row digests,
   omitted-counts, evidence-quality values, and recommendation/replay versions.
3. Add dashboard view that consumes governance snapshots only.
4. Add Slack rendering with dedupe and rate limiting inherited from Slice 10
   patterns.
5. Add agent-context builder that selects findings and provenance relevant to a
   task contract, repo, path, or line range. After Slice 21, this builder must
   call the Context Layer package API and return `ContextLayerPackageSummary`
   rather than assembling uncited provider context locally.
6. Add report artifacts such as `review:governance-report:{corpus_id}` with
   bounded summary only.
7. Keep governance agent/tooling read-only. If future self-healing is added, it
   must use separate policy activation docs.

## Persistence And Artifact Compatibility

- Governance reports are projections of governance rows.
- Supervisor records may cite governance finding ids, but supervisor remains
  read-only/advisory.
- Dashboard reads governance snapshots with bounded fields and ETags; it does
  not resolve full evidence bodies by default. The ETag seed is
  `snapshot_digest`.
- Agent `policy_guidance` is prompt context only. It cannot override task
  contracts, gate requirements, failure-router policy, merge-queue policy, or
  any activated consumer policy artifact from Slice 17.
- Agent context references omitted detail refs for drilldown rather than
  embedding large evidence.
- After Slice 21, agent context persists and returns the consumed context package
  id, package digest, source DAG artifact id, DAG sha, typed evidence digest, and
  provider state digest. This is required so prompts, gates, and reports can
  cite or reject stale package context after restart.

## Edge Cases And Failure Handling

- Governance snapshot stale: report stale status and do not present new
  recommendations as current.
- Missing line provenance: show provenance gap finding, not a blank answer.
- Too many findings: rank by severity, confidence, lost-time estimate, and
  recency; include omitted refs.
- Slack delivery failure: keep report artifact and retry via existing outbox
  policy if configured.
- Active workflow pressure: reporting returns cached snapshots instead of
  forcing expensive recomputation.

## Tests

- CLI emits stable JSON and nonzero exit for blocked evidence.
- Dashboard snapshot payload respects byte budgets and contains no full artifact
  bodies.
- Slack digest dedupes repeated identical governance snapshots by
  `snapshot_digest` and emits material updates when the digest changes.
- Agent context builder returns task-relevant findings and line provenance under
  prompt budget.
- After Slice 21, agent context builder consumes `ContextLayerPackage`, returns
  `ContextLayerPackageSummary`, and tests that package id/digest, source DAG,
  typed evidence digest, provider state digest, omitted counts, and
  advisory-only marker are preserved.
- After Slice 21, stale or mismatched context package summaries are rejected for
  gate use and rendered as stale in reports, not silently reused.
- After Slice 21, provider output remains advisory until mapped by the IriAI
  lineage plugin.
- Truncated reports include exact page refs and completeness metadata; missing
  page refs make the report display-only and block recommendation/acceptance
  consumption.
- Agent context marks policy guidance advisory-only and tests that prompts
  cannot treat it as activated policy.
- Report generation is reproducible for the same corpus id.
- Governance agent/tooling cannot mutate workflow, product, merge queue, or
  supervisor action state.

## Acceptance Criteria

- Reports are bounded, reproducible, evidence-cited, and structured first.
- Truncated or preview reports are never authoritative unless exact page refs
  and completeness metadata cover the consumer's required scope.
- Workflow agents can receive compact governance context at task execute time.
- After Slice 21, every context response that uses line/context-layer provenance
  carries a citeable context package id and digest.
- Workflow agents receive governance policy guidance only as advisory context;
  contracts, gates, router, and merge queue remain authoritative.
- Human-facing dashboard/Slack output explains top findings without hiding
  evidence quality or omitted details.
- Reporting honors Slice 10 read-only and bounded-read guarantees.
- Implementation-log anchors are visible in plan-vs-actual reports.

### Acceptance Criteria — Activation-Authority Boundary Elaboration

The Slice 10 read-only and bounded-read guarantees enumerated in the bullet
*"Reporting honors Slice 10 read-only and bounded-read guarantees"* above
expand, for the Slice 19 governance agent + reporting layer specifically,
into the following enumerated activation-authority-boundary acceptance
criteria. Every one of these is a hard structural requirement that the
typed-shape layer, the typed projection layer, the typed read-only API
surface, the typed Slack/dashboard rendering layer, the typed agent-context
builder layer, the typed report-artifact emitter layer, and the typed CLI
layer (8th sub-slice) MUST preserve at module-import time and across every
public method call:

- The governance agent + reporting layer is READ-ONLY / ADVISORY for the
  workflow's primary mutation authorities (executor / dispatcher / verify
  routing / merge queue / regroup overlay / supervisor action state /
  failure router route table). Governance reads typed Slice 13-18 surfaces
  and projects bounded, refs-only, exact-vs-preview-distinguished snapshots
  for advisory consumption; it never mutates a primary-authority surface.
- The governance agent + reporting layer's typed BaseModels are
  `model_config = ConfigDict(extra="forbid")` across the surface (no silent
  field gain; no schema drift; no silent migration of in-flight features).
- The governance agent + reporting layer's typed read-only API surface
  exposes a narrow public-method roster per class (1-2 public methods on
  each emitter / projector / renderer / builder / view). No mutation
  methods on any BaseModel; no writer-call patterns on any typed surface.
- The governance agent + reporting layer's typed artifact-key prefix for
  report artifacts is `review:*` (specifically
  `REPORT_ARTIFACT_KEY_PREFIX = "review:governance-report:"` as a typed
  Literal). The layer does NOT emit any `dag-*` artifact keys; this
  partitions the report-artifact identity space from the primary
  dispatcher / verify / merge / regroup artifact-key space.
- The governance agent + reporting layer's typed
  `policy_guidance_authority: Literal["advisory_only"]` field on
  `GovernanceAgentContext` (per the doc-19:103-117
  `GovernanceAgentContext` shape) enforces, at the typed-shape layer, that
  prompts and downstream consumers cannot treat the policy guidance as
  activated policy. Pydantic ValidationError fires on any other Literal
  value; the field's hardcoded default is `"advisory_only"` everywhere the
  builder constructs the typed shape.
- The governance agent + reporting layer's typed Slack rendering and
  dashboard view consume only typed `GovernanceSnapshot` rows + bounded
  evidence refs. Dashboard ETag seed is `snapshot_digest` per the
  doc-19:171-173 dashboard contract. Slack dedupe key is `snapshot_digest`
  per the doc-19:140-142 Slack contract.
- The governance agent + reporting layer's typed agent-context builder
  enforces the doc-19:124-127 hard cap (20 000 chars) on the
  `max_prompt_chars` field of `GovernanceAgentContext`. The builder is
  stateless; reusing the same instance produces consistent results;
  prompt-budget enforcement is iterative truncation with exact omitted
  refs.
- The governance agent + reporting layer's typed shapes propagate the
  Slice 13A shared completeness model (`CompletenessState` enum +
  `EvidencePageRef` shape + `EvidenceCompleteness` record) into every
  reporting surface. The exact-vs-preview distinction per doc-13a:128-131
  is preserved structurally: every truncated snapshot or agent context
  carries exact `GovernanceEvidencePageRef` rows and `completeness`;
  without those refs the response is display-only and cannot feed
  acceptance / recommendations / policy guidance / task-execute context.
- The governance agent + reporting layer's typed CLI (8th sub-slice per
  doc-19:59-66 + step 1 line 150 + doc-19:198 test contract) emits stable
  JSON first and prose second, returns nonzero exit codes for blocked
  evidence, and projects from the typed `GovernanceSnapshot` /
  `GovernanceAgentContext` / `LineProvenanceResult` / `CounterfactualResult`
  shapes. The CLI does NOT introduce new mutation authority; it does NOT
  extend the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS` set; it does NOT
  emit `dag-*` artifact keys; it reads typed projections only.
- The governance agent + reporting layer's typed surface depends only on
  Slice 01-12 typed contracts plus the typed Slice 13-18 governance
  layer; no Slice 19 module imports `dashboard.py`, `supervisor/actions/`,
  `merge_queue_store.py`, `regroup_overlay_store.py`, or
  `workflows/develop/execution/failure_router.py` writer surfaces.
  Failure-router additions for governance failure ids are 4-pure-data
  add-points (`FAILURE_TYPES`, `ROUTE_TABLE`, `Literal["..."]`, reason
  string) per the Slice 14 2nd + Slice 19 2nd-6th sub-slice precedent;
  the route table is NOT redefined; existing routes are NOT mutated.

### Acceptance Criteria — Supervisor / Dashboard Read-Only Pin

The supervisor / dashboard read-only contract is the single hardest
invariant the Slice 19 governance agent + reporting layer must preserve.
The Slice 10c-1 typed read-only surface declares the
`CONTROL_PLANE_WRITER_METHODS` frozenset at
`src/iriai_build_v2/supervisor/read_only.py` as the authoritative roster
of methods that may mutate the supervisor / dashboard / merge-queue /
regroup-overlay state on behalf of any non-primary subsystem. Per the
Slice 10 read-only and bounded-read guarantees pinned in the bullet
*"Reporting honors Slice 10 read-only and bounded-read guarantees"*
above, the Slice 19 governance layer is one such non-primary subsystem:
it reads typed Slice 13-18 surfaces, projects bounded snapshots, and
renders dashboard / Slack / agent-context / report-artifact / CLI
surfaces on top of those projections. None of the Slice 19 typed surfaces
may EXTEND the `CONTROL_PLANE_WRITER_METHODS` frozenset; none may
WIDEN the writer-method roster; none may MUTATE the frozenset in any
way (including via `add` / `update` / augmented assignment / direct
re-binding). The Slice 19 7th sub-slice test surface enforces this
STRUCTURALLY via runtime frozenset-identity assertions BEFORE + AFTER
all 6 Slice 19 source modules are imported, and AST-based scans for
mutation patterns on the symbol. The Slice 19 8th sub-slice CLI must
preserve this invariant; it forward-applies the same test surface to
the CLI module once it lands.

This invariant rules out: direct mutations of the frozenset; parallel
writer-method sets under a different name; subclassing a supervisor /
dashboard writer class and overriding its public mutation surface;
importing the writer-side modules from any Slice 19 source file; and
emitting `dag-*` artifact keys (which would imply primary-authority
identity rather than `review:*` advisory identity). The Slice 19 7th
sub-slice test surface flags every such pattern structurally. The
enumerated AC bullet form of this invariant is:

- Supervisor/dashboard read-only contract preserved (no governance writer
  extends the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS` set).
- The slice-wide cross-cutting + forward-applicable test surface at
  `tests/test_execution_control_governance_19_activation_boundary.py`
  (Slice 19 7th sub-slice) asserts ALL of the activation-authority
  boundary criteria above STRUCTURALLY across all 6 Slice 19 source
  modules at module-import time, mirroring the Slice 17 7th sub-slice
  test-only precedent and forward-applying to the Slice 19 8th sub-slice
  CLI module once it lands.

## Rollout And Rollback Notes

Rollback disables governance CLI/API/reporting endpoints and Slack governance
digests. Existing governance report artifacts remain audit history. Do not
delete governance findings or evidence sets during rollback.

## Cross-Slice Dependencies

- Slice 10 supplies supervisor/dashboard contracts.
- Slice 12 supplies landing and acceptance records.
- Slice 13 supplies evidence sets.
- Slice 14 supplies line provenance.
- Slice 15 supplies metrics.
- Slice 16 supplies findings.
- Slice 17 supplies recommendations.
- Slice 18 supplies replay results.
- Slice 21 supplies the provider-backed IriAI Context Layer for task-execute
  context packages.

## Slice 13A Shared Completeness Model Dependency

Per **doc-13a:285-287 § Refactoring Steps step 9** — *"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally."* — this
slice's reporting surfaces (`GovernanceSnapshot`,
`ContextLayerPackageSummary`, `GovernanceAgentContext` — all defined
at lines 71-117) depend on the Slice 13A shared completeness model.
Each shape carries a `completeness: CompletenessState` field and a
`page_refs: list[GovernanceEvidencePageRef]` field that map onto the
shared `CompletenessState` enum + `EvidencePageRef` shape.

Source-of-truth modules:

- `src/iriai_build_v2/execution_control/completeness.py` (Slice 13A
  2nd sub-slice) — `CompletenessState`, `EvidencePageRef`,
  `EvidenceCompleteness`, `ExactEvidenceManifest`,
  `AuthoritativeContextRef`, `compute_completeness_digest`.
- `src/iriai_build_v2/execution_control/snapshot_companion.py` (Slice
  13A 6th sub-slice) — `AuthoritativeSnapshotListFieldCompleteness`
  carries the per-list-field completeness that dashboard truncation
  metadata reports. The doc's existing rule (lines 128-131:
  *"Reporting budgets are preview/display budgets. Any truncated
  snapshot or agent context must include exact `GovernanceEvidencePageRef`
  rows plus `completeness`; without those refs the response is
  display-only and cannot feed acceptance, recommendations, policy
  guidance, or task-execute context."*) is exactly the shared
  preview-vs-exact distinction enforced by the Slice 13A 6th
  sub-slice's snapshot companion record.
- `src/iriai_build_v2/execution_control/dispatcher_prompt_context.py`
  (Slice 13A 4th sub-slice) — `AuthoritativePromptContextRouting` is
  the source-of-truth shape for the `GovernanceAgentContext`
  fail-closed disposition when `required_complete_for` cannot be
  satisfied (per doc-13a:269-272).
- `src/iriai_build_v2/execution_control/gate_companion.py` (Slice 13A
  5th sub-slice) — `AuthoritativeGateProofRow` is the source-of-truth
  shape for any reporting surface that summarizes gate evidence; gate
  summaries that are not backed by a typed proof row remain
  display-only per doc-13a:276-278. The `GovernanceAgentContext`
  `policy_guidance_authority: Literal["advisory_only"]` field aligns
  with the typed gate-companion record's advisory-only disposition
  when the record is missing.

The doc's existing § "Dashboard / Slack / agent context budget" rules
already use the shared `CompletenessState` 4 values implicitly; this
dependency reference makes the source-of-truth pointer explicit and
aligns the reporting-surface shapes with the typed contract enforced
by the Slice 13A 4th-6th sub-slices.

Per **P3-13A-6-3 dead-until-wired binding statement** (see
`13a-acceptance.md:193-227`), the composite adapter chain must be
wired into a real consumer site before reporting surfaces consume 13A
typed completeness as execution authority for the
`task-execute-agent-context` channel. The wiring is the **Slice 13A
8th sub-slice 13An-2** deliverable.

This dependency-reconciliation reference was added by
**Slice 13A 8th sub-slice 13An-1** (this iteration) per
doc-13a:285-287 step 9.
