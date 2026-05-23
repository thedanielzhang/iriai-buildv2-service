# 01. Typed Journal And Compatibility Projections

## Objective

Add a durable typed execution journal while preserving existing artifact/event
interfaces. This is the foundation for crash-safe resume, merge queue
idempotency, supervisor visibility, and future dashboard migration.

## Current Code Citations

- Existing durable tables: [schema.sql](/Users/danielzhang/src/iriai/iriai-build-v2/schema.sql:1).
- Artifact latest-by-key reads: [PostgresArtifactStore.get](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:71) and [get_record](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:82).
- Artifact summaries and slices: [list_record_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:143), [latest_summary](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:211), and [get_slice](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:242).
- Artifact append and dashboard mirror behavior: [PostgresArtifactStore.put](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:324) and [PublicDashboardOutbox.mirror_artifact_write](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/public_dashboard.py:192).
- Events: [FeatureStore.log_event](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:55), [get_events](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:115), and [list_event_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:122).
- Advisory lock primitive: [FeatureStore.advisory_lock](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:164).
- Task result projection shape: [ImplementationResult](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:1022).
- Commit failure projection shape and event: [_record_dag_commit_failure](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:789).
- Task result writes: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5147).
- Verify result writes: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3075).
- Group checkpoint writes: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4214).

## Current Failure Mode From `8ac124d6`

The workflow can write task evidence before commit and verification are truly
complete. Resume logic then sees `dag-task:*` evidence while the canonical repo,
verifier context, or group checkpoint may still be incomplete. This creates
stale projection, repeated verification, and ambiguous repair routing.

## Proposed Interfaces/Types

Add an `ExecutionControlStore` in `src/iriai_build_v2/workflows/develop/execution/journal.py`.

Required methods:

```python
class ExecutionControlStore:
    async def start_attempt(self, attempt: ExecutionAttemptCreate) -> ExecutionAttempt: ...
    async def finish_attempt(self, attempt_id: int, outcome: AttemptOutcome) -> AttemptFinishResult: ...
    async def record_workspace_snapshot(self, snapshot: WorkspaceSnapshotCreate) -> WorkspaceSnapshot: ...
    async def put_task_contract(self, contract: TaskDeliverableContractCreate) -> TaskDeliverableContract: ...
    async def record_patch_summary(self, summary: PatchSummaryCreate) -> EvidenceNode: ...
    async def record_contract_verdict(self, verdict: ContractVerdictCreate) -> EvidenceNode: ...
    async def record_sandbox_manifest(self, manifest: SandboxManifestCreate) -> EvidenceNode: ...
    async def add_evidence(self, evidence: EvidenceNodeCreate) -> EvidenceNode: ...
    async def add_evidence_edge(self, edge: EvidenceEdgeCreate) -> EvidenceEdge: ...
    async def add_evidence_graph(self, graph: EvidenceGraphCreate) -> EvidenceGraphResult: ...
    async def record_failure(self, failure: TypedFailureCreate) -> TypedFailure: ...
    async def reserve_route_budget(self, request: RouteBudgetReserveRequest) -> RouteBudgetReservation: ...
    async def finish_route_budget(self, request: RouteBudgetFinishRequest) -> RouteBudgetReservation: ...
    async def record_repair_request(self, request: RepairRequestCreate) -> EvidenceNode: ...
    async def record_repair_outcome(self, outcome: RepairOutcomeCreate) -> EvidenceNode: ...
    async def record_retry_request(self, request: RetryRequestCreate) -> EvidenceNode: ...
    async def record_retry_outcome(self, outcome: RetryOutcomeCreate) -> EvidenceNode: ...
    async def enqueue_merge(self, item: MergeQueueItemCreate) -> MergeQueueItem: ...
    async def claim_merge(self, feature_id: str, lease_owner: str) -> LeaseToken | None: ...
    async def heartbeat_merge(self, token: LeaseToken) -> MergeQueueItem: ...
    async def transition_merge(self, token: LeaseToken, transition: MergeTransition) -> MergeQueueItem: ...
    async def complete_merge(self, token: LeaseToken, result: MergeResult) -> MergeQueueItem: ...
    async def project_task_result(self, projection: TaskResultProjection) -> ProjectionResult: ...
    async def project_task_contract(self, projection: TaskContractProjection) -> ProjectionResult: ...
    async def project_contract_verdict(self, projection: ContractVerdictProjection) -> ProjectionResult: ...
    async def project_sandbox_manifest(self, projection: SandboxManifestProjection) -> ProjectionResult: ...
    async def project_sandbox_patch(self, projection: SandboxPatchProjection) -> ProjectionResult: ...
    async def project_workspace_snapshot(self, projection: WorkspaceSnapshotProjection) -> ProjectionResult: ...
    async def project_workspace_acl_normalization(self, projection: WorkspaceAclProjection) -> ProjectionResult: ...
    async def project_worktree_alias(self, projection: WorktreeAliasProjection) -> ProjectionResult: ...
    async def project_path_canonicalization(self, projection: PathCanonicalizationProjection) -> ProjectionResult: ...
    async def project_failure_route(self, projection: FailureRouteProjection) -> ProjectionResult: ...
    async def project_repair_request(self, projection: RepairRequestProjection) -> ProjectionResult: ...
    async def project_repair_outcome(self, projection: RepairOutcomeProjection) -> ProjectionResult: ...
    async def project_retry_request(self, projection: RetryRequestProjection) -> ProjectionResult: ...
    async def project_retry_outcome(self, projection: RetryOutcomeProjection) -> ProjectionResult: ...
    async def project_verify_result(self, projection: VerifyProjection) -> ProjectionResult: ...
    async def project_verify_graph(self, projection: VerifyGraphProjection) -> ProjectionResult: ...
    async def project_commit_failure(self, projection: CommitFailureProjection) -> ProjectionResult: ...
    async def project_group_checkpoint(self, projection: GroupCheckpointProjection) -> ProjectionResult: ...
    async def project_merge_proof(self, projection: MergeProofProjection) -> ProjectionResult: ...
    async def project_commit_proof(self, projection: CommitProofProjection) -> ProjectionResult: ...
    async def project_regroup_overlay(self, projection: RegroupProjection) -> ProjectionResult: ...
    async def project_landing_gate(self, projection: LandingGateProjection) -> ProjectionResult: ...
    async def project_regroup_active(self, projection: RegroupActiveProjection) -> ProjectionResult: ...
    async def project_regroup_rollback(self, projection: RegroupRollbackProjection) -> ProjectionResult: ...
    async def project_regroup_observation(self, projection: RegroupObservationProjection) -> ProjectionResult: ...
    async def project_sizing_review(self, projection: SizingReviewProjection) -> ProjectionResult: ...
    async def recover_projection(self, request: ProjectionRecoveryRequest) -> ProjectionResult: ...
    async def reconstruct_feature_state(self, feature_id: str) -> FeatureExecutionState: ...
```

All create/finish/project methods accept an explicit idempotency key and a
canonical request digest. The store computes the digest from stable JSON
serialization with sorted keys; callers may supply the key, but the store owns
digest comparison and conflict detection.

### Additive Tables

