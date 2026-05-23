# 04. Sandbox Runner

## Objective

Ensure implementers and repair agents work in isolated sandboxes, not canonical
repos. Canonical repos are mutated only by the durable merge queue after gates
and contracts pass. The sandbox runner is the execution boundary: it allocates
ephemeral repo copies from typed base snapshots, binds each supported runtime to
that copy, captures a deterministic patch, and releases or preserves evidence
without treating sandbox contents as product-authoritative state.

## Current Code Citations

- Existing workspace manager: [WorkspaceManager](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/services/workspace.py:312).
- Isolated clone setup: [workspace.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/services/workspace.py:432).
- Worktree setup in implementation flow: [_ensure_task_worktrees](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1635).
- Runner workspace override: [_runner.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/_runner.py:302).
- Claude cwd and sandbox behavior: [claude.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/claude.py:536).
- Codex sandbox flags: [codex.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/codex.py:547).
- Claude pool cwd manifest handling: [claude_pool.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/claude_pool.py:1045).

## Current Failure Mode From `8ac124d6`

Agents and repair routes could interact with canonical or alias paths directly.
Prompt instructions and runtime-specific guards were not a uniform sandbox
contract. This allowed stale alias writes, ACL-specific failures, and canonical
dirty state to appear before the workflow had verified ownership. A runtime
could also report task success even when its useful changes lived in a stale
worktree, escaped the declared task contract, or were never captured as a patch
that the merge queue could replay.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/sandbox.py`.

```python
class SandboxSpec(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    attempt_no: int
    task_ids: list[str]
    repo_ids: list[str]
    base_snapshot_ids: list[int]
    base_commits: dict[str, str]
    mode: Literal["wave", "task", "repair", "canonicalization"]
    writable_roots: list[str]
    readonly_roots: list[str]
    contract_ids: list[int]
    ttl_seconds: int = 86_400

class SandboxLease(BaseModel):
    sandbox_id: str
    root: str
    repo_roots: dict[str, str]
    base_commits: dict[str, str]
    expires_at: str
    owner: str
    status: Literal[
        "allocating",
        "allocated",
        "binding",
        "running",
        "capturing",
        "captured",
        "released",
        "retained",
        "failed",
        "poisoned",
    ]
    patch_summary_ids: list[int]

class RuntimeWorkspaceBinding(BaseModel):
    sandbox_id: str
    runtime: Literal["claude", "codex", "claude_pool"]
    cwd: str
    workspace_override: str
    repo_roots: dict[str, str]
    writable_roots: list[str]
    readonly_roots: list[str]
    blocked_roots: list[str]
    env: dict[str, str]
    role_metadata: dict[str, Any]
    manifest_path: str | None = None

class SandboxRepoPatch(BaseModel):
    repo_id: str
    base_commit: str
    head_commit: str | None
    changed_paths: list[str]
    created_paths: list[str]
    modified_paths: list[str]
    deleted_paths: list[str]
    renamed_paths: list[tuple[str, str]]
    binary_paths: list[str]
    mode_changed_paths: list[str]
    executable_bit_changed_paths: list[str]
    outside_contract_paths: list[str]
    diff_sha256: str
    diff_artifact_id: int

class PatchCaptureResult(BaseModel):
    sandbox_id: str
    patch_summary_ids: list[int]
    repo_patches: list[SandboxRepoPatch]
    empty: bool
    clean_after_capture: bool

class SandboxRunner:
    async def allocate(self, spec: SandboxSpec) -> SandboxLease: ...
    async def bind_runtime(self, lease: SandboxLease, runtime: str) -> RuntimeWorkspaceBinding: ...
    async def capture_patch(self, lease: SandboxLease) -> PatchCaptureResult: ...
    async def release(self, lease: SandboxLease, disposition: str) -> None: ...
```

`SandboxRepoPatch` is the capture-time detail object. The persisted evidence id
is still the Slice 03 `PatchSummary.id`; `PatchCaptureResult.patch_summary_ids`
contains those evidence ids so gates and the merge queue use the same patch
identity.

## Sandbox Allocation Lifecycle

1. Build `SandboxSpec` only after workspace authority preflight and contract
   compilation have stored base snapshot ids, repo ids, and contract ids.
2. Create a deterministic idempotency key from feature id, DAG sha, group idx,
   attempt number, mode, repo ids, base commits, and contract ids. A retry with
   the same key returns the existing non-terminal lease.
3. Insert a `sandbox_leases` row with status `allocating`, then create
   `.iriai/features/{slug}/sandboxes/g{group_idx}/attempt-{attempt_no}/`.
   Filesystem creation is under a per-feature allocation lock so two dispatch
   workers cannot claim the same path with different base commits.
4. For each repo, create a standalone clone or copy-only checkout rooted under
   the sandbox. The existing workspace manager already uses standalone
   no-local clones for feature repo copies; the sandbox runner reuses that
   behavior but pins every repo to the recorded base commit before dispatch.
5. Verify every repo root is a real directory, not a symlink, has a `.git`
   directory or file contained by the sandbox, and that `HEAD` matches
   `SandboxSpec.base_commits[repo_id]`. Mismatches fail before runtime binding.
6. Write `sandbox-manifest.json` with lease id, repo roots, base commits,
   contract ids, blocked canonical roots, lease expiration, allocation lock
   owner, and expected runtime. Mark the lease `allocated` only after manifest,
   repo setup, and repo binding rows are durable.
7. `bind_runtime` transitions `allocated -> binding -> running`, records the
   runtime binding, and returns a binding object that the dispatcher passes to
   `Ask`/runtime invocation.
8. Runtime exit freezes the lease: no more runtime bindings are issued, status
   moves to `capturing`, and patch capture starts even if the runtime failed.
9. Successful capture stores patch evidence ids and transitions to `captured`.
   `release` then deletes clean sandboxes and transitions to `released`, or
   preserves failed/diagnostic sandboxes behind an artifact pointer and
   transitions to `retained`.
10. A recovery sweeper resumes leases in `allocating`, `allocated`, `binding`,
    `running`, `capturing`, `captured`, or `retained` by consulting the manifest
    and process heartbeat. It either captures a partial patch, marks the lease
    failed, records retained evidence, or releases stale filesystem state
    without touching canonical repos.

Lifecycle invariants:

- A runtime can bind to a lease at most once unless the previous binding failed
  before process start.
- A captured lease is immutable. Repairs allocate a new attempt lease instead of
  reopening an old sandbox.
- Lease cleanup is best-effort and never changes attempt, gate, or merge
  decisions. Evidence survives through typed records and diff artifacts.
- Allocation and release are idempotent over typed rows first and filesystem
  state second. A missing already-released directory is cleanup success; a
  missing active repo root is evidence loss and routes through capture failure.

## Canonical Mutation Prohibition

Sandbox runner is an isolation boundary, not a convenience wrapper around
canonical worktrees:

- Canonical repo roots, feature alias roots, and legacy worktree roots are
  always `blocked_roots` for implementer and repair leases. They are never
  included in `writable_roots`, even temporarily during allocation, capture, or
  cleanup.
- Runtime adapters must pass only sandbox repo roots as cwd/workspace values.
  Any legacy path value that resolves to canonical, alias, or feature worktree
  roots is a binding failure, not a warning.
- Patch capture may read recorded base commits and sandbox working trees, but it
  must not run `git add`, `git apply`, `git checkout`, `git reset`, `git
  commit`, `git clean`, or equivalent mutating operations against canonical
  repos.
- Cleanup deletes only paths covered by an active or retained sandbox manifest.
  The cleanup implementation refuses to delete a path unless the path is under
  the sandbox root, the manifest id matches the lease row, and no repo root
  resolves into a canonical or alias root.
- The only production component allowed to mutate canonical repos for these
  attempts is Slice 08 durable merge queue after it has claimed immutable patch
  evidence ids and passed its gates.

## Runtime Bindings

Claude:

- Pass `Workspace(path=binding.cwd)` so `_build_options` sets `cwd` to the
  sandbox root and enables the existing write guard for Edit/Write calls.
- Force `role.metadata["sandbox"] = True` for implementer and repair roles.
  If Claude cannot install the write guard or sandbox option, fail closed before
  query dispatch.
- Populate `blocked_roots` with canonical repo roots and alias roots. The write
  guard must reject resolved paths outside `binding.writable_roots`, including
  symlink escapes.
- Keep package/cache read access, such as the existing npm cache add-dir, out of
  writable roots. Cache writes must go under a sandbox-local temp/cache path.

Codex:

- Invoke Codex with `-C binding.cwd` through the existing workspace plumbing and
  keep output, schema, trace, temp, and per-invocation `CODEX_HOME` under the
  sandbox or feature `.iriai/runtime` area.
- Product implementer and repair dispatch must not use role metadata that emits
  `--dangerously-bypass-approvals-and-sandbox`. If a role requires that mode,
  the dispatcher records a sandbox binding failure and routes through the typed
  failure router instead of running against canonical paths.
- Default Codex write access is limited to the sandbox checkout. Read-only
  verifier roles may set `codex_read_only_shell`; write-producing roles may not.
- The binding adapter strips legacy `workspace_override` values that point to
  feature repo aliases and replaces them with the sandbox root.

Claude pool:

- Write the pool job manifest with `"cwd": binding.cwd`, plus
  `sandbox_id`, `repo_roots`, `contract_ids`, `writable_roots`, and
  `blocked_roots`.
- The pool worker refuses a queued job whose manifest cwd is absent, symlinked,
  expired, or not covered by an active sandbox lease.
- Pool stdout/stderr/result files remain pool artifacts, but their manifest
  links back to the sandbox lease. Patch capture is performed by
  `SandboxRunner`, not by the pool worker.
- A worker crash leaves the lease recoverable from the manifest and heartbeat;
  the next recovery pass captures partial filesystem state if it exists.

## Patch Capture

Patch capture is filesystem-first and replayable:

1. For each repo root in the lease, read the recorded base commit and current
   `HEAD`. If `HEAD` differs from the base, record the sandbox commits as
   evidence, but do not fail capture solely for that difference.
2. Reset a temporary capture worktree/index view to the recorded base commit,
   replace its tracked file contents with the sandbox working tree state,
   explicitly remove tracked paths that are absent from the sandbox, add
   untracked sandbox files that are not ignored by the contract/capture policy,
   and generate the patch from the recorded base. The merge queue consumes only
   this patch evidence, never sandbox commits as canonical commits.
3. Build a temporary git index from the recorded base view, add the working tree
   into that temp index, and generate
   `git diff --cached --binary --find-renames --full-index` from the temp index.
   This includes untracked, modified, deleted, renamed, binary, and file-mode
   changes without mutating the sandbox repo's normal index.
4. Independently parse `git status --porcelain=v2 -z --untracked-files=all` to
   classify created, modified, deleted, and renamed paths. Normalize paths as
   repo-relative POSIX paths.
5. Parse diff headers and/or porcelain v2 mode fields to classify mode-only
   changes, including executable bit changes, even when file contents are
   otherwise identical.
6. Resolve every changed path with `strict=False` and reject any path that
   escapes its repo root or traverses a blocked canonical/alias root.
7. Validate the changed path set against all contract ids in the lease. Store
   outside-contract paths in the patch summary evidence and block merge queue
   enqueue.
8. Store the binary-safe diff as an artifact, store digest/path metadata as
   `sandbox_patch_summary` evidence, and project a bounded compatibility
   artifact `dag-sandbox-patch:g{group_idx}:attempt-{attempt_no}:repo-{repo_id}`
   for dashboards. The manifest projection remains
   `dag-sandbox:g{group_idx}:attempt-{attempt_no}` and is written when the
   durable sandbox manifest is created.
9. Empty patches are valid evidence but never sufficient for task success when
   the contract requires file changes or generated outputs.

Patch capture never applies, rebases, commits, or checks out canonical repos.
The durable merge queue is the only component allowed to replay captured diffs
against canonical state.

Diff artifacts must be byte-stable for the same sandbox tree and recorded base:
path order is deterministic, path names are NUL-safe during internal capture,
binary diffs are stored without UTF-8 coercion, deleted binary files remain
represented in the patch, and mode-only changes are included even when
`changed_paths` would otherwise be empty under content-only comparison.

## Isolation Guarantees

- Canonical repo roots are never writable roots for implementer or repair
  runtime bindings.
- Runtime cwd, workspace override, temp dirs, output paths, trace files,
  generated schema files, and pool manifests are all sandbox or feature-artifact
  paths.
- The sandbox runner refuses symlinked repo roots and rejects changed paths that
  resolve outside the sandbox checkout.
- Read-only context roots may be mounted or copied only when the runtime adapter
  can enforce read-only access. Otherwise the runner creates a copy-only
  sandbox and omits the canonical path from runtime context.
- Prompts may describe the sandbox, but prompts are not enforcement. Enforcement
  is by cwd binding, runtime guard, blocked root checks, and patch validation.
- Sandbox contents are disposable evidence. Product authority starts only when
  Slice 08 validates and applies the captured patch in the merge queue.
- Canonical cleanliness is asserted before runtime dispatch and again after
  patch capture in integration tests. Any dirty canonical state from sandbox
  allocation, binding, capture, or cleanup is a P1 isolation regression.

## Refactoring Steps

1. Add `execution/sandbox.py` and persistence methods for leases, repo
   bindings, runtime bindings, and patch evidence.
2. Replace implementation-phase direct worktree dispatch with
   `SandboxRunner.allocate` after workspace authority preflight and contract
   compilation.
3. Make `RuntimeDispatcher` require a `RuntimeWorkspaceBinding` for all
   implementer and repair attempts; remove fallback to canonical
   `worktree_root` for those roles.
4. Adapt Claude, Codex, and Claude pool invocation paths to consume the same
   binding contract while preserving their runtime-specific options.
5. Move patch capture out of dispatcher/runtimes and into `SandboxRunner` so
   every runtime produces comparable `sandbox_patch_summary` evidence.
6. Validate captured paths against Slice 03 contracts before enqueueing any
   merge queue item.
7. Teach the failure router to classify allocation, binding, runtime,
   isolation, capture, contract, and cleanup failures separately.
8. Add a sandbox recovery sweeper that runs on resume before new dispatch.
9. Remove legacy product-authoritative canonical writes from implementer and
   repair routes in the same landing as dispatcher and merge queue integration.
10. Delete or quarantine old shadow-only sandbox toggles; production execution
    has one path for new attempts.

## Persistence And Artifact Compatibility

- `sandbox_leases` stores the lifecycle state, idempotency key, owner,
  feature id, DAG sha, group idx, attempt number, mode, and lease expiration.
- `sandbox_repo_bindings` stores repo id, sandbox repo root, base snapshot id,
  base commit, writable/read-only classification, and blocked canonical roots.
- `runtime_workspace_bindings` stores runtime name, cwd, workspace override,
  manifest path, role metadata digest, and invocation attempt id.
- `sandbox_patch_summary` evidence stores the Slice 03 `PatchSummary` identity
  plus `SandboxRepoPatch` details in a spill-backed artifact when the diff or
  path list is large.
- Project bounded dashboard artifacts such as
  `dag-sandbox:g{group}:attempt-{n}` and
  `dag-sandbox-patch:g{group}:attempt-{n}:repo-{repo_id}`. These are
  compatibility views over typed evidence, not authority.
- Do not write `dag-task:*` from raw sandbox output. Dispatcher/journal writes
  task-attempt projection only after runtime output and patch evidence are both
  accepted.
- Do not write `dag-group:*` from sandbox output directly. Only the merge queue
  can project group checkpoint state after canonical apply, gates, commit, and
  no-dirty proof.
- Atomic feature landing keeps these compatibility artifacts available for
  existing dashboards, but does not run a separate shadow production path.

## Edge Cases And Failure Handling

- Runtime cannot enforce read-only canonical root: fail closed for that runtime
  or use a copy-only sandbox that omits canonical paths entirely.
- Sandbox allocation fails due to disk, clone, or permission errors: classify
  as environment/resource failure with retry budget; no runtime starts.
- Lease expires while runtime is still running: cancel the process, mark the
  lease `poisoned`, capture partial patch if safe, and emit
  `runtime_timeout/watchdog_timeout`. The failure router owns the
  `retry_dispatch` decision for a new attempt and fresh lease.
- Runtime exits non-zero or returns malformed output: still capture the patch,
  mark runtime failure separately, and let gates/failure router decide whether
  partial evidence is useful.
- Runtime creates commits in the sandbox: record commit ids as evidence, but
  generate the mergeable patch from recorded base commit to working tree.
- Patch capture fails after runtime success: mark the task attempt incomplete,
  retain the sandbox for inspection, and do not project `dag-task:*` success.
- Patch includes outside-contract paths: capture `sandbox_patch_summary`
  evidence, block merge queue enqueue, and route deterministic contract
  violation.
- Patch contains symlink escape, absolute path artifact, or path traversal:
  mark isolation failure and poison the lease; do not enqueue or apply.
- Patch contains only chmod/executable-bit changes: persist it as a mode-change
  patch and require the contract to allow that path before enqueue.
- Patch is empty but task claims completion: route verifier/product check or
  task-output contradiction based on contract requirements.
- Sandbox base becomes stale before merge: merge queue rebases or rejects;
  sandbox runner never mutates canonical state.
- Cleanup fails after capture: leave a retained lease with expiration metadata
  and schedule cleanup retry. Cleanup failure does not invalidate captured
  evidence.
- Crash after runtime before capture: resume recovery freezes the lease,
  captures current working tree if repo roots are intact, or records a capture
  failure if evidence is unrecoverable.
- Crash after capture before release: resume reads patch evidence ids from the
  lease and retries release idempotently.
- Retained sandbox exceeds retention TTL: preserve typed evidence and diff
  artifacts, then delete only manifest-owned filesystem paths. The retained
  lease transitions to `released` with a retention-expired disposition.
- Manifest and lease row disagree on sandbox id, root, repo roots, or base
  commits: quarantine the filesystem path, mark the lease `poisoned`, and
  require operator cleanup rather than guessing which state is authoritative.

## Tests

Unit tests:

- `SandboxSpec` idempotency key changes when DAG sha, base commit, repo id,
  contract id, group idx, or attempt number changes.
- Allocation creates one standalone non-symlink repo root per repo id and pins
  `HEAD` to the recorded base commit.
- Repeating `allocate` with the same idempotency key returns the existing
  active lease instead of creating a second sandbox.
- Allocation fails closed when a repo root is a symlink, missing, or checked out
  at the wrong base commit.
- `release` is idempotent for `captured`, `failed`, and already-cleaned
  sandboxes.

Runtime binding tests:

- Claude binding passes sandbox cwd to `Workspace`, forces sandbox metadata on
  implementer/repair roles, and rejects Edit/Write paths outside writable roots.
- Codex binding emits `-C <sandbox cwd>`, stores output/schema/temp/trace paths
  under sandbox or feature `.iriai/runtime`, and rejects role metadata that
  would use `--dangerously-bypass-approvals-and-sandbox` for product write
  attempts.
- Codex read-only verifier binding may use read-only shell mode but cannot be
  used for write-producing implementation or repair attempts.
- Claude pool manifests contain sandbox cwd, sandbox id, repo roots, contract
  ids, writable roots, and blocked roots; the worker refuses expired or
  symlinked cwd values.
- Legacy actor `workspace_override` pointing to canonical or alias worktrees is
  replaced by sandbox cwd for implementer and repair roles.

Patch capture tests:

- Capture records created, modified, deleted, renamed, and binary paths.
- Capture records mode-only changes and executable-bit changes, including when
  file contents are unchanged.
- Untracked files are included in the generated binary diff without mutating
  the sandbox repo index.
- Tracked files deleted in the sandbox are explicitly removed from the temporary
  capture index and appear as deletions in the generated diff.
- Deleted binary files and binary modifications produce replayable
  `--binary --full-index` artifacts.
- Runtime-created commits are reduced to a replayable patch against the
  recorded base commit.
- Empty patch evidence is persisted and classified separately from successful
  product change.
- Changed paths are normalized to repo-relative POSIX paths.
- Symlink escapes, `..` traversal, absolute paths, and paths resolving into
  canonical roots fail isolation validation.
- Outside-contract patch is persisted as evidence and rejected before merge
  queue enqueue.
- Large diffs spill to artifacts and store only hashes/path summaries in typed
  evidence.

Recovery and failure tests:

- Runtime crash still captures partial patch evidence when repo roots remain
  valid.
- Capture failure after runtime success leaves attempt incomplete and retains
  sandbox inspection artifacts.
- Lease timeout cancels runtime, marks lease `poisoned`, and retries through a
  new attempt id.
- Crash in `allocating`, `allocated`, `binding`, `running`, `capturing`,
  `captured`, or `retained` states resumes deterministically.
- Disk-full allocation failure is classified as environment/resource failure
  and does not start a runtime.
- Cleanup retry removes released sandboxes without deleting retained evidence.

Integration tests:

- Agent-created files appear only under sandbox roots during implementation and
  repair.
- Attempted canonical writes are blocked by runtime binding or absent from the
  runtime workspace.
- Canonical repo status remains clean after allocation, runtime binding, patch
  capture, release, and cleanup retry.
- Dispatcher cannot start implementer or repair runtime without a sandbox
  binding.
- Merge queue receives only captured patch evidence ids, never a mutable
  sandbox path as authority.
- `dag-task:*` projection waits for accepted runtime output plus patch
  evidence; `dag-group:*` is never written by sandbox runner.
- End-to-end group execution shows canonical repos remain clean until Slice 08
  merge queue applies the captured patch.
- Failed sandbox can be retained and later inspected by lease id, patch evidence
  id, artifact id, and base snapshot id.
- Atomic feature landing has no production shadow/legacy dual path for new
  attempts; disabling the feature returns all new attempts to the previous
  executor only by rolling back the atomic landing.
- Startup guard refuses to enable sandbox execution unless Slice 08 merge queue,
  Slice 03 contract validation, and runtime binding enforcement are all active
  in the same deployed build.

## Acceptance Criteria

- No implementer or repair agent writes directly to canonical feature repos.
- Sandbox patch evidence is sufficient for merge queue to apply or reject changes.
- Runtime differences are hidden behind one binding contract.
- Allocation, runtime binding, patch capture, and release are durable and
  recoverable across process crashes.
- Contract violations and isolation failures are captured as evidence before
  they are routed.
- New implementation and repair attempts have no product-authoritative path
  around sandbox runner plus merge queue.
- Sandbox-only implementation becomes product-authoritative only as part of the
  same atomic landing that enables Slice 08 merge queue canonical patch apply.

## Rollout/Rollback Notes

There is no phased production rollout for this slice. The production change
lands atomically with the typed journal, workspace authority, contracts,
dispatcher boundary, gates, failure router, and merge queue needed to make
sandbox output authoritative. New implementation and repair attempts either use
the complete execution-control path or the deployed build is rolled back to the
previous executor.

Do not introduce a runtime feature flag that sends a percentage of production
attempts through the sandbox path while other new attempts use the legacy
canonical writer. Flags may disable the whole atomic execution-control path at
process startup, but they must not create per-attempt mixed authority once the
process accepts work.

Pre-landing validation happens in tests and local/staging runs only:

- Unit and integration tests above must pass.
- Existing resume fixtures must prove unfinished legacy attempts are classified
  once at startup: legacy-only features continue on the legacy executor with no
  mixed typed writes, while intentionally restarted control-plane features begin
  from a checkpoint under the fully validated typed path. There is no automatic
  synthetic migration of an in-flight legacy attempt into sandbox execution.
- A startup guard must fail closed if Slice 08 merge queue is unavailable,
  because sandbox-captured patches cannot become product state without it.

Rollback is a deploy rollback, not a per-slice runtime switch. Rollback must:

- Stop new sandbox dispatch.
- Let already-claimed merge queue items drain to a safe terminal state or be
  recovered by queue rollback rules.
- Preserve `sandbox_leases`, runtime bindings, patch evidence, and diff
  artifacts for audit.
- Refuse to hand any retained sandbox path to the legacy executor as a
  canonical workspace.

## Cross-Slice Dependencies

- Slice 2 provides canonical base snapshots.
- Slice 3 provides contract ids and allowed write surfaces.
- Slice 5 dispatches runtimes through sandbox bindings.
- Slice 8 consumes captured patches for canonical merge.
- Slice 7 routes allocation, binding, isolation, runtime, capture, and cleanup
  failures.
