# Implementation Prompt: Governance Layer (Slices 13–19)

Drives the **post-landing governance layer** of the Transactional Execution
Control Plane. Slices 00–12 are ACCEPTED on the working branch
(`feat/execution-control-plane` PR); the execution control plane is
product-authoritative at the 2987-test green baseline. Governance/provenance/
metrics/findings/policy/replay/reporting are evidence-driven analytical
layers that sit on top — they must never become a second mutation authority.

This file has two parts:

1. **Loop driver prompt** — paste verbatim into `/loop` to start the run.
   The orchestrator that receives it stays context-minimal and only
   dispatches subagents.
2. **Subagent contract** — the substantive instructions worker subagents
   read at dispatch. The orchestrator does NOT load this section; only
   workers do.

---

## Loop driver prompt (paste this into `/loop`)

```
Continue the Transactional Execution Control Plane governance layer
(Slices 13–19) autonomously to completion. You are the loop
ORCHESTRATOR — do not do implementation work, source-file reads,
or edits yourself; keep your own context minimal.

Each iteration:

1. Read only docs/execution-control-plane/STATUS.md and the tail
   of docs/execution-control-plane/implementation-journal.md
   (last ~200 lines).

2. Spawn ONE general-purpose subagent in the foreground to execute
   the next safe action — one verified sub-deliverable (a sub-slice
   plus its review loop). Instruct it to: read STATUS.md, the
   journal tail, and
   docs/execution-control-plane/IMPLEMENTATION_PROMPT_GOVERNANCE.md
   (Subagent Contract section onward); then follow that prompt's
   restart protocol, non-negotiables, subagent review model, and
   no-operator-intervention rule; dispatch its own implementer/
   reviewer/finalizer subagents; journal to implementation-journal.md
   and implementation-decisions.jsonl before and after the chunk;
   and overwrite STATUS.md last.

3. When it returns, verify cheaply: re-read STATUS.md to confirm
   the next-action pointer advanced, and run the hygiene gate
   (python -m compileall -q src/iriai_build_v2 dashboard.py;
   git diff --check) plus the sub-slice's targeted tests. If
   verification fails or the subagent reports a blocker, have the
   next iteration's subagent remediate before progressing.

If the subagent reports it cannot dispatch its own review subagents
(nesting limit), fall back: dispatch the implementer + reviewer +
finalizer yourself as separate sibling subagents, so source reads
and edits still stay out of your context.

If the slice-end review fires (one of 13n / 14n / 15n / 16n / 17n /
18n / 19n or the final post-Slice-19 governance-acceptance review),
dispatch the six PARALLEL vector-review subagents in a single message
(V1 doc-acceptance / V2 contract integrity / V3 test honesty /
V4 Slice 00–12 preservation / V5 fail-closed + deps / V6 Slice 13A
invariant compliance); then synthesize their verdicts before
dispatching the finalizer.

Continue iterating through Slices 13, 13A, 14, 15, 16, 17, 18, 19
and the final global test gate. Stop only when the governance global
test gate is green or a genuine external outage occurs.
```

---

## Subagent Contract (read at dispatch by worker subagents)

The orchestrator does NOT load this section. Each worker subagent reads it
fresh when dispatched. The instructions below assume you are an
implementer/reviewer/finalizer subagent, not the orchestrator.

### Your Role

You are dispatched for ONE sub-deliverable in Slices 13–19. Read STATUS.md +
the journal tail to find the next safe action, then take one of these roles
(the orchestrator names which):

- **Implementer**: extract / write source + tests for the sub-slice, run
  targeted regression, hygiene-gate, append the BEFORE-chunk journal entry.
- **Reviewer**: independently verify the implementer's diff against the
  doc-N acceptance criteria; six-vector lens; return P1/P2/P3 with file:line.
- **Finalizer**: append the AFTER-chunk journal entry + JSONL line +
  overwrite STATUS.md.

If you can dispatch your own subagents (foreground general-purpose), run the
full implementer→reviewer→finalizer triad inside one turn. If you hit a
nesting limit, return early to the orchestrator with the exact role you
completed; the orchestrator will dispatch the next role as a sibling.

### Source Of Truth

Read these in order:

1. `docs/execution-control-plane/STATUS.md` — current pointer.
2. `docs/execution-control-plane/implementation-journal.md` — tail
   (~200 lines + the most recent `STARTING` / `COMPLETE` / `ACCEPTED`
   entries).
3. `docs/execution-control-plane/IMPLEMENTATION_PROMPT.md` — the prior
   canonical prompt for Slices 00–12; inherit its restart protocol,
   journal discipline, subagent review model, no-operator-intervention
   rule, and final-completion criteria.
4. `docs/execution-control-plane/12-rollout-and-acceptance-matrix.md` —
   the landing contract you must not violate.
5. The doc-N file for your slice (one of `13-*`, `13a-*`, `14-*`, `15-*`,
   `16-*`, `17-*`, `18-*`, `19-*`).

Cross-references to the landed surface live in
`src/iriai_build_v2/execution_control/` (`store.py`, `atomic_landing.py`,
`adoption.py`, `startup.py`) and `src/iriai_build_v2/workflows/develop/
execution/` (the 16 boundary modules). Reuse those contracts.

### Non-Negotiables

- **Governance is analytical, advisory, read-only.** No governance
  component mutates executor/control-plane/product state, takes merge or
  checkpoint authority, forces policy activation, or escalates to broad
  product repair. The owning workflow component activates each policy
  after explicit owner review.
- **Reuse Slice 01–12 typed contracts.** Reads come from
  `ExecutionControlStore`, `ControlPlaneSnapshot`,
  `AtomicLandingGateResult`, `WorkflowImprovementMetrics`,
  `InFlightAdoptionRecord`, the typed failure router, the regroup
  overlay, the supervisor/dashboard bounded readers. Do not introduce a
  second journal, second projection authority, or second event taxonomy.
- **`8ac124d6` is evidence-only.** Replay (Slice 18) reads the fixture;
  nothing mutates the feature.
- **Slice 13A invariant for downstream slices.** Anything that can
  influence dispatch, verification, merge, checkpoint, routing, scheduler
  feedback, or policy recommendation consumes exact cited evidence or an
  exact paged manifest. Previews are display-only. Slice 13A must land
  before Slices 14–19 use exact/paged evidence as execution authority.
- **No silent migration of in-flight features.** Slice-12d adoption
  marker remains the only path.
- **Bounded reads.** Reuse the typed snapshot's `LIMIT cap+1` truncation
  discipline and the supervisor's `SET LOCAL statement_timeout` pattern.
  No artifact-body hydration on the governance read path.
- **No operator escalation for deterministic workflow classes.** Findings
  that match a deterministic class route through Slice-07 typed failure
  router or Slice-10 supervisor classifier.
- **No progress past a sub-slice** with open P1/P2 findings, failing
  targeted tests, or missing journal entries.
- **No operator intervention.** Classify the blocker, dispatch a focused
  subagent, patch the repo or docs, add tests so the blocker can't
  recur, continue from the last journaled action. Stop only for an
  external service outage; name the exact condition + the resume
  command in the journal.

### Persistent Decision And Progress Journal

Extend the existing journals — do NOT recreate them. Slices 00–12 + the
global test gate are recorded in:

- `docs/execution-control-plane/implementation-journal.md` (currently
  ends with `IMPLEMENTATION COMPLETE — All Gates Green`).
- `docs/execution-control-plane/implementation-decisions.jsonl`.

Append a `## YYYY-MM-DD — Slice 13 STARTING (Governance Layer Begins)`
entry before the first code change. Subsequent entries follow the
`STARTING` / `COMPLETE` / `ACCEPTED` shape used by Slices 10–12. JSONL
line schema unchanged; use `slice="13-governance-evidence-model"` etc.
so the search surface stays partitionable.

Overwrite `docs/execution-control-plane/STATUS.md` after every sub-slice
as the cheap O(1) restart pointer.

### Subagent Review Model

Each slice runs this loop (inherited from Slice 10–12 discipline):

1. STARTING journal entry naming the inventory + targeted tests.
2. Dispatch implementer subagent (foreground).
3. Dispatch INDEPENDENT reviewer subagent on the diff.
4. If P1 or blocking P2 returned: dispatch remediator, then re-reviewer.
   Iterate until CLEAN.
5. Dispatch finalizer subagent for the AFTER journal entry +
   STATUS.md advance.
6. Orchestrator verifies cheaply (hygiene gate + targeted tests).

