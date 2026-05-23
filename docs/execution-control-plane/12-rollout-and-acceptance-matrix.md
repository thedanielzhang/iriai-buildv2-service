# 12. Atomic Landing And Acceptance Matrix

## Objective

Define the readiness gates, validation matrix, operational go/no-go decision,
whole-feature rollback, metrics, and acceptance criteria for the execution
control plane. This slice makes the architecture releasable as one complete
landing: internal implementation may proceed slice-by-slice, but production
authority is all-or-nothing.

## Current Code Citations

- Workflow phase transition path: [FeatureStore.transition_phase](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:47).
- Quiesce tests: [test_workflow_quiesce.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_workflow_quiesce.py:85), [test_workflow_quiesce.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_workflow_quiesce.py:141), and [test_workflow_quiesce.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_workflow_quiesce.py:206).
- Regroup validation tests: [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:4156) and [test_dag_regroup.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_regroup.py:621).
- Workspace isolation tests: [test_workspace_isolation.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/test_workspace_isolation.py:20).
- Process-improvement metrics: [identify_process_improvements](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1011).

## Current Failure Mode From `8ac124d6`

The workflow can make progress after localized fixes, but there is no single
release proof showing that retry cycles/task, stale projection recurrence,
commit loops, alias drift, ACL failures, checkpoint safety, and supervisor
visibility improve together. A partial production exposure would keep that
failure mode alive by allowing one control-plane path to become authoritative
while another required path is still legacy or unproven.

## Atomic Landing Contract

Production has exactly two supported execution modes:

1. Legacy executor only.
2. Complete execution control plane after the atomic landing gate passes.

There is no production mode for a pilot lane, canary lane, per-slice behavior
flag, per-feature subset, compatibility-only subset, or automatic migration of
an active legacy feature. Internal controls can exercise implementation slices
in CI, local validation, fixture replay, and private candidate environments, but
they must not decide execution authority, merge authority, checkpoint authority,
resume authority, or supervisor/operator authority in production.

The single product-authoritative switch is the complete-bundle enablement,
`IRIAI_EXEC_CONTROL_PLANE_ENABLED`. It can be turned on only for a deploy
artifact whose candidate commit has a recorded go decision. Turning that switch
on admits new starts to the complete control plane and unlocks an explicit
in-flight adoption workflow for eligible legacy features. It does not silently
change the resume path for a feature that already has legacy execution state.
Existing legacy features continue on the legacy executor until an adoption
record is written at a safe boundary under the fully validated control-plane
candidate.

## In-Flight Cutover Policy

Cut over as soon as safely possible after the complete control plane is
implemented and the atomic landing gate is green. "Safely possible" means the
feature is at a checkpoint or quiesce boundary, has no active agents, verifier,
RCA, repair, merge, or commit invocation, has clean canonical repositories, and
has a reconstructable effective DAG and compatibility projection state.

The runner must never infer adoption from the presence of typed tables,
configuration flags, or a restarted bridge. Adoption is explicit and durable:

1. The legacy feature reaches a safe boundary.
2. The adoption command reconstructs completed groups as sealed typed evidence,
   imports the active regroup overlay if one exists, compiles contracts and
   workspace snapshots for remaining work, initializes an empty merge queue, and
   validates projection parity.
3. The store writes an adoption marker, for example
   `execution-control-adoption:{feature_id}`, with the candidate commit,
   deploy artifact id, feature id, legacy root DAG id/hash, completed checkpoint
   range, next effective group, active regroup marker ids, workspace snapshot
   ids, compatibility projection digest, and rollback disposition.
4. Resume sees the adoption marker, verifies it against current Postgres and
   workspace state, and only then enters the control-plane resume path.

If any adoption check fails, the feature remains on the legacy executor or
quiesced before the next group. Failure to adopt is not a product failure and
must not route to RCA or broad repair.

The landing gate fails closed. Missing evidence, stale evidence, unowned
rollback, inconsistent typed/projection state, unknown queue state, or any
checkpoint safety gap is a no-go, even if all other slices pass.

