# 17. Policy Recommendation Interface

## Objective

Define how governance findings become policy recommendations that other workflow
components can consume safely. The recommendation interface lets scheduler
feedback, failure routing, supervisor, dashboard, and future feature planning
learn from governance analysis without granting the analyzer direct mutation
authority.

Governance is advisory by default. Behavior-changing policy updates require an
explicit policy artifact, tests, owner review, and later activation by the
owning workflow component.

## Current Code Citations

- Failure-router dependency and route storage plan: [07-typed-failure-router.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/07-typed-failure-router.md:716).
- Scheduler feedback data flow: [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:527).
- Scheduler feedback projection rule: [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:560).
- Supervisor read-only integration contract: [10-supervisor-dashboard-integration.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/10-supervisor-dashboard-integration.md:3).
- Supervisor action record model: [SupervisorActionRecord](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:425).
- Atomic landing production mode constraints: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:28).

## Current Failure Mode From `8ac124d6`

The workflow learned local fixes such as ACL normalization, alias preflight, and
regroup sizing, but those lessons were encoded as piecemeal patches. The
governance tool needs a formal recommendation interface so future improvements
can be proposed, reviewed, simulated, and consumed without becoming ad hoc
runtime mutations.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 07 logs for the final failure taxonomy, retry budgets, and route
  decision schema.
- Slice 09 logs for scheduler policy fields, activation boundaries, and sizing
  recommendation semantics.
- Slice 10 logs for supervisor/dashboard read-only guarantees and Slack dedupe.
- Slice 12 logs for atomic landing and in-flight adoption policy.
- Slice 13-16 governance docs and implementation logs for evidence, metric, and
  finding shape.

Compatible deviations:

- A consumer may ignore a recommendation if it records a reason and preserves the
  recommendation audit record.
- Policy artifacts may be component-specific, but they must reference the same
  source finding ids and metric ids.

Blocking deviations:

- A governance recommendation can directly alter scheduler, router, merge queue,
  supervisor, or executor behavior.
- A recommendation lacks source findings, confidence, or owner component.
- A consumer treats advisory text as executable policy.

## Proposed Interfaces And Types

```python
PolicyConsumer = Literal["scheduler", "failure_router", "supervisor", "dashboard", "planning", "merge_queue"]
PolicyRecommendationStatus = Literal[
    "draft",
    "reviewed",
    "accepted",
    "rejected",
    "needs_more_evidence",
    "superseded",
]

class GovernancePolicyRecommendation(BaseModel):
    idempotency_key: str
    recommendation_id: str
    consumer: PolicyConsumer
    status: PolicyRecommendationStatus
    source_finding_ids: list[str]
    source_metric_refs: list[str]
    counterfactual_result_refs: list[str]
    confidence: float
    expected_impact: dict[str, float]
    risk_level: Literal["low", "medium", "high"]
    safe_runtime_action: bool
    requires_tests: list[str]
    proposed_policy_artifact: (
        SchedulerPolicyArtifact
        | FailureRouterPolicyArtifact
        | SupervisorPolicyArtifact
        | DashboardPolicyArtifact
        | PlanningPolicyArtifact
        | MergeQueuePolicyArtifact
    )
    activation_requirements: list[str]
    rollback_requirements: list[str]

class PolicyRecommendationDecision(BaseModel):
    recommendation_id: str
    decision: Literal["accept", "reject", "needs_more_evidence"]
    decided_by: str
    decided_at: datetime
    rationale: str
    evidence_refs: list[GovernanceEvidenceRef]

class FailureRouterPolicyArtifact(BaseModel):
    failure_class: str
    failure_type: str
    action: Literal["retry", "repair", "queue_recovery", "quiesce", "operator_required"]
    route_budget_key: str
    max_attempts: int
    idempotency_key_template: str
    required_tests: list[str]

class SchedulerPolicyArtifact(BaseModel):
    policy_kind: Literal["wave_cap", "barrier", "lane_priority"]
    scope: dict[str, str]
    value: dict[str, Any]
    guardrails: list[str]

class SupervisorPolicyArtifact(BaseModel):
    policy_kind: Literal["classification_hint", "dedupe", "digest_priority"]
    scope: dict[str, str]
    value: dict[str, Any]
    read_only: Literal[True] = True

class DashboardPolicyArtifact(BaseModel):
    policy_kind: Literal["view_priority", "alert_threshold", "panel_visibility"]
    scope: dict[str, str]
    value: dict[str, Any]
    read_only: Literal[True] = True

class PlanningPolicyArtifact(BaseModel):
    policy_kind: Literal["future_dag_hint", "contract_template_hint"]
    scope: dict[str, str]
    value: dict[str, Any]
    advisory_only: Literal[True] = True

class MergeQueuePolicyArtifact(BaseModel):
    policy_kind: Literal["lane_priority", "recovery_budget", "commit_gate_hint"]
    scope: dict[str, str]
    value: dict[str, Any]
    required_queue_tests: list[str]
```

