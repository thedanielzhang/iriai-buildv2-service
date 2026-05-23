# 00. Evidence And Current State

## Objective

Document why the execution control plane is needed, using feature `8ac124d6` as
the source of evidence. `8ac124d6` is the workflow feature id, not a Git
revision in this repository. This slice does not implement behavior. It creates
the shared factual baseline, fixture schema, and metric definitions that all
later slices must preserve.

The production target is one atomic landing for new execution behavior after the
full control-plane contract is implemented, fixture-proven, and regression
gated. Shadow data collection in this slice is read-only evidence work; it is
not a phased production rollout.

The evidence window is live-state sensitive. Older notes captured groups 0-38
while G38 was active on 2026-05-06. A read-only Postgres probe on 2026-05-18
showed the same feature still in `implementation`, with latest checkpoint
`dag-group:69` and G70 active. Fixture builders must therefore freeze explicit
high-water artifact/event ids and hashes instead of assuming any named group is
still current.

## Current Code Citations

- Durable feature, event, and artifact tables: [schema.sql](/Users/danielzhang/src/iriai/iriai-build-v2/schema.sql:1).
- DAG execution and resume: [_implement_dag](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4647).
- Verify, repair, retry, and checkpoint loop: [_verify_and_fix_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2925).
- Per-task resume, dispatch, runtime call, result events, and `dag-task:*` writes: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4865), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4962), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5036), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5060), and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5147).
- Initial and retry verifier events/artifacts: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3026), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3075), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4106), and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4145).
- Direct repair routing and repeated deterministic route guard: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3127), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3145), and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3194).
- Worktree registry models: [WorktreeRegistryRepo](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1395) and [WorktreeRegistry](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1422).
- Worktree creation and registry persistence: [_ensure_task_worktrees](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1635).
- Runtime DAG path canonicalization, task/spec reconciliation, and deterministic preflight: [_record_dag_path_canonicalization](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2782), [_reconcile_dag_task_specs](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:11149), [_reconcile_dag_task_results](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:13444), and [_run_dag_group_preflight](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:14135).
- Commit helpers: [_commit_repos](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5686), [_commit_repos_in_root](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5732), and [_commit_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5904).
- Checkpoint commit failure handling and `dag-group:*` writes: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4166), [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4173), and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4203).
- Worktree alias pre-dispatch guard: [_run_worktree_alias_pre_dispatch_guard](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:13891).
- Commit failure persistence, direct-route classification, and authority-gate repair: [_record_dag_commit_failure](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:789), [_classify_dag_direct_repair_route](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:890), and [_attempt_dag_authority_gate_repair](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:14953).
- Artifact latest-by-key and bounded summary/slice APIs: [PostgresArtifactStore.get](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:71), [get_record](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:82), [list_record_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:143), [latest_summary](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:211), and [get_slice](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:242).
- Broad artifact and event reads to avoid in fixture traversal except for explicit single-row inspection: [PostgresArtifactStore.list_records](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:99) and [FeatureStore.get_events](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:115).
- Event logging, bounded event summaries, and advisory locks: [FeatureStore.log_event](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:55), [list_event_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:122), and [advisory_lock](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:164).
- DAG/task/result models: [ImplementationTask](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:942), [ImplementationDAG](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:984), [DerivedDAGArtifact](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:994), and [ImplementationResult](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:1022).
- Supervisor priority order: [SupervisorClassifier.classify](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:24).
- Sizing metrics, process-improvement extraction, adaptive sizing, and DB safety snapshot: [collect_sizing_metrics](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:836), [identify_process_improvements](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1011), [recommend_adaptive_sizing](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1256), and [_safety_snapshot](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1825).
- Metric classification coverage for observed drag classes: [test_process_improvements_rank_observed_drag_classes](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_regroup.py:546).
- Bounded artifact-summary query coverage: [test_artifact_store_list_record_summaries_uses_bounded_projection_query](/Users/danielzhang/src/iriai/iriai-build-v2/tests/test_storage_artifacts.py:164).

