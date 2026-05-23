# Implementation Prompt: Transactional Execution Control Plane

Use this prompt to start the implementation of the Transactional Execution
Control Plane. It is intended for a coding agent with access to this repository,
subagents, local tests, and the existing documentation under
`docs/execution-control-plane/`.

## Prompt

You are implementing the Transactional Execution Control Plane for
`iriai-build-v2`.

Your objective is to land the complete workflow rearchitecture described in
`docs/execution-control-plane/`:

- typed execution journal and compatibility projections,
- canonical workspace authority,
- task deliverable contracts,
- sandboxed runtime execution,
- dispatcher/runtime boundary,
- verification graph,
- typed failure router,
- durable merge queue,
- regroup overlay/scheduler feedback,
- supervisor/dashboard integration,
- refactor of the current implementation monolith,
- atomic landing and explicit in-flight adoption.

This is not a staged production rollout. Internal implementation may proceed
slice-by-slice, but the product-authoritative workflow lands only as the complete
bundle after every slice is implemented, verified, reviewed, and regression
tested.

## Source Of Truth

Read these files first, in order:

1. `docs/execution-control-plane/README.md`
2. `docs/execution-control-plane/execution-flow-comparison.html`
3. `docs/execution-control-plane/00-evidence-and-current-state.md`
4. `docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md`
5. `docs/execution-control-plane/02-workspace-authority.md`
6. `docs/execution-control-plane/03-task-deliverable-contracts.md`
7. `docs/execution-control-plane/04-sandbox-runner.md`
8. `docs/execution-control-plane/05-dispatcher-runtime-boundary.md`
9. `docs/execution-control-plane/06-gates-and-verification-graph.md`
10. `docs/execution-control-plane/07-typed-failure-router.md`
11. `docs/execution-control-plane/08-durable-merge-queue.md`
12. `docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md`
13. `docs/execution-control-plane/10-supervisor-dashboard-integration.md`
14. `docs/execution-control-plane/11-refactor-map.md`
15. `docs/execution-control-plane/12-rollout-and-acceptance-matrix.md`

Use the HTML comparison page as the quickest architecture alignment tool. It is
not a separate source of truth; if the docs and HTML disagree, update the docs
and the HTML together before implementation continues.

Post-landing change-control source:

- `docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md`

Do not implement Slice 13A as part of the current Slices 00-12 atomic landing
unless the active slice review loop independently accepts a matching fix. Slice
13A starts after Slices 00-12 have landed, and before governance/context
surfaces are allowed to treat exact/paged evidence as execution authority.

## Non-Negotiables

- Do not mutate active feature `8ac124d6`; treat it as evidence only.
- Do not silently migrate in-flight legacy features. In-flight adoption requires
  an explicit durable adoption marker such as
  `execution-control-adoption:{feature_id}` at a checkpoint/quiesce boundary.
- Do not create a production mode where only one slice is authoritative.
- Do not let implementer or repair agents mutate canonical repos directly once
  the sandbox path is active.
- Do not checkpoint without linked gate evidence, merge proof, commit proof,
  no-dirty proof, checkpoint body evidence, and compatibility projection proof.
- Do not route workspace, alias, ACL, stale projection, commit-only, runtime, or
  queue failures to broad product repair unless typed evidence proves a product
  defect.
- Do not broaden dashboard/supervisor/MCP reads to hydrate artifact bodies.
- Do not progress from one implementation slice to the next until the current
  slice is implemented, tested, independently reviewed, and review findings are
  resolved.
- Do not ask the operator to resolve implementation blockers. Create a
  deterministic remediation task, dispatch the appropriate subagent, and keep
  working. Stop only for an external credential/service outage that cannot be
  simulated, and record the precise unresolved condition in the journal.

## Persistent Decision And Progress Journal

Before code changes, create and maintain these restart-safe files:

- `docs/execution-control-plane/implementation-journal.md`
- `docs/execution-control-plane/implementation-decisions.jsonl`

Update the journal before and after every slice, every subagent dispatch, every
review cycle, every failed test run, every architecture decision, and every
restart/resume.

