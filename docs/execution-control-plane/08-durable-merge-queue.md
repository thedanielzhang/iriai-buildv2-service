# 08. Durable Merge Queue

## Objective

Move canonical repo mutation, commit, no-dirty proof, and group checkpoint into
a durable merge queue. Implementation and repair agents produce sandbox patches;
the queue validates, rebases when deterministic, applies, gates, commits, proves
clean state, projects compatibility artifacts, and writes group checkpoints
idempotently.

The queue is the only product-authoritative landing path for new execution
control plane attempts. This slice does not introduce a shadow writer, feature
flagged legacy fallback, or phased production pilot. It lands atomically with the
typed journal, workspace authority, contracts, sandbox runner, gates, and
failure router pieces needed to make sandbox output authoritative.

## Current Code Citations

- Current implementation commit path: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5159).
- Commit helpers: [_commit_repos](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5686), [_commit_repos_in_root](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5732), and [_commit_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:5904).
- Checkpoint write after approval: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4166) and [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4203).
- Typed queue table and projection ownership planned by Slice 1: [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:432) and [01-typed-journal-and-compatibility-projections.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/01-typed-journal-and-compatibility-projections.md:551).
- Sandbox patches are replayed only by the queue: [04-sandbox-runner.md](/Users/danielzhang/src/iriai/iriai-build-v2/docs/execution-control-plane/04-sandbox-runner.md:227).
- Existing commit failure tests: [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:107), [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:421), and [test_dag_expanded_verify.py](/Users/danielzhang/src/iriai/iriai-build-v2/tests/workflows/test_dag_expanded_verify.py:444).

## Current Failure Mode From `8ac124d6`

Commit failures and dirty state sometimes appeared after task evidence or verify
evidence had already been recorded. Crash boundaries between commit and
checkpoint were not explicit typed states, making retry and resume ambiguous.

The durable queue fixes this by making `dag-task:*` attempt evidence only and
requiring a typed queue row to pass through apply, gate, commit, no-dirty proof,
and checkpoint projection before `dag-group:*` exists.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/merge_queue.py`.
The implementation may keep compatibility shims in `implementation.py` during
the atomic landing, but production callers must use this module.

```python
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MergeQueueStatus = Literal[
    "queued",
    "leased",
    "applying",
    "verifying",
    "committing",
    "integrated",
    "checkpointing",
    "done",
    "failed",
    "poisoned",
    "cancelled",
]


class RepoTarget(BaseModel):
    repo_id: str
    repo_path: str
    base_commit: str
    expected_head: str | None = None


class RepoCommitProof(BaseModel):
    repo_id: str
    repo_path: str
    pre_apply_head: str
    applied_head: str
    result_commit: str
    tree_sha: str
    changed_paths: list[str]
    status_before: str
    status_after: str
    no_dirty_snapshot_id: int


