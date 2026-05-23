"""Durable merge queue worker orchestration (Slice 08d).

``MergeQueue`` wraps ``MergeQueueStore`` (typed queue persistence) and
``git_service`` (the git layer) into the worker apply/commit flow. Canonical
repos are mutated ONLY here — implementer and repair agents produce immutable
sandbox patch evidence; the queue applies it.

08d-2 implemented ``apply_candidate`` (the doc-08 patch apply/rebase algorithm
with merge proof); 08d-3 added ``run_required_gates`` (post-apply gates).
``commit_and_prove_clean`` / ``mark_integrated`` and the
``GroupMergeCoordinator`` land in later 08d iterations.
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, Field

from . import git_service
from .journal import (
    LeaseFencedError,
    MergeProof,
    MergeQueueError,
    MergeQueueItem,
    MergeQueueStore,
    RepoCommitProof,
)


class LeaseToken(BaseModel):
    """A worker's fencing token for a claimed queue item."""

    item_id: int
    lease_owner: str
    lease_version: int

    @classmethod
    def for_item(cls, item: MergeQueueItem) -> "LeaseToken":
        return cls(
            item_id=item.id,
            lease_owner=item.lease_owner or "",
            lease_version=item.lease_version,
        )


class RepoApplyInput(BaseModel):
    """Patch + contract path scope for one repo of an integration lane.

    Assembled by the caller from immutable sandbox patch evidence and the lane
    contracts, so ``apply_candidate`` stays decoupled from sandbox internals.
    """

    repo_id: str
    patch_text: str
    allowed_paths: list[str] = Field(default_factory=list)


class RepoApplyOutcome(BaseModel):
    repo_id: str
    pre_apply_head: str = ""
    applied_head: str = ""
    applied: bool = False


class MergeApplyResult(BaseModel):
    item_id: int
    applied: bool
    status: str
    failure_class: str = ""
    detail: str = ""
    merge_proof_evidence_id: int | None = None
    repo_outcomes: list[RepoApplyOutcome] = Field(default_factory=list)
    # Slice 11j P3-6 fold-in: the structured list of paths that escaped the
    # lane contracts when ``failure_class == "contract_violation"``. ADDITIVE
    # to the Slice-08 contract -- defaults to an empty list so every existing
    # construction (a successful apply, a non-contract-violation failure)
    # remains byte-for-byte unchanged. The durable-merge-queue drain reads
    # this field and surfaces the paths as ``target_paths`` on the typed-
    # failure-router observation payload so
    # :meth:`FailureRouter._allows_product_repair` no longer downgrades the
    # ``contract_violation`` route to ``quiesce`` when the escapes are
    # classifiable as product-repair targets. An empty list preserves the
    # pre-11j fail-closed-to-``quiesce`` safety net for un-upgraded paths.
    escaped_paths: list[str] = Field(default_factory=list)


class GateOutcome(BaseModel):
    """The post-apply gate runner's verdict for a queue item.

    The runner (Slice 06 gate machinery, injected) runs the deterministic gates
    against the applied canonical state and records the aggregate gate evidence.
    """

    approved: bool
    aggregate_evidence_id: int | None = None
    failure_class: str = ""
    detail: str = ""


class MergeGateResult(BaseModel):
    item_id: int
    approved: bool
    status: str
    post_apply_gate_evidence_id: int | None = None
    failure_class: str = ""
    detail: str = ""


class MergeCommitResult(BaseModel):
    item_id: int
    committed: bool
    status: str
    result_commit: str = ""
    commit_proof_evidence_id: int | None = None
    failure_class: str = ""
    detail: str = ""
    repo_proofs: list[RepoCommitProof] = Field(default_factory=list)


ApplyInputProvider = Callable[[MergeQueueItem], Awaitable[list[RepoApplyInput]]]
GateRunner = Callable[[MergeQueueItem], Awaitable[GateOutcome]]
# (item, repo_id) -> a workspace_snapshots id proving the repo is clean.
NoDirtyRecorder = Callable[[MergeQueueItem, str], Awaitable[int]]


def _path_allowed(path: str, allowed: list[str]) -> bool:
    """A repo-relative path is allowed if it matches a contract path entry.

    An entry matches by exact path, directory prefix (``dir/``), or glob.
    """

    for pattern in allowed:
        if path == pattern:
            return True
        if pattern.endswith("/") and path.startswith(pattern):
            return True
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


class _ApplyFailure(Exception):
    """Internal signal: an apply step failed; roll back all repos then fail.

    Slice 11j P3-6 fold-in: an optional ``escaped_paths`` carries the structured
    list of contract-escaping paths for the two ``contract_violation`` raise
    sites in :meth:`MergeQueue.apply_candidate` (the patch-path-outside-contract
    pre-apply rejection and the applied-path-set escape post-apply check). The
    field is propagated through :meth:`MergeQueue._fail` onto
    :attr:`MergeApplyResult.escaped_paths` so the durable-merge-queue drain can
    surface them as ``target_paths`` on the typed-failure-router observation
    payload (which :meth:`FailureRouter._repair_scope` reads). The other
    ``_ApplyFailure`` raise sites (``merge_conflict`` / ``stale_projection`` /
    ``checkpoint_contradiction``) leave ``escaped_paths`` ``None`` -- the field
    is only meaningful for the contract-violation paths, and a ``None`` value
    on the producer side yields an empty list on the consumer side via the
    default-factory contract on :class:`MergeApplyResult`.
    """

    def __init__(
        self,
        failure_class: str,
        detail: str,
        escaped_paths: list[str] | None = None,
    ) -> None:
        self.failure_class = failure_class
        self.detail = detail
        self.escaped_paths = escaped_paths
        super().__init__(detail)


