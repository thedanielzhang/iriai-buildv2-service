# 03. Task Deliverable Contracts

## Objective

Convert planning-time task intent into execution-time contracts that constrain
implementation, verification, repair, sandbox output, and merge queue acceptance.

## Current Code Citations

- Task scope and acceptance sub-models: [TaskAcceptanceCriterion](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:423) and [TaskFileScope](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:430).
- Task model: [ImplementationTask](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:942), DAG model: [ImplementationDAG](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:984), and task result model: [ImplementationResult](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/models/outputs.py:1022).
- Workspace registry models: [WorktreeRegistryRepo](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1395) and [WorktreeRegistry](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1422).
- Per-task resume and result persistence: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4865) and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5147).
- Manifest expected/forbidden path loading and matching: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9384) and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9436).
- Deterministic DAG preflight path checks and artifact write: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:14135) and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:14478).
- Preflight tests for forbidden paths and workspace concerns: [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:4794), [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:4969), and [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:5058).
- Bounded artifact summary and slice APIs for large evidence: [artifacts.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:143) and [artifacts.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/storage/artifacts.py:242).

## Current Failure Mode From `8ac124d6`

Tasks declared broad or ambiguous file scopes, while retries and repairs produced
evidence in unexpected paths. Verifiers later found stale generated outputs,
missing canonical files, package mirror drift, and contradictory implementation
surfaces. The workflow lacked a single contract saying what each task was allowed
and required to change.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/task_contracts.py`.

```python
JsonValue = str | int | float | bool | None | dict[str, "JsonValue"] | list["JsonValue"]
PathIntent = Literal["create", "modify", "delete", "read_only", "generated", "unknown"]
PathMatchKind = Literal["file", "directory"]
GateKind = Literal["deterministic", "command", "model_verifier", "expanded_lens", "manual_raw_gate"]
EvidenceRequirementKind = Literal["path_exists", "path_absent", "command_passed", "verdict_approved", "snapshot_fresh", "artifact_projection"]
WriteSetMode = Literal["declared", "unknown_isolated"]
SandboxIsolationMode = Literal["group_shared", "per_task"]
MergeAdmissionMode = Literal["atomic_group", "single_task"]

class ContractPathRule(BaseModel):
    repo_id: str
    path: str
    match_kind: PathMatchKind = "file"
    intent: PathIntent
    required: bool = False
    allow_modify: bool = False
    allow_create: bool = False
    allow_delete: bool = False
    source: str

class AcceptanceCriterionSpec(BaseModel):
    id: str
    source_model: Literal["TaskAcceptanceCriterion", "ImplementationTask", "derived"]
    source_field: str
    source_ordinal: int
    text: str
    must_pass: bool = True
    linked_path_rules: list[str] = Field(default_factory=list)
    digest: str

class RequiredEvidenceSpec(BaseModel):
    id: str
    kind: EvidenceRequirementKind
    repo_id: str | None = None
    path: str | None = None
    command_id: str | None = None
    criterion_ids: list[str] = Field(default_factory=list)
    evidence_node_kind: str
    required: bool = True

class GateCommandSpec(BaseModel):
    id: str
    command: list[str]
    cwd_repo_id: str
    timeout_seconds: int
    env_allowlist: list[str] = Field(default_factory=list)
    expected_exit_code: int = 0
    output_budget_chars: int = 12000

class VerificationGateSpec(BaseModel):
    id: str
    gate_kind: GateKind
    name: str
    source: Literal["task_acceptance", "task_verification", "manifest", "derived"]
    criterion_ids: list[str]
    command: GateCommandSpec | None = None
    required_evidence: list[RequiredEvidenceSpec] = Field(default_factory=list)
    lens_slug: str | None = None
    blocks_merge: bool = True
    blocks_checkpoint: bool = True
    digest: str

