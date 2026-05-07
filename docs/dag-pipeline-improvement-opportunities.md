# DAG Pipeline Improvement Opportunities

Feature basis: `8ac124d6`, groups 0-38, with strongest evidence from G28-G38.  
Companion retrospective: `docs/dag-pipeline-retrospective.md`.

## Recommendation Summary

The pipeline should move from "large topology-valid waves plus corrective repair loops" to "semantic waves with typed authority, deterministic gates, and disposable projections." The late tail shows that local stale-path fixes help, but they are not enough when stale state can leak through task results, task specs, generated verifier snapshots, changed-files, commits, and resume checkpoints (`artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-group:37 id=1273018`).

The highest-value direction is:

1. Define artifact authority rules and enforce them before each preflight.
2. Replace hard-packed 20-task waves with semantic waves plus integration barriers.
3. Split the retry loop into deterministic gates, product verification, repair planning, and commit/checkpoint approval.
4. Extract typed services from `implementation.py` before deeper behavior changes.

## Current Feature In Flight

### Current Read

As of the latest local evidence, feature `8ac124d6` is still in `implementation` and G38 is active. Local wall clock was 2026-05-06 12:35:27 PDT, and Postgres `now()` was 2026-05-06 12:35:31 -07 during this check (`query:q-current-clock`). The feature metadata records `concurrency_max=2`, so the bridge is already throttled relative to the earlier high-fanout runs (`query:q-current-feature-status`).

The old stale-DAG class is not the current blocker. The latest G38 deterministic preflight is clean: `dag-repair-preflight:g38:retry-initial` has `approved=true` with empty concerns and path problems (`artifact:dag-repair-preflight:g38:retry-initial id=1351098`). The run then spent about 12 minutes in normal model verification from `dag_verify_start` at 11:29:17 to `dag_verify_finish` at 11:43:35, and the failure was product/regression oriented: backend package compile and DB coverage gaps (`event:24254`, `event:24258`, `artifact:dag-verify:g38:initial id=1351629`).

The retry-0 cycle then spent about 30 minutes on expanded verification and RCA: expanded verify ran from 11:43:36 to 12:13:36 and completed all lenses with no failed lenses, but with 32 concerns and 24 gaps; RCA completed at 12:19:36 (`event:24260`, `event:24279`, `artifact:dag-repair-expanded-verify:g38:retry-0 id=1352852`, `event:24280`, `event:24282`, `artifact:dag-verify-rca:g38:retry-0 id=1353093`). The product implementer ran from 12:19:36 to 12:31:36, about 12 minutes, and produced a real product fix attempt (`event:24283`, `event:24284`, `event:24285`, `artifact:dag-fix:g38:retry-0 id=1353595`).

The latest error is a commit/husky hygiene failure, but the workflow did not crash. At 12:31:53, the host recorded `dag_commit_failed`, wrote `dag-commit-failure:g38:retry-0`, converted the failure into a verifier-style deterministic blocker, and immediately started retry-1 expanded verify (`event:24286`, `artifact:dag-commit-failure:g38:retry-0 id=1353600`, `artifact:dag-verify:g38:retry-0 id=1353601`, `event:24287`, `event:24288`). A read-only event check after 12:31:53 found one `dag_commit_failed`, one `dag_repair_cycle_start`, one `dag_expanded_verify_start`, six `agent_start` events, and zero `phase_execute_error` events (`query:q-current-after-commit`). The specific hook error is product-repairable repo hygiene: `src/webviews/projectSurface/src/chat/__tests__/ChatSidepaneShell.test.tsx(149,29)` contains an unexpected unicode section sign, charCode 167, with the hook saying to suppress via `// allow-any-unicode-next-line`; backend committed successfully as `d8eaff3457ca138f9b2504ea185083ca5e7adf21` (`artifact:dag-commit-failure:g38:retry-0 id=1353600`).

### Recommendation

