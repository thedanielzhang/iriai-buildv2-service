# 09. Regroup Overlay And Scheduler Feedback

## Objective

Generalize derived DAG regrouping as a persisted overlay and feed typed execution
metrics back into future wave sizing. Regrouping remains review/activation based;
scheduler speed never overrides dependencies, write sets, barriers, or safety.
This slice is part of the single atomic execution-control-plane feature landing;
it is not a phased production rollout or a compatibility-only first release.

## Current Code Citations

- Derived DAG model: [DerivedDAGArtifact](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:994).
- Current G45-G73 regroup artifact keys and builder: [dag_regroup.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:25) and [build_staged_regroup](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:336).
- Effective order projection: [_effective_execution_order](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:604).
- Sizing metrics collector: [collect_sizing_metrics](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:836).
- Derived DAG validation in implementation phase: [_validate_derived_dag_artifact_update](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9902).
- Active regroup marker resolver: [_resolve_active_regroup_before_group_dispatch](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4397).
- Regroup process improvement extractor: [identify_process_improvements](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1011).
- Adaptive sizing recommender: [recommend_adaptive_sizing](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1256).
- Regroup activation and rollback commands: [command_activate](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:2009) and [command_rollback](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:2220).
- DB safety snapshot used by review tooling: [_safety_snapshot](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/dag_regroup.py:1825).
- Regroup and adaptive sizing tests: [test_dag_regroup.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_regroup.py:315), [test_dag_regroup.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_regroup.py:621), [test_dag_regroup.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_regroup.py:844), [test_dag_regroup.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_regroup.py:1463), and [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:4156).

## Current Failure Mode From `8ac124d6`

The G45+ regroup reduced checkpoint size and made progress more observable, but
sizing was initially policy-driven rather than fully data-driven. Later analysis
showed that overly small waves can reduce normalized throughput even while
improving checkpoint latency.

The current implementation also treats regroup as a special G45-G73 case:
artifact keys are fixed, active marker resolution lives inside the implementation
phase, activation safety is split between command code and dispatch code, and
adaptive sizing produces review JSON without a typed evidence contract. That is
good enough for a one-off recovery, but not enough for a reusable control-plane
component. The target shape is a typed overlay that can be validated, activated,
resolved, observed, and rolled back through one store API while projecting the
legacy `dag-regroup:*` artifacts synchronously for existing readers.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/regroup_overlay.py`.
This module owns overlay schemas, validation, activation, rollback, resolver
projection, scheduler feedback, and compatibility artifact projection.

Overlay identifiers are deterministic:

- `overlay_id = sha256(feature_id, source_dag_key, base_dag_artifact_id,
  base_dag_sha256, group_idx_offset, canonical derived order)[:24]`.
- `overlay_slug = g{group_idx_offset}-g{last_original_group}` when the original
  suffix has a bounded end, otherwise `g{group_idx_offset}-tail`.
- Legacy projection keys are `dag-regroup:{overlay_slug}`,
  `dag-regroup-active:{overlay_slug}`, `dag-regroup-rollback:{overlay_slug}`,
  and `dag-regroup-observation:{overlay_slug}`.
  The existing `g45-g73` spelling remains a compatibility projection for that
  exact suffix.

### Overlay Schema

```python
class OverlayCompatibilityKeys(BaseModel):
    canonical_artifact_key: str
    active_marker_key: str
    rollback_artifact_key: str
    observation_artifact_key: str
    sizing_review_key_prefix: str
    projection_idempotency_keys: dict[str, str] = Field(default_factory=dict)
    legacy_alias_keys: list[str] = Field(default_factory=list)


class OverlayBarrier(BaseModel):
    barrier_id: str
    task_ids: list[str]
    hard: bool = True
    source: Literal["task_contract", "speed_index", "operator", "legacy"]


class OverlayTaskSpeedMetadata(BaseModel):
    semantic_lane: str = "unknown"
    barrier: str = "unknown"
    critical_path_depth: int = 0
    commit_risk: int = 0
    verification_cost: int = 0
    unknown_write: bool = False
    scheduler_feedback_ids: list[int] = Field(default_factory=list)


class RegroupActivationContract(BaseModel):
    required_checkpoint_key: str
    forbidden_checkpoint_key: str
    forbidden_first_wave_task_keys: list[str]
    forbidden_group_artifact_prefixes: list[str]
    forbidden_group_event_idx: int
    required_base_dag_artifact_id: int
    required_base_dag_sha256: str
    required_overlay_sha256: str
    requires_feature_advisory_lock: bool = True


class RegroupRollbackPlan(BaseModel):
    restore_source_dag_key: str
    restore_from_checkpoint_group: int
    rollback_marker_key: str
    allowed_until_group_idx: int
    forbidden_started_keys: list[str]
    forbidden_started_event_group_idx: int
    forbidden_typed_attempt_group_idx: int
    forbidden_merge_queue_group_idx: int
    forward_only_after_start: bool = True


