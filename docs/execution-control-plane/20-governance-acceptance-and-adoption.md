# 20. Governance Acceptance And Adoption

## Objective

Define the all-at-once acceptance gate for the governance tool after Slices
00-12 complete. This slice proves the governance feature can advise the workflow
without causing unsafe mutation, self-reinforcing bad policy, broad DB reads, or
confusing product defects with workflow improvements.

## Current Code Citations

- Atomic landing contract for execution control plane: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:28).
- In-flight cutover policy for the control plane: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:52).
- Readiness gate table: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:260).
- CI/test matrix pattern: [12-rollout-and-acceptance-matrix.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/12-rollout-and-acceptance-matrix.md:279).
- Slice 19A accepted boundary state: [19a-governance-implementation-reassessment.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/19a-governance-implementation-reassessment.md:11).
- Current Slice 20 active / Slice 21 blocked restart pointer: [STATUS.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/STATUS.md:7).
- Implementation prompt review loop: [IMPLEMENTATION_PROMPT.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/IMPLEMENTATION_PROMPT.md:131).
- Implementation journal current structure: [implementation-journal.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/implementation-journal.md:3).

## Current Failure Mode From `8ac124d6`

The workflow accumulated lessons during execution, but there was no complete
acceptance gate proving that analysis, recommendations, and reporting are safe
as a system. Governance must land as an integrated analytical feature, not as a
set of isolated scripts that can accidentally influence runtime behavior without
evidence, replay, or review.

## Slice 19A Boundary Reconciliation

Slice 19A is accepted and Slice 20 is active. The 19A reassessment result remains
required upstream evidence for this slice, but its historical blockers are closed
unless this slice discovers a new Slice 20 defect. Slice 20 must therefore consume
the accepted boundary state, not re-run 19A remediation:

- No open Slice 19A P1/P2 findings carry into Slice 20.
- The accepted Slice 13A exact/paged completeness, serialized-budget,
  dashboard/read-only, and RouteExecutor fail-closed boundaries are prerequisites
  for Slice 20 acceptance.
- Slice 21 remains blocked until Slice 20 is accepted.
- Active feature `8ac124d6` is evidence only for replay/provenance and must not
  be mutated by governance acceptance or adoption.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Complete Slice 00-12 implementation journal and decision log.
- Slice 12 atomic landing result, accepted post-landing 13A remediation status
  through Slice 19A, and in-flight adoption records.
- All Slice 13-19 implementation logs, reviewer findings, accepted deviations,
  test outputs, and generated review artifacts.
- Governance replay corpus definitions and `8ac124d6` fixture provenance.
- Any P3 follow-ups from Slices 00-19 that touch evidence correctness,
  recommendation safety, bounded reads, or reporting fidelity.

Compatible deviations:

- Governance commands may be split by module as long as the acceptance gate sees
  one complete feature.
- Additional report surfaces are allowed if they are read-only and bounded.

Blocking deviations:

- Any newly discovered or reopened P1/P2 in Slices 00-19 or Slice 20.
- Any governance recommendation can mutate runtime policy directly.
- Governance analysis can run unbounded body scans by default.
- Implementation journal/log review is missing from the acceptance record.

## Proposed Interfaces And Types

```python
class GovernanceAcceptanceResult(BaseModel):
    candidate_id: str
    candidate_commit: str
    passed: bool
    prerequisite_control_plane_landing_id: str
    evidence_model_result: str
    provenance_result: str
    metrics_result: str
    findings_result: str
    recommendation_result: str
    replay_result: str
    reporting_result: str
    implementation_journal_audit_result: str
    implementation_journal_audit_refs: list[ImplementationArtifactAnchor]
    missing_journal_items: list[str]
    unresolved_review_findings: list[str]
    required_tests: list[str]
    blockers: list[str]

class GovernanceAdoptionRecord(BaseModel):
    candidate_id: str
    adopted_at: datetime
    all_read_only_surfaces_enabled: bool
    enabled_surfaces: list[Literal[
        "new_feature_analysis",
        "agent_context",
        "dashboard",
        "supervisor_digest",
        "cli_reporting",
    ]]
    runtime_policy_mutation_allowed: Literal["never"]
    rollback_disposition: str
```

