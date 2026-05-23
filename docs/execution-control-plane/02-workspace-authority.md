# 02. Workspace Authority

## Objective

Create one authoritative layer for canonical repo identity, worktree aliases,
ACL/writeability, branch/head state, dirty state, and agent-writable paths. The
layer is blocking from the first dispatch after it is landed, so alias, ACL, and
workspace snapshot decisions are made once by the workflow instead of being
rediscovered by verifier, repair, commit, or supervisor code. This removes
operator involvement for classes the workflow can safely resolve.

## Current Code Citations

- Worktree registry models: [WorktreeRegistryRepo](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1395) and [WorktreeRegistry](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1422).
- Worktree setup and registry persistence: [_ensure_task_worktrees](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1635) and [_record_worktree_registry](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:1616).
- Feature workspace manager: [WorkspaceManager](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/services/workspace.py:312).
- Isolated repo clone behavior: [_clone_repo](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/services/workspace.py:425).
- Worktree alias map and path classification: [_worktree_alias_map_for_group](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9546) and [_worktree_alias_path_info](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:9651).
- Worktree alias pre-dispatch guard: [_run_worktree_alias_pre_dispatch_guard](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:13891), invoked before task resume and model dispatch at [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4833).
- Agent writeability helper: [_path_agent_writable](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:545).
- Current ACL normalization and writeability preflight: [_normalize_feature_workspace_cleanup_permissions](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:11785), [_normalize_dag_workspace_acl](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:12023), and [_dag_workspace_writeability_problems](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:12263).
- Initial dispatch ACL gate: [implementation.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:4888).
- Runtime workspace override handling: [_runner.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/workflows/_runner.py:302).
- Claude write guard sandbox behavior: [claude.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/claude.py:536).
- Codex sandbox flags: [codex.py](/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/runtimes/codex.py:547).

## Current Failure Mode From `8ac124d6`

The workflow saw `iriai-studio-backend` versus `iriai-studio-backend-wt`
divergence and ACL failures where bridge/operator checks did not match actual
agent user writeability. The verifier read canonical paths while evidence or
fixes were sometimes created in aliases or non-canonical roots.

## Proposed Interfaces/Types

Implement `src/iriai_build_v2/workflows/develop/execution/workspace_authority.py`.

Core types:

```python
class RepoIdentity(BaseModel):
    repo_id: str
    repo_name: str
    role: str
    workspace_relative_path: str
    canonical_path: str
    source_path: str | None
    alias_paths: list[str]
    remote_url: str | None
    remote_fingerprint: str | None
    branch: str | None
    head_sha: str | None
    git_common_dir: str | None
    source_git_common_dir: str | None
    identity_kind: str
    identity_value: str
    writable_task_ids: list[str]
    read_only_task_ids: list[str]
    safety_status: str
    safety_reasons: list[str]
    identity_evidence_digest: str

class CanonicalRepoRegistry(BaseModel):
    feature_id: str
    feature_slug: str
    feature_root: str
    registry_version: str
    repos: list[RepoIdentity]
    aliases: dict[str, str]
    collisions: list[dict[str, str]]
    blocked: bool
    blockers: list[dict[str, str]]
    registry_digest: str

class CanonicalPathResolution(BaseModel):
    original_path: str
    canonical_path: str
    repo_id: str | None
    path_kind: Literal["canonical", "alias", "outside_root", "unknown_repo"]
    alias_path: str | None
    alias_exists: bool
    canonical_exists: bool
    divergent: bool
    symlink_blocker: str | None
    repair_route: str
    reasons: list[str]

class PathTarget(BaseModel):
    raw_path: str
    action: Literal["read", "create", "modify", "delete", "stage"]
    task_id: str | None
    contract_id: int | None
    source: Literal["task", "contract", "verifier", "repair", "commit", "merge"]

class AclTarget(BaseModel):
    repo_id: str
    raw_path: str
    canonical_path: str
    action: str
    nearest_existing_parent: str | None

class WorkspacePreflight(BaseModel):
    approved: bool
    resolutions: list[CanonicalPathResolution]
    acl_targets: list[AclTarget]
    blockers: list[dict[str, str]]
    repair_routes: list[str]
    snapshot_required: bool

class AclNormalizationResult(BaseModel):
    approved: bool
    changed: list[dict[str, str]]
    already_ok: list[dict[str, str]]
    warnings: list[dict[str, str]]
    failed: list[dict[str, str]]
    denied_targets: list[AclTarget]
    repair_route: str | None

class WorkspaceSnapshot(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    attempt_id: int | None
    stage: str
    repo_id: str
    role: str
    canonical_path: str
    workspace_relative_path: str
    source_path: str | None
    remote_url: str | None
    remote_fingerprint: str | None
    branch: str | None
    head_sha: str | None
    git_common_dir: str | None
    source_git_common_dir: str | None
    case_sensitivity: Literal["case_sensitive", "case_insensitive", "unknown"]
    index_digest: str
    worktree_status_digest: str
    dirty_paths: list[str]
    staged_paths: list[str]
    untracked_paths: list[str]
    forbidden_paths: list[str]
    denied_paths: list[str]
    symlink_paths: list[str]
    outside_root_targets: list[str]
    agent_writable_paths: list[str]
    alias_paths: list[str]
    registry_artifact_id: int | None
    acl_artifact_id: int | None
    compatibility_projection_artifact_ids: list[int]
    no_dirty: bool
    validated_at: str
    captured_at: str
    warnings: list[str]
    safety_status: str
    idempotency_key: str

class WorkspaceAuthority:
    async def build_registry(feature_id: str, tasks: list[ImplementationTask]) -> CanonicalRepoRegistry: ...
    async def resolve_path(path: str, registry: CanonicalRepoRegistry) -> CanonicalPathResolution: ...
    async def preflight_targets(targets: list[PathTarget], registry: CanonicalRepoRegistry) -> WorkspacePreflight: ...
    async def normalize_acl(report: WorkspacePreflight) -> AclNormalizationResult: ...
    async def route_preflight(report: WorkspacePreflight) -> list[FailureObservation]: ...
    async def snapshot(
        feature_id: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
        attempt_id: int,
        registry: CanonicalRepoRegistry,
        targets: list[PathTarget],
        task_ids: list[str] | None = None,
    ) -> list[WorkspaceSnapshot]: ...
```

`WorkspaceAuthority` is the only module allowed to decide whether a path is
canonical, whether an alias can be rewritten, whether a repo root is writable by
the agent population, or whether workspace state is clean enough for dispatch,
repair, merge, or checkpoint.

`CanonicalRepoRegistry` is the typed successor to the legacy worktree registry
artifact. Legacy `WorktreeRegistry` rows remain as compatibility projections,
but dispatch, verifier context, repair prompts, merge queue, and checkpoints read
only the authority registry. The registry is immutable for a control-plane stage:
repo rows are sorted by `(repo_id, canonical_path)`, alias maps are sorted by
longest alias path then lexical path, and `registry_digest` is computed over that
canonical JSON. Any caller that still needs legacy fields receives a projection
from this record rather than rebuilding identity or alias state.

`snapshot` always receives the DAG hash, stage, attempt id, and exact target set
from the caller. The durable `workspace_snapshots` idempotency key intentionally
describes repo state only and matches Slice 01 exactly:
`snapshot:{feature_id}:{dag_sha256}:g{group_idx or '-'}:{stage}:{repo_id}:{head_sha}:{index_digest}:{worktree_status_digest}`.
Target-specific safety is recorded separately as
`evidence_nodes(kind='workspace_preflight')` with idempotency key
`workspace-preflight:{snapshot_id}:{target_digest}`. Dispatch, gates, merge
queue, checkpoint, and recovery cite both the immutable snapshot id and the
target-proof evidence id; they must not rely on snapshot payload alone for
target-specific proof.

## Canonical Repo Identity Algorithm

Inputs are the current task list, existing `WorktreeRegistry` rows when present,
`WorkspaceManager` feature repo roots, `DIRECTORY_MAP.md` entries when available,
and git metadata from each candidate repo root. The algorithm is deterministic
and never rewrites a repo only because its basename ends with `-wt`.

