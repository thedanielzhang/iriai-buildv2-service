# 10. Supervisor And Dashboard Integration

## Objective

Move supervisor and dashboard visibility from artifact-body inference toward
typed control-plane summaries. Supervisor remains read-only/advisory in v1.
This slice lands as one atomic feature: typed snapshot APIs, dashboard use,
supervisor evidence/classifier wiring, Slack dedupe, read-only policy, and tests
ship together. There is no production phase where dashboard and supervisor
disagree about the authoritative execution state.

## Current Code Citations

- Supervisor classifier priority: [SupervisorClassifier.classify](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:24).
- Supervisor failure/action enums: [models.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:14) and [models.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:25).
- Supervisor observation/evidence packet models: [SupervisorObservation](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:201) and [ClassificationResult](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/models.py:283).
- Operator-required classifier: [classifier.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:38).
- Pipeline-bug classifier: [classifier.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:110).
- Safe-restart classifier: [classifier.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:205).
- Deterministic-unblock classifier: [classifier.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:254).
- Normal product repair classifier: [classifier.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:340).
- Dashboard feature assembly and ETag handling: [dashboard.py](/Users/danielzhang/src/iriai/iriai-build-v2/dashboard.py:268).
- Supervisor evidence collection: [evidence.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/evidence.py:102).
- Read-only MCP evidence service: [mcp_server.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/mcp_server.py:93).
- Current MCP snapshot tool: [SupervisorEvidenceMcpService.get_current_snapshot](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/mcp_server.py:145) and [FastMCP tool registration](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/mcp_server.py:568).
- Read-only evidence toolbox and bounded artifact paths: [SupervisorEvidenceToolbox](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/tools.py:35), [_list_artifact_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/tools.py:137), and [_artifact_rows_by_id](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/tools.py:187).
- Public dashboard outbox: [public_dashboard.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/public_dashboard.py:79).
- Supervisor action policy: [ActionPolicy](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/actions.py:27).
- Supervisor Slack service protocol: [SupervisorSlackService](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/slack.py:128).
- Artifact summary API for bounded views: [list_record_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:143).
- Event summary API for bounded views: [list_event_summaries](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/features.py:122).

## Current Failure Mode From `8ac124d6`

Supervisor messages sometimes repeated stale restart or degraded evidence while
the executor was actively handling deterministic issues. Artifact-body inference
also risks stale or unbounded context. The supervisor needs typed state, not
heuristic reconstruction from noisy logs.

## Proposed Interfaces/Types

Add typed summary APIs in
`src/iriai_build_v2/workflows/develop/execution/snapshots.py` and expose them
through the Slice 1 `ExecutionControlStore`.

