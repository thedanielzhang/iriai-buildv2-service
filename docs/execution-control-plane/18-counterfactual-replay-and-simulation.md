# 18. Counterfactual Replay And Simulation

## Objective

Plan a replay and simulation layer that evaluates whether alternative workflow
policies would have reduced drag on historical executions. The first corpus is
`8ac124d6`, supplemented by Slice 00-12 implementation artifacts once the
execution control plane lands.

Replay is used to validate recommendations before they influence future
workflow behavior. It is not proof that a policy is safe to activate by itself.

## Current Code Citations

- Static replay fixture test entrypoint: [test_execution_control_plane_fixture_replay.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_execution_control_plane_fixture_replay.py:13).
- Fixture bounded-body contract: [feature_8ac124d6 README](/Users/danielzhang/src/iriai/iriai-build-v2/tests/fixtures/execution_control_plane/feature_8ac124d6/README.md:1).
- Current metric and recommendation builder path: [_build_sizing_outputs](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1554).
- Scheduler feedback advisory artifact: [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:560).
- Atomic landing CI matrix: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:279).

## Current Failure Mode From `8ac124d6`

The team has repeatedly needed to ask whether a change would have saved time:
smaller waves, larger waves, deterministic alias routing, ACL normalization,
commit hygiene routing, and supervisor improvements. Without replay, those
answers remain anecdotal. The governance tool should test proposed policies
against known history before they become future workflow recommendations.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 00 fixtures and collector audits.
- Slice 01 reconstruction and projection parity tests.
- Slice 07 route-decision logs and retry-budget records.
- Slice 08 merge queue status/recovery logs.
- Slice 09 scheduler feedback logs, derived overlay tests, and sizing
  recommendation outputs.
- Slice 12 landing metrics and acceptance records.
- All Slice 00-12 reviewer findings and accepted deviations, because simulation
  must model implementation reality, not only the original plan.

Compatible deviations:

- Replay may start with deterministic summary-level simulation when full event
  replay is not available, if validity limits are explicit.
- Counterfactual duration estimates may be ranges rather than exact values.

Blocking deviations:

- Replay silently drops evidence gaps.
- Counterfactual output is consumed as policy without validity limits.
- Replay mutates live execution state or active artifacts.

## Proposed Interfaces And Types

```python
ReplayMode = Literal["event_replay", "summary_replay", "hybrid"]

class ReplayCorpus(BaseModel):
    corpus_id: str
    feature_ids: list[str]
    evidence_set_ids: list[str]
    implementation_anchor_ids: list[str]
    mode: ReplayMode
    validity_limits: list[str]

class CounterfactualScenario(BaseModel):
    scenario_id: str
    policy_under_test: dict[str, Any]
    baseline_policy_refs: list[str]
    affected_consumers: list[PolicyConsumer]
    required_evidence_kinds: list[str]
    assumptions: list[str]

class CounterfactualResult(BaseModel):
    result_id: str
    result_version: str
    scenario_id: str
    corpus_id: str
    assumptions: list[str]
    validity_limits: list[str]
    policy_provenance_refs: list[str]
    safety_guard_class: str | None = None
    estimated_delta_hours: float | None
    estimated_delta_repair_cycles: float | None
    estimated_delta_commit_failures: float | None
    estimated_risk_change: Literal["lower", "same", "higher", "unknown"]
    confidence: float
    invalidated_by: list[str]
    supporting_finding_ids: list[str]
    recommended_next_step: Literal["discard", "collect_more_evidence", "draft_policy", "implementation_plan"]
```

Initial scenarios:

- Larger waves for low-risk UI/test lanes.
- Smaller waves for backend/generated-output lanes.
- Alias canonicalization before verifier context generation.
- ACL normalization before dispatch.
- Commit hygiene route before broad product repair.
- Runtime/provider retry before product RCA.
- Reduced verifier lenses for low-risk lanes.
- Extra raw gates for high-risk generated outputs.

## Refactoring Steps

1. Build replay corpus loader over Slice 13 evidence sets and Slice 00 fixtures.
2. Add scenario definitions with required evidence and validity limits.
3. Implement summary replay first for metrics-level counterfactuals.
4. Add event replay where typed attempt, gate, failure, queue, and checkpoint
   transitions are available.
5. Compare baseline vs scenario outcomes using Slice 15 metrics.
6. Emit counterfactual results as typed governance rows and review artifacts.
7. Require Slice 17 recommendations to cite counterfactual results for any
   behavior-changing policy.

## Persistence And Artifact Compatibility

- Replay results are review/governance artifacts only.
- Replay must not write `dag-*` execution authority artifacts or active policy
  markers.
- Replay inputs include implementation-log anchors so accepted deviations and
  review findings can explain why a policy did or did not work.
- Historical replay is immutable by corpus id and scenario id. New assumptions
  require a new result version.

## Edge Cases And Failure Handling

- Missing typed timing: use summary replay with lower confidence.
- Policy requires evidence not in corpus: mark invalidated and collect more
  evidence.
- Product defect dominates window: do not infer workflow policy success from a
  product-blocked group without separate workflow evidence.
