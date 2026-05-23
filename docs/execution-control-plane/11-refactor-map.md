# 11. Refactor Map

## Objective

Extract the current implementation monolith into maintainable modules without
breaking workflow behavior, imports, or compatibility artifacts.

This refactor map is a construction plan for the single atomic
execution-control-plane feature landing. Extraction PRs may be reviewed and
merged into the control-plane integration branch in a safe order, but they are
not phased production rollout steps. Production enablement happens once, after
the journal, workspace authority, contracts, sandbox runner, dispatcher, gates,
failure router, merge queue, regroup overlay, compatibility projections, and
regression suite are ready together.

## How To Use This Map

Use this document as the extraction checklist for turning
`implementation.py` into a phase adapter. It is not a replacement for the
behavior slices. When a module boundary is unclear, prefer the owning slice's
state/proof rule over the convenience of the current helper location.

For each extraction PR, reviewers should be able to answer four questions:

- What behavior moved, and which module owns it now?
- Which legacy import names and monkeypatch targets still work through shims?
- Which targeted tests prove the new facade and the compatibility shim?
- Why is the PR still refactor-only, or why is it the final atomic landing?

This keeps the plan maintainable for future agents: edit the boundary contract,
shim inventory, and test expectations in the same PR whenever a helper moves
between modules.

## Current Code Citations

- Phase adapter area: [ImplementationPhase.execute](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2111).
- DAG executor: [_implement_dag](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4647).
- Verify/repair loop: [_verify_and_fix_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2925).
- Worktree setup: [_ensure_task_worktrees](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1635).
- Commit helpers: [_commit_repos_in_root](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5732).
- Regroup validation: [_validate_derived_dag_artifact_update](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9902).
- Current post-DAG gates: code review at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2282),
  security at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2338),
  test authoring at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2395),
  QA at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2439),
  integration at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2495),
  final verifier at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2555),
  source push at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2626),
  report at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2672),
  and notification at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2721).
- Current post-test readiness guard:
  [_raise_if_dag_incomplete_before_post_test](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/post_test_observation.py:51)
  and
  [PostTestObservationPhase.execute](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/post_test_observation.py:695).
- Workspace manager: [WorkspaceManager](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/services/workspace.py:312).
- Quiesce tests: [test_workflow_quiesce.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_workflow_quiesce.py:85).
- README refactor-map contract: [README.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/README.md:143).
- Historical rollout filename warning: [README.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/README.md:146).
- Atomic journal landing constraint: [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:588) and [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:918).
- Merge queue atomic landing constraint: [08-durable-merge-queue.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/08-durable-merge-queue.md:11).
- Regroup overlay atomic landing constraint: [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:8) and [09-regroup-overlay-and-scheduler-feedback.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md:580).

## Current Failure Mode From `8ac124d6`

The monolith made local fixes possible but also encouraged repeated patches in
different branches of the same flow. That created risk that one route fixed ACL,
alias, projection, or commit behavior while another route still bypassed it.

The refactor must therefore remove duplicate authority paths, not merely move
large helper bodies into new files. Each extraction has to prove that legacy
imports, monkeypatch targets, artifact keys, quiesce behavior, retry semantics,
and checkpoint semantics still behave as they did until the atomic control-plane
landing replaces the authority model as a complete unit.

## Proposed Interfaces/Types

This slice is primarily a refactor plan, so the interface is a module boundary
contract rather than a new external runtime API. Each extracted module must
expose a small facade that can be called from `ImplementationPhase.execute`,
from the final `execution/control_plane.py`, or from compatibility shim
functions left in `implementation.py`.

Facade requirements:

- Inputs must be typed Pydantic models or explicit dataclasses, not unstructured
  `runner.services` lookups.
- Outputs must be typed outcomes with evidence ids, artifact ids, typed row ids,
  or failure ids.
- Public module calls must include `feature_id`, `attempt_id` when available,
  `group_idx` when group-scoped, `retry_label` when retry-scoped, and an
  idempotency key for every mutation.
- Modules must not assemble legacy artifact keys directly. They may request a
  named compatibility projection by typed enum/request object, but only
  journal-owned projection helpers build the concrete legacy key.
- Modules must not import from `implementation.py`; compatibility flows point
  from `implementation.py` into modules, never the reverse.
- Shims in `implementation.py` must preserve existing test monkeypatch targets
  until tests migrate.
- Any production caller that mutates git, artifacts, typed rows, checkpoints,
  or active regroup state must do so through a single owning facade.

Boundary-level API contracts:

