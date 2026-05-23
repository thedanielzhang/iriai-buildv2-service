# 07. Typed Failure Router

## Objective

Centralize failure classification and retry decisions. The router decides whether
to retry, repair, canonicalize, clean commit hygiene, handle merge conflict,
pause, or escalate to operator-required. It replaces scattered retry and repair
branching with typed, budgeted decisions.

The first production landing is atomic with the surrounding execution-control
plane slices. There is no advisory-only or partial production path for new
attempts: dispatcher, sandbox runner, gates, failure router, merge queue,
journal projections, supervisor read models, and rollback guards must all be
available before this router becomes authoritative.

## Current Code Citations

- Direct route classifier definitions: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:890).
- Direct route classification in verify loop: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3127).
- Repeated deterministic route guard: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3145).
- Operator-required route behavior: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3194).
- Commit failure artifact handling: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4174).
- Supervisor failure priority order: [SupervisorClassifier.classify](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:24).
- Supervisor deterministic unblock classifier: [classifier.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/supervisor/classifier.py:254).

## Current Failure Mode From `8ac124d6`

Commit-only failures, alias drift, ACL gaps, stale projection, and runtime
provider errors sometimes entered broad model repair or RCA. That burned retries
without changing the real blocking condition.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/failure_router.py`.

```python
FailureSeverity = Literal["info", "warning", "error", "fatal"]

FailureClass = Literal[
    "product_defect",
    "contract_compile",
    "contract_violation",
    "stale_projection",
    "worktree_alias",
    "acl_workability",
    "sandbox_allocation",
    "sandbox_binding",
    "sandbox_isolation",
    "sandbox_capture",
    "sandbox_cleanup",
    "commit_hygiene",
    "merge_conflict",
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "dispatcher_internal",
    "verifier_provider",
    "verifier_context",
    "checkpoint_contradiction",
    "regroup_invalid",
    "evidence_corruption",
    "resource_exhausted",
    "operator_required",
    "unknown",
]

FailureType = Literal[
    "semantic_verifier_rejected",
    "required_path_missing",
    "contract_invalid_path",
    "contract_scope_conflict",
    "contract_missing_dependency",
    "contract_same_wave_dependency",
    "outside_allowed_paths",
    "forbidden_path_touched",
    "read_only_path_touched",
    "contract_id_mismatch",
    "alias_points_to_noncanonical_root",
    "alias_only_canonical_missing",
    "alias_canonical_divergent",
    "unwritable_runtime_path",
    "sandbox_clone_failed",
    "sandbox_disk_quota",
    "sandbox_base_snapshot_unavailable",
    "runtime_workspace_binding_failed",
    "canonical_path_exposed_to_writer",
    "path_escape_detected",
    "patch_capture_failed",
    "sandbox_index_corrupt",
    "cleanup_failed",
    "commit_hook_failed",
    "dirty_after_commit",
    "stale_base_commit",
    "rebase_conflict",
    "patch_apply_conflict",
    "provider_internal_error",
    "provider_rate_limited",
    "provider_transport_error",
    "process_failed",
    "watchdog_timeout",
    "runtime_cancelled",
    "prompt_too_large",
    "context_materialization_failed",
    "context_permission_denied",
    "malformed_structured_output",
    "idempotency_conflict",
    "verifier_context_stale",
    "workspace_snapshot_stale",
    "verifier_provider_timeout",
    "verifier_provider_crash",
    "verifier_parse_failed",
    "checkpoint_after_failed_gate",
    "regroup_dependency_cycle",
    "regroup_write_conflict",
    "artifact_hash_mismatch",
    "payload_digest_mismatch",
    "projection_body_conflict",
    "db_resource_exhausted",
    "disk_resource_exhausted",
    "process_resource_exhausted",
    "provider_quota_exhausted",
    "operator_clearance_required",
    "unclassified",
]

RouteAction = Literal[
    "retry_dispatch",
    "run_product_repair",
    "run_contract_repair",
    "run_canonicalization_repair",
    "run_workspace_repair",
    "run_commit_hygiene_repair",
    "retry_verifier",
    "retry_merge",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
    "quiesce",
    "operator_required",
]

class RouteDecision(BaseModel):
    failure_id: int
    route_decision_id: int | None
    action: RouteAction
    budget_remaining: int
    budget_exhausted: bool = False
    reason: str
    required_evidence_ids: list[int]
    signature_hash: str
    idempotency_key: str
    repair_scope: dict[str, object] = Field(default_factory=dict)