Let the current G38 retry-1 cycle run for now, but watch it closely. Do not stop the bridge mid-expanded-verify to land Phase 2/3 pipeline rearchitecture. The evidence says the expensive stale-metadata class is currently contained: preflight is clean, stale task/spec reconciliation already ran, and the current live error is a small repo-hygiene issue caused by product/test content, not a crash or hidden stale-artifact loop (`artifact:dag-repair-preflight:g38:retry-initial id=1351098`, `artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-commit-failure:g38:retry-0 id=1353600`).

This recommendation is not "let it run no matter what." It is a costed call:

- Cost already paid if we stop now: the retry-1 expanded verify cycle started at 12:31:53 and had already launched six lens agents by 12:35:31 (`event:24288`, `query:q-current-after-commit`).
- Expected cost to continue: likely one retry cycle focused on a concrete husky error, plus any product regressions the existing lenses keep finding (`artifact:dag-commit-failure:g38:retry-0 id=1353600`, `artifact:dag-verify:g38:retry-0 id=1353601`).
- Expected cost to stop now: kill/restart overhead, risk of resuming mid-G38 with partial backend commit `d8eaff3457ca138f9b2504ea185083ca5e7adf21` and dirty `iriai-studio` changes, plus the engineering time to patch a broader loop while the only priority feature is paused (`artifact:dag-commit-failure:g38:retry-0 id=1353600`).
- Pipeline-learning value: the current hard-gate commit behavior is now producing exactly the artifact we wanted instead of crashing (`event:24286`, `artifact:dag-verify:g38:retry-0 id=1353601`). Interrupting immediately would reduce evidence about whether the new commit-failure path converges.

The right operating policy for this feature:

- Continue the active G38 retry-1 cycle unless it stalls, crashes, repeats the same commit/husky failure after one repair attempt, or produces another deterministic workflow blocker that is not product-repairable (`event:24288`, `query:q-current-after-commit`).
- If the next failure is product-verification output, keep the bridge running and let the existing retry loop handle it.
- If the exact same unicode/husky failure repeats, or if retry-1 spends another full repair cycle without targeting `ChatSidepaneShell.test.tsx`, stop at that boundary and patch the loop to route commit failures directly to a focused hygiene repair without expanded verify.
- If another deterministic workflow state failure appears, such as stale projections, embedded repo hygiene, writeability, or checkpoint leakage, stop at that boundary and apply a narrow Phase 1 safety fix.
- If G38 checkpoints cleanly, that is the best time to restart with low-risk pipeline improvements.
- Defer semantic wave planning, durable state-machine extraction, and bugfix scheduler redesign until after `8ac124d6` completes or reaches a clean checkpoint, because those changes alter resume/checkpoint behavior.

### Work Allowed While It Runs

Safe while the feature runs:

- Documentation and analysis only.
- Read-only health checks and artifact queries.
- Tests or code work in `iriai-build-v2` that is not imported by the running bridge until a planned restart.

Defer until a checkpoint, crash, or intentional bridge stop:

- Changes to `_implement_dag`, `_verify_and_fix_group`, artifact reconciliation, commit handling, Slack recovery, or runtime scheduling.
- Any migration that changes artifact keys, resume semantics, or checkpoint conditions.

If the feature is the top priority, the short-term goal is to finish G38 with the current patched pipeline, then apply Phase 1 observability/safety improvements before the next feature. The larger architecture work should be planned from these docs rather than mixed into this active repair cycle.

## Rearchitecture Direction

### 1. Add An Explicit Artifact Authority Model

Current fact:

- `PostgresArtifactStore.get()` returns the latest row by descending id, and `put()` always appends a row (`storage/artifacts.py:25-53`).
- Runtime resume reconstructs completed groups and task state from `dag-group:*` and `dag-task:*` artifacts (`implementation.py:_implement_dag lines 2762-2850`).
- Artifact mirror files are a parallel filesystem view, not the canonical store (`services/artifacts.py:1-5`, `services/artifacts.py:41-69`).