```python
SnapshotScope = Literal["dashboard", "supervisor", "mcp"]

class SnapshotBudget(BaseModel):
    max_attempts: int = 20
    max_failures: int = 40
    max_merge_items: int = 40
    max_retry_budgets: int = 40
    max_gate_results: int = 40
    max_workspace_snapshots: int = 20
    max_evidence_refs: int = 80
    max_event_summaries: int = 100
    max_artifact_summaries: int = 200
    max_artifact_detail_chars: int = 20_000
    max_path_samples_per_snapshot: int = 10
    max_response_bytes: int = 250_000
    query_timeout_ms: int = 1_500

class ControlPlaneSnapshotQuery(BaseModel):
    feature_id: str
    group_idx: int | None = None
    after_snapshot_version: str | None = None
    include_terminal_groups: bool = False
    scope: SnapshotScope
    budget: SnapshotBudget = Field(default_factory=SnapshotBudget)

class SnapshotCursor(BaseModel):
    table: Literal[
        "execution_attempts",
        "workspace_snapshots",
        "typed_failures",
        "failure_route_budgets",
        "merge_queue_items",
        "evidence_nodes",
        "sandbox_leases",
        "runtime_workspace_bindings",
    ]
    max_id: int
    max_updated_at: datetime | None = None

class EvidenceRef(BaseModel):
    table: Literal["evidence_nodes", "artifacts", "events", "workspace_snapshots"]
    id: int
    citation: str
    kind: str = ""
    summary: str = ""
    artifact_key: str = ""

class ExecutionAttemptSummary(BaseModel):
    attempt_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_id: str | None
    attempt_kind: Literal["task", "verify", "repair", "merge", "checkpoint", "regroup"]
    stage: str
    retry: int
    status: Literal["started", "succeeded", "failed", "cancelled", "incomplete"]
    actor: str
    runtime: str
    input_digest: str
    workspace_snapshot_id: int | None
    latest_evidence_ids: list[int]
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime

class WorkspaceSnapshotSummary(BaseModel):
    snapshot_id: int
    attempt_id: int | None
    group_idx: int | None
    repo_id: str
    role: str
    canonical_path: str
    workspace_relative_path: str
    stage: str
    head_sha: str
    index_digest: str
    worktree_status_digest: str
    no_dirty: bool
    safety_status: str
    dirty_path_count: int
    dirty_path_sample: list[str]
    forbidden_path_count: int
    forbidden_path_sample: list[str]
    captured_at: datetime

class TypedFailureSummary(BaseModel):
    failure_id: int
    attempt_id: int | None
    evidence_id: int | None
    failure_class: str
    failure_type: str
    severity: Literal["info", "warning", "error", "fatal"]
    deterministic: bool
    operator_required: bool
    retryable: bool
    status: Literal["open", "routed", "retrying", "resolved", "suppressed"]
    route: str
    signature_hash: str
    summary: str
    evidence_refs: list[EvidenceRef]
    created_at: datetime
    resolved_at: datetime | None

class MergeQueueSummary(BaseModel):
    item_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    repo_id: str
    status: Literal[
        "queued", "leased", "applying", "verifying", "committing",
        "integrated", "checkpointing", "done", "failed", "poisoned", "cancelled"
    ]
    priority: int
    lease_owner: str | None
    leased_until: datetime | None
    lease_version: int
    result_commit: str
    failure_id: int | None
    required_gate_evidence_ids: list[int]
    updated_at: datetime

class RetryBudgetSummary(BaseModel):
    scope: Literal["feature", "group", "failure_signature", "route"]
    group_idx: int | None
    route: str
    failure_signature_hash: str | None
    budget_total: int
    budget_used: int
    budget_remaining: int
    terminal_reason: str = ""

class GateStatusSummary(BaseModel):
    gate_name: str
    group_idx: int | None
    approved: bool
    deterministic: bool
    evidence_id: int
    failure_id: int | None
    created_at: datetime

class SandboxLeaseSummary(BaseModel):
    lease_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    mode: str
    status: str
    sandbox_root: str
    patch_summary_ids: list[int]
    leased_until: datetime | None
    updated_at: datetime

class RuntimeBindingSummary(BaseModel):
    binding_id: int
    sandbox_lease_id: int
    attempt_id: int
    runtime_name: str
    status: str
    cwd: str
    updated_at: datetime

class ControlPlaneSnapshot(BaseModel):
    feature_id: str
    snapshot_version: str
    generated_at: datetime
    source: Literal["typed", "legacy_fallback", "mixed"]
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
    truncated: bool = False
    omitted_counts: dict[str, int] = Field(default_factory=dict)
    cursors: list[SnapshotCursor] = Field(default_factory=list)
    active_group_idx: int | None
    active_attempts: list[ExecutionAttemptSummary]
    workspace_snapshots: list[WorkspaceSnapshotSummary]
    latest_failures: list[TypedFailureSummary]
    merge_queue: list[MergeQueueSummary]
    retry_budgets: list[RetryBudgetSummary]
    sandbox_leases: list[SandboxLeaseSummary]
    runtime_bindings: list[RuntimeBindingSummary]
    gates: list[GateStatusSummary]
    checkpoints: list[EvidenceRef]
    recommended_route: str
    recommended_action: Literal["observe", "digest", "recommend", "act_guarded", "stop/escalate"]
    evidence_refs: list[EvidenceRef]

class SupervisorDigest(BaseModel):
    feature_id: str
    group_idx: int | None
    snapshot_version: str
    classification: Literal[
        "healthy_progress",
        "normal_product_repair",
        "deterministic_unblock",
        "pipeline_bug_suspected",
        "operator_required",
        "watch_only",
        "safe_restart_candidate",
        "stale_codex_invocation",
    ]
    confidence: float
    facts: list[str]
    inference: str
    recommended_action: Literal["observe", "digest", "recommend", "act_guarded", "stop/escalate"]
    recommended_route: str
    failure_signature_hashes: list[str]
    evidence_refs: list[EvidenceRef]
    slack_dedupe_key: str
    suppress_until: datetime | None = None
```

Store methods:

```python
class ExecutionControlStore:
    async def get_control_plane_snapshot(
        self,
        query: ControlPlaneSnapshotQuery,
    ) -> ControlPlaneSnapshot: ...

    async def get_control_plane_snapshot_version(
        self,
        feature_id: str,
    ) -> str: ...
```

The version is a stable digest over max ids and max `updated_at` values from
`execution_attempts`, `typed_failures`, `failure_route_budgets`,
`merge_queue_items`, `evidence_nodes`, `workspace_snapshots`, `sandbox_leases`,
and `runtime_workspace_bindings`. It must not hash artifact bodies. Budget-only
and sandbox-only updates must therefore advance the snapshot version even when
the underlying failure row does not change.

Snapshot contract invariants:

- `ControlPlaneSnapshot` is the single shared status contract for dashboard,
  MCP, supervisor classification, Slack digest generation, and public dashboard
  projection. Consumers may render smaller views, but they must not reconstruct
  workflow authority from artifact bodies or untyped event text.