class RegroupActiveMarker(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["active", "rolled_back"]
    feature_id: str
    overlay_id: str
    overlay_slug: str
    overlay_row_id: int
    canonical_artifact_key: str
    canonical_artifact_id: int
    canonical_sha256: str
    active_marker_key: str
    rollback_artifact_key: str
    rollback_artifact_id: int
    source_dag_key: str
    base_dag_artifact_id: int
    base_dag_sha256: str
    checkpointed_group: int
    group_idx_offset: int
    validation_digest: str
    activated_at: datetime | None = None
    rolled_back_at: datetime | None = None
    reason: str = ""


class RegroupOverlay(BaseModel):
    schema_version: Literal[1] = 1
    overlay_id: str
    overlay_slug: str
    feature_id: str
    status: Literal["staged", "active", "rolled_back", "superseded", "rejected"]
    artifact_key: str
    source_dag_key: str
    base_dag_artifact_id: int
    base_dag_sha256: str
    checkpointed_group: int
    group_idx_offset: int
    last_original_group: int | None = None
    original_execution_order: list[list[str]]
    derived_execution_order: list[list[str]]
    original_to_new_group_mapping: dict[int, list[int]]
    task_definition_fingerprints: dict[str, str]
    remaining_dependency_edges: dict[str, list[str]]
    barriers: list[OverlayBarrier]
    write_sets: dict[str, list[str]]
    speed_index: dict[str, OverlayTaskSpeedMetadata]
    activation_contract: RegroupActivationContract
    rollback_plan: RegroupRollbackPlan
    compatibility_keys: OverlayCompatibilityKeys
    validation_evidence_ids: list[int] = Field(default_factory=list)
    scheduler_feedback_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    activated_at: datetime | None = None
    rolled_back_at: datetime | None = None
    reason: str = ""
    overlay_sha256: str
    validation_digest: str
```

`RegroupOverlay` is the canonical typed record. A `DerivedDAGArtifact` remains
the compatibility payload for artifact consumers and is generated from the typed
overlay plus the unchanged base task definitions. The generated payload must
round-trip through the existing `DerivedDAGArtifact` model during projection.

### Regroup Projection Model

The projection path is one-way from typed overlay rows to legacy artifacts.
Legacy artifacts are compatibility views, not a second source of authority.

Projection methods live in the journal/store layer and are invoked only inside
the transaction that writes or updates `execution_regroup_overlays`:

- `project_regroup_overlay(overlay)` writes `dag-regroup:{overlay_slug}` as a
  `DerivedDAGArtifact` whose `dag.tasks` are copied byte-for-byte from the base
  suffix tasks; only `dag.execution_order` changes the scheduling placement.
  It includes `base_dag_artifact_id`, `base_dag_sha256`, `checkpointed_group`,
  `group_idx_offset`, `original_execution_order`,
  `original_to_new_group_mapping`, `barriers`, `write_sets`, `speed_index`,
  activation contract, rollback plan, validation notes, `overlay_id`, and
  `overlay_sha256`. Fields not present on `DerivedDAGArtifact` are carried under
  bounded compatibility metadata inside `speed_index["overlay"]` and
  `validation_notes`, so the payload still round-trips through the existing
  Pydantic model.
- `project_regroup_active(overlay, marker)` writes
  `dag-regroup-active:{overlay_slug}` as a `RegroupActiveMarker`. The marker
  must reference the same typed row id, overlay id, overlay sha, validation
  digest, canonical artifact id/key/sha, rollback artifact id/key, base DAG
  id/hash, and group offset that were committed in the typed row.
- `project_regroup_rollback(overlay)` writes
  `dag-regroup-rollback:{overlay_slug}` as a bounded rollback eligibility view.
  It is informational until rollback executes; rollback authority remains the
  typed row plus active marker validation under lock.
- `project_regroup_observation(resolve_result)` writes
  `dag-regroup-observation:{overlay_slug}` after resolver application. It
  records the selected overlay id, resume group, effective group count, and
  validation evidence ids for dashboards and restart diagnostics.
- `project_sizing_review(feedback)` writes
  `review:dag-sizing:{feature_id}:{window}` from `SchedulerFeedback`. It is
  review evidence only and is never read as an active marker or executable plan.

Projection idempotency keys include feature id, projection kind, projection key,
typed source row id, and body sha. A second write with the same idempotency key
must return the existing projection link; the same projection key with a
different body sha is an evidence-corruption failure that quiesces dispatch.
The exact `g45-g73` compatibility aliases are produced only when
`group_idx_offset == 45` and `last_original_group == 73`.

### Scheduler Feedback Schema

```python
class SchedulerGroupMetric(BaseModel):
    metric_id: str
    feature_id: str
    group_idx: int
    overlay_id: str | None = None
    state: Literal["pending", "active", "completed", "failed", "rolled_back"]
    completed: bool
    active: bool = False
    task_ids: list[str]
    task_count: int
    checkpoint_projection_id: int | None = None
    merge_queue_item_id: int | None = None
    task_attempt_ids: list[int] = Field(default_factory=list)
    failure_ids: list[int] = Field(default_factory=list)
    gate_evidence_ids: list[int] = Field(default_factory=list)
    compatibility_projection_ids: list[int] = Field(default_factory=list)
    started_at: datetime | None = None
    checkpointed_at: datetime | None = None
    checkpoint_duration_h: float | None = None
    implementation_duration_h: float | None = None
    verification_duration_h: float | None = None
    repair_duration_h: float | None = None
    merge_queue_wait_h: float | None = None
    merge_apply_duration_h: float | None = None
    commit_duration_h: float | None = None
    lane_counts: dict[str, int]
    barrier_counts: dict[str, int]
    repo_count: int
    write_set_count: int
    unknown_write_count: int
    max_dependency_depth: int
    max_commit_risk: int
    max_verification_cost: int
    verify_count: int
    expanded_verify_count: int
    product_repair_cycles: int
    workflow_repair_cycles: int
    commit_failures: int
    merge_conflicts: int
    queue_retries: int
    runtime_failures: int
    workspace_failures: int
    stale_projection_repairs: int
    verify_cost_units: int
    tasks_per_hour: float | None
    hours_per_task: float | None
    product_repair_cycles_per_task: float | None
    workflow_repair_cycles_per_task: float | None
    commit_failures_per_task: float | None
    merge_conflicts_per_task: float | None
    verify_cost_per_task: float | None
    tail_risks: list[str]
    data_quality_flags: list[str] = Field(default_factory=list)
    evidence_ids: list[int]

class SchedulerFeedback(BaseModel):
    schema_version: Literal[1] = 1
    feedback_id: str
    feature_id: str
    generated_at: datetime
    window_start_group: int
    window_end_group: int
    overlay_id: str | None = None
    lane: str
    barrier: str
    completed_groups: list[int]
    sample_count: int
    tasks_per_hour: float | None
    hours_per_task_p50: float | None
    hours_per_task_p75: float | None
    product_repair_cycles_per_task: float | None
    workflow_repair_cycles_per_task: float | None
    commit_failures_per_task: float | None
    merge_conflicts_per_task: float | None
    verify_cost_per_task: float | None
    queue_wait_p75_h: float | None
    data_quality: Literal["sufficient", "insufficient", "mixed", "stale"]
    recommended_cap: int
    current_cap: int
    confidence: Literal["low", "medium", "high"]
    reasons: list[str]
    metric_ids: list[str]
    evidence_ids: list[int]
```

Metrics are grouped by both lane and hard barrier. Barrier stats win over lane
stats when they have at least two completed samples; otherwise the recommender
falls back to lane stats and then to conservative policy caps.

`metric_id` is deterministic:
`sha256(feature_id, group_idx, overlay_id or "root", checkpoint_projection_id,
task_ids, evidence_ids)[:24]`. A metric is usable for sizing only when it has
typed evidence links for task attempts, gate/verification, merge or checkpoint,
and compatibility projection lineage while legacy readers remain. Missing links
do not block status reporting, but they set `data_quality_flags` and force
`SchedulerFeedback.data_quality` away from `sufficient`.

### Scheduler Metrics And Cap Rules

Only completed groups contribute to throughput and sizing baselines. A group is
completed only after checkpoint projection exists and is linked to merge, commit,
no-dirty, and gate evidence.

| Metric | Formula / source | Notes |
| --- | --- | --- |
| `checkpoint_duration_h` | `checkpointed_at - max(previous_checkpointed_at, first_group_attempt_at)` | Null until checkpointed. |
| `tasks_per_hour` | `task_count / checkpoint_duration_h` | Completed groups only. |
| `hours_per_task` | `checkpoint_duration_h / task_count` | Used for p50/p75 cap sizing. |
| `product_repair_cycles_per_task` | typed product repair attempts / task count | Product defects only. |
| `workflow_repair_cycles_per_task` | typed workflow/control-plane repair attempts / task count | Alias, ACL, stale projection, runtime, queue, and commit hygiene classes. |
| `commit_failures_per_task` | typed commit failure records / task count | Includes commit hook and no-dirty failures before queue success. |
| `merge_conflicts_per_task` | merge queue conflict records / task count | Kept separate from product repair. |
| `verify_cost_per_task` | weighted gate cost units / task count | Expanded verify can carry higher weight than raw/local gates. |
| `queue_wait_p75_h` | p75 merge queue wait for lane/barrier window | Used for observability, not for bypassing safety. |

Cap computation is conservative:

1. Determine `policy_cap` from task risk: unknown writes or high-risk barriers
   cap at 4; backend or multi-repo work caps at 6; isolated UI/document work
   caps at 10; test-only and perf lanes cap at 14.
2. Require at least two completed samples with evidence ids for the lane/barrier
   before widening above the current cap.
3. Reduce to current cap or 4 when workflow repair, product repair, commit
   failure, or merge conflict rates exceed the global completed baseline by more
   than 10 percent.
4. Bound the cap by p75 hours/task:
   `floor(12h_checkpoint_budget / hours_per_task_p75)`, clamped to
   `[4, policy_cap]`.
5. Apply dependency, hard-barrier, write-set, and mapping validators after cap
   selection. These validators may shrink or reject a candidate wave; metrics
   may not override them.

### Validation Algorithm

`validate_overlay(candidate, base_context, activation_check=False)` returns
`OverlayValidationResult(valid, reason, details, evidence_ids, normalized)`.
The algorithm is deterministic and runs under the feature advisory lock for
activation and rollback.

1. Parse the typed overlay or compatibility `DerivedDAGArtifact`, normalize
   group ids to absolute indexes, normalize and sort canonical path write sets,
   and compute the canonical overlay sha from the typed normalized form. Reject
   malformed JSON, wrong schema version, mismatched artifact key/status, or a
   compatibility artifact whose `speed_index["overlay"]` identity disagrees with
   the typed projection link.
2. Load `source_dag_key` through the store. Reject unless the loaded artifact id
   and SHA-256 exactly match `base_dag_artifact_id` and `base_dag_sha256`.
3. Verify `checkpointed_group + 1 == group_idx_offset`, the checkpointed group
   exists, and `original_execution_order` equals
   `base_dag.execution_order[group_idx_offset:]`.
4. Build multisets for base suffix task ids, derived task definitions, and
   derived execution-order ids. Reject missing, extra, duplicate, or unknown
   task ids.
5. Compare task definition fingerprints against the base task model. Do not
   allow prompt, files, team, requirement coverage, dependency list, or task id
   mutation. The only allowed change is group placement represented by
   `derived_execution_order`.
6. Compare `remaining_dependency_edges` to the base DAG suffix exactly. Edges
   between remaining tasks must be preserved with no additions or drops.
   Dependencies to already checkpointed tasks are treated as satisfied evidence
   and must not reappear as executable tasks.
7. Build `derived_group_by_task`. Reject unknown dependencies, dependencies in
   the same derived wave, and dependencies scheduled after their dependents.
8. Validate `original_to_new_group_mapping`: every original suffix group is
   present, every mapped new group is in `[group_idx_offset,
   group_idx_offset + len(derived_execution_order) - 1]`, and every task in each
   derived group belongs to one of the mapped original groups. A task may not be
   assigned to a new group unless its original group maps to that new group.
9. Compile hard barriers from Slice 3 contracts first, then overlay barriers,
   then legacy speed metadata. Reject a derived group that mixes hard barriers.
   Soft barrier merges must be explicit in the overlay and included in
   validation notes.
10. Compile authoritative write sets from task contracts, task file scopes,
   declared task files, and overlay additions. Overlay additions may add paths
   but may not remove, rename, narrow, or mask authoritative paths. If a derived
   group merges tasks from multiple original groups, every task in that group
   must have write-set coverage. Reject same-wave write-set overlap after path
   canonicalization and reject widened waves containing any `unknown_write`
   task.
11. Validate activation and rollback contracts against the normalized first
    derived wave: required checkpoint key, forbidden checkpoint key, forbidden
    `dag-task:*` keys, forbidden group artifact prefixes, forbidden group event
    metadata, forbidden typed attempts, forbidden merge queue items, required
    base dag id/hash, and required overlay sha.
12. During activation or resolver checks, validate `RegroupActiveMarker` against
    the typed overlay row, projection link, canonical artifact body sha, latest
    successful validation digest, base DAG id/hash, and rollback projection. Any
    missing, stale, inactive, or mismatched marker fails closed and quiesces
    dispatch before the affected group.
13. Emit a typed validation record and compatibility validation artifact in the
    same transaction. Re-validating the same overlay id with the same digest is
    idempotent; a different digest for the same overlay id is rejected.

Validation never widens a wave because of scheduler metrics. Metrics can only
produce a recommendation artifact. The recommendation must be converted into a
new overlay and pass the same validation path before activation.

## Refactoring Steps

1. Extract `regroup_overlay.py` with typed overlay models, feedback models,
   validation helpers, projection helpers, activation, rollback, and resolver
   APIs. Keep `dag_regroup.py` as a CLI/review facade that delegates to the new
   module.
2. Replace hard-coded G45-G73 constants in dispatch resolution with
   `RegroupOverlayResolver.resolve(feature_id, group_idx)`. The resolver reads
   typed overlay state first, validates the active marker/projection pair, and
   then reads legacy projection aliases only to support existing G45-G73
   artifacts during the same atomic landing.
3. Move `_validate_derived_dag_artifact_update` regroup-specific checks into the
   overlay validator and call it from artifact repair/update validation, CLI
   activation, and resolver safety checks.
4. Add typed overlay rows, typed validation rows, typed scheduler feedback rows,
   and legacy artifact projections in the same store transaction. There is no
   production period where only typed rows or only compatibility artifacts are
   authoritative.
5. Extend sizing analysis to read typed attempts, typed failures, gate
   durations, merge queue outcomes, and projection-linked legacy ids. The
   current artifact/event collector remains only as a compatibility projection
   source until all readers migrate; typed metrics are the recommender input.
6. Make adaptive sizing output a typed `SchedulerFeedback` plus
   `review:dag-sizing:{feature_id}:{window}` projection. It can recommend caps
   and candidate waves, but it cannot write an active marker.
7. Wire activation to an explicit operator/control-plane action that converts an
   approved recommendation or staged derived DAG into `RegroupOverlay`, validates
   it under lock, writes canonical/projection rows atomically, and emits a typed
   activation event.
8. Wire rollback to the overlay store. Rollback writes a `rolled_back` active
   marker and typed rollback event before the first derived wave starts; after
   that boundary it rejects and requires a forward-only overlay from the latest
   checkpoint.
9. Add a resolver observation path that records which overlay was applied,
   which evidence ids justified it, and why dispatch quiesced when validation
   failed. Observation writes are diagnostic projections and cannot make an
   invalid overlay executable.
10. Run targeted regroup, scheduler, resolver, rollback, projection parity, and
   full execution-control-plane regression tests before the single production
   landing.

## Persistence And Artifact Compatibility

- Root `dag` is never overwritten.
- `execution_regroup_overlays` is canonical for new control-plane decisions.
  Columns: `id BIGSERIAL PRIMARY KEY`, `feature_id TEXT NOT NULL`,
  `overlay_id TEXT NOT NULL`, `overlay_slug TEXT NOT NULL`,
  `status TEXT NOT NULL`, `artifact_key TEXT NOT NULL`,
  `source_dag_key TEXT NOT NULL`, `base_dag_artifact_id BIGINT NOT NULL`,
  `base_dag_sha256 TEXT NOT NULL`, `checkpointed_group INTEGER NOT NULL`,
  `group_idx_offset INTEGER NOT NULL`, `last_original_group INTEGER`,
  `overlay_sha256 TEXT NOT NULL`, `validation_digest TEXT NOT NULL`,
  `latest_successful_validation_id BIGINT`, `active_marker_projection_id BIGINT`,
  `payload_json JSONB NOT NULL`, `compatibility_artifact_ids JSONB NOT NULL
  DEFAULT '[]'`, `activated_at TIMESTAMPTZ`, `rolled_back_at TIMESTAMPTZ`,
  `idempotency_key TEXT NOT NULL UNIQUE`, and `created_at`/`updated_at`.
- Required overlay constraints/indexes:
  `CHECK (status IN ('staged', 'active', 'rolled_back', 'superseded', 'rejected'))`,
  `UNIQUE (feature_id, overlay_id)`, unique active overlay per feature with
  `CREATE UNIQUE INDEX ... ON execution_regroup_overlays(feature_id) WHERE
  status = 'active'`, `idx_regroup_overlay_base` on
  `(feature_id, source_dag_key, base_dag_artifact_id, base_dag_sha256)`, and
  `idx_regroup_overlay_status` on `(feature_id, status, updated_at DESC)`.
- Activation and rollback run under the feature advisory lock. Activation updates
  exactly one staged row to `active`, writes compatibility projections in the
  same transaction, and fails if another active overlay exists unless the request
  is a forward-only suffix overlay. A forward-only suffix overlay must prove the
  prior active overlay has started, set the prior row `active -> superseded`,
  set `payload_json.supersedes_overlay_id`, and use
  `group_idx_offset > prior.payload_json.highest_started_group_idx`. Rollback
  updates `active -> rolled_back` only before the rollback boundary proves no
  protected group/task/event/artifact has started; after that, superseding
  forward-only overlays are the only regroup mutation path.
- `execution_regroup_validations` stores validation attempts with columns:
  `id`, `feature_id`, `overlay_id`, `overlay_row_id`, `valid`, `reason`,
  `validation_digest`, bounded `details_json`, `evidence_ids`,
  `idempotency_key`, and `created_at`. Indexes: unique `idempotency_key`,
  `(feature_id, overlay_id, created_at DESC)`, and `(feature_id, valid,
  created_at DESC)`.
- `execution_scheduler_feedback` stores lane/barrier windows and recommendation
  payloads with `id`, `feedback_id`, `feature_id`, `window_start_group`,
  `window_end_group`, `lane`, `barrier`, `sample_count`, `recommended_cap`,
  `current_cap`, `data_quality`, `confidence`, `metric_ids`, `evidence_ids`,
  `payload_json`, `idempotency_key`, and `created_at`. Indexes: unique
  `idempotency_key`, `(feature_id, window_start_group, window_end_group,
  created_at DESC)`, and `(feature_id, lane, barrier, created_at DESC)`.
- Existing regroup artifacts remain synchronous compatibility projections:
  `dag-regroup:{overlay_slug}`, `dag-regroup-active:{overlay_slug}`,
  `dag-regroup-rollback:{overlay_slug}`, `dag-regroup-observation:{overlay_slug}`,
  and `review:dag-sizing:{feature_id}:{window}`.
- Projection writes are part of the same typed transaction as canonical rows.
  If a projection write fails, the typed write rolls back.
- Projection payloads include typed row ids and overlay sha so legacy readers can
  report precise evidence without becoming writers.
- Activation still requires the boundary checkpoint to exist and the next
  checkpoint, first-wave task artifacts, group-scoped verification artifacts,
  group-scoped failure artifacts, and non-regroup events for that group to be
  absent.
- Active marker resolution is fail-closed. The resolver must load the active
  typed row, the active-marker projection, the canonical regroup projection, and
  the source DAG record by id. It applies the overlay only when all ids, hashes,
  status values, validation digest, group offset, and projection links match.
  A stale or orphaned compatibility marker may be used only to produce a
  diagnostic `regroup_invalid` failure and quiesce reason.
- Scheduler feedback is advisory evidence. It is allowed to affect future
  recommendation artifacts only; the overlay validator remains the sole gate for
  executable regroup changes.

### Adaptive Sizing Data Flow

1. Collect typed attempts from Slice 1, task contracts from Slice 3, gate results
   from Slice 6, failure classes from Slice 7, merge queue timings from Slice 8,
   and legacy projection ids while compatibility readers still exist. Artifact
   timestamps are not used as authoritative durations when typed attempt,
   queue, or checkpoint evidence exists.
2. Build `SchedulerGroupMetric` for each group from the first post-regroup group
   through the high-water checkpoint. Active and incomplete groups are included
   for status but excluded from completed-throughput averages.
3. Attach evidence ids by category: task attempts, verify attempts, repair
   attempts, failure records, merge queue items, commit/no-dirty proofs,
   checkpoints, and compatibility artifacts. Feedback without all required
   evidence categories is `data_quality="insufficient"` or `mixed`; stale
   projection lineage sets `data_quality="stale"`.
4. Aggregate completed groups by `barrier:{barrier}` when at least two completed
   samples exist; otherwise aggregate by `lane:{lane}`. Keep global post-change
   baseline metrics for comparison.
5. Compute policy cap: unknown writes or high-risk barriers cap at 4,
   backend/multi-repo work caps at 6, isolated UI/document work caps at 10, and
   test-only or perf lanes cap at 14.
6. Compute evidence cap from p75 hours/task:
   `evidence_cap = floor(12h_checkpoint_budget / hours_per_task_p75)`, bounded
   to `[4, policy_cap]`. If sample count is below two, data is stale, or repair
   or commit rates exceed baseline by more than 10 percent, keep the current cap
   or reduce to 4. Never widen from a window whose completed groups omit typed
   merge/checkpoint proof or whose active group is still running.
7. Produce recommended waves by topological order. The scheduler may choose
   eligible tasks up to the cap only when hard barriers match, write sets do not
   overlap, merged original groups have full write-set coverage, and dependencies
   are already scheduled. Unknown writes, missing contracts, or hard-barrier
   ambiguity shrink the candidate wave before validation instead of being
   deferred to runtime.
8. Persist `SchedulerFeedback` and project
   `review:dag-sizing:{feature_id}:{window}`. No active overlay marker is
   written by this flow.

### Activation And Rollback Constraints

Activation and rollback both run under the feature advisory lock and in a single
store transaction.

Activation requires:

- Overlay status is `staged`, validation digest matches the latest successful
  validation record, and the canonical overlay sha matches the active marker
  payload to be written.
- `RegroupActiveMarker` can be built from the same transaction inputs and
  contains the exact overlay row id, overlay id, canonical artifact id/key/sha,
  rollback artifact id/key, base DAG id/hash, validation digest, checkpointed
  group, and group offset.
- Current `source_dag_key` id and sha match the overlay base id/hash.
- `dag-group:{checkpointed_group}` exists and is the latest checkpoint at or
  before `group_idx_offset`.
- `dag-group:{group_idx_offset}` does not exist.
- No `dag-task:*` artifact exists for any task in the first derived wave.
- No group-scoped verify, failure, preflight, merge queue, or repair artifact
  exists for `group_idx_offset`.
- No non-regroup event exists with `metadata.group_idx == group_idx_offset`.
- No typed attempt, typed failure, merge queue item, workspace snapshot, or gate
  evidence row exists for `group_idx_offset` except the regroup validation and
  activation evidence being written in the same transaction.
- No different overlay is already active for the suffix.

Activation writes the typed overlay status transition, canonical compatibility
artifact, rollback artifact, active marker, and typed activation event together.
If any write fails, none of the writes become authoritative.

Rollback requires:

- Overlay status is `active`, the active marker references the same overlay row
  id, overlay id, validation digest, canonical artifact id/key/sha, base DAG
  id/hash, group offset, and rollback artifact id/key, and the rollback request
  includes a reason.
- The same "not started" checks used by activation still pass for the first
  derived wave and `group_idx_offset`.
- No merge queue item, typed attempt, typed failure, group-scoped gate evidence,
  workspace snapshot, checkpoint projection, or non-regroup event exists for the
  first derived group.

Rollback writes a `rolled_back` status, a new active marker with
`status="rolled_back"`, and a typed rollback event. It does not delete the
canonical overlay, rollback artifact, scheduler feedback, validation records,
events, checkpoints, or root DAG. If rollback constraints fail, the only safe
path is a forward-only overlay from the latest checkpoint.

Forward-only overlays must start strictly after the latest completed checkpoint
and after any started group from the prior overlay. They may supersede the prior
active row only after proving dependency/write-set preservation for the
remaining suffix and preserving all typed evidence/projection history from the
superseded overlay. They must not attempt to reconstruct root DAG order for
already-started regrouped work.

## Edge Cases And Failure Handling

- Stale base DAG id/hash: reject overlay.
- Missing, duplicate, extra, or mutated tasks: reject overlay.
- Dropped dependencies: reject overlay.
- Added remaining-suffix dependencies: reject overlay.
- Same-wave dependencies: reject overlay.
- Dependency added to a task that did not have it in the remaining base suffix:
  reject overlay.
- Dependency/write-set preservation cannot be repaired by scheduler feedback;
  feedback must produce a new candidate overlay and re-run validation.
- Mapping omits an original group or maps tasks outside declared source groups:
  reject overlay.
- Hard barrier mix in a derived wave: reject overlay.
- Merged original groups without complete write-set coverage: reject overlay.
- Write-set conflict: reject overlay.
- Unknown write set on a task proposed for a widened wave: reject the wave or cap
  the recommendation at 4.
- Existing active overlay for the same suffix: idempotently accept only if the
  overlay id and sha match; otherwise reject and require explicit rollback or a
  forward-only overlay from a later checkpoint.
- Active marker exists without canonical overlay/projection sha match: fail
  closed, quiesce dispatch for the affected group, and require workflow
  diagnosis.
- Active typed row exists without active marker projection, or active marker
  exists without a matching active typed row: fail closed and run projection
  recovery from typed source if the typed row hash is intact.
- Same projection key with a different body sha: classify as
  `evidence_corruption/projection_body_conflict`, quiesce, and do not append a
  second active marker.
- Boundary checkpoint missing: reject activation.
- G45 or equivalent next group already started: reject activation and require a
  forward-only plan from the latest checkpoint.
- Rollback requested after any first-wave task artifact, group checkpoint,
  group-scoped verification/failure artifact, typed attempt, typed failure,
  gate evidence, workspace snapshot, merge queue item, or non-regroup event
  exists: reject rollback and require forward-only plan.
- Scheduler evidence insufficient, stale, or missing evidence ids: keep current
  lane cap, emit `data_quality="insufficient"`, and record missing categories.
- Metrics show higher repair or commit failure rate than baseline by more than
  10 percent: do not widen that lane/barrier.
- Metrics include active/incomplete groups in status only; if they enter
  throughput or p75 sizing, reject the feedback as invalid.
- Typed rows written but projection transaction fails: roll back the transaction
  and leave no partially authoritative overlay.

## Tests

Add focused tests in `tests/workflows/test_regroup_overlay.py` and extend the
existing regroup tests where compatibility behavior is already covered.

Schema and projection:

- Typed `RegroupOverlay` round-trips to a `DerivedDAGArtifact` compatibility
  projection and back without changing task ids, derived order, mapping,
  activation contract, rollback plan, write sets, or overlay sha.
- `overlay_slug` generation preserves the existing `g45-g73` projection key for
  that suffix and produces deterministic keys for non-G45 suffixes.
- Projection payload carries overlay identity only through fields accepted by
  `DerivedDAGArtifact` and fails if unsupported extra fields are required for
  round-trip validation.
- A duplicate projection idempotency key returns the existing link, while the
  same projection key with a different body sha raises
  `projection_body_conflict`.
- Projection transaction rollback leaves no typed overlay row when a compatibility
  artifact write fails.

Validation:

- Valid overlay passes with base context and emits validation evidence ids.
- Stale base artifact id rejects with `dag_regroup_base_dag_artifact_mismatch`.
- Stale base hash rejects with `dag_regroup_base_dag_hash_mismatch`.
- Missing, extra, duplicate, or execution-order-only task ids reject.
- Mutated task definitions reject while pure scheduling placement changes pass.
- Dropped remaining-suffix dependency rejects.
- Added remaining-suffix dependency rejects.
- Same-wave dependency rejects.
- Dependency scheduled after dependent rejects.
- Original-to-new mapping missing group, extra group, empty target, out-of-range
  target, or task/source mismatch rejects.
- Hard barrier mix rejects.
- Soft barrier mix passes only when explicitly declared as soft.
- Same-wave write-set overlap rejects.
- Merged original groups with missing write-set coverage reject.
- Overlay cannot remove an authoritative write path from a task contract.
- Unknown-write tasks cannot be merged across original groups or widened above
  the conservative cap.
- Compatibility artifact validation rejects a `speed_index["overlay"]` identity
  that disagrees with the typed projection link.

Activation and resolver:

- Activation succeeds only when `dag-group:{checkpointed_group}` exists and all
  forbidden next-group artifacts/events are absent.
- Activation rejects if any typed attempt, typed failure, gate evidence,
  workspace snapshot, or merge queue item already exists for the first derived
  group.
- Activation is idempotent for the same overlay id/sha and rejected for a
  different overlay over the same active suffix.
- Active resolver applies a typed overlay after restart and records the same
  observation shape as the legacy resolver.
- Resolver quiesces when active marker, canonical overlay, projection link, base
  DAG id/sha, validation digest, group offset, rollback artifact id/key, or
  overlay sha do not match.
- Resolver quiesces when a legacy active marker exists without a matching active
  typed row, and projection recovery succeeds only from an intact typed source
  row.
- Legacy G45-G73 projection fixtures still resolve through the compatibility
  path after the extraction.

Rollback:

- Rollback writes a `rolled_back` marker and typed rollback event before the
  first derived task starts.
- Rollback is rejected after any first-wave `dag-task:*`, `dag-group:*`,
  `dag-verify:*`, `dag-commit-failure:*`, typed attempt, typed failure, gate
  evidence, workspace snapshot, merge queue item, or non-regroup group event
  exists.
- Rollback rejection leaves the active marker untouched and does not create a
  partial rolled-back projection.
- Rollback never deletes canonical overlay, rollback artifact, validation rows,
  scheduler feedback rows, or checkpoints.
- After rollback rejection, a forward-only overlay can be staged from the latest
  checkpoint and must pass normal validation.

Scheduler metrics and adaptive sizing:

- Group metric builder joins typed attempts, failures, gate durations, merge
  queue outcomes, checkpoints, and projection evidence ids.
- Active and incomplete groups are excluded from completed throughput and p75
  hours/task averages but still appear in status output.
- Barrier-level stats are preferred over lane-level stats when sample count is
  at least two.
- Metrics with missing typed attempt, gate, merge/checkpoint, or projection
  evidence links are excluded from sizing and set data quality to
  `insufficient`, `mixed`, or `stale` as appropriate.
- Insufficient samples keep the current cap and emit `data_quality="insufficient"`.
- Higher repair or commit failure rates than baseline prevent widening.
- Unknown write sets cap recommendation at 4.
- High-risk barriers cap recommendation at 4; backend/multi-repo at 6; isolated
  UI/document at 10; test-only/perf at 14.
- Recommendation wave construction preserves topological order and refuses
  hard-barrier or write-set conflicts even when metrics support a wider cap.
- Scheduler recommendation writes feedback/review artifacts only and never
  writes `dag-regroup-active:*`.
- Review projection key is exactly `review:dag-sizing:{feature_id}:{window}` and
  is never consumed by resolver activation.

Regression:

- Existing tests for current regroup planner/validator/activation/rollback keep
  passing.
- `pytest tests/workflows/test_dag_regroup.py -q`
- `pytest tests/workflows/test_dag_expanded_verify.py -q`
- Full execution-control-plane regression from the acceptance matrix before the
  atomic landing.

## Acceptance Criteria

- Regroup behavior is reusable outside the one G45-G73 case.
- Scheduler recommendations are data-backed and include evidence ids.
- Activation remains explicit, bounded, and rollbackable.
- Speed-indexing cannot bypass dependency or write safety.
- Typed overlay rows and compatibility artifacts are written atomically.
- Active resolver has one source of truth for validation and dispatch.
- Adaptive sizing never includes active or incomplete waves in completed
  throughput averages.
- There is no production shadow phase for this slice. It lands only as part of
  the fully validated execution-control-plane feature.

## Rollout/Rollback Notes

This slice lands as part of the single atomic execution-control-plane feature:
typed overlay storage, validation, activation, resolver integration, scheduler
feedback, compatibility projections, dashboard/supervisor read models, and tests
must be ready together before production enablement. Internal development flags
may support local tests and fixture comparison, but there is no phased
production rollout and no compatibility-only production mode.

Rollback has two distinct meanings:

- Feature-level overlay rollback is allowed only before the first derived wave
  starts. It writes a `rolled_back` active marker and leaves all typed rows,
  projections, validation records, scheduler feedback, and checkpoints intact.
- Deployment rollback of the atomic control-plane feature stops new
  control-plane starts and resumes only features that have not crossed a
  product-authoritative control-plane boundary. If an overlay has already
  started execution, do not silently return to root DAG order; quiesce and use a
  forward-only overlay from the latest checkpoint or drain under the current
  validated path.

Never delete root DAG artifacts, regroup artifacts, checkpoints, merge proofs,
typed audit rows, or scheduler feedback as a rollback mechanism.

## Cross-Slice Dependencies

- Slice 1 supplies typed metrics and projection APIs.
- Slice 3 supplies write-set/contract information.
- Slice 7 supplies retry/failure taxonomy metrics.
- Slice 8 supplies merge duration and failure metrics.
- Slice 10 displays scheduler feedback.
- Slice 6 supplies gate duration and stale-context evidence.
- Slice 12 validates this slice only as part of the atomic execution-control-plane landing, not as a phased production rollout.