Recommendation:

Create a typed authority registry for implementation-phase artifacts:

| Artifact family | Authority | Rule |
|---|---|---|
| `dag` and subfeature `dag-fragments/*.json` | Planning source authority | Must be canonicalized or rejected before dispatch. |
| `dag-task:{full_task_id}` | Latest valid implementation result authority | Append-only; full task id latest row wins; old rows are history. |
| `dag-group:{idx}` | Checkpoint authority | Write only after raw verifier approval and commit/no-dirty proof. |
| `dag-task-reconcile:*` and `dag-task-spec-reconcile:*` | Host evidence | Audit-only; can explain in-memory replacement but should not become source state. |
| Expanded verify snapshots and changed-files | Disposable projections | Always regenerate from reconciled source before preflight/retry. |
| Artifact mirror files | Derived mirror | Never outrank DB artifacts or product repo state. |

Testable contract:

- If a generated projection contains stale paths but source DAG fragments and latest `dag-task:*` rows are canonical, preflight should regenerate the projection and proceed (`artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-repair-preflight:g38:retry-initial id=1351098`).
- If source DAG fragments contain retired paths, preflight should route typed artifact closure repair before dispatching product implementers (`artifact:dag-repair-preflight:g30:retry-initial id=1052604`, `implementation.py:_build_dag_artifact_closure_scan lines 5572-5605`).

### 2. Introduce Semantic Waves Instead Of Hard Packing

Current fact:

- Planning asks the model to avoid false dependencies and be aggressive about parallelization (`planning_lead/prompt.md:61-71`, `task_planning.py:5657-5660`, `task_planning.py:6631-6634`).
- The host normalizer ensures explicit dependency topology is valid and pushes same-wave dependencies into later waves (`task_planning.py:_normalize_subfeature_execution_order lines 3152-3266`).
- Root DAG assembly concatenates subfeature waves and normalizes topology, but it does not add a semantic risk regrouping pass (`task_planning.py:5158-5175`).
- Implementation dispatches every pending task in the group via `asyncio.gather`, then verifies the whole group as one unit (`implementation.py:_implement_dag lines 3088-3105`).

Recommendation:

Add a host-side semantic wave builder after root DAG assembly and before implementation. It should preserve explicit dependencies, then score candidate waves for implicit integration risk.

Suggested score:

| Signal | Score pressure | Evidence source |
|---|---:|---|
| Same exact file in `file_scope` or `files` | High split/barrier | `ImplementationTask.file_scope`, `ImplementationTask.files` (`outputs.py:949`, `outputs.py:979`) |
| Same package/barrel/export surface | Medium/high split | Import graph, changed files, task references |
| Generated mirror or catalog pair | High barrier | G38 Alembic/package mirror failure (`artifact:dag-verify:g38:initial id=1351629`) |
| Retired/canonical path migration | High barrier | G30/G38 stale path artifacts (`artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`) |
| Shared verification gates or AC ids | Medium split | `ImplementationTask.verification_gates` (`outputs.py:957-966`) |
| Cross-repo protocol or bridge contract | High barrier | Expanded verify contract lens exists because this is common risk (`implementation.py:DagVerifyLensSpec lines 4150-4205`) |
| No shared repo/path/gate/contract | Low pressure | Can pack larger wave |

Rubric:

- Target 5-10 tasks for medium/high risk surfaces.
- Allow up to 20 tasks only when repo, path prefix, contract surface, generated outputs, and verifier ownership are disjoint.
- Insert explicit integration barrier tasks for cross-cutting changes before downstream feature work.
- Reuse the repair scheduler precedent: `_compute_fix_schedule()` already separates repair groups by overlapping affected files; the same idea should be used before initial dispatch (`implementation.py:_compute_fix_schedule lines 4032-4055`).

Expected impact:

This should reduce tail risk because one stale chat relocation or backend generated mirror issue would block a smaller semantic unit instead of a 20-task group (`docs/dag-execution-learnings.md:47-65`, `artifact:dag-verify:g30:initial id=1084035`, `query:q-g28-g38-metrics`).

### 3. Add Integration Barriers

Use barrier tasks when work changes a shared contract or generated projection that many downstream tasks consume.

Initial barrier catalog:

| Barrier | Why | Evidence |
|---|---|---|
| Path migration/canonicalization barrier | Retired `src/vs/workbench/.../chat` and `src/webviews/dashboard` paths repeatedly reentered specs/results/projections. | `artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097` |
| Backend package/import barrier | Backend `src/iriai_studio_backend` path projection and Alembic mirror issues persisted into G38. | `artifact:dag-path-canonicalization:g38 id=1351094`, `artifact:dag-verify:g38:initial id=1351629` |
| Bridge protocol/fixture barrier | Contract/protocol verification is a dedicated lens and should gate downstream UI/backend consumers. | `implementation.py:DagVerifyLensSpec lines 4170-4177` |
| Generated catalog/mirror barrier | Generated snapshots and mirrors should be regenerated and validated before downstream tasks depend on them. | `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `services/artifacts.py:41-69` |
| Commit/hygiene barrier | Husky/precommit failure and embedded `.git` blockers delayed checkpoint despite product work. | `artifact:dag-commit-failure:g38:retry-0 id=1316714`, `implementation.py:_commit_repos_in_root lines 3625-3668` |
| Environment/writeability barrier | Backend permission problems created blocked task rows before product verification. | `event:23395`, `implementation.py:_implement_dag lines 2859-2902` |

Acceptance:

- A group cannot start product implementation if barrier preflight reports retired authoritative DAG paths, non-writable canonical targets, embedded `.git`, gitlinks, pending/proposed files, or manifest-forbidden workspace/index paths.
- A barrier can be satisfied by host regeneration only when the stale item is a disposable projection; product source and source DAG drift still route through product/artifact repair respectively.

## Retry Loop Redesign

### Current Shape

The current flow is layered inside `_verify_and_fix_group()`:

1. Sanitize repair results.
2. Reconcile `dag-task:*` results.
3. Reconcile task specs.
4. Collect files.
5. Run deterministic preflight.
6. Run model verifier when preflight is clean.
7. Deduplicate/partition findings.
8. Enter RCA/fix/reverify loop (`implementation.py:_verify_and_fix_group lines 1935-2065`).

Task dispatch and checkpointing live in `_implement_dag()`; groups are skipped on resume when `dag-group:{idx}` exists (`implementation.py:_implement_dag lines 2742-2850`). Commit handling is called after implementation and checkpoint paths through `_commit_repos()` and `_commit_repos_in_root()` (`implementation.py:_commit_repos lines 3579-3607`, `implementation.py:_commit_repos_in_root lines 3625-3668`).

### Proposed Shape

Split each group attempt into typed stages:

1. `projection_reconcile`: rehydrate task specs and generated verify context from canonical DAG/task rows.
2. `workspace_gate`: check writeability, forbidden files, git state, embedded repos, pending/proposed files.
3. `artifact_gate`: check source DAG fragments, latest `dag-task:*`, and closure scan for typed stale signatures.
4. `model_verify`: run expanded/model verify only after deterministic gates are clean.
5. `repair_plan`: classify issues into artifact-only, product-cleanup, product-semantic, environment/operator, or contradiction.
6. `repair_apply`: apply the chosen lane and persist structured repair evidence.
7. `commit_gate`: commit dirty direct repos or record no-dirty proof; any failure becomes deterministic blocker.
8. `checkpoint_gate`: write `dag-group:*` only if raw deterministic gates, model verifier, and commit gate are approved.

Key rule:

Display dedupe and ledger state must not affect raw checkpoint decisions. G37 shows why: `event:23309` recorded `approved:false`, but `event:23310` checkpointed the group (`artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`).

## Codebase Cleanup Roadmap

### Phase 1: Safety And Observability Wins

Goals:

- Stabilize the current pipeline without changing core behavior.
- Make future tail analysis cheaper.

Actions:

- Persist a `dag-group-attempt:*` artifact for every group attempt with raw gate verdicts, projection reconciliation status, commit/no-dirty proof, and model verifier id.
- Add a metrics rollup artifact per group: wall time, active agent time, preflight count, repair count, expanded verify count, commit failures, task errors, and checkpoint status.
- Make `dag-group:*` include raw verifier artifact id, raw preflight artifact id, commit artifact id/no-dirty proof, and reconciliation artifact ids.
- Add a pre-dispatch barrier report for non-writable targets and repo hygiene. The current writeability preflight exists near dispatch, but it should be visible as a gate artifact with operator instructions (`implementation.py:_implement_dag lines 2859-2902`).
- Treat Slack streamer failures as UI delivery failures only; do not let status-posting noise obscure workflow state. The streamer logs and suppresses flush errors today (`streamer.py:196-220`).

Tests:

- Checkpoint regression: raw failed preflight plus resolved ledger item must not write `dag-group:*` (`artifact:dag-verify:g37:initial id=1273016`).
- Commit artifact regression: failing husky commit persists bounded stdout/stderr/status and blocks checkpoint (`artifact:dag-commit-failure:g38:retry-0 id=1316714`).
- Metrics regression: group rollup matches artifact/event counts from local query fixtures.

### Phase 2: Typed Boundaries And Extracted Services

Goals:

- Reduce `implementation.py` coupling before deeper planner/executor changes.
- Create stable units that can be characterized and tested.

Actions:

- Extract `DagExecutionService`: group scheduling, task dispatch, task result persistence, resume from checkpoints (`implementation.py:_implement_dag lines 2742-2915`).
- Extract `DagVerificationService`: preflight, expanded verify, raw verdict, ledger display processing (`implementation.py:_verify_and_fix_group lines 1935-2065`, `implementation.py:DagVerifyLensSpec lines 4150-4205`).
- Extract `DagStateReconciler`: `dag-task:*`, task specs, projection regeneration, closure scanning (`implementation.py:DagTaskReconcileOutcome lines 8385-8396`, `implementation.py:_build_dag_artifact_closure_scan lines 5572-5605`).
- Extract `WorkflowGitService`: repo discovery, hygiene, status, file-scoped staging, commit, push. This should replace direct private helper reuse in bugfix-v2 (`queue.py imports implementation helpers lines 44-64`, `implementation.py:_commit_repos_in_root lines 3625-3668`).
- Extract `WorkflowRuntimeScheduler`: concurrency limiter, primary/secondary runtime policy, actor metadata routing. Today this is split across runner and implementation actor helpers (`_runner.py:126-150`, `_runner.py:223-365`, `implementation.py:_make_parallel_actor lines 998-1005`).
- Extract `ArtifactRepository`: typed latest-row reads, append writes, record identity, compare-and-skip semantics, and projection invalidation. Current store gives latest-row and append primitives but not domain transitions (`storage/artifacts.py:25-53`).

Tests:

- Characterization tests around current G30/G37/G38 fixtures before extraction.
- Unit tests for each extracted service using fake artifact store, fake git service, and fake runtime.
- Contract tests verifying artifact key compatibility and append-only behavior.

### Phase 3: State Machine And Planner Changes

Goals:

- Make long-running/resumable execution explicit.
- Reduce retry tail by preventing high-risk wave composition.

Actions:

- Introduce a durable group attempt state machine with states such as `planned`, `projection_reconciled`, `workspace_ready`, `artifact_ready`, `model_verified`, `repairing`, `commit_ready`, `checkpointed`, `operator_blocked`.
- Replace implicit resume reconstruction from only `dag-group:*` and `dag-task:*` with typed attempt state plus compatibility fallback (`implementation.py:_implement_dag lines 2762-2785`, `_runner.py:829-910`).
- Add a semantic wave planner after root DAG assembly. It should compute risk scores and insert integration barriers (`task_planning.py:5158-5175`, `outputs.py:942-990`).
- Turn bugfix-v2 queue into a durable scheduler/job model with leases and explicit lane states. It is currently a long-lived phase with in-memory asyncio task maps plus artifact snapshots (`queue.py:174-190`, `queue.py:_execute_bug_lane lines 2173-2205`, `queue.py:_promote_lane lines 2575-2605`, `queue.py:_load_queue lines 3056-3066`).
- Split Slack orchestration from execution-service construction. Slack currently owns runtime/runner creation, recovery maps, active runtimes, and UI streaming in one class (`orchestrator.py:184-230`, `orchestrator.py:_create_runtime_and_runner lines 1172-1198`, `orchestrator.py:_run_workflow_resumed lines 985-1012`).

Tests:

- State-machine recovery tests: crash after each state and resume exactly once without skipping failed raw gates.
- Semantic wave tests: high-risk fixtures split; low-risk fixtures pack; explicit dependencies remain valid.
- Bugflow scheduler tests: lane lease expires and resumes without duplicate promotion or hidden in-memory state.

## Canonicalization And Artifact-Authority Rules

Rules:

1. Product repo state beats all generated projections.
2. Source DAG fragments beat generated task-spec snapshots.
3. Latest valid full-id `dag-task:{task_id}` row beats stale in-memory implementation results.
4. Generated verifier context, changed-files, and handover snippets are disposable and must be regenerated from authority before each preflight.
5. Repair evidence explains actions; it does not become authority unless it appends a validated canonical artifact row or modifies product source through a normal product repair lane.
6. Checkpoints are authority only when they cite raw approved verifier/preflight and commit/no-dirty proof.

These rules are directly motivated by G30 stale path recurrence, G37 checkpoint leakage, and G38 task/spec reconciliation (`artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-group:37 id=1273018`, `artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`).

## Test Strategy

### Unit Tests

- Artifact authority: latest valid `dag-task:*` replaces stale in-memory result; invalid latest row is rejected; old rows remain historical (`storage/artifacts.py:25-53`).
- Projection reconciliation: stale generated task-spec snapshots are regenerated when source fragments are canonical (`artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`).
- Closure classification: typed stale retired paths block; canonical replacement paths and generated snapshot filenames do not block.
- Semantic wave scoring: shared generated mirror and path migration tasks split or require a barrier; disjoint tasks can pack.
- Git state: staged delete of manifest-forbidden file is cleanup evidence; unstaged delete is stage-required; tracked existing forbidden file blocks.

### Workflow Regression Tests

- G30 fixture: stale chat paths across task result, task spec, fragment, generated context, and changed-files are found in one closure/reconcile pass; no piecemeal repeat cycle (`artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-repair-preflight:g30:retry-initial id=1052604`).
- G37 fixture: raw preflight fail cannot checkpoint even if ledger says a similar finding was resolved (`artifact:dag-verify:g37:initial id=1273016`, `event:23310`).
- G38 fixture: canonical latest rows plus stale projections regenerate cleanly, then product verifier sees backend/Alembic issue (`artifact:dag-repair-preflight:g38:retry-initial id=1351098`, `artifact:dag-verify:g38:initial id=1351629`).
- Commit fixture: failing precommit/husky returns structured `WorkflowCommitError`, persists `dag-commit-failure:*`, and enters repair/operator route (`artifact:dag-commit-failure:g38:retry-0 id=1316714`).

### Git/Worktree Tests

- Embedded `.git` and gitlinks block commits, but direct valid feature repos are allowed (`implementation.py:_commit_repos_in_root lines 3634-3668`).
- File-scoped commits do not stage unrelated dirty files in the same repo.
- Commit-no-dirty proof is persisted when there are no changes.
- Push failures become explicit workflow outcomes, not quiet logs. Current push path catches and logs failures while continuing (`implementation.py:_push_clones_to_source_root lines 1708-1720`).

### Recovery Tests

- Crash after task result append but before group verify: resume uses latest valid `dag-task:*`.
- Crash after raw failed preflight: resume does not checkpoint.
- Crash after commit failure: resume surfaces deterministic commit blocker.
- Slack reconnect/UI errors do not affect execution state but are observable (`orchestrator.py:_run_workflow_resumed lines 985-1012`, `streamer.py:196-220`).

### Metrics Validation

For every group, persist and assert:

- time from dispatch to checkpoint,
- active agent time,
- preflight count,
- expanded verify count,
- repair count,
- task error count,
- commit failure count,
- checkpoint raw verdict artifact id,
- commit/no-dirty proof id.

These metrics are the local equivalent of `query:q-g28-g38-metrics` and `query:q-g28-g38-forensics`, which were necessary to explain G28-G38 after the fact.

## Open Decisions

| Decision | Recommendation | Rationale |
|---|---|---|
| Default semantic wave size | Use dynamic sizing by risk score, with target 8 tasks, soft cap 10 for medium/high-risk surfaces, and up to 20 only for low-risk disjoint work. | Healthy packed waves were faster, but late packed groups amplified retry tails. A dynamic cap preserves throughput where safe while shrinking high-blast-radius groups (`docs/dag-execution-learnings.md:24-32`, `query:q-g28-g38-metrics`). |
| Source DAG canonicalization | Deterministically rewrite persisted source DAG artifacts only when the rule is manifest-backed, lossless, and auditable; otherwise fail before dispatch and route to planning/artifact repair. Keep runtime canonicalization only as a safety net and record it as drift. | Silent runtime compensation let stale source/projection state reappear. G38 showed host reconciliation works when source fragments are canonical, but G30 showed source/task-spec stale paths must be closed at the source (`artifact:dag-repair-preflight:g30:retry-initial id=1052604`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`). |
| Commit scope | Move to group-scoped explicit allowlists built from task results, repair results, and host-generated cleanup artifacts. Use file-scoped staging where practical; block unrelated dirty files unless they are explicitly classified. | Repo-wide `git add --all .` can sweep unrelated accumulated changes into checkpoint commits. Commit/hygiene failures have already been major blockers (`artifact:dag-commit-failure:g38:retry-0 id=1316714`, `implementation.py:_commit_repos_in_root lines 3625-3668`). |
| Generated verifier context storage | Keep generated verifier context as attempt-scoped evidence artifacts, but make them disposable and always record regeneration inputs. Do not treat them as authoritative source. | We need the artifacts for auditability, but stale snapshots caused repeated preflight leakage. The authority should be canonical DAG/task rows plus product state, not `.iriai-context` files (`artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`). |
| Bugfix-v2 and full-develop scheduler | Do not merge schedulers first. Extract shared services for artifact state, git/worktree, runtime scheduling, retry budgets, and metrics; then consider a common durable job model later. | Bugfix-v2 and full-develop have different lifecycles, but they already share private helpers in fragile ways. Shared services reduce duplication without forcing one scheduler abstraction too early (`queue.py:44-64`, `queue.py:174-190`, `implementation.py:3579-3668`). |
| Operator-required versus product-repairable failures | Classify by locus and write authority. Operator-required: missing/exhausted infra like pgserver, credentials, DB/socket failures, bridge/runtime crashes, permissions that prevent writes, and repo hygiene outside the feature write boundary. Product-repairable: test failures, compile/import failures, spec mismatches, hook failures caused by changed files, and manifest-forbidden files inside the feature repo when cleanup preserves coverage. | This keeps agents from inventing `_pending_*` fallbacks for permission failures while still letting them fix real product code. G38 had permission and commit blockers as well as real product regressions (`event:23395`, `artifact:dag-commit-failure:g38:retry-0 id=1316714`, `artifact:dag-verify:g38:initial id=1351629`). |