1. Discover candidate roots from explicit task `repo_path`, file scopes,
   registry `repo_path`/`canonical_path`, and direct children under
   `.iriai/features/{slug}/repos`. Reject recursive repo discovery except as
   legacy evidence; the canonical execution root must be a direct repo root
   under the feature `repos` directory.
2. Normalize every candidate path by stripping line suffixes, replacing
   backslashes, removing leading `./`, resolving with `strict=False`, and
   requiring the resolved path to remain inside the feature repo root. Any
   symlink component or `..` escape is a safety blocker, not a canonicalization
   candidate.
3. Collect identity evidence: existing registry `repo_id`, workspace-relative
   repo path, `source_path`, real source path, source `git_common_dir`, local
   `git_common_dir`, normalized remote URL, branch, head SHA, role, action, and
   writable/read-only task ids.
4. Normalize remotes into `remote_fingerprint` by lowercasing scheme/host,
   stripping credentials, normalizing `git@host:org/repo(.git)` to
   `ssh://host/org/repo`, removing a trailing `.git`, and preserving
   owner/repo case only where the host is case-sensitive. Empty remotes do not
   prove identity.
5. Choose `identity_kind` and `identity_value` by precedence:
   `registry_repo_id` when non-empty and collision-free; otherwise
   `source_git_common_dir`; otherwise real `source_path`; otherwise
   `remote_fingerprint` plus directory-map repo name; otherwise
   `new_feature_repo:{feature_id}:{workspace_relative_path}` for scaffolded
   repos without upstream evidence.
6. Compute `repo_id` as
   `sha256("repo-identity-v1\0" + identity_kind + "\0" + identity_value)[:24]`
   and store the human repo name separately. If two different canonical roots
   produce the same `repo_id`, select the registry-backed direct child as
   canonical only when both roots resolve to the same source evidence; otherwise
   emit `worktree_alias/alias_canonical_divergent` and block dispatch through
   the failure router.
7. Select `canonical_path` from registry `canonical_path` if it is contained,
   direct, non-symlink, and exists; otherwise from registry `repo_path`;
   otherwise from `WorkspaceManager` output. `source_path` remains provenance
   and is never used as an agent cwd.
8. Mark all other contained roots with the same `repo_id` as aliases. A path is
   an alias only when identity evidence matches; basename similarity is
   insufficient.

Registry construction also writes a bounded `canonical-repo-registry:g{group}`
compatibility artifact that includes only repo ids, canonical workspace-relative
paths, alias edges, blocker summaries, and the typed `registry_digest`. Full
evidence lives in typed rows or spill artifacts. Downstream code must not read
the filesystem to rediscover repo roots after this point; it passes paths back to
`resolve_path` and uses returned repo ids.

## Alias Resolution Algorithm

Alias resolution runs before task resume, normal dispatch, verifier context
generation, repair routing, and merge queue validation.

1. Build alias edges from `RepoIdentity.alias_paths` to `canonical_path`.
   Compatibility support for `<repo>-wt` is allowed only when the registry
   identifies `<repo>` as the canonical execution repo, `<repo>-wt` is not itself
   a registered repo, and remote/source evidence does not conflict.
2. Resolve paths by longest alias prefix first. Absolute paths must first be
   relativized to the feature root; paths outside the feature root are rejected.
   The suffix after the alias prefix is preserved exactly after normalization.
3. Classify each path:
   `canonical` when it already points into the canonical root; `alias` when it
   maps losslessly to a canonical root; `outside_root` when containment fails;
   `unknown_repo` when no registered repo owns the path.
4. For aliases, probe disk state for both alias and canonical paths. If the
   canonical file exists and the alias file is missing or byte-identical, rewrite
   the reference as metadata-only stale projection evidence. If only the alias
   file exists, record alias-only evidence, block normal dispatch, and route a
   sandboxed canonicalization repair through the failure router and merge queue.
   If both exist and differ, route sandboxed alias adjudication with both hashes
   and no operator escalation. If neither exists, treat the reference as stale
   metadata unless the task contract requires creation.
5. Refuse to rewrite when any path component is a symlink, when an alias points
   outside the feature root after resolution, or when the alias repo has its own
   distinct identity. Those failures are workflow-blocked safety failures, not
   product repair prompts.