## Current Failure Mode From `8ac124d6`

The feature made progress, but the workflow lost time in repeatable infrastructure classes:

- Worktree alias drift: implementation evidence and verifier context referenced `*-wt` or other non-canonical paths while canonical verifiers read the feature repo.
- ACL/writeability gaps: checks sometimes passed as the bridge/operator user while actual agent users could not create or modify canonical files.
- Stale projection: old `dag-task:*`, generated context, or material artifacts remained visible to verifier/RCA prompts after deterministic repair.
- Claimed-file oscillation: retries created, deleted, or moved files that remained in task evidence even after the canonical state changed.
- Commit hygiene loops: commit-only failures entered broad repair or verifier loops rather than a narrow commit-hygiene lane.
- Product contract drift: generated catalogs, backend/package mirrors, and message-store contracts failed late because no typed deliverable contract owned the expected surfaces.
- Runtime/provider stalls: provider stalls and retry events were visible inside the same event stream as product verification and checkpoint decisions.

## Seed `8ac124d6` Evidence Baseline

The rows below are not the final fixture. They are the minimum seed evidence the
fixture builder must reproduce with bounded reads and explicit high-water ids.
Where the feature kept progressing after a note was written, the fixture must
prefer the frozen read window over "latest" claims.

Current-state probe, read-only on 2026-05-18 PDT. The SQL probe hash below is
only a quick identity check; the fixture must compute the required SHA-256 or
content-ref hash through the collector.

| Evidence | Observed row/count | Why it matters |
| --- | --- | --- |
| Feature state | `features.id=8ac124d6`, phase `implementation`, updated `2026-05-17 23:14:56 -07` during the probe window | The feature was still active; evidence collection cannot assume completion. |
| Root DAG | latest `dag` artifact id `810505`, created `2026-04-25 20:08:24 -07`, length `11240578`, probe md5 `1f0b67de7c3ab8e7c1feb55faf9692c8` | Fixture metadata must pin the root DAG row and digest before deriving group windows. |
| Regroup overlay | `dag-regroup:g45-g73` artifact id `1632760`; `dag_regroup_overlay_applied` event `34657` records `group_idx_offset=45`, `base_dag_artifact_id=810505`, and `effective_group_count=146` | The active execution order differs from the root DAG after G45; metrics must use effective groups. |
| Latest checkpoint in probe | `dag-group:69` artifact id `1885599`, created `2026-05-18 15:49:27 -07`; G70 events/artifacts followed | The evidence window must include active group data after the latest checkpoint. |
| Event/artifact volume | Initial count saw `63724` artifact rows and `30300` event rows for the feature; top event counts included `10393 agent_start`, `9973 agent_done`, `606 dag_task_start`, `585 dag_task_finish`, `417 dag_verify_start`, `413 dag_verify_finish`, `339 dag_repair_cycle_start`, `256 dag_expanded_verify_start`, `254 dag_expanded_verify_finish`, and `111 dag_commit_failed` | The fixture must use summaries/pages. Broad row hydration is not viable or acceptable. |
| G70 active tail | `dag_commit_failed` event `34605`, `dag_verify_finish` events `34615` and `34647`, `dag_expanded_verify_finish` event `34636`, and active reverify event `34658` appeared in later reads while the probe ran | Evidence collection must record high-water ids at start/end and classify out-of-window rows explicitly. |

Historical seed evidence from groups 28-38:

| Evidence | Primary refs | Current reading |
| --- | --- | --- |
| Late retry tail | G28-G38 produced repeated preflight, verify, expanded-verify, fix, RCA, task-reconcile, and spec-reconcile rows; G30 alone had `19` verify artifacts, `13` expanded verify artifacts, `19` task-reconcile rows, `2` task-spec-reconcile rows, and `15` `dag_repair_cycle_start` events | The late tail was dominated by workflow/reconciliation drag, not only product implementation. |
| G30 stale projection closure | `artifact:dag-repair-preflight:g30:retry-initial id=1044830`, `artifact:dag-repair-preflight:g30:retry-initial id=1052604`, `artifact:dag-repair-triage:g30:retry-0 id=1078324`, `artifact:dag-verify:g30:initial id=1084035`, `event:20233` | Stale retired chat paths appeared first in task results, then in source DAG/task-spec fragments and generated context. |
| G37 checkpoint contradiction | `artifact:dag-verify:g37:initial id=1273016` was `approved=false` for manifest-forbidden workspace/index state; `artifact:dag-group:37 id=1273018`, `event:23309`, and `event:23310` followed | Checkpoint projection could be written despite a raw deterministic preflight failure. |
| G38 stale projection repaired, product defects remained | `artifact:dag-path-canonicalization:g38 id=1351094`, `artifact:dag-task-reconcile:g38:retry-initial id=1351096`, `artifact:dag-task-spec-reconcile:g38:retry-initial id=1351097`, `artifact:dag-repair-preflight:g38:retry-initial id=1351098`, `artifact:dag-verify:g38:initial id=1351629` | Host reconciliation made preflight clean, then real product/backend verifier failures surfaced. |
| G38 commit hygiene | `artifact:dag-commit-failure:g38:retry-0 id=1316714`, later `artifact:dag-commit-failure:g38:retry-0 id=1353600`, and `event:24113`/`event:24286` | Commit/hook failures became durable evidence, but still consumed broad retry/verify surface before focused routing was reliable. |
| G38 ACL/writeability | `event:23395`, `artifact:dag-verify-rca:g38:retry-0 id=1278566`, and `artifact:dag-verify-rca:g38:retry-1 id=1279962` | Permission/writeability failures were mixed with product repair and needed earlier deterministic gating. |

Current live evidence after regroup:

| Bottleneck | Primary refs | Current reading |
| --- | --- | --- |
| Worktree alias drift | `artifact:dag-worktree-alias-preflight:g60:initial-dispatch id=1833161` and id `1833214` show `iriai-studio-backend-wt -> iriai-studio-backend`, `approved=false`, `worktree_alias_divergent=true`, and `repair_route=product_cleanup_required` for `dag-task:T-sf6-s17-001`; G66-G70 also produced alias preflight rows | Alias handling is active and sometimes blocks dispatch; it cannot remain prompt-local or dashboard-inferred. |
| ACL/writeability gaps | `artifact:dag-writeability-preflight:g39:initial id=1360859`; `artifact:dag-writeability-preflight:g48:retry-0 id=1679162` and id `1681020` record `operator_required=true` plus ACL normalization attempts | Writeability has to be checked as the actual runtime user and routed before normal repair dispatch. |
| Commit hygiene loops | `artifact:dag-commit-failure:g70:implementation id=1886229`, `event:34605`, and `artifact:dag-direct-repair-route:g70:retry-0 id=1886244` with route `commit_hygiene_focused` | Focused routing now exists, but the existence of 111 `dag_commit_failed` events shows why commit proof must be a first-class queue gate. |
| Product contract drift after task success | `artifact:dag-verify:g70:retry-0 id=1886292` and `artifact:dag-verify:g70:retry-1 id=1886458` cite perf-baseline schema/trailer and AC-94/95/96 failures after `dag-task:TASK-SF8-S8-4 id=1886224` and other task results | Task result success is not contract satisfaction or integration proof. |
| Runtime/provider stall | `event:34600 agent_stalled`, `event:34601 agent_invocation_start`, `event:34602 agent_start`, and `event:34603 agent_done` for `implementer-g70-t3-a0` | Runtime retry evidence must be typed separately from product verifier outcomes. |

The final fixture must not copy these tables by hand. It must recreate them from
summary pages, selected slices, derived metrics, and an audit log that records
every read.