## Proposed Interfaces/Types

This slice defines release-control interfaces, not executor runtime interfaces.

```python
class AtomicLandingGateResult(BaseModel):
    candidate_id: str
    candidate_commit: str
    deploy_artifact_id: str
    passed: bool
    required_tests: list[str]
    required_gate_results: dict[str, Literal["passed", "failed", "missing", "stale"]]
    ci_matrix_run_id: str | None
    metrics_snapshot_id: int | None
    operational_decision: Literal["go", "no_go"]
    decided_by: str | None
    decided_at: datetime | None
    rollback_runbook_id: str | None
    forbidden_partial_controls_enabled: list[str]
    blockers: list[str]

class WorkflowImprovementMetrics(BaseModel):
    feature_id: str
    candidate_id: str
    validation_corpus_id: str
    retry_cycles_per_task: float
    commit_failures_per_task: float
    stale_projection_count: int
    alias_or_acl_failures: int
    checkpoint_safety_regressions: int
    workflow_drag_hours: float
    tasks_per_hour: float
    operator_required_escalations: int
    db_rss_regression_pct: float
    postgres_bytes_growth_pct: float
    complexity_adjusted_tasks_per_hour: float
    baseline_retry_cycles_per_task: float
    baseline_commit_failures_per_task: float
    baseline_stale_projection_count: int
    baseline_workflow_drag_hours: float
    baseline_complexity_adjusted_tasks_per_hour: float

class InFlightAdoptionRecord(BaseModel):
    feature_id: str
    candidate_commit: str
    deploy_artifact_id: str
    legacy_root_dag_artifact_id: int
    legacy_root_dag_sha256: str
    completed_checkpoint_range: tuple[int, int]
    next_effective_group_idx: int
    active_regroup_artifact_ids: list[int]
    workspace_snapshot_ids: list[int]
    projection_digest: str
    adoption_marker_artifact_id: int | None
    adopted_at: datetime
    rollback_disposition: Literal["legacy_resume_before_next_group", "control_plane_only_after_next_attempt"]
    blockers: list[str]
```

## Refactoring Steps

1. Build control-plane slices in dependency order behind internal development
   controls. Those controls may exercise code in CI and local validation, but
   they must not create a product-authoritative subset.
2. Add landing-gate helpers after typed journal exists so gate results can be
   stored as typed evidence and projected for review.
3. Add a command or test helper that gathers required metrics from typed state,
   legacy summaries, and the `8ac124d6` evidence baseline.
4. Add one production enablement control for the complete execution control
   plane: `IRIAI_EXEC_CONTROL_PLANE_ENABLED`. Per-slice controls are temporary
   construction and validation switches only, and the landing gate must assert
   that no per-slice control is being used as production authority.
5. Wire the CI matrix so every required test group reports a named result, test
   command, candidate commit, start time, finish time, and freshness verdict into
   the landing gate.
6. Add the operational go/no-go checklist, alert owner, rollback command path,
   queue-drain procedure, and typed/projection consistency check before the
   complete control plane can be enabled.
7. Add the in-flight adoption command and resume guard. The command is allowed
   only after the complete-bundle go decision and only at checkpoint/quiesce
   boundaries. It writes the adoption marker described above and must be tested
   against legacy artifact-only state, active regroup overlays, and clean
   boundary adoption.
8. Keep existing in-flight legacy features on the legacy executor unless the
   adoption marker is present and verifies against current state. The desired
   operating posture after landing is to run adoption at the first eligible
   safe boundary, not to leave long-running features on the legacy executor by
   default.
9. Add a startup guard that refuses new control-plane starts when the deploy
   artifact does not match the go-approved candidate commit, required migrations
   are missing, the global switch is disabled, or any forbidden partial control
   is enabled for production authority.

## Persistence And Artifact Compatibility

- Atomic landing gate results may be stored as typed evidence and projected as
  `review:execution-control-landing:{candidate}` artifacts.