class MergeQueue:
    """Worker-facing merge queue: claim a lane and apply its patch evidence."""

    def __init__(
        self,
        store: MergeQueueStore,
        apply_input_provider: ApplyInputProvider,
        gate_runner: GateRunner | None = None,
        no_dirty_recorder: NoDirtyRecorder | None = None,
    ) -> None:
        self._store = store
        self._provide_apply_inputs = apply_input_provider
        self._gate_runner = gate_runner
        self._record_no_dirty = no_dirty_recorder

    # ── delegating lease methods ────────────────────────────────────────────

    async def claim(
        self, feature_id: str, lease_owner: str
    ) -> MergeQueueItem | None:
        return await self._store.claim(feature_id, lease_owner)

    async def heartbeat(
        self, item_id: int, lease_owner: str, lease_version: int
    ) -> MergeQueueItem:
        return await self._store.heartbeat(item_id, lease_owner, lease_version)

    async def recover_expired(
        self, feature_id: str, lease_owner: str
    ) -> MergeQueueItem | None:
        return await self._store.recover_expired(feature_id, lease_owner)

    async def get(self, item_id: int) -> MergeQueueItem | None:
        return await self._store.get(item_id)

    # ── apply ───────────────────────────────────────────────────────────────

    async def apply_candidate(
        self, item: MergeQueueItem, token: LeaseToken
    ) -> MergeApplyResult:
        """Apply the lane's immutable patch evidence to its canonical repos.

        Runs the doc-08 apply/rebase algorithm under the feature advisory lock:
        baseline cleanliness, pre-apply head, deterministic rebase check,
        contract path validation, ``git apply``, applied-path validation, merge
        proof, and the ``leased -> applying -> verifying`` transitions. A
        conflict or contract escape resets the repo and fails the lane closed.
        """

        inputs = {
            inp.repo_id: inp
            for inp in await self._provide_apply_inputs(item)
        }
        targets = item.repo_targets
        if not targets:
            return await self._fail(
                item, token, "checkpoint_contradiction",
                "queue item has no repo targets to apply",
            )
        target_ids = {target.repo_id for target in targets}
        missing = target_ids - set(inputs)
        if missing:
            return await self._fail(
                item, token, "checkpoint_contradiction",
                f"no apply input for repo targets {sorted(missing)}",
            )
        extra = set(inputs) - target_ids
        if extra:
            return await self._fail(
                item, token, "checkpoint_contradiction",
                f"apply input supplied for unknown repos {sorted(extra)}",
            )

        all_patch_paths = sorted(
            {
                path
                for inp in inputs.values()
                for path in git_service.patch_path_set(inp.patch_text)
            }
        )
        await self._store.acquire_feature_lock(item.feature_id)
        # Every touched repo's pre-apply HEAD, so any failure rolls back ALL of
        # them — never just the repo that failed.
        pre_apply_heads: dict[str, str] = {}
        try:
            try:
                # Baseline cleanliness is a precondition of leased -> applying.
                for target in targets:
                    if not await git_service.working_tree_clean(
                        Path(target.repo_path)
                    ):
                        raise _ApplyFailure(
                            "checkpoint_contradiction",
                            f"repo {target.repo_id!r} is not clean at baseline",
                        )

                await self._store.transition(
                    item.id, token.lease_owner, token.lease_version, "applying"
                )

                outcomes: list[RepoApplyOutcome] = []
                applied_heads: dict[str, str] = {}
                rebased = False

                for target in targets:
                    inp = inputs[target.repo_id]
                    repo_path = Path(target.repo_path)
                    pre_apply = await git_service.head_commit(repo_path)
                    pre_apply_heads[target.repo_id] = pre_apply

                    if (
                        target.expected_head
                        and target.expected_head != pre_apply
                    ):
                        raise _ApplyFailure(
                            "stale_projection",
                            f"repo {target.repo_id!r} HEAD {pre_apply} != "
                            f"expected_head {target.expected_head}",
                        )

                    await self._store.advance_repo_target(
                        item.id, target.repo_id, token.lease_owner,
                        token.lease_version, "pre_apply_recorded",
                        pre_apply_head=pre_apply,
                    )

                    if pre_apply != target.base_commit:
                        if not await git_service.is_ancestor(
                            repo_path, target.base_commit, pre_apply
                        ):
                            raise _ApplyFailure(
                                "merge_conflict",
                                f"repo {target.repo_id!r} base "
                                f"{target.base_commit} is not an ancestor of "
                                f"HEAD",
                            )
                        rebased = True

                    patch_paths = git_service.patch_path_set(inp.patch_text)
                    outside = [
                        p for p in patch_paths
                        if not _path_allowed(p, inp.allowed_paths)
                    ]
                    if outside:
                        # Slice 11j P3-6 fold-in: pass the sorted escape list
                        # as structured ``escaped_paths`` so the drain can
                        # surface it on the router observation payload as
                        # ``target_paths`` (which :meth:`FailureRouter._
                        # repair_scope` reads).
                        raise _ApplyFailure(
                            "contract_violation",
                            f"repo {target.repo_id!r} patch touches paths "
                            f"outside the lane contracts: {sorted(outside)}",
                            escaped_paths=sorted(outside),
                        )

                    check = await git_service.apply_check(
                        repo_path, inp.patch_text
                    )
                    if not check.applied:
                        raise _ApplyFailure(
                            "merge_conflict",
                            f"repo {target.repo_id!r} patch does not apply",
                        )

                    applied = await git_service.apply_patch(
                        repo_path, inp.patch_text
                    )
                    if not applied.applied:
                        raise _ApplyFailure(
                            "merge_conflict",
                            f"repo {target.repo_id!r} 3-way apply produced "
                            f"conflicts",
                        )

                    applied_paths = await git_service.changed_path_set(
                        repo_path
                    )
                    escaped = [
                        p for p in applied_paths
                        if not _path_allowed(p, inp.allowed_paths)
                    ]
                    if escaped:
                        # Slice 11j P3-6 fold-in: pass the sorted escape list
                        # as structured ``escaped_paths`` so the drain can
                        # surface it on the router observation payload as
                        # ``target_paths`` (which :meth:`FailureRouter._
                        # repair_scope` reads).
                        raise _ApplyFailure(
                            "contract_violation",
                            f"repo {target.repo_id!r} applied path set escapes "
                            f"the lane contracts: {sorted(escaped)}",
                            escaped_paths=sorted(escaped),
                        )

                    applied_head = await git_service.head_commit(repo_path)
                    applied_heads[target.repo_id] = applied_head
                    await self._store.advance_repo_target(
                        item.id, target.repo_id, token.lease_owner,
                        token.lease_version, "applied",
                        applied_head=applied_head,
                    )
                    outcomes.append(
                        RepoApplyOutcome(
                            repo_id=target.repo_id,
                            pre_apply_head=pre_apply,
                            applied_head=applied_head,
                            applied=True,
                        )
                    )

                patch_digest = hashlib.sha256(
                    "\0".join(
                        sorted(inp.patch_text for inp in inputs.values())
                    ).encode()
                ).hexdigest()
                merge_proof_id = await self._store.record_merge_proof(
                    item.id,
                    feature_id=item.feature_id,
                    group_idx=item.group_idx,
                    proof=MergeProof(
                        base_commit=";".join(
                            sorted(t.base_commit for t in targets)
                        ),
                        pre_apply_heads=pre_apply_heads,
                        applied_heads=applied_heads,
                        patch_digest=patch_digest,
                        patch_path_set=all_patch_paths,
                        rebased=rebased,
                    ),
                )
                await self._store.transition(
                    item.id, token.lease_owner, token.lease_version,
                    "verifying", merge_proof_evidence_id=merge_proof_id,
                )
                return MergeApplyResult(
                    item_id=item.id,
                    applied=True,
                    status="verifying",
                    merge_proof_evidence_id=merge_proof_id,
                    repo_outcomes=outcomes,
                )
            except _ApplyFailure as failure:
                await self._rollback_all(
                    targets, pre_apply_heads, all_patch_paths
                )
                # Slice 11j P3-6 fold-in: forward the structured
                # ``escaped_paths`` from the contract-violation raise sites so
                # :meth:`_fail` populates the returned
                # :class:`MergeApplyResult.escaped_paths`. Non-contract-violation
                # raise sites leave the field ``None`` -- the default-factory
                # contract on the result emits an empty list.
                return await self._fail(
                    item, token, failure.failure_class, failure.detail,
                    escaped_paths=failure.escaped_paths,
                )
        finally:
            await self._store.release_feature_lock(item.feature_id)

    async def _rollback_all(
        self,
        targets: list,
        pre_apply_heads: dict[str, str],
        patch_paths: list[str],
    ) -> None:
        """Reset every touched canonical repo to its recorded pre-apply HEAD.

        Any apply failure — including one on a later repo of a multi-repo lane —
        must leave NO repo mutated. Best-effort: a reset that itself fails is
        left for crash recovery to reconcile.
        """

        for target in targets:
            pre = pre_apply_heads.get(target.repo_id)
            if pre is None:
                continue
            repo_path = Path(target.repo_path)
            try:
                await git_service.reset_hard(repo_path, pre)
                if patch_paths:
                    await git_service.clean_untracked(repo_path, patch_paths)
            except git_service.GitError:
                pass

    async def run_required_gates(
        self, item: MergeQueueItem, token: LeaseToken
    ) -> MergeGateResult:
        """Run the post-apply deterministic gates against the applied state.

        The injected ``gate_runner`` runs the Slice 06 gates and records the
        aggregate gate evidence. On approval the lane advances
        ``verifying -> committing`` with the real aggregate evidence id. On
        rejection (or an approval without an evidence id) the canonical repos
        are reset to their pre-apply HEAD and the lane fails closed.
        """

        if self._gate_runner is None:
            raise MergeQueueError("run_required_gates requires a gate_runner")

        outcome = await self._gate_runner(item)

        if not outcome.approved:
            await self._reset_to_pre_apply(item)
            failure_class = outcome.failure_class or "verifier_provider"
            detail = outcome.detail or "post-apply gates rejected the candidate"
            await self._fail(item, token, failure_class, detail)
            return MergeGateResult(
                item_id=item.id, approved=False, status="failed",
                failure_class=failure_class, detail=detail,
            )

        if outcome.aggregate_evidence_id is None:
            await self._reset_to_pre_apply(item)
            detail = "post-apply gates approved without an aggregate evidence id"
            await self._fail(item, token, "checkpoint_contradiction", detail)
            return MergeGateResult(
                item_id=item.id, approved=False, status="failed",
                failure_class="checkpoint_contradiction", detail=detail,
            )

        await self._store.transition(
            item.id, token.lease_owner, token.lease_version, "committing",
            post_apply_gate_evidence_id=outcome.aggregate_evidence_id,
        )
        return MergeGateResult(
            item_id=item.id,
            approved=True,
            status="committing",
            post_apply_gate_evidence_id=outcome.aggregate_evidence_id,
        )

    async def _reset_to_pre_apply(
        self,
        item: MergeQueueItem,
        *,
        only_repo_ids: set[str] | None = None,
    ) -> None:
        """Reset applied-but-uncommitted repos to their recorded pre-apply HEAD.

        Used when post-apply gates reject the candidate, or when a commit hook
        rejects it. ``only_repo_ids`` limits the reset to a subset — a commit
        hook failure on a later repo of a multi-repo lane must NOT discard the
        real commits earlier repos already produced (doc 08 commit step 9).
        Untracked files added by the patch are cleaned using the merge proof's
        ``patch_path_set``.
        """

        targets = [
            target
            for target in item.repo_targets
            if only_repo_ids is None or target.repo_id in only_repo_ids
        ]
        pre_apply_heads = {
            target.repo_id: target.pre_apply_head
            for target in targets
            if target.pre_apply_head
        }
        patch_paths: list[str] = []
        if item.merge_proof_evidence_id is not None:
            proof = await self._store.load_proof(item.merge_proof_evidence_id)
            if proof:
                patch_paths = list(proof.get("patch_path_set") or [])
        await self._rollback_all(targets, pre_apply_heads, patch_paths)

    async def commit_and_prove_clean(
        self, item: MergeQueueItem, token: LeaseToken
    ) -> MergeCommitResult:
        """Commit a ``committing`` lane's applied changes and prove clean state.

        Per repo: stage the validated paths, commit via ``git_service.commit``
        (a hook rejection is a typed ``commit_hygiene`` failure, never raised),
        advance the repo target applied -> committed, prove no-dirty + record a
        workspace no-dirty snapshot, advance committed -> clean. Finally record
        the commit proof. Git commit and DB commit cannot be one physical
        transaction; once a ``result_commit`` exists recovery reconciles from
        the typed rows.
        """

        if self._record_no_dirty is None:
            raise MergeQueueError(
                "commit_and_prove_clean requires a no_dirty_recorder"
            )

        task_names = [coverage.task_id for coverage in item.task_coverage]

        def _ids(values: list[int]) -> str:
            return ",".join(str(value) for value in values)

        message = git_service.build_commit_message(
            item.group_idx,
            task_names,
            {
                "Feature-ID": item.feature_id,
                "DAG-SHA256": item.dag_sha256,
                "Group-Index": str(item.group_idx),
                "Merge-Queue-Item": str(item.id),
                "Patch-Evidence": _ids(item.patch_evidence_ids),
                "Gate-Evidence": _ids(item.gate_evidence_ids),
                "Contracts": _ids(item.contract_ids),
            },
        )

        await self._store.acquire_feature_lock(item.feature_id)
        try:
            repo_proofs: list[RepoCommitProof] = []
            result_commits: list[str] = []
            # Repos with a real commit already produced in THIS call — they are
            # never reset, even if a later repo's commit fails.
            committed_repo_ids: set[str] = set()
            for target in item.repo_targets:
                repo_path = Path(target.repo_path)
                changed = sorted(await git_service.changed_path_set(repo_path))
                await git_service.stage_paths(repo_path, changed)

                commit_result = await git_service.commit(repo_path, message)
                if not commit_result.committed:
                    # Hook rejection — no commit on THIS repo. Reset only repos
                    # that have not committed; earlier repos of a multi-repo
                    # lane keep their real commits (doc 08 commit step 9).
                    uncommitted = {
                        t.repo_id for t in item.repo_targets
                    } - committed_repo_ids
                    await self._reset_to_pre_apply(
                        item, only_repo_ids=uncommitted
                    )
                    detail = "commit hook rejected the candidate"
                    if commit_result.hook_failure is not None:
                        detail = (
                            f"commit hook failed (exit "
                            f"{commit_result.hook_failure.returncode})"
                        )
                    await self._fail(item, token, "commit_hygiene", detail)
                    return MergeCommitResult(
                        item_id=item.id, committed=False, status="failed",
                        failure_class="commit_hygiene", detail=detail,
                    )

                # A real commit now exists for this repo — it must never be
                # reset by a later repo's failure in this lane.
                committed_repo_ids.add(target.repo_id)
                await self._store.advance_repo_target(
                    item.id, target.repo_id, token.lease_owner,
                    token.lease_version, "committed",
                    result_commit=commit_result.commit,
                    tree_sha=commit_result.tree,
                )

                if not await git_service.working_tree_clean(repo_path):
                    # A commit happened but the repo is dirty — keep the result
                    # commit and block the lane (doc 08 commit step 9).
                    detail = (
                        f"repo {target.repo_id!r} is dirty after commit "
                        f"{commit_result.commit}"
                    )
                    await self._fail(
                        item, token, "checkpoint_contradiction", detail
                    )
                    return MergeCommitResult(
                        item_id=item.id, committed=False, status="failed",
                        failure_class="checkpoint_contradiction", detail=detail,
                    )

                snapshot_id = await self._record_no_dirty(item, target.repo_id)
                await self._store.advance_repo_target(
                    item.id, target.repo_id, token.lease_owner,
                    token.lease_version, "clean",
                    no_dirty_snapshot_id=snapshot_id,
                )
                repo_proofs.append(
                    RepoCommitProof(
                        repo_id=target.repo_id,
                        repo_path=target.repo_path,
                        pre_apply_head=target.pre_apply_head,
                        applied_head=target.applied_head,
                        result_commit=commit_result.commit,
                        tree_sha=commit_result.tree,
                        changed_paths=changed,
                        no_dirty_snapshot_id=snapshot_id,
                    )
                )
                result_commits.append(commit_result.commit)

            commit_proof_id = await self._store.record_commit_proof(
                item.id,
                feature_id=item.feature_id,
                group_idx=item.group_idx,
                repo_proofs=repo_proofs,
            )
            return MergeCommitResult(
                item_id=item.id,
                committed=True,
                status="committing",
                result_commit=",".join(result_commits),
                commit_proof_evidence_id=commit_proof_id,
                repo_proofs=repo_proofs,
            )
        finally:
            await self._store.release_feature_lock(item.feature_id)

    async def mark_integrated(
        self,
        item: MergeQueueItem,
        token: LeaseToken,
        commit_result: MergeCommitResult,
    ) -> MergeQueueItem:
        """Transition a committed lane ``committing -> integrated``.

        ``integrated`` means the lane is committed and clean but the group
        checkpoint is not yet projected (the GroupMergeCoordinator owns that).
        Requires the commit proof + result commit from
        :meth:`commit_and_prove_clean`.
        """

        if not commit_result.committed:
            raise MergeQueueError(
                "mark_integrated requires a successful commit result"
            )
        return await self._store.transition(
            item.id, token.lease_owner, token.lease_version, "integrated",
            commit_proof_evidence_id=commit_result.commit_proof_evidence_id,
            result_commit=commit_result.result_commit,
        )

    async def _fail(
        self,
        item: MergeQueueItem,
        token: LeaseToken,
        failure_class: str,
        detail: str,
        *,
        escaped_paths: list[str] | None = None,
    ) -> MergeApplyResult:
        """Fail the lane closed with typed failure context.

        Slice 7 failure routing is wired in 08e; here the lane is recorded
        ``failed`` with the typed class so the caller can route it.

        Slice 11j P3-6 fold-in: ``escaped_paths`` carries the structured list
        of contract-escaping paths for the two ``contract_violation`` raise
        sites in :meth:`apply_candidate`. ADDITIVE -- defaults to ``None``;
        when populated the resulting :class:`MergeApplyResult.escaped_paths`
        is the sorted escape list, otherwise it is the empty list (via the
        default-factory on the model). The drain surfaces the field as
        ``target_paths`` on the typed-failure-router observation payload so
        :meth:`FailureRouter._allows_product_repair` no longer downgrades the
        ``contract_violation`` route to ``quiesce``.
        """

        try:
            # Mark every non-terminal repo target failed so the parent item and
            # the repo recovery ledger agree (doc 08 step 11) — checkpoint
            # coverage rejects a lane whose parent/child statuses disagree.
            current = await self._store.get(item.id)
            if current is not None:
                for target in current.repo_targets:
                    if target.status not in ("failed", "poisoned", "clean"):
                        await self._store.advance_repo_target(
                            item.id, target.repo_id, token.lease_owner,
                            token.lease_version, "failed",
                        )
            await self._store.transition(
                item.id, token.lease_owner, token.lease_version, "failed",
                last_error=f"{failure_class}: {detail}",
            )
        except (LeaseFencedError, MergeQueueError):
            # Best effort: if the worker was fenced, a recovery worker now owns
            # the lane and will record its terminal state.
            pass
        return MergeApplyResult(
            item_id=item.id,
            applied=False,
            status="failed",
            failure_class=failure_class,
            detail=detail,
            escaped_paths=list(escaped_paths) if escaped_paths else [],
        )