| Module | Public facade | Owns | Must not own |
| --- | --- | --- | --- |
| `execution/types.py` | Shared Pydantic/dataclass request and outcome types. | Cross-module value objects, idempotency keys, typed failure enums. | Persistence, git, runtime calls, artifact key construction. |
| `execution/control_plane.py` | `ExecutionControlPlane.run(feature, state, runner, adapter) -> DagExecutionOutcome`. | State-machine orchestration, wave/group sequencing, transition ordering, quiesce propagation. | Git commands, provider calls, direct artifact body scans, legacy key construction. |
| `execution/journal.py` | `ExecutionControlStore` with methods from Slice 01, including `start_attempt`, `finish_attempt`, `add_evidence_graph`, `record_failure`, `enqueue_merge`, and the typed projection helpers. | Typed execution rows, evidence graph rows, projection transactions, legacy `dag-*` key builders, resume reconstruction. | Runtime dispatch, git mutation, path normalization. |
| `execution/workspace_authority.py` | `WorkspaceAuthority.build_snapshot`, `resolve_paths`, `preflight`, `normalize_acl`. | Canonical repo identity, aliases, ACL/writeability, workspace snapshots, outside-root/symlink decisions. | Product repair, checkpoint writes, provider-specific sandbox flags. |
| `execution/task_contracts.py` | `compile_contracts`, `validate_patch_against_contracts`, `validate_checkpoint_scope`. | Required/forbidden path contracts, write-set authority, deliverable validation. | Model prompts except for structured contract payloads, git staging. |
| `execution/sandbox.py` | `SandboxRunner.prepare`, `bind_runtime`, `capture_patch`, `discard`. | Sandbox lifecycle, immutable patch evidence, runtime workspace binding. | Canonical repo mutation, queue checkpointing, broad repair routing. |
| `execution/dispatcher.py` | `ExecutionDispatcher.dispatch_task`, `resume_task`, `record_runtime_failure`. | Provider/runtime invocation boundary, retry idempotency, runtime error normalization. | Commit/checkpoint decisions, workspace authority policy, failure budgets. |
| `execution/gates.py` | `GateRunner.run_preflight`, `run_raw_gate`, `run_checkpoint_gate`. | Deterministic preflight gates, raw-gate ordering, stale-context blockers, gate evidence shape. | Model repair, queue apply, projection keys. |
| `execution/verification.py` | `VerificationService.verify_group`, `run_lenses`, `load_bounded_context`. | Model verifier orchestration, expanded lenses, bounded verify context, verifier outcome typing. | Product repair execution, commit, checkpoint. |
| `execution/repair.py` | `RouteExecutor.build_route_request`, `build_repair_request`, `build_retry_request`, `RepairService.execute(request)`, `RepairService.resume(request_id)`, `RepairService.summarize_outcome`. | Concrete `RepairRequest`/`RepairOutcome` and `RetryRequest`/`RetryOutcome` execution for product, contract, canonicalization, workspace, commit-hygiene, dispatcher retry, verifier retry, merge retry, sandbox capture retry, and cleanup routes. | Failure classification budgets, queue apply/checkpoint, journal projection key construction. |
| `execution/failure_router.py` | `FailureRouter.decide(failure_id) -> RouteDecision`. | Typed failure taxonomy, retry budgets, deterministic route selection, quiesce/escalation decisions. | Running repairs, mutating git, scanning artifact bodies. |
| `execution/git_service.py` | `GitService.status`, `add_paths`, `commit`, `commit_repos_in_root`, `no_dirty_proof`, `parse_commit_failure`. | Git status/add/commit, hook failure parsing, dirty-state proof, bounded command output. | Checkpoint projection, queue leasing, repair policy. |
| `execution/merge_queue.py` | `MergeQueueService.enqueue`, `claim`, `apply_patch`, `run_post_apply_gates`, `commit_and_checkpoint`, `recover_expired`. | Canonical patch apply, queue leases, commit, no-dirty proof, checkpoint projection. | Runtime dispatch, sandbox patch generation, task completion authority from `dag-task:*`. |
| `execution/regroup_overlay.py` | `RegroupOverlayService.validate`, `activate`, `resolve_active`, `rollback_before_first_wave`. | Derived DAG overlay validation, active marker resolution, activation and rollback records. | Scheduler speed decisions that bypass dependencies/write sets, queue commits. |
| `execution/post_dag_gates.py` | `PostDagGateService.run`, `resume`, `record_gate_result`, `assert_feature_ready_for_observation`. | Feature-level code review, security, test authoring, QA, integration, final verifier, source push, implementation report, backlog report, and completion notification orchestration. | Group dispatch, sandbox patch generation, merge queue internals, root DAG mutation. |
| `execution/post_test_guard.py` | `PostTestReadinessGuard.assert_ready(feature_id)`. | Effective-DAG completion checks, post-DAG gate completion checks, and no-active-control-plane-work checks before `PostTestObservationPhase`. | Collecting post-test observations or dispatching product fixes. |

Cross-module dependency direction:

```text
ImplementationPhase.execute
  -> execution.control_plane
      -> journal
      -> workspace_authority
      -> task_contracts
      -> sandbox
      -> dispatcher
      -> gates
      -> verification
      -> repair
      -> failure_router
      -> merge_queue
      -> regroup_overlay
      -> post_dag_gates
      -> post_test_guard
      -> git_service
```

The dependency direction is intentionally shallow. Lower modules may depend on
`execution/types.py` and narrowly on `journal.py` for typed persistence, but
they must not call back into `control_plane.py` or `implementation.py`.

## Proposed Module Boundaries

- `execution/types.py`: shared type definitions used by the extracted modules.
- `execution/control_plane.py`: state transitions and high-level group/wave orchestration.
- `execution/journal.py`: typed storage and legacy projections.
- `execution/workspace_authority.py`: canonical repos, snapshots, ACL, alias.
- `execution/task_contracts.py`: deliverable contract compilation and validation.
- `execution/sandbox.py`: sandbox lifecycle and patch capture.
- `execution/dispatcher.py`: runtime invocation boundary.
- `execution/gates.py`: deterministic preflight and approval policy.
- `execution/verification.py`: model verifier and expanded lenses.
- `execution/repair.py`: product and deterministic repair execution.
- `execution/failure_router.py`: typed failure taxonomy and retry budgets.
- `execution/git_service.py`: git status, add, commit, no-dirty proof, hook failure parsing.
- `execution/merge_queue.py`: durable canonical merge and checkpoint.
- `execution/regroup_overlay.py`: derived DAG overlay, activation, rollback validation.
- `execution/post_dag_gates.py`: feature-level business gates that currently
  run after DAG completion inside `ImplementationPhase.execute`.
- `execution/post_test_guard.py`: defensive readiness checks before
  `PostTestObservationPhase` collects human/test observations.

Ownership rules:

- `journal.py` is the only module allowed to create legacy `dag-*` artifact keys
  for new typed execution-control-plane writes.
- Feature modules may own the semantics of a projection, such as regroup
  activation or checkpoint proof, but they must pass typed projection requests
  to `journal.py` rather than serializing legacy keys or artifact payloads
  themselves.