- Existing artifacts and events remain the source for legacy feature metrics
  until typed state is available.
- Internal validation controls must not mutate active `dag`, completed
  checkpoints, or active regroup artifacts unless the complete control plane is
  enabled for that feature.
- Compatibility projections must be synchronous with typed writes before any
  control-plane decision becomes product-authoritative.
- Rollback keeps typed audit rows, compatibility projections, merge/commit
  proofs, and checkpoint evidence for diagnosis. It must not delete completed
  checkpoints, root DAG artifacts, active regroup artifacts, or typed audit
  rows.

## Edge Cases And Failure Handling

- Active legacy feature: do not migrate automatically. Continue through the
  legacy executor until a safe boundary adoption marker is written and verified.
  After full landing, the expected operational behavior is to adopt eligible
  in-flight work promptly at the first safe boundary.
- Stale or partial adoption marker: reject the marker, keep the feature on the
  legacy executor or quiesced before the next group, and record a workflow
  adoption failure. Do not route to product repair.
- Feature has active legacy invocation: adoption is blocked until the invocation
  completes, fails, or is quiesced and the checkpoint/attempt state is
  reconciled.
- One internal slice passes but the full bundle fails: no production landing.
  Keep the global control-plane enablement disabled and record blockers.
- Typed tables unavailable before enablement: mark the landing gate no-go. Typed
  tables unavailable after enablement: quiesce new control-plane starts and roll
  back the deployment; do not silently route new starts to the legacy executor.
- Queue poisoned item: quiesce the control-plane feature and require workflow
  diagnosis, not product repair.
- Supervisor typed snapshot unavailable: expose a degraded read-only summary
  and block landing if bounded visibility cannot be proven.
- DB safety budget exceeded: mark the gate no-go until typed dashboard polling,
  projection volume, or query shape is corrected. Do not broaden artifact reads
  to compensate.

## Tests

- Atomic landing gate fails if any required test group is missing, failed, or
  stale relative to the candidate commit.
- Per-slice development controls cannot make product-authoritative decisions
  when the complete control plane is disabled.
- Metrics collector can compare legacy artifact/event evidence, typed state,
  and the `8ac124d6` baseline.
- In-flight adoption command reconstructs sealed completed groups, imports the
  active regroup overlay, compiles remaining contracts/workspace snapshots,
  initializes an empty merge queue, writes an adoption marker, and resumes on
  the control-plane path only after marker verification.
- Stale, partial, mismatched, or active-invocation adoption attempts fail closed
  without entering RCA, repair, post-DAG gates, or post-test observation.
- Whole-feature rollback preserves legacy resume and quiesce behavior.
- Full regression command list stays executable from repo root.
- Checkpoint safety tests prove no `dag-group:*` projection is written without
  gate evidence, merge proof, commit proof, and no-dirty proof.

## Internal Build Controls And Hard Gates

Per-slice controls exist only to build and validate the implementation. They
must be unavailable as a production acceptance strategy. The production landing
decision is made once, for the complete control plane, and the landing record
must prove every slice is present in the same candidate commit.