## Citation Appendix

Primary artifact/event citations:

- G30 stale metadata and pass: `artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-repair-preflight:g30:retry-initial id=1052604`, `artifact:dag-repair-triage:g30:retry-0 id=1078324`, `artifact:dag-verify:g30:initial id=1084035`, `event:20233`.
- G37 checkpoint leak: `artifact:dag-verify:g37:initial id=1273016`, `artifact:dag-group:37 id=1273018`, `event:23309`, `event:23310`.
- G38 reconciliation and product blockers: `artifact:dag-path-canonicalization:g38 id=1351094`, `artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-repair-preflight:g38:retry-initial id=1351098`, `artifact:dag-commit-failure:g38:retry-0 id=1316714`, `artifact:dag-verify:g38:retry-1 id=1326086`, `artifact:dag-verify:g38:initial id=1351629`, `event:24113`.
- Current G38 active repair: `artifact:dag-repair-expanded-verify:g38:retry-0 id=1352852`, `artifact:dag-verify-rca:g38:retry-0 id=1353093`, `artifact:dag-fix:g38:retry-0 id=1353595`, `artifact:dag-commit-failure:g38:retry-0 id=1353600`, `artifact:dag-verify:g38:retry-0 id=1353601`, `event:24279`, `event:24282`, `event:24283`, `event:24284`, `event:24285`, `event:24286`, `event:24287`, `event:24288`.
- Timing/process queries: `query:q-feature-wide-counts`, `query:q-g0-g19-metrics`, `query:q-g20-g27-metrics`, `query:q-g28-g38-metrics`, `query:q-g28-g38-forensics`, `query:q-checkpoints`, `query:q-current-feature-status`, `query:q-current-clock`, `query:q-current-after-commit`.

