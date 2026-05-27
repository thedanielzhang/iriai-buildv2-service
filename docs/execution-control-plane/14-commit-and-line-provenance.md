# 14. Commit And Line Provenance

## Objective

Attach workflow execution provenance to Git commits and make accepted task
changes traceable down to file and line context. For the post-Slice-12
governance feature, this is a non-blocking governance projection over commits
and commit proofs that already exist. It must not create new checkpoint
authority after the execution-control-plane landing.

The invariant is:

Every accepted DAG task is linked to at least one integration commit. A commit
may cover multiple tasks, and a task may span multiple commits or repositories.

This slice makes Git a durable, portable provenance projection while Postgres
typed journal rows remain canonical for execution control. If commit provenance
is later required as a hard checkpoint prerequisite, that requirement belongs in
Slice 08 and Slice 12 before the execution control plane lands, not in the
post-landing governance feature.

## Current Code Citations

- Current checkpoint commit call: [_commit_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9584).
- Current `dag-group:*` checkpoint body with `commit_hash`: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9644).
- Current direct Git commit invocation: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:11789).
- Current commit hash lookup and comma-joined multi-repo result: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:11839).
- Current `_commit_group` definition: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:11865).
- Planned `RepoCommitProof`: [08-durable-merge-queue.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/08-durable-merge-queue.md:70).
- Planned structured commit proof projection: [08-durable-merge-queue.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/08-durable-merge-queue.md:465).
- Planned checkpoint transaction: [08-durable-merge-queue.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/08-durable-merge-queue.md:482).

## Current Failure Mode From `8ac124d6`

Commit evidence currently answers "which commit checkpointed a group" but not
enough of "why does this line exist" or "which task, verifier, repair, sandbox,
and checkpoint accepted it." That made worktree alias drift, stale task
projection, and commit-heavy groups harder to diagnose. The governance tool
needs a stable bridge from typed workflow evidence to Git blame and line history.

## Upstream Implementation Artifact Review

Before implementation, review:

- The complete Slice 00-12 upstream implementation bundle: plan docs,
  `implementation-journal.md`, `implementation-decisions.jsonl`, acceptance
  records, reviewer findings, test outputs, and accepted deviations.
- Slice 01 journal implementation logs and projection tests for
  `project_commit_proof` and projection-link idempotency.
- Slice 03 contract logs for task-to-path ownership and acceptance criteria.
- Slice 04 sandbox logs for patch capture format and path normalization.
- Slice 06 verification graph logs for gate and aggregate verdict evidence ids.
- Slice 08 merge queue logs, tests, and accepted deviations for queue item
  schema, `RepoCommitProof`, no-dirty proof, multi-repo handling, and checkpoint
  recovery.
- Slice 12 landing record for explicit in-flight adoption constraints.

Compatible deviations:

- The merge queue may split commits by repo, lane, or conflict-recovery unit if
  every accepted task remains covered by at least one commit-provenance payload.
- Commit trailers may contain a compact digest when task lists are too long, as
  long as Git notes or refs contain the full structured payload.
- Existing Slice 08/12 implementations may not hard-gate Git notes/refs; this
  slice may add non-blocking governance projections and provenance-gap findings
  without changing checkpoint semantics.

Blocking deviations:

- Any checkpoint can be written without commit proof.
- Any merge queue implementation cannot identify covered task ids per commit.
- Any accepted patch can bypass task contracts or sandbox patch evidence.
- This slice attempts to block `dag-group:*` checkpointing, merge-queue
  integration, or resume because Git note/ref provenance projection failed.

## Proposed Interfaces And Types

```python
class CommitProvenanceTrailer(BaseModel):
    feature_id: str
    group_idx: int
    effective_group_idx: int | None = None
    task_ids_digest: str
    merge_queue_item_ids_digest: str
    checkpoint_ref: str
    precommit_provenance_ref: str
    precommit_provenance_digest: str

class CommitProvenancePayload(BaseModel):
    schema_version: str = "iriai.commit_provenance.v1"
    feature_id: str
    dag_sha256: str
    group_idx: int
    effective_group_idx: int | None
    repo_id: str
    commit_hash: str
    parent_hash: str
    tree_hash: str
    task_ids: list[str]
    contract_ids: list[int]
    attempt_ids: list[int]
    sandbox_patch_evidence_ids: list[int]
    gate_evidence_ids: list[int]
    merge_queue_item_ids: list[int]
    commit_proof_evidence_id: int
    checkpoint_artifact_id: int | None
    no_dirty_snapshot_ids: list[int]
    implementation_log_anchors: list[str]
    precommit_provenance_ref: str
    payload_sha256: str

class LineProvenanceQuery(BaseModel):
    repo_id: str
    ref: str
    path: str
    line_start: int
    line_end: int
    include_history: bool = True
    max_lines: int = 500
    max_commits: int = 50
    max_payload_bytes: int = 512_000
    timeout_ms: int = 10_000

class LineProvenanceResult(BaseModel):
    commit_hashes: list[str]
    task_ids: list[str]
    provenance_payload_refs: list[str]
    page_refs: list[GovernanceEvidencePageRef]
    completeness: CompletenessState
    completeness_digest: str
    confidence: float
    gaps: list[str]
```