Alias detection records an evidence score instead of a boolean guess:

| Evidence | Meaning | Effect |
| --- | --- | --- |
| `registry_exact` | Registry row or typed repo id maps alias root to canonical root. | Sufficient when source evidence also agrees. |
| `source_git_common_dir_match` | Alias and canonical roots trace to the same source git common dir. | Strong identity evidence. |
| `source_path_match` | Real source paths match after strict containment checks. | Strong identity evidence. |
| `remote_fingerprint_match` | Normalized remotes match and directory-map names agree. | Supporting evidence only; never enough when git/source evidence conflicts. |
| `basename_suffix_wt` | Alias is `<repo>-wt`. | Compatibility hint only; never sufficient by itself. |

Every alias resolution emits a `path_canonicalization` evidence node with
`repo_id`, `alias_path`, `canonical_path`, file existence flags, optional content
hashes, and the selected repair route. Verifier context can consume rewritten
paths only from these evidence nodes, not from ad hoc string replacement.

## ACL Normalization Algorithm

ACL normalization operates only on canonical feature repo roots and only after
path resolution succeeds. It is a workflow responsibility: model agents,
verifiers, commit code, and supervisors must request writeability proof from
workspace authority instead of probing with their own user, bridge user, or
runtime-specific `os.access` checks.

This is the only pre-merge canonical filesystem mutation exception. It is
limited to metadata repair needed for runtime-user writeability:
chmod/chgrp/setgid and identical-content replacement of a non-git file when the
file bytes are unchanged. It must never change product content, stage files,
commit files, apply sandbox patches, or make a product decision. All such
metadata repairs are recorded as workspace evidence before dispatch, and merge
queue remains the only owner of product-content mutation and checkpoints.

1. Build `AclTarget` rows from task contracts, pending task file scopes, repair
   target files, verifier concern paths, and commit-hygiene paths. Relative
   targets are repo-relative unless they already include the task repo prefix;
   absolute targets must resolve inside a known canonical repo.
2. For each target, compute the mutation closure: repo root, existing parent
   chain up to the nearest existing parent, the target itself when it exists,
   and `.git` plus `.git/index` when present so staging can succeed.
3. Run safety checks before mutation: no symlink ancestors, no path outside the
   canonical repo, no recursive/nested repo root unless it is the registered
   canonical root, no chmod of symlink targets, and no atomic replacement of git
   metadata.
4. Desired permissions are group-read/write for files and group-read/write/exec
   plus setgid for directories. When the shared agent group is configured, chgrp
   to that group before chmod. When it is not configured, use the existing
   `_path_agent_writable` semantics so owner-write is trusted only outside
   feature workspaces.
5. After each attempted change, re-check with agent writability rules, not
   bridge-user `os.access`. A chmod/chgrp failure is a warning if the path is
   already agent-writable.
6. If a regular non-git file cannot be chmodded but its parent is already
   agent-writable, replace it atomically with an identical copy using the desired
   group-writable mode. This is a deterministic workflow repair, not product
   mutation.
7. For create targets whose final path does not exist, normalize only the
   nearest existing parent inside the repo. If no contained parent exists, emit
   `acl_workability/unwritable_runtime_path` when contained workspace
   normalization may repair it, otherwise
   `operator_required/operator_clearance_required`.
8. `AclNormalizationResult.approved` is true only when every writable target is
   agent-writable or safely creatable after normalization. Failures route to
   deterministic workspace repair when contained; external or uncontained
   failures use the canonical `quiesce` route through the failure router.

Writeability proof is target-specific. For each writable `PathTarget`, the
authority records the runtime identity class being checked, nearest existing
parent, permission bits before and after normalization, shared group decision,
and whether the target is create-safe, modify-safe, delete-safe, or stage-safe.
The proof is valid only for the canonical path and action named in the
`AclTarget`; callers may not reuse a parent-directory proof for a different repo
or action without another preflight.

## Workspace Snapshot Fields

Each snapshot is an immutable view of one canonical repo at one control-plane
stage. Capture snapshots before normal dispatch, after deterministic workspace
repair, before verifier/repair prompts, before merge queue claim, and after
successful commit/checkpoint. Recompute after ACL normalization because chmod,
chgrp, or atomic replacement may alter git metadata or filesystem state.