| Slice | Internal development control | Product-authoritative alone | Hard readiness gate |
| --- | --- | --- | --- |
| 00 Evidence | Fixture replay only | No | `8ac124d6` failure classes are reproducible, baseline metrics are stored, and validation corpus membership is frozen. |
| 01 Typed journal | `IRIAI_EXEC_JOURNAL_SHADOW` | No | Projection parity tests, resume tests, synchronous compatibility projection, no DB/RSS regression over 10%. |
| 02 Workspace authority | `IRIAI_WORKSPACE_AUTHORITY_BLOCKING` | No | Alias/ACL targeted tests and zero operator-required escalation for resolvable workspace classes. |
| 03 Contracts | `IRIAI_TASK_CONTRACTS_BLOCKING` | No | Contract compiler tests, forbidden/required path tests, and reviewed false-positive budget. |
| 04 Sandbox | `IRIAI_SANDBOX_CAPTURE_SHADOW` | No | Runtime binding tests, isolated write tests, and patch capture tests. |
| 05 Dispatcher | `IRIAI_RUNTIME_DISPATCHER_V2` | No | Runtime failure, retry dedupe, timeout, and resume-after-dispatch tests. |
| 06 Gates | `IRIAI_VERIFICATION_GRAPH_V2` | No | Raw gate, stale context, verifier crash, and bounded-read tests. |
| 07 Failure router | `IRIAI_FAILURE_ROUTER_V2` | No | Route tests for commit, ACL, alias, stale projection, runtime, queue, and product failure classes. |
| 08 Merge queue | `IRIAI_MERGE_QUEUE_V2` | No | Queue idempotency, status rollback, crash recovery, no-dirty proof, and checkpoint projection tests. |
| 09 Regroup overlay | `IRIAI_REGROUP_OVERLAY_V2` | No | Dependency, write-set, activation, rollback, and scheduler-safety tests. |
| 10 Supervisor typed snapshot | `IRIAI_SUPERVISOR_TYPED_SNAPSHOT` | No | Bounded snapshot, Slack dedupe, deterministic unblock priority, and read-only mutation tests. |
| 11 Refactor map | Extraction branch only | No | Imports, wrapper boundaries, post-DAG gate preservation, post-test readiness guard, monkeypatch compatibility, and targeted extraction tests prove behavior-preserving movement. |
| 12 Atomic landing gate | `IRIAI_EXEC_CONTROL_PLANE_ENABLED` | Complete bundle only | All required gates green, operational go decision recorded, zero checkpoint safety regressions, and required metric deltas met. |

## Readiness Gates

A gate can be marked green only when its proof is generated from the same
candidate commit as the deploy artifact, includes a freshness timestamp, and is
linked from the `AtomicLandingGateResult`. A skipped gate is a failed gate.

| Gate | Required proof | No-go signal |
| --- | --- | --- |
| Atomic enablement | `IRIAI_EXEC_CONTROL_PLANE_ENABLED` is the only production authority switch, all internal controls are recorded as non-authoritative, and candidate commit matches the deploy artifact. | Any per-slice control, pilot lane, canary lane, or feature subset can affect production authority. |
| Schema and journal | Typed rows, idempotency keys, projection links, and reconstruction tests pass. | Missing migration, non-idempotent write, or typed success without legacy projection visibility. |
| Workspace and contracts | Canonical repo identity, ACL/writeability, allowed/required/forbidden outputs, and contract ids are recorded before dispatch. | Implementer can start with unresolved alias, ACL, symlink, outside-root, or dirty generated-output ambiguity. |
| Sandbox and dispatcher | Implementers run in isolated roots, runtime attempts are typed, and provider failures route without hidden side effects. | Sandbox can mutate canonical repos or dispatcher can commit/checkpoint. |
| Verification and routing | Gate evidence records exact context and failure router maps workflow classes deterministically. | Summary-only or stale context approves merge, or workflow-class failure routes to broad product repair. |
| Merge and checkpoint | Queue owns canonical apply, commit, no-dirty proof, crash recovery, and `dag-group:*` projection. | Any checkpoint can be written without linked gate, merge, commit, and no-dirty evidence. |
| Post-DAG business gates | Code review, security audit, test authoring, QA, integration testing, final verifier, source push, reports, and notification are represented as feature-level gate evidence after effective DAG completion. | Implementation can complete or post-test observation can start after group checkpoint completion while any preserved business gate is missing, failed, or stale. |
| Consumers | Resume, dashboard, supervisor, regroup, and post-test readers use typed snapshots or synchronous compatibility projections. | Consumer requires broad artifact-body scan or sees inconsistent typed/projection state. |
| Resource safety | Median DB/RSS and Postgres growth stay within budget against baseline. | `db_rss_regression_pct` or `postgres_bytes_growth_pct` exceeds 10% without explicit operational approval. |
| Operations | Runbook, alerting, rollback command path, queue drain procedure, active-feature disposition, and owner signoff are recorded. | Missing owner, stale runbook, unknown queue item state, unreconciled active feature, or untested rollback command path. |