Use `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and additive
constraints only. The existing `features`, `events`, and `artifacts` tables
remain unchanged so legacy readers continue using latest-by-key artifact reads.
Postgres partial uniqueness must be created as `CREATE UNIQUE INDEX IF NOT
EXISTS`, not as table-level `UNIQUE ... WHERE` constraints. `ALTER TABLE ADD
CONSTRAINT` has no portable `IF NOT EXISTS`; every check, foreign key, and
deferrable circular foreign key must be added inside an idempotent `DO $$` block
that checks `pg_constraint` by explicit constraint name before altering the
table. New `updated_at` columns are maintained by store writes; this slice does
not rely on implicit triggers.

`execution_attempts`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Stable typed attempt id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope for all indexes. |
| `dag_sha256` | `TEXT NOT NULL DEFAULT ''` | Source DAG digest, empty only for legacy reconstruction. |
| `attempt_kind` | `TEXT NOT NULL` | One of `task`, `verify`, `repair`, `merge`, `checkpoint`, `regroup`. |
| `group_idx` | `INTEGER` | Required for group-scoped attempts. |
| `task_id` | `TEXT` | Required for task attempts. |
| `stage` | `TEXT NOT NULL` | `initial`, `retry-n`, `implementation-commit`, `checkpoint`, or queue stage. |
| `retry` | `INTEGER NOT NULL DEFAULT 0` | Numeric retry cursor; `initial` maps to `0`. |
| `status` | `TEXT NOT NULL` | `started`, `succeeded`, `failed`, `cancelled`, `incomplete`. |
| `dispatcher_state` | `TEXT NOT NULL DEFAULT 'requested'` | Durable sub-state for task/repair runtime attempts: `requested`, `attempt_started`, `context_prepared`, `runtime_invoking`, `runtime_returned`, `patch_capturing`, `output_normalizing`, `evidence_recording`, `succeeded`, `failed`, `cancelled`, or `incomplete`. Non-dispatch attempts keep `requested` until terminal status. |
| `actor` | `TEXT NOT NULL` | Dispatcher, verifier, merge queue, or repair service owner. |
| `runtime` | `TEXT NOT NULL DEFAULT ''` | Runtime/provider policy used for the attempt. |
| `input_digest` | `TEXT NOT NULL` | Digest over prompt, contract, snapshots, and prior evidence refs. |
| `request_digest` | `TEXT NOT NULL` | Digest over the full normalized `start_attempt` request, including scope, actor, runtime, and declared initial refs. |
| `workspace_snapshot_id` | `BIGINT` | Initial snapshot if known at start. No inline foreign key; see DDL order below. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable retry key. |
| `request_payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded request metadata, no raw prompt blobs. |
| `result_payload` | `JSONB NOT NULL DEFAULT '{}'` | Terminal summary and typed ids. |
| `started_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | First insert time. |
| `finished_at` | `TIMESTAMPTZ` | Set only for terminal states. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit and polling cursors. |

Indexes and constraints:

- `CHECK (status IN ('started', 'succeeded', 'failed', 'cancelled', 'incomplete'))`.
- `CHECK (dispatcher_state IN ('requested', 'attempt_started', 'context_prepared', 'runtime_invoking', 'runtime_returned', 'patch_capturing', 'output_normalizing', 'evidence_recording', 'succeeded', 'failed', 'cancelled', 'incomplete'))`.
- `CHECK (attempt_kind IN ('task', 'verify', 'repair', 'merge', 'checkpoint', 'regroup'))`.
- `CHECK ((attempt_kind <> 'task') OR task_id IS NOT NULL)`.
- `idx_execution_attempts_feature_group` on `(feature_id, dag_sha256, group_idx, id DESC)`.
- `idx_execution_attempts_task` on `(feature_id, task_id, retry, id DESC)` where `task_id IS NOT NULL`.
- `idx_execution_attempts_status` on `(feature_id, status, updated_at DESC)`.
- `idx_execution_attempts_started` on `(status, started_at)` where `status = 'started'`.

`workspace_snapshots`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Snapshot evidence id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `dag_sha256` | `TEXT NOT NULL DEFAULT ''` | Active DAG digest when group-scoped. Empty only for pre-DAG/global workspace probes. |
| `attempt_id` | `BIGINT REFERENCES execution_attempts(id)` | Nullable for pre-dispatch snapshots. |
| `group_idx` | `INTEGER` | Nullable only for pre-DAG/global probes; required for implementation/verification/merge/checkpoint stages. |
| `repo_id` | `TEXT NOT NULL` | Stable repo/workspace identifier. |
| `role` | `TEXT NOT NULL` | `canonical`, `sandbox`, `source`, or `auxiliary`. |
| `canonical_path` | `TEXT NOT NULL` | Absolute canonical repo path for the repo identity. |
| `workspace_relative_path` | `TEXT NOT NULL DEFAULT ''` | Path relative to feature workspace root. |
| `source_path` | `TEXT` | Source clone path when known. |
| `stage` | `TEXT NOT NULL` | Closed vocabulary: `pre_dispatch`, `post_workspace_repair`, `pre_runtime`, `post_runtime`, `pre_verify`, `pre_merge`, `post_apply`, `post_commit`, `checkpoint`, `rollback_recovery`. |
| `remote_url` | `TEXT` | Git remote URL when available. |
| `remote_fingerprint` | `TEXT` | Normalized remote identity digest. |
| `branch` | `TEXT` | Current branch when available. |
| `head_sha` | `TEXT NOT NULL DEFAULT ''` | Current HEAD or empty for non-git workspaces. |
| `git_common_dir` | `TEXT` | Git common dir for worktree identity evidence. |
| `source_git_common_dir` | `TEXT` | Source clone common dir when known. |
| `case_sensitivity` | `TEXT NOT NULL DEFAULT 'unknown'` | `case_sensitive`, `case_insensitive`, or `unknown`; contract validation fails closed when unknown and conflicting case variants exist. |
| `index_digest` | `TEXT NOT NULL DEFAULT ''` | Digest over index state. |
| `worktree_status_digest` | `TEXT NOT NULL DEFAULT ''` | Digest over changed paths and stat metadata. |
| `snapshot_digest` | `TEXT NOT NULL` | Digest over normalized authoritative snapshot columns, excluding ids, timestamps, and compatibility projection mirrors. |
| `dirty_paths` | `JSONB NOT NULL DEFAULT '[]'` | Sorted relative paths. |
| `staged_paths` | `JSONB NOT NULL DEFAULT '[]'` | Sorted relative staged paths. |
| `untracked_paths` | `JSONB NOT NULL DEFAULT '[]'` | Sorted relative untracked paths. |
| `forbidden_paths` | `JSONB NOT NULL DEFAULT '[]'` | Forbidden writes observed. |
| `denied_paths` | `JSONB NOT NULL DEFAULT '[]'` | Agent-writeability denied targets. |
| `symlink_paths` | `JSONB NOT NULL DEFAULT '[]'` | Symlink ancestors or targets observed. |
| `outside_root_targets` | `JSONB NOT NULL DEFAULT '[]'` | Rejected outside-root paths. |
| `agent_writable_paths` | `JSONB NOT NULL DEFAULT '[]'` | Paths proven writable by runtime identity/group. |
| `alias_paths` | `JSONB NOT NULL DEFAULT '[]'` | Alias paths mapped or rejected by workspace authority. |
| `registry_artifact_id` | `BIGINT REFERENCES artifacts(id)` | Worktree registry evidence when available. |
| `acl_artifact_id` | `BIGINT REFERENCES artifacts(id)` | ACL normalization/preflight evidence when available. |
| `compatibility_projection_artifact_ids` | `JSONB NOT NULL DEFAULT '[]'` | Legacy artifact ids projected from the snapshot. |
| `safety_status` | `TEXT NOT NULL DEFAULT 'ok'` | `ok`, `warning`, `blocked`, or `poisoned`. |
| `warnings` | `JSONB NOT NULL DEFAULT '[]'` | Bounded warnings; detailed evidence lives in evidence nodes. |
| `no_dirty` | `BOOLEAN NOT NULL DEFAULT FALSE` | True only when dirty paths are empty. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable snapshot key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded probe metadata. |
| `validated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Time the authority validated the snapshot. |
| `captured_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Snapshot time. |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit cursor for snapshot APIs. |

Indexes:

- `CHECK (stage IN ('pre_dispatch', 'post_workspace_repair', 'pre_runtime', 'post_runtime', 'pre_verify', 'pre_merge', 'post_apply', 'post_commit', 'checkpoint', 'rollback_recovery'))`.
- `CHECK (role IN ('canonical', 'sandbox', 'source', 'auxiliary'))`.
- `CHECK (case_sensitivity IN ('case_sensitive', 'case_insensitive', 'unknown'))`.
- `idx_workspace_snapshots_feature_group` on `(feature_id, dag_sha256, group_idx, stage, captured_at DESC)`.
- `idx_workspace_snapshots_feature_repo` on `(feature_id, repo_id, captured_at DESC)`.
- `idx_workspace_snapshots_attempt` on `(attempt_id, id DESC)` where `attempt_id IS NOT NULL`.
- `idx_workspace_snapshots_dirty` on `(feature_id, captured_at DESC)` where `no_dirty = FALSE`.

DDL order for the attempt/snapshot relationship is fixed:

1. Create `execution_attempts` with `workspace_snapshot_id BIGINT` but no
   foreign key.
2. Create `workspace_snapshots` with
   `attempt_id BIGINT REFERENCES execution_attempts(id)`.
3. Add `ALTER TABLE execution_attempts ADD CONSTRAINT
   execution_attempts_workspace_snapshot_id_fkey FOREIGN KEY
   (workspace_snapshot_id) REFERENCES workspace_snapshots(id) DEFERRABLE
   INITIALLY DEFERRED`.
4. Transactions that create an attempt and its initial snapshot insert the
   attempt first, insert the snapshot, then update
   `execution_attempts.workspace_snapshot_id` before commit. Tests must prove
   this migration runs from an empty database and reruns idempotently.

`task_deliverable_contracts`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Contract id used by dispatcher and gates. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `dag_sha256` | `TEXT NOT NULL` | Active DAG digest. |
| `source_dag_artifact_id` | `BIGINT REFERENCES artifacts(id)` | Source DAG row when available. |
| `source_dag_sha256` | `TEXT NOT NULL DEFAULT ''` | Source DAG body digest. |
| `group_idx` | `INTEGER NOT NULL` | Group assignment. |
| `task_id` | `TEXT NOT NULL` | Task id. |
| `repo_id` | `TEXT NOT NULL DEFAULT ''` | Primary target repo. |
| `repo_path` | `TEXT NOT NULL DEFAULT ''` | Primary target path. |
| `required_paths` | `JSONB NOT NULL DEFAULT '[]'` | Must exist or be changed as declared. |
| `allowed_paths` | `JSONB NOT NULL DEFAULT '[]'` | Declared writable closure. |
| `forbidden_paths` | `JSONB NOT NULL DEFAULT '[]'` | Must not be touched. |
| `generated_outputs` | `JSONB NOT NULL DEFAULT '[]'` | Generated-output path rules tied to source paths or gates. |
| `acceptance_criteria` | `JSONB NOT NULL DEFAULT '[]'` | Array of Slice 03 `AcceptanceCriterionSpec` objects. Each object must include stable `id`, source model/field/ordinal, text, linked path rule ids, and digest. |
| `verification_gates` | `JSONB NOT NULL DEFAULT '[]'` | Array of Slice 03 `VerificationGateSpec` objects. Each gate must include stable `id`, criterion ids, kind, optional command spec, required evidence specs, blocking flags, and digest. |
| `non_goals` | `JSONB NOT NULL DEFAULT '[]'` | Explicit non-goals/forbidden product outcomes. |
| `dependency_task_ids` | `JSONB NOT NULL DEFAULT '[]'` | Task ids that must be checkpointed or present in prior waves. |
| `unknown_write_set` | `BOOLEAN NOT NULL DEFAULT FALSE` | True when writable scope is ambiguous and must use conservative scheduling. |
| `compile_warnings` | `JSONB NOT NULL DEFAULT '[]'` | Bounded compiler warnings; any blocking issue is a typed failure. |
| `normalized_contract_json` | `JSONB NOT NULL` | Exact normalized `TaskDeliverableContract` body used for digest/projection parity. |
| `contract_digest` | `TEXT NOT NULL` | Digest over normalized contract fields. |
| `status` | `TEXT NOT NULL DEFAULT 'active'` | `active`, `superseded`, `cancelled`. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable create key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded display metadata. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `CHECK (status IN ('active', 'superseded', 'cancelled'))`.
- `UNIQUE (feature_id, dag_sha256, group_idx, task_id, contract_digest)`.
- `UNIQUE (id, feature_id, dag_sha256, group_idx, task_id)` to support
  composite foreign keys from merge queue coverage rows.
- Partial unique index `uniq_task_contracts_active_scope` on
  `(feature_id, dag_sha256, group_idx, task_id)` where `status = 'active'`.
- `idx_task_contracts_active_group` on `(feature_id, dag_sha256, group_idx, task_id)` where `status = 'active'`.
- `idx_task_contracts_source_artifact` on `(source_dag_artifact_id)` where `source_dag_artifact_id IS NOT NULL`.

Contract compilation is fenced by this partial unique index and the feature
advisory lock. A recompilation that changes `contract_digest` must mark the old
active row `superseded` and insert the new active row in the same transaction;
dispatch, gates, and merge queue must fail closed if they observe more than one
active row in legacy data.

`evidence_nodes`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Typed evidence id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `attempt_id` | `BIGINT REFERENCES execution_attempts(id)` | Nullable for imported legacy evidence. |
| `contract_id` | `BIGINT REFERENCES task_deliverable_contracts(id)` | Optional contract link. |
| `snapshot_id` | `BIGINT REFERENCES workspace_snapshots(id)` | Optional snapshot link. |
| `group_idx` | `INTEGER` | Nullable only for feature-level evidence. |
| `stage` | `TEXT NOT NULL DEFAULT ''` | Execution stage such as `pre_verify`, `initial`, `retry-n`, `pre_merge`, or `checkpoint`. |
| `kind` | `TEXT NOT NULL` | Must be one of the canonical evidence kinds listed below. |
| `name` | `TEXT NOT NULL DEFAULT ''` | Stable node/gate/lens name for graph and dashboard reads. |
| `status` | `TEXT NOT NULL DEFAULT 'approved'` | `pending`, `running`, `approved`, `rejected`, `failed`, or `skipped`. |
| `deterministic` | `BOOLEAN NOT NULL DEFAULT TRUE` | Whether the evidence came from deterministic code. |
| `source_ref` | `TEXT NOT NULL DEFAULT ''` | Runtime request id, artifact ref, event ref, or gate command id. |
| `artifact_id` | `BIGINT REFERENCES artifacts(id)` | Compatibility artifact link if any. |
| `artifact_key` | `TEXT NOT NULL DEFAULT ''` | Denormalized for bounded scans. |
| `event_id` | `BIGINT REFERENCES events(id)` | Event evidence link if any. |
| `input_refs` | `JSONB NOT NULL DEFAULT '[]'` | Bounded input refs for graph replay. |
| `output_refs` | `JSONB NOT NULL DEFAULT '[]'` | Bounded output refs for graph replay. |
| `failure_id` | `BIGINT` | Failure exposed by this node, if any. No inline foreign key; see DDL order below. |
| `verdict_id` | `BIGINT REFERENCES evidence_nodes(id)` | Verdict/aggregate node id when this node supports another verdict. |
| `content_hash` | `TEXT NOT NULL` | Digest over canonical evidence body. |
| `summary` | `TEXT NOT NULL DEFAULT ''` | Short display summary. |
| `metadata` | `JSONB NOT NULL DEFAULT '{}'` | Bounded graph/display metadata. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded typed body or refs to large blobs. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable evidence key. |
| `started_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Evidence/gate start time. |
| `finished_at` | `TIMESTAMPTZ` | Evidence/gate terminal time. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit and snapshot API cursor. |

