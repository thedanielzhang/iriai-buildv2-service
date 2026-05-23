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