- `snapshot_version` is the ETag seed, Slack/outbox idempotency seed, audit
  replay cursor, and optimistic concurrency token for display projections. Every
  supervisor digest, Slack decision, and public outbox event records the exact
  version it used.
- `source="typed"` is the normal path. `source="mixed"` is allowed only when a
  bounded typed query partially degrades and bounded legacy summaries fill
  display context. `source="legacy_fallback"` is for old features with missing
  typed rows. Neither fallback mode may outrank a present typed route,
  checkpoint, gate, or merge queue state.
- Snapshot builders enforce feature and optional group scope on every table
  read before joining or aggregating. Budgets are maximum caps, not caller
  preferences that can be raised through query parameters.
- All payloads are summary-only: ids, digests, counts, bounded samples,
  timestamps, statuses, routes, and citations. Raw prompts, artifact values,
  stdout/stderr bodies, verifier bodies, and complete dirty path lists stay
  behind bounded detail endpoints.

### Dashboard Integration Points

- Add `/api/feature/{feature_id}/control-plane` as the typed, bounded API used
  by new dashboard panels and supervisor tooling. Keep `/api/feature/{feature_id}`
  compatible by embedding a compact `control_plane` object when typed tables
  exist.
- Extend the existing dashboard ETag composition with
  `get_control_plane_snapshot_version(feature_id)` so control-plane-only changes
  refresh the UI without waiting for artifact/event writes.
- Render these panels from typed rows only: active attempts, workspace/sandbox
  snapshots, latest typed failures, route/retry budget, merge queue, gate state,
  checkpoints, and supervisor digest.
- Detail panes use `EvidenceRef` ids to call existing bounded artifact/event
  detail endpoints. The summary payload never includes artifact bodies,
  full event content, raw prompts, raw stdout/stderr, or full dirty path lists.
- Public dashboard mirroring emits a bounded `control_plane.snapshot_changed`
  outbox event containing `feature_id`, `snapshot_version`, visible counters,
  route, and cited evidence refs. It does not publish private evidence bodies.
- Public outbox writes use `(feature_id, snapshot_version,
  "control_plane.snapshot_changed")` as the idempotency key. Re-projecting the
  same snapshot updates delivery metadata or coalesced counters; it does not
  enqueue duplicate public notifications.
- UI copy must label legacy fallback state explicitly as degraded so operators
  do not mistake artifact reconstruction for typed control-plane truth.

### Supervisor Classifier Mapping

Typed route decisions are primary. Legacy artifact classifiers remain only as
fallback when `ControlPlaneSnapshot.source == "legacy_fallback"` or when a typed
row cites a legacy artifact as evidence.

Deterministic executor-owned classes must never become operator escalation while
their typed route is retry/repair and budget remains. This includes worktree
aliasing, ACL workability, stale projection, commit hygiene, contract compile
repair, verifier context rebuild, sandbox allocation/capture/cleanup, runtime
structured output, merge retry, and bounded diagnostic dispatch. If one of these
classes reaches `route="quiesce"`, the supervisor reports the stopped route and
the scheduler/workflow correction needed; it still must not ask the operator to
manually edit files, copy artifacts, rebase branches, or restart the bridge
unless independent bridge evidence satisfies the safe-restart row below.
`operator_required` is valid only when the typed router emits
`failure_class="operator_required"` or `operator_required=true` and no
deterministic repair route with remaining budget exists.