The markdown journal must contain:

- current slice,
- current branch/commit if available,
- files changed in this slice,
- active subagents and their assigned ownership,
- tests run and results,
- unresolved risks,
- restart instructions,
- next safe action.

The JSONL decision log must use one object per line:

```json
{
  "timestamp": "ISO-8601",
  "slice": "02-workspace-authority",
  "event": "decision|dispatch|review|test|patch|resume|blocker|acceptance",
  "summary": "short human-readable summary",
  "files": ["src/..."],
  "evidence": ["test command", "artifact id", "subagent id"],
  "decision": "chosen option or null",
  "alternatives_rejected": ["..."],
  "next_action": "..."
}
```

On restart, first read both journal files, inspect `git status`, inspect the
latest test output if available, and continue from the last safe action. Do not
restart from Slice 00 unless the journal explicitly says no implementation work
has begun.

## Subagent Implementation Model

Use subagents for implementation and review. Do not do the entire
rearchitecture as a single local patch.

For each slice:

1. Create a short slice execution brief in the journal.
2. Dispatch implementation workers with disjoint file/module ownership whenever
   parallel work is safe.
3. Keep the immediate critical-path integration work local.
4. Dispatch independent reviewers after implementation workers finish.
5. Patch review findings.
6. Redispatch reviewers until there are no P1/P2 correctness findings.
7. Run the targeted tests for the slice.
8. Fix failures and repeat review/test until green.
9. Record slice acceptance in both journal files.
10. Only then proceed to the next slice.

Required reviewer vectors for every behavior-changing slice:

- **Existing workflow compatibility:** verifies legacy resume, legacy artifact
  projections, post-DAG gates, post-test readiness, and current business logic
  are preserved.
- **Persistence and crash recovery:** verifies idempotency keys, typed state,
  projection links, restart behavior, and recovery boundaries.
- **Workspace and mutation safety:** verifies canonical repo authority, sandbox
  isolation, merge queue ownership, no-dirty proof, ACL/alias handling, and
  outside-root/symlink safety.
- **Failure routing correctness:** verifies workflow-class failures route
  deterministically and product defects still reach product repair.
- **Supervisor/dashboard safety:** verifies bounded reads, read-only supervisor
  behavior, Slack dedupe, and no operator escalation for deterministic workflow
  classes.
- **Test coverage:** verifies targeted tests cover positive, negative, resume,
  crash, and regression behavior for the slice.

Reviewers must return findings by severity:

- P1: correctness, data loss, unsafe mutation, resume breakage, checkpoint
  safety, or silent migration risk.
- P2: important workflow regression, missing safety test, bounded-read gap, or
  compatibility risk.
- P3: maintainability, clarity, or non-blocking test quality issue.

Do not progress with any open P1/P2 finding.

## Implementation Sequence

Implement in this order. This is a construction order, not a production rollout.

### Slice 00: Evidence Fixtures And Compatibility Inventory

Implement only the checked-in evidence fixtures, compatibility index, and test
helpers needed by later slices. Do not change execution behavior.

Acceptance:

- `8ac124d6` failure classes are represented in fixtures.
- Existing artifact/event compatibility consumers are indexed.
- Fixture replay is deterministic.
- Reviewers confirm no production behavior changed.

### Slice 01: Typed Journal And Compatibility Projections

Implement `ExecutionControlStore`, typed execution rows, idempotency keys,
projection links, and synchronous legacy `dag-*` compatibility projections.

Acceptance:

- Projection parity is proven for `dag-task:*`, `dag-verify:*`,
  `dag-commit-failure:*`, `dag-group:*`, and regroup projections where relevant.
- Typed success cannot exist without required compatibility visibility while
  legacy readers depend on artifacts.
- Legacy artifact-only features remain resumable through the legacy path.
- Crash/retry of projection writes is idempotent.

### Slice 02: Workspace Authority

Implement canonical repo registry, worktree alias detection, ACL normalization,
workspace snapshots, and writeability proof.

Acceptance:

- `*-wt` aliases, divergent repos, missing parents, symlinks, outside-root paths,
  dirty generated outputs, and ACL gaps are handled deterministically.
- Resolvable workspace classes do not require operator escalation.
- Implementers cannot start with unresolved workspace ambiguity.

### Slice 03: Task Deliverable Contracts

Implement contract compilation from DAG tasks into required, allowed, and
forbidden paths, write sets, acceptance criteria, gate requirements, and
evidence expectations.

Acceptance:

- Ambiguous write sets fail closed or receive explicit conservative contracts.
- Forbidden edits are rejected before merge.
- Missing required evidence prevents approval/checkpoint.
- Verifier, repair, sandbox, and merge queue consume the same contract identity.

### Slice 04: Sandbox Runner

Implement per-wave sandboxing by default, optional per-task sandboxing, runtime
workspace binding, patch/diff capture, and canonical mutation prohibition.

Acceptance:

- Claude, Codex, and Claude pool adapters receive sandbox paths, not canonical
  repo paths.
- Agents cannot write directly to canonical repos.
- Patch capture is deterministic and linked to attempts/contracts.
- Sandbox cleanup/retention is restart-safe.

### Slice 05: Dispatcher Runtime Boundary

Extract runtime dispatch from the implementation monolith. The dispatcher owns
runtime invocation, retry id, actor metadata, structured output capture, and
runtime failure typing. It must not checkpoint, commit, or route repair.

Acceptance:

- Runtime failures are typed and resumable.
- Duplicate retries dedupe through idempotency keys.
- Dispatcher cannot commit, checkpoint, or mutate canonical repos.
- Existing runtime adapters remain compatible.

### Slice 06: Gates And Verification Graph

Implement deterministic preflight gates, bounded evidence graph, raw verifier,
expanded lenses, stale-context checks, and aggregate approval nodes.

Acceptance:

- Merge/checkpoint cannot proceed on stale context, summary-only approval, or
  verifier crash.
- Gate context reads are bounded.
- Raw gate approval requirements are explicit.
- Existing expanded verify behavior remains represented as graph evidence.

### Slice 07: Typed Failure Router

Implement the central failure taxonomy, retry budgets, route decisions, and
replacement path for scattered retry/RCA/repair logic.

Acceptance:

- Commit hygiene, stale projection, workspace/ACL, alias drift, runtime
  provider, merge conflict, queue recovery, checkpoint contradiction, and
  product defect routes are deterministic.
- Repeated same-class failures do not create broad repair loops.
- Product defects still reach implementing agents with sufficient evidence.
- No operator escalation for deterministic workflow classes.

### Slice 08: Durable Merge Queue

Implement queue item schema, lease/claim model, canonical apply/rebase/verify,
commit, no-dirty proof, checkpoint projection, and crash recovery.

Acceptance:

- Queue is the only canonical product mutation path.
- Duplicate claims, stale base, failed commit, failed push, and crash before or
  after commit recover safely.
- `dag-group:*` projection is written only after complete proof.
- Checkpoint idempotency is proven.

### Slice 09: Regroup Overlay And Scheduler Feedback

Implement typed regroup overlay, active marker validation, rollback boundaries,
effective-DAG resolver, and metrics feedback for future scheduling.

Acceptance:

- Dependency preservation, write-set conflicts, stale DAG, active marker,
  rollback-before-first-wave, and post-test readiness are tested.
- Root `dag` remains immutable.
- Legacy `dag-regroup:*` artifacts are compatibility projections of typed
  overlay state.

### Slice 10: Supervisor And Dashboard Integration

Implement typed summaries, dashboard views, supervisor read-only contract,
Slack suppression/deduping, and deterministic unblock classification.

Acceptance:

- Supervisor and dashboard use bounded typed snapshots first.
- Supervisor cannot mutate executor/control-plane/product state.
- Repeated messages are deduped.
- Deterministic workflow unblocks are classified without operator escalation.

### Slice 11: Refactor Map Execution

Split the current implementation monolith into maintainable modules following
`11-refactor-map.md`, preserving compatibility shims and monkeypatch targets
where required.