Indexes:

- `CHECK (status IN ('pending', 'running', 'approved', 'rejected', 'failed', 'skipped'))`.
- `CHECK (kind IN (...canonical evidence kinds listed below...))`. The concrete
  DDL must enumerate every value from the list below, with no literal ellipsis,
  and must be generated from the same Python enum used by
  `ExecutionControlStore`; tests assert the enum and database allowed set are
  identical.
- `idx_evidence_feature_kind` on `(feature_id, kind, id DESC)`.
- `idx_evidence_feature_group_stage` on `(feature_id, group_idx, stage, kind, id DESC)`.
- `idx_evidence_status` on `(feature_id, status, id DESC)` where `status IN ('pending', 'running', 'rejected', 'failed')`.
- `idx_evidence_attempt` on `(attempt_id, id)` where `attempt_id IS NOT NULL`.
- `idx_evidence_artifact` on `(artifact_id)` where `artifact_id IS NOT NULL`.
- `idx_evidence_event` on `(event_id)` where `event_id IS NOT NULL`.
- `idx_evidence_content_hash` on `(feature_id, content_hash)`.

Canonical evidence kinds are owned here so later slices do not invent local
spellings. The allowed set is:

- Runtime and dispatch: `runtime_invocation`, `raw_output`,
  `structured_result`, `runtime_failure_context`.
- Workspace and contracts: `workspace_snapshot`, `workspace_preflight`,
  `workspace_acl_normalization`, `worktree_alias_preflight`,
  `path_canonicalization`, `contract_compile`, `contract_verdict`.
- Sandbox: `sandbox_lease`, `sandbox_manifest`, `sandbox_binding`,
  `sandbox_patch_summary`, `sandbox_cleanup`.
- Gates and verification: `gate_request`, `candidate_manifest`,
  `deterministic_gate`, `context_package`, `raw_verifier`, `expanded_lens`,
  `aggregate_verdict`, `merge_gate`, `checkpoint_gate`, `verify_verdict`.
- Queue and checkpoint: `merge_proof`, `commit_proof`, `checkpoint`,
  `queue_status`, `rollback_recovery`.
- Routing, repair, retry, compatibility, and landing validation: `failure_context`,
  `failure_route_decision`, `repair_request`, `repair_outcome`,
  `retry_request`, `retry_outcome`,
  `legacy_projection`, `supervisor_digest`, `scheduler_feedback`,
  `execution_control_landing`.

`evidence_edges`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Edge id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `from_node_id` | `BIGINT NOT NULL REFERENCES evidence_nodes(id)` | Source evidence node. |
| `to_node_id` | `BIGINT NOT NULL REFERENCES evidence_nodes(id)` | Target evidence node. |
| `edge_kind` | `TEXT NOT NULL` | `requires`, `reads`, `produces`, `blocks`, `supersedes`, `routes_to`, `projects`. |
| `required` | `BOOLEAN NOT NULL DEFAULT TRUE` | Required for aggregate approval when true. |
| `edge_digest` | `TEXT NOT NULL` | Digest over normalized source, target, kind, and required flag. Payload is display-only. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable edge key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded edge metadata. |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `CHECK (edge_kind IN ('requires', 'reads', 'produces', 'blocks', 'supersedes', 'routes_to', 'projects'))`.
- `UNIQUE (feature_id, from_node_id, to_node_id, edge_kind)`.
- `idx_evidence_edges_to` on `(feature_id, to_node_id, edge_kind)`.
- `idx_evidence_edges_from` on `(feature_id, from_node_id, edge_kind)`.

`evidence_graphs`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Graph id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `dag_sha256` | `TEXT NOT NULL` | Source DAG digest. |
| `group_idx` | `INTEGER` | Nullable only for feature-level graphs. |
| `stage` | `TEXT NOT NULL` | `pre_verify`, `initial`, `retry-n`, `checkpoint`, or queue stage. |
| `graph_kind` | `TEXT NOT NULL` | `verification`, `checkpoint_gate`, `merge_gate`, `recovery`, or `supervisor_digest`. |
| `aggregate_node_id` | `BIGINT REFERENCES evidence_nodes(id)` | Aggregate verdict node when available. |
| `required_node_ids` | `JSONB NOT NULL DEFAULT '[]'` | Display/cache mirror of `evidence_edges(required=true)` target node ids. Not authoritative for approval. |
| `optional_node_ids` | `JSONB NOT NULL DEFAULT '[]'` | Display/cache mirror of advisory/lens edge target node ids. |
| `blocking_failure_ids` | `JSONB NOT NULL DEFAULT '[]'` | Typed failure ids blocking approval. |
| `approved` | `BOOLEAN NOT NULL DEFAULT FALSE` | True only when all required nodes approve. |
| `input_digest` | `TEXT NOT NULL` | Digest of graph inputs and required node ids. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable graph key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded graph summary and typed refs. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `CHECK (graph_kind IN ('verification', 'checkpoint_gate', 'merge_gate', 'recovery', 'supervisor_digest'))`.
- `idx_evidence_graphs_feature_group` on `(feature_id, dag_sha256, group_idx, stage, id DESC)`.
- `idx_evidence_graphs_approved` on `(feature_id, approved, id DESC)`.

`add_evidence_graph` must insert the graph row, evidence nodes, and
`evidence_edges` in one transaction. The `approved` column is computed from
required edge rows joined to real `evidence_nodes.status`, not from
`required_node_ids` JSON. `required_node_ids`, `optional_node_ids`, and
`blocking_failure_ids` are bounded summary mirrors for dashboard reads; mutating
those JSON arrays cannot make a graph approved or rejected.