Git projection rules:

- Commit trailers are mandatory and compact.
- Trailer values must be known before `git commit`. `precommit_provenance_ref`
  is derived from stable inputs such as feature id, dag sha, group, repo id,
  queue item ids, task id digest, and contract digest. It must not contain the
  result commit hash unless the implementation uses an explicit amend flow that
  reruns all digest checks.
- Full payloads are written to Git notes or Git refs, for example
  `refs/notes/iriai` keyed by commit or
  `refs/iriai/provenance/{precommit_provenance_digest}`. The payload may include
  the result commit hash because it is written after commit.
- Postgres stores the canonical `dag-commit-proof:*` evidence and the Git
  provenance ref/digest.
- Git notes/refs are verified during resume but are not the source of execution
  authority.
- `payload_sha256` is computed from canonical JSON with `payload_sha256` omitted.
  Tests must prove recomputing the digest after loading the payload gives the
  stored value.

## Refactoring Steps

1. Inspect Slice 08 merge queue commit proof. If Slice 08 already landed
   trailer/Git-provenance fields, verify and index them. If it did not, do not
   alter `dag-commit-proof:*`, commit-proof typed rows, checkpoint projection
   shape, or resume expectations from this governance slice.
2. Extract a Git provenance writer behind a narrow governance projection
   interface that runs from existing commit-proof/checkpoint evidence. It may be
   invoked by a post-checkpoint governance job or an explicitly non-blocking
   merge-queue hook, but it must not decide checkpoint success.
3. Generate commit messages from typed task/group/queue context. Do not let
   runtime agents author trailers.
4. Write Git notes/refs with structured payloads and record their refs/digests
   in governance provenance rows and review artifacts. If Slice 08 already
   includes provenance fields in commit proof, verify them; otherwise do not
   rewrite existing commit proof rows.
5. Add a line-provenance reader that combines Git blame, commit trailers,
   notes/refs, and typed `dag-commit-proof:*` evidence under
   `LineProvenanceQuery` caps.
6. Add lineage handling for rewrite scenarios: if a commit is rebased,
   cherry-picked, or replaced by recovery, emit an explicit old-to-new lineage
   payload.
7. Ensure multi-repo checkpoints preserve legacy comma-separated `commit_hash`
   display while structured proofs remain per repo.

## Persistence And Artifact Compatibility

- `dag-group:*` keeps the legacy `commit_hash` field.
- `dag-commit-proof:*` remains the merge-queue checkpoint proof authority owned
  by Slice 08. Governance provenance stores structured Git provenance refs and
  payload digests in separate governance rows and review artifacts unless those
  fields already exist from the Slice 08/Slice 12 landing.
- Governance reads commit provenance through typed commit proof first, then Git
  notes/refs, then trailers. It never treats trailers alone as full proof.
- Existing legacy commits without provenance are allowed as historical evidence
  gaps and must not be rewritten.

## Edge Cases And Failure Handling

- Git note write fails after commit: governance records a
  `line_provenance_gap` or `governance_evidence_conflict` finding and retries
  the projection idempotently. It does not block checkpointing or resume.
- Commit has trailers but missing note/ref: line query returns partial evidence
  and a gap; governance records provenance-gap findings.
- Line range exceeds inline caps: reject the query or return
  `completeness="paged"` with exact `GovernanceEvidencePageRef` rows for the
  remaining history. A bounded partial result without exact page refs is
  `preview_only` and cannot feed context packages, governance findings, metrics,
  or policy recommendations as line provenance authority.
- Multi-repo group: every repo commit gets a payload; the group checkpoint links
  all payload refs.
- No-op checkpoint: payload references the no-op gate and no-dirty proof, not
  fake changed lines.
- Rebase/cherry-pick: preserve old/new lineage and reject ambiguous line
  provenance unless lineage is recorded.

## Tests

- Commit message generator emits required trailers and rejects oversized trailer
  values by digesting them.
- Git notes/refs writer is idempotent and verifies payload digest.
- Trailer provenance identity is precommit-stable and payload digest excludes the
  digest field itself.