If scope risks a large shaky chunk, SPLIT into sub-slices and record
the split decision in the journal (mirroring the Slice 12a-1 / 12a-2 /
12a-3 precedent).

Required six-vector reviewer set for slice-end reviews (carried forward
from 08g / 09e-2 / 10g-3 / 11n / 12f):

- **V1** — Doc-N acceptance criteria coverage (every bullet → component
  + test).
- **V2** — Contract integrity + reuse of Slice 01–12 surfaces (no new
  authority).
- **V3** — Test coverage honesty (no skips, real assertions,
  back-import guards).
- **V4** — Slice 00–12 acceptance preservation (the 2987-test baseline
  holds).
- **V5** — Fail-closed semantics + dependency direction.
- **V6** — Slice 13A invariant compliance (exact-vs-preview
  enforcement).

Severities:

- **P1**: correctness, data loss, unsafe mutation, evidence-completeness
  violation, governance taking executor authority, silent migration,
  bounded-read regression.
- **P2**: workflow regression, missing safety test, dependency-direction
  violation, Slice 13A violation.
- **P3**: maintainability, clarity, non-blocking test quality.

Acceptance gated on 0 P1 / 0 P2 across all six vectors. Carried P3s
go in the ledger.

### Implementation Sequence

#### Slice 13 — Governance Evidence Model

Land the typed evidence model the governance layer reads. Reuse
Slice-10a `ControlPlaneSnapshot`, Slice-08 merge-queue + commit /
no-dirty proof rows, Slice-09 regroup overlay + scheduler feedback,
supervisor digests, Slice-12b `AtomicLandingGateResult` /
`WorkflowImprovementMetrics`. Add only the governance-side composition
shapes (evidence-set identity, source-of-truth tags, exact-vs-preview
flags).

Acceptance:

- Every governance evidence record cites typed sources; no
  artifact-body hydration.
- Compatibility-projection consumers still see the same legacy `dag-*`
  keys.
- Bounded reads honored (`LIMIT cap+1`, statement timeouts).
- Reviewers confirm no new mutation authority introduced.

#### Slice 13A — Lossless Context And Evidence Completeness (Precondition)

Cross-cutting invariant: every component that can influence
dispatch / verify / merge / checkpoint / route / scheduler / policy
consumes exact cited evidence or an exact paged manifest. Lossy
summaries remain display-only. Per the doc, this is a post-landing
change-control remediation — it must not destabilize an active slice
review cycle.

Acceptance:

- Task prompts, verifier packages, dashboard snapshots, supervisor
  digests, and governance inputs all carry an `evidence_completeness`
  tag (`exact` / `paged_exact` / `preview`).
- Slice 13A acceptance tests prove no `preview`-flagged evidence
  reaches dispatch / verify / merge / checkpoint / route / scheduler /
  policy code paths.
- Resource limits remain mandatory but are page/read limits, not
  silent truncation permission.
- Slices 14–19 may now read exact/paged evidence as execution
  authority.

#### Slice 14 — Commit And Line Provenance

Attach workflow execution provenance to Git commits; make accepted
task changes traceable down to file + line. Non-blocking governance
projection over commit / no-dirty proofs that already exist after
Slice 08.

Acceptance:

- Every accepted DAG task links to ≥1 integration commit; commits may
  span multiple tasks / repos.
- Slice-08 merge-queue commit proof remains canonical for execution
  control.
- The provenance projection does not become a hard checkpoint
  prerequisite.

#### Slice 15 — Governance Metrics And Scoring

Normalized throughput-oriented metrics that are correctness-gated.
Compose with Slice-12b `WorkflowImprovementMetrics` and the existing
bounded query primitives (`_lane_stats_from_metrics`,
`_fetch_metric_events`, `_fetch_artifact_summaries`).

Acceptance:

- Metrics never recommend changes that violate dependencies,
  write-set safety, sandbox isolation, merge proof, checkpoint proof,
  or bounded resource use.
- `8ac124d6` baseline + zero-checkpoint-regressions discipline
  preserved.
- Per-feature, per-wave, per-lane, per-runtime, per-policy
  normalization is reproducible.

#### Slice 16 — Finding Engine And Taxonomy

Deterministic rule engine that turns evidence + metrics into
structured findings (product defect / workflow drag / unsafe workflow
behavior / implementation-plan drift / evidence gap).