Evidence collection must prove each class with row ids, timestamps, and bounded
content refs, not with prose. For `8ac124d6`, build the evidence window around
the active root DAG artifact id/hash, latest checkpoint group, active group,
and the regroup offset if a `dag-regroup:*` overlay is active. Then collect
groups from the first post-regroup group through the latest active group.

Required collection passes:

1. Artifact summary pass: call `list_record_summaries(feature_id="8ac124d6", prefixes=(...))` in pages of 500 for `dag-task:`, `dag-group:`, `dag-verify:`, `dag-commit-failure:`, `dag-worktree-alias-preflight:`, `dag-writeability-preflight:`, `dag-workspace-acl-normalization:`, `dag-workspace-permission-repair:`, `dag-task-reconcile:`, `dag-task-spec-reconcile:`, `dag-verify-rca:`, `dag-repair-rca:`, `dag-fix:`, `dag-repair-expanded-verify:`, `dag-repair-lens:`, `dag-direct-repair-route:`, `dag-authority-gate:`, `worktree-registry`, `worktree-registry:g`, `dag-regroup:`, and `dag-regroup-active:`.
2. Event summary pass: call `list_event_summaries(feature_id="8ac124d6", limit=500, group_idx=...)` for every candidate group plus one unfiltered latest page to catch feature-level events. Store event ids, event types, sources, preview content, metadata, and created timestamps.
3. Selected body pass: call `get_slice` only for artifact ids selected by the summary pass as proof rows. Default slice budget is 20,000 chars. A finding can request more only by recording why preview text was insufficient.
4. Derived metric pass: feed summaries and event summaries into the pure `collect_sizing_metrics` and `identify_process_improvements` functions rather than running write-producing operator commands.
5. Contradiction pass: compare latest `dag-task:*` file claims, task specs, alias preflight reports, verifier evidence, commit proofs, and `dag-group:*` checkpoint rows. Preserve disagreement as evidence instead of reconciling it away.

Concrete metrics to report per feature and per group:

| Metric | Formula | Evidence source |
| --- | --- | --- |
| `retry_cycles_per_task` | `dag_repair_cycle_start` count divided by completed task count | event summaries plus `dag-group:*` task ids |
| `commit_failures_per_task` | `dag_commit_failed` events or `dag-commit-failure:*` artifacts divided by completed task count | event summaries and commit-failure summaries |
| `stale_projection_repairs_per_task` | `dag-task-reconcile:*` plus `dag-task-spec-reconcile:*` count divided by task count | artifact summaries |
| `alias_events_per_group` | `dag-worktree-alias-preflight:*` rows with problems divided by inspected groups | alias preflight slices |
| `acl_normalizations_per_group` | ACL normalization/preflight artifacts with mutations or operator_required divided by inspected groups | ACL artifacts and `dag_writeability_preflight_failed` events |
| `claimed_file_churn_count` | count of file paths that appear, disappear, or move across `dag-task:*`, fix, RCA, and verifier artifacts for the same task/group | selected artifact slices |
| `broad_repair_misroute_count` | deterministic class findings followed by normal RCA/fix dispatch before deterministic repair evidence appears | direct-route, repair, RCA, and verify rows |
| `late_contract_failure_count` | verifier/RCA failures naming catalog, package mirror, generated output, or message-store contracts after task success evidence was written | verifier/RCA slices plus task results |
| `runtime_retry_or_stall_count` | `agent_stalled`, provider retry, runtime crash, and invocation retry events inside the inspected window | event summaries |
| `verify_cost_units_per_task` | `(dag_verify_finish count + dag_expanded_verify_finish count * 6) / task_count` | existing regroup metric convention |
| `checkpoint_duration_h` | time from previous `dag-group:*` checkpoint to current `dag-group:*` checkpoint | checkpoint artifact timestamps |
| `tasks_per_hour` | checkpointed task count divided by `checkpoint_duration_h` | group checkpoint rows |
| `workflow_drag_hours` | sum of deterministic-class group durations weighted by class-specific attribution used by `identify_process_improvements` | process-improvement output |
| `evidence_ref_coverage` | findings with at least one artifact id or event id divided by total findings | fixture report |
| `bounded_read_ratio` | summary/slice reads divided by all artifact/event reads in the collector | collector audit log |
| `checkpoint_safety_gap_count` | checkpoints lacking verifier approval, commit proof, and no-dirty proof evidence | `dag-verify:*`, `dag-commit-*`, `dag-group:*` rows |