class GroupMergeCoverage(BaseModel):
    """Whether a DAG group's expected tasks are covered for checkpoint."""

    feature_id: str
    dag_sha256: str
    group_idx: int
    expected_task_ids: list[str] = Field(default_factory=list)
    integrated_queue_item_ids: list[int] = Field(default_factory=list)
    # 08g P2-A: lanes already in `checkpointing` (a crash entered `checkpointing`
    # then died before `complete_checkpoint`). A candidate status alongside
    # `integrated`/`done`; `checkpoint_group` MUST include these in the
    # `complete_checkpoint` set so doc 08 § Tests' "Repeating checkpoint from
    # `checkpointing` rows succeeds" holds. Additive field, default empty.
    checkpointing_queue_item_ids: list[int] = Field(default_factory=list)
    done_queue_item_ids: list[int] = Field(default_factory=list)
    missing_task_ids: list[str] = Field(default_factory=list)
    duplicate_task_ids: list[str] = Field(default_factory=list)
    failed_queue_item_ids: list[int] = Field(default_factory=list)
    result_commits: list[str] = Field(default_factory=list)
    approved: bool = False


ExpectedTaskIdsProvider = Callable[[str, str, int], Awaitable[list[str]]]

# Slice 08e-3b: an optional provider of the per-task ``ImplementationResult``
# model-dump dicts for one ``(feature_id, dag_sha256, group_idx)`` group. When
# wired, ``_reconstruct_checkpoint_body`` populates the legacy ``dag-group:*``
# body's ``results`` list with them — the legacy resume freshness gate
# (``_checkpoint_results_match_tasks``) hard-requires one validatable
# ``ImplementationResult`` dump per expected task id. When ``None`` (e.g. the
# 08d coordinator unit tests) the body carries ``results: []`` as before.
TaskResultsProvider = Callable[[str, str, int], Awaitable[list[dict]]]