| Typed evidence | Supervisor class | Action | Notes |
| --- | --- | --- | --- |
| `typed_failures.failure_class = 'checkpoint_contradiction'` or gate failure followed by checkpoint evidence | `pipeline_bug_suspected` | `stop/escalate` | Highest priority. Blocks restart/product repair recommendations. |
| `failure_class in ('dispatcher_internal', 'sandbox_isolation', 'evidence_corruption')` or any `severity='fatal'` route `quiesce` with `failure_class != 'checkpoint_contradiction'` | `pipeline_bug_suspected` | `stop/escalate` | Fatal control-plane safety issues. Supervisor reports executor quiesce and cited evidence, not restart/product repair. Checkpoint contradictions are matched only by the row above. |
| `failure_class in ('regroup_invalid', 'contract_compile')` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Scheduler/contract correction is required before dispatch can continue. If the router returns `run_contract_repair`, classify as deterministic unblock instead. |
| `failure_class in ('worktree_alias', 'acl_workability', 'stale_projection', 'commit_hygiene')` with route `run_canonicalization_repair`, `run_workspace_repair`, `run_commit_hygiene_repair`, or `retry_verifier` and budget remains | `deterministic_unblock` | `recommend` | Never escalates to operator while `retryable` and budget remains. Stale generated-output projections use `stale_projection/verifier_context_stale`. |
| `failure_class in ('worktree_alias', 'acl_workability', 'stale_projection', 'commit_hygiene')` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Deterministic unblock budget is exhausted or no longer safe. Supervisor reports the blocked route and scheduler/workflow correction need, not manual file copying or broad product repair. |
| `failure_class = 'contract_compile'` with route `run_contract_repair` and budget remains | `deterministic_unblock` | `recommend` | Contract repair is executor-owned only when the router selected an explicit repair route. `contract_compile/quiesce` is mapped above as a safety stop. |
| `failure_class in ('sandbox_allocation', 'sandbox_capture', 'sandbox_cleanup', 'runtime_context', 'runtime_structured_output')` with route in `retry_dispatch`, `retry_sandbox_capture`, or `run_sandbox_cleanup` and budget remains | `deterministic_unblock` | `recommend` | Deterministic retry/repair stays executor-owned while budget remains. |
| `failure_class in ('sandbox_allocation', 'sandbox_capture', 'sandbox_cleanup', 'runtime_context', 'runtime_structured_output')` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | The router has stopped automatic retry for this deterministic class. |
| `failure_class = 'resource_exhausted'` with route in `retry_dispatch`, `retry_sandbox_capture`, or `run_sandbox_cleanup` and budget remains | `watch_only` | `observe` | Resource governor/backoff is the next executor-owned action. |
| `failure_class = 'resource_exhausted'` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Resource pressure is no longer safely retryable. |
| `failure_class in ('sandbox_binding', 'runtime_cancelled')` with route `quiesce` | `watch_only` | `observe` | The workflow is intentionally paused or prevented from unsafe runtime start. Do not recommend product repair. |
| `failure_class = 'verifier_context'` with route `retry_verifier` | `deterministic_unblock` | `recommend` | Verifier-context rebuild is executor-owned. The display must not call it product repair or safe-restart unless the bridge is separately dead with no active lease. |
| `failure_class = 'verifier_provider'` with route `retry_verifier` and active provider retry budget | `watch_only` | `observe` | Verifier provider failures do not mark product unhealthy until a verifier returns a product verdict. |
| `failure_class = 'verifier_provider'` with route `quiesce` | `watch_only` | `observe` | Provider retry budget is exhausted or unsafe; supervisor reports provider blockage without classifying product unhealthy. |
| `failure_class = 'merge_conflict'` with route `retry_merge` and an active queue budget | `deterministic_unblock` | `recommend` | Queue recovery is executor-owned; supervisor reports route/budget and does not ask for manual rebase while retry remains. |
| `failure_class = 'merge_conflict'` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Merge retry is exhausted or unsafe; scheduler feedback/regroup is required before continuing. |
| `failure_class = 'operator_required'` or `operator_required = true` and no deterministic repair route remains | `operator_required` | `stop/escalate` | Requires explicit typed route; worktree samples alone are insufficient if the router can repair. |
| `failure_class = 'product_defect'` with route `run_product_repair` | `normal_product_repair` | `recommend` | Product failures stay product repair even if historical pipeline noise exists. |
| `failure_class = 'product_defect'` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Product repair budget is exhausted; scheduler feedback/regroup or contract review is required before continuing. |
| `failure_class = 'contract_violation'` with route `run_product_repair` or `run_contract_repair` | `normal_product_repair` | `recommend` | Contract failures use the canonical typed class; the dashboard may display legacy labels like `contract`, but never persists them as `failure_class`. |
| `failure_class = 'contract_violation'` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Contract id/scope contradictions at merge or checkpoint are safety stops, not product repair or healthy progress. |
| `failure_class in ('runtime_provider', 'runtime_timeout')` with active provider/runtime retry budget | `watch_only` | `observe` | Runtime/provider noise does not mark product unhealthy. |
| `failure_class in ('runtime_provider', 'runtime_timeout')` with route `quiesce` | `watch_only` | `observe` | Runtime/provider retry budget is exhausted or unsafe; supervisor reports runtime blockage without product repair or restart guidance. |
| `failure_class = 'unknown'` with route `quiesce` | `pipeline_bug_suspected` | `stop/escalate` | Unknown stopped routes are safety stops. |
| `failure_class = 'unknown'` with route `retry_dispatch` and diagnostic budget remains | `watch_only` | `observe` | One bounded diagnostic retry may run without product mutation. Unknown never becomes healthy progress. |
| `merge_queue.status in ('queued', 'leased', 'applying', 'verifying', 'committing', 'integrated', 'checkpointing')` and no open fatal failure | `healthy_progress` | `observe` | Dashboard queue state is the live progress source. `integrated` means committed/clean lane waiting for group checkpoint coordination. |
| Bridge `dead/stopped/crashed/unreachable` or wedged logs and no active invocation/queue lease | `safe_restart_candidate` | `recommend` in read-only v1 | Requires bridge evidence in addition to typed workflow quiescence. |
| Live Codex subprocess with stable heartbeat and no output growth and no active deterministic route/queue lease | `stale_codex_invocation` | `recommend` | Existing local process evidence remains separate from control-plane state and cannot outrank an executor-owned deterministic unblock. |