- `workspace_authority.py` is the only module allowed to canonicalize workspace
  paths, classify alias divergence, or decide outside-root/symlink failures.
- `git_service.py` is the only module allowed to shell out to git for status,
  add, commit, tree, and clean-proof operations.
- `merge_queue.py` is the only module allowed to mutate canonical product repos
  for new execution-control-plane attempts.
- `control_plane.py` is the only module allowed to decide group/wave order, but
  it must depend on `regroup_overlay.py` for active DAG resolution.
- `post_dag_gates.py` is the only module allowed to mark implementation-phase
  business gates complete after effective DAG checkpoint completion.
- `post_test_guard.py` is the only module allowed to decide whether
  `PostTestObservationPhase` can begin under the new control-plane path.
- `implementation.py` owns only phase adaptation, legacy import compatibility,
  and temporary wrapper targets during the construction sequence.

Boundary review checklist:

- If a function shells out to git, it belongs in `git_service.py` or receives a
  `GitService` dependency.
- If a function writes or reads `dag-*` compatibility artifacts for new typed
  execution-control-plane state, the write/read must be mediated by
  `journal.py` projection or reconstruction APIs.
- If a function decides whether a repo/path is canonical, aliased, writable, or
  safe to mutate, it belongs in `workspace_authority.py`.
- If a function mutates canonical product repositories after sandbox capture, it
  belongs in `merge_queue.py`.
- If a function chooses what should happen after a typed failure, it belongs in
  `failure_router.py`; if it performs that chosen action, it belongs in
  `repair.py`, `dispatcher.py`, `merge_queue.py`, or `workspace_authority.py`
  depending on the side effect.
- If a function only translates legacy phase arguments to typed requests, keep
  it as a shim in `implementation.py` until the matching removal condition is
  met.

## Refactoring Steps

Extraction PRs are numbered as review/build steps, not production rollout
phases. Every PR must be behavior-preserving against current production call
sites unless explicitly labeled as the final atomic feature landing.

1. PR 11.0: add `execution/types.py`, an `execution/__init__.py`, and an
   `ImplementationAdapters` dataclass in `implementation.py`. The adapter holds
   callables for the current helper seams so later extracted modules can be
   injected without breaking monkeypatch tests.
2. PR 11.1: extract `git_service.py`. Keep `_commit_repos`,
   `_commit_repos_in_root`, `_commit_group`, `_record_dag_commit_failure`,
   `_commit_failure_issue`, `_commit_failure_verdict`, and commit parser helper
   names in `implementation.py` as wrappers. In this PR, only git command,
   status, clean-proof, and parser logic moves to `GitService`; any
   artifact/projection write behavior stays byte-compatible in the existing
   shim body until PR 11.2 adds journal projection helpers.
3. PR 11.2: add `journal.py` and projection helpers before any extraction needs
   typed persistence. Move legacy key construction for new execution writes into
   journal-owned functions, rewire the projection portions of commit/checkpoint
   shims from PR 11.1, and preserve old artifact shapes through compatibility
   projections.
4. PR 11.3: extract `workspace_authority.py` in two behavior-preserving slices:
   first delegate alias/path/ACL decisions through wrappers; then move typed
   snapshot writes through `journal.py`.
5. PR 11.4: extract `task_contracts.py` and `sandbox.py` together enough to
   prove that contracts travel with sandbox patch evidence. Keep canonical repo
   mutation on the old path until `merge_queue.py` lands in the atomic feature.
6. PR 11.5: extract `dispatcher.py`. Runtime selection, actor creation, runtime
   failure normalization, and retry idempotency move behind the dispatcher; it
   returns typed attempt outcomes but does not commit or checkpoint.
7. PR 11.6: extract `gates.py`. Deterministic preflight, raw gate, stale-context
   checks, and checkpoint-gate evidence move behind `GateRunner`.
8. PR 11.7: extract `verification.py`. Expanded lenses and model verifier
   orchestration move behind `VerificationService`, while existing wrapper names
   preserve legacy tests.
9. PR 11.8: extract `repair.py` and `failure_router.py`. Product repair,
   artifact repair, deterministic route classification, retry budgets, quiesce,
   and operator escalation decisions become typed boundaries.
10. PR 11.9: extract `merge_queue.py` after git, journal, workspace authority,
    contracts, sandbox, gates, and failure router APIs are stable. The queue owns
    canonical apply, commit, no-dirty proof, and checkpoint projection, matching
    the Slice 8 atomic constraint.
11. PR 11.10: extract `regroup_overlay.py`. Move derived DAG validation, active
    marker resolution, activation, rollback-before-first-wave, and scheduler
    safety checks behind a typed facade.
12. PR 11.11: extract `post_dag_gates.py` and `post_test_guard.py`. Move the
    existing post-DAG code review/security/test-authoring/QA/integration/final
    verifier/source-push/report/notification sequence behind a feature-level
    service, and make the post-test guard consume effective-DAG completion plus
    post-DAG gate evidence.
13. PR 11.12: extract `control_plane.py` and shrink `ImplementationPhase.execute`
    to phase adaptation, service assembly, quiesce propagation, post-DAG gate
    delegation, and compatibility wrapper exports.
14. PR 11.13: final atomic execution-control-plane landing. Wire production
    entrypoints to the new control plane, journal, queue, and projections as one
    feature. This PR may remove unused internal construction toggles, but it
    must not use production behavior flags as a rollout strategy.

The order is intentional: types/adapters come first so extraction can preserve
monkeypatch and import compatibility; git and journal come before queue/checkpoint
authority because they define command and projection ownership; workspace,
contracts, sandbox, dispatcher, gates, verification, and router/repair establish
typed evidence before canonical mutation moves; merge queue comes late because
it becomes the only product-authoritative mutation path; regroup and control
plane come after those owners are stable so scheduling cannot bypass write-set,
gate, queue, or checkpoint rules.

