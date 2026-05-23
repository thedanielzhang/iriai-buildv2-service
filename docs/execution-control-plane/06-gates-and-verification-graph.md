# 06. Gates And Verification Graph

## Objective

Represent deterministic preflight checks, model verifier calls, expanded lenses,
and raw gate approvals as a typed evidence graph. Gate approval becomes an
explicit prerequisite for merge and checkpoint.

This slice lands as one atomic feature: the graph schema, writers, readers,
compatibility projections, merge/checkpoint enforcement, failure routing, and
tests ship together. There is no phased production rollout and no supported
state where legacy verifier artifacts can approve a merge without graph-backed
gate evidence.

## Current Code Citations

- Verify/fix loop entry: [_verify_and_fix_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2925).
- Initial verifier write: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3026) and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:3075).
- Reverify path: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4106).
- Current deterministic preflight: [_run_dag_group_preflight](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:14135).
- Expanded lens definitions: [_dag_verify_lens_specs](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:6257).
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
  implementation report at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2672),
  and notification at
  [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:2721).
- Current post-test DAG-completion guard:
  [_raise_if_dag_incomplete_before_post_test](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/post_test_observation.py:51)
  and
  [PostTestObservationPhase.execute](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/post_test_observation.py:695).
- Verdict model: [Verdict](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:858).
- Commit/checkpoint block tests: [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:444) and [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:2371).
- Expanded verify tests: [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:1331).

## Current Failure Mode From `8ac124d6`

Verifier context could include stale paths or stale task evidence. Some failures
were deterministic workspace/projection failures but still reached model verifier
or RCA paths. This wasted cycles and blurred product defects with pipeline drag.

The missing invariant is: every mergeable group needs a reproducible chain from
candidate inputs to deterministic gates, raw verifier verdict, expanded lens
verdicts, aggregate verdict, and merge queue acceptance. Today the workflow can
infer that chain from `dag-verify:*` and related artifacts, but it cannot prove
node ordering, source freshness, bounded reads, or why a failure entered a
product repair path instead of an execution-control path.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/gates.py` and
`src/iriai_build_v2/workflows/develop/execution/verification.py`.

The implementation adds typed evidence nodes and directed edges. Artifacts such
as `dag-verify:g{group}:{stage}` remain compatibility projections; the evidence
graph is authoritative.

```python
EvidenceNodeKind = Literal[
    "gate_request",
    "candidate_manifest",
    "deterministic_gate",
    "context_package",
    "raw_verifier",
    "expanded_lens",
    "aggregate_verdict",
    "merge_gate",
    "checkpoint_gate",
]

EvidenceEdgeKind = Literal[
    "requires",
    "reads",
    "produces",
    "blocks",
    "supersedes",
]

class EvidenceRef(BaseModel):
    kind: Literal["artifact", "event", "contract", "snapshot", "patch", "commit"]
    id: int | str
    sha256: str | None = None
    projection_key: str | None = None

class EvidenceNode(BaseModel):
    id: int
    feature_id: str
    group_idx: int
    stage: str
    kind: EvidenceNodeKind
    name: str
    idempotency_key: str
    status: Literal["pending", "running", "approved", "rejected", "failed", "skipped"]
    deterministic: bool
    input_hash: str
    output_hash: str | None = None
    started_at: str
    finished_at: str | None = None
    input_refs: list[EvidenceRef] = Field(default_factory=list)
    output_refs: list[EvidenceRef] = Field(default_factory=list)
    failure_id: int | None = None
    verdict_id: int | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

class EvidenceEdge(BaseModel):
    id: int
    from_node_id: int
    to_node_id: int
    kind: EvidenceEdgeKind
    required: bool = True