class FailureObservation(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int | None = None
    task_id: str | None = None
    attempt_id: int | None = None
    source: Literal[
        "dispatcher",
        "workspace_authority",
        "contract",
        "sandbox",
        "verification_graph",
        "merge_queue",
        "regroup",
        "journal",
        "artifact_store",
    ]
    failure_class: FailureClass
    failure_type: FailureType
    severity: FailureSeverity = "error"
    deterministic: bool
    retryable: bool
    operator_required: bool = False
    evidence_ids: list[int]
    payload: dict[str, object]

class FailureRouter:
    def record(self, observation: FailureObservation) -> int: ...
    def decide(self, failure_id: int) -> RouteDecision: ...
    def mark_route_started(self, decision: RouteDecision) -> RouteDecision: ...
    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None: ...

RepairKind = Literal[
    "product",
    "contract",
    "canonicalization",
    "workspace",
    "commit_hygiene",
    "sandbox_cleanup",
]

RepairMutation = Literal[
    "sandbox_product_patch",
    "contract_recompile",
    "workspace_metadata",
    "workspace_acl",
    "projection_refresh",
    "commit_hygiene_patch",
    "sandbox_cleanup",
]

RepairAttemptStatus = Literal[
    "requested",
    "sandbox_allocating",
    "dispatching",
    "capturing",
    "validating",
    "queued_for_merge",
    "metadata_applied",
    "succeeded",
    "failed",
    "quiesced",
]

class RepairTarget(BaseModel):
    repo_id: str | None = None
    path: str | None = None
    contract_id: int | None = None
    evidence_id: int | None = None
    failure_id: int | None = None
    reason: str

class RepairRequest(BaseModel):
    id: int | None
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_id: str | None = None
    route_decision_id: int
    failure_id: int
    action: RouteAction
    repair_kind: RepairKind
    allowed_mutations: list[RepairMutation]
    target_repo_ids: list[str]
    target_paths: list[str]
    target_contract_ids: list[int]
    required_evidence_ids: list[int]
    targets: list[RepairTarget]
    sandbox_mode: Literal["none", "task", "repair", "canonicalization"]
    enqueue_strategy: Literal["none", "merge_queue", "metadata_only", "cleanup_only"]
    required_gate_ids: list[str]
    prompt_constraints: list[str]
    budget_key: str
    idempotency_key: str
    input_digest: str

class RepairOutcome(BaseModel):
    id: int | None
    repair_request_id: int
    status: RepairAttemptStatus
    attempt_id: int | None = None
    sandbox_lease_id: int | None = None
    dispatcher_attempt_id: int | None = None
    patch_summary_ids: list[int] = Field(default_factory=list)
    contract_verdict_ids: list[int] = Field(default_factory=list)
    workspace_snapshot_ids: list[int] = Field(default_factory=list)
    merge_queue_item_ids: list[int] = Field(default_factory=list)
    projected_artifact_ids: list[int] = Field(default_factory=list)
    resolved_failure_id: int | None = None
    produced_failure_id: int | None = None
    summary: str
    idempotency_key: str

RetryKind = Literal[
    "dispatch",
    "verifier",
    "merge",
    "sandbox_capture",
    "sandbox_cleanup",
]

RetryRequestStatus = Literal["requested", "started", "succeeded", "failed", "quiesced"]

class RetryRequest(BaseModel):
    id: int | None
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_id: str | None = None
    route_decision_id: int
    failure_id: int
    action: RouteAction
    retry_kind: RetryKind
    attempt_kind: Literal["task", "verify", "merge", "repair"]
    preserve_contract_ids: list[int]
    preserve_gate_ids: list[str]
    preserve_sandbox_lease_id: int | None = None
    preserve_merge_queue_item_id: int | None = None
    required_evidence_ids: list[int]
    reset_context: bool = False
    allocate_new_sandbox: bool = False
    idempotency_key: str
    input_digest: str

class RetryOutcome(BaseModel):
    id: int | None
    retry_request_id: int
    status: RetryRequestStatus
    spawned_attempt_id: int | None = None
    spawned_evidence_ids: list[int] = Field(default_factory=list)
    spawned_merge_queue_item_ids: list[int] = Field(default_factory=list)
    resolved_failure_id: int | None = None
    produced_failure_id: int | None = None
    summary: str
    idempotency_key: str

RouteRequest = RepairRequest | RetryRequest

class RouteExecutor:
    def build_route_request(self, decision: RouteDecision) -> RouteRequest: ...
    def build_repair_request(self, decision: RouteDecision) -> RepairRequest: ...
    def build_retry_request(self, decision: RouteDecision) -> RetryRequest: ...
    async def execute_repair(self, request: RepairRequest) -> RepairOutcome: ...
    async def execute_retry(self, request: RetryRequest) -> RetryOutcome: ...
```

Hard routing invariants:

- A persisted `failure_class` must be one of the canonical enum values above.
  Legacy classifier labels, direct-route strings, artifact keys, and supervisor
  categories are compatibility text only.
- `FailureTypePolicy` derives `retryable`, `deterministic`, and
  `operator_required`; producers may propose evidence but cannot override those
  booleans at insert time.
- `retryable=true` is necessary but never sufficient for a retry. The concrete
  `(failure_class, failure_type)` route row, remaining budget, and required
  evidence must all permit the `retry_*` action.
- `run_product_repair` is allowed only for `product_defect` and scoped
  `contract_violation` rows that already name fixed contract ids and offending
  product paths. It is forbidden for commit hygiene, alias, ACL, stale
  projection, sandbox, runtime provider/context, verifier provider/context,
  checkpoint, regroup, evidence corruption, resource, operator, and unknown
  classes.
- Workflow-class repair outside scoped `contract_violation` cleanup is metadata,
  contract, canonicalization, workspace, commit-hygiene, or cleanup repair only.
  It cannot be upgraded to broad product RCA because a previous repair failed, a
  legacy artifact asks for expanded verify, or a supervisor classifier
  recommends an unblock.
- `retry_*` actions rerun a bounded operation with preserved contracts, gates,
  route-decision evidence, and sandbox/queue lineage. They do not create a model
  prompt whose goal is to reinterpret product requirements.

The supervisor is not a `FailureObservation.source`. It may record
`supervisor_observations` and Slack/dashboard summaries, but typed failure
authority belongs to executor-owned producers: dispatcher, workspace authority,
contracts, sandbox, verification graph, merge queue, regroup scheduler, journal,
and artifact store. `journal` is reserved for projection/idempotency/recovery
conflicts detected while writing typed rows. `artifact_store` is reserved for
hash, spill, or projection-body corruption detected by bounded artifact APIs.
When the supervisor detects a possible new blocker, it must cite evidence and
recommend a bounded executor recheck; it must not call `FailureRouter.record`.

### Router-To-Repair Execution Contract

`RouteAction` is not merely a label. Every route that begins with `run_` or
`retry_` must produce a concrete `RepairRequest` or `RetryRequest` before any
agent, git mutation, verifier call, or queue transition starts. `RepairRequest`
is for work that may create new evidence or patches; `RetryRequest` is for
rerunning an existing dispatcher, verifier, merge, sandbox-capture, or cleanup
operation while preserving the original contracts and route-decision evidence.

Repair request construction rules:

1. `FailureRouter.decide` returns the action and bounded `repair_scope`.
   `mark_route_started` reserves budget and writes
   `evidence_nodes(kind='failure_route_decision')`. `RouteExecutor` may build a
   request only from a started decision, never from an unreserved decision.
2. `run_product_repair` creates `RepairKind="product"`,
   `allowed_mutations=["sandbox_product_patch"]`,
   `sandbox_mode="repair"`, and `enqueue_strategy="merge_queue"`. It must carry
   the original contract ids, gate ids, patch/verdict evidence, target paths, and
   non-goals into the repair prompt. The repair cannot broaden contracts.
3. `run_canonicalization_repair` creates `RepairKind="canonicalization"`. Alias
   metadata-only cases use `allowed_mutations=["projection_refresh"]`,
   `sandbox_mode="none"`, and `enqueue_strategy="metadata_only"`. Alias-only or
   divergent product content uses `allowed_mutations=["sandbox_product_patch"]`,
   `sandbox_mode="canonicalization"`, and `enqueue_strategy="merge_queue"`.
4. `run_workspace_repair` creates `RepairKind="workspace"`. ACL normalization
   uses `allowed_mutations=["workspace_acl"]`, no sandbox, and metadata evidence
   from workspace authority. Snapshot/projection refresh uses
   `["workspace_metadata", "projection_refresh"]`. It cannot edit product files.
5. `run_commit_hygiene_repair` creates `RepairKind="commit_hygiene"`,
   `allowed_mutations=["commit_hygiene_patch"]`, and `enqueue_strategy="merge_queue"`.
   The request includes queue item id, hook/status evidence, staged path set, and
   no-dirty proof requirements. It must not run semantic product RCA unless a
   later gate records a product failure.
6. `run_contract_repair` creates `RepairKind="contract"` and
   `allowed_mutations=["contract_recompile"]`. It may recompile contracts from
   immutable DAG/workspace metadata, apply canonical path normalization already
   proven by workspace authority, or emit scheduler/regroup feedback. It must not
   invent new task scope, edit the root DAG, or mutate product files. If a valid
   contract cannot be produced from existing immutable inputs, the outcome is
   `quiesced` with a typed `contract_compile` or `regroup_invalid` failure.
7. `retry_dispatch`, `retry_verifier`, `retry_merge`, and
   `retry_sandbox_capture` create `RetryRequest`, not `RepairRequest`.
   `retry_dispatch` uses `retry_kind="dispatch"` and may allocate a new sandbox
   only when the original failure class allows it; it preserves contract ids,
   gate ids, prompt constraints, and non-goals, and it cannot broaden scope to
   solve a workflow-class failure as a product defect. `retry_verifier` uses
   `retry_kind="verifier"` with `reset_context=True` only for stale context
   routes and preserves the verifier graph/gate set. `retry_merge` uses
   `retry_kind="merge"` only when the preserved source queue item is terminal
   `failed`, has no `result_commit`, and has no integrated, checkpointing, or
   done replacement chain. It preserves that failed source queue item id in
   `preserve_merge_queue_item_id` and creates a replacement queue item linked
   through the real `merge_queue_items.retry_of_queue_item_id` column; any JSON
   payload mirror is display-only. The failed source row remains terminal
   evidence and is never mutated back to active. The replacement row must carry
   the same feature id, DAG sha, group id, task coverage, contract coverage, gate
   requirements, queue lane, and route-decision evidence. If the source row is
   `integrated`, `checkpointing`, `done`, `poisoned`, or already superseded by a
   non-cancelled replacement chain, the retry request is rejected as
   `checkpoint_contradiction/checkpoint_after_failed_gate` or as the existing
   terminal route, not enqueued. `retry_sandbox_capture` uses
   `retry_kind="sandbox_capture"` and preserves the retained sandbox lease id.
8. `run_sandbox_cleanup` creates a `RetryRequest` with
   `retry_kind="sandbox_cleanup"` unless cleanup needs a long-running external
   worker, in which case it may create `RepairKind="sandbox_cleanup"` with
   `allowed_mutations=["sandbox_cleanup"]`. It never creates a product repair
   prompt.

Repair and retry lifecycle:

1. Persist the route request before any rerun or repair side effect:
   `RepairRequest` writes an `execution_attempts` row with
   `attempt_kind='repair'` plus `evidence_nodes(kind='repair_request')`;
   `RetryRequest` writes `evidence_nodes(kind='retry_request')` and, when the
   retry spawns a new runtime/verifier/merge attempt or replacement queue item,
   the corresponding `execution_attempts` row or `merge_queue_items` row in the
   same journal transaction. The request evidence
   body stores route decision id, preserved contract/gate/sandbox/queue ids,
   retry kind or repair kind, input digest, and idempotency key.
2. If `sandbox_mode != "none"`, allocate or reuse a sandbox through Slice 04.
   Runtime cwd is the sandbox binding; canonical repos are blocked.
3. Dispatch repair or retry work only after the persisted request evidence exists
   and workspace authority plus contract checks approve the target set.
4. Capture repair output as sandbox patch summaries or metadata evidence. Product
   file changes must pass Slice 03 contract verdicts and Slice 06 gates.
5. Enqueue mergeable repair patches through Slice 08. Metadata-only repairs write
   projections/snapshots through the owning service and then trigger the requested
   verifier/dispatcher retry.
6. Persist `RepairOutcome` as `evidence_nodes(kind='repair_outcome')` or
   `RetryOutcome` as `evidence_nodes(kind='retry_outcome')`; for `retry_merge`,
   `spawned_merge_queue_item_ids` must include the replacement row id before the
   source route is marked finished. Then call `mark_route_finished`, and link
   `resolved_failure_id` or `produced_failure_id`. A failed repair never silently
   falls back to broad RCA; it records the new typed failure and asks the router
   for the next decision.
7. On resume, `RouteExecutor` first loads `repair_request` or `retry_request`
   evidence by idempotency key. If the request exists and the side effect has not
   started, it resumes from the request. If the spawned attempt/evidence exists,
   or a replacement queue item exists with the real
   `merge_queue_items.retry_of_queue_item_id` column pointing at the preserved
   source row, it resumes from those ids. A duplicate retry request with a
   different input digest is an `IdempotencyConflict` and quiesces.

Acceptance criteria for repair execution:

- Every repairable route has a deterministic `RepairRequest` builder test.
- Every retry route has a deterministic `RetryRequest` builder test that preserves
  contract ids, gate ids, sandbox/queue ids, and route-decision evidence.
- Every `RepairRequest` names allowed mutation classes, target paths/contracts,
  required evidence ids, enqueue strategy, and sandbox policy.
- Product/canonicalization/commit-hygiene repairs that touch files can mutate
  canonical repos only through the merge queue.
- Workspace and projection repairs cannot mutate product files.
- Contract repair cannot edit root DAG artifacts or widen contracts from model
  output.
- Crash/resume after request creation, sandbox dispatch, patch capture,
  metadata-only apply, merge enqueue, or outcome write returns the same typed ids
  from idempotency keys.

### Failure Taxonomy

`failure_class` is the stable routing class. `failure_type` is the narrow
signature discriminator used for budgets, dashboards, and compatibility
projection. The router must not infer product defects from deterministic
pipeline failures just because they occur during implementation.

Producers may use local observation names internally, but they must map to this
canonical `(failure_class, failure_type)` pair before inserting `typed_failures`.
Names such as `contract`, `workspace_authority`, `verify`,
`checkpoint_safety`, and `runtime_malformed_output` are allowed only as source
labels or legacy display text. The journal rejects them as persisted
`typed_failures.failure_class` values, and supervisor/dashboard mappings read
from the canonical enum.

The persisted booleans are not producer choices. The router package owns a
`FailureTypePolicy` table keyed by `(failure_class, failure_type)`. The grouped
table below is shorthand for pairs that share defaults; specific route rows
remain the source of truth when the same `failure_type` appears under different
classes, such as `runtime_context/context_materialization_failed` and
`verifier_context/context_materialization_failed`. Producer tests assert every
emitted `(failure_class, failure_type)` pair appears in the concrete policy
table.

Taxonomy compatibility rules:

- A `failure_type` may be reused under multiple classes only when every
  supported pair has an explicit route row, budget row, and policy row. There is
  no fallback from a known type under an unknown class.
- `contract_compile` describes invalid or impossible contract material before
  runtime work. `contract_violation` describes produced output that violated an
  otherwise valid contract. The router must not swap these classes to find a
  cheaper route.
- `stale_projection` is for stale or inconsistent derived reads. It is not a
  semantic product failure until a verifier rerun against fresh typed evidence
  records `product_defect`.
- `worktree_alias` and `acl_workability` remain workflow-repairable unless
  workspace authority explicitly records `operator_clearance_required`.
- `unknown/unclassified` is intentionally non-repairing. It cannot be converted
  into product repair by legacy direct-route compatibility.

| Failure type(s) | retryable | deterministic | operator_required |
| --- | --- | --- | --- |
| `semantic_verifier_rejected`, `required_path_missing` | true | false | false |
| `contract_invalid_path`, `contract_scope_conflict`, `contract_missing_dependency`, `contract_same_wave_dependency`, `outside_allowed_paths`, `forbidden_path_touched`, `read_only_path_touched`, `contract_id_mismatch` | false | true | false |
| `alias_points_to_noncanonical_root`, `alias_only_canonical_missing`, `alias_canonical_divergent`, `unwritable_runtime_path`, `workspace_snapshot_stale` | true | true | false |
| `sandbox_clone_failed`, `sandbox_disk_quota`, `sandbox_base_snapshot_unavailable` | true | false | false |
| `runtime_workspace_binding_failed`, `canonical_path_exposed_to_writer`, `path_escape_detected`, `sandbox_index_corrupt` | false | true | false |
| `patch_capture_failed`, `cleanup_failed` | true | false | false |
| `commit_hook_failed`, `dirty_after_commit` | true | true | false |
| `stale_base_commit`, `rebase_conflict`, `patch_apply_conflict` | true | false | false |
| `provider_internal_error`, `provider_rate_limited`, `provider_transport_error`, `process_failed`, `watchdog_timeout`, `verifier_provider_timeout`, `verifier_provider_crash`, `verifier_parse_failed` | true | false | false |
| `runtime_cancelled` | false | false | false |
| `prompt_too_large`, `context_materialization_failed`, `malformed_structured_output`, `verifier_context_stale` | true | true | false |
| `context_permission_denied`, `operator_clearance_required` | false | true | true |
| `idempotency_conflict`, `checkpoint_after_failed_gate`, `regroup_dependency_cycle`, `regroup_write_conflict`, `artifact_hash_mismatch`, `payload_digest_mismatch`, `projection_body_conflict` | false | true | false |
| `db_resource_exhausted`, `disk_resource_exhausted`, `process_resource_exhausted`, `provider_quota_exhausted` | true | false | false |
| `unclassified` | false | false | false |

| Failure class | Typical source | Examples | Deterministic | Default route |
| --- | --- | --- | --- | --- |
| `product_defect` | Verification graph, contract presence checks | Semantic verifier rejection, required canonical deliverable missing after patch apply | No | `run_product_repair` |
| `contract_compile` | Contract compiler | Invalid path, conflicting task scopes, impossible generated-output rule | Yes | `run_contract_repair` or `quiesce` |
| `contract_violation` | Contract validation, sandbox patch capture, merge admission | Outside allowed paths, forbidden path touched, read-only path modified | Yes | `run_contract_repair` or scoped `run_product_repair` |
| `stale_projection` | Workspace authority, gates, verifier context | Artifact/latest-key drift, generated snapshot stale, outdated verifier material | Yes | `retry_verifier` after refresh |
| `worktree_alias` | Workspace authority, contract compiler, legacy artifact normalization | Alias path points outside canonical repo or to old mirror | Yes | `run_canonicalization_repair` |
| `acl_workability` | Workspace authority, runtime preflight | Runtime user cannot write allowed paths, bridge user can | Yes | `run_workspace_repair` |
| `sandbox_allocation` | Sandbox runner | Clone failed, disk quota, base snapshot unavailable | No | `retry_dispatch` after new lease |
| `sandbox_binding` | Sandbox runner, dispatcher | Runtime binding missing cwd, unsafe Codex mode requested, manifest invalid | Yes | `quiesce` unless metadata can be repaired |
| `sandbox_isolation` | Sandbox runner, patch capture | Canonical path exposed to writer, path traversal, symlink escape | Yes/fatal | `quiesce` |
| `sandbox_capture` | Sandbox runner | Patch summary failed, diff hash unavailable, repo index corrupted in sandbox | By type policy | `retry_sandbox_capture` |
| `sandbox_cleanup` | Sandbox runner recovery | Release/delete failed after capture | No | `run_sandbox_cleanup` |
| `commit_hygiene` | Merge queue, compatibility commit helper | Hook failure, dirty canonical state after attempted commit | Yes | `run_commit_hygiene_repair` |
| `merge_conflict` | Merge queue | Stale base, rebase conflict, patch no longer applies | No | `retry_merge` |
| `runtime_provider` | Dispatcher/runtime adapter | Provider internal error, transport failure, rate limit with request id | No | `retry_dispatch` |
| `runtime_timeout` | Dispatcher/runtime adapter | Watchdog timeout, missing heartbeat, runtime stall | No | `retry_dispatch` |
| `runtime_cancelled` | Dispatcher/runtime adapter | Operator/workflow cancellation before terminal runtime output | No | `quiesce` or resume according to cancellation owner |
| `runtime_context` | Dispatcher/context builder | Prompt too large, context materialization failed, selected evidence unavailable | Yes | `retry_dispatch` for rebuildable context, otherwise `quiesce` by specific route row |
| `runtime_structured_output` | Dispatcher | Missing structured fields, schema parse failure, tool output contradiction | Yes | `retry_dispatch` |
| `dispatcher_internal` | Dispatcher/journal boundary | Idempotency conflict, impossible state transition, unexpected local exception | Yes/fatal | `quiesce` |
| `verifier_provider` | Verification graph | Verifier runtime/provider crashed or timed out | No | `retry_verifier` |
| `verifier_context` | Verification graph | Missing gate evidence, stale prompt context, unbounded artifact read refused | Yes | `retry_verifier` after context rebuild |
| `checkpoint_contradiction` | Merge queue, journal recovery, gates | Checkpoint attempted after failed gate, mismatched contract/gate ids | Yes/fatal | `quiesce` |
| `regroup_invalid` | Regroup overlay, scheduler | Dependency cycle, impossible atomic group, conflicting write ownership | Yes | `quiesce` with scheduler feedback |
| `evidence_corruption` | Journal, artifact store, sandbox capture | Artifact hash mismatch, payload digest mismatch, projection body conflict | Yes/fatal | `quiesce` or one recapture when source sandbox is retained |
| `resource_exhausted` | Dispatcher, sandbox runner, merge queue | Disk, process limit, provider quota without provider-class details | No | Specific route row supplies one concrete enum action. |
| `operator_required` | Workspace authority, dispatcher, journal, artifact store | Credentials, manual approval, non-workflow permissions, unsafe recovery conflict | Yes | `operator_required` |
| `unknown` | Any source | Classifier cannot safely map the observation | No | one diagnostic `quiesce`/bounded retry only when safe |

### Retry Budgets

Budgets are counted per `(feature_id, failure_class, failure_type,
signature_hash)` and are consumed when `mark_route_started` reserves the route,
not when the repair finishes. A crash after reservation resumes the same route
decision by idempotency key instead of spending a second budget slot.

Budget rules:

- Budgets are keyed to the concrete `(failure_class, failure_type)` pair after
  taxonomy normalization. A workflow failure can never borrow product-defect
  budget.
- `decide` may return a terminal `quiesce` or `operator_required` decision with
  `budget_remaining=0` without reserving a retry/repair attempt. Starting a
  non-terminal route is what consumes the budget slot.
- Class budgets are defaults; narrower type rows may set a lower budget, but may
  not raise the class budget without an explicit policy-table entry and test.
- When a route produces a different typed failure, that child failure gets its
  own budget key and links to the parent decision. When it produces the same
  signature, the original reserved slot is counted and the next decision uses
  the exhaustion route.
- Provider and resource budgets must honor backoff/governor evidence in the
  payload; retrying immediately against an unchanged quota or resource condition
  is treated as budget exhausted.

| Failure class | Budget | Budget scope | Exhaustion route |
| --- | --- | --- | --- |
| `product_defect` | 2 | Per group/task/product signature | `quiesce` and request regroup/scheduler feedback if repeated |
| `contract_compile` | 1 | Per contract digest/signature | `quiesce`; planner/contract repair required before dispatch |
| `contract_violation` | 1 | Per contract id plus offending path set | `quiesce`; do not broaden contract automatically |
| `stale_projection` | 1 | Per source artifact/snapshot digest | `quiesce` as `checkpoint_contradiction` if refresh repeats |
| `worktree_alias` | 1 | Per alias and canonical repo id | `quiesce`; escalate only when workspace authority marks manual |
| `acl_workability` | 1 | Per runtime user and path digest | `operator_required` only when authority says not workflow-repairable |
| `sandbox_allocation` | 2 | Per base snapshot/repo/resource code | `quiesce` as resource blocker |
| `sandbox_binding` | 0 | Per role/runtime/binding digest | `quiesce` |
| `sandbox_isolation` | 0 | Per sandbox id/path escape signature | `quiesce` and poison lease |
| `sandbox_capture` | 1 | Per sandbox id/repo/base commit | `quiesce` and retain sandbox evidence |
| `sandbox_cleanup` | 3 | Per sandbox lease id | Mark retained; do not block completed merge evidence |
| `commit_hygiene` | 1 | Per commit stage/hook/status signature | `quiesce` |
| `merge_conflict` | 1 | Per queue item/base/head signature | `quiesce` or scheduler regroup when dependency-driven |
| `runtime_provider` | 2 | Per provider/model/error code | `quiesce` as provider/resource blocker |
| `runtime_timeout` | 1 | Per role/runtime/timeout digest | `quiesce` as provider/runtime blocker |
| `runtime_cancelled` | 0 | Per cancellation owner/attempt id | `quiesce` unless owner requested resume |
| `runtime_context` | 1 | Per context digest/materialization error | `quiesce` |
| `runtime_structured_output` | 1 | Per role/runtime/schema digest | `quiesce` |
| `dispatcher_internal` | 0 | Per state/idempotency digest | `quiesce` |
| `verifier_provider` | 2 | Per provider/model/error code | `quiesce` without marking product unhealthy |
| `verifier_context` | 1 | Per context digest/gate set | `quiesce` |
| `checkpoint_contradiction` | 0 | Per checkpoint/gate/contract digest | `quiesce` |
| `regroup_invalid` | 0 | Per DAG/regroup digest | `quiesce` |
| `evidence_corruption` | 1 only when recapturable | Per corrupted evidence id/content hash | `quiesce` |
| `resource_exhausted` | 1 | Per resource code/owner | `quiesce` |
| `operator_required` | 0 | Per operator reason/path digest | `operator_required` |
| `unknown` | 0 or 1 diagnostic | Per normalized payload digest | `quiesce` |

### Routing Table

The route table is data-backed in code, not a series of local `if` branches in
the implementation loop. More-specific `failure_type` rows may override the
class default only by narrowing the route.

Route validation:

- Every route row declares a single action, required evidence kinds, budget key
  builder, and request builder. Missing builder coverage is a startup failure.
- A route row that returns `run_product_repair` must prove the class is
  `product_defect` or scoped `contract_violation` cleanup and must include fixed
  contract ids.
- A route row for a deterministic workflow class outside scoped
  `contract_violation` cleanup must return a non-product repair, retry,
  `quiesce`, or `operator_required` action.
- `retry_merge` rows must validate source queue eligibility by locking the
  source `merge_queue_items` row and checking the real supersession column, not
  by reading payload mirrors.

| Failure class/type | Action | Required evidence | Notes |
| --- | --- | --- | --- |
| `product_defect/semantic_verifier_rejected` | `run_product_repair` | Verifier verdict, gate ids, contract ids, patch summary ids | Product repair receives fixed contracts and current sandbox binding. |
| `product_defect/required_path_missing` | `run_product_repair` | Contract verdict, virtual apply or post-merge snapshot | If evidence exists only in stale artifact/context, reclassify as `stale_projection`. |
| `contract_compile/contract_invalid_path` | `run_contract_repair` | Contract compile error, task contract ids, offending paths | Real DAG contract defect only; alias drift is classified as `worktree_alias`. |
| `contract_compile/contract_scope_conflict` | `quiesce` | Conflicting contract ids, DAG sha | Scheduler/regroup must create a valid atomic group before dispatch. |
| `contract_compile/contract_missing_dependency` | `quiesce` | Contract closure, missing task/dependency id, DAG sha | Planner/regroup repair must restore dependency closure before dispatch. |
| `contract_compile/contract_same_wave_dependency` | `quiesce` | Contract closure, same-wave dependency pair, regroup overlay | Scheduler/regroup must split the wave before dispatch. |
| `contract_violation/outside_allowed_paths` | `run_product_repair` | Patch summary, contract verdict, offending paths | Repair edits product back into contract scope; it must not widen the contract. |
| `contract_violation/forbidden_path_touched` | `run_product_repair` | Patch summary, forbidden rule, workspace snapshot | Product cleanup only; bad contract rules must be emitted as `contract_compile/contract_invalid_path`. |
| `contract_violation/read_only_path_touched` | `run_product_repair` | Patch summary, read-only rule, offending paths | Repair must move changes out of read-only scope; never widen read-only paths. |
| `contract_violation/contract_id_mismatch` | `quiesce` | Dispatcher, gate, merge queue contract ids | Queue cannot infer equivalence from task id or path overlap. |
| `stale_projection/verifier_context_stale` | `retry_verifier` | Context digest, newer snapshot/evidence ids | Rebuild context before spending model verifier work. |
| `stale_projection/workspace_snapshot_stale` | `run_workspace_repair` | Snapshot id, expected root/base, observed root/base | Rebuild workspace snapshot through authority before verifier or merge work continues. |
| `worktree_alias/alias_points_to_noncanonical_root` | `run_canonicalization_repair` | Workspace snapshot, alias report | No product repair and no operator escalation while workflow repairable. |
| `worktree_alias/alias_only_canonical_missing` | `run_canonicalization_repair` | Alias/canonical path hashes, registry evidence | Canonicalization repair is sandboxed and merge-queue-applied. |
| `worktree_alias/alias_canonical_divergent` | `run_canonicalization_repair` | Alias/canonical hashes and task contract ids | Focused adjudication repair; no direct workspace copy. |
| `acl_workability/unwritable_runtime_path` | `run_workspace_repair` | Runtime-user ACL report, path digest | Recheck with the actual runtime identity before escalation. |
| `sandbox_allocation/sandbox_clone_failed` | `retry_dispatch` | Base snapshot, sandbox lease attempt, resource code | New sandbox lease; old partial lease is terminal or cleaned first. |
| `sandbox_allocation/sandbox_disk_quota` | `quiesce` | Disk budget report, sandbox root, lease id | Resource governor must free space or reduce concurrency before retry. |
| `sandbox_allocation/sandbox_base_snapshot_unavailable` | `retry_dispatch` | Missing snapshot id, workspace authority status | Rebuild workspace snapshot, then allocate a new lease. |
| `sandbox_binding/runtime_workspace_binding_failed` | `quiesce` | Binding manifest, runtime role metadata digest | Runtime must not start without enforceable sandbox binding. |
| `sandbox_isolation/canonical_path_exposed_to_writer` | `quiesce` | Binding manifest, runtime command, workspace roots | Fatal for the attempt; poison lease and preserve evidence. |
| `sandbox_isolation/path_escape_detected` | `quiesce` | Patch summary, resolved path proof | Never enqueue or apply the patch. |
| `sandbox_capture/patch_capture_failed` | `retry_sandbox_capture` | Sandbox id, repo id, base commit, capture stderr | Recapture only from the retained sandbox and same base. |
| `sandbox_capture/sandbox_index_corrupt` | `quiesce` | Sandbox repo id, index error, lease id | Preserve sandbox; do not rerun product repair blindly. |
| `sandbox_cleanup/cleanup_failed` | `run_sandbox_cleanup` | Lease id, cleanup error | Cleanup retries are operational and do not change product route. |
| `commit_hygiene/commit_hook_failed` | `run_commit_hygiene_repair` | Commit failure payload, hook/status output, queue item id | Never enters broad RCA unless a later verifier produces product failure. |
| `commit_hygiene/dirty_after_commit` | `run_commit_hygiene_repair` | Porcelain-v2 status, commit proof, queue item id | Queue item cannot checkpoint until no-dirty proof exists. |
| `merge_conflict/stale_base_commit` | `retry_merge` | Queue item, base/head commits, patch digest | One safe rebase/apply attempt, then quiesce/regroup. |
| `merge_conflict/rebase_conflict` | `retry_merge` | Queue item, conflicted files, base/head commits | Retry only with refreshed base and same contract scope. |
| `merge_conflict/patch_apply_conflict` | `retry_merge` | Patch digest, conflicted paths, queue item | Rebuild from sandbox/contract evidence; do not product-repair from merge stderr alone. |
| `runtime_provider/provider_internal_error` | `retry_dispatch` | Provider request id, adapter error, attempt id | Does not mark task output or product unhealthy. |
| `runtime_provider/provider_rate_limited` | `retry_dispatch` | Provider retry-after/quota evidence | Honor backoff inside budget; no broad repair. |
| `runtime_provider/provider_transport_error` | `retry_dispatch` | Transport error, runtime adapter, attempt id | Retry through runtime policy; no product route. |
| `runtime_provider/process_failed` | `retry_dispatch` | Exit code, stderr slice, runtime adapter, sandbox lease id | Retry through runtime policy after preserving partial sandbox evidence; no product route. |
| `runtime_timeout/watchdog_timeout` | `retry_dispatch` | Invocation id, timeout, last heartbeat | Retry once with same contract and fresh runtime lease. |
| `runtime_cancelled/runtime_cancelled` | `quiesce` | Cancellation owner, attempt id, runtime state | Resume only through an explicit new dispatch attempt. |
| `runtime_context/prompt_too_large` | `retry_dispatch` | Context manifest, budget report | Rebuild bounded context once using stricter slice budgets; never broad product repair. |
| `runtime_context/context_materialization_failed` | `quiesce` | Missing evidence/file ref, context builder error | Fix evidence/context before runtime dispatch. |
| `runtime_context/context_permission_denied` | `operator_required` | Denied path outside feature/sandbox roots, runtime user | Only outside owned roots escalates; owned-root ACL remains `acl_workability`. |
| `runtime_structured_output/malformed_structured_output` | `retry_dispatch` | Raw output evidence, schema digest | Retry with same contract and stricter schema context. |
| `dispatcher_internal/idempotency_conflict` | `quiesce` | Existing row digest, requested row digest | Fatal control-plane consistency issue. |
| `verifier_provider/verifier_provider_timeout` | `retry_verifier` | Verifier invocation id, timeout, last heartbeat | Product route is unchanged until verifier runs successfully. |
| `verifier_provider/verifier_provider_crash` | `retry_verifier` | Verifier provider evidence, request id when available | Product route is unchanged until verifier runs successfully. |
| `verifier_provider/verifier_parse_failed` | `retry_verifier` | Raw verifier output evidence and schema digest | Retry verifier/context only; do not run product repair from malformed verifier output. |
| `verifier_context/context_materialization_failed` | `retry_verifier` | Missing required context refs, budget report, graph id | Rebuild verifier context from bounded evidence before rerunning verifier. |
| `verifier_context/verifier_context_stale` | `retry_verifier` | Gate graph, context digest, snapshot id | Use only for graph-internal verifier package staleness after typed refs were selected. Stale legacy artifacts, generated-output projections, or alias paths are `stale_projection/verifier_context_stale`. |
| `checkpoint_contradiction/checkpoint_after_failed_gate` | `quiesce` | Gate failure evidence, checkpoint/queue row | Fatal workflow safety stop. |
| `regroup_invalid/regroup_dependency_cycle` | `quiesce` | Regroup overlay, dependency graph | Scheduler feedback must change DAG/grouping before more execution. |
| `regroup_invalid/regroup_write_conflict` | `quiesce` | Regroup overlay, write-set conflict evidence | Scheduler feedback must split/serialize before dispatch. |
| `evidence_corruption/artifact_hash_mismatch` | `quiesce` | Evidence node id, stored hash, observed hash | Recapture happens only through a new explicit route after retained source evidence is proven intact. |
| `evidence_corruption/payload_digest_mismatch` | `quiesce` | Payload digest refs and source row ids | Rebuild projection/evidence from typed source only when source hash is intact. |
| `evidence_corruption/projection_body_conflict` | `quiesce` | Projection key, typed source id, conflicting body hashes | Do not append a second projection under the same idempotency key. |
| `resource_exhausted/db_resource_exhausted` | `quiesce` | DB connection/RSS/growth budget evidence | Reduce readers/concurrency before retry. |
| `resource_exhausted/disk_resource_exhausted` | `quiesce` | Disk/temp/sandbox budget evidence | Cleanup/governor action before retry. |
| `resource_exhausted/process_resource_exhausted` | `retry_dispatch` | Process limit, active runtime counts | Retry only after governor reduces concurrency. |
| `resource_exhausted/provider_quota_exhausted` | `retry_dispatch` | Provider quota/reset evidence | Honor backoff and budget; no product repair. |
| `resource_exhausted/unclassified` | `quiesce` | Resource owner/code and retry-after if any | Unknown resource pressure stops for governor diagnosis rather than guessing. |
| `operator_required/operator_clearance_required` | `operator_required` | Workspace authority/operator report | Only class allowed to stop for manual action by default. |
| `unknown/unclassified` | `quiesce` | Original observation payload | One bounded diagnostic attempt only when all safety gates allow it. |

## Refactoring Steps

1. Add `execution/failure_router.py` with taxonomy constants, route table,
   budget policy, signature builder, and `FailureRouter` service methods.
2. Add `execution/repair.py` request/outcome models and `RouteExecutor`
   integration. It converts started route decisions into concrete repair, retry,
   merge, verifier, or cleanup requests without reclassifying failures.
3. Add journal/store methods for `record_failure`, route-decision evidence, repair
   request/outcome evidence, and
   atomic budget reservation. These methods must return existing rows on
   matching idempotency keys and raise `IdempotencyConflict` on digest mismatch.
4. Normalize failures from dispatcher, gates, verifier, sandbox, workspace
   authority, contracts, regroup, and merge queue into `typed_failures` before
   any retry, repair, cleanup, merge, checkpoint, or supervisor projection.
5. Replace direct route branching in `_verify_and_fix_group` and commit-failure
   handling with calls to `FailureRouter.decide`. The implementation phase may
   execute the returned action but may not reinterpret its class.
6. Move deterministic route repeat checks into the router by signature and
   budget. Remove local duplicate-signature guards after compatibility tests
   prove the router produces the same blocked-repeat decisions.
7. Teach dispatcher and verifier retry paths to request new attempts through the
   router action, preserving original contract ids, sandbox policy, runtime
   binding digest, and route-decision evidence id.
8. Teach sandbox runner to emit separate failures for allocation, binding,
   isolation, capture, and cleanup; fatal isolation failures must poison the
   lease before routing.
9. Teach contract validation to emit `contract_compile` and
   `contract_violation` classes before sandbox output can enter merge queue.
10. Teach merge queue to emit `commit_hygiene`, `merge_conflict`, and
   `checkpoint_contradiction` failures and to stop queue-item progress until the
   router returns a permitted queue action.
11. Project route decisions into current supervisor/dashboard read models.
    Supervisor remains read-only and never mutates router budgets or route
    outcomes.
12. Delete or quarantine legacy production branches that can run broad repair
    around the router. Compatibility artifact projection may remain, but route
    authority must have one path for new typed attempts.

### Signature And Idempotency

Signatures are canonical JSON digests. Builders must normalize path separators
to repo-relative POSIX paths, sort unordered lists, lowercase only when workspace
authority declares the repo case-insensitive, and omit volatile fields such as
timestamps, process ids, stdout line numbers, and retry ordinals.

Base signature material:

- `feature_id`, `dag_sha256`, `group_idx`, `task_id` when present.
- `failure_class`, `failure_type`, source slice, severity, deterministic flag.
- Primary typed ids: contract ids, sandbox id, queue item id, gate ids, snapshot
  ids, attempt kind/stage, provider/runtime name, repo id, base/head commits.
- Normalized discriminators: path set, provider error code/request id class,
  hook name, status digest, context digest, payload content hash, and source
  evidence content hash.

Idempotency keys:

- Failure row:
  `failure:{feature_id}:{attempt_id or '-'}:{failure_class}:{signature_hash}`.
- Route decision evidence:
  `route:{feature_id}:{failure_id}:{signature_hash}:{action}:n{reservation_ordinal}`.
- Repair/dispatch attempt spawned by a route:
  `attempt:{feature_id}:{dag_sha256}:g{group_idx or '-'}:{task_id or '-'}:{action}:{failure_id}:n{reservation_ordinal}:{input_digest}`.
- Repair request evidence:
  `repair:{feature_id}:{route_decision_id}:{failure_id}:{action}:{repair_kind}:{input_digest}`.
- Repair outcome evidence:
  `repair-outcome:{feature_id}:{repair_request_id}:{status}:{output_digest}`.
- Retry request evidence:
  `retry:{feature_id}:{route_decision_id}:{failure_id}:{retry_kind}:{attempt_kind}:{input_digest}`.
- Retry outcome evidence:
  `retry-outcome:{feature_id}:{retry_request_id}:{status}:{output_digest}`.
- Compatibility projection:
  `projection:{feature_id}:failure_router:{legacy_key}:typed_failures:{failure_id}:{body_sha256}`.

Idempotency rules:

- Recording the same failure signature returns the existing failure row and
  increments bounded occurrence metadata; it does not create parallel repair
  authority.
- `decide` is pure for a given failure row plus current budget state. It does
  not consume budget.
- `mark_route_started` reserves exactly one budget slot in the same transaction
  that writes route-decision evidence. Retrying `mark_route_started` with the
  same idempotency key returns the same decision.
- A route can spawn at most one active repair/dispatch/merge/verifier attempt
  for the same `(failure_id, action, reservation_ordinal)`.
- If a repair produces the same failure signature, the next decision sees the
  budget already consumed and routes to the class exhaustion action.
- If a repair produces a different signature, it is a new failure, but parent
  failure id and decision id are linked in payload for causality.
- Operator-required decisions are terminal until an explicit operator clearance
  evidence id is recorded; clearance creates a new failure/decision signature
  rather than editing the old terminal row.

### Integration Points

- Typed journal: Owns `typed_failures`, route-decision evidence, projection
  idempotency, budget counters in payload, and compatibility artifacts.
- Workspace authority: Emits `worktree_alias`, `acl_workability`, and
  `operator_required` observations with canonical repo ids and runtime-user
  writeability evidence.
- Task contracts: Emits `contract_compile` and `contract_violation`; passes
  contract ids and verdict ids through router repair scope.
- Sandbox runner: Emits `sandbox_allocation`, `sandbox_binding`,
  `sandbox_isolation`, `sandbox_capture`, and `sandbox_cleanup`; blocks runtime
  start or merge enqueue until route authority says the condition is repairable.
- Runtime dispatcher: Emits `runtime_provider`, `runtime_timeout`,
  `runtime_cancelled`, `runtime_context`, `runtime_structured_output`,
  `dispatcher_internal`, and cancellation/resource failures; it never decides
  product repair.
- Verification graph: Emits `product_defect`, `verifier_provider`,
  `verifier_context`, and stale gate failures with gate evidence ids.
- Merge queue: Emits `merge_conflict`, `commit_hygiene`, and
  `checkpoint_contradiction`; it consumes router actions for rebase, commit
  hygiene repair, quiesce, and checkpoint stop.
- Regroup overlay/scheduler: Consumes `regroup_invalid`, exhausted
  `product_defect`, and exhausted `merge_conflict` routes as structured feedback
  for new DAG grouping.
- Supervisor/dashboard: Read route decisions from typed summaries and show the
  executor route as authoritative. Legacy classifier categories remain a
  projection layer for old UI and Slack behavior.

## Persistence And Artifact Compatibility

> Implementation reconciliation (Slice 07): the failure router persists through
> the existing typed journal rather than dedicated `typed_failures` /
> `failure_route_budgets` tables. The bullets below describe the design as
> built. A separate budget table was rejected as a drift-prone second source of
> truth for retry state.

- Failures are persisted as `evidence_nodes(kind='runtime_failure_context')`
  rows whose payload carries the typed failure fields — `failure_class`,
  `failure_type`, `signature_hash`, deterministic/retryable/operator flags,
  evidence ids, and the bounded `route_decision` object. The evidence node is
  the typed failure row; there is no separate `typed_failures` table.
- Each route decision is persisted as the bounded `route_decision` object
  embedded in the failure evidence payload (the `runtime_failure_context`
  evidence for the runtime-provider path; the `dag-direct-repair-route:*`
  projection for the direct-route path). `ExecutionControlStore` bounded
  snapshots project `route`, `failure_class`, `failure_type`, `deterministic`,
  `retryable`, and `operator_required` from `route_decision` and prefer those
  typed values over legacy metadata.
- The typed `FailureRouter` is a pure routing component: `decide()` is a
  deterministic function of the persisted failure, and the runner-scoped
  `InMemoryFailureRouterPort` only deduplicates within a single process. Route
  decisions reconstruct identically on crash/resume from the durably persisted
  failure evidence and the journal-backed retry counters.
- Retry budgets are not a standalone `failure_route_budgets` table. Budget
  exhaustion is derived from the existing durable journal-backed retry counters
  — the DAG group `retry` counter for runtime-provider failures (re-read from
  the journal on resume) and the signature-scoped direct-route counters — so
  retry state has a single source of truth.
- The `failure_route_decision`, `repair_request`, `retry_request`,
  `repair_outcome`, and `retry_outcome` evidence-node kinds are reserved in the
  schema. Persisting `repair_request`/`retry_request` and their outcomes is
  owned by the merge-queue / repair-execution slice (Slice 08), consistent with
  `RouteExecutor.execute_repair`/`execute_retry` being deferred there.
- `retry_merge` depends on `merge_queue_items.retry_of_queue_item_id` as a real
  nullable self-reference, plus a retry-source index and a partial unique
  constraint that permits at most one active replacement chain for a failed
  source queue item. Startup must fail closed if this column, index, constraint,
  or store-level validation hook is absent.
- The merge queue store must create a replacement row and route/retry outcome
  evidence in one transaction. Checkpoint coverage must read
  `retry_of_queue_item_id` and task/contract coverage tables; payload fields
  such as `retry_of_queue_item_id`, `retry_of`, or `supersedes` are display
  mirrors only and cannot authorize replacement.
- Project compatibility artifacts for current supervisor and dashboard where
  needed. Existing `dag-commit-failure:*`, ACL, alias, direct-route, preflight,
  and `dag-verify:*` artifacts remain evidence, but route authority moves to
  typed failures and route-decision evidence.
- Keep legacy artifact bodies bounded when projected from router state. For
  `dag-direct-repair-route:*`, the compatibility body must preserve the current
  legacy shape, including `route`, repeated-route signature fields, skip flags,
  target files, and repair scope, while also adding bounded typed refs for
  failure id, route decision id, required evidence ids, canonical action, and
  budget remaining.
- Legacy direct-route strings are compatibility payload values, not new
  `RouteAction` values. The projection layer must use this crosswalk:

  | Legacy `route` / `repair_route` | Canonical class/type | Canonical action | Compatibility notes |
  | --- | --- | --- | --- |
  | `commit_hygiene_focused` | `commit_hygiene/commit_hook_failed` or `commit_hygiene/dirty_after_commit` | `run_commit_hygiene_repair` | Preserve signature, skip-expanded-verify flags, hook/status excerpts, and target files. |
  | `manifest_forbidden_product_cleanup` | `contract_violation/forbidden_path_touched` | `run_product_repair` | Preserve target files and manifest-forbidden marker; do not map to canonicalization. |
  | `repo_hygiene_operator` | `operator_required/operator_clearance_required` | `operator_required` | Only for non-workflow-safe repo hygiene; resolvable ACL/worktree cases use workspace/canonicalization classes. |
  | `normal_verify_repair` | `product_defect/semantic_verifier_rejected` or `product_defect/required_path_missing` | `run_product_repair` | Preserve legacy route for readers; typed router chooses the specific product failure type from verdict tags. |
  | `semantic_verify_needed` | `stale_projection/verifier_context_stale` or `product_defect/semantic_verifier_rejected` | `retry_verifier` or `run_product_repair` | Used only after deterministic artifact-only handling finds no actionable metadata repair. |
  | `artifact_only` | `stale_projection/verifier_context_stale` | `retry_verifier` | Metadata/projection-only path canonicalization; no product repair or canonical repo mutation. |
  | `product_cleanup_required` | `contract_violation/forbidden_path_touched` | `run_product_repair` | Focused product cleanup in canonical paths through sandbox and merge queue. |

  Any unknown legacy route string records
  `evidence_corruption/projection_body_conflict` and routes to `quiesce`; the
  compatibility writer must never invent a canonical action from an
  unrecognized string.
- Route decisions are append-only. Resolution marks failure status and links
  evidence; it does not rewrite the original classification, signature, or route
  history.
- Atomic landing does not create a shadow production route. Compatibility
  projection exists only for readers that have not migrated to typed summaries.

## Edge Cases And Failure Handling

- Same failure signature repeated after repair: the reserved budget is already
  consumed; next route uses exhaustion behavior and does not spawn duplicate
  repair attempts.
- Commit-only failure: never dispatch broad product repair unless commit hygiene
  repair changes product state and a later verifier/gate records a
  `product_defect`.
- Worktree alias or ACL failure: deterministic repair/canonicalization, no
  operator escalation when workspace authority says the condition is workflow
  repairable.
- Contract violation: preserve the original contract. Product repair may modify
  files to satisfy it, but only planner/contract repair can produce a new
  contract digest from a new DAG artifact.
- Sandbox isolation failure: poison the lease, preserve evidence, block merge
  enqueue, and quiesce. Automatic retry is allowed only if the failure was
  reclassified away from isolation by workspace authority evidence.
- Sandbox cleanup failure: retry cleanup within its own operational budget, but
  do not invalidate already captured patch or merge evidence.
- Provider internal error: retry runtime/verifier within provider budget; do not
  mark product unhealthy.
- Resource exhaustion: retry only when the payload includes a changing condition
  such as retry-after or cleanup-completed evidence; otherwise quiesce.
- Evidence corruption: recapture only from the original retained sandbox/source
  and only when the new evidence can be linked to the same base digest.
- Checkpoint contradiction: fatal safety stop. Do not run product repair,
  verifier retry, or merge retry until typed gate/queue ids agree.
- Operator-required: terminal route until explicit clearance evidence is
  recorded. Clearance creates a new route decision rather than editing history.
- Unknown failure: quiesce if safety risk, otherwise one bounded diagnostic
  attempt that cannot mutate product files or checkpoint state.

## Tests

Unit tests for `failure_router.py`:

- Taxonomy constants include every class and type above, and route table entries
  exist for every non-abstract class/type combination used by dispatcher,
  contracts, sandbox runner, gates, merge queue, and regroup.
- Signature builder is stable for equivalent payload ordering, path separator
  variants, and unordered evidence lists; it changes when normalized path,
  contract id, base commit, provider error class, gate set, or payload hash
  changes.
- Signature builder omits timestamps, process ids, retry ordinals, and
  unbounded stdout/stderr bodies.
- Failure idempotency returns the existing row for the same key and raises
  `IdempotencyConflict` when the key matches but payload digest differs.
- `decide` is side-effect-free and returns the same action while no budget
  reservation has occurred.
- `mark_route_started` atomically reserves one budget slot and returns the same
  decision when repeated with the same route idempotency key.
- Budget exhaustion routes match the retry-budget table for every failure class.
- Operator-required route has zero retry budget and remains terminal until
  clearance evidence creates a new signature.
- Every repairable `RouteAction` builds a `RepairRequest` with the correct
  `repair_kind`, allowed mutations, sandbox mode, enqueue strategy, target
  contract ids, required evidence ids, and idempotency key.
- `mark_route_started` is required before
  `RouteExecutor.build_route_request`; unreserved decisions cannot spawn repair,
  retry, merge, verifier, or cleanup work.
- Repair outcomes link back to the route decision, reserve no extra budget on
  idempotent replay, and record `produced_failure_id` when the repair reveals a
  different blocker.

Routing policy tests:

- Generated route-table tests prove no deterministic workflow class outside
  scoped `contract_violation` cleanup maps to `run_product_repair`. The only
  allowed product-repair classes are `product_defect` and scoped
  `contract_violation`.
- Commit-only failure routes to `run_commit_hygiene_repair` and never to broad
  product repair.
- Worktree alias routes to `run_canonicalization_repair`.
- ACL failure routes to `run_workspace_repair`; it escalates only when workspace
  authority marks it non-repairable.
- Stale projection routes to context/artifact refresh and `retry_verifier`.
- Contract compile invalid path routes to canonicalization when authority shows
  alias drift, and to contract repair/quiesce when the DAG contract itself is
  invalid.
- Contract violation for `outside_allowed_paths` routes to scoped product repair
  with original contract ids.
- Contract violation for `forbidden_path_touched` blocks merge and does not
  broaden allowed paths.
- Sandbox allocation failure retries with a new lease within budget.
- Sandbox binding failure quiesces before runtime start.
- Sandbox isolation path escape poisons the lease, blocks merge enqueue, and
  consumes no automatic retry budget.
- Sandbox capture failure retries recapture only from the retained sandbox and
  same base commit.
- Metadata-only canonicalization repair refreshes projections and verifier
  context without allocating a sandbox or touching product files.
- Alias-divergent canonicalization repair allocates a canonicalization sandbox,
  captures a patch, validates contracts, and enters the merge queue before any
  canonical product mutation.
- Workspace ACL repair runs through workspace authority, writes ACL/snapshot
  evidence, and cannot create patch summaries.
- Contract repair can recompile from immutable inputs or quiesce with scheduler
  feedback, but tests fail if it edits the root DAG or widens task scope from
  model output.
- Sandbox cleanup failure retries cleanup without changing product route.
- Runtime provider error routes to runtime retry and does not mark product
  unhealthy.
- Runtime malformed output gets one bounded dispatch retry with the same
  contract and sandbox policy.
- Verifier provider error routes to verifier retry and preserves the previous
  product classification.
- Checkpoint contradiction quiesces immediately and cannot be downgraded by
  product repair.
- Merge conflict attempts one safe `retry_merge`, then quiesces or emits
  scheduler feedback.
- `retry_merge` builder rejects any source queue item that is not terminal
  `failed`, has a result commit, belongs to another feature/DAG/group, lacks
  preserved task/contract coverage, or already has an active replacement through
  the real `merge_queue_items.retry_of_queue_item_id` column.
- `retry_merge` creates exactly one replacement row with
  `retry_of_queue_item_id` set to the failed source id and never relies on a JSON
  payload mirror for supersession, coverage, or checkpoint authorization.
- Evidence corruption recaptures only when retained source evidence exists;
  otherwise it quiesces.
- Product defect remains product repair and is not hidden by pipeline fixes.

Integration and regression tests:

- `_verify_and_fix_group` calls `FailureRouter.decide` for verifier, direct
  route, commit, workspace, and deterministic preflight failures; legacy direct
  branching no longer spawns repair attempts independently.
- Dispatcher provider failure writes a typed failure and optional compatibility
  artifact, then router returns `retry_dispatch`.
- Contract verdict failure writes typed `contract_violation`; merge queue
  admission refuses the patch before any verifier or checkpoint success
  projection.
- Sandbox runner emits separate allocation, binding, isolation, capture, and
  cleanup failures with required evidence ids.
- Merge queue commit hook failure writes `commit_hygiene`, projects
  `dag-commit-failure:*` once, and does not checkpoint.
- Crash after route reservation but before repair dispatch resumes the same
  decision and does not spend a second budget slot.
- Crash after repair dispatch but before route finish links the recovered
  attempt to the same route decision.
- Repeated same signature after repair exhausts budget and quiesces/escalates
  according to class.
- Different signature after repair records a child failure and preserves parent
  decision causality.
- Supervisor and dashboard read the same route action and budget remaining as
  the executor, with no artifact-body hydration.
- Legacy compatibility projections for `dag-verify:*`,
  `dag-commit-failure:*`, ACL, alias, and direct-route artifacts are written
  exactly once per projection idempotency key.
- Atomic startup guard fails closed when typed journal, sandbox runner,
  dispatcher boundary, gate graph, failure router, or merge queue dependencies
  are unavailable.
- Atomic startup guard also fails closed when the merge queue lacks the real
  `retry_of_queue_item_id` column or its active-replacement validation, when any
  route row lacks a request builder, or when compatibility projection is the
  only available route writer.
- Staging asserts that disabling only the router is not a supported production
  mode: new typed attempts either use all Slice 01-10 authorities behind the
  Slice 12 gate or the build rolls back.

## Acceptance Criteria

- All retry/repair decisions are traceable to typed failure ids.
- Broad RCA/product repair is not used for deterministic workflow failures.
- Supervisor and dashboard show the same route decision as the executor.
- Retry budgets prevent infinite same-class loops.
- `contract_compile`, `contract_violation`, `sandbox_allocation`,
  `sandbox_binding`, `sandbox_isolation`, `sandbox_capture`, and
  `sandbox_cleanup` are first-class classes with explicit routes and budgets.
- Route decisions are idempotent across crash/resume and cannot spawn duplicate
  repair, dispatch, verifier, merge, or cleanup attempts for the same reserved
  budget slot.
- Commit hygiene, ACL, alias, stale projection, sandbox isolation, and
  checkpoint contradiction never enter broad product repair by default.
- New implementation and repair attempts have one authoritative route path
  through typed failures; legacy artifacts are projections only.
- Atomic landing guard prevents enabling the router unless the typed journal,
  workspace authority, contracts, sandbox runner, dispatcher boundary, gates,
  merge queue, and supervisor read projection are compatible.

## Rollout/Rollback Notes

There is no phased production rollout for this slice. The router lands as part
of one atomic execution-control feature with Slices 01-10 and the Slice 12
release gate. For new typed attempts, production either uses the complete
router-authoritative execution path or the deployed build is rolled back to the
previous executor.

Unsupported rollout modes:

- No advisory-only router that records suggestions while legacy branches still
  choose repairs for new attempts.
- No per-route or per-class production enablement flags.
- No mixed mode where compatibility artifacts are written as route authority.
- No synthetic migration of in-flight legacy features into typed route
  authority without an explicit validated checkpoint restart.

Pre-landing validation happens in tests and local/staging runs only:

- All tests above pass, including crash/resume and idempotency coverage.
- Startup guard proves typed journal, workspace authority, contracts, sandbox
  runner, dispatcher boundary, verification graph, merge queue, supervisor read
  projection, and compatibility projection methods are available.
- Migration/resume fixtures prove active legacy-only features are classified
  once and continue on the legacy executor with no mixed typed writes. A feature
  may enter typed execution only by an explicit restart from a validated
  checkpoint under the complete control plane; no automatic synthetic migration
  is allowed.
- Compatibility artifact readers still receive bounded `dag-verify:*`,
  `dag-commit-failure:*`, ACL, alias, and route summaries projected from typed
  state.

Rollback is a deploy rollback, not a router flag flip:

- Stop new typed dispatch and route reservation.
- Let already-started route attempts, sandbox leases, and merge queue items
  drain to safe terminal states or be recovered by their owning slice rollback
  rules.
- Preserve typed failures, route-decision evidence, signatures, projections,
  and budget payloads for audit.
- Do not hand retained sandbox paths, partially routed attempts, or typed queue
  items to the legacy executor as canonical state.
- Legacy executor may resume only features that never entered typed route
  authority, or features that an explicit repair migration has reconciled.

## Cross-Slice Dependencies

- Slice 1 stores typed failures.
- Slice 2 emits workspace failures.
- Slice 3 emits contract compile and contract violation failures.
- Slice 4 emits sandbox allocation, binding, isolation, capture, and cleanup failures.
- Slice 5 emits runtime and malformed-output failures.
- Slice 6 emits gate/verifier/product/context failures.
- Slice 8 emits merge failures.
- Slice 9 consumes exhausted product, merge, and regroup feedback.
- Slice 10 exposes decisions to supervisor and dashboard.
- Slice 12 gates the atomic landing and deploy rollback behavior.