Acceptance:

- Findings cite exact evidence (per Slice 13A).
- Findings route through Slice-07 typed failure router for
  executor-relevant classes and through Slice-10 supervisor classifier
  for supervisor-relevant ones — does not introduce a third route
  table.
- No operator escalation for deterministic workflow classes.

#### Slice 17 — Policy Recommendation Interface

Advisory contract that lets scheduler feedback, failure routing,
supervisor, dashboard, and future feature planning consume governance
findings. Behavior changes require an explicit policy artifact +
tests + owner review + later activation by the owning component.

Acceptance:

- Recommendations never grant the analyzer direct mutation authority.
- Each recommendation cites the finding(s), the evidence set, and
  the proposed activation surface.
- Production activation is gated on the owning component's existing
  acceptance (Slice 07 for routes, Slice 09 for scheduler, Slice 10
  for supervisor, etc.).

#### Slice 18 — Counterfactual Replay And Simulation

Replay layer that evaluates whether alternative policies would have
reduced drag on historical executions. First corpus is `8ac124d6`
plus Slice 00–12 implementation artifacts.

Acceptance:

- Replay is reproducible against the static fixture; CI matrix runs
  the bounded replay deterministically.
- Replay output validates recommendations but is not by itself proof
  of safety to activate.
- `8ac124d6` is read-only; no mutation regardless of replay outcome.

#### Slice 19 — Governance Agent And Reporting

CLI/API + dashboard integration + Slack/report output +
agent-readable summaries. Structured records primary, prose reports
secondary.

Acceptance:

- Output is bounded, reproducible, evidence-cited.
- Supervisor/dashboard read-only contract preserved (no governance
  writer extends the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS` set).
- Reports cite exact evidence per Slice 13A.

### Global Test Gate

After Slice 19 acceptance, run:

```bash
python -m compileall -q src/iriai_build_v2 dashboard.py
git diff --check
pytest tests/workflows/test_dag_expanded_verify.py -q
pytest tests/workflows/test_dag_regroup.py -q
pytest tests/workflows/test_workflow_quiesce.py -q
pytest tests/test_workspace_isolation.py -q
pytest tests/supervisor -q
pytest tests/workflows/test_threaded_planning.py -q
pytest tests/test_atomic_landing.py tests/test_execution_control_adoption.py tests/test_execution_control_startup.py -q
pytest tests/test_governance_evidence.py -q          # Slice 13
pytest tests/test_lossless_context.py -q             # Slice 13A
pytest tests/test_commit_provenance.py -q            # Slice 14
pytest tests/test_governance_metrics.py -q           # Slice 15
pytest tests/test_finding_engine.py -q               # Slice 16
pytest tests/test_policy_recommendation.py -q        # Slice 17
pytest tests/test_counterfactual_replay.py -q        # Slice 18
pytest tests/test_governance_agent.py -q             # Slice 19
pytest -q
```

The full suite must remain GREEN (currently 2987; governance landings
strictly add). If a shard fails, fix and rerun.

### No-Operator-Intervention Rule (carried forward)

The implementation agent owns the full loop. When blocked, classify
(implementation bug / test fixture gap / environment dependency /
external service outage / architecture inconsistency), dispatch a
focused subagent, patch the repo or docs, add or update tests so the
blocker can't recur silently, and continue from the last safe
journaled action. Stop only for a truly external condition that
cannot be simulated. The final stop message must name the exact
external condition, the last safe state, the journal entries written,
and the resume command.

### Final Completion Criteria

The governance layer is complete only when:

- every Slice 13 / 13A / 14 / 15 / 16 / 17 / 18 / 19 acceptance
  criterion is met,
- all P1/P2 review findings are resolved,
- the journals + JSONL log are complete with `Slice 19 ACCEPTED` +
  a closing `GOVERNANCE COMPLETE — All Gates Green` entry,
- the global test gate is green,
- no governance component holds executor / control-plane / product
  mutation authority,
- the Slice 13A invariant holds for every component that can
  influence dispatch / verify / merge / checkpoint / route /
  scheduler / policy,
- governance can produce a bounded evidence-cited report against
  `8ac124d6` and against the Slice 00–12 implementation artifacts.

Do not summarize the project as complete before these criteria are
true.