- Commit provenance projection failure after checkpoint produces a governance
  gap finding and does not mutate checkpoint state.
- Line provenance query enforces max lines, commits, payload bytes, and timeout.
- Multi-repo commit proof links all per-repo provenance payloads.
- Line provenance query maps a file range to task ids through blame plus commit
  provenance.
- Large line-history query returns exact page refs and stable digests; consumers
  must fetch pages or mark the result display-only before using it as evidence.
- Rewritten commit lineage preserves task coverage.
- Legacy commit without provenance is reported as a bounded governance gap, not
  an execution failure.

## Acceptance Criteria

- Every accepted task is linked to at least one integration commit.
- Every workflow-authored commit declares the feature, group, task coverage
  digest, checkpoint ref, and full provenance ref.
- Full provenance is available from typed commit proof plus Git notes/refs.
- Line-level provenance can cite task ids, commit hashes, and evidence ids.
- Slice 21 can consume this provenance through the `ProvenanceProvider`
  interface without changing commit proof, checkpoint, merge, or resume
  authority.
- Provenance failures are routed as workflow/projection failures, not product
  defects.
- Governance provenance projection failures never block `dag-group:*`
  checkpointing, merge queue integration, or resume.

## Rollout And Rollback Notes

This lands with the governance tool after Slice 12 and after merge queue commit
proof exists. Rollback disables new Git provenance writes but leaves existing
commit trailers and Git notes/refs intact. Do not rewrite historical commits
during rollback.

## Cross-Slice Dependencies

- Slice 01 provides typed projection links.
- Slice 03 provides task/path contracts.
- Slice 04 provides sandbox patch evidence.
- Slice 06 provides gate evidence.
- Slice 08 owns commit, no-dirty proof, and checkpoint projection.
- Slice 13 defines governance evidence refs that cite Git provenance.
- Slice 21 consumes this slice through provider adapters and the IriAI lineage
  plugin.

## Slice 13A Shared Completeness Model Dependency

Per **doc-13a:285-287 § Refactoring Steps step 9** — *"Update governance
Slices 13-20 and context Slice 21 to depend on this shared completeness
model instead of redefining authority semantics locally."* — this
slice's `LineProvenanceResult.completeness: CompletenessState` field
(see lines 124-132 above) and its `page_refs:
list[GovernanceEvidencePageRef]` field both depend on the Slice 13A
shared completeness model.

Source-of-truth modules:

- `src/iriai_build_v2/execution_control/completeness.py` (Slice 13A
  2nd sub-slice) — `CompletenessState`, `EvidencePageRef`,
  `EvidenceCompleteness`, `ExactEvidenceManifest`,
  `AuthoritativeContextRef`, `compute_completeness_digest`.
- `src/iriai_build_v2/execution_control/gate_companion.py` (Slice 13A
  5th sub-slice) — `AuthoritativeGateProofRow` is the **only** typed
  shape by which a deterministic line-provenance summary can satisfy
  a required gate per doc-13a:276-278; line-provenance summaries that
  are not backed by an `AuthoritativeGateProofRow` cannot feed gate
  authority.
- `src/iriai_build_v2/execution_control/snapshot_companion.py` (Slice
  13A 6th sub-slice) — `AuthoritativeSnapshotListFieldCompleteness`
  carries per-list-field completeness for any list field (including
  line-provenance result lists) that classifier rules consume.
- `src/iriai_build_v2/execution_control/dispatcher_prompt_context.py`
  (Slice 13A 4th sub-slice) — line-provenance evidence that feeds the
  dispatcher's prompt-context bundle (via the typed
  `AuthoritativePromptContextBundle` adapter) is governed by the
  same completeness contract; line-provenance inputs that the
  dispatcher cannot exact-cite must route through the
  `runtime_context/context_incomplete` typed failure id per
  doc-13a:269-272.

The doc's existing `completeness="paged"` + `GovernanceEvidencePageRef`
wording at lines 200-203 already maps onto the shared model's
`CompletenessState = Literal["complete", "paged", "preview_only",
"unavailable"]` enum; this dependency reference makes that mapping
explicit. Per P3-13A-6-3 and Slice 19A source-of-truth
`19a-governance-implementation-reassessment.md` (`19A-P2-001`), the current
dashboard wrapper is display/advisory-only and does not let line-provenance
evidence become execution authority. Authority use must wait for a future
source-of-truth slice that wires an actual authoritative consumer with durable
failure observation.

This dependency-reconciliation reference was added by
**Slice 13A 8th sub-slice 13An-1** (this iteration) per
doc-13a:285-287 step 9.