# Slice 08e-3b REMEDIATION 2: an optional provider of the DAG/wave-ordered
# group task id list for one ``(feature_id, dag_sha256, group_idx)`` group.
# When wired, ``_reconstruct_checkpoint_body`` uses it for the legacy
# ``dag-group:*`` body's ``task_ids`` instead of the lexically-``sorted``
# ``coverage.expected_task_ids`` — the legacy resume freshness gate
# (``_dag_group_checkpoint_is_fresh``) compares ``task_ids`` ORDER-SENSITIVELY
# against ``dag.execution_order[g]`` (wave order). When ``None`` (e.g. the 08d
# coordinator unit tests) the body's ``task_ids`` falls back to
# ``coverage.expected_task_ids`` as before.
BodyTaskIdsProvider = Callable[[str, str, int], Awaitable[list[str]]]

# Lane statuses that count as covering a task for a group checkpoint.
_CANDIDATE_STATUSES = frozenset({"integrated", "checkpointing", "done"})


class CheckpointProjection(BaseModel):
    """The checkpoint projector's output.

    The projector writes the ``dag-group:*`` compatibility projection plus the
    Slice 06 ``checkpoint_gate`` + checkpoint-body evidence (08e wires it to
    ``ExecutionControlStore.project_group_checkpoint``) and returns the ids the
    coordinator stamps onto every covered lane. The projector MUST be idempotent
    on the doc-08 step-3 checkpoint key so checkpoint recovery never
    double-projects.
    """

    checkpoint_projection_id: int
    checkpoint_gate_evidence_id: int
    checkpoint_evidence_id: int
    body_sha256: str