Classifier priority after this slice:

1. Typed checkpoint contradiction.
2. Typed operator-required with no repair route.
3. Typed deterministic unblock with route budget remaining.
4. Safe bridge restart candidate, only when bridge evidence shows dead/stopped/crashed/unreachable and no active invocation, sandbox lease, or merge queue lease exists.
5. Stale Codex invocation, only when no typed deterministic route, active queue lease, or active dispatcher attempt is present.
6. Typed product repair.
7. Healthy typed progress.
8. Legacy fallback classifiers.

Coverage rule: every canonical `FailureClass` from Slice 07 must match exactly one
typed mapping row above or a static classifier test fails. When a class has
multiple routes, the route action and severity choose the row; legacy artifact
labels are never used to fill unmapped typed classes.

### Slack Dedupe And Suppression

Add `SupervisorDigestDedupeStore` backed by two supervisor-owned tables:
`supervisor_digest_state` for the latest aggregate per dedupe key and
`supervisor_digest_audit` for append-only send/suppress decisions. They are
audit state, not execution authority. Do not use artifacts for dedupe state;
artifacts may be projected for operator review only after the table write
succeeds.

```python
class SupervisorDigestKey(BaseModel):
    feature_id: str
    group_idx: int | None
    classification: str
    recommended_action: str
    recommended_route: str
    failure_signature_hashes: list[str]
    merge_queue_statuses: list[str]
    active_attempt_ids: list[int]

class SupervisorDigestDecision(BaseModel):
    dedupe_key: str
    should_send: bool
    reason: Literal[
        "first_seen",
        "material_change",
        "operator_requested",
        "suppressed_duplicate",
        "suppressed_within_cooldown",
        "coalesced",
    ]
    suppressed_count: int = 0
    prior_digest_id: int | None = None
```

State table contract, `supervisor_digest_state`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Dedupe state id. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `group_idx` | `INTEGER` | Nullable for feature-level digest. |
| `dedupe_key` | `TEXT NOT NULL` | Stable JSON digest over `SupervisorDigestKey`. |
| `last_snapshot_version` | `TEXT NOT NULL DEFAULT ''` | Latest snapshot version considered for this key. |
| `classification` | `TEXT NOT NULL` | Supervisor classification emitted. |
| `recommended_action` | `TEXT NOT NULL DEFAULT ''` | Bounded action label. |
| `recommended_route` | `TEXT NOT NULL DEFAULT ''` | Typed route label when present. |
| `last_sent_at` | `TIMESTAMPTZ` | Last Slack send time for this key. |
| `suppressed_count` | `INTEGER NOT NULL DEFAULT 0` | Coalesced duplicate count. |
| `last_digest_payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded display payload, no artifact bodies. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit. |

Indexes: unique `(feature_id, dedupe_key)`,
`idx_supervisor_dedupe_state_updated` on `(feature_id, updated_at DESC)`, and
`idx_supervisor_dedupe_state_group` on `(feature_id, group_idx, updated_at DESC)`.

Audit table contract, `supervisor_digest_audit`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL PRIMARY KEY` | Append-only decision id. |
| `state_id` | `BIGINT REFERENCES supervisor_digest_state(id)` | Latest-state row for this key. |
| `feature_id` | `TEXT NOT NULL REFERENCES features(id)` | Feature scope. |
| `group_idx` | `INTEGER` | Nullable for feature-level digest. |
| `dedupe_key` | `TEXT NOT NULL` | Stable JSON digest over `SupervisorDigestKey`. |
| `snapshot_version` | `TEXT NOT NULL` | Control-plane snapshot version used for the decision. |
| `should_send` | `BOOLEAN NOT NULL` | Whether Slack/outbox send was attempted. |
| `reason` | `TEXT NOT NULL` | `SupervisorDigestDecision.reason`. |
| `citation_refs` | `JSONB NOT NULL DEFAULT '[]'` | Bounded cited evidence refs. |
| `slack_channel` / `slack_thread_ts` | `TEXT NOT NULL DEFAULT ''` | Slack routing context. |
| `slack_message_ts` | `TEXT NOT NULL DEFAULT ''` | Message timestamp when sent. |
| `payload` | `JSONB NOT NULL DEFAULT '{}'` | Bounded display payload, no artifact bodies. |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Audit cursor. |

Indexes: `idx_supervisor_dedupe_audit_feature` on `(feature_id, id DESC)`,
`idx_supervisor_dedupe_audit_key` on `(feature_id, dedupe_key, id DESC)`, and
`idx_supervisor_dedupe_audit_group` on `(feature_id, group_idx, id DESC)`.

Rules:

- Key digest uses stable JSON over `SupervisorDigestKey`; evidence ids alone do
  not create a new Slack message unless classification, route, action, active
  attempt, queue status, or failure signature changes.
- Background Slack idempotency is keyed by `(feature_id, group_idx,
  dedupe_key, snapshot_version)`. Reprocessing the same snapshot may append a
  suppress/coalesce audit row, but it must not send a second background Slack
  message for the same material state.
- Suppress identical background digests for at least 30 minutes. Coalesce a
  suppressed count and send one update if the same condition persists past the
  cooldown.
- Never suppress direct operator answers, first `stop/escalate` for a new
  failure signature, or first `operator_required` for a new typed route.
- Do suppress repeated restart recommendations while an active deterministic
  unblock, active agent invocation, or leased merge queue item is visible.
- Record every send/suppress decision with snapshot version, dedupe key,
  citations, Slack channel/thread, message timestamp if sent, and suppression
  reason. These records may be written by the supervisor under the audit
  exception policy.

### Read-Only And Audit Exception Policy

Read-only supervisor means it may not mutate executor/control-plane authority:
no product file edits, no execution artifact projection writes, no checkpoints,
no merge queue rows, no typed failures, no retry budgets, no task contracts, no
workspace snapshots, and no attempt state transitions.

Service wiring must make that contract mechanical. In default v1 read-only mode,
supervisor code receives only read/query handles plus supervisor-owned audit,
dedupe, and display outbox writers. Any MCP tool or action policy path that
would call an execution-authority writer is absent or denied before runtime
parameters are inspected.

Allowed writes:

- Append-only supervisor observation, decision, digest, and action audit records.
- Slack dedupe/suppression records.
- Public dashboard display outbox events derived from typed snapshots.
- Existing guarded bridge actions only when `SupervisorMode.GUARDED` is
  explicitly configured outside this v1 read-only default. Those actions are
  limited to bridge/process control, must write planned/blocked/completed audit
  records, and still cannot mutate typed execution state or product files.

Denied writes fail closed and produce a blocked action audit row rather than a
best-effort mutation.

### Bounded-Read Constraints

- Snapshot queries use keyed indexes and per-list limits from `SnapshotBudget`.
  No unbounded `get_events`, no broad artifact `SELECT value`, and no full
  dirty path, stdout/stderr, prompt, or verifier body hydration.
- Implement list reads as keyset or indexed bounded queries with `LIMIT cap + 1`
  so truncation is explicit. Do not use unbounded `OFFSET`, cross-feature scans,
  or post-query slicing as the enforcement mechanism.
- Apply a statement/query timeout at the store boundary. Timeout handling returns
  degraded partial state plus `degradation_reasons`; it never retries by dropping
  caps or reading raw artifact/event bodies.
- All list fields include truncation metadata via `truncated`, `omitted_counts`,
  and cursors. Dashboard and supervisor must display degraded/partial state
  when truncation occurs.
- Default query caps: attempts 20, failures 40, merge items 40, retry budgets
  40, gates 40, workspace snapshots 20, evidence refs 80, events 100 summaries,
  artifacts 200 summaries, artifact detail slices 20,000 chars, response budget
  250 KB, query timeout 1.5 seconds.
- SQL fallback in MCP remains feature-scoped, read-only, capped, and timeout
  bounded. It is not used for normal status when typed snapshot succeeds.

## Refactoring Steps

1. Add `get_control_plane_snapshot` and
   `get_control_plane_snapshot_version` to the Slice 1 store in the same PR as
   the additive schema/index migrations they read.
2. Add Pydantic models above and serialize snapshots through stable JSON so
   dashboard, MCP, Slack, and tests share one contract.
3. Update `SupervisorObservation` to carry `control_plane:
   ControlPlaneSnapshot | None` and `evidence_mode:
   Literal["typed", "legacy_fallback", "mixed"]`.
4. Update `collect_evidence` and `SupervisorEvidenceMcpService.get_current_snapshot`
   to call the typed snapshot first. Legacy artifact/event summaries are used
   only when typed tables are absent or the typed query degrades.
5. Replace artifact-body classifier inputs with typed failure/route mapping.
   Keep existing classifier methods as compatibility fallback, and gate them
   behind `evidence_mode != "typed"` or missing typed fields.
6. Add dashboard `/api/feature/{feature_id}/control-plane`, wire existing
   `/api/feature/{feature_id}` ETag/cache invalidation to snapshot version, and
   render typed panels without hydrating artifact bodies.
7. Add `SupervisorDigestDedupeStore` and route all background Slack digests
   through send/suppress decisions before calling the Slack client.
8. Enforce read-only policy in `ActionPolicy` and MCP service construction:
   supervisor audit/dedupe/outbox writes are allowed, execution authority writes
   are denied.
9. Update public dashboard outbox projection to include bounded
   `control_plane.snapshot_changed` display events.