Target baselines:

- Fixture completeness: `evidence_ref_coverage == 1.0`.
- Safety: `checkpoint_safety_gap_count == 0` for any target-architecture fixture.
- Read discipline: `bounded_read_ratio >= 0.95`; the remaining reads must be named single-row body inspections.
- Improvement gate for atomic landing: deterministic workflow drag classes
  (`worktree_alias`, `acl_workability`, `stale_projection`, `commit_hygiene`,
  `runtime_provider`, `verifier_context`, `checkpoint_contradiction`) must show
  at least 20% lower `retry_cycles_per_task` than the frozen `8ac124d6`
  evidence window, with no checkpoint safety regression.

## Why Piecemeal Patches Are Insufficient

Piecemeal guards are useful but they do not change the authority model. Today,
the same control flow handles planning evidence, workspace setup, agent dispatch,
task result persistence, verification, repair, commit, checkpoint, and regroup
resolution. A guard can prevent one symptom, but new symptoms still appear when
evidence, workspace state, and commit state disagree.

The target architecture moves those concerns into explicit durable records:

- Workspace authority decides what paths are canonical before an agent starts.
- Task contracts decide what outputs count.
- Sandboxes prevent accidental canonical mutation.
- Gates classify deterministic failures before model repair.
- Merge queue owns canonical integration and checkpoint projection.
- Typed failures give supervisor and scheduler a stable evidence source.

The evidence from `8ac124d6` should be read as authority disagreement, not as a
set of isolated product bugs:

| Bottleneck | Current authority conflict | Target owner | Target architecture mapping |
| --- | --- | --- | --- |
| Worktree alias drift | Task specs, task results, and verifier context can point at aliases while canonical verification reads the feature repo | Slice 02 workspace authority, Slice 03 contracts, Slice 06 gates | Registry-backed `RepoIdentity` and `WorkspaceSnapshot` normalize canonical paths before dispatch; task contracts store canonical repo ids; stale alias context becomes deterministic gate failure |
| ACL/writeability gaps | Bridge/operator checks can pass while the agent runtime user cannot write target files | Slice 02 workspace authority, Slice 07 failure router, Slice 10 supervisor/dashboard | Agent-user writable-path evidence is captured in snapshots; resolvable ACL failures route to workspace normalization; supervisor reports deterministic unblock instead of operator-required |
| Stale projection | `dag-task:*`, generated context, and material artifacts can survive after canonical state changes | Slice 01 typed journal, Slice 05 dispatcher, Slice 06 gates, Slice 08 merge queue | `dag-task:*` becomes attempt evidence only; typed projection links record exactly which row produced a legacy artifact; merge queue writes `dag-group:*` only after integration proof |
| Claimed-file oscillation | Retries can create/delete/move files while older task evidence still claims the old file set | Slice 03 contracts, Slice 04 sandbox runner, Slice 06 gates | `TaskDeliverableContract` plus `PatchSummary` records required/allowed/forbidden paths and changed paths per attempt; gate graph rejects missing claimed deliverables before broad RCA |
| Commit hygiene loops | Commit-only failures occur after verify success and can re-enter broad repair or verifier paths | Slice 07 failure router, Slice 08 durable merge queue, Slice 11 git service extraction | Queue owns commit/no-dirty proof; commit hook/status failures become typed `commit_hygiene` failures; retry budget is narrow and idempotent |
| Product contract drift | Generated catalogs, backend mirrors, package mirrors, and message-store contracts fail late because no execution-time contract owns them | Slice 03 contracts, Slice 06 gates, Slice 08 merge queue | Contract compiler records generated outputs and acceptance surfaces; deterministic gates run before model verifier and again before merge; queue cannot checkpoint without contract/gate evidence |
| Runtime/provider failure mixed with product repair | Runtime crashes and provider errors are logged inside the task loop that also decides verifier and checkpoint outcomes | Slice 05 dispatcher, Slice 07 failure router | Dispatcher records runtime failures as typed evidence with stable retry keys; product RCA is reserved for product failures after runtime/provider routes are exhausted |
| Supervisor/dashboard stale guidance | Heuristic artifact-body inference can recommend restart/operator action while deterministic executor work is active | Slice 10 supervisor/dashboard integration | Typed snapshots expose active attempt, failure, retry budget, queue, and evidence ids with bounded reads; Slack dedupe keys on failure signature and recommended action |
| Post-DAG boundary drift | A quiesced or incomplete effective DAG can be mistaken for a completed implementation phase, allowing post-test observation to start too early | Slice 06 gates, Slice 11 refactor map, Slice 12 landing matrix | Feature-level business gates stay explicit after group checkpoint completion; post-test readiness reads effective-DAG completion and post-DAG gate evidence, not root-DAG length alone |