class MergeResult(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    checkpointed: bool
    approved: bool
    done_queue_item_ids: list[int] = Field(default_factory=list)
    checkpoint_projection_id: int | None = None
    result_commit: str = ""
    detail: str = ""


# (coverage, reconstructed legacy body) -> the projection + gate evidence ids.
CheckpointProjector = Callable[
    ["GroupMergeCoverage", dict], Awaitable[CheckpointProjection]
]


class GroupMergeCoordinator:
    """Computes group checkpoint coverage and runs the checkpoint transaction.

    08d-5a implemented ``expected_task_ids`` and ``coverage``; 08d-5c added the
    idempotent ``checkpoint_group`` transaction.
    """

    def __init__(
        self,
        store: MergeQueueStore,
        expected_task_ids_provider: ExpectedTaskIdsProvider,
        checkpoint_projector: CheckpointProjector | None = None,
        task_results_provider: TaskResultsProvider | None = None,
        body_task_ids_provider: BodyTaskIdsProvider | None = None,
    ) -> None:
        self._store = store
        self._expected_provider = expected_task_ids_provider
        self._checkpoint_projector = checkpoint_projector
        # Slice 08e-3b: optional — supplies the per-task ``ImplementationResult``
        # dumps for the reconstructed legacy ``dag-group:*`` body. Additive: the
        # 08d ``checkpoint_group`` transaction and signature are unchanged.
        self._task_results_provider = task_results_provider
        # Slice 08e-3b REMEDIATION 2: optional — supplies the DAG/wave-ordered
        # group task id list for the reconstructed legacy ``dag-group:*`` body's
        # ``task_ids``. Without it the body falls back to the lexically-sorted
        # ``coverage.expected_task_ids``, which the legacy resume freshness gate
        # rejects ORDER-SENSITIVELY for a non-lexically-sorted group. Additive.
        self._body_task_ids_provider = body_task_ids_provider

    async def expected_task_ids(
        self, feature_id: str, dag_sha256: str, group_idx: int
    ) -> list[str]:
        return list(
            await self._expected_provider(feature_id, dag_sha256, group_idx)
        )

    async def coverage(
        self, feature_id: str, dag_sha256: str, group_idx: int
    ) -> GroupMergeCoverage:
        """Compute whether every expected task is covered exactly once.

        A task is covered by a candidate lane (``integrated`` / ``checkpointing``
        / ``done``). A terminal ``failed`` lane blocks the group unless exactly
        one ``retry_of``-linked replacement candidate lane covers the same task
        set. A ``poisoned`` lane always blocks. ``approved`` is true only when
        coverage is complete with no missing/duplicate/unexpected/poisoned/
        unsuperseded failure.

        ``approved`` with a non-empty ``done_queue_item_ids`` and no
        ``integrated_queue_item_ids`` means the group is already checkpointed —
        the idempotent-success case ``checkpoint_group`` (08d-5b) detects.
        """

        expected = set(
            await self.expected_task_ids(feature_id, dag_sha256, group_idx)
        )
        items = await self._store.list_group_items(
            feature_id, dag_sha256, group_idx
        )

        # The partial unique index uniq_merge_queue_retry_source_active
        # guarantees at most one non-cancelled replacement per failed source.
        replacements: dict[int, MergeQueueItem] = {}
        for item in items:
            if (
                item.retry_of_queue_item_id is not None
                and item.status != "cancelled"
            ):
                replacements[item.retry_of_queue_item_id] = item

        def _superseded(failed_item: MergeQueueItem) -> bool:
            replacement = replacements.get(failed_item.id)
            if (
                replacement is None
                or replacement.status not in _CANDIDATE_STATUSES
            ):
                return False
            return {c.task_id for c in replacement.task_coverage} == {
                c.task_id for c in failed_item.task_coverage
            }

        candidate_by_task: dict[str, list[int]] = defaultdict(list)
        for item in items:
            if item.status not in _CANDIDATE_STATUSES:
                continue
            for coverage in item.task_coverage:
                candidate_by_task[coverage.task_id].append(item.id)

        missing = sorted(
            task for task in expected if not candidate_by_task.get(task)
        )
        duplicate = sorted(
            task
            for task in expected
            if len(candidate_by_task.get(task, [])) > 1
        )
        # A candidate lane covering a task outside the effective-DAG expected
        # set is a coverage/DAG-drift integrity violation — block the group.
        unexpected = [task for task in candidate_by_task if task not in expected]
        poisoned = [it for it in items if it.status == "poisoned"]
        blocking_failed = [
            it
            for it in items
            if it.status == "failed"
            and any(c.task_id in expected for c in it.task_coverage)
            and not _superseded(it)
        ]
        approved = (
            bool(expected)
            and not missing
            and not duplicate
            and not unexpected
            and not poisoned
            and not blocking_failed
        )
        return GroupMergeCoverage(
            feature_id=feature_id,
            dag_sha256=dag_sha256,
            group_idx=group_idx,
            expected_task_ids=sorted(expected),
            integrated_queue_item_ids=sorted(
                it.id for it in items if it.status == "integrated"
            ),
            checkpointing_queue_item_ids=sorted(
                it.id for it in items if it.status == "checkpointing"
            ),
            done_queue_item_ids=sorted(
                it.id for it in items if it.status == "done"
            ),
            missing_task_ids=missing,
            duplicate_task_ids=duplicate,
            failed_queue_item_ids=sorted(
                it.id for it in items if it.status == "failed"
            ),
            result_commits=sorted(
                {
                    it.result_commit
                    for it in items
                    if it.status in _CANDIDATE_STATUSES and it.result_commit
                }
            ),
            approved=approved,
        )

    async def checkpoint_group(
        self, coverage: GroupMergeCoverage, token: LeaseToken
    ) -> MergeResult:
        """Idempotently checkpoint a group whose lanes are all integrated.

        Under the feature advisory lock: coverage is recomputed (the passed-in
        ``coverage`` is advisory). If the group is already checkpointed (all
        candidate lanes ``done``) the existing result is returned. Otherwise the
        legacy ``dag-group:*`` body is reconstructed, the injected
        ``checkpoint_projector`` writes the projection + checkpoint-gate
        evidence, and every covered lane is advanced to ``done``.

        Re-running is a no-op success. A crash between the projector and
        ``complete_checkpoint`` re-invokes the projector on recovery, so the
        ``checkpoint_projector`` MUST be idempotent on the doc-08 step-3
        checkpoint key — the 08e wiring to
        ``ExecutionControlStore.project_group_checkpoint`` enforces this via the
        evidence idempotency keys.
        """

        if self._checkpoint_projector is None:
            raise MergeQueueError(
                "checkpoint_group requires a checkpoint_projector"
            )
        feature_id = coverage.feature_id
        dag_sha256 = coverage.dag_sha256
        group_idx = coverage.group_idx

        await self._store.acquire_feature_lock(feature_id)
        try:
            # The passed-in coverage is advisory — recompute under the lock.
            current = await self.coverage(feature_id, dag_sha256, group_idx)

            # Already checkpointed: all candidate lanes are done. 08g P2-A: a
            # `checkpointing` lane (a crash mid-checkpoint) is still PENDING —
            # not done — so a group with any `checkpointing` lane must NOT take
            # the idempotent-already-done branch; it must re-run the checkpoint
            # transaction below to advance that lane.
            if (
                current.done_queue_item_ids
                and not current.integrated_queue_item_ids
                and not current.checkpointing_queue_item_ids
                and current.approved
            ):
                items = await self._store.list_group_items(
                    feature_id, dag_sha256, group_idx
                )
                done_set = set(current.done_queue_item_ids)
                stamps = {
                    (
                        it.checkpoint_projection_id,
                        it.checkpoint_coverage_digest,
                        it.checkpoint_body_sha256,
                    )
                    for it in items
                    if it.id in done_set
                }
                if len(stamps) > 1:
                    # Split-brain: covered lanes carry divergent checkpoint ids.
                    return MergeResult(
                        feature_id=feature_id,
                        dag_sha256=dag_sha256,
                        group_idx=group_idx,
                        checkpointed=False,
                        approved=True,
                        done_queue_item_ids=current.done_queue_item_ids,
                        detail=(
                            "checkpoint_contradiction: covered lanes carry "
                            "divergent checkpoint ids"
                        ),
                    )
                projection_id = next(iter(stamps))[0] if stamps else None
                return MergeResult(
                    feature_id=feature_id,
                    dag_sha256=dag_sha256,
                    group_idx=group_idx,
                    checkpointed=True,
                    approved=True,
                    done_queue_item_ids=current.done_queue_item_ids,
                    checkpoint_projection_id=projection_id,
                    result_commit=",".join(current.result_commits),
                    detail="group already checkpointed",
                )

            if not current.approved:
                return MergeResult(
                    feature_id=feature_id,
                    dag_sha256=dag_sha256,
                    group_idx=group_idx,
                    checkpointed=False,
                    approved=False,
                    detail="group coverage is not approved for checkpoint",
                )

            # Slice 08e-3b: reconstruct the legacy `dag-group:*` body from typed
            # rows. When a `task_results_provider` is wired, the body carries the
            # per-task `ImplementationResult` dumps the legacy resume freshness
            # gate (`_checkpoint_results_match_tasks`) hard-requires; absent one,
            # `results` stays `[]` (08d unit-test behavior, unchanged).
            task_results: list[dict] = []
            if self._task_results_provider is not None:
                task_results = list(
                    await self._task_results_provider(
                        feature_id, dag_sha256, group_idx
                    )
                )
            # Slice 08e-3b REMEDIATION 2: when a `body_task_ids_provider` is
            # wired, the body's `task_ids` carries the DAG/wave order the legacy
            # `_dag_group_checkpoint_is_fresh` gate compares ORDER-SENSITIVELY;
            # absent one it falls back to `coverage.expected_task_ids` (08d
            # unit-test behavior, unchanged).
            body_task_ids: list[str] | None = None
            if self._body_task_ids_provider is not None:
                body_task_ids = list(
                    await self._body_task_ids_provider(
                        feature_id, dag_sha256, group_idx
                    )
                )
            body = _reconstruct_checkpoint_body(
                current, results=task_results, task_ids=body_task_ids
            )
            projection = await self._checkpoint_projector(current, body)
            coverage_digest = _checkpoint_coverage_digest(current)
            # 08g P2-A: a lane already in `checkpointing` (a crash entered
            # `checkpointing` then died before `complete_checkpoint`) is a
            # candidate alongside `integrated`/`done` — it MUST be in the
            # `complete_checkpoint` set. `complete_checkpoint` advances
            # `status IN ('integrated','checkpointing') -> done` and rejects an
            # empty id list, so omitting `checkpointing` lanes made a re-driven
            # checkpoint of an all-`checkpointing` group raise (doc 08 § Tests:
            # "Repeating checkpoint from `checkpointing` rows succeeds").
            covered = sorted(
                set(current.integrated_queue_item_ids)
                | set(current.checkpointing_queue_item_ids)
                | set(current.done_queue_item_ids)
            )
            done = await self._store.complete_checkpoint(
                covered,
                checkpoint_gate_evidence_id=projection.checkpoint_gate_evidence_id,
                checkpoint_evidence_id=projection.checkpoint_evidence_id,
                checkpoint_projection_id=projection.checkpoint_projection_id,
                checkpoint_coverage_digest=coverage_digest,
                checkpoint_body_sha256=projection.body_sha256,
            )
            return MergeResult(
                feature_id=feature_id,
                dag_sha256=dag_sha256,
                group_idx=group_idx,
                checkpointed=True,
                approved=True,
                done_queue_item_ids=done or covered,
                checkpoint_projection_id=projection.checkpoint_projection_id,
                result_commit=",".join(current.result_commits),
            )
        finally:
            await self._store.release_feature_lock(feature_id)


class MergeQueueReadiness(BaseModel):
    """Result of the merge queue startup readiness check.

    Fails closed: the Slice 12 atomic-landing gate must refuse to enable the
    control plane when ``ready`` is false.
    """

    ready: bool
    missing: list[str] = Field(default_factory=list)


_REQUIRED_QUEUE_TABLES = (
    "merge_queue_items",
    "merge_queue_task_coverage",
    "merge_queue_repo_targets",
)
_REQUIRED_EVIDENCE_KINDS = ("merge_proof", "commit_proof", "checkpoint_gate")


def _assess_merge_queue_readiness(
    present_tables: set[str],
    evidence_kind_constraint: str | None,
    git_available: bool,
) -> MergeQueueReadiness:
    """Pure readiness verdict from the gathered facts (unit-testable)."""

    missing: list[str] = []
    for table in _REQUIRED_QUEUE_TABLES:
        if table not in present_tables:
            missing.append(f"table:{table}")
    for kind in _REQUIRED_EVIDENCE_KINDS:
        if (
            not evidence_kind_constraint
            or f"'{kind}'" not in evidence_kind_constraint
        ):
            missing.append(f"evidence_kind:{kind}")
    if not git_available:
        missing.append("git")
    return MergeQueueReadiness(ready=not missing, missing=sorted(missing))


async def _git_available() -> bool:
    try:
        result = await git_service.run_git(
            Path.cwd(), "--version", check=False
        )
        return result.ok
    except (OSError, git_service.GitError):  # pragma: no cover - env-specific
        return False


async def verify_merge_queue_ready(conn) -> MergeQueueReadiness:
    """Startup readiness check for the durable merge queue (doc 08 step 8).

    Verifies the queue schema (the three ``merge_queue_*`` tables and the
    ``merge_proof``/``commit_proof``/``checkpoint_gate`` evidence kinds) and
    that git is available. The Slice 12 landing gate calls this and fails
    closed if it is not ready.
    """

    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename LIKE 'merge_queue%'"
    )
    present = {row["tablename"] for row in rows}
    kind_constraint = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'evidence_nodes_kind_check'"
    )
    return _assess_merge_queue_readiness(
        present, kind_constraint, await _git_available()
    )