`enabled_surfaces` must contain the complete required surface set listed above.
Partial governance adoption is not supported; if one required surface is
unavailable, the adoption record is not written.

Acceptance artifacts:

- `review:governance-acceptance:{candidate_id}`
- `review:governance-journal-audit:{candidate_id}`
- `review:governance-replay-corpus:{candidate_id}`
- `review:governance-adoption:{candidate_id}`

## Refactoring Steps

1. Add governance acceptance collector after Slices 13-19 exist.
2. Validate that Slices 00-12 are complete and no governance implementation
   starts against a partial control-plane landing. Validate that required 13A
   remediation is accepted through Slice 19A before governance/context surfaces
   consume exact/paged evidence as execution authority.
3. Validate every governance slice has implementation journal entries, decision
   log entries, reviewer dispatches, test outputs, accepted deviations, and no
   open P1/P2 findings.
4. Run the governance test matrix and store result refs.
5. Run replay against `8ac124d6` and the Slice 00-12 implementation corpus.
6. Verify every recommendation is advisory unless a later policy activation
   feature explicitly owns mutation.
7. Write governance acceptance and adoption review artifacts.
8. Enable governance for new-feature analysis, dashboard, supervisor digest, and
   CLI reporting together only after the acceptance record passes.
9. Keep task-execute agent context disabled until Slice 21 lands the exact/paged
   context-package contract and its own acceptance record passes. Slice 19 may
   expose human-readable governance context earlier, but it is display/advisory
   only and must not be used as task execution context.

## Persistence And Artifact Compatibility

- Governance acceptance artifacts are review artifacts, not execution authority.
- Governance adoption enables analytical/read-only surfaces only.
- No active feature is migrated or changed by governance adoption.
- Active feature `8ac124d6` remains replay/provenance evidence only and must not
  be mutated by this slice.
- Existing typed execution rows, commit proofs, Git notes/refs, and governance
  records remain append-only audit history.

## Edge Cases And Failure Handling

- Control plane not landed: governance acceptance fails closed.
- Missing Slice 00-12 logs: governance acceptance fails because plan-vs-actual
  analysis cannot be trusted.
- Replay corpus incomplete: reporting can still show evidence gaps, but policy
  recommendations remain blocked.
- Dashboard unavailable: governance acceptance/adoption fails closed. Local CLI
  validation may still run for diagnosis, but no adoption record is written and
  no governance surface is enabled.
- Post-adoption bad finding rule: supersede the rule version and rerun
  acceptance for the affected slice; do not rewrite old findings.

## Tests

- Acceptance fails when any Slice 00-19 implementation journal section is
  missing.
- Acceptance fails with unresolved P1/P2 reviewer findings.
- Acceptance fails when governance recommendation has mutation authority.
- Acceptance fails when bounded-read tests detect full artifact body scans.
- Acceptance passes with complete evidence, metrics, findings, recommendations,
  replay, reporting, and implementation-journal audit.
- Adoption record enables the complete required analytical/read-only surface set
  or is not written.
- Rollback disables governance surfaces without mutating execution state.

## Acceptance Criteria

- Governance lands only after Slices 00-12, required 13A remediation, and
  Slices 13-19 are complete and accepted through the Slice 19A boundary review.
- The acceptance record audits implementation journals/logs, reviewer findings,
  test outputs, accepted deviations, and replay corpus completeness.
- Governance can advise workflow components and agents without mutating runtime
  policy.
- Dashboard, supervisor, and human-readable governance/reporting context
  surfaces are bounded and read-only. Task-execute agent context remains
  disabled until Slice 21 lands.
- Bad governance rules can be superseded without rewriting history.

## Rollout And Rollback Notes

The governance tool lands as one analytical feature after acceptance passes.
Rollback disables governance ingestion, recommendation, replay, reporting, and
human-readable governance/reporting context surfaces. Task-execute agent context
remains controlled by Slice 21. Rollback must leave governance audit rows and
review artifacts intact for diagnosis.

## Cross-Slice Dependencies

- Slices 00-12 must already be complete and accepted.
- Required 13A remediation is accepted through the Slice 19A boundary state; this
  slice must fail closed if a future regression reopens it.
