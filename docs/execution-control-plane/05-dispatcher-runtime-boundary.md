# 05. Dispatcher Runtime Boundary

## Objective

Extract task dispatch and runtime invocation from the DAG executor. The
dispatcher starts attempts, invokes a runtime inside a sandbox, captures output,
and records evidence. It does not commit, checkpoint, route repairs, or mutate
canonical repos.

The dispatcher boundary is the only product-writing runtime entrypoint for
implementation and repair work. It converts a task contract, sandbox binding,
runtime policy, and bounded context bundle into typed attempt evidence. A
successful dispatch means "the runtime attempt completed and all attempt
evidence was recorded"; it does not mean "the group is integrated" or "the
canonical repository is correct."

## Current Code Citations

- DAG task dispatch loop: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4962).
- Inner task runner: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4977).
- Legacy prompt construction: [_build_task_prompt](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2807).
- Runtime call through `runner.run(Ask(...))`: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5036).
- Task result persistence: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5147).
- Enhancement task duplicate dispatch loop: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5497).
- Enhancement runtime call and marker write: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5548) and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5613).
- Runner runtime retry and watchdog behavior: [_runner.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/_runner.py:508).
- Structured task result shape: [ImplementationResult](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:1022).
- Claude structured-output fallback behavior: [claude.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/claude.py:289).
- Codex structured-output retry/fallback behavior: [codex.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/codex.py:373).
- Claude pool structured-output retry/fallback behavior: [claude_pool.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/claude_pool.py:624).

## Current Failure Mode From `8ac124d6`

Runtime failures, provider errors, stale context, and task output evidence were
mixed into the same flow that later made verify, repair, and checkpoint
decisions. A provider failure could crash the resumed workflow rather than
becoming a typed retryable runtime failure with a stable resume point.

The legacy loop also treats `ImplementationResult` as both runtime output and
resume authority. The same function builds prompts from task text, chooses
runtime actor metadata, retries crashes, mutates mismatched `task_id`, enriches
fallback file metadata, writes `dag-task:*`, and then proceeds toward commit and
checkpoint. That makes it impossible to tell on resume whether a marker means
"runtime produced a summary", "patch was captured", "contracts passed", or
"group integration is complete."

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/dispatcher.py`.

```python
from typing import Any, Literal

from pydantic import BaseModel, Field

DispatcherState = Literal[
    "requested",
    "attempt_started",
    "context_prepared",
    "runtime_invoking",
    "runtime_returned",
    "patch_capturing",
    "output_normalizing",
    "evidence_recording",
    "succeeded",
    "failed",
    "cancelled",
    "incomplete",
]

RuntimeTerminalReason = Literal[
    "completed",
    "cancelled",
    "provider_error",
    "timeout",
    "watchdog_stall",
    "process_failed",
    "prompt_too_large",
    "context_materialization_failed",
    "structured_output_invalid",
    "sandbox_binding_failed",
    "patch_capture_failed",
]

class ActorMetadata(BaseModel):
    actor_id: str
    actor_name: str
    actor_role: str
    runtime: Literal["claude", "codex", "claude_pool"]
    runtime_policy: str
    runtime_policy_digest: str
    model: str | None = None
    tool_profile: str
    sandbox_required: bool = True
    approval_profile: str = "no_canonical_writes"
    metadata_digest: str

class DispatchRetryIdentity(BaseModel):
    retry: int
    dispatch_retry_id: str
    retry_of_attempt_id: int | None = None
    failure_retry_of_id: int | None = None
    route_decision_id: int | None = None
    route_request_id: int | None = None