Each PR must include a shim audit:

- List wrappers kept in `implementation.py`.
- List production call sites moved to the new module.
- List tests still monkeypatching `implementation_module.*`.
- List new module-level tests added.
- Confirm that artifact keys, event names, and result payload shapes are
  unchanged for refactor-only PRs.
- Confirm there is no new production path that can independently enable typed
  journal, sandbox, dispatcher, gates, failure router, merge queue, or regroup
  authority before PR 11.13.
- Confirm no file outside the module, tests, and the owning shim surface changed
  merely to chase imports; unrelated import migration waits for the matching
  shim/test PR.

## Compatibility Shims

Shims live in `implementation.py` until all production call sites and tests have
been migrated. They must be thin, documented, and lazy-import the extracted
module inside the function body to avoid circular imports.

Required shim groups:

| Shim group | Existing names to preserve | Delegate target | Removal condition |
| --- | --- | --- | --- |
| Commit/git | `_commit_repos`, `_commit_repos_in_root`, `_commit_group`, `_record_dag_commit_failure`, `_commit_failure_issue`, `_commit_failure_verdict`, `_parse_commit_failure_location`, `_parse_commit_failure_locations`, `_commit_failure_payload` | `git_service.py` plus `journal.py` projection helpers | New module tests cover git behavior and no remaining production code imports these names. Keep a smoke wrapper test until external monkeypatch consumers are audited. |
| Workspace authority | `_ensure_task_worktrees`, `_record_worktree_registry`, `_worktree_alias_map_for_group`, `_worktree_alias_path_info`, `_run_worktree_alias_pre_dispatch_guard`, `_normalize_dag_workspace_acl`, `_dag_workspace_writeability_problems`, `_path_agent_writable` | `workspace_authority.py` | All workspace/alias/ACL tests import the module facade or use adapter injection. |
| Dispatcher/runtime | `_make_parallel_actor`, `_runner_runtime_policy`, `_dag_group_runtime_pair`, `_post_dag_runtime_pair`, `_diagnostic_runtime_for_policy`, `_dag_repair_runtime_for` | `dispatcher.py` | Runtime policy tests use dispatcher APIs and the control plane receives runtime policy through typed config. |
| Gates/verification | `_run_dag_group_preflight`, `_run_expanded_dag_verify_lenses`, `_verify_and_fix_group` | `gates.py` and `verification.py` | Expanded verify, preflight, raw gate, and stale-context tests use module APIs or adapter injection. |
| Repair/router | `_attempt_parallel_dag_repair`, `_run_dag_artifact_repair_lane`, `_apply_dag_artifact_repair_updates`, `_classify_dag_direct_repair_route`, `_record_dag_direct_repair_route`, `_direct_route_repeated_signature` | `repair.py` and `failure_router.py` | Repair route tests use `RepairService`/`FailureRouter`; wrapper smoke tests still assert legacy payload shape. |
| Merge/checkpoint | Direct checkpoint helpers and direct calls near current commit/checkpoint path | `merge_queue.py` | Queue tests prove idempotent checkpoint projection; new control plane has no direct canonical commit path. |
| Regroup | `_validate_derived_dag_artifact_update`, `_resolve_active_regroup_before_group_dispatch` | `regroup_overlay.py` | Regroup tests use overlay facade; quiesce tests still prove phase-level pause propagation. |
| Post-DAG gates | Post-DAG code review, security, test authoring, QA, integration, final verifier, source push, implementation report, backlog report, and notification helper calls currently embedded after DAG completion | `post_dag_gates.py` plus `post_test_guard.py` | Feature-level gate tests prove every business gate still runs after effective DAG completion and post-test observation cannot start early. |
| DAG adapter | `_implement_dag` | `control_plane.py` | `ImplementationPhase.execute` delegates to `ExecutionControlPlane.run`; legacy tests either call control plane directly or keep one wrapper smoke test. |

Shim implementation rules:

- A shim may translate legacy positional arguments into a typed request, call the
  new facade, and translate the typed outcome back to the historical return
  shape.
- A shim must not duplicate business logic from the new module.
- A shim must not call another shim if that would hide module ownership or make
  monkeypatch order ambiguous.
- If an extracted parent function still needs a monkeypatchable child, pass the
  child through `ImplementationAdapters`; do not have the new module import
  `implementation.py` to find the patched function.
- A shim docstring should name its delegate module, the compatibility behavior
  it preserves, and the test or importer condition required before removal.
- Shims should translate data only at the edge. Any validation, retry, route,
  projection, git, or checkpoint decision inside a shim is a failed extraction.
- Keep wrapper names stable through the final atomic landing unless the same PR
  updates every importer and test that references the name.

## Import And Test Migration Strategy

Production import strategy:

- New production code imports module facades, not individual helper functions,
  unless the helper is an intentionally public pure function.
- `implementation.py` imports extracted modules lazily in shims and imports only
  stable types at module import time.
- Extracted modules depend on `execution/types.py`, typed store interfaces,
  narrow services, and standard library utilities; they never depend on the
  develop phase adapter.
- Adapter construction happens at the phase boundary. Tests can inject fake
  adapters without monkeypatching deep module globals.
- Avoid package-level singletons. Services receive stores, runtime selectors,
  clock functions, and command runners explicitly.

Test migration strategy:

1. In the extraction PR, add direct module tests for the new facade before
   moving parent orchestration.
2. Keep existing tests that import
   `iriai_build_v2.workflows.develop.phases.implementation` green through shims.
3. When migrating a test, move the assertion to the module facade only if it is
   about module-owned behavior. Keep adapter-level tests for phase ordering,
   quiesce propagation, legacy payload shape, and monkeypatch compatibility.
