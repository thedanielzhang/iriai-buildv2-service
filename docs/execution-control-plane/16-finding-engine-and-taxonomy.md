# 16. Finding Engine And Taxonomy

## Objective

Define the governance finding taxonomy and deterministic rule engine. Findings
turn evidence sets and metrics into structured workflow-improvement signals that
can be consumed by humans, agents, scheduler feedback, failure routing, and
future feature planning.

Findings must distinguish product defects, workflow drag, unsafe workflow
behavior, implementation-plan drift, and evidence gaps.

## Current Code Citations

- Existing process-improvement findings: [identify_process_improvements](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1011).
- Existing finding payload shape: [_process_improvement](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:986).
- Supervisor classifier priority order: [SupervisorClassifier.classify](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:23).
- Deterministic unblock classifier source: [SupervisorClassifier._deterministic_unblock](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:254).
- Failure route dependency in Slice 07: [07-typed-failure-router.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/07-typed-failure-router.md:716).
- Scheduler feedback dependencies: [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:814).

## Current Failure Mode From `8ac124d6`

The current process-improvement output can identify classes such as commit
hygiene loops or alias drift, but those findings are not yet a general typed
governance layer. The workflow needs findings that are stable enough for
machine consumption and conservative enough to avoid runaway self-improvement.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 07 implementation logs for final failure-class names, retry-budget
  semantics, and route decisions.
- Slice 09 implementation logs for scheduler feedback fields and cap decisions.
- Slice 10 implementation logs for supervisor classes, Slack dedupe, and
  read-only guarantees.
- Slice 12 acceptance logs for allowed policy/adoption behavior.
- All Slice 00-12 reviewer findings, including resolved P1/P2 and accepted P3
  follow-ups, because repeated implementation-plan deviations are governance
  findings.

Compatible deviations:

- Finding class names can differ from this plan if a migration table maps old
  names and tests assert one canonical emitted class per condition.
- Some findings may start advisory-only if the slice records missing evidence and
  blocks policy consumption.

Blocking deviations:

- Findings can be emitted without evidence refs or log anchors.
- Findings merge product defects and workflow failures into one class.
- A finding can directly mutate scheduler, router, supervisor, or executor state.

## Proposed Interfaces And Types

```python
FindingSeverity = Literal["info", "low", "medium", "high", "critical"]
FindingKind = Literal[
    "workflow_inefficiency",
    "unsafe_route",
    "stale_projection",
    "over_verification",
    "under_verification",
    "task_contract_weakness",
    "scheduler_mismatch",
    "runtime_instability",
    "merge_queue_drag",
    "provenance_gap",
    "implementation_plan_deviation",
    "resource_safety_risk",
    "product_defect_cluster",
    "governance_evidence_conflict",
]

FindingCausalRole = Literal["primary", "contributing", "symptom", "unknown"]

class GovernanceFinding(BaseModel):
    idempotency_key: str
    kind: FindingKind
    class_name: str
    severity: FindingSeverity
    confidence: float
    feature_id: str | None
    affected_scope: dict[str, Any]
    primary_evidence_refs: list[GovernanceEvidenceRef]
    supporting_evidence_refs: list[GovernanceEvidenceRef]
    implementation_log_anchors: list[str]
    metric_refs: list[str]
    estimated_lost_hours: float | None
    estimated_retry_impact: float | None
    recommended_action_display: str
    recommendation_draft_ref: str | None = None
    safe_runtime_action: bool
    requires_policy_artifact: bool
    product_defect_related: bool
    workflow_related: bool
    causal_role: FindingCausalRole
    primary_cause_finding_id: str | None = None
    linked_finding_ids: list[str] = Field(default_factory=list)

class FindingRule(BaseModel):
    rule_id: str
    version: str
    required_metric_names: list[str]
    required_evidence_kinds: list[str]
    min_confidence: float
    emits_kind: FindingKind
```

`recommended_action_display` is non-executable report text. Runtime or workflow
consumers must ignore it for policy changes. Any behavior-changing proposal must
be represented as a separate Slice 17 recommendation draft with its own evidence
refs, review state, and consumer-owned activation path.

Required v1 classes:

- `commit_hygiene_loop`
- `acl_or_writeability_drag`
- `worktree_alias_drift`
- `stale_context_projection`
- `runtime_provider_instability`
- `merge_queue_wait_or_retry_drag`
- `over_verification_low_risk_lane`
- `under_verification_high_risk_lane`
- `scheduler_wave_too_small`
- `scheduler_wave_too_large`
- `task_contract_ambiguity`
- `line_provenance_gap`
- `implementation_journal_gap`
- `accepted_plan_deviation`
- `resource_budget_pressure`
- `governance_evidence_conflict`

Legacy process-improvement class migration table:

| Legacy class | Canonical class |
| --- | --- |
| `commit_hygiene_loops` | `commit_hygiene_loop` |
| `acl_workability_normalization` | `acl_or_writeability_drag` |
| `worktree_alias_canonical_path_drift` | `worktree_alias_drift` |
| `stale_dag_task_projection` | `stale_context_projection` |
| `product_contract_catalog_drift` | `task_contract_ambiguity` |
| `claimed_file_retry_oscillation` | `task_contract_ambiguity` |
| `agent_runtime_stalls_or_failures` | `runtime_provider_instability` |
| `over_verification_on_low_risk_waves` | `over_verification_low_risk_lane` |
| `wave_size_throughput_regression` | `scheduler_wave_too_small` or `scheduler_wave_too_large`, selected by metric direction |

## Refactoring Steps

1. Convert existing process-improvement logic into versioned finding rules after
   the governance evidence and metric layers exist.
2. Add dedupe keys from finding class, feature/window, affected scope, evidence
   digest, and rule version.
3. Add primary-vs-supporting evidence rules. Every finding needs at least one
   primary canonical evidence ref unless it is explicitly an evidence-gap
   finding.
4. Add product/workflow separation. Product defect clusters can be observed, but
   workflow policy recommendations must cite workflow-related causes.
5. Add implementation-plan deviation rules over journal anchors, reviewer
   findings, accepted deviations, and late test failures.
6. Store findings as typed governance rows and project bounded review artifacts
   such as `review:governance-findings:{corpus_id}`.
7. Add suppression/expiry metadata so old findings do not keep driving future
   recommendations after the underlying policy changes.

## Persistence And Artifact Compatibility

- Findings are derived governance records and never write execution `dag-*`
  authority artifacts.
- Findings cite evidence refs, metric refs, and implementation-log anchors.
- Existing `review:dag-process-improvements:*` artifacts can be imported as
  legacy derived evidence but must not be treated as canonical finding rows.
- Finding ids are stable across reruns when input evidence and rule version do
  not change.

## Edge Cases And Failure Handling

- Conflicting evidence: lower confidence and emit a `governance_evidence_conflict`
  finding if conflict affects a policy decision.
- Repeated same-class failure: dedupe within a feature window but include repeat
  count and affected scopes.
- Product defect plus workflow drag: emit separate linked findings; set
  `causal_role`, `primary_cause_finding_id`, and `linked_finding_ids` so
  downstream recommendations can act only on the workflow-related primary or
  contributing cause.
- Missing implementation logs: emit `implementation_journal_gap` and block
  plan-vs-actual recommendations.
- Low confidence: findings may be reported but cannot feed policy artifacts.

## Tests

- Known `8ac124d6` fixture classes emit expected finding kinds and evidence refs.
- Findings dedupe across repeated runs with the same evidence digest.
- Product defect and workflow drag are emitted as distinct linked findings.
- Missing implementation journal emits a gap finding and blocks dependent rules.
- Low-confidence findings do not become policy recommendations.
- Accepted P3 implementation deviation can be reported without blocking
  governance acceptance unless it affects evidence correctness.

## Acceptance Criteria

- Findings are deterministic, typed, deduped, versioned, and evidence-backed.
- Every finding distinguishes workflow-related and product-related impact.
- Every governance recommendation has a source finding and confidence threshold.
- Implementation-plan drift is visible as a governance finding class.
- No finding directly mutates workflow state.

## Rollout And Rollback Notes

This slice is advisory. Rollback disables finding generation and leaves existing
finding artifacts for audit. If a finding rule is bad, release a new rule version
and mark prior findings superseded rather than rewriting history.

## Cross-Slice Dependencies

- Slice 13 supplies evidence refs and implementation-log anchors.
- Slice 15 supplies metric refs and confidence.
- Slice 07 supplies failure classes.
- Slice 09 supplies scheduler policy context.
- Slice 10 supplies supervisor classes and read-only guidance.
- Slice 12 supplies acceptance/adoption constraints.