class DispatchRequest(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    task_id: str
    task_name: str
    retry: int
    retry_identity: DispatchRetryIdentity
    contract_ids: list[int]
    sandbox_id: str
    workspace_snapshot_ids: list[int]
    base_commit_by_repo: dict[str, str]
    runtime_policy: str
    runtime_policy_digest: str
    actor_role: str
    actor_metadata: ActorMetadata
    prior_evidence_ids: list[int]
    cancellation_token: str | None = None
    request_digest: str
    idempotency_key: str

class PromptContextBundle(BaseModel):
    prompt_ref: int
    prompt_sha256: str
    prompt_summary: str
    context_file_refs: list[int]
    context_file_paths: list[str]
    context_sha256: str
    included_contract_ids: list[int]
    included_evidence_ids: list[int]
    excluded_evidence_ids: list[int]
    truncation_notes: list[str]

class RuntimeInvocationRequest(BaseModel):
    attempt_id: int
    invocation_id: str
    runtime: Literal["claude", "codex", "claude_pool"]
    actor_name: str
    actor_role: str
    actor_metadata: ActorMetadata
    workspace_binding: RuntimeWorkspaceBinding
    prompt: str
    prompt_ref: int
    output_schema: str
    output_schema_digest: str
    output_type_name: str
    timeout_seconds: int
    retry_within_invocation: bool = True
    cancellation_token: str | None = None
    metadata: dict[str, Any]

class RuntimeInvocationResponse(BaseModel):
    invocation_id: str
    status: Literal["completed", "failed", "cancelled"]
    terminal_reason: RuntimeTerminalReason
    process_started: bool = False
    structured_output: dict[str, Any] | None
    raw_text: str | None
    raw_artifact_id: int | None
    provider_request_id: str | None
    provider_error_code: str | None
    stdout_artifact_id: int | None = None
    stderr_artifact_id: int | None = None
    adapter_retry_ids: list[str] = Field(default_factory=list)
    adapter_retry_count: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int

class StructuredOutputRecord(BaseModel):
    evidence_id: int
    schema_name: str
    schema_digest: str
    valid: bool
    original_payload: dict[str, Any] | None
    normalized_payload: dict[str, Any] | None
    validation_errors: list[str]
    corrected_fields: dict[str, Any]
    task_id_matches_request: bool

class RuntimeFailureRecord(BaseModel):
    failure_id: int
    failure_class: Literal[
        "runtime_provider",
        "runtime_timeout",
        "runtime_cancelled",
        "runtime_context",
        "runtime_structured_output",
        "sandbox_binding",
        "sandbox_capture",
        "dispatcher_internal",
    ]
    failure_type: str
    retryable: bool
    deterministic: bool
    operator_required: bool
    provider_request_id: str | None
    evidence_ids: list[int]
    signature_hash: str

class DispatchOutcome(BaseModel):
    attempt_id: int
    state: DispatcherState
    status: Literal["succeeded", "failed", "cancelled", "incomplete"]
    runtime_terminal_reason: RuntimeTerminalReason | None
    structured_result_evidence_id: int | None
    raw_text_ref: int | None
    patch_summary_ids: list[int]
    compatibility_artifact_ids: list[int]
    runtime_failure_id: int | None
    typed_failure_id: int | None
    idempotency_key: str

class RuntimeDispatcher:
    async def dispatch(self, request: DispatchRequest) -> DispatchOutcome: ...
```

Dispatcher dependencies are injected ports, not global calls:

- `ExecutionControlStore` starts and finishes attempts, stores evidence, records
  typed failures, and writes compatibility projections.
- `SandboxRunner` validates the lease, binds the runtime, and captures patches.
- `RuntimeClient` wraps `runner.run(Ask(...))` and returns
  `RuntimeInvocationResponse` instead of raising provider/runtime errors across
  the dispatcher boundary.
- `ContractPromptBuilder` renders task contracts and bounded context into a
  prompt/context bundle.
- `Clock`, `IdempotencyKeyFactory`, and `CancellationRegistry` make retries,
  recovery, and cancellation deterministic in tests.

## Boundary Ownership And Forbidden Side Effects

The dispatcher owns exactly one side-effect boundary: turning a task or repair
runtime request into durable attempt evidence. It may start and finish dispatch
attempts, store prompt/runtime/structured-output evidence, record typed failure
observations, project successful `dag-task:*` compatibility artifacts, bind a
sandbox runtime, invoke a runtime adapter, and ask the sandbox runner to capture
patch evidence.

The dispatcher must not own downstream decisions or canonical mutation:

- No canonical repo write, checkout, rebase, merge, commit, hook retry, or
  no-dirty proof.
- No `dag-group:*`, `dag-merge-proof:*`, or `dag-commit-proof:*` projection.
- No merge queue enqueue, claim, apply, checkpoint, or retry-supersession call.
- No failure route decision, route budget reservation, repair request creation,
  repair dispatch, or scheduler/regroup mutation.
- No compatibility artifact rewrite except the bounded `dag-task:{task_id}`
  projection produced from a successful dispatch attempt.

Port shape enforces this boundary. The dispatcher receives a narrow
`DispatchJournalPort`/`ExecutionControlStore` facade with only dispatch attempt,
evidence, typed-failure observation, and task-result projection methods. It does
not receive the full journal interface that exposes `enqueue_merge`,
`project_group_checkpoint`, `reserve_route_budget`, or repair-request helpers.
The runtime adapter receives only `RuntimeInvocationRequest`; it does not
receive the store, merge queue, router, or canonical workspace handles.

Typed failure recording is not route ownership. The dispatcher can insert a
`FailureObservation(source='dispatcher', ...)` or equivalent typed failure row
so Slice 07 can route later, but it cannot call `FailureRouter.decide`,
`mark_route_started`, or any `RouteExecutor` method. Coordinators consume
`DispatchOutcome.typed_failure_id` and hand it to the router after the dispatch
attempt is terminal.

## Dispatcher State Machine

The dispatcher is a small durable state machine around one task attempt. Every
transition writes an event-like attempt update or evidence row before moving to
the next side effect.

| From | Event | To | Durable write before transition |
| --- | --- | --- | --- |
| `requested` | request accepted and idempotency key claimed | `attempt_started` | `execution_attempts(status='started')` with request digest |
| `attempt_started` | sandbox lease and contracts revalidated | `context_prepared` | prompt/context evidence and bounded request payload |
| `context_prepared` | runtime binding returned | `runtime_invoking` | runtime binding evidence and invocation metadata |
| `runtime_invoking` | runtime completed normally | `runtime_returned` | raw output artifact/evidence and invocation response payload |
| `runtime_invoking` | runtime failed or timed out after process start | `patch_capturing` | raw stderr/stdout when available, runtime failure evidence, and partial-capture request |
| `runtime_invoking` | runtime failed before process start | `evidence_recording` | runtime failure evidence and no patch-capture request |
| `runtime_invoking` | cancellation observed | `cancelled` | attempt finish with cancellation reason |
| `runtime_returned` | sandbox patch capture starts | `patch_capturing` | lease status update through `SandboxRunner` |
| `patch_capturing` | patch capture succeeds | `output_normalizing` | patch summary evidence ids |
| `patch_capturing` | patch capture fails after runtime return | `incomplete` | runtime output evidence plus `sandbox_capture` failure |
| `output_normalizing` | structured output validates | `evidence_recording` | structured output evidence with original and normalized payloads |
| `output_normalizing` | structured output is invalid after runtime adapter retries | `evidence_recording` | invalid structured-output evidence and typed failure |
| `evidence_recording` | success projection transaction completes | `succeeded` | attempt finish and bounded `dag-task:*` projection |
| `evidence_recording` | failure projection transaction completes | `failed` | attempt finish and typed failure id |

Terminal states are `succeeded`, `failed`, `cancelled`, and `incomplete`.
Recovery scans `execution_attempts(status='started')` and re-enters at the last
durable state. It never re-invokes a runtime if a completed invocation response
or raw output evidence already exists for the same invocation id.

The last durable state is `execution_attempts.dispatcher_state`, updated in the
same transaction as the durable write named in the table above. Evidence rows may
explain the transition, but the resume cursor is the state column; recovery must
not infer state by scanning prose or compatibility artifacts.

State invariants:

- `succeeded` requires structured-output evidence, raw-output evidence or an
  explicit empty-output marker, and successful sandbox patch capture. The patch
  can be empty, but it must be captured and represented.
- `failed` requires a typed failure id linked to the attempt.
- `incomplete` is reserved for split-brain boundary failures where the runtime
  may have changed the sandbox but the dispatcher could not capture or persist
  enough evidence to safely classify success. The router receives this as
  `sandbox_capture` or `dispatcher_internal`; no task success projection is
  written.
- `cancelled` is terminal and non-successful. Cancellation does not decrement a
  product retry budget unless the failure router later decides it should.
- The dispatcher does not transition to merge, verify, repair, checkpoint, or
  commit states.

## Runtime Request/Response Contracts

`DispatchRequest` is the outer durable contract. It contains only stable ids,
digests, policy names, and bounded metadata. It must not contain raw prompt
text, raw prior artifacts, canonical cwd values, secrets, or latest-by-key
artifact reads. Actor metadata in this request describes the runtime identity
and policy that will be used; it is evidence for analysis and idempotency, not
permission to widen filesystem authority.

`RuntimeInvocationRequest` is the adapter contract. It is created only after the
attempt row exists and after `SandboxRunner.bind_runtime` returns a
`RuntimeWorkspaceBinding`. Its `prompt` may be large, but the dispatcher stores
the prompt as evidence first and passes the exact same prompt bytes to the
runtime. The `prompt_sha256`, `context_sha256`, output schema name, runtime
policy digest, contract ids, sandbox id, and workspace snapshot ids are part of
the attempt input digest. The adapter receives actor metadata as immutable
metadata and must use the sandbox binding, not actor metadata, as the authority
for cwd and writable roots.

`RuntimeInvocationResponse` is exception-free at the dispatcher boundary.
Runtime adapters catch provider exceptions, liveness watchdog failures, schema
retry exhaustion, process exits, and cancellation signals and convert them into
a response with `status`, `terminal_reason`, and provider metadata. Unhandled
Python exceptions inside the dispatcher itself become `dispatcher_internal`
typed failures.

The response contract distinguishes:

- `status='completed'` and `terminal_reason='completed'`: runtime returned and
  structured output may be normalized.
- `status='failed'` with `provider_error`, `timeout`, `watchdog_stall`, or
  `process_failed`: runtime did not produce a trusted structured result.
- `status='failed'` with `structured_output_invalid`: the runtime may have done
  filesystem work, but the result payload cannot satisfy the output contract.
- `status='cancelled'`: the cancellation token was observed before a terminal
  success.
- `process_started=False`: the runtime was not allowed to mutate the sandbox,
  so recovery does not capture patches unless the sandbox runner reports a
  started binding independently.
- `process_started=True`: the dispatcher attempts patch capture even when the
  terminal reason is a provider error, timeout, watchdog stall, process failure,
  or structured-output failure.

Runtime adapters may continue to do narrow in-invocation retries that are
already provider-local, such as structured-output correction prompts or watchdog
retry attempts, but those sub-attempts are stored in the invocation metadata.
The dispatcher does not hide a failed task attempt by looping through new task
attempts internally.

## Prompt And Context Boundaries

Prompt construction moves out of `_implement_dag` and into
`ContractPromptBuilder`. The prompt is built from typed inputs only:

1. Active task contract ids for the request.
2. Task id, task name, repo id, and bounded task description from the effective
   DAG artifact selected for this run.
3. Contract required, allowed, forbidden, generated, and read-only path rules.
4. Sandbox-relative repo roots and a clear "do not leave this sandbox" runtime
   directive.
5. Prior evidence ids explicitly listed in `DispatchRequest.prior_evidence_ids`,
   rendered through bounded summaries or context files.
6. Handover summaries generated from typed attempt/evidence rows, not from
   unbounded legacy latest-by-key artifact reads.

Context files are materialized under the sandbox or feature runtime evidence
area, not under canonical repositories. When context exceeds the inline
threshold, the builder writes read-only context files and includes relative
paths in the prompt. Each context file path, artifact id, and content digest is
recorded in `PromptContextBundle`. If context materialization fails, the
dispatcher records `runtime_context/context_materialization_failed` and never
invokes the runtime.

Prompt exclusions are explicit. Evidence omitted because it is too large,
stale, superseded, outside the task contract, or not in the request evidence set
is listed in `excluded_evidence_ids` with a bounded reason. This prevents a
resumed dispatcher from silently changing prompt context by rereading latest
artifacts.

The prompt must not ask the implementer to commit, checkpoint, update
`dag-task:*`, modify canonical paths, or repair unrelated tasks. Those actions
belong to the merge queue, journal projection layer, and failure router.

## Retry And Idempotency

Retry is split into three layers:

1. Runtime-local retries: provider SDK retries, structured-output correction,
   and liveness retry inside the runtime adapter. These stay within one
   `RuntimeInvocationRequest` and are summarized in invocation metadata.
2. Dispatcher attempt retry: a new `DispatchRequest.retry` value creates a new
   execution attempt, usually after the failure router returns
   `retry_dispatch`.
3. Workflow retry budget: the failure router owns budgets by failure class and
   decides whether another dispatch attempt is allowed.

The dispatcher idempotency key is computed from:

- feature id, DAG sha, group idx, task id, retry, and actor role;
- sorted contract ids and their digests;
- sandbox id, base commits, and workspace snapshot ids;
- runtime policy digest and output schema name;
- prompt/context digest and explicit prior evidence ids;
- `retry_identity.failure_retry_of_id`, `route_decision_id`, and
  `route_request_id` when the retry was authorized by Slice 07.

Same key, same request digest:

- If no terminal attempt exists, return or resume the existing started attempt.
- If a terminal attempt exists, return the stored `DispatchOutcome` without
  invoking the runtime again.
- If the stored request digest differs, raise an idempotency conflict and record
  `dispatcher_internal/idempotency_conflict`.

Concurrent duplicate dispatches use the `ExecutionControlStore.start_attempt`
unique idempotency key as the fence. Only the winner may bind the sandbox and
invoke the runtime. Losers poll the existing attempt and return its terminal
outcome.

An attempt retry never reuses a captured sandbox. Repairs and retries allocate a
new sandbox lease with a new sandbox id. The retry request may reference prior
attempt evidence, but filesystem authority comes only from the new lease and
recorded base snapshots.

Retry identity invariants:

- `DispatchRequest.retry` and `DispatchRetryIdentity.retry` must match. The
  duplicate value is intentional during migration: legacy callers can still log
  the numeric retry while typed readers use `retry_identity`.
- `dispatch_retry_id` is stable for one routed retry request and is included in
  the attempt payload, runtime invocation metadata, structured-output evidence,
  and typed failure payload. It is not the runtime provider request id.
- `route_decision_id` and `route_request_id` are input references only. Their
  presence proves a retry was authorized upstream; it does not let the
  dispatcher reserve budget, choose a route, or create repair work.
- `retry_of_attempt_id` links to the prior dispatch attempt when retrying a
  runtime/provider/context failure. It must not point at a merge, verify,
  checkpoint, or repair attempt.
- `failure_retry_of_id` links to the typed failure being retried. The
  dispatcher copies it into any new failure signature so the router can count
  repeated failures under the same budget key.
- Runtime-local retries use `adapter_retry_ids` inside one invocation response.
  They never increment `DispatchRequest.retry`, allocate a new sandbox, or
  produce a second `execution_attempts` row.

Actor metadata invariants:

- `ActorMetadata.metadata_digest` covers actor name, role, runtime, runtime
  policy digest, model, tool profile, sandbox requirement, and approval profile.
  A same-key request with different actor metadata is an idempotency conflict.
- Actor metadata is bounded and secret-free. API keys, raw provider headers,
  local HOME paths, and canonical cwd strings are not stored in actor metadata.
- Runtime adapters may add provider request ids, trace refs, and usage data to
  `RuntimeInvocationResponse`, but they must not mutate the actor metadata after
  the invocation starts.
- `sandbox_required=True` is mandatory for implementation and repair actors. A
  runtime policy that requires canonical write access or sandbox bypass records
  `sandbox_binding/runtime_workspace_binding_failed` before provider invocation.
- Retry dashboards and supervisor summaries read actor/runtime metadata from
  typed rows, not Slack text, raw transcripts, or heuristic artifact bodies.

## Structured-Output Handling

The dispatcher treats runtime structured output as attempt evidence, not
authority over product state.

Normalization rules:

1. Store raw text, raw structured payload, provider metadata, stdout/stderr
   refs, and validation errors before any compatibility projection.
2. Validate the payload against `ImplementationResult` for implementation task
   dispatch. Other dispatch kinds must supply a named schema and validator.
3. Preserve the original payload exactly in `StructuredOutputRecord`.
4. If `task_id` differs from `DispatchRequest.task_id`, store
   `task_id_matches_request=False`, add a corrected `task_id` only to the
   normalized payload, and record the mismatch in `corrected_fields`.
5. If file metadata is empty, do not infer success from the summary. The
   dispatcher may derive bounded display file lists from patch summaries, but
   that derived metadata is marked as host-derived in the normalized payload.
6. Self-reported `status='completed'` is not enough for downstream acceptance.
   Contracts, gates, and merge queue evidence decide acceptance.
7. A runtime fallback `ImplementationResult` synthesized because the runtime
   could not produce valid JSON is recorded as `valid=False` unless the adapter
   includes the original structured payload and it passes validation. It may be
   shown in diagnostics but must not project `dag-task:*` as success.
8. Malformed JSON, schema validation failure, missing required fields, or output
   with untrusted synthesized fields records
   `runtime_structured_output/malformed_structured_output`.

Compatibility projection:

- `dag-task:{task_id}` is written only by the dispatcher/journal projection
  transaction after attempt success evidence is durable.
- The projected artifact body is byte-equivalent to the current
  `ImplementationResult` JSON body. It does not include control-plane metadata.
  Attempt id, structured-result evidence id, patch summary ids, contract ids,
  runtime, sandbox id, and `projection_authority='dispatcher_attempt'` live in
  typed rows and `execution_artifact_projections.payload`.
- Projection is a resume aid for legacy readers and dashboards. It is not a
  merge proof, checkpoint proof, or contract verdict.
- Failed, cancelled, incomplete, or invalid-structured-output attempts do not
  write successful `dag-task:*`. They may write bounded diagnostic artifacts
  owned by the failure projection layer.

## Failure Classification Handoff

The dispatcher records typed failures with enough classification for the failure
router to make the next decision, but it does not decide the route.

Classification table:

| Dispatcher observation | Failure class | Retryable | Deterministic | Handoff notes |
| --- | --- | --- | --- | --- |
| Provider 429/5xx/transport error with request id | `runtime_provider` | yes | no | Include provider request id, runtime, attempt, and backoff hints. |
| Watchdog stall or timeout | `runtime_timeout` | yes | no | Include invocation id, timeout, and last heartbeat if available. |
| Prompt too large before runtime start | `runtime_context` / `prompt_too_large` | yes | yes | Router requests bounded context compaction through `retry_dispatch`, not product repair. |
| Context materialization failed with bounded input evidence | `runtime_context` / `context_materialization_failed` | true by policy | yes | Canonical route is `quiesce`; dispatcher must not locally retry or product-repair. |
| Context permission failure outside feature/sandbox roots | `runtime_context` / `context_permission_denied` | no | yes | Mark operator-required; automatic cleanup must not broaden permissions outside owned roots. |
| Runtime returns invalid structured output after adapter retries | `runtime_structured_output` / `malformed_structured_output` | yes | yes | Include raw output refs and schema validation errors. |
| Runtime cancelled by operator/workflow | `runtime_cancelled` | no | no | Router decides whether to resume or leave cancelled. |
| Runtime process exits nonzero without a trusted provider error | `runtime_provider` / `process_failed` | yes | no | Include exit code, stderr slice, runtime adapter, and sandbox lease evidence. |
| Sandbox binding refuses canonical or unsafe cwd | `sandbox_binding` / `runtime_workspace_binding_failed` | no | yes | Route through `quiesce`; never invent sandbox repair or product repair for unsafe cwd binding. |
| Patch capture command fails after runtime success | `sandbox_capture` / `patch_capture_failed` | yes | no | Mark attempt `incomplete`; preserve raw output and sandbox lease evidence. |
| Sandbox index is corrupt after runtime success | `sandbox_capture` / `sandbox_index_corrupt` | no | yes | Canonical route is `quiesce`; preserve sandbox and do not rerun product repair blindly. |
| Duplicate idempotency key with different digest | `dispatcher_internal` / `idempotency_conflict` | no | yes | Canonical route is `quiesce`; this is a control-plane consistency failure. |

Every `RuntimeFailureRecord` links to the primary evidence ids and stores a
signature hash based on failure class, type, runtime, provider code, task id,
contract ids, sandbox id, and stable error details. The router uses that
signature for budget accounting and duplicate suppression.

The dispatcher never converts a runtime/provider/sandbox failure into
`ImplementationResult(status='blocked')` as a success-shaped output. Legacy
blocked results are replaced by typed failures plus optional compatibility
diagnostics.

Runtime failure capture rules:

- Provider/runtime exceptions may be logged by the adapter, but they cross the
  dispatcher boundary only as `RuntimeInvocationResponse(status='failed', ...)`
  plus raw stdout/stderr/trace refs when available.
- If a failure occurs before prompt bytes are handed to the provider and before
  the sandbox process starts, the dispatcher records context/binding/runtime
  evidence and skips patch capture.
- If the provider accepted the request or the adapter started a runtime process,
  the dispatcher treats the sandbox as possibly changed. It asks
  `SandboxRunner.capture_patch` for diagnostic patch evidence before finishing
  the failed attempt.
- Diagnostic patch evidence from a failed runtime is never merge-admission
  authority. The merge queue can consume patch evidence only from successful
  dispatch attempts that later pass contracts and gates, or from explicit
  repair attempts routed by Slice 07.
- Timeout/watchdog cleanup is a runtime-adapter responsibility. The dispatcher
  records the terminal response, the final heartbeat/elapsed time when present,
  and the capture outcome; it does not kill arbitrary processes outside the
  sandbox lease.
- If runtime failure evidence cannot be persisted after the runtime has started,
  the attempt is `incomplete`, the sandbox is retained when possible, and resume
  must prefer recovery/recapture over rerunning the provider.

## Refactoring Steps

1. Add `dispatcher.py` with `RuntimeDispatcher`, request/response models,
   state-transition helpers, and dependency ports. Keep it free of commit,
   checkpoint, verifier, merge queue, and repair-route imports.
2. Add `runtime_client.py` or an adapter class near the dispatcher that wraps
   `runner.run(Ask(...))` and converts runtime exceptions into
   `RuntimeInvocationResponse`. The adapter may delegate to existing Claude,
   Codex, and Claude pool structured-output behavior, but it must not let
   provider errors escape the dispatcher.
3. Add `prompt_context.py` with `ContractPromptBuilder`. Replace direct calls to
   `_build_task_prompt` in the implementation and enhancement dispatch loops
   with prompt bundles built from task contracts and explicit evidence ids.
4. Extend `ExecutionControlStore` with dispatcher-specific helpers:
   `start_dispatch_attempt`, `record_prompt_context`,
   `record_runtime_invocation`, `record_structured_output`,
   `record_runtime_failure`, `finish_dispatch_attempt`, and
   `project_task_result_from_attempt`. These are thin typed wrappers around the
   Slice 01 journal methods.
5. Expose only those helpers to `RuntimeDispatcher` through a narrow dispatch
   journal port. Do not inject the full store, merge queue, failure router, git
   service, or checkpoint projector.
6. Make dispatcher start the attempt row before sandbox binding. Request
   payload stores ids, digests, runtime policy, actor role, and bounded metadata;
   raw prompt text is stored as evidence/artifact, not inline in the attempt
   payload.
7. Require a `RuntimeWorkspaceBinding` from `SandboxRunner.bind_runtime` before
   creating `RuntimeInvocationRequest`. Refuse any binding whose cwd or writable
   roots point at canonical repos or unresolved aliases.
8. Invoke the runtime once per dispatcher attempt. Runtime-local retries are
   represented as sub-invocation metadata; task-attempt retries are created only
   by a later `DispatchRequest` with an incremented retry.
9. Capture the sandbox patch after every runtime terminal response except
   pre-start context/binding failures and explicit cancellation before process
   start. If the runtime failed after process start, capture partial patches as
   diagnostic evidence.
10. Normalize structured output after patch capture. Store original payload,
   normalized payload, validation errors, host-derived fields, and task-id
   corrections as typed evidence.
11. Finish the attempt in one projection transaction. Success writes structured
    evidence, patch evidence, attempt finish, and bounded `dag-task:*`
    projection. Failure writes typed failure, attempt finish, and optional
    diagnostic projection. The transaction must be idempotent.
12. Replace both legacy task loops with dispatch requests: the primary
    implementation loop around the existing `TASK_MAX_RETRIES` block and the
    enhancement loop around the duplicate `TASK_MAX_RETRIES` block. The DAG
    executor gathers `DispatchOutcome` values and hands successful attempt
    evidence to gates/merge queue; it no longer mutates `ImplementationResult`
    or writes `dag-task:*` directly.
13. Delete direct Slack warning logic from the dispatch loop. Runtime struggle
    notifications, if still needed, are emitted by dashboard/supervisor readers
    from typed failure rows and retry budgets.

## Persistence And Artifact Compatibility

- Successful structured result can project `dag-task:{task_id}` for
  compatibility, but dispatcher/journal is the sole writer for this key and it
  is marked task-attempt evidence only. The artifact body remains
  byte-equivalent to `ImplementationResult`; control-plane metadata lives in
  typed rows and projection-link payloads.
- Merge queue must not rewrite `dag-task:*`; it writes merge/commit proof
  artifacts and `dag-group:*`.
- Runtime failures write typed failures and optional compatibility artifacts for
  current supervisor/dashboard readers. They do not write blocked
  `ImplementationResult` rows as if a task completed.
- Actor/runtime metadata must be stored in typed rows so retry analysis does not
  depend on Slack logs or raw runtime transcripts. The metadata includes
  `dispatch_retry_id`, `invocation_id`, runtime policy digest, actor metadata
  digest, adapter retry ids, and provider request id when available.
- Prompt/context evidence uses bounded artifacts with spill-backed slices for
  large context. Attempt payloads store only ids, digests, and summaries.
- Raw runtime output is evidence kind `raw_output`; structured output is
  evidence kind `structured_result`; invocation metadata is evidence kind
  `runtime_invocation`; patch capture remains `sandbox_patch_summary` from Slice
  04.
- Compatibility projection idempotency is based on attempt id and evidence ids,
  not only `task_id`. A newer retry may supersede an older `dag-task:*`
  projection, but projection audit rows preserve both.
- Legacy readers that load `dag-task:*` must see the exact expected
  `ImplementationResult` shape only. Extra bounded metadata lives in typed
  attempt/evidence rows and `execution_artifact_projections.payload`; new readers
  must use typed rows for authority.
- On resume, dispatcher first reconstructs from typed rows. It may import legacy
  `dag-task:*` only when no typed execution rows exist for the feature, and that
  import is a separate legacy reconstruction path owned by Slice 01.

## Resume And Recovery Safety

Resume reads `execution_attempts`, typed evidence rows, typed failures,
projection links, and sandbox lease state. It must not infer dispatch progress
from `dag-task:*` bodies, Slack messages, raw transcript text, latest-by-key
artifact lookup, or canonical repository state.

Recovery by state:

| Durable state | Recovery behavior |
| --- | --- |
| `attempt_started` | Rebuild or find prompt/context evidence using the original request digest, then bind the sandbox if no binding evidence exists. |
| `context_prepared` | Reuse the stored prompt/context bundle exactly; do not reread latest evidence or regenerate a different prompt. |
| `runtime_invoking` with no terminal invocation evidence | Query runtime adapter recovery by `invocation_id` and sandbox lease heartbeat. If the adapter proves the provider/process never started, mark retryable failure; if start state is unknown, retain sandbox and mark `incomplete` instead of invoking a second provider call. |
| `runtime_invoking` with terminal invocation evidence | Continue from the stored response; never re-invoke the same `invocation_id`. |
| `runtime_returned` | Capture the sandbox patch once for the original lease. Duplicate workers must share the same capture idempotency key. |
| `patch_capturing` | If patch evidence exists, continue to normalization. If capture failed and the sandbox is retained, let Slice 07 decide `retry_sandbox_capture`; the dispatcher does not rerun runtime work. |
| `output_normalizing` | Re-run only pure schema validation/normalization from stored raw output, schema digest, and patch summary ids. |
| `evidence_recording` | Complete the idempotent finish/projection transaction or return the existing terminal outcome. |
| terminal states | Return the stored `DispatchOutcome`; downstream gates, router, and merge queue decide what happens next. |

Resume invariants:

- A completed invocation response, raw output artifact, or provider request id
  for an `invocation_id` is a fence against another runtime call for the same
  attempt.
- A successful `dag-task:*` projection is a compatibility mirror of a succeeded
  dispatch attempt. It is not a group checkpoint and does not cause resume to
  skip gates, merge, commit, no-dirty proof, or `dag-group:*` projection.
- A typed failure id on the attempt is a handoff token. Resume may return it to
  the coordinator, but the dispatcher does not call the router or start repair.
- `incomplete` attempts are never silently promoted to `failed` or `succeeded`
  from legacy artifacts. They require typed recovery evidence, sandbox
  recapture, or operator/quiesce routing.
- Retry resume uses `DispatchRetryIdentity` and the route request id to recreate
  the same retry attempt. It does not allocate a second retry id or spend a new
  route budget slot.

## Edge Cases And Failure Handling

- Runtime returns malformed structured output: record raw output, validation
  errors, `runtime_structured_output/malformed_structured_output`, and no successful
  `dag-task:*` projection.
- Provider API error: record retryable `runtime_provider` failure with provider
  request id, provider code, runtime, and invocation id when available.
- Runtime watchdog stall: finish the runtime invocation as failed, capture any
  partial sandbox patch if process start is known, and hand off
  `runtime_timeout`.
- Prompt/context too large before runtime start: record deterministic
  `runtime_context/prompt_too_large`; do not spend provider retries.
- Context file materialization fails: record `runtime_context` and preserve any
  partially written context artifact refs for cleanup diagnostics.
- Runtime cancellation: record cancelled attempt and do not project task
  success. If cancellation races with runtime completion, the first durable
  terminal invocation response wins.
- Duplicate retry: idempotency key prevents duplicate attempts for the same
  dispatch request. Same key with different digest records an idempotency
  conflict instead of choosing one payload.
- Sandbox patch capture fails after runtime success: mark attempt `incomplete`,
  preserve raw output and invocation evidence, and do not project task success.
- Runtime writes no files but returns completed: success can be recorded as
  attempt evidence only if patch capture succeeded. Contract/gate layers decide
  whether an empty patch is acceptable.
- Runtime reports files outside contract: store the self-report but rely on
  patch summary and contract verdicts. The dispatcher may mark the normalized
  result with `outside_contract_self_reported_paths`; it does not repair.
- Runtime reports a different `task_id`: normalize for compatibility, preserve
  the mismatch as evidence, and let gates/router use the mismatch if it matters.
- Sandbox binding points to canonical cwd or symlinked root: fail closed with
  `sandbox_binding/runtime_workspace_binding_failed` before runtime invocation.
- Dispatcher crashes after runtime return but before patch capture: recovery
  resumes at `runtime_returned`, captures the existing sandbox once, and records
  evidence under the original attempt id.
- Dispatcher crashes after projection write but before returning outcome:
  duplicate dispatch returns the stored terminal outcome by idempotency key.
- Typed failure recording is temporarily unavailable: finish attempt as
  `incomplete` only if the typed failure cannot be recorded in the same
  transaction. Do not lose raw runtime evidence and do not call the router as a
  fallback.

## Tests

Unit tests for state and persistence:

- `test_dispatch_starts_attempt_before_side_effects`: dispatcher writes
  `execution_attempts(status='started')` before prompt materialization, sandbox
  binding, or runtime invocation.
- `test_dispatch_state_machine_rejects_invalid_transition`: direct
  `runtime_invoking -> succeeded` without patch/structured evidence is rejected.
- `test_dispatch_finish_success_transaction_projects_once`: success finish writes
  structured evidence, raw output evidence, patch evidence refs, attempt finish,
  and one `dag-task:*` projection.
- `test_dispatch_failure_transaction_records_typed_failure`: provider failure
  writes attempt finish and typed failure in one idempotent transaction.
- `test_dispatch_incomplete_when_patch_capture_fails_after_runtime_success`:
  raw runtime output is preserved, attempt is `incomplete`, no success
  projection is written.
- `test_dispatcher_port_excludes_route_and_checkpoint_methods`: dispatcher is
  constructed with a narrow journal port that has no merge, route, commit, or
  checkpoint APIs.

Runtime contract tests:

- `test_runtime_client_converts_provider_exception_to_response`: fake runtime
  raises provider error; dispatcher receives `RuntimeInvocationResponse` and the
  workflow does not crash.
- `test_runtime_client_records_provider_request_id`: provider request id/code
  are stored in failure payload and signature hash material.
- `test_runtime_client_timeout_is_retryable_runtime_timeout`: watchdog timeout
  maps to `runtime_timeout` and not product repair.
- `test_runtime_cancellation_wins_before_process_start`: cancelled attempts
  finish as cancelled and do not capture or project success.
- `test_runtime_started_failure_captures_diagnostic_patch_only`: failed runtime
  with `process_started=True` captures patch evidence without making it merge
  admissible.
- `test_actor_metadata_digest_is_part_of_request_digest`: changing runtime
  actor metadata under the same idempotency key records an idempotency conflict.

Prompt/context boundary tests:

- `test_prompt_uses_contract_block_not_legacy_latest_artifacts`: prompt builder
  renders contract ids/path rules and only explicit prior evidence ids.
- `test_large_context_offloads_under_sandbox_context_dir`: large context writes
  read-only files under sandbox/runtime evidence paths with stable digests.
- `test_context_materialization_failure_skips_runtime`: builder failure records
  `runtime_context` and runtime client is not called.
- `test_prompt_digest_changes_when_evidence_set_changes`: adding/removing prior
  evidence changes the request digest and idempotency conflict behavior is
  exercised.

Structured-output tests:

- `test_structured_output_original_and_normalized_payloads_are_both_stored`:
  original runtime payload is preserved and host corrections are explicit.
- `test_task_id_mismatch_is_corrected_only_in_normalized_payload`: mismatch is
  evidence, compatibility projection uses expected task id, original payload is
  unchanged.
- `test_synthesized_runtime_fallback_is_not_success_projection`: fallback
  `ImplementationResult` generated after JSON exhaustion records invalid
  structured output and no `dag-task:*`.
- `test_empty_file_metadata_uses_patch_summary_as_host_derived_display_only`:
  dispatcher does not treat inferred file lists as runtime authority.

Retry/idempotency tests:

- `test_duplicate_inflight_dispatch_invokes_runtime_once`: concurrent same-key
  calls share one attempt and one runtime invocation.
- `test_duplicate_terminal_dispatch_returns_stored_outcome`: same request after
  terminal success/failure does not re-run runtime or patch capture.
- `test_same_key_different_digest_records_conflict`: altered prompt/context with
  same key fails closed.
- `test_retry_request_allocates_new_attempt_and_requires_new_sandbox`: retry+1
  cannot reuse the previous captured sandbox lease.
- `test_route_request_ids_are_inputs_not_router_calls`: a dispatch retry can
  cite `route_decision_id` and `route_request_id`, but dispatcher never reserves
  route budget or creates repair requests.

Resume tests:

- `test_resume_runtime_invoking_with_terminal_response_does_not_reinvoke`: stored
  invocation response fences duplicate provider calls.
- `test_resume_runtime_invoking_unknown_process_start_marks_incomplete`: unknown
  provider/process start state retains sandbox evidence and avoids duplicate
  runtime invocation.
- `test_resume_runtime_returned_captures_patch_once`: duplicate recovery workers
  share the patch-capture idempotency key.
- `test_resume_output_normalizing_is_pure`: normalization can be replayed from
  raw output, schema digest, and patch summary ids without reading latest
  artifacts.

Integration tests:

- `test_primary_dag_loop_uses_dispatcher_and_does_not_write_dag_task_directly`.
- `test_enhancement_loop_uses_dispatcher_and_does_not_write_dag_task_directly`.
- `test_dispatcher_cannot_call_commit_checkpoint_or_merge_queue`: inject ports
  without those APIs and assert no imports/calls occur.
- `test_dispatcher_cannot_call_failure_router_or_route_executor`: dispatcher
  returns typed failure ids but never calls route decision or repair APIs.
- `test_resume_after_dispatch_success_before_merge_checkpoint_is_not_group_done`:
  typed task attempt exists but merge/checkpoint evidence is absent, so resume
  continues at gate/merge work.
- `test_runtime_provider_failure_routes_through_typed_failure_router_input`:
  dispatcher records the failure payload expected by Slice 07, with no product
  repair result.
- `test_partial_patch_after_runtime_failure_is_diagnostic_only`: failed runtime
  may capture patch evidence, but no merge queue item is enqueued by dispatcher.

End-to-end atomic feature test:

- `test_atomic_execution_control_dispatch_path`: with journal, contracts,
  sandbox runner, dispatcher, failure router handoff, and merge queue admission
  enabled together, one task runs in a sandbox, records attempt evidence,
  projects compatibility `dag-task:*`, passes contract/gate admission, and does
  not mark the group complete until merge/checkpoint proof exists.

## Acceptance Criteria

- `_implement_dag` no longer owns runtime invocation logic directly.
- Dispatcher output is sufficient for gates and failure router to decide next steps.
- Runtime failures are resumable evidence, not uncaught workflow failures.
- `dag-task:*` projection timing is unambiguous: dispatcher/journal owns attempt projection, merge queue owns canonical integration proof and checkpoint.
- Prompt/context input is digest-addressed and cannot drift through legacy
  latest-by-key artifact reads.
- The dispatcher cannot import or call commit, checkpoint, merge queue claim, or
  route-decision/repair APIs.
- Actor metadata and retry identity are durable, bounded, secret-free, and part
  of request digest/idempotency checks.
- Runtime provider, timeout, context, structured-output, sandbox binding, and
  patch-capture failures are typed and linked to evidence ids.
- Replaying the same dispatch request is safe under crash, resume, and
  concurrent duplicate worker execution.
- Empty or self-reported runtime success cannot bypass sandbox patch capture,
  contract validation, gates, merge queue, or checkpoint proof.

## Rollout/Rollback Notes

Land this as one atomic execution-control feature, not as a phased production
rollout. The landing branch includes the journal methods, dispatcher, runtime
adapter, prompt/context builder, sandbox binding integration, failure handoff,
legacy loop replacement, compatibility projections, and tests in the same
change set. The old direct `runner.run(Ask(...))` task path is removed from the
production dispatch path when the feature lands.

Compatibility artifacts still exist for legacy readers, but compatibility is
not a staged execution mode. It is a projection layer written from typed
attempts.

Rollback is a whole-feature code revert before deployment if the atomic test
matrix fails. If rollback is required after deployment, revert dispatcher usage
and leave additive typed rows/artifacts in place as inert evidence; do not
partially run typed dispatcher for some groups and legacy dispatcher for others
inside the same feature execution.

## Cross-Slice Dependencies

- Slice 1 stores attempts and evidence.
- Slice 3 supplies contracts.
- Slice 4 supplies sandbox bindings and patch capture.
- Slice 7 consumes dispatcher failures and produces retry/repair requests; the
  dispatcher only cites those ids on incoming retry dispatches.
- Slice 8 consumes successful patch evidence and approved contracts, but the
  dispatcher never enqueues or applies merge items directly.
- Slice 10 reads dispatcher attempts, failures, and projections for supervisor
  visibility without becoming route authority.