4. Replace brittle monkeypatches of private child helpers with adapter injection
   once the parent function moves into `control_plane.py`.
5. After all tests for a shim group migrate, keep one compatibility smoke test
   that calls the legacy shim and asserts the same return/payload shape.
6. Do not update imports across unrelated files in the same PR unless the PR
   also owns the corresponding shim/test migration.
7. When a moved helper currently appears in broad workflow tests, add the direct
   module test first, then keep the workflow test as the integration proof. Do
   not rely on the broad workflow test alone to define the new module contract.

## Persistence And Artifact Compatibility

- During extraction, old helper names remain as shims that call new modules.
- Do not change artifact keys in refactor-only commits.
- Do not change event names, metadata keys, or artifact payload shapes in
  refactor-only commits.
- Refactor commits must not change checkpoint semantics.
- No behavior-changing production flag is allowed as a rollout mechanism. Test
  fixtures may use local toggles or dependency injection to compare old and new
  paths, but production enablement is a single atomic feature landing.
- Compatibility artifacts are the supported legacy surface, not a temporary
  shadow stream.
- Old artifact-only features remain resumable through the legacy adapter until a
  separately tested backfill/migration tool exists.
- `dag-task:*` remains attempt evidence. It must never become checkpoint,
  canonical integration, or group completion authority.
- Any module writing typed rows and compatibility artifacts must do so in one
  journal-owned transaction or fail without partial projection.

## Anti-Patterns To Avoid

- Moving code into files while preserving one hidden transaction through
  `runner.services`, mutable tasks, and ad hoc artifact keys.
- Treating `dag-task:*` as completion authority.
- Relying on prompts as sandbox enforcement.
- Mixing product-code repair and derived-artifact repair.
- Adding new broad artifact-body scans to compensate for moved code.
- Changing line-of-business behavior in the same commit as mechanical extraction.
- Adding production behavior flags, shadow writers, advisory-first production
  modes, or pilot lanes as a substitute for the atomic feature landing.
- Letting extracted modules import `implementation.py` or depend on phase-global
  mutable state.
- Letting more than one module own the same side effect, especially git commit,
  checkpoint projection, queue status, artifact key construction, or active
  regroup marker updates.
- Recreating current helper signatures as "typed" APIs while still accepting
  open-ended `dict[str, Any]` payloads for core decisions.
- Using import-time store/runtime lookup, environment-variable branching, or
  package-level command runners to bypass dependency injection.
- Squashing many moved helpers into a generic `utils.py`, `helpers.py`, or
  `execution_common.py` module without an ownership boundary.
- Updating tests only by changing monkeypatch target strings without adding
  direct module tests for the extracted behavior.
- Treating the final full regression command as a replacement for the targeted
  tests required after each extraction.

Guardrails reviewers should enforce:

- Every mutating facade has an idempotency key and an owning typed row or
  explicit legacy compatibility reason.
- Every production side effect can be traced to one module owner.
- Every wrapper has a named removal condition.
- Every extraction PR states whether it is refactor-only or part of the final
  atomic feature landing.
- Every PR that touches `ImplementationPhase.execute`, `_implement_dag`, or
  quiesce paths runs the quiesce tests.
- Every extraction PR contains at least one negative test for a boundary it now
  owns, such as forbidden paths, stale projection, duplicate idempotency key,
  non-clean git state, queue lease conflict, or regroup rollback after the first
  wave.

## Edge Cases And Failure Handling

- Downstream imports from implementation phase must remain available until migrated.
- Test monkeypatch targets must keep compatibility wrappers or receive explicit test updates.
- Runtime-specific behavior must not be silently normalized until sandbox adapter tests exist.
- Quiesce behavior must remain unchanged during refactor-only slices.
- Existing artifact-only features must resume through legacy read adapters
  without synthetic typed backfill.
- A refactor-only PR that changes artifact payload ordering, omitted fields, or
  default values must be treated as behavior-changing and held for the final
  atomic landing unless tests prove compatibility.
- If a module extraction uncovers an existing bug, record it in the PR notes and
  either fix it in a separate behavior PR or carry a compatibility shim that
  preserves current behavior until the atomic landing.
- If an extracted module cannot reconstruct enough typed context, fail closed
  with a typed workflow/control-plane failure; do not broaden artifact scans.
- If a queue item or regroup overlay has crossed a product-authoritative
  boundary, rollback is forward-only: quiesce, recover, or drain under the
  current typed path rather than silently returning to legacy order.

## Tests

Run targeted tests after every extraction PR, then run the broader gate before
the final atomic feature landing. The paths below include existing regression
tests and exact new test names expected from the extraction work.

Minimum proof bundle for every extraction PR:

- Direct module tests for the new facade, including a success path, an
  idempotency or retry path when the module mutates state, and at least one
  fail-closed path for the boundary it owns.
- Compatibility shim smoke tests for any preserved `implementation.py` names
  that production code or existing tests still import or monkeypatch.
- Targeted legacy regression tests named in the table below.
- `python -m compileall -q` over the touched package path, plus the broader
  compile command before PR 11.12 and PR 11.13.
- `pytest tests/workflows/test_workflow_quiesce.py -q` whenever phase adapter,
  `_implement_dag`, scheduler/regroup resolution, or pause propagation code
  changes.
- A PR note stating "refactor-only" or "final atomic landing" and listing the
  artifact/event/payload compatibility check performed.