Consumer contracts:

- Scheduler runtime behavior consumes only consumer-owned `activated` policy
  records. `accepted` governance recommendations are reviewed/staged evidence
  and may not change scheduling by themselves.
- Failure-router runtime behavior consumes only consumer-owned `activated`
  route-budget or route-priority policy records with replay coverage. `accepted`
  governance recommendations are review inputs only.
- Supervisor and dashboard consume advisory summaries and must remain read-only.
- Planning consumes historical recommendations as context for future DAG design.
- Merge queue consumes only explicit consumer-owned `activated` policy artifacts
  covered by merge queue tests.
- `activated` is deliberately not a `GovernancePolicyRecommendation.status`.
  Activation belongs to a separate consumer-owned policy record with its own
  schema, tests, replay proof, rollback plan, and audit trail. Governance
  recommendations can propose or be accepted for review, but cannot become
  runtime policy by changing their own row status.

## Refactoring Steps

1. Add governance recommendation models and typed storage after findings exist.
2. Add recommendation builders that convert high-confidence findings into
   consumer-specific draft policy artifacts.
3. Add a policy validation interface per consumer. Validation proves the artifact
   can be understood, not that it should be activated.
4. Add decision records for accept/reject/needs-more-evidence.
5. Add replay requirement hooks so any behavior-changing recommendation can point
   to Slice 18 counterfactual results.
6. Add consumer read APIs that return accepted-but-not-activated policy artifacts
   separately from consumer-owned activated policy. Runtime consumers must ignore
   non-activated governance recommendations.
7. Keep activation out of governance v1 unless a later self-healing feature
   explicitly owns activation with tests.

## Persistence And Artifact Compatibility

- Store recommendations as typed governance rows and project review artifacts
  such as `review:governance-recommendations:{corpus_id}`.
- Do not write `dag-regroup-active:*`, route-budget state, supervisor actions, or
  merge queue state from governance recommendation generation.
- If a consumer later activates a policy, it writes its own activation artifact
  and references the recommendation id.

## Edge Cases And Failure Handling

- Low-confidence finding: no recommendation, or recommendation status
  `needs_more_evidence`.
- Conflicting recommendations for one consumer: mark both draft and require
  human or policy owner decision.
- Stale source finding: recommendation cannot be accepted until refreshed.
- Consumer schema changed after recommendation generation: validation fails and
  recommendation becomes stale.
- Safe runtime action false: recommendation can be reported but not consumed by
  runtime policy without a later implementation plan.

## Tests

- Recommendation builder refuses findings below confidence threshold.
- Recommendation includes source finding ids, metric refs, expected impact,
  activation requirements, and rollback requirements.
- Scheduler consumer validation rejects policies that violate dependency,
  write-set, barrier, or safety constraints.
- Failure-router consumer validation rejects untested route changes.
- Supervisor/dashboard consume summaries without mutation capability.
- Accepted recommendation cannot change runtime behavior or become activated
  without consumer-owned activation evidence.

## Acceptance Criteria