## Proposed Interfaces/Types

No production interface is added in this slice. The implementation artifact is a
read-only fixture and report set. These types can be dataclasses, pydantic
models, or JSON fixtures under a future
`tests/fixtures/execution_control_plane/` package.

Core fixture types:

- `EvidenceRef`: source kind (`artifact`, `event`, `code`, `filesystem_snapshot`, `derived_metric`), feature id, group idx, task id, attempt label, artifact id/key or event id/type, created_at, summary preview, content hash when available, stored bytes/chars, selected slice range, and why the ref proves the finding.
- `FeatureExecutionEvidence`: feature id, root DAG artifact id/hash, active regroup artifact id/hash, latest checkpoint group, active group, inspected group range, completed task count, event high-water mark, artifact high-water mark, evidence refs, metric refs, and collection timestamp.
- `GroupExecutionEvidence`: group idx, task ids, task count, checkpoint artifact id, verify artifact ids, repair/RCA/fix artifact ids, commit artifact ids, workspace/ACL/alias artifact ids, event ids by type, derived metrics, and contradictions.
- `WorkflowDragFinding`: class, severity, affected groups, affected tasks, first_seen_at, last_seen_at, retry count, estimated drag hours, deterministic flag, current authority conflict, owning target slice, target invariant, evidence refs, and open questions.
- `ArtifactProjectionEvidence`: legacy key, latest artifact id, previous artifact ids inspected, source typed record id if present, value hash, summary bytes, and whether the row is authority, compatibility projection, or attempt evidence.
- `EventEvidence`: event id, event type, source, group/task metadata, preview, content bytes, timestamp, and correlation ids.
- `ContradictionEvidence`: left ref, right ref, conflict type (`path`, `status`, `time`, `commit`, `checkpoint`, `contract`), observed values, and required target owner.
- `CompatibilityConsumerIndex`: consumer name, code citation, legacy artifact key prefixes read, event types read, current body-read behavior, bounded-read alternative, migration owner, and atomic-landing blocker status.
- `CollectorAudit`: summary read calls, selected slice calls, forbidden broad read calls, pages read, max artifact/event id seen, database safety snapshot, and fixture hash.

Evidence roles:

- `attempt_evidence`: task/runtime output that proves what an agent claimed, not that the product integrated.
- `workspace_evidence`: repo identity, path canonicalization, ACL/writeability, dirty state, and alias resolution.
- `gate_evidence`: deterministic preflight, verifier, expanded lens, and raw approval rows.
- `failure_evidence`: typed or legacy failure rows that decide a route.
- `merge_evidence`: patch apply, commit, hook, no-dirty, and merge conflict proof.
- `checkpoint_evidence`: `dag-group:*` rows and their required upstream refs.
- `observer_evidence`: supervisor/dashboard summaries that may explain but must not own execution authority.