Acceptance:

- Imports remain stable or compatibility wrappers are documented and tested.
- Existing post-DAG business gates and post-test guard remain preserved.
- The old monolith delegates; it does not retain hidden authority for
  control-plane features.
- Tests prove behavior-preserving extraction.

### Slice 12: Atomic Landing, Adoption, And Acceptance Gate

Implement the landing gate, complete-bundle enablement, startup guard,
operational go/no-go record, rollback runbook hooks, metrics comparison, and
explicit in-flight adoption workflow.

Acceptance:

- `IRIAI_EXEC_CONTROL_PLANE_ENABLED` is the only product-authoritative
  production enablement switch.
- New features can start on the complete control plane only after the gate is
  green.
- In-flight legacy features are not silently migrated.
- Eligible in-flight features can be adopted promptly at the first safe
  checkpoint/quiesce boundary through a durable adoption marker.
- Stale, partial, mismatched, or active-invocation adoption attempts fail
  closed without RCA/repair/post-test advancement.

## Review And Redispatch Loop

At the end of every slice, run this loop:

1. Run targeted tests for the slice.
2. Dispatch at least two independent review subagents:
   - one focused on compatibility/resume/business logic,
   - one focused on safety/persistence/failure routing.
3. For high-blast-radius slices, dispatch additional reviewers for workspace,
   merge queue, supervisor/dashboard, and test coverage.
4. Patch every P1/P2 finding.
5. Rerun targeted tests.
6. Redispatch reviewers with the patched diff.
7. Repeat until reviewers report no P1/P2 findings.
8. Record acceptance in the journal.

Never proceed to the next slice with:

- failing targeted tests,
- unreviewed behavior-changing code,
- open P1/P2 findings,
- missing journal entries,
- unclear rollback/resume behavior,
- unresolved compatibility projection gaps.

## Global Test Gate

Run targeted tests throughout implementation. Before declaring the complete
implementation ready, run:

```bash
python -m compileall -q src/iriai_build_v2 dashboard.py
pytest tests/workflows/test_dag_expanded_verify.py -q
pytest tests/workflows/test_dag_regroup.py -q
pytest tests/workflows/test_workflow_quiesce.py -q
pytest tests/test_workspace_isolation.py -q
pytest tests/supervisor -q
pytest tests/workflows/test_threaded_planning.py -q
pytest -q
```

If the full suite is too large to complete in one process, shard it
deterministically, record every shard command and result in the journal, and
rerun failed shards after fixes. The implementation is not complete until the
equivalent full gate is green or every remaining failure is proven unrelated and
recorded with exact evidence.

## No-Operator-Intervention Rule

The implementation agent owns the full loop. Do not ask the operator to:

- choose between documented architecture options,
- manually reconcile worktrees,
- manually normalize ACLs,
- manually inspect Postgres rows,
- manually restart the implementation process,
- decide whether to rerun tests,
- decide whether to redispatch reviewers.

When blocked, do this instead:

1. Record the blocker in both journals.
2. Classify it as implementation bug, test fixture gap, environment dependency,
   external service outage, or architecture inconsistency.
3. Dispatch a focused subagent when investigation can run in parallel.
4. Patch the implementation or docs when the repo can resolve it.
5. Add or update tests so the blocker cannot recur silently.
6. Continue from the last safe journaled action.

Only stop for a truly external condition that cannot be simulated or worked
around locally. The final message in that case must name the exact external
condition, the last safe state, the journal entries written, and the command to
resume.

## Final Completion Criteria

The work is complete only when:

- every slice acceptance criterion is met,
- all P1/P2 review findings are resolved,
- the persistent journal and JSONL decision log are complete,
- the global test gate is green,
- diagrams/docs are updated if the architecture changed,
- the control plane can start new features after the atomic landing gate,
- legacy in-flight features remain legacy unless explicitly adopted,
- eligible in-flight adoption is available at safe boundaries,
- rollback/admission-stop behavior is documented and tested.

Do not summarize the project as complete before these criteria are true.