Primary source citations:

- DAG implementation monolith and loop: `src/iriai_build_v2/workflows/develop/phases/implementation.py:1-95`, `implementation.py:1084-1125`.
- DAG execution/resume/task dispatch: `implementation.py:2742-2915`, `implementation.py:3088-3105`.
- Verification/retry/preflight: `implementation.py:1885-2065`, `implementation.py:9169-9225`.
- Repair scheduling and closure: `implementation.py:4032-4055`, `implementation.py:5572-5605`, `implementation.py:5811-5845`, `implementation.py:10237-10315`.
- Commit/git handling: `implementation.py:3579-3668`, `implementation.py:1708-1720`.
- Planning topology and prompts: `src/iriai_build_v2/workflows/planning/phases/task_planning.py:3152-3266`, `task_planning.py:5158-5175`, `task_planning.py:5657-5660`, `task_planning.py:6631-6634`, `src/iriai_build_v2/roles/planning_lead/prompt.md:61-71`.
- Task/DAG data model: `src/iriai_build_v2/models/outputs.py:942-990`.
- Artifact store and mirror: `src/iriai_build_v2/storage/artifacts.py:25-53`, `src/iriai_build_v2/services/artifacts.py:1-69`.
- Runner and Slack orchestration: `src/iriai_build_v2/workflows/_runner.py:126-150`, `_runner.py:223-365`, `_runner.py:829-910`, `src/iriai_build_v2/interfaces/slack/orchestrator.py:184-230`, `orchestrator.py:985-1012`, `orchestrator.py:1172-1198`, `src/iriai_build_v2/interfaces/slack/streamer.py:196-220`.
- Bugfix queue architecture: `src/iriai_build_v2/workflows/bugfix_v2/phases/queue.py:44-64`, `queue.py:174-190`, `queue.py:2173-2205`, `queue.py:2575-2605`, `queue.py:3056-3066`.