- Small sample size: report confidence and avoid policy recommendations.
- Overfit risk: require at least one non-`8ac124d6` corpus before marking a
  general policy high confidence. A safety-guard exception is allowed only for
  policies whose sole effect is to fail closed earlier, reduce mutation
  authority, or add bounded preflight evidence. The scenario must set
  `safety_guard_class`, cite non-governance primary evidence, and pass a
  chain-depth check proving it is not derived solely from prior governance
  recommendations. Safety-guard exceptions still cannot activate runtime policy
  without Slice 17 consumer-owned activation.

## Tests

- Replay corpus loader rejects malformed or unbounded fixture inputs.
- Summary replay excludes active incomplete groups from completed metrics.
- Event replay preserves transition order and idempotency keys.
- Counterfactual scenarios with missing evidence return invalidated results.
- Known `8ac124d6` scenarios produce deterministic result ids.
- Policy recommendation builder refuses behavior-changing recommendations
  without counterfactual result refs.
- Safety-guard exception tests reject self-labeled policies, governance-only
  provenance chains, and policies that increase mutation authority.

## Acceptance Criteria

- Counterfactuals are deterministic, versioned, and evidence-backed.
- Every result lists assumptions and validity limits.
- Replay cannot mutate live workflow state.
- Recommendations that affect runtime behavior cite replay results or explicitly
  say more evidence is needed.
- The replay corpus includes both `8ac124d6` evidence and Slice 00-12
  implementation artifacts.

## Rollout And Rollback Notes

Rollback disables simulation commands and leaves replay results as historical
review artifacts. Bad scenario logic is superseded by a new scenario version
rather than rewriting past results.

## Cross-Slice Dependencies

- Slice 13 supplies evidence sets.
- Slice 15 supplies metrics.
- Slice 16 supplies findings.
- Slice 17 consumes replay results for recommendations.
- Slice 00 supplies the initial `8ac124d6` corpus.
- Slice 12 supplies acceptance metrics and implementation artifacts.

## Slice 13A Shared Completeness Model Dependency

Per **doc-13a:285-287 § Refactoring Steps step 9** — *"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally."* — this
slice's counterfactual replay and simulation evidence depends on the
Slice 13A shared completeness model. Replay-result rows and scenario
fixtures cite typed governance evidence refs whose `CompletenessState`
governs whether the replay can produce a high-confidence result or
must mark itself invalidated.

Source-of-truth modules:

- `src/iriai_build_v2/execution_control/completeness.py` (Slice 13A
  2nd sub-slice) — `CompletenessState`, `EvidenceCompleteness`,
  `AuthoritativeContextRef`, `EvidencePageRef`, `ExactEvidenceManifest`,
  `compute_completeness_digest`. Replay fixtures cite typed evidence
  refs (via `AuthoritativeContextRef`); refs whose completeness-state
  is `preview_only` or `unavailable` cannot supply replay-input
  evidence.
- The shared `ExactEvidenceManifest` is the source-of-truth shape for
  the "Replay corpus loader rejects malformed or unbounded fixture
  inputs" acceptance test (§ Tests). Fixture inputs whose typed
  manifest is missing or incomplete fail closed at load time per the
  doc-13a:18-23 invariant.

Per-purpose adapter modules consumed (READ-ONLY references):

- `src/iriai_build_v2/execution_control/dispatcher_prompt_context.py`
  (Slice 13A 4th sub-slice) — replay scenarios that simulate
  dispatcher behavior must consume the typed
  `AuthoritativePromptContextRouting` shape; scenarios that simulate
  `runtime_context/context_incomplete` outcomes must use the typed
  failure id rather than a string label.
- `src/iriai_build_v2/execution_control/gate_companion.py` (Slice 13A
  5th sub-slice) — replay scenarios that simulate gate behavior must
  consume the typed `AuthoritativeGateCompanionRecord` +
  `AuthoritativeGateProofRow` shapes; scenarios that simulate the
  `verifier_context/companion_record_unavailable` or
  `verifier_context/proof_row_required` typed failure ids must use
  the typed failure ids rather than free-text labels.
- `src/iriai_build_v2/execution_control/snapshot_companion.py`
  (Slice 13A 6th sub-slice) — replay scenarios that simulate
  classifier behavior must consume the typed
  `AuthoritativeSnapshotClassifierRouting` shape; scenarios that
  simulate the `evidence_corruption/list_field_incomplete` or
  `evidence_corruption/classifier_rule_blocked` typed failure ids
  must use the typed failure ids rather than free-text labels.

Per the existing § "Edge Cases And Failure Handling" rule
*"Policy requires evidence not in corpus: mark invalidated and collect
more evidence."* — the "evidence not in corpus" check is precisely the
shared `CompletenessState="unavailable"` state. This dependency
reference makes that mapping explicit.

Per **P3-13A-6-3 dead-until-wired binding statement** (see
`13a-acceptance.md:193-227`), the composite adapter chain must be
wired into a real consumer site before replay results citing 13A typed
completeness can feed Slice 17 policy recommendations as runtime
authority. The wiring is the **Slice 13A 8th sub-slice 13An-2**
deliverable.

This dependency-reconciliation reference was added by
**Slice 13A 8th sub-slice 13An-1** (this iteration) per
doc-13a:285-287 step 9.