## Refactoring Steps

1. Add a read-only fixture builder that accepts `feature_id`, `from_group`, `to_group`, `artifact_after_id`, `event_after_id`, and read budgets. It must use summary APIs first and selected slices second.
2. Record root evidence before per-group evidence: latest `dag`, root DAG id/hash, active `dag-regroup:*` marker if any, latest `dag-group:*`, active group, and high-water ids for artifacts/events.
3. Page artifact summaries by prefix. Do not call `list_records` or broad SQL selecting `value` during traversal. Record a collector audit row for every summary/slice read.
4. Page event summaries by group. Use unfiltered event summaries only to discover feature-level events and high-water marks.
5. Build `GroupExecutionEvidence` rows and compute concrete metrics from the summary data. Reuse `collect_sizing_metrics` and `identify_process_improvements` as pure functions, not the write-producing operator command.
6. Select body slices only for proof artifacts referenced by findings. Store the selected range, body hash, and reason for selection.
7. Build `ContradictionEvidence` rows for mismatches between `dag-task:*`, task specs, alias/ACL reports, verifier context, commit evidence, and checkpoints.
8. Emit a bottleneck-to-owner table from the findings. Every finding must map to one owning slice and one target invariant.
9. Add `CompatibilityConsumerIndex` rows for resume, dashboard, supervisor, regroup, post-test observation, public dashboard, and Slack surfaces. Mark any consumer that still requires artifact bodies as an atomic-landing blocker.
10. Freeze the fixture by high-water ids and hashes so it does not depend on the live feature continuing to exist in a particular phase.
11. Capture current post-DAG business gate behavior as feature-level evidence:
    code review, security audit, test authoring, QA, integration testing, final
    verifier, source-repo push, implementation report, backlog report, and
    notification. The evidence fixture must distinguish group checkpoint
    completion from implementation-phase completion.

## Persistence And Artifact Compatibility

- Do not write new canonical `dag-*` artifacts in this slice.
- Fixture extraction must use bounded reads except for explicitly selected single artifacts.
- Preserve exact legacy key semantics in all examples because later slices rely on projection parity.
- Do not run commands that insert `review:*`, `dag-regroup:*`, event, or artifact rows against `8ac124d6` while producing this fixture. If an existing command writes analysis artifacts, call its pure collection functions instead.
- Store fixture outputs outside production artifact tables. Acceptable destinations are repo fixtures, test snapshots, or local scratch reports explicitly excluded from workflow state.
- Artifact examples must label whether a key is authority today, compatibility projection in the target, or non-authoritative attempt evidence.
- `dag-task:{task_id}` examples must preserve `ImplementationResult` JSON shape while stating that target architecture treats it as attempt evidence.
- `dag-group:{group_idx}` examples must preserve checkpoint JSON shape while stating that target architecture writes it only after merge/commit/no-dirty/gate proof.
- Compatibility examples must include id/hash pairs. A key name without row id and digest is insufficient evidence.

## Edge Cases And Failure Handling

- If `8ac124d6` continues progressing while fixtures are captured, record the latest root DAG id/hash and checkpoint range in fixture metadata.
- If an artifact body exceeds bounded-read limits, store a summary plus artifact id/hash, not the full body.
- If metrics disagree between events and artifacts, mark the fixture as contradiction evidence instead of normalizing it away.
- If a group appears active by events but checkpointed by artifacts, preserve both refs and classify as `checkpoint_contradiction` until target ownership is assigned.
- If a legacy key has multiple rows, use the latest row for current-state assertions and retain previous row ids only when proving drift or oscillation.
- If a selected artifact slice is insufficient to prove a finding, request a second bounded slice with a recorded reason; do not hydrate the whole feature history.
- If the regroup overlay changes between collection start and finish, freeze the earlier high-water ids and record the later marker as out-of-window evidence.
- If event metadata lacks `group_idx`, infer group only from artifact keys or selected content when deterministic; otherwise leave group unknown.
- If ACL/alias evidence was already repaired before fixture capture, keep the repair row and measure recurrence by counting prior unresolved rows, not by assuming the latest clean state erased the drag.
- If a command or fixture builder would mutate production rows, stop and produce a local-only report instead.
- If a code citation line changes during parallel work, update the citation to the current working-tree line before accepting the fixture.