`typed_failures`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Failure id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `attempt_id` | `BIGINT REFERENCES execution_attempts(id)` | Attempt that exposed it. |
| `evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Primary evidence. |
| `failure_class` | `TEXT NOT NULL` | Must match the canonical `FailureClass` enum in [07-typed-failure-router.md](07-typed-failure-router.md). |
| `failure_type` | `TEXT NOT NULL` | Must match the canonical `FailureType` enum in [07-typed-failure-router.md](07-typed-failure-router.md). No pre-normalized or empty failure type may be inserted. |
| `severity` | `TEXT NOT NULL DEFAULT 'error'` | `info`, `warning`, `error`, `fatal`. |
| `deterministic` | `BOOLEAN NOT NULL DEFAULT FALSE` | Same inputs should fail again. |
| `operator_required` | `BOOLEAN NOT NULL DEFAULT FALSE` | Human action needed. |
| `retryable` | `BOOLEAN NOT NULL DEFAULT FALSE` | Automatic retry allowed only when `FailureTypePolicy` says so. |
| `status` | `TEXT NOT NULL DEFAULT 'open'` | `open`, `routed`, `retrying`, `resolved`, `suppressed`. |
| `route` | `TEXT NOT NULL DEFAULT ''` | Latest canonical `RouteAction` value or empty before routing. Legacy route strings stay in compatibility projection payloads only. |
| `signature_hash` | `TEXT NOT NULL` | Dedup key for same class/type/evidence signature. |
| `failure_digest` | `TEXT NOT NULL` | Digest over normalized failure body and policy-derived booleans. Used to detect idempotency conflicts that share a signature but differ in details. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable failure key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded details and refs. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit and snapshot API cursor. |
| `resolved_at` | `TIMESTAMPTZ` | Set only when the failure is resolved or suppressed. |

Indexes:

- `CHECK (failure_type <> '')`.
- `CHECK (route IN ('', 'retry_dispatch', 'run_product_repair', 'run_contract_repair', 'run_canonicalization_repair', 'run_workspace_repair', 'run_commit_hygiene_repair', 'retry_verifier', 'retry_merge', 'retry_sandbox_capture', 'run_sandbox_cleanup', 'quiesce', 'operator_required'))`.
- `idx_typed_failures_signature` on `(feature_id, failure_class, signature_hash, id DESC)`.
- `idx_typed_failures_attempt` on `(attempt_id, id DESC)` where `attempt_id IS NOT NULL`.
- Partial unique index `uniq_typed_failures_unresolved_signature` on
  `(feature_id, failure_class, failure_type, signature_hash)` where
  `status IN ('open', 'routed', 'retrying')`.

`record_failure` validates `(failure_class, failure_type)` against Slice 07's
`FailureTypePolicy` before insert and derives `retryable`, `deterministic`, and
`operator_required` from that policy unless the caller supplies the same values.
Conflicting caller booleans are rejected as `FailurePolicyConflict`.

`record_failure` dedupes unresolved failures by the partial unique signature
constraint, not by attempt-local idempotency alone. On conflict it locks and
returns the existing unresolved row, appending a bounded observation edge if the
new evidence id is novel. A later occurrence after `resolved` may create a new
row because it represents a new active blocker and consumes a new budget window.

DDL order for the evidence/failure relationship is fixed:

1. Create `evidence_nodes` with `failure_id BIGINT` but no foreign key.
2. Create `typed_failures` with `evidence_id BIGINT REFERENCES evidence_nodes(id)`.
3. Add `ALTER TABLE evidence_nodes ADD CONSTRAINT evidence_nodes_failure_id_fkey
   FOREIGN KEY (failure_id) REFERENCES typed_failures(id) DEFERRABLE INITIALLY
   DEFERRED`.
4. `add_evidence_graph` and `record_failure` transactions that create both sides
   must defer constraints until commit; tests must prove the migration can run
   from an empty database and can be rerun idempotently.

`failure_route_budgets` (reconciled — Slice 07): this dedicated budget table is
not part of the implemented typed journal. Retry-budget exhaustion is derived
from the existing durable journal-backed retry counters — the DAG group `retry`
counter for runtime-provider failures and the signature-scoped direct-route
counters — so retry state keeps a single source of truth. Route decisions
persist as the bounded `route_decision` object embedded in
`evidence_nodes(kind='runtime_failure_context')` payloads (and
`dag-direct-repair-route:*` projections). See `07-typed-failure-router.md`
§ Persistence And Artifact Compatibility for the authoritative description.

`merge_queue_items`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Queue item id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `dag_sha256` | `TEXT NOT NULL` | Source DAG digest. |
| `group_idx` | `INTEGER NOT NULL` | Group to integrate. |
| `repo_id` | `TEXT NOT NULL DEFAULT ''` | Repo target. |
| `repo_path` | `TEXT NOT NULL DEFAULT ''` | Canonical repo path. |
| `attempt_id` | `BIGINT REFERENCES execution_attempts(id)` | Merge/checkpoint attempt id. |
| `contract_ids` | `JSONB NOT NULL DEFAULT '[]'` | Display mirror of required task contracts. Authoritative task coverage is `merge_queue_task_coverage`. |
| `patch_evidence_ids` | `JSONB NOT NULL DEFAULT '[]'` | Display mirror of sandbox patch evidence. Authoritative patch inputs are evidence nodes/edges locked by enqueue. |
| `gate_evidence_ids` | `JSONB NOT NULL DEFAULT '[]'` | Display mirror of required gate evidence. Authoritative gate proofs are the real aggregate evidence columns below. |
| `pre_queue_gate_evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Aggregate pre-queue approval proof required before queue claim. |
| `post_apply_gate_evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Aggregate post-apply approval proof required before commit. |
| `base_commit` | `TEXT NOT NULL` | Expected base. |
| `head_commit` | `TEXT NOT NULL DEFAULT ''` | Candidate head or patch digest source. |
| `status` | `TEXT NOT NULL DEFAULT 'queued'` | `queued`, `leased`, `applying`, `verifying`, `committing`, `integrated`, `checkpointing`, `done`, `failed`, `poisoned`, `cancelled`. `integrated` means a lane is committed and clean but the group checkpoint is not yet projected. |
| `priority` | `INTEGER NOT NULL DEFAULT 100` | Lower claims first. |
| `lease_owner` | `TEXT` | Current worker. |
| `leased_until` | `TIMESTAMPTZ` | Lease expiration. |
| `lease_version` | `INTEGER NOT NULL DEFAULT 0` | Heartbeat fencing token. |
| `result_commit` | `TEXT NOT NULL DEFAULT ''` | Commit produced by queue. |
| `merge_proof_evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Required once status reaches `committing`; path/base/apply proof. |
| `commit_proof_evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Required once status reaches `integrated`; commit and no-dirty proof. |
| `checkpoint_gate_evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Required when status is `done`; approved Slice 06 `checkpoint_gate` evidence node for the group checkpoint. |
| `checkpoint_evidence_id` | `BIGINT REFERENCES evidence_nodes(id)` | Required when status is `done`; checkpoint body evidence linked from the approved checkpoint gate output refs. |
| `checkpoint_projection_id` | `BIGINT` | Required when status is `done`; `dag-group:*` projection link. No inline foreign key; see DDL order below. |
| `checkpoint_coverage_digest` | `TEXT NOT NULL DEFAULT ''` | Durable digest over sorted covered queue item ids, expected task ids, and retry supersession links. Required for `checkpointing` and `done`; used for idempotent checkpoint replay. |
| `checkpoint_body_sha256` | `TEXT NOT NULL DEFAULT ''` | Durable digest of the exact legacy `dag-group:*` body approved by the checkpoint gate. Required for `checkpointing` and `done`. |
| `retry_of_queue_item_id` | `BIGINT REFERENCES merge_queue_items(id)` | Source failed queue item when this row is a `retry_merge` replacement. This is the authoritative supersession link; JSON payload mirrors are display-only. |
| `failure_id` | `BIGINT REFERENCES typed_failures(id)` | Terminal failure if any. |
| `request_digest` | `TEXT NOT NULL` | Digest over normalized enqueue request, including parent fields, coverage rows, repo target rows, patch refs, and gate refs. Used for idempotency conflict detection. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable queue key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded queue metadata. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `CHECK (status IN ('queued', 'leased', 'applying', 'verifying', 'committing', 'integrated', 'checkpointing', 'done', 'failed', 'poisoned', 'cancelled'))`.
- `idx_merge_queue_claim` on `(feature_id, status, priority, id)` where `status IN ('queued', 'leased')`.
- `idx_merge_queue_lease_expiry` on `(leased_until, id)` where `status = 'leased'`.
- `idx_merge_queue_active_recovery` on `(feature_id, leased_until, status, id)` where `status IN ('applying', 'verifying', 'committing', 'checkpointing')`.
- `idx_merge_queue_group` on `(feature_id, dag_sha256, group_idx, id DESC)`.
- `idx_merge_queue_result_commit` on `(feature_id, result_commit)` where `result_commit <> ''`.
- `UNIQUE (id, feature_id, dag_sha256, group_idx)` to support composite foreign
  keys from task coverage rows.
- `idx_merge_queue_retry_source` on `(retry_of_queue_item_id)` where
  `retry_of_queue_item_id IS NOT NULL`.
- Partial unique index `uniq_merge_queue_retry_source_active` on
  `(retry_of_queue_item_id)` where `retry_of_queue_item_id IS NOT NULL AND
  status <> 'cancelled'`. A failed replacement can itself become the source of a
  later retry, but one source row cannot have two live replacement children.
- Gate/proof progression constraints use real columns, not JSONB payload:
  `status IN ('queued','leased','applying','verifying','committing','integrated','checkpointing','done')`
  requires `pre_queue_gate_evidence_id IS NOT NULL`;
  `status IN ('verifying','committing','integrated','checkpointing','done')` requires
  `merge_proof_evidence_id IS NOT NULL`;
  `status IN ('committing','integrated','checkpointing','done')` requires
  `post_apply_gate_evidence_id IS NOT NULL`;
  `status IN ('integrated','checkpointing','done')` requires
  `commit_proof_evidence_id IS NOT NULL`; `status = 'done'` requires
  `checkpoint_gate_evidence_id IS NOT NULL`, `checkpoint_evidence_id IS NOT NULL`,
  `checkpoint_projection_id IS NOT NULL`, and `result_commit <> ''`.
  `status IN ('checkpointing','done')` requires
  `checkpoint_coverage_digest <> ''` and `checkpoint_body_sha256 <> ''`.

`retry_of_queue_item_id` needs store-level transaction validation in addition
to the index because the invariant is cross-row: the source row must have the
same `feature_id`, `dag_sha256`, and `group_idx`; status `failed`; empty
`result_commit`; no poison state; matching `merge_queue_task_coverage` rows; and
no existing non-cancelled replacement. The replacement row must carry a fresh
`idempotency_key` and may only be created by the Slice 07 `retry_merge` route.

`merge_queue_task_coverage`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Durable task-coverage row id. |
| `queue_item_id` | `BIGINT NOT NULL REFERENCES merge_queue_items(id) ON DELETE RESTRICT` | Owning integration lane. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Duplicated scope for indexed checkpoint coverage queries. Must match parent. |
| `dag_sha256` | `TEXT NOT NULL` | Duplicated scope. Must match parent. |
| `group_idx` | `INTEGER NOT NULL` | Duplicated scope. Must match parent. |
| `task_id` | `TEXT NOT NULL` | Effective DAG task id covered by this lane. |
| `contract_id` | `BIGINT NOT NULL REFERENCES task_deliverable_contracts(id)` | Active contract proving this lane is allowed to satisfy `task_id`. |
| `coverage_digest` | `TEXT NOT NULL` | Digest over `(queue_item_id, feature_id, dag_sha256, group_idx, task_id, contract_id)`. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | `merge-coverage:{queue_item_id}:{task_id}:{contract_id}:{coverage_digest}`. |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `UNIQUE (queue_item_id, task_id)`.
- Composite foreign key
  `(queue_item_id, feature_id, dag_sha256, group_idx)` references
  `merge_queue_items(id, feature_id, dag_sha256, group_idx)`.
- Composite foreign key
  `(contract_id, feature_id, dag_sha256, group_idx, task_id)` references
  `task_deliverable_contracts(id, feature_id, dag_sha256, group_idx, task_id)`.
- `idx_merge_queue_task_coverage_group` on `(feature_id, dag_sha256, group_idx, task_id, queue_item_id)`.
- `idx_merge_queue_task_coverage_item` on `(queue_item_id, id)`.
- Store-level transaction validation locks the parent queue row and active
  contract rows, then rejects any scope mismatch, unknown task id, inactive
  contract, duplicate task id, or task/contract mismatch before enqueue commits.
- Checkpoint coverage, retry supersession, and duplicate-task detection must read
  this table joined to locked `merge_queue_items` rows. `payload.task_ids` and
  `payload.group_expected_task_ids` are display mirrors only and cannot authorize
  coverage.

`merge_queue_repo_targets`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Durable per-repo queue target/state row. |
| `queue_item_id` | `BIGINT NOT NULL REFERENCES merge_queue_items(id) ON DELETE RESTRICT` | Owning integration lane. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Duplicated scope for recovery queries. Must match parent. |
| `dag_sha256` | `TEXT NOT NULL` | Duplicated scope. Must match parent. |
| `group_idx` | `INTEGER NOT NULL` | Duplicated scope. Must match parent. |
| `repo_id` | `TEXT NOT NULL` | Canonical repo identity. |
| `repo_path` | `TEXT NOT NULL` | Canonical repo path approved by workspace authority. |
| `base_commit` | `TEXT NOT NULL` | Expected base for applying the patch in this repo. |
| `expected_head` | `TEXT NOT NULL DEFAULT ''` | Optional expected current head before apply. |
| `pre_apply_head` | `TEXT NOT NULL DEFAULT ''` | Recorded before canonical mutation; required once status reaches `applying`. |
| `applied_head` | `TEXT NOT NULL DEFAULT ''` | HEAD after patch apply and before commit. |
| `result_commit` | `TEXT NOT NULL DEFAULT ''` | Per-repo result commit after successful commit. |
| `tree_sha` | `TEXT NOT NULL DEFAULT ''` | Per-repo tree digest after commit. |
| `no_dirty_snapshot_id` | `BIGINT REFERENCES workspace_snapshots(id)` | Per-repo clean proof after commit. |
| `status` | `TEXT NOT NULL DEFAULT 'pending'` | `pending`, `pre_apply_recorded`, `applied`, `committed`, `clean`, `failed`, `poisoned`. |
| `target_digest` | `TEXT NOT NULL` | Digest over immutable target identity fields: queue item, scope, repo id/path, base commit, and expected head. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | `merge-repo-target:{queue_item_id}:{repo_id}:{base_commit}`. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded display metadata only. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `CHECK (status IN ('pending', 'pre_apply_recorded', 'applied', 'committed', 'clean', 'failed', 'poisoned'))`.
- Proof progression checks use real columns:
  `status IN ('pre_apply_recorded','applied','committed','clean')` requires
  `pre_apply_head <> ''`; `status IN ('applied','committed','clean')` requires
  `applied_head <> ''`; `status IN ('committed','clean')` requires
  `result_commit <> ''` and `tree_sha <> ''`; `status = 'clean'` requires
  `no_dirty_snapshot_id IS NOT NULL`.