Required fields:

- Identity: `feature_id`, `dag_sha256`, `group_idx`, `attempt_id`, `stage`,
  `repo_id`, `role`, `workspace_relative_path`, `canonical_path`,
  `source_path`, `alias_paths`.
- Git provenance: `remote_url`, `remote_fingerprint`, `branch`, `head_sha`,
  `git_common_dir`, `source_git_common_dir`, `case_sensitivity`.
- State digests: `index_digest` from staged entries plus index metadata,
  `worktree_status_digest` from porcelain-v2 status, and `idempotency_key` from
  feature/group/stage/repo/head/index/status.
- Path state: `dirty_paths`, `staged_paths`, `untracked_paths`,
  `forbidden_paths`, `denied_paths`, `agent_writable_paths`, `symlink_paths`,
  `outside_root_targets`.
- Evidence links: typed attempt id, registry artifact id, ACL artifact id, and
  compatibility projection artifact ids when present.
- Timing and diagnostics: `validated_at`, `captured_at`, warning list, safety
  status, and bounded sample counts for lists that are truncated in dashboard
  summaries.

Snapshot freshness is checked by `(repo_id, stage, head_sha, index_digest,
worktree_status_digest, registry_digest)`. A snapshot becomes stale when the
registry digest changes, when a route targets a path absent from its linked
`workspace_preflight` evidence, when git status cannot be reproduced, or when a
merge queue lease observes a different head without queue evidence. Stale
snapshots route as `stale_projection/workspace_snapshot_stale` and must be
rebuilt by authority before verifier, repair, merge, or checkpoint work
continues.

## Safety Checks

- Normal implementer dispatch requires zero unresolved alias resolutions, zero
  denied ACL targets, no repo identity collisions, and a fresh pre-dispatch
  snapshot for every writable repo.
- Runtime adapters receive canonical repo identity metadata from this layer, but
  implementer/repair cwd is always the sandbox cwd from Slice 04. Alias paths may
  appear in prompt evidence only as blocked/repaired historical references.
- Workspace authority never follows symlink ancestors for write decisions. It
  can report the symlink path as evidence, but mutation stops before the symlink.
- Merge queue validation compares the pre-dispatch or repair base snapshot with
  the candidate merge snapshot. Head changes outside the queue lease, dirty paths
  outside contract allowlists, or newly discovered aliases block merge.
- Snapshot capture must be bounded. Store full path lists in typed rows or spill
  artifacts when needed, but supervisor/dashboard summaries read only bounded
  fields and evidence ids.

## Repair Routing

Deterministic workspace failures route before broad RCA or product repair:

| Class | Canonical failure class/type | Route | Notes |
| --- | --- | --- | --- |
| Alias metadata only, canonical exists | `stale_projection/verifier_context_stale` | `retry_verifier` | Rewrite bounded task/spec/projection evidence to canonical paths, record `dag-path-canonicalization:g{group}`, and rebuild context. |
| Alias-only file exists | `worktree_alias/alias_only_canonical_missing` | `run_canonicalization_repair` | Record alias evidence and enqueue sandboxed canonicalization repair; canonical product-content changes are applied only by merge queue. |
| Alias and canonical differ | `worktree_alias/alias_canonical_divergent` | `run_canonicalization_repair` | Focused sandboxed repair receives both paths, hashes, and contracts; no operator escalation and no direct workspace-authority copy. |
| Contained ACL denied | `acl_workability/unwritable_runtime_path` | `run_workspace_repair` | Apply ACL normalization, verify with agent writability, snapshot again. |
| Symlink or outside-root target | `operator_required/operator_clearance_required` | `operator_required` | Fail closed before model dispatch; not product repair. |
| Unknown repo or identity collision | `operator_required/operator_clearance_required` | `operator_required` | Requires planning/workspace correction before implementation. |
| Dirty path outside contract | `contract_violation/outside_allowed_paths` | `run_product_repair` | Repair moves work back into the fixed contract scope. |