- Slices 13-19 must be complete with no open P1/P2 findings.
- Slice 13 supplies evidence sets.
- Slice 14 supplies commit/line provenance.
- Slice 15 supplies metrics.
- Slice 16 supplies findings.
- Slice 17 supplies recommendations.
- Slice 18 supplies replay.
- Slice 19 supplies reporting and agent context.
- Slice 21 is required before task-execute agent context can be adopted.

## Slice 13A Shared Completeness Model Dependency

Per **doc-13a:285-287 § Refactoring Steps step 9** — *"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally."* — this
slice's all-at-once governance acceptance gate depends on the Slice
13A shared completeness model. The existing § "Refactoring Steps"
step 2 already pins this dependency: *"Validate that required 13A
remediation is complete before governance/context surfaces consume
exact/paged evidence as execution authority."*

Source-of-truth modules:

- `src/iriai_build_v2/execution_control/completeness.py` (Slice 13A
  2nd sub-slice) — `CompletenessState`, `EvidenceCompleteness`,
  `AuthoritativeContextRef`, `EvidencePageRef`, `ExactEvidenceManifest`,
  `compute_completeness_digest`.
- `src/iriai_build_v2/execution_control/prompt_context_adapter.py` +
  `dispatcher_prompt_context.py` + `gate_companion.py` +
  `snapshot_companion.py` (Slice 13A 3rd-6th sub-slices) — the four
  adapter modules + their typed companion records.
- `docs/execution-control-plane/13a-acceptance.md` — the Slice 13A
  acceptance artifact pins the doc-13a § Refactoring Steps per-step
  status table; the governance-acceptance gate consumes that
  per-step status table as one of its required-13A-remediation
  signals.

The governance-acceptance collector (§ Refactoring Step 1: *"Add
governance acceptance collector after Slices 13-19 exist."*) must
include a fail-closed precondition that:

- The Slice 13A acceptance artifact at
  `docs/execution-control-plane/13a-acceptance.md` lists all 9
  doc-13a § Refactoring Steps as SATISFIED.
- The Slice 19A accepted boundary at
  `docs/execution-control-plane/19a-governance-implementation-reassessment.md`
  is still closed, including **19A-P2-001**.
- The composite-adapter wiring referenced by **P3-13A-6-3** (see
  `13a-acceptance.md:193-227`) remains a retained authority-boundary
  marker: the current dashboard wrapper is display/advisory-only, and
  durable failure observation plus production execution-authority wiring
  remain future-source-of-truth work unless a later accepted slice owns them.
- The Slice 13A test surface (the 7 modules tabulated at
  `13a-acceptance.md:255-263` + this iteration's NEW step-9
  reconciliation test surface) is all green at byte-identical baselines.

Per **P3-13A-6-3 dead-until-wired binding statement** (see
`13a-acceptance.md:193-227`), a governance slice may not claim gate
execution authority from preview-only, unavailable, stale, non-exact, or
otherwise ambiguous evidence. Slice 20 adoption is analytical/read-only only;
it must preserve the current display/advisory-only boundary and keep
task-execute agent context disabled until Slice 21 acceptance.

Historical **13An-2** evidence remains audit history for the dashboard-wrapper
consumer experiment, but it is not sufficient authority closure after Slice
19A. Any future authority claim must come from a future source-of-truth slice
with durable failure observation, tests, and an explicit acceptance record.

This dependency-reconciliation reference was added by
**Slice 13A 8th sub-slice 13An-1** (this iteration) per
doc-13a:285-287 step 9.

## Acceptance Result -- 2026-05-27

Slice 20 is accepted after implementation, remediation, independent re-review,
focused gates, and the full repository gate.

Acceptance evidence:

- Independent remediation pass 1 re-review returned no open P1/P2 findings.
- Governance acceptance/adoption focused tests: PASS, 56 passed.
- Slice 20 / 19A boundary focused suite: PASS, 501 passed.
- `python -m compileall -q src/iriai_build_v2 dashboard.py`: PASS.
- `git diff --check`: PASS.
- JSONL per-line parse: PASS.
- Full `pytest -q`: PASS, 11370 passed / 1389 warnings.

Slice 21 may begin only after this acceptance is recorded in STATUS, the
implementation journal, and the decision log.