class ContractExecutionPolicy(BaseModel):
    write_set_mode: WriteSetMode
    sandbox_isolation: SandboxIsolationMode
    merge_admission: MergeAdmissionMode
    requires_contract_verdict: bool = True
    repair_may_broaden_scope: bool = False
    phased_rollout_allowed: bool = False

class TaskDeliverableContract(BaseModel):
    id: int | None
    feature_id: str
    dag_sha256: str
    source_dag_artifact_id: int
    source_dag_sha256: str
    group_idx: int
    task_id: str
    repo_id: str
    repo_path: str
    required_paths: list[ContractPathRule]
    allowed_paths: list[ContractPathRule]
    read_only_paths: list[ContractPathRule]
    forbidden_paths: list[ContractPathRule]
    generated_outputs: list[ContractPathRule]
    acceptance_criteria: list[AcceptanceCriterionSpec]
    verification_gates: list[VerificationGateSpec]
    execution_policy: ContractExecutionPolicy
    non_goals: list[str]
    dependency_task_ids: list[str]
    unknown_write_set: bool = False
    compile_warnings: list[str]
    normalized_contract_json: dict[str, JsonValue]
    contract_digest: str
    status: Literal["active", "superseded", "cancelled"] = "active"
    idempotency_key: str

class PatchSummary(BaseModel):
    id: int | None
    evidence_node_id: int | None
    sandbox_id: str
    contract_ids: list[int]
    repo_id: str
    base_commit: str | None
    changed_paths: list[str]
    created_paths: list[str]
    modified_paths: list[str]
    deleted_paths: list[str]
    renamed_paths: dict[str, str]
    diff_sha256: str
    diff_artifact_id: int | None
    summary_artifact_id: int | None

class ContractVerdict(BaseModel):
    id: int | None
    contract_id: int
    patch_summary_id: int
    approved: bool
    violation_codes: list[str]
    violations: list[dict[str, str]]
    required_evidence_node_ids: list[int]

class ContractCompiler:
    def compile_task(self, request: ContractCompileRequest) -> TaskDeliverableContract: ...
    def compile_group(self, request: ContractGroupCompileRequest) -> list[TaskDeliverableContract]: ...
    def validate_patch(self, contract: TaskDeliverableContract, patch: PatchSummary, workspace: WorkspaceSnapshot) -> ContractVerdict: ...
    def validate_presence(self, contract: TaskDeliverableContract, snapshot: WorkspaceSnapshot) -> ContractVerdict: ...