class GateRequest(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    contract_ids: list[int]
    verification_gate_ids: list[str]
    workspace_snapshot_ids: list[int]
    patch_summary_ids: list[int]
    task_attempt_ids: list[int]
    candidate_manifest_id: int
    idempotency_key: str

class CheckpointGateRequest(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: Literal["checkpoint"]
    integrated_queue_item_ids: list[int]
    expected_task_ids: list[str]
    covered_task_ids: list[str]
    post_apply_gate_evidence_ids: list[int]
    merge_proof_evidence_ids: list[int]
    commit_proof_evidence_ids: list[int]
    no_dirty_snapshot_ids: list[int]
    checkpoint_coverage_digest: str
    checkpoint_body_sha256: str
    checkpoint_body_evidence_id: int
    idempotency_key: str

class CandidateManifest(BaseModel):
    id: int | None
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    contract_ids: list[int]
    workspace_snapshot_ids: list[int]
    patch_summary_ids: list[int]
    task_attempt_ids: list[int]
    merge_queue_item_id: int | None = None
    manifest_digest: str
    idempotency_key: str

class GateResult(BaseModel):
    gate_name: str
    approved: bool
    deterministic: bool
    evidence_node_id: int
    failure_id: int | None

class BoundedQuery(BaseModel):
    source: Literal["artifact", "event", "file", "diff", "contract", "snapshot"]
    lookup_kind: Literal["id", "exact_key", "bounded_feature", "file_slice"]
    ids: list[int | str] = Field(default_factory=list)
    limit: int | None = None
    after_id: int | None = None
    event_types: list[str] = Field(default_factory=list)
    deterministic_order: str | None = None

class ReadBudgetReport(BaseModel):
    bounded_queries: list[BoundedQuery]
    artifact_count: int
    event_count: int
    file_count: int
    aggregate_bytes: int
    omitted_optional_refs: list[EvidenceRef] = Field(default_factory=list)
    omitted_required_refs: list[EvidenceRef] = Field(default_factory=list)
    blocked_unbounded_read_count: int = 0
    budget_digest: str

class VerifierNodeResult(BaseModel):
    node_id: int
    verifier_kind: Literal["raw", "lens"]
    lens_slug: str | None = None
    approved: bool
    verdict_id: int
    provider_failure_id: int | None = None
    prompt_context_node_id: int
    read_budget: "ReadBudgetReport"

class AggregateVerdict(BaseModel):
    node_id: int
    approved: bool
    raw_verdict_node_id: int | None
    required_gate_node_ids: list[int]
    required_lens_node_ids: list[int]
    merged_verdict_id: int
    failure_ids: list[int]
    blocking_failure_class: str | None

class GraphApprovalProof(BaseModel):
    aggregate_node_id: int
    aggregate_verdict_id: int
    required_edge_ids: list[int]
    required_node_status_digest: str
    raw_verifier_node_id: int
    required_lens_node_ids: list[int]
    projection_keys: list[str] = Field(default_factory=list)
    proof_digest: str

class VerificationGraph(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    gate_request_node_id: int
    nodes: list[int]
    edges: list[EvidenceEdge]
    raw_verdict_id: int | None
    expanded_lens_ids: list[int]
    aggregate_verdict_id: int
    approved: bool
```

`candidate_manifest_id` is the `evidence_nodes.id` for a `kind='candidate_manifest'`
row. `GateRunner` writes that evidence node immediately before the
`gate_request`, using the `CandidateManifest` body above. It is not the sandbox
manifest and not a merge queue row; merge queue items cite this evidence node
when rerunning gates after patch apply.

Gate requests consume the `VerificationGateSpec` records compiled by Slice 03.
The graph does not reinterpret free-form acceptance text. It loads the explicit
gate ids from the current contracts and proves:

- Every `GateRequest.verification_gate_ids` entry exists in exactly one active
  contract for the group, unless it is a group-level infrastructure gate with
  `source="derived"`.
- Every gate's `criterion_ids` exist in that same contract's
  `AcceptanceCriterionSpec` list.
- Every `RequiredEvidenceSpec` in a blocking gate is represented by an evidence
  node or by a deterministic gate failure before raw verifier dispatch.
- Command gates use argv arrays from `GateCommandSpec`; shell strings are invalid
  and route through `contract_compile/contract_invalid_path`.
- The bounded context package cites criterion ids and gate ids, not positional
  acceptance text, so repair prompts and verifier summaries remain stable across
  wording-only prompt changes.

`CheckpointGateRequest` is a separate request contract for
`evidence_nodes(kind='checkpoint_gate')`; it is not a verifier candidate
manifest. `GateRunner.run_checkpoint_gate` validates it by loading exactly the
listed queue items and evidence ids under the feature advisory lock, proving:

- `expected_task_ids` equals the effective DAG group task ids.
- `covered_task_ids` is the sorted union of `merge_queue_task_coverage.task_id`
  rows for `integrated_queue_item_ids`, with no missing or duplicate task ids.
  Queue JSON payload task lists are display mirrors and are ignored for
  checkpoint approval.
- `checkpoint_coverage_digest` equals the deterministic digest over
  `expected_task_ids`, `covered_task_ids`, `integrated_queue_item_ids`, and
  retry-supersession links loaded from real queue and coverage columns.
- Every queue item is `integrated`, `checkpointing`, or already part of an
  idempotent `done` checkpoint replay for the same checkpoint evidence. A
  `checkpointing` row must have real
  `merge_queue_items.checkpoint_coverage_digest` and
  `merge_queue_items.checkpoint_body_sha256` column values that match the
  current request; JSON payload mirrors are ignored for approval.
- Every listed post-apply gate, merge proof, commit proof, no-dirty snapshot, and
  checkpoint body evidence id belongs to the same feature, DAG hash, group, and
  queue coverage set.
- Every `integrated_queue_item_ids` row stores a `merge_gate` evidence id that
  requires the same approved aggregate node consumed by the post-apply gate.
  Raw `dag-verify:*` projections and queue payload mirrors are ignored for this
  proof.
- `checkpoint_body_sha256` matches `checkpoint_body_evidence_id`.
- The output checkpoint gate node cites `checkpoint_body_evidence_id` in
  `output_refs`; Slice 08 stores that node id as `checkpoint_gate_evidence_id`
  and the body evidence id as `checkpoint_evidence_id`.

### Gate DAG

The graph is a DAG per `(feature_id, dag_sha256, group_idx, stage, attempt)`.
Each node has a stable idempotency key, and duplicate writes with the same input
hash return the existing node. Duplicate writes with a different input hash fail
as an execution-control conflict.

Graph approval is computed from real `evidence_edges(required=true)` rows joined
to `evidence_nodes.status`. Summary arrays such as
`evidence_graphs.required_node_ids` are cache/display mirrors only and cannot
approve a graph when the edge rows or node statuses disagree.

All merge/checkpoint callers must ask the graph store for a `GraphApprovalProof`.
The proof is built inside the same feature advisory lock and transaction that
accepts the queue item or writes checkpoint state. It loads required edge rows,
required node statuses, raw verifier node id, required lens node ids, aggregate
verdict id, and projection keys from graph tables. Callers are not allowed to
assemble approval by mixing raw artifacts, queue payload JSON, or summary arrays.
If a compatibility projection is present but not reachable from the proof's
aggregate node, the graph is rejected with `aggregate.conflict`.

Required node order:

| Order | Node | Purpose | Blocks verifier dispatch |
| --- | --- | --- | --- |
| 1 | `gate_request` | Captures candidate manifest, DAG hash, stage, attempt, contract ids, patch ids, task attempt ids, and workspace snapshots. | Yes |
| 2 | `workspace_snapshot_freshness` | Proves each referenced workspace snapshot exists, belongs to the feature, and matches the candidate base/root. | Yes |
| 3 | `contract_closure` | Proves every group task has a current contract, dependency references are known, and same-wave dependencies are absent. | Yes |
| 4 | `artifact_freshness` | Proves task attempts, patch summaries, and prior verifier artifacts are from the current DAG/group/attempt lineage. | Yes |
| 5 | `path_scope_and_projection` | Proves reported paths are canonical, in scope, not retired aliases, and not missing from the workspace. | Yes |
| 6 | `patch_integrity` | Proves patch summaries match the sandbox patch ids, touched files, and workspace snapshot hash. | Yes |
| 7 | `bounded_context_package` | Builds the exact model context package and records read budgets and selected slices. | Yes |
| 8 | `raw_verifier` | Runs the normal verifier against the bounded context package. | Yes for lenses |
| 9 | `expanded_lens:*` | Runs each required focused lens against the same package plus raw verdict summary. | Yes for aggregate |
| 10 | `aggregate_verdict` | Merges gates, raw verifier, and lenses into the single gate decision. | Yes for merge |
| 11 | `merge_gate` | Records that merge queue acceptance consumed the approved aggregate node. | N/A |
| 12 | `checkpoint_gate` | Records final group-level checkpoint approval over all integrated lanes, commit/no-dirty proof, and coverage. | N/A |

Edges are explicit:

- `gate_request -> deterministic_gate` edges use `requires`.
- Deterministic gate nodes point to the exact artifacts/events/contracts they
  read through `reads` edges or `input_refs`.
- `bounded_context_package -> raw_verifier` and `raw_verifier -> expanded_lens:*`
  use `requires`.
- Every verifier and lens points to its `Verdict` artifact through `produces`.
- `aggregate_verdict -> merge_gate` uses `requires`; the merge queue rejects
  raw verifier ids that are not reachable from an approved aggregate node.
- `merge_gate -> checkpoint_gate` uses `requires` when the same group reaches
  integrated queue coverage. The checkpoint gate reads `GroupMergeCoverage`,
  post-apply gate ids, merge proof ids, commit proof ids, no-dirty snapshot ids,
  and the checkpoint body digest. `dag-group:*` projection is forbidden unless an
  approved `checkpoint_gate` node is linked to the queue transaction.

### Feature-Level Post-DAG Gates

The current `ImplementationPhase` does not end when the last DAG group
checkpoints. It then runs feature-level business gates: code review, security
audit, test authoring, QA, integration testing, final verifier, source-repo
push, implementation report, optional backlog report, and completion
notification. The control plane must preserve those semantics.

Represent these gates in the evidence graph with `group_idx=None` and
`stage="post_dag:{gate_slug}"`, not as ordinary group verification. The
feature-level scope is carried by nullable `group_idx`, `stage`, and gate
metadata. This avoids inventing a second approval model while keeping group
checkpoint proof separate from implementation completion proof.

Required post-DAG gate slugs:

| Gate slug | Legacy compatibility artifact/event | Target graph requirement |
| --- | --- | --- |
| `code_review` | `dag-gate:code-review` and related review verdict rows | Approved feature-level gate after all effective groups checkpoint |
| `security` | `dag-gate:security` and security verdict rows | Approved feature-level gate using bounded feature/repo evidence |
| `test_authoring` | `dag-gate:test-authoring` and test authoring outputs | Approved gate plus any produced test changes routed through typed git/merge evidence |
| `qa` | `dag-gate:qa` and QA verdict rows | Approved gate over the integrated canonical repos |
| `integration` | `dag-gate:integration` and integration verdict rows | Approved gate over merged/checkpointed output, not sandbox output |
| `verifier` | `dag-gate:verifier` and final verifier verdict rows | Approved final feature verifier before implementation report completion |
| `source_push` | source push events/artifacts from `_push_clones_to_source` | Commit/push proof or typed failure routed before completion |
| `implementation_report` | `implementation-report` and backlog/report artifacts | Report evidence linked to final gate state and effective DAG completion |
| `notify` | Slack/notification event | Notification evidence after final report, never as completion authority |

Post-DAG gate rules:

- The first post-DAG gate cannot start until the effective DAG resolver proves
  every effective group is checkpointed, including active regroup overlays.
- A `dag-group:*` projection proves group checkpoint only. It does not prove
  implementation-phase completion until post-DAG gate graph approval also
  exists.
- Test-authoring changes, source pushes, and any other feature-level mutations
  must use the same typed git service/merge queue proof model or a documented
  feature-level queue item. Direct commit/push helpers outside typed proof are
  compatibility shims only during construction.
- Post-DAG gate failures route through Slice 07 failure routing. They must not
  be hidden as successful implementation completion and must not advance to
  `PostTestObservationPhase`.
- Compatibility projections for `dag-gate:*`, final reports, and notification
  artifacts are synchronous with typed gate writes while legacy consumers still
  read them.
- `PostTestObservationPhase` readiness reads typed implementation completion
  and effective-DAG completion. It must not rely on root-DAG length alone,
  because active regroup overlays can change the effective remaining order.

### Verifier And Lens Nodes

The raw verifier is one required node per group/stage/attempt. Expanded lenses
are required nodes derived from `_dag_verify_lens_specs()`: build/dependency,
runtime composition, contract/protocol, acceptance coverage, security/boundary,
and regression/downstream. Lens execution may be concurrent after raw verifier
completion, but node materialization and aggregation are deterministic by
`lens_slug` sort order.

Verifier node payloads store:

- Prompt template version and runtime/provider name.
- Context package node id and package hash.
- Exact input refs and read-budget report.
- Raw model output artifact id.
- Parsed `Verdict` artifact id.
- Provider/runtime failure id when the call did not produce a parseable verdict.
- Redaction summary, including omitted oversize refs and secret redaction counts.

Lens node payloads store the same fields plus `lens_slug`, `lens_label`,
`actor`, `focus`, and `raw_verdict_node_id`. A lens never rereads broad feature
state. It receives the already-built context package, raw verdict summary, and
bounded raw verifier concerns.

Raw verifier approval is necessary but never sufficient for merge/checkpoint.
A raw verifier node is approved only when all of these are true:

- Its required `bounded_context_package` parent is approved and the package hash
  matches the node payload.
- The provider returned a parseable `Verdict` with `approved=True`, no
  provider/runtime failure id, and no blocking concerns.
- The prompt template version, provider/runtime name, redaction summary, and
  `ReadBudgetReport.budget_digest` are stored before projection writes.
- The raw output artifact, parsed `Verdict` artifact, and compatibility
  `dag-verify:*` projection all cite the same raw verifier node id.

Expanded lenses are required whenever `_dag_verify_lens_specs()` returns them
for the group/stage. A missing, skipped, failed, or rejected required lens keeps
the aggregate rejected even if the raw verifier approved.

### Deterministic Preflight Order

Preflight runs before any model call, and it stops at the first blocking class
that makes later gates unreliable. Nonblocking warnings can accumulate, but a
blocking deterministic gate sets the graph aggregate to rejected without
dispatching the raw verifier.

1. Validate `GateRequest`: feature id, DAG hash, group index, stage, attempt,
   idempotency key, and required input lists are present and internally unique.
2. Load explicit workspace snapshots by id and verify feature ownership,
   canonical root, base commit, and snapshot hash.
3. Load explicit task contracts by id and verify all group task ids are present,
   dependencies are known, same-wave dependencies are absent, all
   `GateRequest.verification_gate_ids` exist, every gate's criterion ids match
   current `AcceptanceCriterionSpec.id` values, and required evidence specs are
   materialized or rejected deterministically.
4. Load explicit task attempt and patch summary ids; reject missing, stale,
   superseded, or cross-feature inputs.
5. Validate path scope against task contracts and workspace authority: canonical
   paths only, no retired aliases, no worktree alias source artifacts, no
   missing changed files, no out-of-scope forbidden deletes.
6. Validate patch integrity: patch id to summary hash, touched path list, and
   workspace snapshot id must match the candidate manifest.
7. Validate compatibility projection state: any existing `dag-verify:*` for the
   same idempotency key must point at the same graph node; conflicting legacy
   artifacts become execution-control failures.
8. Build the bounded context package from explicit refs only. The package node
   records file slices, event slices, artifact summaries, omitted refs, and
   hashes.
9. Dispatch raw verifier only when steps 1 through 8 produce approved evidence
   nodes.

The merge queue reruns steps 2, 5, 6, and 7 after applying the candidate patch to
the canonical repo. It does not rerun the model verifier unless the canonical
apply changed the context package hash; a changed hash invalidates the aggregate
node and routes through verification again.

### Stale Context Invariant

The context package is stale if any referenced artifact, event, task attempt,
patch summary, workspace snapshot, or contract no longer matches the candidate
manifest lineage. Staleness is determined from ids, DAG hash, feature id,
group index, attempt, snapshot root/base, patch digest, and contract generation.
"Latest" lookups are forbidden while proving freshness because they can silently
advance the package after the candidate manifest was signed.

When staleness is detected:

- The stale deterministic gate writes `artifact_freshness.stale`,
  `workspace_snapshot.stale`, `path_scope.invalid`, or
  `context_package.insufficient` before any model call.
- The aggregate is rejected with an execution-control failure class, not a
  product defect.
- Existing raw verifier or lens projections for the same group/stage are marked
  stale or conflicted unless their graph node is reachable from the same
  candidate manifest digest.
- Retry creates a new attempt or a new superseding context package only after
  all explicit refs are refreshed and the idempotency key/input hash pair
  changes accordingly.

### Bounded-Read Rules

All graph construction and verifier context building must be bounded by explicit
refs. The default path must not call unbounded `get_events`, broad artifact
scans, or unrestricted `SELECT value`.

`gates.py` and `verification.py` should depend on a small read gateway that only
exposes bounded methods such as `get_artifacts_by_ids`,
`get_artifact_by_exact_key`, `get_events_by_ids`,
`get_events_for_attempt`, and `get_file_slice`. Store-level broad readers remain
available for other workflow code but are not injected into graph construction.
Tests should make the broad methods fail fast when called from this slice.

Read rules:

- Events are read only by event id, task attempt id, or a bounded feature query
  with `limit`, `after_event_id`, and event type filters recorded in the node.
- Artifacts are read only by artifact id or exact projection key named in
  `GateRequest`; prefix scans are allowed only for compatibility projection
  reconciliation and must have a hard limit plus deterministic ordering.
- Large artifacts are summarized before verifier use. The context package stores
  the summary artifact id, byte count, line count, selected ranges, and hash of
  the omitted body.
- File reads are constrained to contract file scopes, patch touched files, and
  explicit verifier requested slices. Each file slice records path, start line,
  end line, byte count, sha256, and reason.
- Diff reads are constrained to patch summary ids and touched path lists. Full
  diffs above the budget are replaced by per-file hunks plus omitted-range
  hashes.
- Model context package budgets are enforced before provider dispatch. Default
  budgets: max 200 artifacts/events, max 80 files, max 2 MiB aggregate text,
  max 20 KiB per file slice, max 200 lines per file slice, and max 20 concerns
  copied from raw verifier into each lens.
- Budget overflow is not silent. It records an approved context package only
  when the omitted refs are non-required; omitted required refs reject
  `bounded_context_package`.
- Every model node records the exact read-budget report so aggregate and failure
  router decisions can distinguish "product concern" from "insufficient
  context package".
- `ReadBudgetReport.blocked_unbounded_read_count` must remain zero for approved
  packages. Any attempted broad read rejects the package even if the caller could
  otherwise recover from cached data.

### Aggregate Verdict Rules

Aggregation is deterministic and conservative:

- If any required deterministic gate is rejected or failed, aggregate is
  rejected and no model verifier node is required.
- If deterministic gates approve but `raw_verifier` is missing, skipped, failed,
  or rejected, aggregate is rejected.
- If `raw_verifier` approves but its projection, parsed `Verdict`, raw output
  artifact, or context package hash does not point back to the same raw verifier
  node, aggregate is rejected with `aggregate.conflict`.
- If raw verifier produces a provider/runtime failure, aggregate is rejected
  with `blocking_failure_class="verifier_provider"` and without product RCA.
- If raw verifier returns `Verdict(approved=False)`, aggregate is rejected and
  the raw concerns are the primary blocking concerns.
- If raw verifier approves but any required lens rejects, aggregate is rejected
  with lens concerns merged into the verdict.
- If a lens fails to execute or returns unparseable output, aggregate is
  rejected with `blocking_failure_class="verifier_provider"` or
  `blocking_failure_class="verifier_context"` according to the failure source,
  not product defect.
- If all deterministic gates, raw verifier, and lenses approve, aggregate
  approves and writes the merged `Verdict`.
- Duplicate concerns are merged by normalized `(severity, source, file,
  canonical_description)` key. The highest severity wins, sources are appended
  in deterministic order, and original node ids remain attached.
- Suggestions and checks never override blocking concerns. They can appear in
  the merged verdict but cannot make a rejected graph approved.
- Approval is reachable only through the aggregate node. `dag-verify:*` raw
  artifacts remain readable for compatibility but cannot satisfy merge/checkpoint
  prerequisites by themselves.

### Failure Mapping

Each rejected/failed node writes a typed failure id consumed by Slice 7. Local
node codes are graph-internal labels only; the persisted `typed_failures` row
must use the canonical failure class/type shown below. The router uses the typed
pair and graph node kind, not prose matching, to choose the repair path.

| Local node code | Canonical failure class/type | Example trigger | Failure route |
| --- | --- | --- | --- |
| `gate_request.invalid` | `dispatcher_internal/idempotency_conflict` | Missing contract ids or duplicated patch ids. | Execution-control bug; halt and surface to supervisor. |
| `workspace_snapshot.stale` | `stale_projection/workspace_snapshot_stale` | Snapshot root/base does not match candidate manifest. | Workspace authority repair from Slice 2. |
| `contract_closure.invalid` | `contract_compile/contract_missing_dependency` or `contract_compile/contract_same_wave_dependency` | Unknown dependency, same-wave dependency, missing AC id. | Task contract repair from Slice 3. |
| `artifact_freshness.stale` | `stale_projection/verifier_context_stale` | Task attempt or patch summary belongs to older DAG hash. | Dispatcher/journal repair from Slices 1 and 5. |
| `path_scope.invalid` | `contract_violation/outside_allowed_paths`, `contract_violation/forbidden_path_touched`, or `worktree_alias/alias_points_to_noncanonical_root` | Retired alias, missing changed file, or forbidden delete. | Workspace/path-scope repair from Slices 2 and 3. |
| `patch_integrity.invalid` | `evidence_corruption/payload_digest_mismatch` | Patch summary hash does not match patch id. | Sandbox patch repair from Slice 4. |
| `context_package.insufficient` | `verifier_context/context_materialization_failed` | Required ref omitted by budget or unavailable by id. | Evidence/context repair; no product RCA. |
| `raw_verifier.rejected` | `product_defect/semantic_verifier_rejected` or `product_defect/required_path_missing` | Product, contract, or acceptance concern in raw `Verdict`. | Product or contract repair based on concern tags. |
| `raw_verifier.runtime` | `verifier_provider/verifier_provider_timeout`, `verifier_provider/verifier_provider_crash`, or `verifier_provider/verifier_parse_failed` | Provider timeout, parse failure, or runtime unavailable. | Runtime/provider retry or operator route from Slice 5. |
| `expanded_lens.rejected` | `product_defect/semantic_verifier_rejected` or `product_defect/required_path_missing` | Lens finds build, security, acceptance, or downstream blocker. | Product or contract repair based on lens slug and concern tags. |
| `expanded_lens.runtime` | `verifier_provider/verifier_provider_timeout`, `verifier_provider/verifier_provider_crash`, or `verifier_provider/verifier_parse_failed` | Lens provider failure or parse failure. | Runtime/provider retry; no product RCA. |
| `aggregate.conflict` | `evidence_corruption/projection_body_conflict` | Legacy projection conflicts with graph node hash. | `quiesce`; journal recovery may append evidence only after source integrity is proven. |
| `merge_gate.missing` | `checkpoint_contradiction/checkpoint_after_failed_gate` | Merge queue item lacks approved aggregate evidence id. | Merge queue rejection from Slice 8. |
| `checkpoint_gate.missing_or_rejected` | `checkpoint_contradiction/checkpoint_after_failed_gate` | Group checkpoint lacks coverage, no-dirty proof, commit proof, or approved checkpoint gate evidence. | Merge queue group checkpoint stop from Slice 8. |

## Refactoring Steps

1. Add graph persistence primitives in Slice 1 storage: `evidence_nodes`,
   `evidence_edges`, idempotency constraints, and typed failure references.
2. Implement `gates.py` with `GateRequest` validation, deterministic gate node
   writers, read-budget accounting, and compatibility projection reconciliation.
3. Implement graph-scoped bounded read helpers and inject only those helpers into
   deterministic gates, context package construction, raw verifier dispatch, and
   lens dispatch.
4. Implement `verification.py` with context package construction, raw verifier
   dispatch, expanded lens dispatch, aggregate verdict construction, and
   deterministic concern merging.
5. Replace direct `_run_dag_group_preflight` and `_do_verify` call sequencing in
   `_verify_and_fix_group` with `run_verification_graph(...)`, preserving the
   current retry semantics and `Verdict` compatibility artifacts.
6. Project `dag-verify:g{group}:{stage}` and related legacy keys through the
   journal from graph node ids exactly once per idempotency key.
7. Update merge queue acceptance to require a transaction-local
   `GraphApprovalProof` and to write a `merge_gate` node before canonical
   apply/checkpoint.
8. Update failure routing to consume typed failure ids from graph nodes and avoid
   broad product RCA for deterministic pipeline failures.
9. Remove legacy verifier-only checkpoint paths in the same landing. Tests must
   prove checkpoint and merge are impossible without aggregate graph approval.
10. Land schema, orchestration, compatibility projections, merge enforcement, and
   tests in one PR/feature branch. Do not add production flags that allow the
   legacy path to approve merges after this slice lands.

## Persistence And Artifact Compatibility

- `evidence_nodes` and `evidence_edges` are authoritative for gate approval.
- Approval helpers expose graph proofs only; no direct caller should read
  `evidence_nodes.status` and decide approval outside the graph store.
- `VerificationGraph` stores artifact ids, event ids, contract ids, snapshot
  ids, patch ids, verifier output ids, aggregate verdict id, and failure ids.
- Node idempotency key:
  `verify-graph:{feature_id}:{dag_sha256}:g{group_idx}:{stage}:a{attempt}:{node_name}`.
- Persistence constraints require unique `(idempotency_key, input_hash)`,
  feature/DAG/group/stage agreement across required edges, and acyclic edge
  insertion inside a graph attempt.
- `dag-verify:g{group}:{stage}` continues to project the current raw verifier or
  deterministic preflight `Verdict` shape for existing consumers.
- A new projection such as `dag-verify-graph:g{group}:{stage}` records the
  graph id, aggregate node id, required gate node ids, raw verifier node id,
  lens node ids, and approval status.
- Existing checkpoint/commit blockers that read raw `dag-verify:*` must be
  moved to graph-aware helpers but continue to write the same legacy blocker
  artifacts for compatibility.
- Large verifier material uses artifact summaries and slices; default paths do
  not call unbounded `get_events` or broad `SELECT value`.
- Replays are safe: if every idempotency key and input hash matches, replay
  returns existing nodes and projections; if a legacy artifact exists without a
  matching graph node, merge/checkpoint rejects until reconciliation writes the
  graph or marks the artifact stale.

## Edge Cases And Failure Handling

- Stale verifier context: deterministic gate fails, writes
  `artifact_freshness.stale` or `context_package.insufficient`, and prevents
  verifier dispatch.
- Raw verifier approves but preflight fails: impossible in the normal DAG order;
  if detected during replay/reconciliation, aggregate rejects and merge queue
  rejects the raw verifier artifact.
- Raw verifier approves but raw approval requirements are incomplete: aggregate
  rejects with `aggregate.conflict` or `verifier_context/context_materialization_failed`,
  depending on whether the mismatch is projection integrity or missing context.
- Preflight approves but model verifier rejects: aggregate rejects and routes
  product or contract repair based on typed concern tags.
- Raw verifier rejects but lenses would have passed: lenses are skipped because
  raw verifier rejection is already blocking; aggregate stores the raw verdict as
  the primary blocker.
- Raw verifier approves but one or more lenses reject: aggregate rejects, merges
  lens concerns, and routes by lens slug plus concern tags.
- Expanded lens fails to execute: record `expanded_lens.runtime`; aggregate
  distinguishes failed execution from product concern.
- Verifier provider failure: route runtime/provider failure, not product defect.
- Required context ref exceeds read budget: reject `bounded_context_package`
  unless a deterministic summary can satisfy the required ref.
- Compatibility artifact conflict: block merge, write `aggregate.conflict`, and
  route to journal compatibility repair.
- Retry after deterministic failure: new attempt creates new node ids; prior
  failed nodes remain linked through `supersedes` edges.
- Retry after provider/runtime failure: reuse deterministic gates and context
  package only when input hashes match; create new model node and aggregate node.
- Crash after raw verifier before lenses: resume reads graph state, completes
  missing lens nodes, and writes one aggregate node.
- Crash after aggregate before merge gate: merge queue can consume the approved
  aggregate id and write `merge_gate` idempotently.

## Tests

Unit tests for `gates.py`:

- `test_gate_request_requires_unique_input_ids`: duplicated contract, patch, or
  snapshot ids fail before reads.
- `test_workspace_snapshot_freshness_rejects_cross_feature_snapshot`: no model
  dispatch and typed failure is `stale_projection/workspace_snapshot_stale`.
- `test_contract_closure_rejects_unknown_and_same_wave_dependencies`: writes one
  deterministic gate node with contract failure details.
- `test_artifact_freshness_rejects_stale_dag_hash`: task attempt from an older
  DAG hash cannot build a context package.
- `test_path_scope_rejects_retired_alias_and_missing_changed_file`: preserves
  current preflight behavior while recording typed failure ids.
- `test_patch_integrity_requires_summary_hash_match`: mismatched patch summary
  blocks verifier dispatch.
- `test_context_package_uses_only_explicit_refs`: fake broad artifact/event
  accessors raise if called.
- `test_context_package_budget_records_omitted_optional_refs`: optional overflow
  is recorded with hashes and still approves.
- `test_context_package_budget_rejects_omitted_required_ref`: required overflow
  fails deterministically.
- `test_gate_nodes_are_idempotent_for_same_input_hash`: replay returns existing
  node ids and does not duplicate projections.
- `test_gate_nodes_reject_same_key_different_input_hash`: conflict routes to
  journal/execution-control failure.
- `test_graph_approval_uses_edges_not_summary_arrays`: mutate
  `evidence_graphs.required_node_ids` after graph creation and prove approval
  still follows required edge rows plus real node statuses.
- `test_graph_approval_proof_rejects_unreachable_projection`: raw projection
  exists but is not reachable from the approved aggregate node.
- `test_checkpoint_gate_requires_merge_gate_for_each_queue_item`: integrated
  queue item with aggregate id but no merge gate evidence cannot checkpoint.
- `test_checkpoint_gate_ignores_queue_payload_json_mirrors`: tampered queue JSON
  task coverage cannot approve or reject without matching real coverage columns.

Unit tests for `verification.py`:

- `test_raw_verifier_not_dispatched_when_preflight_blocks`: verifier spy is not
  called and aggregate rejects from deterministic failure.
- `test_raw_verifier_node_records_prompt_context_and_read_budget`: parsed
  `Verdict` and raw output artifacts are linked.
- `test_raw_verifier_approval_requires_parseable_approved_verdict`: raw output
  without a parsed approved `Verdict` cannot produce an approved raw node.
- `test_raw_verifier_projection_must_cite_same_node`: mismatched raw output,
  parsed verdict, or `dag-verify:*` projection rejects the aggregate.
- `test_raw_verifier_provider_failure_maps_to_runtime_failure`: aggregate
  rejects without product RCA.
- `test_lens_nodes_sort_by_slug_for_deterministic_aggregation`: concurrent lens
  completion order does not affect merged verdict order.
- `test_lens_uses_existing_context_package_not_broad_reads`: lens context
  builder receives only package id, raw summary, and bounded concerns.
- `test_lens_runtime_failure_rejects_aggregate_as_verifier_provider`: no product
  repair route is emitted.
- `test_aggregate_rejects_without_raw_verifier_node`: raw compatibility artifact
  alone is insufficient.
- `test_aggregate_rejects_when_any_required_lens_rejects`: merged verdict keeps
  lens source node ids.
- `test_aggregate_merges_duplicate_concerns_by_normalized_key`: highest severity
  wins and source list is deterministic.
- `test_aggregate_approves_only_when_all_required_nodes_approve`: approved graph
  contains gate, raw verifier, lens, and aggregate node ids.

Integration tests in `tests/workflows/test_dag_expanded_verify.py`:

- Extend `test_dag_group_preflight_uses_raw_verdict_for_checkpoint_gate` to
  assert checkpoint/commit rejection reads the aggregate node, not the raw
  `dag-verify:*` artifact alone.
- Add `test_verification_graph_records_preflight_before_raw_verifier`: persisted
  node order is gate request, deterministic gates, context package, raw
  verifier, lenses, aggregate.
- Add `test_stale_context_prevents_model_dispatch_and_records_failure`: stale
  path/task evidence never reaches `_do_verify`.
- Add `test_compat_dag_verify_projection_written_once_per_idempotency_key`:
  replaying the same graph does not duplicate or rewrite projections.
- Add `test_legacy_dag_verify_without_graph_cannot_checkpoint`: synthetic raw
  artifact cannot satisfy merge/checkpoint prerequisites.
- Add `test_legacy_checkpoint_path_removed_in_same_landing`: direct legacy
  checkpoint helper raises or delegates to graph proof lookup only.
- Add `test_crash_after_raw_verifier_resumes_lenses_and_aggregate_once`: resume
  completes missing nodes without rerunning deterministic gates.
- Add `test_retry_after_provider_failure_reuses_deterministic_nodes`: retry
  creates new model/aggregate nodes only when input hashes match.
- Add `test_merge_queue_requires_approved_aggregate_node`: queue item with raw
  verifier id but no aggregate id is rejected.
- Add `test_merge_queue_writes_merge_gate_node_before_checkpoint`: checkpoint
  proof includes aggregate and merge gate evidence ids.
- Add `test_canonical_apply_context_hash_change_invalidates_aggregate`: merge
  queue reruns verification when post-apply context hash differs.
- Add `test_failure_router_receives_typed_gate_failure`: deterministic path
  failure routes to workspace/contract repair, not product RCA.
- Add `test_graph_replay_detects_compat_projection_conflict`: conflicting
  `dag-verify:*` projection blocks merge and writes `aggregate.conflict`.
- Add `test_stale_raw_projection_after_context_refresh_cannot_merge`: refreshed
  candidate manifest invalidates older raw verifier projection even when its
  `Verdict.approved` flag is true.
- Add `test_post_dag_gates_run_after_final_effective_checkpoint`: code review,
  security, test authoring, QA, integration, final verifier, source push,
  reports, and notification are represented after the last effective group
  checkpoint.
- Add `test_post_dag_gate_failure_blocks_post_test_observation`: a failed or
  missing feature-level gate keeps the workflow in implementation and routes
  through failure handling instead of starting observation collection.
- Add `test_post_test_guard_uses_effective_dag_overlay`: active regroup overlay
  completion, not root-DAG group count alone, decides whether post-test can
  start.

Regression tests for bounded reads:

- Instrument artifact and event stores with fail-fast broad-read sentinels.
- Assert verifier and lens flows read by explicit ids/keys and stay under
  recorded budgets.
- Assert read-budget reports are attached to raw verifier, lens, and aggregate
  nodes.
- Assert oversized required evidence fails before provider dispatch.
- Assert approved context packages have zero blocked unbounded reads and a stable
  `budget_digest`.

## Acceptance Criteria

- Merge queue can prove all required gates passed using typed evidence ids.
- Verifier and expanded lenses never depend on stale broad artifact scans.
- Deterministic workspace/projection failures do not enter broad product RCA.
- A raw `dag-verify:*` artifact without a reachable approved aggregate node
  cannot approve merge or checkpoint.
- A raw verifier approval is accepted only with an approved context package,
  parseable approved `Verdict`, matching projection links, and successful
  required lenses.
- Checkpoint approval requires graph-backed aggregate, merge gate, post-apply
  gate, commit proof, no-dirty snapshot, queue coverage, and checkpoint body
  evidence loaded from authoritative columns.
- Implementation completion requires feature-level post-DAG gate evidence after
  effective DAG completion; post-test observation cannot start from group
  checkpoints alone.
- Replays are idempotent and conflict-detecting by input hash.
- The feature lands atomically with tests covering schema, orchestration,
  compatibility projections, merge enforcement, and failure routing.

## Rollout/Rollback Notes

This slice does not use a phased production rollout. It lands as a single
atomic feature branch/PR with graph persistence, verifier orchestration,
compatibility projections, merge enforcement, and tests enabled together.

Rollback is operationally simple: stop new workflow starts, drain or mark active
verification graph attempts failed with a typed execution-control failure, and
revert the atomic feature if needed. Do not leave a mixed mode where some groups
checkpoint through graph approval and others checkpoint through legacy raw
`dag-verify:*` artifacts. Completed graph evidence remains audit data and must
not be rewritten during rollback.

## Cross-Slice Dependencies

- Slice 1 stores evidence graph nodes, edges, failures, and verify projections.
- Slice 2 supplies workspace snapshots and path authority.
- Slice 3 supplies task contracts and acceptance criteria ids.
- Slice 4 supplies sandbox patch ids and patch summaries.
- Slice 5 supplies dispatcher/runtime boundaries and provider failure classes.
- Slice 7 consumes typed gate, verifier, lens, and aggregate failures.
- Slice 8 requires approved aggregate and merge gate evidence for merge/checkpoint.