- `UNIQUE (queue_item_id, repo_id)`.
- Composite foreign key
  `(queue_item_id, feature_id, dag_sha256, group_idx)` references
  `merge_queue_items(id, feature_id, dag_sha256, group_idx)`.
- `idx_merge_queue_repo_targets_group` on `(feature_id, dag_sha256, group_idx, queue_item_id, repo_id)`.
- `UNIQUE (queue_item_id, repo_id, target_digest)`.
- `idx_merge_queue_repo_targets_recovery` on `(feature_id, status, updated_at DESC)` where `status IN ('pre_apply_recorded', 'applied', 'committed')`.
- Store-level transaction validation ensures every target repo appears in
  workspace authority snapshots, belongs to the canonical feature root, and has
  write scope covered by the lane contracts before enqueue commits.
- Crash recovery, apply/rebase, commit proof, and no-dirty proof must use this
  table joined to locked `merge_queue_items`. `payload.repo_targets`,
  `payload.pre_apply_heads`, `payload.applied_heads`, and
  `payload.repo_commit_proofs` are display mirrors only.

`sandbox_leases`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Durable sandbox lease id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `dag_sha256` | `TEXT NOT NULL` | Source DAG digest. |
| `group_idx` | `INTEGER NOT NULL` | Owning group. |
| `attempt_id` | `BIGINT REFERENCES execution_attempts(id)` | Runtime or repair attempt. |
| `attempt_no` | `INTEGER NOT NULL` | Human-visible attempt number for projections. |
| `mode` | `TEXT NOT NULL` | `wave`, `task`, `repair`, or `canonicalization`. |
| `status` | `TEXT NOT NULL DEFAULT 'allocating'` | `allocating`, `allocated`, `binding`, `running`, `capturing`, `captured`, `released`, `retained`, `failed`, `poisoned`. |
| `lease_owner` | `TEXT NOT NULL` | Worker/runtime owner. |
| `leased_until` | `TIMESTAMPTZ NOT NULL` | Lease expiry for recovery. |
| `lease_version` | `INTEGER NOT NULL DEFAULT 0` | Fencing token. |
| `base_snapshot_ids` | `JSONB NOT NULL DEFAULT '[]'` | Canonical snapshots used to create the sandbox. |
| `sandbox_root` | `TEXT NOT NULL` | Absolute sandbox root; never a canonical repo path. |
| `lease_digest` | `TEXT NOT NULL` | Digest over sandbox root, base snapshots, repo ids, base commits, mode, and runtime owner inputs. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable allocation key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded allocation metadata. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

`sandbox_repo_bindings`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Binding id. |
| `sandbox_lease_id` | `BIGINT NOT NULL REFERENCES sandbox_leases(id)` | Owning lease. |
| `repo_id` | `TEXT NOT NULL` | Canonical repo identity. |
| `sandbox_repo_root` | `TEXT NOT NULL` | Writable sandbox repo root. |
| `canonical_repo_root` | `TEXT NOT NULL` | Blocked canonical root for escape checks. |
| `base_snapshot_id` | `BIGINT NOT NULL REFERENCES workspace_snapshots(id)` | Snapshot used to populate the sandbox. |
| `base_commit` | `TEXT NOT NULL DEFAULT ''` | Source commit. |
| `writable` | `BOOLEAN NOT NULL DEFAULT TRUE` | Whether runtime may modify this repo copy. |
| `blocked_canonical_roots` | `JSONB NOT NULL DEFAULT '[]'` | Canonical roots denied to runtime. |
| `status` | `TEXT NOT NULL DEFAULT 'active'` | `active`, `released`, or `poisoned`. |
| `binding_digest` | `TEXT NOT NULL` | Digest over lease id, repo identity, roots, base snapshot, base commit, and blocked roots. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | `sandbox-binding:{sandbox_lease_id}:{repo_id}:{binding_digest}`. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded binding metadata. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit and recovery cursor. |

`runtime_workspace_bindings`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Runtime binding id. |
| `sandbox_lease_id` | `BIGINT NOT NULL REFERENCES sandbox_leases(id)` | Owning lease. |
| `attempt_id` | `BIGINT NOT NULL REFERENCES execution_attempts(id)` | Runtime attempt. |
| `runtime_name` | `TEXT NOT NULL` | `claude`, `codex`, `claude_pool`, etc. |
| `cwd` | `TEXT NOT NULL` | Sandbox cwd only; canonical cwd is rejected. |
| `workspace_override` | `TEXT NOT NULL DEFAULT ''` | Runtime-specific workspace value. |
| `manifest_path` | `TEXT NOT NULL DEFAULT ''` | Manifest path when used. |
| `role_metadata_digest` | `TEXT NOT NULL` | Digest over runtime role/contract metadata. |
| `binding_digest` | `TEXT NOT NULL` | Digest over runtime name, cwd, workspace override, manifest path, and role metadata digest. |
| `status` | `TEXT NOT NULL DEFAULT 'bound'` | `bound`, `started`, `finished`, `failed`, or `poisoned`. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable binding key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded runtime metadata. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit and snapshot API cursor. |

Indexes and constraints:

- `CHECK (status IN ('allocating', 'allocated', 'binding', 'running', 'capturing', 'captured', 'released', 'retained', 'failed', 'poisoned'))`.
- `CHECK (mode IN ('wave', 'task', 'repair', 'canonicalization'))`.
- `idx_sandbox_leases_recovery` on `(status, leased_until, id)` where `status IN ('allocating', 'allocated', 'binding', 'running', 'capturing', 'captured', 'retained')`.
- `idx_sandbox_leases_feature_group` on `(feature_id, dag_sha256, group_idx, id DESC)`.
- `CHECK (status IN ('active', 'released', 'poisoned'))` on `sandbox_repo_bindings`.
- `CHECK (status IN ('bound', 'started', 'finished', 'failed', 'poisoned'))` on `runtime_workspace_bindings`.
- `UNIQUE (sandbox_lease_id, repo_id)` on `sandbox_repo_bindings`.
- `idx_sandbox_repo_bindings_lease` on `(sandbox_lease_id, repo_id)`.
- `idx_runtime_workspace_bindings_attempt` on `(attempt_id, id DESC)`.
- `CHECK (cwd NOT LIKE '%/.iriai/features/%/repos/%')` is not sufficient for safety; application code must compare `cwd` against blocked canonical roots from `sandbox_repo_bindings` before insert.

`execution_artifact_projections`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Projection audit row. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `source_table` | `TEXT NOT NULL` | `execution_attempts`, `evidence_nodes`, `evidence_graphs`, `typed_failures`, `merge_queue_items`, `workspace_snapshots`, `task_deliverable_contracts`, `execution_regroup_overlays`, `execution_regroup_validations`, or `execution_scheduler_feedback`. |
| `source_id` | `BIGINT NOT NULL` | Source typed row id. |
| `projection_owner` | `TEXT NOT NULL` | Service allowed to write the key family. |
| `projection_kind` | `TEXT NOT NULL` | `task_result`, `task_contract`, `contract_verdict`, `sandbox_manifest`, `sandbox_patch`, `workspace_snapshot`, `workspace_acl_normalization`, `worktree_alias_preflight`, `path_canonicalization`, `failure_route`, `repair_request`, `repair_outcome`, `retry_request`, `retry_outcome`, `verify_result`, `verify_graph`, `commit_failure`, `group_checkpoint`, `merge_proof`, `commit_proof`, `regroup_overlay`, `regroup_active`, `regroup_rollback`, `regroup_observation`, `sizing_review`, `landing_gate_review`. |
| `projection_key` | `TEXT NOT NULL` | Legacy artifact key. |
| `artifact_id` | `BIGINT NOT NULL REFERENCES artifacts(id)` | Inserted artifact row. |
| `legacy_event_id` | `BIGINT REFERENCES events(id)` | Optional legacy event row inserted by the same projection transaction, such as `dag_commit_failed` or `dag_group_checkpoint`. |
| `dashboard_outbox_event_id` | `TEXT REFERENCES public_dashboard_outbox(event_id)` | Optional outbox event inserted by the same transaction when dashboard mirroring is enabled. |
| `body_sha256` | `TEXT NOT NULL` | Digest over exact artifact body. |
| `idempotency_key` | `TEXT NOT NULL UNIQUE` | Stable projection key. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded metadata. |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes and constraints:

- `CHECK (source_table IN ('execution_attempts', 'evidence_nodes',
  'evidence_graphs', 'typed_failures', 'merge_queue_items',
  'workspace_snapshots', 'task_deliverable_contracts',
  'execution_regroup_overlays', 'execution_regroup_validations',
  'execution_scheduler_feedback'))`.
- `CHECK (projection_owner IN ('dispatcher', 'contract_service',
  'sandbox_runner', 'workspace_authority', 'failure_router', 'repair_service',
  'verification_graph', 'merge_queue', 'regroup_overlay',
  'landing_validator'))`.
- `CHECK (projection_kind IN ('task_result', 'task_contract',
  'contract_verdict', 'sandbox_manifest', 'sandbox_patch',
  'workspace_snapshot', 'workspace_acl_normalization',
  'worktree_alias_preflight', 'path_canonicalization', 'failure_route',
  'repair_request', 'repair_outcome', 'retry_request', 'retry_outcome',
  'verify_result', 'verify_graph', 'commit_failure', 'group_checkpoint',
  'merge_proof', 'commit_proof', 'regroup_overlay', 'regroup_active',
  'regroup_rollback', 'regroup_observation', 'sizing_review',
  'landing_gate_review'))`.
- `UNIQUE (source_table, source_id, projection_key)`.
- `UNIQUE (artifact_id)`.
- `idx_execution_projections_legacy_event` on `(legacy_event_id)` where `legacy_event_id IS NOT NULL`.
- `idx_execution_projections_dashboard_event` on `(dashboard_outbox_event_id)` where `dashboard_outbox_event_id IS NOT NULL`.
- `idx_execution_projections_feature_key` on `(feature_id, projection_key, id DESC)`.
- `idx_execution_projections_source` on `(source_table, source_id, id DESC)`.

DDL order for the merge-queue/checkpoint-projection relationship is fixed:

1. Create `merge_queue_items` with `checkpoint_projection_id BIGINT` but no
   foreign key.