10. Add deployment/startup assertions that the snapshot store, dashboard route,
    classifier mapping, MCP snapshot path, Slack dedupe store, and public outbox
    projection are all present when typed control-plane mode is enabled.
11. Land tests, migrations, dashboard, supervisor, and Slack behavior
    atomically. Do not merge a state where only one consumer has switched to
    typed snapshots.

## Persistence And Artifact Compatibility

- Supervisor can fall back to legacy artifact/event summaries for old features.
- Supervisor audit/action artifacts are allowed only as bounded observational records; they are not execution authority.
- Typed snapshot APIs must not return artifact bodies.
- Dashboard detail panes fetch bounded slices by artifact id.
- Slack messages cite typed evidence ids or legacy artifact/event ids.
- Snapshot APIs return typed ids, artifact/event references, digests, counts,
  and previews only. Full bodies remain behind existing bounded slice/detail
  tools.
- Legacy `dag-*` artifacts remain compatible projections, but dashboard and
  supervisor prefer typed rows whenever both exist. If typed and legacy evidence
  conflict, typed route/checkpoint/merge state wins and the conflict is surfaced
  as degraded evidence.
- Supervisor digest, observation, and dedupe audit records include
  `snapshot_version`; replay can explain exactly what state produced a Slack
  send or suppression decision.
- Additive supervisor audit/dedupe tables or artifact keys are retained on
  rollback. They are explanatory records and must not be consumed as execution
  authority.

## Edge Cases And Failure Handling

- Typed state unavailable: supervisor returns degraded legacy summary, not an agent-authored assessment.
- Active deterministic unblock exists: supervisor must not recommend restart unless bridge is actually dead and no active invocation exists.
- Repeated same digest: suppress or coalesce Slack messages.
- Snapshot query exceeds budget: return partial bounded snapshot with degraded flag.
- Typed query timeout: return `source="mixed"`, `degraded=true`, and include the
  legacy bounded summary if available. Do not retry with an unbounded query.
- Typed/legacy mismatch: classify from typed rows, add mismatch to
  `degradation_reasons`, and cite both evidence refs for audit.
- Missing retry budget row: treat route as degraded and do not recommend broad
  repair until the failure router supplies a budget decision.
- Deterministic class with `operator_required=true` but an active newer repair
  route: prefer the newer deterministic route, mark the older operator signal as
  superseded in facts/degradation context, and do not escalate to the operator.
- Merge queue lease expired: dashboard marks item stale; supervisor classifies
  as watch-only unless a typed failure or bridge/process evidence indicates an
  actionable condition.
- Open `operator_required` typed failure with a newer deterministic repair route:
  classify deterministic unblock, not operator-required.
- Product verifier failure after a deterministic repair: classify product repair
  only when the product failure is newer than the repair evidence and belongs to
  the active group/snapshot version.
- Public dashboard async delivery failure after enqueue commit: log and
  continue. Private dashboard and supervisor still read the typed snapshot
  directly. Public outbox enqueue failure while the outbox is configured is not
  ignorable: it fails the projection transaction so typed snapshot, cursor/ETag
  compatibility state, and outbox row cannot diverge.
- Dedupe store write failure: fail open for operator-requested replies, fail
  quiet for background duplicate candidates, and emit a local warning/audit error
  if possible.

## Tests

Unit tests:

- `ControlPlaneSnapshotQuery` enforces budget caps and rejects negative limits.
- Snapshot version changes when typed max ids/updated times change and does not
  change when only artifact body text changes.
- Typed snapshot serialization contains no `value`, `content`, raw prompt,
  stdout/stderr, or full dirty path body fields.
- Typed checkpoint contradiction maps to `pipeline_bug_suspected` before all
  other classes.
- Typed deterministic unblock maps ahead of safe restart when an active repair
  attempt or merge lease exists.
- Worktree alias and ACL failures never classify as operator-required when
  `route` is `run_canonicalization_repair` and budget remains.
- Commit hygiene failure maps to deterministic unblock and never product repair.
- Runtime provider failure with retry budget maps to watch-only/observe.
- Product defect remains normal product repair even with older stale projection
  or commit failure artifacts.
- Operator-required maps to stop/escalate only when typed route is
  `operator_required` or budget is exhausted.
- `SupervisorDigestKey` is stable across evidence id churn and changes when
  classification, route, action, signature, active attempt, or queue status
  changes.
- Repeated identical background Slack digest is suppressed, coalesced, and later
  emitted after cooldown.
- Direct operator Slack questions bypass suppression but still record dedupe
  audit.
- Read-only action policy blocks execution/control-plane mutation and writes a
  blocked audit record.
- Guarded bridge action, when explicitly configured, writes planned/completed or
  failed audit records and does not touch typed execution tables.
- Supervisor service construction in read-only mode exposes no write-capable
  execution store handles and rejects MCP/action attempts to mutate authority
  rows before they reach the store.