- Governance recommendations are typed, evidence-backed, and consumer-scoped.
- Recommendation generation has no direct mutation authority.
- Behavior-changing recommendations require explicit policy artifacts and tests.
- Consumers can ignore or reject recommendations with durable rationale.
- The interface is compatible with Slices 07, 09, 10, and 12 implementation logs.

## Rollout And Rollback Notes

Rollback disables recommendation generation and consumer read APIs. Existing
recommendation artifacts remain historical audit records. Activated policy
rollback belongs to the owning consumer, not the governance analyzer.

## Cross-Slice Dependencies

- Slice 13 supplies evidence refs.
- Slice 15 supplies metrics and confidence.
- Slice 16 supplies findings.
- Slice 07 owns failure routing policy.
- Slice 09 owns scheduler policy.
- Slice 10 owns supervisor/dashboard consumption.
- Slice 12 owns activation and adoption constraints.

## Slice 13A Shared Completeness Model Dependency

Per **doc-13a:285-287 § Refactoring Steps step 9** — *"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally."* — this
slice's policy recommendation interface depends on the Slice 13A
shared completeness model in two specific places:

1. Every `GovernancePolicyRecommendation.source_finding_ids` /
   `source_metric_refs` / `counterfactual_result_refs` ultimately
   chains back to typed governance evidence whose
   `CompletenessState` is one of the shared 4 values (`complete`,
   `paged`, `preview_only`, `unavailable`). A recommendation cannot
   activate runtime policy from evidence whose completeness-state is
   `preview_only` or `unavailable`; that fail-closed disposition is
   the doc-13a:18-23 invariant.
2. Per **doc-13a:285-287 explicitly names Slice 17** as one of the
   slices that MUST consume the shared completeness model. Slice 19A
   later reopened the P3-13A-6-3 dashboard-wrapper authority claim as
   `19A-P2-001`;
   until a future source-of-truth slice wires an actual authoritative
   consumer with durable failure observation, the Slice 13A typed
   surfaces are advisory inputs for recommendations and not runtime
   activation authority.

Source-of-truth modules:

- `src/iriai_build_v2/execution_control/completeness.py` (Slice 13A
  2nd sub-slice) — `CompletenessState`, `EvidenceCompleteness`,
  `AuthoritativeContextRef`, `EvidencePageRef`, `ExactEvidenceManifest`,
  `compute_completeness_digest`.
- `src/iriai_build_v2/execution_control/gate_companion.py` (Slice 13A
  5th sub-slice) — `AuthoritativeGateProofRow` is the only typed
  shape by which a deterministic summary can satisfy a required gate
  per doc-13a:276-278. Policy recommendations that would activate
  runtime behavior must cite typed proof rows, not summary-only
  evidence.
- `src/iriai_build_v2/execution_control/snapshot_companion.py` (Slice
  13A 6th sub-slice) — `AuthoritativeSnapshotClassifierRouting` carries
  the fail-closed disposition for classifier rules consuming incomplete
  snapshot fields. Per doc-13a:280-282, classifier rules MUST fail
  closed when their required snapshot fields are incomplete; policy
  recommendations that consume classifier-driven evidence must respect
  the same fail-closed disposition.
- `src/iriai_build_v2/execution_control/dispatcher_prompt_context.py`
  (Slice 13A 4th sub-slice) — policy recommendations that would
  alter dispatcher-prompt context budgets (e.g. raising
  `required_complete_for` for a specific task class) must consume
  the typed `AuthoritativePromptContextRouting` shape so the
  recommendation's expected impact on
  `runtime_context/context_incomplete` failure routing is auditable.

Per P3-13A-6-3 and Slice 19A source-of-truth
`19a-governance-implementation-reassessment.md` (`19A-P2-001`), the current
dashboard wrapper is display/advisory-only and does not let any Slice 17 policy
recommendation claim runtime activation authority. Authority use must wait for
a future source-of-truth slice that wires an actual authoritative consumer with
durable failure observation.

This dependency-reconciliation reference was added by
**Slice 13A 8th sub-slice 13An-1** (this iteration) per
doc-13a:285-287 step 9.