Routing is derived only from typed preflight and snapshot evidence. The route
idempotency key is
`workspace-route:{feature_id}:{dag_sha256}:g{group_idx}:{stage}:{repo_id}:{failure_class}:{target_digest}`.
If multiple workspace failures exist, route in this order: safety containment
blockers, identity collisions, alias divergence, alias-only canonicalization,
ACL/writeability, stale projection, then contract-owned dirty state. This makes
the first retry deterministic and prevents verifier RCA from spending model work
on a workspace condition the workflow can resolve.

## Refactoring Steps

1. Add `workspace_authority.py` with the types and pure path/identity helpers
   above, plus fixture-friendly adapters around git and filesystem probes.
2. Move registry construction and repo identity logic out of implementation
   phase. Existing `_ensure_task_worktrees` delegates to the authority but keeps
   `WorkspaceManager` clone/scaffold behavior.
3. Replace `_worktree_alias_map_for_group`,
   `_worktree_alias_path_info`, and `_run_worktree_alias_pre_dispatch_guard`
   with `WorkspaceAuthority.preflight_targets`.
4. Replace bridge-user writeability probes and scattered ACL helpers with
   authority-owned agent-writability and ACL normalization.
5. Normalize ACLs within feature repo roots only, then immediately snapshot the
   repo state that was normalized.
6. Route deterministic alias/ACL/snapshot-staleness failures through the typed
   failure router before normal implementer dispatch, verifier RCA, or product
   repair prompts.
7. Record `WorkspaceSnapshot` through the typed journal before dispatch, repair,
   merge queue claim, commit, and checkpoint stages.
8. Update Claude, Codex, and pool runtime bindings to receive sandbox cwd from
   Slice 04 plus canonical repo id, alias map, and denied path metadata from this
   layer. Refuse any implementer/repair binding that points cwd at a canonical
   repo.

## Persistence And Artifact Compatibility

- Keep emitting existing worktree registry artifacts and add authority-computed
  fields without removing legacy keys.
- Keep `dag-worktree-alias-preflight:g{group}:initial-dispatch`, retry-time
  alias artifacts, and `dag-path-canonicalization:g{group}` as compatibility
  evidence.
- Keep ACL normalization artifacts such as
  `dag-workspace-acl-normalization:g{group}:...` and link their artifact ids
  from snapshots.
- Store typed snapshots in `workspace_snapshots` and project summary artifacts
  for current supervisor/dashboard consumers.
- Compatibility artifacts are written synchronously with typed records. A
  snapshot or repair decision must not be authoritative if its legacy evidence
  projection failed.
- Legacy artifacts are read only to seed the initial authority registry. After
  the first successful authority registry for a group, legacy alias maps, ACL
  reports, and generated snapshots are compatibility output and cannot override
  typed registry, preflight, or snapshot decisions.

## Edge Cases And Failure Handling

- Legitimate repo name ending in `-wt`: do not rewrite unless registry/source
  evidence maps it to another canonical repo.
- Two candidate roots share a remote but have different source/git-common-dir
  evidence: emit `worktree_alias/alias_canonical_divergent` and block instead
  of guessing.
- Registry `repo_id` collision across different canonical paths: accept only if
  source evidence proves identity; otherwise block before dispatch.
- Alias-only file exists: record `worktree_alias` evidence and dispatch a
  sandboxed canonicalization repair whose product-content changes enter
  canonical repos only through the merge queue.
- Alias and canonical files both exist but differ: dispatch focused sandboxed
  adjudication repair; no operator escalation and no direct workspace-authority
  product-content copy.
- Missing parent path: normalize nearest existing parent inside feature repo root.
- Symlink or outside-root target: fail closed as workflow-blocked, not product repair.
- ACL normalization fails but path is already agent-writable: record nonfatal warning.
- ACL normalization fails and path is not agent-writable: quiesce or route
  deterministic workflow repair, not broad RCA.
- Dirty generated output appears in a snapshot without a matching contract or
  generated-output owner: fail closed as `contract_compile/contract_scope_conflict`
  or `regroup_invalid/regroup_write_conflict` based on the owning scheduler
  evidence; do not route product repair until an owner is known.
- Head SHA changes between pre-dispatch and merge queue claim without queue
  evidence: reject the queue claim and rebuild snapshots.