2. Create `merge_queue_task_coverage` and `merge_queue_repo_targets` after
   `merge_queue_items`; their parent FKs have no circular dependency and must be
   available before queue enqueue code can run.
3. Create `execution_artifact_projections`.
4. Add `ALTER TABLE merge_queue_items ADD CONSTRAINT
   merge_queue_items_checkpoint_projection_id_fkey FOREIGN KEY
   (checkpoint_projection_id) REFERENCES execution_artifact_projections(id)
   DEFERRABLE INITIALLY DEFERRED`.
5. The group checkpoint transaction locks all `integrated` lanes, loads coverage
   from `merge_queue_task_coverage`, sets
   `checkpoint_projection_id` before setting `status = 'done'`, and validates the
   real-column proof constraint plus FK at commit.

### Transaction Boundaries

- Every mutating `ExecutionControlStore` method obtains one `asyncpg`
  connection and opens one database transaction. It does not call
  `PostgresArtifactStore.put()` for journal-owned projections because that API
  acquires its own connection and mirrors the dashboard outside the transaction.
- Projection methods insert or update the typed source row, insert the
  compatibility `artifacts` row, insert any legacy `events` row and public
  dashboard outbox row, then insert the `execution_artifact_projections` link in
  the same transaction. Dashboard delivery may remain asynchronous, but the
  outbox enqueue must be committed with the artifact projection.
- Projection methods that replace legacy write sites which also emitted
  `events` must insert those event rows through the same connection and record
  `legacy_event_id` on `execution_artifact_projections`. Do not call
  `PostgresFeatureStore.log_event()` from the journal path because it acquires
  its own connection and mirrors best-effort outside the transaction.
- The dashboard mirror insert must be connection-aware and schema-compatible
  with the existing `public_dashboard_outbox` table: deterministic
  `event_id` (`artifact-write:{artifact_id}` for artifact projections), no
  separate outbox idempotency column, bounded payload, and `ON CONFLICT
  (event_id) DO NOTHING`. Do not call
  `PublicDashboardOutbox.emit_event()` or `mirror_artifact_write()` from inside a
  journal transaction because those helpers use the pool and swallow failures.
- `start_attempt` inserts only the attempt row and initial evidence/snapshot refs
  supplied in the request. It never projects a legacy success key.
- `finish_attempt` locks the attempt row with `FOR UPDATE`, validates legal
  status transitions, writes terminal evidence/failure rows, and projects only
  if the outcome includes an owner-approved projection request.
- `project_task_result`, `project_repair_request`, `project_repair_outcome`,
  `project_verify_result`, `project_commit_failure`, and
  `project_group_checkpoint` are the only methods allowed to insert legacy
  `dag-*` execution keys from the typed path. They all insert artifact rows
  append-only; they never update or delete old artifact rows.
- `claim_merge` uses `FOR UPDATE SKIP LOCKED` over claimable queue rows and
  atomically sets `status = 'leased'`, `lease_owner`, `leased_until`, increments
  `lease_version`, and returns `LeaseToken(item_id, lease_owner, lease_version)`.
  Expired leases are claimable; non-expired leases are not.
- `heartbeat_merge`, `transition_merge`, and `complete_merge` all update by
  `(id, lease_owner, lease_version)` and fail with `LeaseLost` on zero rows.
- `complete_merge` and `project_group_checkpoint` run under the feature advisory
  lock before writing `dag-group:*`, preventing duplicate checkpoint projection
  when two workers recover the same result commit.

### Canonical Digests

Every idempotent request has one authoritative digest column. The digest input
is a normalized Python value serialized with `json.dumps(..., sort_keys=True,
separators=(',', ':'), default=str)` after Pydantic models have been converted
with `model_dump(mode='json')`. Artifact body digests are different: they are
`sha256` over the exact UTF-8 body string that legacy readers should receive,
before any artifact-store spill envelope is stored in `artifacts.value`.

Do not compare `payload::text` or other JSONB textual renderings for
idempotency. `request_payload`, `result_payload`, `payload`, and `metadata` are
bounded display/debug fields unless a table explicitly declares a digest column
for that body. On conflict, the store compares the explicit digest columns
(`request_digest`, `input_digest`, `snapshot_digest`, `contract_digest`,
`content_hash`, `edge_digest`, `failure_digest`, `budget_digest`,
`coverage_digest`, `target_digest`, `lease_digest`, `binding_digest`,
`role_metadata_digest`, or `body_sha256`, depending on the table) and returns
the existing row only when those digests match.

### Real Columns And Mirrors

State transitions, checkpoint authority, route budgets, task coverage, repo
targets, leases, evidence approval, and projection lineage must be derived from
named columns, foreign keys, typed child tables, and evidence edges. JSON fields
named `payload`, `metadata`, `request_payload`, or `result_payload` are never
authorization sources. If a payload mirror says a task is covered, a repo is
clean, a route budget remains, or a checkpoint is done, but the corresponding
real row/column does not, the store treats the mirror as stale and routes repair
or recovery.

Top-level columns with closed semantics, including JSONB arrays such as
`workspace_snapshots.dirty_paths` and
`task_deliverable_contracts.allowed_paths`, plus typed child rows such as
`merge_queue_task_coverage`, may be authoritative only when the plan names them
explicitly and their digest participates in the request digest. Any summary
mirror can be dropped and rebuilt from typed rows without changing workflow
state.

### Idempotency Keys

Idempotency keys are stable, human-inspectable prefixes plus a digest:

- Attempts:
  `attempt:{feature_id}:{dag_sha256}:g{group_idx}:{task_id or '-'}:{attempt_kind}:{stage}:r{retry}`.
  `input_digest` and `request_digest` are stored on the row and compared on
  conflict; a duplicate attempt scope with a different digest raises
  `IdempotencyConflict` instead of creating a second dispatch attempt.
- Workspace snapshots:
  `snapshot:{feature_id}:{dag_sha256}:g{group_idx or '-'}:{stage}:{repo_id}:{head_sha}:{index_digest}:{worktree_status_digest}`.
  `snapshot_digest` is compared on conflict because the key intentionally omits
  large path arrays.
- Contracts:
  `contract:{feature_id}:{dag_sha256}:g{group_idx}:{task_id}:{contract_digest}`.
- Evidence:
  `evidence:{feature_id}:{attempt_id or '-'}:{kind}:{content_hash}:{source_ref}`.
- Evidence edges:
  `edge:{feature_id}:{from_node_id}:{to_node_id}:{edge_kind}:{required}`.
  `edge_digest` is compared on conflict.
- Evidence graphs:
  `graph:{feature_id}:{dag_sha256}:g{group_idx}:{stage}:{graph_kind}:{input_digest}`.
- Failures:
  `failure:{feature_id}:{attempt_id or '-'}:{failure_class}:{signature_hash}`.
- Sandbox leases:
  `sandbox:{feature_id}:{dag_sha256}:g{group_idx}:{mode}:{attempt_no}:{repo_ids_digest}:{base_commits_digest}:{contract_ids_digest}:{base_snapshot_digest}`.
  Binding rows use their own `binding_digest` and idempotency keys so rerunning
  sandbox allocation can return already-created repo/runtime bindings without
  trusting lease payload mirrors.
- Merge queue:
  `merge:{feature_id}:{dag_sha256}:g{group_idx}:{integration_lane}:{task_ids_digest}:{repo_id}:{base_commit}:{head_commit or patch_digest}`.
- Merge queue task coverage:
  `merge-coverage:{queue_item_id}:{task_id}:{contract_id}:{coverage_digest}`.
- Projections:
  `projection:{feature_id}:{projection_owner}:{projection_key}:{source_table}:{source_id}:{body_sha256}`.

On conflict, the store fetches the existing row and compares the stored digest
fields listed above. If they match, the operation returns the existing row as an
idempotent success. If they differ, it raises `IdempotencyConflict` and records
no partial state.

### Projection Ownership

Legacy key ownership is strict and enforced in projection helper code:

| Legacy key family | Owner | Source row | Notes |
| --- | --- | --- | --- |
| `dag-task:{task_id}` | `dispatcher` | `evidence_nodes(kind='structured_result')` | Exact `ImplementationResult` JSON body. Attempt evidence only; never canonical integration proof. |
| `dag-task-contract:{task_id}` | `contract_service` | `task_deliverable_contracts` | Bounded contract summary only: contract id, digest, repo id, path counts, unknown-write flag, gate list, and compile warnings. |
| `dag-contract-verdict:g{group_idx}:{task_id}:{sandbox_id}` | `contract_service` | `evidence_nodes(kind='contract_verdict')` | Bounded verdict over patch/contract validation. It is compatibility evidence, not merge/checkpoint authority. |
| `dag-sandbox:g{group_idx}:attempt-{attempt_no}` | `sandbox_runner` | `evidence_nodes(kind='sandbox_manifest')` | Bounded sandbox manifest summary for dashboard/supervisor visibility. |
| `dag-sandbox-patch:g{group_idx}:attempt-{attempt_no}:repo-{repo_id}` | `sandbox_runner` | `evidence_nodes(kind='sandbox_patch_summary')` | Bounded patch summary; diff bodies remain spill-backed artifact evidence. |
| `dag-workspace-snapshot:g{group_idx}:{stage}:repo-{repo_id}` | `workspace_authority` | `workspace_snapshots` | Bounded snapshot summary with digests, path counts, safety status, and projection lineage. |
| `dag-workspace-acl-normalization:g{group_idx}:{stage}` | `workspace_authority` | `evidence_nodes(kind='workspace_acl_normalization')` plus `workspace_snapshots` | ACL normalization/preflight evidence; may describe chmod/chgrp/setgid metadata repair but not product-content changes. |
| `dag-worktree-alias-preflight:g{group_idx}:{stage}` | `workspace_authority` | `evidence_nodes(kind='worktree_alias_preflight')` plus `workspace_snapshots` | Alias detection and classification evidence before dispatch or retry. |
| `dag-path-canonicalization:g{group_idx}` | `workspace_authority` | `evidence_nodes(kind='path_canonicalization')` | Metadata-only path/projection canonicalization summary. Alias-only product-content repair still goes through sandbox and merge queue. |
| `dag-direct-repair-route:g{group_idx}:{stage}` | `failure_router` | `evidence_nodes(kind='failure_route_decision')` | Compatibility route summary for legacy supervisor/dashboard readers. Executor authority is the typed route decision and `failure_route_budgets`, not this artifact. |
| `dag-repair-request:g{group_idx}:{stage}:{failure_id}` | `repair_service` | `evidence_nodes(kind='repair_request')` | Bounded repair request summary: route decision id, repair kind, allowed mutations, target contracts/paths, sandbox mode, enqueue strategy, and required evidence ids. |
| `dag-repair-outcome:g{group_idx}:{stage}:{failure_id}` | `repair_service` | `evidence_nodes(kind='repair_outcome')` | Bounded repair outcome summary: status, attempt id, sandbox id, patch ids, merge queue item ids, resolved or produced failure id, and projected artifact ids. |
| `dag-retry-request:g{group_idx}:{stage}:{failure_id}` | `repair_service` | `evidence_nodes(kind='retry_request')` | Bounded retry request summary for dispatcher, verifier, merge, sandbox-capture, or cleanup retry. It preserves original contract/gate/sandbox/queue ids. |
| `dag-retry-outcome:g{group_idx}:{stage}:{failure_id}` | `repair_service` | `evidence_nodes(kind='retry_outcome')` | Bounded retry outcome summary with spawned attempt/evidence ids and resolved or produced failure id. |
| `dag-verify:g{group_idx}:{initial|retry-n|checkpoint-commit}` | `verification_graph` for verifier attempts, `merge_queue` for `checkpoint-commit` commit failures | `evidence_nodes(kind='aggregate_verdict')` or `typed_failures` | Exact current verifier/commit-failure verdict shape. `aggregate_verdict` is the compatibility source for model verify stages; checkpoint-commit failures project from typed commit failures. |
| `dag-verify-graph:g{group_idx}:{stage}` | `verification_graph` | `evidence_graphs` plus required `evidence_nodes`/`evidence_edges` | Bounded graph summary: graph id, aggregate node id, required gate node ids, raw verifier node id, lens node ids, and approval status. It is advisory/debug projection only; checkpoint authority still comes from typed graph evidence. |
| `dag-commit-failure:g{group_idx}:{stage}` | `merge_queue` | `typed_failures(failure_class='commit_hygiene')` | Canonical commit failures only occur in queue-owned canonical apply/commit/checkpoint stages. The projection also inserts the legacy `dag_commit_failed` event in the same transaction and stores `legacy_event_id`. The failure router records route decisions but does not write this key. |
| `dag-group:{group_idx}` | `merge_queue` | `merge_queue_items(status='done')` plus checkpoint evidence | Written only after gate evidence, result commit, no-dirty proof, and feature advisory lock. The projection also inserts the legacy `dag_group_checkpoint` event in the same transaction and stores `legacy_event_id`. |
| `dag-merge-proof:g{group_idx}` / `dag-commit-proof:g{group_idx}` | `merge_queue` | `evidence_nodes(kind='merge_proof'/'commit_proof')` | New compatibility keys for supervisor/debug; not required by legacy resume. |
| `dag-regroup:{overlay_slug}` / `dag-regroup-active:{overlay_slug}` / `dag-regroup-rollback:{overlay_slug}` / `dag-regroup-observation:{overlay_slug}` | `regroup_overlay` | `execution_regroup_overlays` and `execution_regroup_validations` | Synchronous compatibility views over typed overlay rows. Root `dag` remains immutable. |
| `review:dag-sizing:{feature_id}:{window}` | `regroup_overlay` | `execution_scheduler_feedback` | Review-only projection. It must never be consumed as an activation marker. |
| `review:execution-control-landing:{candidate}` | `landing_validator` | `evidence_nodes(kind='execution_control_landing')` | Review-only atomic landing gate result. It records validation evidence for the complete control-plane candidate and is never consumed as runtime authority. |

No module outside `execution/journal.py` may assemble these keys for writes.
Existing legacy readers can keep assembling keys until their slice migrates.

### Service Method Semantics

- `start_attempt` returns the existing row for a matching idempotency key. It
  rejects a terminal existing row only when the request digest differs.
- `finish_attempt` is terminal-idempotent. Repeating the same terminal outcome
  returns the existing terminal row and existing projection links; attempting to
  change `succeeded` to `failed`, or vice versa, raises `InvalidAttemptTransition`.
- `record_workspace_snapshot` creates immutable snapshot rows. If the associated
  attempt is already terminal, the snapshot is allowed only for recovery metadata
  and cannot change the attempt outcome.
- `put_task_contract` creates immutable active contracts and supersedes older
  active contracts for the same `(feature_id, dag_sha256, group_idx, task_id)`
  only when the new contract digest differs and the caller supplies a
  `supersedes_contract_id`.
- `add_evidence` creates immutable evidence and may link an existing artifact or
  event id, but only projection methods may create new journal-owned artifact
  rows.
- `record_failure` deduplicates by signature hash within a feature/failure
  class, increments occurrence metadata in `payload`, and preserves the first
  evidence id as the primary route anchor.
- `enqueue_merge` creates one item per candidate patch/head and inserts all
  `merge_queue_task_coverage` and `merge_queue_repo_targets` rows in the same
  transaction. It computes `merge_queue_items.request_digest` from the normalized
  parent request plus child coverage/target rows, returns the existing row only
  when that digest and the child-row digests match, and raises
  `IdempotencyConflict` otherwise. It does not claim, apply, commit, or
  checkpoint in the same method. It rejects an enqueue if task coverage or repo
  target state can be inferred only from payload JSON.
- `claim_merge` returns at most one `LeaseToken` and fences stale workers with
  `lease_version`.
- `heartbeat_merge` extends only a lease held by the same owner and version.
- `transition_merge` applies non-terminal state changes only when the supplied
  `LeaseToken` still matches `(id, lease_owner, lease_version)`.
- `complete_merge` moves a lane to `integrated` after commit/no-dirty proof.
  Group checkpoint projection is a separate coordinator transaction that locks all
  integrated lanes for the group, may mark them `checkpointing` inside the
  transaction, and sets every covered lane to `done` with the same checkpoint
  projection link.
- `recover_projection` is used after a crash when a typed terminal row exists
  but a required projection link does not. It reuses the original projection
  idempotency key and body digest, and it refuses to reconstruct bodies from
  stale artifact latest-by-key state.
- `reconstruct_feature_state` returns typed state when typed rows exist for the
  feature. For artifact-only legacy features, it returns a `LegacyFeatureState`
  built from bounded artifact/event summary APIs and marks all typed ids absent.

## Refactoring Steps

1. Add the tables, constraints, and indexes above to `schema.sql` using
   additive DDL only.
2. Implement `execution/journal.py` with low-level connection-aware helpers for
   typed inserts, artifact projection inserts, projection links, and dashboard
   outbox enqueue.
3. Add typed Pydantic models in `execution/types.py` for create requests,
   terminal outcomes, projection requests, idempotency conflicts, and
   reconstructed feature state.
4. Replace legacy write sites for `dag-task:*`, `dag-verify:*`,
   `dag-commit-failure:*`, and `dag-group:*` with a write-path adapter that
   branches by feature execution mode. Features that never entered typed
   execution keep using the legacy artifact writer until they finish. New typed
   features, or features explicitly restarted from a validated checkpoint under
   the complete control plane, must write only through journal projection
   helpers. This is a behavior-preserving compatibility projection, not a
   shadow writer.
5. Move key construction for new writes into journal-owned functions such as
   `task_result_key(task_id)`, `verify_key(group_idx, retry_label)`,
   `commit_failure_key(group_idx, stage)`, and `group_checkpoint_key(group_idx)`.
6. Move companion event logging for typed commit-failure and group-checkpoint
   projections into the journal helpers so the artifact, legacy event, dashboard
   outbox row, and projection audit link commit together.
7. Keep legacy read adapters in place. They may read and write old
   artifact-only features through the legacy branch, but new writes from the
   typed path must go through the journal.
8. Update dashboard/supervisor/resume tests so compatibility artifact behavior
   is asserted from the artifact rows produced by typed projection transactions.
9. Land schema, store, caller refactor, recovery helper, and tests as one atomic
   feature change. Do not ship a production shadow phase, dual writer, or
   flag-gated partial path.

## Migration Steps

1. Apply additive DDL. Old application code remains compatible because existing
   tables and artifact/event behavior are unchanged.
2. Deploy the code change that requires the journal for new execution-control
   writes. The deploy must include schema, service implementation, caller
   refactors, and tests together.
3. At feature resume time, classify each feature once:
   `typed` if it has `execution_attempts` rows; `legacy` if it has no typed rows
   but has existing `dag-*` artifacts/events; `new` if it has neither.
4. `legacy` features continue through the legacy reader/adapter and may finish
   without synthetic backfill. They do not receive mixed typed writes. A feature
   enters typed execution only by an explicit restart from a validated checkpoint
   under the complete control plane.
5. `new` features use typed journal writes from the first attempt. Every legacy
   artifact they need is produced as a synchronous compatibility projection from
   the journal transaction.
6. If deployment must be rolled back, revert application code. Additive tables
   and projection audit rows remain in the database; legacy artifacts written by
   the journal are ordinary artifact rows and remain readable.

## Persistence And Artifact Compatibility

- `dag-task:{task_id}` must keep the exact
  `ImplementationResult.model_dump_json()` body shape used today.
- `dag-verify:g{group_idx}:{initial|retry-n|checkpoint-commit}` must keep the
  current verifier body produced by the existing `to_str(verdict)` call path.
- `dag-commit-failure:g{group_idx}:{stage}` must remain readable by existing
  supervisor and repair code. Its body must match
  `json.dumps(_commit_failure_payload(...), indent=2)`, including current
  `metadata`, `outcomes`, `successful_commit_hashes`, and
  `manifest_forbidden_matches` behavior.
- `dag-group:{group_idx}` remains the checkpoint boundary for legacy resume. Its
  body must match the current `_json.dumps(checkpoint)` shape with `group_idx`,
  `task_ids`, `results` from `ImplementationResult.model_dump()`, `verdict`, and
  `commit_hash`.
- Artifact projections must be synchronous with typed writes. A typed success invisible to dashboard/resume is not acceptable after landing.
- Projection body serialization must match the current caller output byte for
  byte where current tests assert exact JSON strings. If existing code emits
  `model_dump_json()`, the journal projection uses that same serialization.
- `artifacts` remains append-only. Projection transactions insert new rows and
  rely on existing latest-by-key reads for compatibility.
- Projection audit rows make typed-to-legacy lineage explicit:
  typed source row -> projection link -> artifact id -> optional legacy event id
  -> optional dashboard outbox event id.
- Public dashboard delivery can still retry asynchronously, but the outbox row
  that causes its ETag to change must be committed with the artifact projection
  when dashboard mirroring is enabled.
- New typed summary reads must use `execution_attempts`, `evidence_nodes`,
  `typed_failures`, and `execution_artifact_projections` indexes before falling
  back to artifact bodies.

## Edge Cases And Failure Handling

- Crash before transaction commit: no typed/projection state is visible; retry recreates the transaction.
- Crash after transaction commit: typed record, artifact row, and projection link are all visible; retry no-ops or updates only allowed terminal metadata.
- Canonical repo mutations are not rolled back by a database transaction. Before
  applying or committing to a canonical repo, the queue worker must persist the
  locked queue item, repo target, `pre_apply_head`, and intent status. Recovery
  reconciles Git HEAD/tree state against `merge_queue_repo_targets` and
  `workspace_snapshots`; it never assumes a DB rollback undid a filesystem or Git
  mutation.