## CI/Test Matrix

Every matrix row is required for atomic landing. A row is accepted only when it
records the exact command or coverage source, candidate commit, run id, pass/fail
state, and freshness verdict. "Not implemented yet" is a no-go until the test
exists or the gate owner records an equivalent checked-in test path.

| Test group | Commands or coverage | Evidence accepted only when | No-go signal |
| --- | --- | --- | --- |
| Static import and syntax | `python -m compileall -q src/iriai_build_v2 dashboard.py` | Command passes from repo root on the candidate commit. | Import/syntax failure, missing generated module, or command run on another commit. |
| Expanded verification and regroup | `pytest tests/workflows/test_dag_expanded_verify.py -q` and `pytest tests/workflows/test_dag_regroup.py -q` | Existing regroup, expanded verifier, and quiesce-hook fixtures remain green. | Stale projection, invalid derived DAG, regroup activation, or scheduler-safety regression. |
| Quiesce and resume safety | `pytest tests/workflows/test_workflow_quiesce.py -q` | Legacy quiesce/resume behavior and typed recovery boundaries both pass. | Any active feature can lose checkpoint/resume safety during landing or rollback. |
| Workspace authority | `pytest tests/test_workspace_isolation.py -q` plus alias/ACL contract tests from Slice 02 | Canonical repo identity, ACL/writeability, symlink, outside-root, and dirty generated-output cases are covered. | Resolvable workspace failure still requires operator intervention or hidden alias repair. |
| Contracts and sandbox | Contract compiler/path-rule tests from Slice 03 plus isolated write and patch-capture tests from Slice 04 | Required/forbidden outputs are enforced before dispatch and sandbox cannot mutate canonical repos. | Implementer can write outside contract or bypass sandbox patch evidence. |
| Dispatcher, gates, and router | Runtime failure/retry tests from Slice 05, verification graph tests from Slice 06, failure-route coverage from Slice 07 | Runtime attempts, gate verdicts, and workflow/product routes are typed and deterministic. | Retry dedupe breaks, stale context approves merge, or workflow failure routes to broad product repair. |
| Merge queue and checkpoint proof | Queue idempotency, status rollback, crash recovery, commit proof, and no-dirty proof tests from Slice 08 | Queue owns canonical apply/commit and every checkpoint projection links gate, merge, commit, and no-dirty proof. | Any `dag-group:*` projection can be written without complete proof. |
| Post-DAG and post-test compatibility | `pytest tests/workflows/develop/execution/test_post_dag_gates.py tests/workflows/develop/execution/test_post_test_guard.py -q` plus existing quiesce tests | Existing post-DAG business gates remain present, effective-DAG completion is used for readiness, and observation cannot start early. | Any preserved gate is skipped, post-test reads root-DAG length alone, or quiesce/incomplete DAG advances to observation. |
| Supervisor and dashboard read models | `pytest tests/supervisor -q` plus bounded snapshot tests from Slice 10 | Read models are bounded, read-only, deduped, and consistent with synchronous projections. | Broad artifact-body scan, mutation through dashboard/supervisor, or inconsistent typed/projection state. |
| Planning compatibility | `pytest tests/workflows/test_threaded_planning.py -q` | Existing planning behavior remains compatible with typed contracts and projections. | Planning fixtures require legacy-only state that the landing cannot project. |
| Full regression | `pytest -q` | Entire suite passes after all targeted rows pass. | Any failing, missing, skipped, or stale required test row. |

## Operational Go/No-Go

The operations decision is recorded on the `AtomicLandingGateResult`.

Go requires:

- Candidate commit, deploy artifact id, schema migration set, and global
  enablement value are frozen in the decision record.
- Every readiness gate is green on the candidate commit.
- CI/test matrix results are present, passing, and not stale.
- Metrics snapshot compares the candidate against the `8ac124d6` baseline and
  the current legacy baseline.