## Tests

- Canonical identity unit tests cover registry `repo_id`, source git common dir,
  source path, remote fingerprint, and scaffolded new repo identity precedence.
- Registry digest tests assert stable ordering independent of task order,
  filesystem iteration order, and legacy artifact key order.
- Registry maps `iriai-studio-backend-wt` to canonical
  `iriai-studio-backend` when source evidence proves identity.
- Unrelated canonical repo named with `-wt` is not rewritten.
- Basename-only alias candidates with no registry/source evidence are rejected
  even when canonical files happen to exist.
- Remote URL normalization treats HTTPS and SSH forms of the same repo as the
  same fingerprint, but does not merge different owner/repo pairs.
- Repo identity collision blocks dispatch and records both roots and evidence.
- Alias resolution uses longest prefix and preserves suffixes for nested paths.
- Alias resolution rejects absolute paths outside the feature root before
  checking disk state.
- Alias-only and divergent alias/canonical cases route deterministic
  canonicalization/adjudication, not operator-required.
- Bridge user can write but agent group cannot: preflight fails before
  normalization and passes after normalization.
- Chmod failure on an already agent-writable path records a warning and does not
  block dispatch.
- Regular non-git file owned by an agent user can be atomically replaced with
  identical contents when parent permissions allow it.
- Missing create-parent path is normalized through nearest existing feature-root parent.
- Symlink and outside-root targets are rejected before mutation.
- Snapshot captures branch, head, index digest, staged paths, dirty paths,
  untracked paths, denied paths, aliases, and evidence links.
- Snapshot idempotency key is stable for identical repo state and changes when
  head, index, status, or stage changes.
- Snapshot freshness tests cover registry digest changes, target-preflight
  mismatch, and merge queue head changes without lease evidence.
- Dirty generated outputs are captured in snapshot and block merge if not owned by contract.
- Runtime binding tests assert Claude/Codex receive sandbox cwd, canonical repo
  identity metadata, and never canonical or alias repo cwd for implementer/repair
  roles.
- Compatibility artifact tests assert alias, ACL, and snapshot projections are
  written with typed rows in the same control-plane transaction.
- Router tests assert alias, ACL, snapshot-stale, identity-collision, and dirty
  contract cases produce the same failure class, route, and idempotency key on
  repeated runs.

## Acceptance Criteria

- No normal implementer dispatch starts while unresolved alias or ACL evidence remains.
- No operator action is required for resolvable canonical path or ACL classes.
- All runtime adapters receive canonical workspace identity metadata from this
  layer; writable cwd still comes from the sandbox runner.
- Supervisor sees deterministic unblock evidence rather than stale restart guidance.
- Repo identity is stable across retries for unchanged source evidence and
  blocks on collisions instead of silently picking a basename.
- Merge queue cannot claim or checkpoint a repo without a fresh canonical
  workspace snapshot and no unowned dirty paths.
- Alias and ACL repair routes are deterministic workflow routes, not broad model
  RCA prompts.
- Tests prove the registry, alias preflight, ACL normalization, snapshots,
  runtime metadata binding, compatibility projections, and router idempotency as
  one blocking slice.

## Rollout/Rollback Notes

Land as one atomic feature branch: module extraction, dispatch integration,
runtime metadata binding, typed snapshot writes, compatibility artifact writes,
repair routing, and tests merge together. There is no production shadow,
preflight-only, or advisory phase for this slice. Once enabled for a new feature,
workspace authority is blocking before the first normal implementer dispatch.

Rollback is a single control-plane revert/kill switch that returns new feature
starts to the legacy workspace path before dispatch begins. Already-written
typed snapshots and compatibility artifacts remain read-only evidence for
diagnosis; rollback does not delete snapshots, registry artifacts, repair
artifacts, commits, or checkpoints.

## Cross-Slice Dependencies

- Slice 1 stores snapshots and compatibility artifacts.
- Slice 3 uses canonical paths in task contracts.
- Slice 4 allocates sandboxes from workspace authority.
- Slice 8 validates canonical merge targets from snapshots.
- Slice 10 reads workspace failure state for supervisor and dashboard.