def _reconstruct_checkpoint_body(
    coverage: GroupMergeCoverage,
    *,
    results: list[dict] | None = None,
    task_ids: list[str] | None = None,
) -> dict:
    """Rebuild the legacy ``dag-group:*`` checkpoint body from typed rows.

    Reconstructed from the typed coverage, never from a latest-artifact scan.

    Slice 08e-3b: ``results`` carries the per-task ``ImplementationResult``
    model-dump dicts (supplied by the coordinator's ``task_results_provider``).
    The legacy ``dag-group:*`` resume freshness gate
    (``implementation._checkpoint_results_match_tasks``) hard-requires exactly
    one validatable ``ImplementationResult`` dump per expected task id, so a
    queue checkpoint that omitted ``results`` was rejected as stale on resume
    and by the post-test guard. When ``results`` is ``None`` (the 08d
    coordinator unit tests, which do not wire the provider) the body carries
    ``results: []`` exactly as before — no 08d behavior changes.

    Slice 08e-3b REMEDIATION 2: ``task_ids`` is the DAG/wave-ordered group task
    id list (supplied by the coordinator's ``body_task_ids_provider``). The
    legacy resume freshness gate ``_dag_group_checkpoint_is_fresh`` compares the
    body's ``task_ids`` ORDER-SENSITIVELY against ``dag.execution_order[g]``
    (wave order, NOT lexical), exactly as the legacy ``_verify_and_fix_group``
    body's ``[t.id for t in group_tasks]`` is in DAG order. ``coverage.
    expected_task_ids`` is deliberately ``sorted`` for the coordinator's
    set-based coverage logic, so a body built from it is lexically ordered and
    a non-lexically-sorted group's checkpoint was rejected as stale. When
    ``task_ids`` is wired the body carries the DAG order; when ``None`` (the
    08d coordinator unit tests) it falls back to ``coverage.expected_task_ids``
    exactly as before — no 08d behavior changes.
    """

    if task_ids is None:
        body_task_ids = list(coverage.expected_task_ids)
    else:
        body_task_ids = list(task_ids)
    return {
        "group_idx": coverage.group_idx,
        "task_ids": body_task_ids,
        "results": list(results or []),
        "verdict": "approved",
        "commit_hash": ",".join(coverage.result_commits),
    }