## Tests

- Fixture builder uses `list_record_summaries` and `list_event_summaries` by default and records a failure if it calls `list_records`, `get_events`, or broad artifact-body SQL during traversal.
- Fixture builder can reconstruct completed, active, and pending group status from `dag-group:*`, event summaries, and active regroup metadata.
- Evidence refs include artifact ids or event ids for every drag finding; code citations alone cannot satisfy a workflow drag finding.
- Contradictory evidence is preserved as its own finding with left/right refs and a target owner.
- Metric tests cover every concrete metric listed above, including division-by-zero behavior for active groups with no completed tasks.
- Bottleneck mapping tests assert every observed class has exactly one primary owning slice and at least one target invariant.
- Compatibility index tests cover resume, dashboard, supervisor, regroup, post-test observation, public dashboard, and Slack consumers.
- Bounded-read tests assert artifact summaries include stored size/previews and selected slices are capped unless explicitly justified.
- Atomic-landing tests assert fixture docs do not describe production behavior moving through partial rollout phases.

## Acceptance Criteria

- Another engineer can explain why each later slice exists from this evidence doc alone.
- Every repeated `8ac124d6` drag class maps to a target owner slice.
- No test fixture depends on the live feature continuing to exist in a particular phase.
- The fixture set does not mutate active feature state.
- Every finding has artifact/event refs, not prose-only evidence.
- Metrics are reproducible from bounded artifact/event summaries plus selected slices.
- Compatibility consumers and legacy projection keys are explicit enough for later projection parity tests.
- The evidence baseline supports one atomic production landing, with partial behavior flags allowed only for local tests or non-production fixture comparison.

## Rollout/Rollback Notes

This slice is documentation and read-only fixture work. There is no production
rollout phase and no behavior flag to enable. Rollback before landing is
deleting the fixture/report additions. No live workflow behavior changes.

For the broader control-plane program, this evidence doc requires a single
atomic production landing for the new execution path after all required slices,
compatibility projections, fixtures, and regression gates pass together. Until
that landing, shadow writes or advisory comparisons may exist only in local
tests or isolated non-production evidence runs. Production must not advance
through partial phases where one behavior slice is authoritative while later
slices are absent.

Rollback after the atomic landing must preserve resume safety: stop admission of
new control-plane features, finish or quiesce in-flight control-plane attempts at
typed boundaries, and only then point new starts back to the legacy executor.
Never delete checkpoints, root DAG artifacts, regroup artifacts, typed audit
rows, or fixture evidence as a rollback mechanism.

## Cross-Slice Dependencies

- Slice 1 uses this evidence to define projection compatibility.
- Slice 2 owns alias and ACL evidence classes.
- Slice 3 owns task deliverable and generated-output contract evidence.
- Slice 4 owns sandbox patch evidence for claimed-file oscillation.
- Slice 5 owns runtime/provider evidence boundaries.
- Slice 6 owns deterministic gate, verifier context, and raw approval evidence.
- Slice 7 owns failure taxonomy and retry-budget evidence classes.
- Slice 8 owns merge, commit, no-dirty, and checkpoint evidence.
- Slice 9 consumes metrics for regroup overlays and scheduler feedback, but speed cannot override write-set or dependency safety.
- Slice 10 consumes typed summaries and must stay read-only/advisory for executor state.
- Slice 11 uses the compatibility index to order refactors without breaking monkeypatch targets or legacy key reads.
- Slice 12 uses this evidence to set atomic-landing success metrics, not phased production rollout gates.