class MergeQueueItem(BaseModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    repo_id: str
    repo_path: str
    attempt_id: int | None = None
    contract_ids: list[int] = Field(default_factory=list)
    patch_evidence_ids: list[int] = Field(default_factory=list)
    gate_evidence_ids: list[int] = Field(default_factory=list)
    pre_queue_gate_evidence_id: int | None = None
    post_apply_gate_evidence_id: int | None = None
    base_commit: str
    head_commit: str = ""
    status: MergeQueueStatus
    priority: int = 100
    lease_owner: str | None = None
    leased_until: datetime | None = None
    lease_version: int = 0
    result_commit: str = ""
    merge_proof_evidence_id: int | None = None
    commit_proof_evidence_id: int | None = None
    checkpoint_gate_evidence_id: int | None = None
    checkpoint_evidence_id: int | None = None
    checkpoint_projection_id: int | None = None
    checkpoint_coverage_digest: str = ""
    checkpoint_body_sha256: str = ""
    retry_of_queue_item_id: int | None = None
    failure_id: int | None = None
    request_digest: str
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MergeQueue:
    async def enqueue(self, item: MergeQueueItemCreate) -> MergeQueueItem: ...
    async def claim(self, feature_id: str, lease_owner: str) -> MergeQueueItem | None: ...
    async def heartbeat(self, item_id: int, lease_owner: str, lease_version: int) -> MergeQueueItem: ...
    async def recover_expired(self, feature_id: str, lease_owner: str) -> MergeQueueItem | None: ...
    async def apply_candidate(self, item: MergeQueueItem, token: LeaseToken) -> MergeApplyResult: ...
    async def run_required_gates(self, item: MergeQueueItem, token: LeaseToken) -> MergeGateResult: ...
    async def commit_and_prove_clean(self, item: MergeQueueItem, token: LeaseToken) -> MergeCommitResult: ...
    async def mark_integrated(self, item: MergeQueueItem, token: LeaseToken) -> MergeQueueItem: ...

class GroupMergeCoverage(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    expected_task_ids: list[str]
    integrated_queue_item_ids: list[int]
    done_queue_item_ids: list[int]
    missing_task_ids: list[str]
    duplicate_task_ids: list[str]
    failed_queue_item_ids: list[int]
    result_commits: list[str]
    approved: bool

class GroupMergeCoordinator:
    async def expected_task_ids(self, feature_id: str, dag_sha256: str, group_idx: int) -> list[str]: ...
    async def coverage(self, feature_id: str, dag_sha256: str, group_idx: int) -> GroupMergeCoverage: ...
    async def checkpoint_group(self, coverage: GroupMergeCoverage, token: FeatureLeaseToken) -> MergeResult: ...
```

Queue item granularity is one integration lane inside a DAG group. Most groups
still produce one queue item, but the queue must split when contracts require
isolation. In particular, an `unknown_write_set=True` contract is enqueued as a
single-task lane (`payload.integration_lane = "task:{task_id}"`) and cannot share
a queue item with another task. Multi-repo lanes use `repo_id = "__feature__"`
and write one `merge_queue_repo_targets` row per repo; any
`payload.repo_targets` list is a display mirror only.

> **Implementation divergence (Slice 08e-2, intentional).** The
> `_enqueue_durable_merge_queue_for_results` splice in `implementation.py`
> enqueues **one `task:{task_id}` lane per task**, never a combined `group`
> lane — even for a multi-task group whose contracts permit sharing. Per-task
> lanes are always isolation-safe (a strict refinement of "the queue must
> split when contracts require isolation"), and `GroupMergeCoordinator.coverage`
> collects every `integrated` lane for the group so the single
> `dag-group:{group_idx}` checkpoint is unaffected. The only cost is extra
> `merge_queue_items` rows for multi-task groups; this is a queue-row-efficiency
> tradeoff, never a correctness or isolation issue. Revisit only if queue-row
> volume becomes a concern.
Task coverage is not inferred from payload. Enqueue writes one
`merge_queue_task_coverage` row per covered task in the same transaction as the
queue row, and all checkpoint coverage/retry-supersession logic reads those rows.
Repo apply/recovery state is also not inferred from payload. The queue records
canonical repo targets, pre-apply heads, applied heads, per-repo result commits,
and no-dirty snapshot ids in `merge_queue_repo_targets`.

The parent queue row is the lane state machine; child rows are the per-task and
per-repo recovery ledger. A transition that depends on task coverage or repo
state is invalid unless the matching child rows already exist and their real
columns support the transition. Payload mirrors are never a fallback when a child
row is missing or inconsistent.

Group checkpoint remains one `dag-group:{group_idx}` projection. The queue emits
it only after `GroupMergeCoordinator` proves every expected task id for the group
is covered by `integrated` queue items, every integrated item cites the same
`dag_sha256`, all post-apply gates approve, and the final group-level checkpoint
gate approves. The checkpoint transaction then sets all covered lanes to `done`
with the same `checkpoint_gate_evidence_id`, `checkpoint_evidence_id`, and
`checkpoint_projection_id`, plus matching `checkpoint_coverage_digest` and
`checkpoint_body_sha256` values.
`result_commit` preserves the current legacy checkpoint display value, including
comma-separated commits for multi-repo or multi-lane groups, while
`dag-commit-proof:g{group_idx}` stores structured per-repo and per-lane proofs.

## Merge Queue Schema

Use the Slice 1 `merge_queue_items`, `merge_queue_task_coverage`, and
`merge_queue_repo_targets` tables as the durable state authority, with these
queue-specific constraints and payload fields:

| Column | Required semantics |
| --- | --- |
| `feature_id`, `dag_sha256`, `group_idx` | Scope one group integration and checkpoint. |
| `repo_id`, `repo_path` | Primary repo target, or `__feature__` plus `merge_queue_repo_targets` rows for multi-repo groups. |
| `attempt_id` | Merge/checkpoint attempt row from `execution_attempts`. |
| `contract_ids` | Non-empty active contracts for every changed task in this integration lane. |
| `patch_evidence_ids` | Immutable sandbox patch evidence. Empty is invalid outside explicit no-op groups. |
| `gate_evidence_ids` | Pre-queue gate evidence ids. Queue writes new post-apply gate evidence ids in `payload.post_apply_gate_evidence_ids`. |
| `pre_queue_gate_evidence_id` | Real column for aggregate pre-queue approval proof. Required for all non-terminal queue processing. |
| `post_apply_gate_evidence_id` | Real column for aggregate post-apply approval proof. Required before commit/checkpoint. |
| `base_commit` | Expected base commit or feature-root digest for the patch set. |
| `head_commit` | Candidate sandbox head or patch digest source. |
| `status` | State machine below. `integrated` means this lane has committed and proved clean but the group checkpoint has not been projected yet. `done` means the group checkpoint projection has been written and linked to this lane. |
| `lease_owner`, `leased_until`, `lease_version` | Fenced worker ownership. |
| `result_commit` | Empty until commit succeeds; stable display string after commit. |
| `failure_id` | Terminal typed failure for `failed` or `poisoned`. |
| `request_digest` | Real digest over parent fields plus task coverage rows, repo target rows, patch refs, and gate refs. Duplicate enqueue compares this digest, not payload JSON. |
| `idempotency_key` | `merge:{feature_id}:{dag_sha256}:g{group_idx}:{integration_lane}:{task_ids_digest}:{repo_id}:{base_commit}:{head_commit or patch_digest}`. |
| `payload` | Bounded JSON metadata described below. |
| `merge_proof_evidence_id` | Nullable until apply proof succeeds; required for `verifying`, `committing`, `integrated`, `checkpointing`, and `done`. |
| `commit_proof_evidence_id` | Nullable until commit/no-dirty proof succeeds; required for `integrated`, `checkpointing`, and `done`. |
| `checkpoint_gate_evidence_id` | Nullable until group coverage is complete; required for `done`. It points to Slice 06 `evidence_nodes(kind='checkpoint_gate')`. |
| `checkpoint_evidence_id` | Nullable until checkpoint body evidence is written by the approved checkpoint gate; required for `done`. |
| `checkpoint_projection_id` | Nullable until legacy `dag-group:*` projection is written; required for `done`. |
| `checkpoint_coverage_digest` | Real column set when rows enter `checkpointing`; digest over covered queue item ids, expected task ids, and replacement lineage. Required for replaying or completing a checkpoint. |
| `checkpoint_body_sha256` | Real column set when rows enter `checkpointing`; digest of the exact legacy `dag-group:*` body approved by the checkpoint gate. |
| `retry_of_queue_item_id` | Real self-reference to the failed source queue item for `retry_merge` replacements. This column, not payload metadata, authorizes supersession during checkpoint coverage. |

Required indexes and constraints:

- Unique `idempotency_key`.
- Check constraint for exactly the statuses listed in `MergeQueueStatus`.
- Claim index on `(feature_id, status, priority, id)` for `queued` and expired
  `leased` rows. The claim predicate must be `status = 'queued' OR
  (status = 'leased' AND leased_until < now())`; normal claim never selects
  `applying`, `verifying`, `committing`, `integrated`, or `checkpointing`.
- Recovery index on `(leased_until, id)` for active leased rows where
  `status IN ('leased', 'applying', 'verifying', 'committing', 'checkpointing')`.
  `integrated` rows are not lease-recovered by normal workers; they are picked
  up by `GroupMergeCoordinator.coverage`.
- Group lookup index on `(feature_id, dag_sha256, group_idx, id DESC)`.
- Result lookup index on `(feature_id, result_commit)` where `result_commit <> ''`.
- Retry-source index on `(retry_of_queue_item_id)` where the column is not null.
- Partial unique index on `(retry_of_queue_item_id)` where the column is not
  null and `status <> 'cancelled'`, so one failed source row cannot be replaced
  by two live rows. A failed replacement can be retried only by creating a new
  row that points at that replacement row, not by reusing the original source.
- Check constraints or generated columns enforce proof progression:
  `status IN ('queued','leased','applying','verifying','committing','integrated','checkpointing','done')`
  requires `pre_queue_gate_evidence_id IS NOT NULL`;
  `status IN ('verifying','committing','integrated','checkpointing','done')` requires
  `merge_proof_evidence_id IS NOT NULL`;
  `status IN ('committing','integrated','checkpointing','done')` requires
  `post_apply_gate_evidence_id IS NOT NULL`;
  `status IN ('integrated','checkpointing','done')` requires
  `commit_proof_evidence_id IS NOT NULL`; `status = 'done'` requires
  `checkpoint_gate_evidence_id IS NOT NULL`, `checkpoint_evidence_id IS NOT NULL`,
  `checkpoint_projection_id IS NOT NULL`, and `result_commit <> ''`. PostgreSQL
  checks must reference real columns, not JSONB payload keys.
  `status IN ('checkpointing','done')` additionally requires
  `checkpoint_coverage_digest <> ''` and `checkpoint_body_sha256 <> ''`.

`merge_queue_task_coverage` rows are required for every non-no-op queue item.
They are inserted in the same transaction as enqueue, after the store validates
the parent row, active contracts, effective DAG task ids, and lane split rules.
The table has `UNIQUE (queue_item_id, task_id)` and group lookup index
`(feature_id, dag_sha256, group_idx, task_id, queue_item_id)` as defined in
Slice 1. Its composite foreign keys bind the duplicated feature/DAG/group/task
columns to both the parent queue row and the active task contract scope. Queue
code may mirror task ids into payload for bounded display, but coverage,
duplicate detection, and checkpoint approval must use the child table.

Enqueue also locks existing coverage rows for the same
`(feature_id, dag_sha256, group_idx, task_id)` set. It rejects a second live lane
for a task id unless the new lane is the single authorized `retry_merge`
replacement for a terminal failed source row with identical coverage. This is a
store-level invariant because PostgreSQL cannot express the parent-row terminal
status filter in a child-table unique index.

`retry_of_queue_item_id` has a store-level validation hook that runs in the same
transaction as enqueue. It locks the source row and source coverage rows, rejects
a source outside the same feature/DAG/group, rejects any source that is not
terminal `failed`, rejects non-empty `result_commit`, rejects
poison/cancelled/done sources, compares replacement coverage rows with source
coverage rows, and rejects a source that already has a non-cancelled replacement
row. This is a schema invariant, not a dashboard convention.

`merge_queue_repo_targets` rows are required for every queue item that may touch
canonical repos. They are inserted in the same transaction as enqueue and carry
the real canonical repo path, base commit, pre-apply head, applied head,
per-repo result commit, tree SHA, target digest, and no-dirty snapshot id. Apply,
rebase, commit, and recovery must read and update these rows under the feature
advisory lock. Child-row proof progression checks require `pre_apply_head`, `applied_head`,
`result_commit`/`tree_sha`, and `no_dirty_snapshot_id` as each repo target moves
through pre-apply, applied, committed, and clean states. Payload mirrors cannot
authorize a repo target or reset point.

Repo target `status` advances independently but monotonically:
`pending -> pre_apply_recorded -> applied -> committed -> clean`, or to
`failed`/`poisoned`. The queue must persist `pre_apply_recorded` before any git
mutation, `applied` before post-apply gates, `committed` immediately after a
commit is identified, and `clean` only after the workspace snapshot proves no
dirty state for that repo.

`payload` keys are versioned with `payload.schema_version = 1`:

| Key | Semantics |
| --- | --- |
| `repo_targets` | Display mirror of `merge_queue_repo_targets`. Required for dashboard readability on multi-repo lanes but never authoritative. |
| `task_ids` | Display mirror of `merge_queue_task_coverage.task_id` rows. Required for dashboard readability but never authoritative for coverage. |
| `integration_lane` | Stable lane id such as `group`, `repo:{repo_id}`, `task:{task_id}`, or `canonicalization:{failure_id}`. Unknown-write-set tasks must use `task:{task_id}`. |
| `group_expected_task_ids` | Display mirror of effective-DAG expected task ids for crash diagnostics. The coordinator recomputes expected ids from the effective DAG before checkpoint. |
| `patch_digest` | SHA-256 of normalized patch evidence contents. |
| `patch_path_set` | Sorted repo-relative paths captured by sandbox. |
| `post_apply_gate_evidence_ids` | Mirror/list of gate evidence generated after canonical apply or rebase. The aggregate proof is the real `post_apply_gate_evidence_id` column. |
| `merge_proof_evidence_id` | Mirror of the real column for bounded display only. |
| `commit_proof_evidence_id` | Mirror of the real column for bounded display only. |
| `checkpoint_gate_evidence_id` | Mirror of the real column for bounded display only. |
| `checkpoint_evidence_id` | Mirror of the real column for bounded display only. |
| `checkpoint_projection_id` | Mirror of the real column for bounded display only. |
| `checkpoint_coverage_digest` | Mirror of the real column for bounded display only. |
| `checkpoint_body_sha256` | Mirror of the real column for bounded display only. |
| `pre_apply_heads` | Display mirror of `merge_queue_repo_targets.pre_apply_head`. |
| `applied_heads` | Display mirror of `merge_queue_repo_targets.applied_head`. |
| `repo_commit_proofs` | Display mirror derived from real per-repo target rows plus `commit_proof_evidence_id`. |
| `recovery_count` | Number of expired active lease recoveries. |
| `retry_of_queue_item_id` | Display mirror of the real self-reference. Coverage and retry validation must never trust this payload key. |
| `last_error` | Bounded structured failure context. |

## Status Transitions

Allowed forward transitions:

| From | To | Preconditions |
| --- | --- | --- |
| none | `queued` | Idempotent enqueue after sandbox patch capture and pre-queue gates approve. |
| `queued` | `leased` | `claim` wins row lock and sets owner, expiry, version. |
| expired `leased` | `leased` | New owner claims and increments `lease_version`. |
| `leased` | `applying` | Lease token is current, contracts/patch/gate evidence load, feature merge lock acquired, repo no-dirty baseline passes. |
| `applying` | `verifying` | Patch applies or deterministic rebase applies, path set still satisfies contracts, merge proof evidence recorded. |
| `verifying` | `committing` | Required post-apply gates approve and evidence ids are recorded. |
| `committing` | `integrated` | Commit succeeds, result commit is recorded, no-dirty proof evidence exists, and lane-level work is complete. |
| `integrated` | `checkpointing` | Group coordinator wins feature lock, coverage proves all expected task ids are integrated exactly once, and final checkpoint gate starts. |
| `checkpointing` | `done` | Group checkpoint transaction writes typed evidence, compatibility projections, and terminal state for every covered lane. |
| active non-terminal | `failed` | Typed failure recorded, canonical repo restored or proved clean, and the router has selected either a retry route or a clean terminal `quiesce` route. |
| active non-terminal | `poisoned` | Ambiguous canonical state, invariant corruption, or recovery cannot prove clean state. Budget exhaustion alone is not poison when the repo is clean and typed failure evidence is complete. |
| `queued` or `leased` | `cancelled` | Feature superseded or deploy rollback stops before canonical mutation begins. |

Forbidden transitions:

- No terminal row can return to an active status.
- `integrated` requires `result_commit`, `merge_proof_evidence_id`,
  `post_apply_gate_evidence_id`, and `commit_proof_evidence_id`, but must not
  have `checkpoint_gate_evidence_id`, `checkpoint_evidence_id`, or
  `checkpoint_projection_id`.
- `done` requires `result_commit`, `merge_proof_evidence_id`,
  `commit_proof_evidence_id`, `checkpoint_gate_evidence_id`,
  `checkpoint_evidence_id`, and `checkpoint_projection_id`.
- `checkpointing` cannot be entered from `verifying`; commit/no-dirty proof is
  mandatory.
- `cancelled` is forbidden after canonical apply starts unless recovery first
  resets or proves no dirty state and records that proof.
- A stale `lease_version` cannot heartbeat, apply, verify, commit, checkpoint,
  fail, poison, or cancel the row.

## Lease Semantics

- `claim` uses `FOR UPDATE SKIP LOCKED` and updates the row in the same
  transaction: `status = 'leased'`, `lease_owner`, `leased_until = now() + ttl`,
  `lease_version = lease_version + 1`, and `updated_at = now()`.
- The implementation must be one atomic `UPDATE ... WHERE id IN (SELECT ...
  FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *` or equivalent. It must not select
  a candidate row and update it in a later statement, because that permits two
  workers to observe the same queued item under load.
- Default TTL is five minutes. Workers heartbeat every minute and before any
  long gate command. Heartbeat extends only when `(id, lease_owner,
  lease_version)` still matches and the row is non-terminal.
- Every mutating method includes the lease token in its `WHERE` clause and must
  affect exactly one row. Zero rows means the worker is fenced and must stop.
- `leased` rows with expired leases are claimable by normal workers because no
  canonical mutation has started.
- Expired rows in `applying`, `verifying`, `committing`, or `checkpointing` are
  recoverable only through `recover_expired`. Recovery first acquires the
  feature advisory lock, then reconstructs canonical repo state from typed row
  data and git facts before continuing or failing.
- A worker may release a lease back to `queued` only while the status is still
  `leased`. After `applying`, the row must reach `done`, `failed`, `poisoned`,
  or an explicit recovery status transition.
- Recovery increments `lease_version`, appends `payload.recovery_count`, and
  preserves previous owner and expiry in `payload.recovery_history`.
- After three expired active recoveries for the same row, mark `poisoned` unless
  the current recovery can move a clean committed lane to `integrated` or the
  group coordinator can complete idempotent checkpointing from integrated lanes.

## Patch Apply And Rebase Algorithm

The queue applies only immutable sandbox patch evidence. It never reads a live
sandbox directory as source of truth.

1. Load the row, patch evidence, active contracts, pre-queue gate evidence, and
   expected repo targets by typed ids. Reject missing or mismatched evidence with
   `checkpoint_contradiction` before touching git.
2. Acquire the feature advisory lock and keep it through commit/checkpoint or
   rollback cleanup. The queue heartbeat keeps the row lease current while the
   lock is held.
3. For every target repo, prove baseline cleanliness with:
   `git status --porcelain=v2 -z --untracked-files=all`,
   `git diff --quiet`, and `git diff --cached --quiet`.
4. Record each target's `merge_queue_repo_targets.pre_apply_head` with
   `git rev-parse HEAD`, set the target `status = 'pre_apply_recorded'`, and
   persist that row update in a durable journal transaction before mutation. If
   `expected_head` is set, it must equal the live HEAD. If the current HEAD
   equals that target's `base_commit`, apply directly.
5. If current HEAD differs from `base_commit`, allow deterministic rebase only
   when `git merge-base --is-ancestor base_commit HEAD` succeeds. Otherwise
   record a `merge_conflict` or `stale_projection` failure and do not checkpoint.
6. Normalize the patch path set from evidence and validate it against active
   contracts before apply. A path outside all contracts fails with
   `contract_violation` or the relevant workspace class (`worktree_alias`,
   `acl_workability`, or `sandbox_isolation`), not a model repair prompt.
7. Run `git apply --check --index --3way --binary <patch>` against the current
   HEAD. If it fails, reset to the recorded pre-apply HEAD, clean generated
   untracked files captured by the failed apply attempt and listed in the patch
   path set, prove no-dirty, record `merge_conflict`, and stop. Untracked files
   outside the patch path set are never deleted automatically; they make recovery
   fail closed to `poisoned` or a workspace repair route.
8. Run `git apply --index --3way --binary <patch>`. For pure file mode changes
   or deletes, verify the index shape using porcelain v2 output rather than
   trusting apply stdout. If apply fails despite the prior check, run the same
   reset, bounded cleanup, no-dirty proof, and `merge_conflict` recording path as
   step 7.
9. Compute the applied path set from `git diff --cached --name-only -z` plus
   unstaged porcelain paths. It must be a subset of the validated contract path
   set and must equal the normalized patch path set unless the patch is a
   deterministic no-op already present in HEAD.
10. Record each target's `applied_head` and `status = 'applied'` in
    `merge_queue_repo_targets`, then record merge proof evidence with base
    commit, pre-apply head, current head, patch digest, path set, rebase
    decision, and tree/index digests. Then move to `verifying`.
11. Re-run required deterministic gates against the applied canonical state.
    Gate failure records typed failure evidence, resets the repo to pre-apply
    HEAD when no commit exists, proves no-dirty with a workspace snapshot, marks
    affected repo targets `failed`, and leaves the item `failed`. Gate approval
    writes the real aggregate id to `post_apply_gate_evidence_id`; payload
    `post_apply_gate_evidence_ids` is only a display mirror.

Direct apply and deterministic rebase are the only apply modes. The queue never
asks an implementation agent to edit canonical repos to resolve a conflict; it
routes a typed `merge_conflict` failure through Slice 7.

## Commit And No-Dirty Proof

Commit is performed only after post-apply gates approve.

1. Reconfirm the lease token and feature advisory lock.
2. Recompute changed paths from index and worktree. They must match the merge
   proof path set and active contracts.
3. Stage with `git add --all -- <validated paths>` per repo. Do not stage the
   entire feature root.
4. Commit with a stable message:
   `feat: group {group_idx} - {task names}`.
5. Add trailers or structured commit metadata where supported:
   `Feature-ID`, `DAG-SHA256`, `Group-Index`, `Merge-Queue-Item`,
   `Patch-Evidence`, `Gate-Evidence`, and `Contracts`.
6. On hook/pre-commit failure, capture stdout, stderr, status before/after, and
   path hints into a `commit_hygiene` typed failure and project the existing
   `dag-commit-failure:g{group_idx}:{stage}` compatibility artifact. Do not
   enter broad implementation repair by default.
7. After commit, read `git rev-parse HEAD`, `git rev-parse HEAD^{tree}`, and
   `git status --porcelain=v2 -z --untracked-files=all`; store per-repo
   `result_commit`, `tree_sha`, and `status = 'committed'` on
   `merge_queue_repo_targets`.
8. Prove no dirty state with all of:
   empty porcelain v2 status, `git diff --quiet`, `git diff --cached --quiet`,
   and a `workspace_snapshots.no_dirty = true` row for each target repo. Store
   the snapshot id on the repo target and advance that target to
   `status = 'clean'`.
9. If any proof fails after a commit, record `checkpoint_contradiction`, leave
   `result_commit` populated, keep the row non-done, and require recovery to
   prove clean or poison. Never write `dag-group:*` from a dirty state.
10. Record `dag-commit-proof:g{group_idx}` evidence containing structured
    `RepoCommitProof` rows. Then move to `integrated`; checkpointing is owned by
    the group coordinator once every expected lane is integrated.

The proof must be strong enough for resume to distinguish:

- No commit happened, so reset/reapply is safe.
- Commit happened and clean proof exists, so checkpoint projection can recover.
- Commit happened but clean proof is missing or false, so checkpoint is blocked.

## Checkpoint Transaction

Git commit and database commit cannot be one physical transaction. The workflow
atomicity guarantee is therefore: once `result_commit` and no-dirty proof are
recorded, recovery can always finish or safely block the checkpoint from typed
state. `dag-group:*` is visible only from the checkpoint transaction below.

`GroupMergeCoordinator.checkpoint_group(coverage, token)` runs under the feature
advisory lock and a single journal transaction:

1. Re-read all queue rows for `(feature_id, dag_sha256, group_idx) FOR UPDATE`.
   Re-read all `merge_queue_task_coverage` rows for those queue rows in the same
   transaction. First compute coverage and supersession from coverage rows: every
   expected task id must be covered exactly once by a candidate lane. A failed
   source row for that task id is ignored only when exactly one candidate lane
   cites it through the real `retry_of_queue_item_id` column, the source row is
   terminal `failed`, the source has empty `result_commit`, the source and
   replacement `merge_queue_task_coverage` task sets match exactly, and the
   unique retry-source index proves there is no competing replacement. Poisoned
   rows are never superseded by automatic retry and always block checkpoint until
   an explicit operator or repair-migration clearance creates new typed evidence.
   Failed rows without such a covered replacement also block checkpoint. If the
   candidate lanes are already
   `status = 'done'` with the same `checkpoint_gate_evidence_id`,
   `checkpoint_evidence_id`, `checkpoint_projection_id`,
   `checkpoint_coverage_digest`, and `checkpoint_body_sha256`, return the
   existing `MergeResult` as an idempotent success. Otherwise
   require every candidate lane to be in `status IN ('integrated', 'checkpointing')`
   with `result_commit <> ''`, merge proof evidence, post-apply gate evidence,
   commit proof evidence, no-dirty snapshots, and, for any `checkpointing` row,
   real `checkpoint_coverage_digest` and `checkpoint_body_sha256` values that
   match the current coverage and reconstructed body. The coordinator computes
   the current `checkpoint_coverage_digest` from locked queue rows, coverage
   rows, expected tasks, and retry supersession links, then creates a Slice 06
   `checkpoint_gate` request over the locked coverage set. If
   the transaction needs an observable in-progress state, set covered rows to
   `checkpointing` and populate real `checkpoint_coverage_digest` and
   `checkpoint_body_sha256` columns inside the same transaction before writing
   checkpoint-gate evidence.
2. Reconstruct the legacy checkpoint body from typed rows, not latest artifact
   scans:

   ```json
   {
     "group_idx": 12,
     "task_ids": ["T1", "T2"],
     "results": ["ImplementationResult model dumps"],
     "verdict": "approved",
     "commit_hash": "abc123"
   }
   ```

3. Insert or load an approved `evidence_nodes(kind='checkpoint_gate')` with an
   idempotency key derived from `(feature_id, dag_sha256, group_idx,
   integrated_queue_item_ids_digest, result_commit, body_sha256)`. The node reads
   coverage, merge proof, post-apply gate, commit proof, no-dirty snapshots, and
   checkpoint body digest. Its output refs include the checkpoint body evidence
   node used for `checkpoint_evidence_id`.
4. Project `dag-merge-proof:g{group_idx}` and `dag-commit-proof:g{group_idx}`
   if their projection links do not already exist.
5. Project `dag-group:{group_idx}` with owner `merge_queue` and source
   `merge_queue_items`. The body must preserve the current legacy shape used by
   checkpoint readers.
6. Insert the dashboard/outbox event equivalent to `dag_group_checkpoint`.
7. Set the real columns `checkpoint_gate_evidence_id`,
   `checkpoint_evidence_id`, `checkpoint_projection_id`,
   `checkpoint_coverage_digest`, and `checkpoint_body_sha256` on every covered
   lane, mirror those ids and digests into each lane payload only for bounded
   display, set every covered lane to `status = 'done'`, clear lease fields, and
   update `updated_at`.

If the transaction fails at any point, no typed evidence, artifact projection,
projection link, outbox row, or terminal state from that attempt may be visible.
Recovery reruns the same transaction using the same idempotency keys.

## Refactoring Steps

These are implementation steps, not phased production rollout steps. They land
together before the new execution control plane is enabled.

1. Extract git operations from implementation phase into
   `execution/git_service.py`: status, apply, path validation helpers, commit,
   hook failure parsing, and no-dirty proof.
2. Add queue persistence methods in `execution/journal.py`: enqueue, claim,
   heartbeat, recover expired active lease, transition with lease fencing,
   record queue evidence, record queue failure, and checkpoint projection.
3. Add `execution/merge_queue.py` with the state machine, feature advisory lock,
   patch apply/rebase algorithm, post-apply gate invocation, commit proof, and
   checkpoint transaction.
4. Replace direct implementation commit at `implementation.py:5159` with
   sandbox patch capture plus queue enqueue. Remove the canonical commit from
   the implementation worker path.
5. Replace checkpoint commit/projection at `implementation.py:4166` and
   `implementation.py:4203` with queue success. The verifier still produces
   gate evidence; the queue owns commit/checkpoint authority.
6. Route queue failures through `execution/failure_router.py` using typed
   failure classes: `merge_conflict`, `commit_hygiene`,
   `checkpoint_contradiction`, `contract_violation`, `worktree_alias`,
   `acl_workability`, `sandbox_isolation`, `verifier_provider`, and
   `verifier_context`.
7. Preserve compatibility shims for tests during the landing, but production
   call sites must not use `_commit_repos`, `_commit_repos_in_root`, or
   `_commit_group` for new execution control plane attempts.
8. Add startup validation that fails closed if the queue, journal projections,
   sandbox patch evidence, or gates are unavailable.

## Persistence And Artifact Compatibility

- `merge_queue_items` is authoritative for canonical integration state.
- `dag-group:*` remains the legacy checkpoint projection and is written only
  after queue success.
- `dag-task:*` remains dispatcher-owned attempt evidence. The queue must never
  write, rewrite, delete, or infer checkpoint completion from `dag-task:*`.
- `dag-verify:g{group_idx}:checkpoint-commit` remains available for current
  commit failure consumers when a queue commit fails.
- Commit failures still project `dag-commit-failure:*` for current consumers.
- Queue result includes typed evidence ids linking contracts, patches, gates,
  merge proof, commit proof, no-dirty snapshots, and checkpoint projection.
- Compatibility artifacts are supported output, not a temporary comparison
  stream. There is no dual writer for the same legacy key family.

## Edge Cases And Failure Handling

- Crash before apply: lease expires and another worker claims normally.
- Crash after `pre_apply_recorded` but before git mutation: recovery verifies the
  live HEAD still equals `pre_apply_head`, proves no-dirty, and may continue
  applying under a new lease.
- Crash while applying before commit: recovery acquires the feature lock,
  resets target repos to `merge_queue_repo_targets.pre_apply_head`, proves
  no-dirty, and either reapplies or fails typed.
- Crash after apply while gates run: recovery uses pre-apply heads if
  `result_commit` is empty; it does not trust partially written gate artifacts
  unless linked from typed evidence.
- Stale base commit: deterministic three-way apply is allowed only when
  `base_commit` is an ancestor of current HEAD; otherwise typed conflict.
- Patch path drift after rebase: fail with `contract_violation` or
  `merge_conflict` and reset before checkpoint.
- Commit hook failure: route as `commit_hygiene`, project commit failure
  compatibility artifacts, and do not checkpoint.
- Crash after commit before clean proof: recovery rechecks result commit and
  no-dirty; if dirty paths remain, block checkpoint with `checkpoint_contradiction`.
- Crash after result commit and clean proof before projection: recovery writes
  the checkpoint transaction exactly once.
- Dirty state after commit: fail or poison the queue item and do not checkpoint.
- Concurrent queue workers: row lease fences item mutation; feature advisory
  lock serializes canonical repo mutation and checkpoint projection.
- Concurrent duplicate claim attempts: only the atomic claim update may return a
  row. All later transition, heartbeat, failure, poison, and checkpoint writes
  include `(id, lease_owner, lease_version)` or a coordinator-held feature lock.
- Missing required gate evidence ids: fail with `checkpoint_contradiction`
  before git mutation.
- Duplicate enqueue: idempotency key returns the existing compatible row or
  raises `IdempotencyConflict` with no partial state.

## Rollback And Recovery Table

Rollback is deploy-level, but active queue rows still need deterministic
recovery. The table below is the operational playbook for both rollback and
normal crash recovery.

| Status | Recovery action | Allowed terminal or next state |
| --- | --- | --- |
| `queued` | Cancel if the feature is superseded or deployment is rolled back before claim. No repo mutation exists. | `cancelled` or `leased` |
| `leased` | If lease is expired, reclaim by incrementing `lease_version`. If rollback starts before apply, clear lease and cancel. | `leased`, `applying`, or `cancelled` |
| `applying` | Acquire feature lock. If `result_commit` is empty, reset each repo to `merge_queue_repo_targets.pre_apply_head` when present, clean failed apply residue, prove no-dirty, then reapply or fail typed. | `verifying`, `failed`, or `poisoned` |
| `verifying` | If commit has not happened, rerun gates from typed evidence or reset to pre-apply heads and fail. Never checkpoint from pre-queue gate evidence alone. | `committing`, `failed`, or `poisoned` |
| `committing` | Inspect git. If result commit exists, record it and run no-dirty proof. If commit failed, record `commit_hygiene`. If state is ambiguous, poison. | `integrated`, `failed`, or `poisoned` |
| `integrated` | No worker lease recovery. The group coordinator includes the lane in coverage and checkpoints once all expected task ids are integrated. | `checkpointing`, `done`, or `poisoned` |
| `checkpointing` | Require all covered lanes, result commits, merge proof, post-apply gate proof, commit proof, and no-dirty snapshots, then rerun the idempotent group checkpoint transaction. | `done` or `poisoned` |
| `done` | No rollback. The commit and checkpoint are canonical append-only history. | `done` |
| `failed` | Preserve evidence. If the router selected retry, retry only through a new router-created sandbox patch/queue item. If the router selected `quiesce`, remain terminal until scheduler/operator workflow resumes from typed evidence. Do not mutate this terminal row. | `failed` |
| `poisoned` | Preserve evidence and stop automatic resume for the feature until operator or repair migration resolves it. | `poisoned` |
| `cancelled` | No repo mutation may have happened. If mutation evidence exists, this is invariant corruption and must become `poisoned`. | `cancelled` or `poisoned` |

Rollback must not hand a partially queued feature to the legacy direct commit
path. A deploy rollback either drains/recover-checkpoints already committed
queue items or stops the feature with typed evidence that explains why it cannot
resume automatically.

## Tests

Schema and idempotency:

- Enqueue requires active contracts, immutable patch evidence, and pre-queue
  gate evidence.
- Duplicate enqueue with the same idempotency key returns the existing row only
  when `merge_queue_items.request_digest`,
  `merge_queue_task_coverage.coverage_digest`, and
  `merge_queue_repo_targets.target_digest` also match.
- Duplicate enqueue with the same key but different patch, coverage, repo target,
  gate, or contract digest raises `IdempotencyConflict`.
- Enqueue rejects a second live queue item that covers the same
  `(feature_id, dag_sha256, group_idx, task_id)` unless it is the one authorized
  retry replacement for a terminal failed source row with identical coverage.
- Schema constraints reject unknown statuses and terminal `done` rows without
  required proof ids.
- Enqueue may include `payload.task_ids` for display, but authoritative rejection
  is based on `merge_queue_task_coverage`: empty coverage rows, unknown task ids,
  inactive contracts, duplicate task rows for the item, contract/coverage scope
  FK mismatches, or contracts for tasks outside the lane all fail in the enqueue
  transaction.
- Enqueue may include `payload.repo_targets` for display, but authoritative
  rejection is based on `merge_queue_repo_targets`: empty repo targets,
  duplicate repo ids, noncanonical repo paths, outside-root paths, missing
  workspace authority snapshots, or parent-scope FK mismatches all fail in the
  enqueue transaction.
- `retry_merge` never mutates a terminal failed source row back to active; it
  creates one replacement queue item with the real `retry_of_queue_item_id`
  column set and a fresh idempotency key that includes the replacement
  lane/base/head digest. Any payload mirror is display-only.
- `retry_merge` enqueue rejects a source row outside the same feature/DAG/group,
  a source not in terminal `failed`, a source with `result_commit`, a poisoned or
  done source, mismatched `merge_queue_task_coverage` rows, and any source that
  already has a non-cancelled replacement row.
- An `unknown_write_set=True` contract enqueues only as
  `integration_lane="task:{task_id}"` with exactly one task id; attempts to
  combine it with another task fail before queue claim.

Lease and concurrency:

- Concurrent `claim` calls return one winner for a `queued` row.
- A stress test proves claim is a single atomic update/returning operation, not a
  select-then-update sequence.
- Non-expired `leased` rows cannot be claimed by another owner.
- Expired `leased` rows can be claimed by another owner and increment
  `lease_version`.
- Stale owners cannot heartbeat or transition after a newer `lease_version`.
- Expired active rows require `recover_expired`; normal `claim` cannot take over
  `applying`, `verifying`, `committing`, or `checkpointing`.
- Heartbeat failure during a long gate fences the worker before commit.

Patch apply and rebase:

- Direct apply succeeds when HEAD equals `base_commit` and path set matches
  contracts.
- Deterministic rebase succeeds when `base_commit` is an ancestor of current
  HEAD and `git apply --3way --check` passes.
- Non-ancestor base fails as `stale_projection` or `merge_conflict`.
- Three-way conflict resets to pre-apply HEAD and proves no-dirty.
- Crash after `pre_apply_recorded`, after failed apply residue, and after
  `applied` each recover from `merge_queue_repo_targets` real columns, not
  payload mirrors.
- Patch that adds, deletes, renames, chmods, or includes binary content is
  validated against contracts using git status/diff, not stdout parsing.
- Patch that touches outside-contract paths fails before apply.
- Post-apply gate failure resets uncommitted changes and blocks checkpoint.

Commit and clean proof:

- Successful commit records result commit, tree SHA, structured repo proofs, and
  no-dirty workspace snapshot ids in `merge_queue_repo_targets` and commit-proof
  evidence.
- Repo target status progresses through `pre_apply_recorded`, `applied`,
  `committed`, and `clean`; checkpoint refuses rows whose parent status and repo
  target statuses disagree.
- Clean repo no-op group does not create an empty commit unless explicitly
  marked as a no-op checkpoint by gates and contracts.
- Pre-commit/husky failure records `commit_hygiene`, projects
  `dag-commit-failure:*`, and does not write `dag-group:*`.
- Dirty status after commit records `checkpoint_contradiction` and blocks
  checkpoint.
- Multi-repo group stores structured per-repo proofs and preserves the legacy
  comma-separated `commit_hash` string in `dag-group:*`.
- Multi-lane group stores structured per-lane proofs and does not checkpoint
  until all expected task ids are covered by `integrated` lanes.

Checkpoint transaction and recovery:

- Successful checkpoint writes checkpoint evidence, `dag-merge-proof:*`,
  `dag-commit-proof:*`, `dag-group:*`, projection links, outbox event, and
  updates all covered lanes from `integrated`/`checkpointing` to `done` in one
  transaction.
- Inject failure after checkpoint evidence insert and assert no projection link,
  artifact, outbox row, or `done` status is visible.
- Inject failure after artifact projection and assert transaction rollback
  removes artifact and projection link.
- Crash after result commit before projection recovers and writes one
  `dag-group:*`.
- Repeating checkpoint with the same idempotency keys is a no-op success.
- Repeating checkpoint from `checkpointing` rows succeeds only when the real
  `checkpoint_coverage_digest` and `checkpoint_body_sha256` columns match the
  current locked coverage and reconstructed body.
- Checkpoint refuses to reconstruct from latest legacy artifact bodies when
  typed evidence is missing.
- Checkpoint refuses if any expected task id is missing, covered by two active
  lanes, covered by any poisoned lane, covered by a failed lane that is not
  superseded by exactly one covered replacement lane via
  the real `retry_of_queue_item_id` column, or covered by a lane whose `dag_sha256`
  differs from the group coordinator.
- Checkpoint tests mutate `payload.task_ids` and `payload.group_expected_task_ids`
  after enqueue and prove approval still follows `merge_queue_task_coverage`
  rows plus the effective DAG, not JSON payload mirrors.
- Recovery tests mutate `payload.repo_targets`, `payload.pre_apply_heads`, and
  `payload.repo_commit_proofs` after enqueue and prove apply/recovery/checkpoint
  still follow `merge_queue_repo_targets` rows, not JSON payload mirrors.

Compatibility and ownership:

- Queue never writes or rewrites `dag-task:*`.
- Projection helper rejects queue attempts to write dispatcher-owned keys.
- Resume after `dag-task:*` but before `dag-group:*` treats the group as
  incomplete and routes to merge recovery.
- Existing commit failure tests remain covered while their implementation moves
  to `git_service.py`.
- Static guard fails if new production code writes `dag-group:*` outside
  journal projection helpers.

Rollback/recovery table:

- Each status in the rollback table has a test that proves the documented next
  state and no-dirty requirement.
- Rollback before apply can cancel; rollback after apply cannot hand off to the
  legacy commit path.
- Poisoned rows stop automatic feature resume and preserve typed evidence.

End-to-end:

- Implementation sandbox output becomes canonical after the queue lane reaches
  `integrated`; the group checkpoint becomes visible only after the coordinator
  advances all covered lanes to `done`.
- Repair sandbox output uses the same queue path as initial implementation.
- Commit-only failures route to commit hygiene repair, not broad product repair.
- Single atomic landing startup guard fails closed when any required queue
  dependency is unavailable.

## Acceptance Criteria

- Canonical repos are mutated only by merge queue workers.
- New execution control plane attempts have exactly one product-authoritative
  landing path: sandbox patch evidence through durable merge queue.
- Queue lanes may split a group for contract isolation, but group checkpoint
  remains a single coordinator-owned projection after all expected task ids are
  covered.
- Group checkpoint is atomic from the workflow perspective: either no
  checkpoint is visible, or commit/no-dirty/projection are all recorded or
  recoverable from typed state.
- Commit-only failures never enter broad implementation repair by default.
- Resume can determine exact merge/checkpoint state from typed queue rows.
- `dag-task:*` remains attempt evidence and is never treated as checkpoint or
  canonical integration proof.
- There is no phased production rollout, no shadow merge queue, and no runtime
  fallback from active queue rows to legacy direct commit helpers.
- Rollback preserves typed audit rows, compatibility artifacts already written,
  and sandbox patch evidence.

## Rollout/Rollback Notes

There is no phased production rollout for this slice. The production change
lands as one atomic feature after schema, journal store, workspace authority,
contracts, sandbox patch capture, gates, failure router, merge queue worker,
compatibility projections, recovery, and tests pass together.

Any environment flag for this slice is a startup readiness guard only. It must
not create a runtime mode where some new execution control plane attempts use
the merge queue while other new attempts continue through legacy direct commit
helpers.

Pre-landing validation happens in tests and local/staging runs only:

- All tests listed above pass.
- Existing legacy-only in-flight features are classified once and continue on
  the legacy executor with no merge-queue rows or mixed typed writes. A feature
  may enter the merge queue only by an explicit restart from a validated
  checkpoint under the complete control plane; no automatic synthetic migration
  is allowed.
- Startup guard verifies queue schema, projection ownership, sandbox patch
  capture, gate runner, feature advisory locks, and git service before accepting
  new implementation or repair attempts.

Rollback is a deploy rollback, not a per-feature runtime switch:

- Stop new implementation, repair, and queue claims.
- Let already-committed queue items in `integrated` or `checkpointing` complete
  the idempotent group checkpoint transaction when clean proof exists.
- Recover or poison active `applying`, `verifying`, or `committing` rows by the
  rollback table before resuming the feature.
- Preserve `merge_queue_items`, typed failures, evidence, projections, sandbox
  patches, and compatibility artifacts for audit.
- Do not delete additive tables during rollback.
- Do not route partially queued work to `_commit_repos`, `_commit_repos_in_root`,
  or `_commit_group`.

## Cross-Slice Dependencies

- Slice 1 stores queue rows, typed evidence, typed failures, and projections.
- Slice 2 provides canonical repo snapshots and workspace no-dirty proofs.
- Slice 3 provides active contracts and path ownership.
- Slice 4 provides immutable sandbox patch evidence.
- Slice 5/6 provide dispatcher/gate evidence needed before enqueue and after
  canonical apply.
- Slice 7 routes queue failures.
- Slice 11 extracts git and merge queue code into stable module boundaries.