```

Contract compiler rules:

1. Compile only from the effective DAG artifact selected for execution, not from
   regenerated prompt text. The compile request must include
   `dag_sha256`, `source_dag_artifact_id`, `source_dag_sha256`, `group_idx`, all
   known task ids, the workspace registry, and manifest entries gathered from
   canonical repos.
2. Resolve every task path through workspace authority before it enters a
   contract. Store canonical repo-relative POSIX paths only. Reject or route a
   compiler failure for absolute paths, `..` segments, symlink escapes,
   unresolved worktree aliases, paths outside the canonical repo root, and repo
   ids not present in the registry.
3. `file_scope` is authoritative for declared task writes. `action="create"`
   creates both a required path and an allowed write rule with
   `allow_create=True`; `action="modify"` creates both a required path and an
   allowed write rule with `allow_modify=True`; `action="delete"` creates an
   allowed delete rule and requires explicit acceptance/gate evidence for the
   absence. `action="read_only"` becomes a `read_only_paths` dependency and
   never appears in `allowed_paths`. Unknown actions are compiler defects.
4. Legacy `task.files` may add allowed write paths only when `file_scope` is
   empty or when each legacy path is already contained by a compiled
   `file_scope` rule. If legacy paths widen a non-empty `file_scope`, store a
   compile warning and route a contract defect before dispatch.
5. Empty writable scope means `unknown_write_set=True`. Unknown contracts can
   dispatch only in a per-task sandbox, cannot share a merge queue item with
   another task, and must pass patch validation before any verifier or merge
   queue acceptance. They do not get broad implicit write authority. Their
   `execution_policy` must be
   `write_set_mode="unknown_isolated"`, `sandbox_isolation="per_task"`, and
   `merge_admission="single_task"`.
6. Manifest `expected_files` become presence evidence candidates, not automatic
   writes, unless the source references the current task id or a source artifact
   in the task lineage. Manifest `forbidden_files` always become forbidden path
   rules.
7. Generated outputs are explicit path rules with `intent="generated"`. They may
   be produced, deleted, or refreshed only when tied to a required source path
   or a verification gate; otherwise they are read-only evidence and dirty
   generated output blocks merge.
8. Acceptance criteria, `not_criteria`, counterexamples, security concerns,
   verification gates, dependency task ids, repo id, canonical path rules,
   execution policy, DAG artifact id/hash, and group idx are digest material.
   Descriptive task name changes are digest material only when the description,
   criteria, or scope also changes.
9. Compile fails closed when a required or generated path intersects a forbidden
   rule. The failure class is a planner/contract defect unless product cleanup is
   required because the forbidden path exists or is tracked in the canonical
   workspace.
10. Compile fails closed when a `read_only_paths` rule intersects a writable
    `allowed_paths` rule for the same contract. Across tasks in the same group,
    read/write overlap is allowed only when the reader task depends on the writer
    and the scheduler places the reader in a later wave; same-wave overlap is a
    contract/scheduling defect. The planner must choose a single same-wave intent
    before dispatch.
11. A compiled contract is the only authority for task writes after dispatch.
    Legacy `ImplementationTask.files`, `ImplementationResult`, compatibility
    `dag-task:*` artifacts, verifier context, and successful tests may provide
    evidence or summaries, but they cannot expand `allowed_paths`, satisfy
    required deliverables, or downgrade forbidden/read-only violations.

Acceptance and gate schema rules:

1. `AcceptanceCriterionSpec.id` is the stable join key used by contracts, gate
   requests, verifier context, repair prompts, and merge queue checks. If the
   current `TaskAcceptanceCriterion` source already has an id, preserve it after
   slug normalization. If it has only text, synthesize
   `ac-{source_ordinal}-{sha256(text)[:10]}` and store `source_ordinal` so later
   compiler versions can prove the mapping.
2. `ImplementationTask.acceptance_criteria`, `not_criteria`, counterexamples,
   and security concerns compile into separate `AcceptanceCriterionSpec` rows.
   Negative criteria use `must_pass=True` with text phrased as a prohibition;
   they do not become optional advisory notes.
3. Every `VerificationGateSpec.criterion_ids` value must exist in
   `acceptance_criteria`. A gate with an empty criterion list is allowed only for
   infrastructure gates such as workspace freshness, patch integrity, and
   no-dirty proof, and it must cite `source="derived"`.
4. Command gates must use `GateCommandSpec.command` as an argv list, never a shell
   string. The command cwd is a canonical `repo_id`; Slice 04 maps it to a
   sandbox repo for pre-merge checks and Slice 08 maps it to the canonical repo
   only inside the merge queue lease.
5. `RequiredEvidenceSpec` is the exact bridge between contracts and Slice 06's
   evidence graph. A required generated output creates either a `path_exists`
   requirement or an explicit `path_absent` requirement tied to a criterion that
   authorizes deletion/absence.
6. Gate ids are stable: `gate:{task_id}:{gate_kind}:{criterion_digest[:10]}` for
   derived gates, or the source command/test id when the planning model supplies
   one. Duplicate gate ids with different digests are contract compile failures.
7. Contract digest includes criterion ids/text, gate ids, command argv, evidence
   requirements, and blocking flags. Reordering gates or evidence requirements is
   normalized by id before digesting.

Path semantics:

- Paths are canonical repo-relative POSIX strings under `repo_id`. `repo_path`
  is display context and must never be used to compare authority.
- A rule with `match_kind="file"` matches only the exact path. A rule with
  `match_kind="directory"` matches the directory and descendants; directory
  rules must end in `/` after normalization and cannot be inferred from a file
  path unless the planning model explicitly declares a directory.
- `required_paths` must be present after accepted implementation unless
  `allow_delete=True` and deletion evidence is tied to acceptance criteria.
  Required presence is checked against sandbox patch application and the
  post-merge workspace snapshot.
- `allowed_paths` is the maximum writable surface for patch validation. A path
  may be changed only when it matches an allowed rule for the same repo and the
  observed operation is permitted by that rule.
- `read_only_paths` is the maximum prompt/verifier context surface that may be
  cited without write permission. It is not a weaker allowed-write set; any
  patch operation against it is a violation.
- Any writable `required_paths` rule compiled from `file_scope` must have a
  corresponding `allowed_paths` rule with the same repo/path/match kind and
  operation flag. Required presence alone never grants write authority.
- `forbidden_paths` overrides allowed and required rules. Any create, modify,
  delete, rename-from, or rename-to touching a forbidden rule is a hard contract
  failure.
- `read_only` paths may be mentioned in prompts and verifier context but cannot
  appear in `created_paths`, `modified_paths`, `deleted_paths`, or
  `renamed_paths`.
- Renames are validated as a delete from the old path and a create at the new
  path. Both endpoints must pass the same repo, allowed-path, and forbidden-path
  checks.
- Case is preserved for storage, but matching uses the repository's declared
  case-sensitivity mode from workspace authority. If that mode is unknown,
  conflicting case variants fail closed.

Write-set and execution policy invariants:

- Declared write-set contracts use `write_set_mode="declared"`,
  `sandbox_isolation="group_shared"` only when every task in the group has
  non-overlapping writable rules and no task forbids another task's required or
  generated path. Otherwise the scheduler must split or route a contract defect
  before dispatch.
- Unknown write-set contracts are isolated single-task units. They may still
  produce a patch, but the patch is judged against observed paths and the fixed
  contract; approval of an unknown write-set patch never grants future authority
  to touch the same paths outside that patch digest.
- A shared atomic group may land only if every member contract has approved
  verdicts for its patch summaries, no member has `unknown_write_set=True`, and
  the combined patch has no cross-contract forbidden/read-only conflicts.
- Sandbox allocation receives contract ids and digests before any agent prompt is
  built. A sandbox whose captured patch references a missing or superseded
  contract digest is discarded rather than repaired in place.
- Repair attempts inherit the original contract id and digest. Product repair may
  change files only inside the original allowed write set; any scope expansion,
  path reclassification, or acceptance/gate change requires planner repair,
  a new immutable DAG artifact, and newly compiled contracts.
- Merge queue admission revalidates the exact patch summary digest against the
  active contract verdict ids. Model-verifier approval, command success, or
  manual raw-gate approval cannot bypass a failed contract verdict.

Patch summary and evidence storage:

- `TaskDeliverableContract.id` is the primary key returned by
  `ExecutionControlStore.put_task_contract`.
- `repo_id` is the stable registry identity; `repo_path` is display/context only.
- `PatchSummary.id` and `PatchSummary.evidence_node_id` are the same
  `evidence_nodes.id` for evidence kind `sandbox_patch_summary`; no separate
  patch table is required for the first landing.
- Large diffs are stored as bounded/spill-backed artifacts referenced by
  `diff_artifact_id`; the summary row stores only repo id, base commit, hashes,
  path lists, rename map, contract ids, and bounded human summary.
- A compact compatibility artifact
  `dag-sandbox-patch:g{group_idx}:attempt-{attempt_no}:repo-{repo_id}` may be
  projected for supervisor display, but the typed evidence node remains
  authoritative.
- Contract verdicts are stored as evidence kind `contract_verdict` and link to
  the contract id, patch summary evidence id, gate evidence ids used for
  generated-output validation, and any workspace snapshot id.
- Contract and patch idempotency keys are based on feature id, DAG hash, group
  idx, task id, repo id, sandbox id, base commit, and patch digest. Replays must
  return the same typed ids or append only terminal routing metadata.

Validation algorithms:

1. `validate_patch` normalizes all patch paths through workspace authority and
   verifies that every path belongs to `patch.repo_id`. Mixed-repo patches are
   split before validation; an unsplittable mixed patch is rejected.
2. It rejects any path matching a forbidden rule before considering allowed
   rules.
3. It checks operation permissions path by path: create requires
   `allow_create`, modify requires `allow_modify`, delete requires
   `allow_delete`, and rename requires delete/create permission on each endpoint.
4. It rejects read-only path changes, outside-root paths, symlink escapes,
   manifest-forbidden descendants, and case-collision variants.
5. It computes missing required paths by applying the patch virtually to the
   sandbox base snapshot and then checking exact required path presence. Missing
   required paths produce `required_path_missing` unless an allowed deletion is
   tied to criteria evidence.
6. It verifies generated outputs by checking either presence after virtual apply
   or explicit absence evidence from a gate. "Not touched" is not sufficient for
   generated outputs that the contract declares as deliverables.
7. It approves only when there are no violations and the patch digest matches
   the captured diff artifact digest. Empty patches can be approved only for
   read-only/verification tasks whose acceptance criteria do not require product
   mutation.
8. `validate_presence` runs the same required/generated/forbidden checks against
   a workspace snapshot after merge queue apply and again before checkpoint.

## Refactoring Steps

1. Add `task_contracts.py` with path normalization, digest construction,
   compiler, patch validation, and presence validation. Keep it free of prompt
   construction and git mutation.
2. Extend `ExecutionControlStore` with `put_task_contract`,
   `record_patch_summary`, and `record_contract_verdict` using one transaction
   per typed record plus compatibility projection.
3. Add a deterministic group write-set pass that computes execution policy,
   rejects cross-task forbidden/read-only conflicts, and marks unknown write-set
   contracts as per-task/single-task before any sandbox is allocated.
4. Compile contracts immediately after resolving the effective DAG and workspace
   registry and before dispatcher prompt construction. A group with compile
   defects does not dispatch.
5. Replace raw task file-scope text in dispatcher prompts with a contract block
   containing contract id, repo id, required/allowed/forbidden path rules,
   generated outputs, acceptance criteria, and explicit non-goals.
6. Pass contract ids through dispatcher attempts, sandbox specs, patch capture,
   gate requests, repair prompts, verifier context, merge queue items, and
   supervisor summaries.
7. Convert deterministic DAG preflight path checks into contract compiler or
   contract verdict checks while keeping existing artifact keys as projections
   during compatibility.
8. Treat `ImplementationResult` as attempt evidence. It may populate summaries
   and compatibility `dag-task:*`, but it cannot satisfy required paths or
   authorize new paths without patch/workspace evidence passing the contract.
9. Update merge queue admission so a queue item requires approved contract
   verdict ids for every patch summary and contract id in the item.
10. Make all contract failures route through typed failure routing before repair;
   repairs receive the original contract id and may produce a new patch verdict,
   but they do not mutate the contract unless the planner/DAG artifact is
   repaired and a new contract digest is compiled.
11. Land schema, store APIs, compiler, dispatcher prompts, sandbox capture,
    gate wiring, repair prompts, merge queue admission, compatibility
    projections, and tests in the same feature slice so no runtime path observes
    contracts as advisory-only metadata.

## Persistence And Artifact Compatibility

- Keep `ImplementationTask` unchanged as the planning model.
- Add typed contract rows in `task_deliverable_contracts` with unique
  `(feature_id, dag_sha256, group_idx, task_id, contract_digest)`.
- Store patch summaries and contract verdicts as `evidence_nodes`; do not add a
  parallel patch-summary table for the first landing.
- Store diff bodies and large validation payloads through bounded/spill-backed
  artifact rows and link them from evidence nodes. Default supervisor/dashboard
  reads use artifact summaries or slices, not full diff bodies.
- Project compatibility artifact `dag-task-contract:{task_id}` with only
  bounded contract summary fields: contract id, digest, repo id, path counts,
  unknown-write-set flag, gates, and compile warnings.
- Existing `dag-task:*` rows must include corrected canonical paths when
  projected from contract-aware results, but they remain dispatcher-owned
  attempt evidence and are never rewritten by the merge queue.
- Project `dag-contract-verdict:g{group_idx}:{task_id}:{sandbox_id}` for current
  verifier/supervisor consumers until they read typed evidence directly.
- Keep source DAG artifacts immutable. If a planner repair changes scope or
  criteria, persist a new DAG artifact and compile new contract ids rather than
  editing existing contract rows in place.

## Edge Cases And Failure Handling

- Empty or ambiguous `file_scope`: compile `unknown_write_set=True`, force
  per-task sandbox isolation, reject shared merge queue admission, and require
  explicit patch validation before semantic verifier context is built.
- Required file missing after implementation: route product repair if the path is
  canonical, writable, and absent after virtual patch apply; route
  stale-projection or workspace repair if evidence exists only in an alias,
  generated snapshot, or legacy artifact.
- Edit outside allowed paths: capture the patch summary, block merge, emit a
  `contract_violation` failure, and route deterministic repair with the offending
  path list. Do not ask product repair to broaden the contract.
- Required/generated path intersects forbidden path: route planner/contract
  defect if the path is only in DAG metadata; route product cleanup first if the
  forbidden path exists on disk or in git state.
- Generated output missing: route `run_product_repair` when source paths and
  gates indicate regeneration is expected; route `retry_verifier` when the only
  missing evidence is a stale generated snapshot.
- Patch deletes a required path: approve only if the contract has
  `allow_delete=True` for that path and an acceptance criterion or gate evidence
  proves the deletion is the intended deliverable.
- Patch modifies read-only path: emit
  `contract_violation/read_only_path_touched`, even when the modification would
  make tests pass.
- Same path allowed by one task and forbidden by another task in a shared group:
  fail group contract compilation and route planner repair; do not rely on merge
  ordering to resolve it.
- Acceptance criteria contradict task dependencies or non-goals: route
  planner/contract defect, not product implementation.
- Contract digest mismatch between dispatcher, sandbox, gates, and merge queue:
  quiesce the queue item as `checkpoint_contradiction` until typed ids agree.
- Patch summary diff artifact hash mismatch: classify as evidence corruption and
  require recapture from sandbox or discard the sandbox.

Contract failure routing:

Contract validation emits only canonical Slice 07 `(failure_class, failure_type)`
pairs so path authority failures remain distinct from product defects even when
the repair action edits product files.

| Local condition | Canonical failure class/type | Route action | Notes |
| --- | --- | --- | --- |
| Invalid path during contract compile | `contract_compile/contract_invalid_path` | `run_contract_repair` | Real DAG contract defect only; alias drift is classified by workspace authority. |
| Alias path during contract compile | `worktree_alias/alias_points_to_noncanonical_root` or `worktree_alias/alias_only_canonical_missing` | `run_canonicalization_repair` | Workspace authority evidence decides metadata-only canonicalization versus sandboxed canonical repair. |
| Scope conflict inside an atomic group | `contract_compile/contract_scope_conflict` | `quiesce` | Contract compiler owns the failure; scheduler/regroup feedback may cite it as follow-on evidence. |
| Forbidden path touched | `contract_violation/forbidden_path_touched` | `run_product_repair` | Product cleanup only; bad contract rules must be emitted as `contract_compile/contract_invalid_path`. |
| Read-only path touched | `contract_violation/read_only_path_touched` | `run_product_repair` | Repair must move the change out of read-only scope; do not broaden the contract. |
| Outside allowed paths | `contract_violation/outside_allowed_paths` | `run_product_repair` with fixed contract | Repair must move work into allowed paths, not broaden scope. |
| Required path missing after virtual apply | `product_defect/required_path_missing` or `stale_projection/workspace_snapshot_stale` | `run_product_repair` or `run_workspace_repair` | Depends on virtual apply and workspace snapshot evidence; verifier retry is only for verifier-context staleness. |
| Generated output missing | `product_defect/required_path_missing` or `stale_projection/verifier_context_stale` | `run_product_repair` or `retry_verifier` | Gate evidence distinguishes stale projection/context from missing product output. |
| Patch digest mismatch | `evidence_corruption/payload_digest_mismatch` | `quiesce` and recapture evidence | Never merge a patch whose artifact hash disagrees with the summary. |
| Contract id mismatch at merge/checkpoint | `contract_violation/contract_id_mismatch` or `checkpoint_contradiction/checkpoint_after_failed_gate` | `quiesce` | Merge queue must not infer equivalence from task ids alone. |

## Tests

Unit tests for `task_contracts.py`:

- Contract compiler preserves task id, source DAG id/hash, group idx, dependency
  ids, repo id, repo path display value, file scope, acceptance criteria,
  `not_criteria`, non-goals, and verification gates.
- Acceptance criteria without source ids receive deterministic ids from text and
  source ordinal; criteria with source ids preserve those ids after slug
  normalization.
- Verification gate specs reject unknown criterion ids, duplicate ids with
  different digests, shell-string command specs, missing command cwd repo ids, and
  required evidence specs whose criterion ids are absent from the contract.
- Infrastructure gates with empty criterion lists are valid only when
  `source="derived"` and `gate_kind="deterministic"`.
- Generated-output gates produce explicit `RequiredEvidenceSpec` rows for
  presence or intentional absence; absence must cite a criterion that authorizes
  it.
- Path normalization accepts canonical repo-relative POSIX paths and rejects
  absolute paths, `..`, symlink escapes, outside-root paths, unknown repo ids,
  unresolved aliases, and case-collision variants when case mode is unknown.
- `create`, `modify`, and `read_only` file-scope actions compile to the expected
  required/allowed/read-only rules, with writable required paths mirrored in
  allowed rules; unknown actions fail compilation.
- `read_only_paths` intersecting writable `allowed_paths` in the same contract
  or atomic group fails compilation before dispatch.
- Legacy `task.files` can fill an empty `file_scope` but cannot widen a non-empty
  writable scope without a compile defect.
- Manifest `forbidden_files` override manifest `expected_files`, `file_scope`,
  generated output declarations, and legacy files.
- Manifest directory forbidden rules match descendants; exact file rules do not
  accidentally match sibling prefixes.
- Unknown write set remains conservative, requires per-task sandbox mode, and
  cannot share a merge queue item with other original group tasks.
- Execution policy is deterministic from the compiled path rules: declared
  non-conflicting groups may use shared atomic group sandboxes, unknown write-set
  tasks must use per-task sandboxes and single-task merge admission, and policy
  fields are included in the contract digest.
- Contract digest changes when material path rules, acceptance criteria, gates,
  dependencies, non-goals, execution policy, DAG hash, or repo id change, and
  remains stable under list ordering that the compiler normalizes
  deterministically.

Patch validation tests:

- Patch touching a forbidden path fails contract validation before allowed-path
  checks.
- Patch creating, modifying, deleting, or renaming outside allowed paths fails
  with operation-specific violation codes.
- Read-only path changes fail even if the path is also present in verifier
  context.
- Required path missing after virtual patch apply blocks merge.
- Required path deletion passes only when `allow_delete=True` and deletion
  evidence is linked.
- Generated output declared in the contract is verified as present or
  intentionally absent with gate evidence; stale "not touched" summaries fail.
- Empty patch can pass only for read-only/verification-only contracts.
- Patch summary with diff hash mismatch fails and records evidence corruption.

Integration tests:

- Every dispatched task stores one contract before sandbox allocation and the
  same contract id appears in dispatcher attempt evidence, sandbox spec, patch
  summary, gate evidence, contract verdict, merge queue item, and supervisor
  summary.
- A compile defect prevents dispatch and writes a typed failure without
  producing `dag-task:*`.
- Existing forbidden-path preflight fixtures are migrated to contract verdict
  assertions while keeping compatibility artifacts readable.
- Merge queue refuses to enqueue or checkpoint without approved contract verdict
  ids for every contract/patch pair.
- Merge queue rejects shared atomic admission when any member contract has
  `unknown_write_set=True` or when combined patches create cross-contract
  forbidden/read-only conflicts.
- A repair attempt for `outside_allowed_paths` receives the original contract and
  produces a new patch summary/verdict rather than mutating the existing
  contract.
- A repair attempt that needs broader scope routes planner/DAG repair and
  produces a new immutable DAG artifact plus new contract ids; it never mutates
  an active contract in place.
- Crash/retry after `put_task_contract`, patch capture, verdict storage, and
  merge queue enqueue returns the same typed ids from idempotency keys.
- Supervisor/dashboard summaries use bounded contract and verdict projections
  without loading full diff artifacts.
- Planner repair that changes scope creates a new DAG artifact and a new
  contract digest; old verdicts remain attached to the old contract.

## Acceptance Criteria

- Every dispatched task has a stored contract.
- Verifier, repair, sandbox, and merge queue use the same contract id.
- No merge queue item can enqueue or checkpoint a task without passing contract
  validation for every patch summary tied to that task.
- Contract failures are classified separately from product failures.
- Unknown-write-set contracts are isolated and cannot broaden write authority at
  merge time.
- Required, allowed, forbidden, and generated path semantics are enforced by code
  and covered by deterministic tests.
- Read-only path semantics and execution policy are enforced by code and covered
  by deterministic tests.
- Atomic group landing is all-or-nothing: no task in a shared group can enqueue,
  checkpoint, or merge unless every contract/patch pair in the group has an
  approved contract verdict.
- Compatibility `dag-task:*` projections remain attempt evidence and never become
  the authority for path satisfaction.
- No production path dispatches with advisory-only contracts or verifier-only
  enforcement.

## Rollout/Rollback Notes

Land the contract compiler, dispatcher wiring, sandbox patch validation, gate
usage, failure routing, merge queue admission, compatibility projections, and
tests as one atomic feature slice. There is no phased production rollout and no
production shadow/advisory mode for this slice; a task either has an authoritative
stored contract and enforced verdicts or it is not dispatched through the new
execution control plane.

The first landing must include the database/store migration, compiler,
dispatcher contract prompt, sandbox patch summary capture, gate evidence joins,
repair prompt wiring, merge queue admission/checkpoint checks, supervisor
compatibility projections, and the deterministic/integration tests above. Do not
ship a mode where contracts are written but ignored by repair, sandbox, gates, or
merge. Do not ship a mode where only some task groups use contract enforcement;
the dispatch entrypoint selects either the legacy execution path or the complete
contract-enforced path before any task attempt starts.

Rollback is whole-slice rollback: disable new dispatch through the execution
control plane entrypoint before any new task attempts start, drain or quiesce
active merge queue items by typed status, and leave already-written contracts,
patch summaries, verdicts, and compatibility artifacts as immutable audit
evidence. Do not partially leave contracts advisory while merge queue or repair
paths continue to use legacy authority; that recreates contradictory path
surfaces.

## Cross-Slice Dependencies

- Slice 1 persists contracts, patch summaries, verdict evidence, and
  compatibility projections.
- Slice 2 provides canonical repo/path identity.
- Slice 4 validates sandbox patches against contracts.
- Slice 6 uses contracts for gate selection.
- Slice 7 adds and routes the `contract_violation` failure class.
- Slice 8 requires contract pass before merge/checkpoint.