| Extraction PR | Exact tests after extraction |
| --- | --- |
| 11.0 types/adapters | Add `tests/workflows/develop/execution/test_adapters.py::test_default_adapters_preserve_legacy_callable_names`; add `tests/workflows/develop/execution/test_adapters.py::test_adapter_injection_replaces_monkeypatch_child_helper`; run `python -m compileall -q src/iriai_build_v2/workflows/develop/phases/implementation.py src/iriai_build_v2/workflows/develop/execution`; run `pytest tests/workflows/develop/execution/test_adapters.py tests/workflows/test_workflow_quiesce.py -q`. |
| 11.1 `git_service.py` | Add `tests/workflows/develop/execution/test_git_service.py::test_commit_clean_repo_returns_empty`; add `tests/workflows/develop/execution/test_git_service.py::test_commit_dirty_repo_returns_commit_hash`; add `tests/workflows/develop/execution/test_git_service.py::test_commit_pre_commit_hook_failure_raises_with_location`; add `tests/workflows/develop/execution/test_git_service.py::test_commit_failure_parser_extracts_husky_file_and_line`; add `tests/workflows/develop/execution/test_git_service.py::test_commit_failure_forbidden_status_routes_cleanup`; add `tests/workflows/develop/execution/test_git_service.py::test_no_dirty_proof_rejects_staged_and_unstaged_changes`; run `pytest tests/workflows/develop/execution/test_git_service.py tests/workflows/test_dag_expanded_verify.py::test_commit_repos_in_root_hard_fails_on_pre_commit_hook tests/workflows/test_dag_expanded_verify.py::test_commit_failure_issue_extracts_hook_file_and_line tests/workflows/test_dag_expanded_verify.py::test_commit_failure_manifest_forbidden_status_routes_to_cleanup tests/workflows/test_dag_expanded_verify.py::test_commit_repos_in_root_clean_repo_returns_empty_string tests/workflows/test_dag_expanded_verify.py::test_commit_repos_in_root_dirty_repo_returns_commit_hash tests/workflows/test_dag_expanded_verify.py::test_dag_checkpoint_commit_failure_blocks_group_and_writes_artifact -q`. |
| 11.2 `journal.py` | Add `tests/workflows/develop/execution/test_journal.py::test_task_attempt_projects_legacy_dag_task_shape`; add `tests/workflows/develop/execution/test_journal.py::test_group_checkpoint_projection_is_idempotent`; add `tests/workflows/develop/execution/test_journal.py::test_projection_transaction_rolls_back_artifact_on_typed_conflict`; add `tests/workflows/develop/execution/test_journal.py::test_resume_state_loads_legacy_artifact_only_feature_without_backfill`; add `tests/workflows/develop/execution/test_journal.py::test_projection_key_builders_are_journal_owned`; run `pytest tests/workflows/develop/execution/test_journal.py tests/workflows/test_workflow_quiesce.py -q`. |
| 11.3 `workspace_authority.py` | Add `tests/workflows/develop/execution/test_workspace_authority.py::test_alias_resolution_rewrites_registered_alias_only`; add `tests/workflows/develop/execution/test_workspace_authority.py::test_divergent_alias_blocks_before_dispatch`; add `tests/workflows/develop/execution/test_workspace_authority.py::test_acl_normalization_uses_agent_writeability`; add `tests/workflows/develop/execution/test_workspace_authority.py::test_outside_root_and_symlink_targets_fail_closed`; add `tests/workflows/develop/execution/test_workspace_authority.py::test_snapshot_projection_uses_journal_transaction`; run `pytest tests/workflows/develop/execution/test_workspace_authority.py tests/test_workspace_isolation.py -q`; run `pytest tests/workflows/test_dag_expanded_verify.py::test_worktree_alias_guard_reconciles_stale_dag_task_metadata tests/workflows/test_dag_expanded_verify.py::test_worktree_alias_guard_blocks_divergent_alias_without_operator tests/workflows/test_dag_expanded_verify.py::test_dag_writeability_rejects_outside_root_and_symlink_targets tests/workflows/test_dag_expanded_verify.py::test_normal_verify_acl_block_stops_before_repair_dispatch -q`. |
| 11.4 `task_contracts.py` and `sandbox.py` | Add `tests/workflows/develop/execution/test_task_contracts.py::test_contract_compiler_preserves_required_and_forbidden_paths`; add `tests/workflows/develop/execution/test_task_contracts.py::test_patch_validation_rejects_forbidden_existing_file`; add `tests/workflows/develop/execution/test_task_contracts.py::test_checkpoint_scope_requires_all_contracts_satisfied`; add `tests/workflows/develop/execution/test_sandbox.py::test_prepare_binds_runtime_to_sandbox_root`; add `tests/workflows/develop/execution/test_sandbox.py::test_capture_patch_records_immutable_evidence`; add `tests/workflows/develop/execution/test_sandbox.py::test_sandbox_patch_cannot_mutate_canonical_repo`; run `pytest tests/workflows/develop/execution/test_task_contracts.py tests/workflows/develop/execution/test_sandbox.py tests/workflows/test_dag_expanded_verify.py::test_dag_preflight_fails_forbidden_task_spec_path tests/workflows/test_dag_expanded_verify.py::test_dag_preflight_fails_manifest_forbidden_file_on_disk tests/workflows/test_dag_expanded_verify.py::test_manifest_forbidden_preflight_routes_to_focused_cleanup_after_permission_repair -q`. |
| 11.5 `dispatcher.py` | Add `tests/workflows/develop/execution/test_dispatcher.py::test_dispatch_records_attempt_with_runtime_policy`; add `tests/workflows/develop/execution/test_dispatcher.py::test_runtime_failure_records_typed_failure_once`; add `tests/workflows/develop/execution/test_dispatcher.py::test_retry_dedupe_reuses_dispatch_idempotency_key`; add `tests/workflows/develop/execution/test_dispatcher.py::test_resume_after_dispatch_reloads_existing_attempt`; add `tests/workflows/develop/execution/test_dispatcher.py::test_dispatcher_does_not_commit_or_checkpoint`; run `pytest tests/workflows/develop/execution/test_dispatcher.py tests/workflows/test_runtime_policy.py tests/workflows/test_dag_expanded_verify.py::test_dag_repair_runtime_roles_are_fixed -q`. |
| 11.6 `gates.py` | Add `tests/workflows/develop/execution/test_gates.py::test_preflight_blocks_structural_issues_before_dispatch`; add `tests/workflows/develop/execution/test_gates.py::test_raw_gate_verdict_required_before_checkpoint`; add `tests/workflows/develop/execution/test_gates.py::test_stale_context_gate_uses_bounded_context`; add `tests/workflows/develop/execution/test_gates.py::test_gate_evidence_projection_shape_matches_legacy`; run `pytest tests/workflows/develop/execution/test_gates.py tests/workflows/test_gate_feedback.py tests/workflows/test_dag_expanded_verify.py::test_dag_group_preflight_reports_structural_blockers tests/workflows/test_dag_expanded_verify.py::test_dag_group_preflight_uses_raw_verdict_for_checkpoint_gate tests/workflows/test_dag_expanded_verify.py::test_dag_preflight_blocks_repo_hygiene_leaks -q`. |
| 11.7 `verification.py` | Add `tests/workflows/develop/execution/test_verification.py::test_verify_group_runs_lenses_in_stable_order`; add `tests/workflows/develop/execution/test_verification.py::test_verify_context_is_bounded_and_deduped`; add `tests/workflows/develop/execution/test_verification.py::test_lens_failures_are_recorded_without_hiding_successes`; add `tests/workflows/develop/execution/test_verification.py::test_verifier_outcome_preserves_legacy_artifact_shape`; run `pytest tests/workflows/develop/execution/test_verification.py tests/workflows/test_dag_expanded_verify.py::test_dag_expanded_verify_merges_and_dedupes_lens_findings tests/workflows/test_dag_expanded_verify.py::test_run_expanded_dag_verify_lenses_records_successes_and_failures tests/workflows/test_dag_expanded_verify.py::test_collect_files_dedupes_preserving_first_seen_order -q`. |
| 11.8 `repair.py` and `failure_router.py` | Add `tests/workflows/develop/execution/test_failure_router.py::test_commit_hygiene_routes_to_focused_repair`; add `tests/workflows/develop/execution/test_failure_router.py::test_repeated_direct_route_blocks_spin`; add `tests/workflows/develop/execution/test_failure_router.py::test_acl_and_alias_route_to_workspace_repair`; add `tests/workflows/develop/execution/test_failure_router.py::test_checkpoint_contradiction_routes_to_quiesce`; add `tests/workflows/develop/execution/test_repair.py::test_product_repair_and_artifact_repair_use_separate_lanes`; add `tests/workflows/develop/execution/test_repair.py::test_artifact_repair_rejects_non_artifact_paths`; run `pytest tests/workflows/develop/execution/test_failure_router.py tests/workflows/develop/execution/test_repair.py tests/workflows/test_dag_expanded_verify.py::test_dag_direct_route_repeated_signature_blocks_spin tests/workflows/test_dag_expanded_verify.py::test_commit_only_retry_routes_directly_without_expanded_verify_or_rca tests/workflows/test_dag_expanded_verify.py::test_parallel_dag_repair_runs_scheduled_fixes_with_primary_runtime tests/workflows/test_dag_expanded_verify.py::test_parallel_dag_repair_rejects_unsafe_artifact_repair_and_persists_reason -q`. |
| 11.9 `merge_queue.py` | Add `tests/workflows/develop/execution/test_merge_queue.py::test_enqueue_is_idempotent_by_group_and_attempt`; add `tests/workflows/develop/execution/test_merge_queue.py::test_claim_uses_lease_fencing`; add `tests/workflows/develop/execution/test_merge_queue.py::test_apply_uses_immutable_sandbox_patch_evidence`; add `tests/workflows/develop/execution/test_merge_queue.py::test_commit_failure_projects_legacy_commit_failure_artifact`; add `tests/workflows/develop/execution/test_merge_queue.py::test_checkpoint_projection_requires_commit_and_no_dirty_proof`; add `tests/workflows/develop/execution/test_merge_queue.py::test_recover_expired_active_item_is_forward_only`; run `pytest tests/workflows/develop/execution/test_merge_queue.py tests/workflows/test_dag_expanded_verify.py::test_dag_checkpoint_commit_failure_blocks_group_and_writes_artifact tests/workflows/test_dag_expanded_verify.py::test_dag_retry_commit_failure_skips_reverify_and_becomes_next_issue tests/workflows/test_dag_expanded_verify.py::test_implement_dag_routes_implementation_commit_failure_to_repair_loop -q`. |
| 11.10 `regroup_overlay.py` | Add `tests/workflows/develop/execution/test_regroup_overlay.py::test_validate_rejects_dropped_dependency`; add `tests/workflows/develop/execution/test_regroup_overlay.py::test_activate_writes_restart_safe_marker`; add `tests/workflows/develop/execution/test_regroup_overlay.py::test_resolve_active_marker_after_restart`; add `tests/workflows/develop/execution/test_regroup_overlay.py::test_rollback_blocks_after_first_regrouped_wave_starts`; run `pytest tests/workflows/develop/execution/test_regroup_overlay.py tests/workflows/test_dag_regroup.py -q`; run `pytest tests/workflows/test_dag_expanded_verify.py::test_regroup_validation_rejects_invalid_derived_dags tests/workflows/test_dag_expanded_verify.py::test_artifact_repair_update_rejects_regroup_with_dropped_dependency tests/workflows/test_dag_expanded_verify.py::test_quiesce_hook_runs_after_group_44_before_group_45 -q`. |
| 11.11 `post_dag_gates.py` and `post_test_guard.py` | Add `tests/workflows/develop/execution/test_post_dag_gates.py::test_all_existing_post_dag_business_gates_run_after_effective_dag_completion`; add `tests/workflows/develop/execution/test_post_dag_gates.py::test_test_authoring_changes_use_typed_git_or_queue_proof`; add `tests/workflows/develop/execution/test_post_dag_gates.py::test_post_dag_gate_failure_routes_without_post_test_advance`; add `tests/workflows/develop/execution/test_post_test_guard.py::test_post_test_guard_uses_effective_dag_overlay`; add `tests/workflows/develop/execution/test_post_test_guard.py::test_post_test_guard_blocks_without_post_dag_gate_approval`; run `pytest tests/workflows/develop/execution/test_post_dag_gates.py tests/workflows/develop/execution/test_post_test_guard.py tests/workflows/test_workflow_quiesce.py -q`. |
| 11.12 `control_plane.py` and execute shrink | Add `tests/workflows/develop/execution/test_control_plane.py::test_control_plane_preserves_group_order_without_overlay`; add `tests/workflows/develop/execution/test_control_plane.py::test_control_plane_uses_active_overlay_before_dispatch`; add `tests/workflows/develop/execution/test_control_plane.py::test_control_plane_propagates_quiesce_without_advancing_phase`; add `tests/workflows/develop/execution/test_control_plane.py::test_control_plane_uses_merge_queue_for_checkpoint_authority`; add `tests/workflows/develop/execution/test_control_plane.py::test_control_plane_runs_post_dag_gates_before_phase_completion`; run `python -m compileall -q src/iriai_build_v2 dashboard.py`; run `pytest tests/workflows/develop/execution/test_control_plane.py tests/workflows/develop/execution/test_post_dag_gates.py tests/workflows/develop/execution/test_post_test_guard.py tests/workflows/test_workflow_quiesce.py tests/workflows/test_dag_expanded_verify.py tests/workflows/test_dag_regroup.py tests/test_workspace_isolation.py tests/workflows/test_runtime_policy.py tests/workflows/test_gate_feedback.py -q`. |
| 11.13 final atomic feature landing | Run `python -m compileall -q src/iriai_build_v2 dashboard.py`; run `pytest tests/workflows/test_dag_expanded_verify.py -q`; run `pytest tests/workflows/test_dag_regroup.py -q`; run `pytest tests/workflows/test_workflow_quiesce.py -q`; run `pytest tests/test_workspace_isolation.py -q`; run `pytest tests/supervisor -q`; run `pytest tests/workflows/test_threaded_planning.py -q`; run `pytest -q`. |