def _checkpoint_coverage_digest(coverage: GroupMergeCoverage) -> str:
    """Digest over the covered queue item ids, expected tasks, and result
    commits — the stable identity of one checkpoint coverage set.

    08g P2-A: the covered id set unions `integrated`, `checkpointing`, AND
    `done` lanes. A lane keeps its `id` as it advances `integrated ->
    checkpointing -> done`, so unioning all three candidate statuses makes the
    digest status-INVARIANT: a checkpoint re-driven after a crash (lanes now
    `checkpointing`) computes the same digest the first run did (lanes then
    `integrated`). Doc 08 § Tests requires a re-driven checkpoint's
    `checkpoint_coverage_digest` to match the original.
    """

    covered = sorted(
        set(coverage.integrated_queue_item_ids)
        | set(coverage.checkpointing_queue_item_ids)
        | set(coverage.done_queue_item_ids)
    )
    payload = "|".join(
        [
            coverage.feature_id,
            coverage.dag_sha256,
            str(coverage.group_idx),
            ",".join(str(i) for i in covered),
            ",".join(sorted(coverage.expected_task_ids)),
            ",".join(sorted(coverage.result_commits)),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "LeaseToken",
    "RepoApplyInput",
    "RepoApplyOutcome",
    "MergeApplyResult",
    "GateOutcome",
    "MergeGateResult",
    "MergeCommitResult",
    "GroupMergeCoverage",
    "CheckpointProjection",
    "MergeResult",
    "MergeQueueReadiness",
    "verify_merge_queue_ready",
    "ApplyInputProvider",
    "GateRunner",
    "NoDirtyRecorder",
    "ExpectedTaskIdsProvider",
    "CheckpointProjector",
    "MergeQueue",
    "GroupMergeCoordinator",
]