- Duplicate runner process: advisory lock protects feature-wide checkpoint transitions; merge queue row leases protect queue items.
- Old feature with only artifacts: typed journal reads must fall back to legacy projection reconstruction.
- Mixed feature from rollback or manual recovery: typed projection links are authoritative for typed attempts; latest legacy artifacts remain only the compatibility surface for legacy consumers.
- Typed terminal row exists but projection link is missing: this should only be
  possible from manual DB edits or pre-transaction bugs. `recover_projection`
  locks the typed source row, rebuilds the body from typed payload/evidence, and
  inserts the missing artifact/link exactly once. It emits a typed
  `checkpoint_contradiction` failure if the body cannot be reconstructed without
  reading latest-by-key stale artifacts.
- Artifact row exists but projection link is missing: recovery may attach the
  existing artifact only if `feature_id`, `projection_key`, `body_sha256`, and
  created time fall within the same typed transaction audit window recorded in
  payload. Otherwise it writes a new artifact row and marks the orphan as legacy
  evidence.
- Projection link exists but artifact row is missing: treat as database
  corruption, record fatal typed failure, and stop checkpoint/resume for that
  feature.
- Idempotency key collision with different digest: reject the write and route a
  deterministic workflow failure; never append a second row under the same key.
- Lease holder dies while applying merge: lease expiry allows a new worker to
  claim; the new worker reconstructs from `base_commit`, patch evidence, and
  snapshots before advancing status.
- Crash after canonical patch apply but before commit: recovery compares each
  `merge_queue_repo_targets.pre_apply_head`, `applied_head`, and live repo HEAD.
  If the apply was complete and clean it resumes at post-apply verification; if
  the repo is dirty or ambiguous it records a typed `commit_hygiene` or
  `workspace` failure and does not project `dag-group:*`.
- Crash after repo commit but before `result_commit` is stored: recovery reads
  the live repo HEAD, proves it descends from the recorded `pre_apply_head` or
  matches the expected tree, records `result_commit` and a fresh no-dirty
  snapshot, then resumes checkpointing. If ancestry/tree proof fails, the item is
  poisoned and routed to operator/workspace repair.
- Crash after merge result commit before checkpoint projection: recovery finds
  `merge_queue_items.status IN ('integrated','checkpointing')`, reacquires the
  feature advisory lock, proves no-dirty state again for covered lanes, and writes
  one `dag-group:*` projection only when group coverage is complete.
- Stale started attempts: `reconstruct_feature_state` may mark a `started`
  attempt `incomplete` only after proving no active runtime binding, sandbox
  lease, or merge lease can still complete it. It must not infer success from a
  stray `dag-task:*` artifact.
- Legacy artifact-only feature resumes after atomic landing: it stays on the
  legacy adapter for writes required to finish that feature. New typed state is
  not partially introduced mid-feature.
- New typed feature sees old stale `dag-task:*` artifact for same task id:
  resume prefers projection links tied to the active `dag_sha256` and attempt
  ids. Latest-by-key artifact reads remain compatibility only, not typed
  authority.

## Tests

- Schema tests:
  - Additive schema creation succeeds on a database that already has `features`,
    `events`, `artifacts`, `public_dashboard_outbox`, `public_display_jobs`, and
    `sessions`.
  - Re-running schema creation is idempotent.
  - Required indexes exist by name and support `EXPLAIN` plans for feature/group
    attempt scans, evidence scans, projection latest-by-key scans, and merge
    queue claims.
  - Idempotent constraint DDL is rerunnable: deferrable foreign keys and check
    constraints are created once through `pg_constraint` guards, and partial
    uniqueness is implemented with named `CREATE UNIQUE INDEX IF NOT EXISTS`
    statements.
  - Check constraints reject invalid attempt, contract, failure, and queue
    statuses.
  - Evidence kind enum parity test proves the Python enum, DDL check constraint,
    and documentation list contain the same canonical spellings.
  - Graph approval tests mutate `evidence_graphs.required_node_ids` and prove
    approval still follows `evidence_edges(required=true)` plus real node
    statuses.
- Idempotency tests:
  - `start_attempt` returns the same row for the same key/digest.
  - `start_attempt` raises `IdempotencyConflict` for same key with different
    `input_digest` or `request_digest`.
  - `finish_attempt` is idempotent for the same terminal outcome and rejects a
    conflicting terminal outcome.
  - Projection idempotency returns the existing artifact id/link for same
    source/key/body digest and rejects same source/key with a different body.
- Projection parity tests:
  - Legacy artifact-only feature write-path test proves resumed legacy features
    continue writing through the legacy adapter and create no typed journal rows
    unless explicitly restarted under the complete control plane.
  - Typed feature write-path test proves `dag-task:*`, `dag-verify:*`,
    `dag-commit-failure:*`, and `dag-group:*` writes go through journal
    projection helpers and cannot call the legacy writer directly.
  - Task projection writes byte-equivalent `dag-task:{task_id}` bodies matching
    current `ImplementationResult.model_dump_json()` output.
  - Verify projection writes byte-equivalent `dag-verify:*` bodies for initial,
    retry, and checkpoint-commit verdicts.
  - Commit failure projection writes the current `dag-commit-failure:*` payload
    and preserves `dag_commit_failed` event linkage through
    `execution_artifact_projections.legacy_event_id`.
  - Checkpoint projection writes the current `dag-group:*` body only from a
    completed merge queue item with result commit and no-dirty evidence, and
    preserves `dag_group_checkpoint` event linkage through
    `execution_artifact_projections.legacy_event_id`.
  - Dashboard outbox parity proves typed projection inserts the same
    `artifact.written` event payload and deterministic `event_id` shape as the
    existing mirror path, but through the journal transaction.
  - Merge queue tests prove it cannot call `project_task_result` or write
    `dag-task:*`.
- Atomicity tests:
  - Inject an exception before commit and assert no typed row, artifact row,
    projection link, or dashboard outbox row is visible.
  - Inject an exception after typed insert but before artifact insert and assert
    rollback removes the typed insert.
  - Inject an exception after artifact insert but before projection link and
    assert rollback removes both.
  - Successful projection commits typed row, artifact row, projection link, and
    dashboard outbox row together.
- Lease and recovery tests:
  - `claim_merge` prevents duplicate claims with concurrent workers.
  - Expired lease can be claimed by another owner and increments lease version.
  - Stale lease owner cannot heartbeat or complete after a newer lease version.
  - `enqueue_merge` inserts `merge_queue_task_coverage` rows transactionally and
    rejects missing, duplicated, inactive-contract, or cross-feature coverage.
  - `enqueue_merge` inserts `merge_queue_repo_targets` rows transactionally and
    rejects missing, duplicated, noncanonical, cross-feature, or outside-root repo
    targets.
  - Schema tests prove `merge_queue_task_coverage` composite foreign keys reject
    parent queue scope mismatch and task contract scope mismatch even when payload
    mirrors look valid.
  - Recovery tests mutate `payload.repo_targets` and `payload.pre_apply_heads` and
    prove apply/recovery still follows `merge_queue_repo_targets` rows.
  - Checkpoint coverage tests mutate `payload.task_ids` and prove coverage still
    follows `merge_queue_task_coverage` rows, not JSON payload mirrors.
  - Recovery tests simulate crash after canonical patch apply but before commit,
    and crash after repo commit but before `result_commit` is stored; both must
    reconcile from `merge_queue_repo_targets`, workspace snapshots, and Git
    ancestry/tree proofs before any checkpoint projection.
  - `retry_merge` replacement tests prove source/replacement task coverage is
    compared through `merge_queue_task_coverage` rows and indexed replacement
    links, not payload fields.
  - Crash after result commit before checkpoint projection recovers and writes
    one `dag-group:*`.
  - Missing projection link recovery reconstructs from typed payload, not stale
    latest-by-key artifacts.
- Resume tests:
  - Resume after `dag-task:*` but before `dag-group:*` treats the group as
    incomplete and routes to merge/verify recovery, not checkpointed completion.
  - Resume of a typed feature uses projection links scoped to active
    `dag_sha256`.
  - Resume of an artifact-only legacy feature reconstructs a `LegacyFeatureState`
    from bounded artifact/event summary APIs.
  - Mixed state created by old in-flight features does not let latest legacy
    artifacts override typed authority for new attempts.
  - Stale `started` attempts become `incomplete` only after runtime binding,
    sandbox lease, and merge lease checks prove no worker can still complete
    them.
- Ownership and caller tests:
  - Static test or `rg`-based guard fails if new production code writes
    `dag-task:*`, `dag-verify:*`, `dag-commit-failure:*`, or `dag-group:*`
    outside `execution/journal.py` and approved legacy adapters.
  - Projection helper rejects an owner/key mismatch, such as merge queue writing
    `dag-task:*`.
  - Dashboard ETag/outbox cursor changes after a typed projection.

## Acceptance Criteria

- No existing dashboard, supervisor, resume, or regroup consumer breaks.
- All legacy authority keys are still written for new typed attempts.
- Typed journal exposes enough state for later slices without reading artifact bodies broadly.
- No checkpoint can be projected without an idempotent typed checkpoint transition.
- `dag-task:*` remains attempt evidence and cannot be interpreted as canonical integration or checkpoint proof.
- There is one atomic production landing for this slice: schema, journal store,
  caller write-path refactor, compatibility projections, recovery helper, and
  tests land together.
- New execution-control-plane writes have no dual writer and no shadow-mode
  production behavior. Compatibility artifacts are the supported legacy surface,
  not a temporary comparison stream.
- Old artifact-only features remain resumable through the legacy adapter without
  requiring automatic typed backfill.
- Every projection has a single owner, a typed source row, an idempotency key,
  and an `execution_artifact_projections` audit link with any legacy event or
  dashboard outbox lineage recorded in real columns.
- No workflow state transition, checkpoint decision, route budget, task coverage
  decision, repo target decision, or recovery decision depends on
  `payload`/`metadata` JSON mirrors.

## Rollout/Rollback Notes

This slice is not rolled out as a phased production shadow mode. It lands as one
atomic feature change after the schema/store/projection/refactor/recovery tests
pass.

Rollback is application-level:

- Revert the journal write-path change if the deployment is bad.
- Leave additive tables in place; they do not affect old code.
- Keep any compatibility artifact rows already written because they are normal
  append-only artifact history.
- Do not delete typed audit rows during rollback. They may be needed to explain
  projections already visible to dashboard, supervisor, or resume.
- If a feature has started on typed journal writes before rollback, either drain
  it through the typed path or stop it and resume only after a repair migration
  explicitly converts its journal state. Do not silently hand a partially typed
  feature to the legacy writer.

## Cross-Slice Dependencies

- Slice 2 writes workspace snapshots through this store.
- Slice 3 writes task deliverable contracts through this store.
- Slice 7 writes typed failures through this store.
- Slice 8 writes merge queue items and checkpoint projections through this store.
- Slice 10 reads typed summaries from this store.