Always run quiesce tests after touching phase adapter code:

```bash
pytest tests/workflows/test_workflow_quiesce.py -q
```

## Acceptance Criteria

- Each extraction PR is behavior-preserving unless it is the final atomic
  execution-control-plane feature landing.
- There is no production shadow phase, dual writer, compatibility-only
  production mode, pilot lane, or behavior flag used as rollout.
- `implementation.py` remains readable as a phase adapter and compatibility shim.
- New modules own cohesive behavior with direct tests.
- Current tests remain green after every extraction step.
- Existing legacy imports and monkeypatch targets keep working until a PR
  explicitly migrates them and adds compatibility smoke coverage.
- Legacy artifact keys, event names, and payload shapes remain stable in
  refactor-only PRs.
- New execution-control-plane writes have a single typed owner and, where legacy
  compatibility is required, journal-owned projection links.
- No checkpoint can be emitted from task evidence alone; queue commit and
  no-dirty proof are required for the new product-authoritative path.
- No implementation phase can complete, and no post-test observation can start,
  until effective DAG completion and all preserved post-DAG business gates have
  typed evidence and compatibility projections.
- The final atomic landing passes the full regression gate before production
  enablement.

## Rollout/Rollback Notes

Prefer small extraction PRs as construction units into the control-plane
integration branch. They are not production rollout phases.