- The global control-plane enablement owner, alert owner, rollback command path,
  queue-drain procedure, and active-feature disposition are documented.
- Eligible in-flight features have an adoption plan that targets the first safe
  checkpoint/quiesce boundary after landing.
- Startup guard has proven it rejects missing migrations, mismatched deploy
  artifacts, disabled global enablement, and forbidden partial controls.
- No active legacy feature will be silently or partially migrated.
- No unresolved queue item, dirty canonical repo, or checkpoint evidence gap
  exists.
- Supervisor/dashboard views can explain the candidate, current queue state,
  active-feature state, and rollback state without broad artifact scans.

No-go is mandatory when any required test is missing or failed, any typed
projection is stale, any checkpoint safety regression is detected, resource
growth exceeds budget, a consumer can observe inconsistent state, or rollback
ownership is unclear.

The go decision authorizes two actions on the approved deploy artifact: enabling
the complete control plane for new starts, and running explicit adoption for
eligible in-flight legacy features at safe boundaries. It does not authorize
enabling an individual slice, routing selected production traffic through the
new path, silently switching legacy resume into the control plane, or silently
falling back from a control-plane start to the legacy executor.

## Rollout/Rollback Notes

Rollback is for the complete execution control plane, not for an individual
product-authoritative subset.

1. Disable `IRIAI_EXEC_CONTROL_PLANE_ENABLED` for new starts.
2. Confirm the startup guard rejects new control-plane starts and that no
   per-slice internal control can continue production admission.
3. Stop control-plane dispatcher and merge queue workers after recording their
   last observed queue item ids, leases, candidate id, and deploy artifact id.
4. Snapshot typed state, compatibility projection links, active feature ids,
   queue status counts, and canonical repo cleanliness before recovery actions.
5. Reconcile each queue item through the status rollback table in
   [08-durable-merge-queue.md](08-durable-merge-queue.md); completed queue
   items remain audit evidence.
6. Quiesce any active control-plane feature at a checkpoint or typed recovery
   boundary. If a feature has passed the legacy/control-plane boundary, do not
   downgrade it mid-attempt; quiesce, drain, or restart intentionally from a safe
   checkpoint under a later fully validated candidate.
7. Do not delete typed rows, legacy projections, merge proofs, commit proofs,
   checkpoint artifacts, root DAG artifacts, active regroup artifacts, or typed
   audit rows.
8. Verify canonical repos are either clean or have a linked merge/commit proof.
9. Resume future new work on the legacy executor or restart intentionally under
   a later fully validated control-plane candidate.
10. Run quiesce/resume, workspace isolation, merge queue rollback, and supervisor
    bounded-read tests before declaring rollback complete.

Rollback completion requires an operational record with the disabled global
switch value, reconciled queue item ids, active feature disposition, remaining
typed/projection evidence, and owner signoff. A rollback that leaves unknown
queue state, dirty canonical repos without proof, or unexplained checkpoint
projections is incomplete.

## Success Metrics

The metrics snapshot is accepted only when it names the validation corpus,
candidate commit, baseline source, collection time range, and query version.
Metrics are evaluated as a bundle. Throughput or latency gains cannot offset a
checkpoint safety regression, stale projection recurrence, or unresolved
workspace/commit workflow class.

Metric formulas:

- `retry_cycles_per_task = typed_retry_count / completed_task_count`.
- `commit_failures_per_task = commit_failure_count / completed_task_count`.
- `workflow_drag_hours = sum(duration for typed failures where failure_class in ('worktree_alias', 'acl_workability', 'stale_projection', 'commit_hygiene', 'runtime_provider', 'merge_conflict', 'checkpoint_contradiction'))`.
- `checkpoint_safety_regressions = count(checkpoint without gate evidence, merge proof, commit proof, or no-dirty proof)`, required to be zero.
- `operator_required_escalations = count(workflow-class failures that needed manual operator intervention despite a deterministic route existing)`, required to be zero for resolvable workspace/projection/commit-only classes.
- `db_rss_regression_pct` and `postgres_bytes_growth_pct` compare validation median to pre-control-plane baseline and must stay under 10% unless explicitly approved in the go/no-go record.
- `complexity_adjusted_tasks_per_hour = completed_task_count / sum(task_complexity_weight * wall_clock_hours)`.
- `task_complexity_weight = 1 + 0.25 * backend_repo_count + 0.25 * cross_repo_flag + 0.25 * generated_output_flag + 0.25 * unknown_write_set_flag + 0.1 * verification_gate_count`, capped at `2.5`.