Store/query tests:

- Snapshot query returns only rows for the requested feature and optional group.
- Active attempts are capped, sorted by recency, and include cursor/omitted
  counts when truncated.
- Latest failures use open/routed/retrying rows first and include resolved rows
  only when needed for current route explanation.
- Workspace snapshot summaries include dirty/forbidden counts and samples, not
  full lists.
- Merge queue summaries expose lease/version/status/result commit without
  reading artifact bodies.
- Gate summaries cite evidence ids and reject checkpoint display when gate
  evidence is missing.
- Query timeout returns degraded partial state and never performs unbounded
  fallback reads.
- Legacy feature fallback still works with only `list_record_summaries` and
  `list_event_summaries`.

Dashboard tests:

- `/api/feature/{feature_id}/control-plane` returns typed snapshot with ETag
  derived from snapshot version.
- Existing `/api/feature/{feature_id}` embeds compact `control_plane` state and
  invalidates cache on typed-only updates.
- Dashboard panels render active attempts, workspace/sandbox state, merge queue,
  typed failures, retry budgets, gates, checkpoints, and degraded legacy state.
- Detail panes fetch bounded slices by `EvidenceRef` id and never receive raw
  bodies from the snapshot endpoint.
- Public dashboard outbox event payload is bounded and contains no private
  evidence body.
- Public dashboard outbox projection is idempotent for the same
  `(feature_id, snapshot_version)` and emits a new event only when the typed
  snapshot version changes.

Integration/regression tests:

- Supervisor and dashboard show the same route for commit hygiene, alias, ACL,
  stale projection, product defect, provider retry, merge conflict, and
  checkpoint contradiction fixtures.
- Active deterministic unblock suppresses restart recommendations while bridge
  is running or an invocation/queue lease is active.
- Active deterministic unblock also suppresses `stale_codex_invocation` as the
  headline class; stale process evidence may appear only as secondary context.
- Deterministic workflow classes with retry/repair budget remaining never
  produce `operator_required`, manual-fix copy, manual rebase, or bridge restart
  recommendations.
- Dead bridge with no active invocation produces safe restart recommendation in
  read-only mode, not an automatic mutation.
- Typed/legacy disagreement is displayed as degraded and classified from typed
  route authority.
- Atomic feature test seeds typed rows, legacy projections, dashboard request,
  MCP snapshot request, classifier digest, and Slack dedupe in one scenario to
  prove all consumers use the same snapshot version.
- Startup/deployment guard test fails if typed mode is enabled without the
  dashboard route, MCP typed snapshot path, classifier mapping, dedupe tables,
  public outbox projection, or read-only policy enforcement.

## Acceptance Criteria

- Supervisor and dashboard agree with executor route decisions.
- Supervisor does not ask operator to manually fix ACL, worktree alias, stale projection, or commit-only classes.
- Dashboard shows queue/sandbox/gate state without broad artifact hydration.
- Slack noise is reduced through signature-based dedupe.
- Supervisor read-only means no executor/control-plane mutation; bounded audit records and explicitly configured guarded bridge actions remain documented exceptions.
- Snapshot APIs are typed, bounded, versioned, and shared by dashboard, MCP, and
  Slack digest generation.
- Legacy fallback is visibly degraded and never outranks typed route/checkpoint
  state.
- Every background Slack send/suppress decision is auditable by snapshot version,
  dedupe key, reason, and cited evidence refs.
- The feature lands atomically: schema, snapshot store, dashboard, supervisor,
  Slack dedupe, public outbox, and tests merge together.

## Rollout/Rollback Notes

No phased production rollout. Build and merge this as one atomic feature branch
with additive migrations and the full test matrix. Production should never run a
new dashboard against legacy supervisor evidence, or a typed supervisor against
an artifact-inference dashboard, except during local development.

Do not introduce a production percentage rollout, long-lived compatibility flag,
or consumer-by-consumer enablement switch. A local development flag may exist
only to exercise legacy fallback tests; production typed mode requires the
startup assertions above to pass before serving dashboard, MCP, supervisor, or
Slack traffic.

Rollback is a whole-feature rollback: redeploy the previous application version
and leave additive typed/audit/dedupe tables in place. Existing legacy
artifact/event summaries continue to serve old code. Do not delete typed audit
or dedupe rows during rollback; they explain Slack/dashboard behavior that may
already have been shown to operators.

## Cross-Slice Dependencies

- Slice 1 provides typed summary storage.
- Slice 7 provides typed failure route decisions.
- Slice 8 provides merge queue state.
- Slice 9 provides scheduler feedback.
- Slice 2 provides workspace snapshot rows and bounded dirty/forbidden samples.
- Slice 6 provides gate evidence ids and approval status.
- Public dashboard outbox remains best-effort display delivery, not execution
  authority.