Rollback for a refactor-only extraction is code rollback: switch wrappers back
to old helper bodies or revert the extraction PR. Because refactor-only PRs do
not change artifact keys, checkpoint semantics, or production authority, they
must be individually reversible.

Rollback for the final atomic feature landing is application-level: stop new
control-plane starts, preserve typed rows and compatibility projections for
diagnosis, and resume only features that have not crossed a product-authoritative
control-plane boundary. Never delete root DAG artifacts, regroup artifacts,
queue rows, checkpoints, merge proofs, typed audit rows, or scheduler feedback
as rollback.

Do not combine unrelated line-of-business behavior changes with mechanical
extraction. Do combine the final production authority change with its schema,
journal, queue, projections, compatibility shims, and tests so the control plane
lands as one complete feature.

## Cross-Slice Dependencies

- This map guides all slices.
- Slices 1-10 define behavior; this slice defines safe extraction order.
- Slice 1 supplies typed journal rows, projection ownership, idempotency keys,
  and legacy compatibility artifacts.
- Slice 2 supplies canonical workspace, alias, ACL, and snapshot contracts.
- Slice 3 supplies deliverable contracts and write-set authority.
- Slice 4 supplies sandbox patch evidence and runtime binding constraints.
- Slice 5 supplies runtime invocation boundaries and retry dedupe expectations.
- Slice 6 supplies preflight, raw gate, stale-context, and verification ordering.
- Slice 7 supplies failure taxonomy, retry budgets, and escalation/quiesce routes.
- Slice 8 supplies the product-authoritative merge queue and checkpoint rules.
- Slice 9 supplies derived DAG overlay validation, activation, rollback, and
  scheduler safety.
- Slice 10 supplies bounded supervisor/dashboard read models and advisory-only
  control surfaces.
- Slice 6 and this slice jointly preserve feature-level post-DAG business gates
  and the effective-DAG readiness boundary before post-test observation.
- Slice 12 defines internal validation gates after each extraction; its filename
  is historical and must not be treated as a phased production rollout plan.