Success means:

- `retry_cycles_per_task <= 0.8 * baseline_retry_cycles_per_task` over the
  validation corpus, or `<= 0.25` when the baseline is too small for a stable
  ratio.
- `workflow_drag_hours <= 0.7 * baseline_workflow_drag_hours` for
  `worktree_alias`, `acl_workability`, `stale_projection`, `commit_hygiene`,
  `runtime_provider`, `merge_conflict`, and `checkpoint_contradiction` failures.
- `commit_failures_per_task <= 0.75 * baseline_commit_failures_per_task`, or
  `<= 0.05` when the baseline is near zero.
- `stale_projection_count <= 0.5 * baseline_stale_projection_count`, or `<= 1`
  in validation fixtures with fewer than three baseline stale projections.
- `complexity_adjusted_tasks_per_hour >= 0.95 *
  baseline_complexity_adjusted_tasks_per_hour`; any lower value is a no-go even
  if checkpoint latency improves.
- `checkpoint_safety_regressions == 0`.
- `operator_required_escalations == 0` for resolvable workspace, projection, or
  commit-only classes.
- `alias_or_acl_failures == 0` for fixture classes that should be resolved by
  workspace authority before dispatch.
- No required metric is missing, manually adjusted outside the recorded query
  version, or collected from a different candidate commit.

## Full Regression Gate

Run after each behavior-changing internal slice and immediately before atomic
landing:

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

## Acceptance Criteria

The atomic landing record is complete only when all of these statements are
true:

- Production authority is enabled only for the complete execution control plane:
  typed journal, workspace authority, contracts, sandbox runner, dispatcher
  boundary, gates, failure router, merge queue, regroup feedback, and
  supervisor/dashboard integration.
- No slice can independently change production execution, merge, checkpoint,
  resume, or supervisor/operator authority.
- Existing in-flight legacy features are not migrated automatically.
- Eligible in-flight legacy features have a documented first-safe-boundary
  adoption path so they can cut over promptly after landing without a silent
  mid-run switch.
- Every product-authoritative side effect has typed evidence, a stable
  idempotency key, and a compatibility projection where legacy consumers still
  depend on artifacts.
- Effective DAG completion and all preserved post-DAG business gates are proven
  before implementation completion or post-test observation readiness.
- CI matrix results, readiness gates, metrics, operational decision, and
  rollback evidence all reference the same candidate commit and deploy artifact.
- Whole-feature rollback preserves resume safety and audit evidence.
- Success metrics compare against `8ac124d6` evidence and a current legacy
  baseline, with zero checkpoint safety regressions.
- The final record says either `go` for complete-bundle enablement or `no_go`.
  It cannot approve a partial production path.

## Cross-Slice Dependencies

- Atomic landing requires Slices 00-12 to pass together.
- Slice 00 supplies the baseline and validation corpus for success metrics.
- Slice 01 is required before any component can record authoritative typed
  evidence.
- Slices 02 and 03 must complete before sandbox, dispatcher, gates, router, or
  merge queue can make safe decisions.
- Slices 04-07 must complete before Slice 08 can own canonical repository
  mutation and checkpoint projection.
- Slices 09 and 10 remain read-only or feedback-only until typed snapshots and
  compatibility projections are proven consistent.
- Slice 11 guides extraction order but does not create production authority.
- Slice 12 is the single acceptance record for readiness gates, CI, metrics,
  operational go/no-go, and whole-feature rollback.
