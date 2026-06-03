"""Production wiring for the durable merge queue (Slice 08e-1).

``merge_queue.py`` (Slice 08d) is intentionally decoupled from its data
sources: ``MergeQueue`` / ``GroupMergeCoordinator`` take four injected
collaborators (``apply_input_provider``, ``gate_runner``, ``no_dirty_recorder``,
``checkpoint_projector``) as plain ``Callable``s. This module builds the real,
Postgres-backed implementations of those collaborators plus the production
startup readiness guard — without touching ``implementation.py`` (that splice
is 08e-2/08e-3).

Design notes (doc 08 § Refactoring Steps 8, § Persistence And Artifact
Compatibility):

* The readiness guard WRAPS the partial ``merge_queue.verify_merge_queue_ready``
  (queue schema + evidence kinds + git) and ADDS the doc-08-step-8 checks it
  lacks: journal projection ownership, sandbox patch capture, and the gate
  runner. It fails closed — any missing dependency yields ``ready=False``.
* Slice 06 ``gates.py`` ``GateRunner`` is in-memory only. The merge queue needs
  REAL ``aggregate_evidence_id`` / ``checkpoint_gate_evidence_id`` values that
  point at durable ``evidence_nodes`` rows. The gate-evidence persistence
  bridge here runs a caller-supplied gate decision and then persists the
  verdict as a typed ``evidence_nodes`` row through
  ``ExecutionControlStore.record_verification_graph_node`` (an existing,
  idempotent primitive — no new persistence authority).
* ``checkpoint_projector`` MUST create/load an APPROVED ``checkpoint_gate``
  evidence node BEFORE calling ``ExecutionControlStore.project_group_checkpoint``
  (the store requires ``source_table='evidence_nodes'`` + an approved
  ``checkpoint_gate`` ``source_id``). Both the evidence write and the
  projection are idempotent on a deterministic doc-08 step-3 checkpoint key, so
  checkpoint recovery never double-projects.

The production caller (08e-2/08e-3) still owns the actual gate *evaluation* —
that lives in the Slice 06 graph and is wired at the implementation call sites.
This module is the persistence + projection bridge between that decision and
the durable queue.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from iriai_build_v2.execution_control import (
    ExecutionControlStore,
    GroupCheckpointProjection,
    VerificationGraphNodeEvidence,
    WorkspaceSnapshotEvidence,
    stable_digest,
)

from . import git_service, merge_queue
from .merge_queue import (
    CheckpointProjection,
    GateOutcome,
    GroupMergeCoverage,
    MergeQueueReadiness,
    RepoApplyInput,
)
from .merge_queue import MergeQueueItem  # re-exported from journal/execution_control

__all__ = [
    "GateDecision",
    "MergeQueueWiringError",
    "verify_merge_queue_production_ready",
    "build_apply_input_provider",
    "build_gate_runner",
    "build_no_dirty_recorder",
    "build_checkpoint_projector",
]


class MergeQueueWiringError(RuntimeError):
    """A production merge-queue collaborator could not resolve a dependency."""


# ── readiness guard ─────────────────────────────────────────────────────────


# doc-08-step-8 / line-840 dependencies the partial verify_merge_queue_ready
# does NOT cover. The production guard adds a check for each.
#
# ``execution_artifact_projections`` is the journal projection-ownership
# ledger: ``ExecutionControlStore._project_group_checkpoint`` writes the
# ``dag-group:*`` projection there with ``projection_owner='merge_queue'``.
_PROJECTION_OWNER_TABLE = "execution_artifact_projections"
_PROJECTION_OWNER_COLUMN = "projection_owner"
_SANDBOX_PATCH_KIND = "sandbox_patch_summary"
_GATE_RUNNER_KINDS = ("aggregate_verdict", "checkpoint_gate")


async def _journal_projection_ownership_ready(conn: Any) -> bool:
    """Doc-08 step 8: the journal must own ``dag-group:*`` projections.

    The merge queue checkpoints by writing the ``dag-group:*`` compatibility
    projection through ``ExecutionControlStore`` projection links. If the
    ``execution_artifact_projections`` ledger (with its ``projection_owner``
    column) is absent, the queue cannot prove projection ownership and must
    fail closed.
    """

    column = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
        _PROJECTION_OWNER_TABLE,
        _PROJECTION_OWNER_COLUMN,
    )
    return column is not None


async def _sandbox_patch_capture_ready(conn: Any) -> bool:
    """Doc-08 step 8: immutable sandbox patch evidence must be representable.

    The queue applies ``sandbox_patch_summary`` evidence nodes; if the
    ``evidence_nodes`` kind constraint does not admit that kind, no patch
    evidence can exist and the queue has nothing to apply.
    """

    constraint = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'evidence_nodes_kind_check'"
    )
    return bool(constraint) and f"'{_SANDBOX_PATCH_KIND}'" in str(constraint)


async def _gate_runner_ready(conn: Any) -> bool:
    """Doc-08 step 8: the post-apply / checkpoint gate runner must be wired.

    The gate-evidence persistence bridge persists ``aggregate_verdict`` and
    ``checkpoint_gate`` evidence nodes. Both kinds must be admitted by the
    ``evidence_nodes`` schema or the bridge cannot record real gate evidence.
    """

    constraint = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'evidence_nodes_kind_check'"
    )
    text = str(constraint or "")
    return bool(constraint) and all(
        f"'{kind}'" in text for kind in _GATE_RUNNER_KINDS
    )


async def verify_merge_queue_production_ready(conn: Any) -> MergeQueueReadiness:
    """Production startup readiness guard for the durable merge queue.

    WRAPS :func:`merge_queue.verify_merge_queue_ready` (queue schema + the
    ``merge_proof``/``commit_proof``/``checkpoint_gate`` evidence kinds + git)
    and ADDS the three doc-08-step-8 / line-840 checks that the partial guard
    omits:

    * ``journal_projection_ownership`` — the ``execution_artifact_projections``
      ledger owns ``dag-group:*`` compatibility projections.
    * ``sandbox_patch_capture`` — ``sandbox_patch_summary`` evidence is
      representable.
    * ``gate_runner`` — ``aggregate_verdict`` / ``checkpoint_gate`` evidence is
      representable for the persistence bridge.

    Fails closed: ANY missing dependency yields ``ready=False`` with the
    missing dependency named in ``missing``. Never silently degrades — the
    Slice 12 atomic-landing gate must refuse to enable the control plane when
    ``ready`` is false.
    """

    base = await merge_queue.verify_merge_queue_ready(conn)
    missing = list(base.missing)

    if not await _journal_projection_ownership_ready(conn):
        missing.append("journal_projection_ownership")
    if not await _sandbox_patch_capture_ready(conn):
        missing.append("sandbox_patch_capture")
    if not await _gate_runner_ready(conn):
        missing.append("gate_runner")

    return MergeQueueReadiness(ready=not missing, missing=sorted(set(missing)))


# ── apply_input_provider ────────────────────────────────────────────────────


def _allowed_paths_for_repo(contract_rows: list[Any], repo_id: str) -> list[str]:
    """Resolve a repo's allowed-path glob set from its task contracts.

    ``task_deliverable_contracts.allowed_paths`` is a JSONB list of
    ``ContractPathRule`` objects (``repo_id`` + ``path`` + ``match_kind``).
    ``merge_queue._path_allowed`` matches by exact path, ``dir/`` prefix, or
    glob; a ``directory`` rule is normalised to a ``dir/`` prefix entry.
    """

    allowed: set[str] = set()
    for row in contract_rows:
        rules = _json_list(row["allowed_paths"])
        required = _json_list(row["required_paths"])
        generated = _json_list(row["generated_outputs"])
        for rule in [*rules, *required, *generated]:
            if not isinstance(rule, dict):
                continue
            if str(rule.get("repo_id") or "") != repo_id:
                continue
            path = str(rule.get("path") or "")
            if not path:
                continue
            if str(rule.get("match_kind") or "file") == "directory":
                allowed.add(path if path.endswith("/") else f"{path}/")
            else:
                allowed.add(path)
    return sorted(allowed)


def build_apply_input_provider(
    conn: Any,
) -> Callable[[MergeQueueItem], Awaitable[list[RepoApplyInput]]]:
    """Build the production ``apply_input_provider`` collaborator.

    Resolves a queue item into ``list[RepoApplyInput]`` from durable evidence:

    1. Each id in ``item.patch_evidence_ids`` is a ``sandbox_patch_summary``
       ``evidence_nodes`` row whose ``payload`` carries ``repo_id`` and
       ``diff_artifact_id``.
    2. ``diff_artifact_id`` indexes ``artifacts.value`` — the immutable diff
       text captured by the sandbox runner.
    3. Each id in ``item.contract_ids`` is a ``task_deliverable_contracts``
       row; its ``allowed_paths`` rules scope the patch per repo.

    Fails closed (``MergeQueueWiringError``) on any missing patch evidence
    node, missing diff artifact, or patch evidence carrying an empty repo id —
    ``apply_candidate`` must never apply an unscoped or unresolved patch.
    """

    async def provide(item: MergeQueueItem) -> list[RepoApplyInput]:
        if not item.patch_evidence_ids:
            raise MergeQueueWiringError(
                f"merge queue item {item.id} has no patch evidence ids"
            )

        contract_rows: list[Any] = []
        if item.contract_ids:
            contract_rows = list(
                await conn.fetch(
                    "SELECT id, allowed_paths, required_paths, generated_outputs "
                    "FROM task_deliverable_contracts "
                    "WHERE id = ANY($1::bigint[]) AND feature_id = $2",
                    list(item.contract_ids),
                    item.feature_id,
                )
            )

        # One RepoApplyInput per repo; a repo touched by multiple patch
        # evidence rows has its diff texts concatenated in evidence-id order so
        # the apply is deterministic.
        diffs_by_repo: dict[str, list[str]] = {}
        for evidence_id in item.patch_evidence_ids:
            node = await conn.fetchrow(
                "SELECT id, feature_id, kind, payload FROM evidence_nodes "
                "WHERE id = $1",
                evidence_id,
            )
            if node is None:
                raise MergeQueueWiringError(
                    f"merge queue item {item.id}: patch evidence node "
                    f"{evidence_id} does not exist"
                )
            if node["feature_id"] != item.feature_id:
                raise MergeQueueWiringError(
                    f"merge queue item {item.id}: patch evidence node "
                    f"{evidence_id} belongs to a different feature"
                )
            payload = _json_dict(node["payload"])
            repo_id = str(payload.get("repo_id") or "")
            if not repo_id:
                raise MergeQueueWiringError(
                    f"merge queue item {item.id}: patch evidence node "
                    f"{evidence_id} carries no repo_id"
                )
            diff_artifact_id = payload.get("diff_artifact_id")
            if diff_artifact_id is None:
                raise MergeQueueWiringError(
                    f"merge queue item {item.id}: patch evidence node "
                    f"{evidence_id} carries no diff_artifact_id"
                )
            diff_text = await conn.fetchval(
                "SELECT value FROM artifacts WHERE id = $1 AND feature_id = $2",
                int(diff_artifact_id),
                item.feature_id,
            )
            if diff_text is None:
                raise MergeQueueWiringError(
                    f"merge queue item {item.id}: diff artifact "
                    f"{diff_artifact_id} for patch evidence {evidence_id} "
                    f"is missing"
                )
            diffs_by_repo.setdefault(repo_id, []).append(str(diff_text))

        inputs: list[RepoApplyInput] = []
        for repo_id in sorted(diffs_by_repo):
            inputs.append(
                RepoApplyInput(
                    repo_id=repo_id,
                    patch_text="".join(diffs_by_repo[repo_id]),
                    allowed_paths=_allowed_paths_for_repo(contract_rows, repo_id),
                )
            )
        return inputs

    return provide


# ── gate-evidence persistence bridge ────────────────────────────────────────


@dataclass(frozen=True)
class GateDecision:
    """A caller-supplied gate verdict, persisted by the wiring as evidence.

    Slice 06 owns gate *evaluation*; this DTO carries that verdict into the
    durable merge queue. The wiring persists it as a typed ``evidence_nodes``
    row and returns the real node id — ``approved`` gate evidence cannot exist
    without a persisted evidence node.
    """

    approved: bool
    failure_class: str = ""
    detail: str = ""
    # Evidence-node ids the gate decision was derived from (raw verifier,
    # lenses, deterministic gates). Recorded as input_refs for lineage.
    input_evidence_ids: list[int] = field(default_factory=list)
    # Free-form, JSON-serialisable verdict material folded into the node
    # payload and content hash (so a changed verdict is a new node).
    verdict_payload: dict[str, Any] = field(default_factory=dict)


# A caller hands the wiring the Slice 06 gate decision for a queue item.
GateDecisionProvider = Callable[[MergeQueueItem], Awaitable[GateDecision]]


def _gate_node_idempotency_key(
    item: MergeQueueItem, kind: str, decision: GateDecision
) -> str:
    """Deterministic idempotency key for a persisted gate evidence node.

    Stable across crash recovery — re-recording the same gate verdict for the
    same queue item reuses the existing ``evidence_nodes`` row rather than
    creating a duplicate. Includes the verdict digest so a genuinely different
    verdict is a distinct node (and ``record_verification_graph_node``'s digest
    guard would reject reusing the key with a different verdict).
    """

    digest = stable_digest(
        {
            "approved": decision.approved,
            "failure_class": decision.failure_class,
            "input_evidence_ids": sorted(decision.input_evidence_ids),
            "verdict_payload": decision.verdict_payload,
        }
    )
    return (
        f"merge-queue-gate:{kind}:item-{item.id}:"
        f"g{item.group_idx}:{item.dag_sha256}:{digest}"
    )


async def _persist_gate_evidence_node(
    store: ExecutionControlStore,
    item: MergeQueueItem,
    *,
    kind: str,
    stage: str,
    decision: GateDecision,
) -> int:
    """Persist a gate verdict as a typed ``evidence_nodes`` row; return its id.

    Uses ``ExecutionControlStore.record_verification_graph_node`` — an existing
    idempotent primitive — so the wiring adds NO second persistence authority.
    The node ``status`` is ``approved`` / ``rejected`` from the gate verdict.
    Idempotent on :func:`_gate_node_idempotency_key`: a recovery re-run reuses
    the same node id.
    """

    status = "approved" if decision.approved else "rejected"
    payload = {
        "merge_queue_item_id": item.id,
        "approved": decision.approved,
        "failure_class": decision.failure_class,
        "detail": decision.detail,
        "verdict": dict(decision.verdict_payload),
    }
    evidence = VerificationGraphNodeEvidence(
        feature_id=item.feature_id,
        idempotency_key=_gate_node_idempotency_key(item, kind, decision),
        kind=kind,
        status=status,
        payload=payload,
        dag_sha256=item.dag_sha256,
        group_idx=item.group_idx,
        stage=stage,
        name=f"{kind}:item-{item.id}",
        deterministic=True,
        input_refs=sorted(decision.input_evidence_ids),
    )
    result = await store.record_verification_graph_node(evidence)
    return result.evidence.id


def build_gate_runner(
    store: ExecutionControlStore,
    gate_decision_provider: GateDecisionProvider,
) -> Callable[[MergeQueueItem], Awaitable[GateOutcome]]:
    """Build the production ``gate_runner`` collaborator.

    ``MergeQueue.run_required_gates`` calls this after a candidate is applied
    to canonical state. The wiring:

    1. Obtains the Slice 06 gate verdict from ``gate_decision_provider``.
    2. Persists that verdict as an ``aggregate_verdict`` ``evidence_nodes`` row
       (the gate-evidence persistence bridge — Slice 06 ``GateRunner`` is
       in-memory only and produces no durable id).
    3. Returns a ``GateOutcome`` carrying the REAL persisted
       ``aggregate_evidence_id``.

    ``run_required_gates`` fails the lane closed if ``approved`` is true but
    ``aggregate_evidence_id`` is ``None`` — so the bridge always persists the
    node, even for a rejection (rejections produce a ``rejected`` node and a
    typed ``failure_class``).
    """

    async def run_gates(item: MergeQueueItem) -> GateOutcome:
        decision = await gate_decision_provider(item)
        aggregate_id = await _persist_gate_evidence_node(
            store,
            item,
            kind="aggregate_verdict",
            stage="merge_queue_post_apply",
            decision=decision,
        )
        if not decision.approved:
            return GateOutcome(
                approved=False,
                aggregate_evidence_id=aggregate_id,
                failure_class=decision.failure_class or "verifier_provider",
                detail=decision.detail
                or "post-apply gates rejected the candidate",
            )
        return GateOutcome(
            approved=True,
            aggregate_evidence_id=aggregate_id,
            detail=decision.detail,
        )

    return run_gates


# ── no_dirty_recorder ───────────────────────────────────────────────────────


def _workspace_snapshot_idempotency(
    item: MergeQueueItem, repo_id: str, head_sha: str
) -> str:
    """Deterministic idempotency key for a post-commit no-dirty snapshot.

    Keyed on the queue item, repo, and post-commit HEAD so re-recording the
    same clean state reuses the existing ``workspace_snapshots`` row.
    """

    return (
        f"merge-queue-no-dirty:item-{item.id}:"
        f"g{item.group_idx}:{item.dag_sha256}:{repo_id}:{head_sha}"
    )


def build_no_dirty_recorder(
    store: ExecutionControlStore,
) -> Callable[[MergeQueueItem, str], Awaitable[int]]:
    """Build the production ``no_dirty_recorder`` collaborator.

    ``MergeQueue.commit_and_prove_clean`` calls this after a repo commits and
    its working tree is verified clean. The wiring proves the repo is clean
    (live ``git status`` — fails closed if it is not) and records a
    ``workspace_snapshots`` row via
    ``ExecutionControlStore.record_workspace_snapshot``, returning the snapshot
    id ``commit_and_prove_clean`` stamps onto the repo target.

    Idempotent on :func:`_workspace_snapshot_idempotency`: a recovery re-run of
    a clean repo reuses the existing snapshot row.
    """

    async def record(item: MergeQueueItem, repo_id: str) -> int:
        target = next(
            (t for t in item.repo_targets if t.repo_id == repo_id), None
        )
        if target is None:
            raise MergeQueueWiringError(
                f"merge queue item {item.id}: no repo target for repo "
                f"{repo_id!r} to prove clean"
            )
        repo_path = target.repo_path
        # Fail closed: the no-dirty proof must observe a genuinely clean tree.
        if not await git_service.working_tree_clean(repo_path):
            raise MergeQueueWiringError(
                f"merge queue item {item.id}: repo {repo_id!r} is not clean — "
                f"refusing to record a no-dirty snapshot"
            )
        head_sha = await git_service.head_commit(repo_path)
        status_lines = await git_service.porcelain_status(repo_path)
        worktree_status_digest = stable_digest(sorted(status_lines))
        index_digest = stable_digest(
            {"head_sha": head_sha, "status": sorted(status_lines)}
        )

        payload = {
            "feature_id": item.feature_id,
            "dag_sha256": item.dag_sha256,
            "group_idx": item.group_idx,
            "stage": "merge_queue_no_dirty",
            "repo_id": repo_id,
            "canonical_path": repo_path,
            "head_sha": head_sha,
            "index_digest": index_digest,
            "worktree_status_digest": worktree_status_digest,
            "dirty_paths": [],
            "no_dirty": True,
            "merge_queue_item_id": item.id,
            "result_commit": target.result_commit or head_sha,
        }
        evidence = WorkspaceSnapshotEvidence(
            feature_id=item.feature_id,
            payload=payload,
            dag_sha256=item.dag_sha256,
            group_idx=item.group_idx,
            stage="merge_queue_no_dirty",
            repo_id=repo_id,
            canonical_path=repo_path,
            head_sha=head_sha,
            index_digest=index_digest,
            worktree_status_digest=worktree_status_digest,
            idempotency_key=_workspace_snapshot_idempotency(
                item, repo_id, head_sha
            ),
        )
        result = await store.record_workspace_snapshot(evidence)
        return result.snapshot.id

    return record


# ── checkpoint_projector ────────────────────────────────────────────────────


def _checkpoint_key(coverage: GroupMergeCoverage) -> str:
    """The doc-08 step-3 checkpoint idempotency key for a covered group.

    Stable identity of one group checkpoint — keyed on the feature, DAG, and
    group only (NOT the covered lane id set), so a recovery re-run after a
    crash between the projector and ``complete_checkpoint`` reuses the exact
    same projection and evidence rows. The store's idempotency keys enforce
    exactly-once projection.
    """

    return (
        f"merge-queue-checkpoint:{coverage.feature_id}:"
        f"{coverage.dag_sha256}:g{coverage.group_idx}"
    )


def build_checkpoint_projector(
    store: ExecutionControlStore,
    gate_decision_provider: Callable[[GroupMergeCoverage], Awaitable[GateDecision]],
) -> Callable[[GroupMergeCoverage, dict], Awaitable[CheckpointProjection]]:
    """Build the production ``checkpoint_projector`` collaborator.

    ``GroupMergeCoordinator.checkpoint_group`` calls this once a group's lanes
    are all integrated. The wiring runs the doc-08 checkpoint projection in the
    store-mandated order:

    1. Obtain the Slice 06 checkpoint-gate verdict from
       ``gate_decision_provider`` and persist it as an APPROVED
       ``checkpoint_gate`` ``evidence_nodes`` row (the gate-evidence
       persistence bridge). ``ExecutionControlStore.project_group_checkpoint``
       REQUIRES ``source_table='evidence_nodes'`` + an approved
       ``checkpoint_gate`` ``source_id`` — so this MUST happen first.
    2. Call ``project_group_checkpoint`` with the reconstructed legacy
       ``dag-group:*`` body and that approved checkpoint-gate node as the
       source.
    3. Return a :class:`CheckpointProjection` carrying the real projection +
       evidence ids the coordinator stamps onto every covered lane.

    Idempotency (doc-08 step-3): the checkpoint-gate evidence node, the
    checkpoint-body evidence node, and the projection all use the SAME
    deterministic :func:`_checkpoint_key`. ``record_verification_graph_node``
    and ``project_group_checkpoint`` are both idempotent on their idempotency
    keys, so a crash between the projector and ``complete_checkpoint`` re-runs
    the projector with no double-projection — a recovery re-run is a no-op
    success returning identical ids.

    A non-approved checkpoint gate fails closed with ``MergeQueueWiringError``:
    the store would reject a non-approved ``checkpoint_gate`` source, and a
    checkpoint must never be projected on an unapproved gate.
    """

    async def project(
        coverage: GroupMergeCoverage, body: dict, *, supersede: bool = False
    ) -> CheckpointProjection:
        checkpoint_key = _checkpoint_key(coverage)
        decision = await gate_decision_provider(coverage)
        if not decision.approved:
            # Fail closed — never project a checkpoint on an unapproved gate.
            raise MergeQueueWiringError(
                f"group checkpoint for {coverage.feature_id} g"
                f"{coverage.group_idx} cannot be projected: the checkpoint "
                f"gate is not approved "
                f"({decision.failure_class or 'unapproved'})"
            )

        # Step 1: persist the APPROVED checkpoint_gate evidence node FIRST.
        gate_evidence = VerificationGraphNodeEvidence(
            feature_id=coverage.feature_id,
            idempotency_key=f"{checkpoint_key}:gate",
            kind="checkpoint_gate",
            status="approved",
            payload={
                "approved": True,
                "group_idx": coverage.group_idx,
                "covered_queue_item_ids": sorted(
                    set(coverage.integrated_queue_item_ids)
                    | set(coverage.done_queue_item_ids)
                ),
                "result_commits": list(coverage.result_commits),
                "verdict": dict(decision.verdict_payload),
            },
            dag_sha256=coverage.dag_sha256,
            group_idx=coverage.group_idx,
            stage="merge_queue_checkpoint",
            name=f"checkpoint_gate:g{coverage.group_idx}",
            deterministic=True,
            input_refs=sorted(decision.input_evidence_ids),
        )
        gate_result = await store.record_verification_graph_node(
            gate_evidence, supersede=supersede
        )
        checkpoint_gate_evidence_id = gate_result.evidence.id

        # The checkpoint-body evidence node — a durable record of the legacy
        # dag-group:* body the projection carries.
        body_sha256 = stable_digest(body)
        body_evidence = VerificationGraphNodeEvidence(
            feature_id=coverage.feature_id,
            idempotency_key=f"{checkpoint_key}:body",
            kind="aggregate_verdict",
            status="approved",
            payload={
                "checkpoint_body": body,
                "body_sha256": body_sha256,
                "group_idx": coverage.group_idx,
            },
            dag_sha256=coverage.dag_sha256,
            group_idx=coverage.group_idx,
            stage="merge_queue_checkpoint",
            name=f"checkpoint_body:g{coverage.group_idx}",
            deterministic=True,
            input_refs=[checkpoint_gate_evidence_id],
        )
        body_result = await store.record_verification_graph_node(
            body_evidence, supersede=supersede
        )
        checkpoint_evidence_id = body_result.evidence.id

        # Step 2: project the legacy dag-group:* compatibility artifact with
        # the approved checkpoint_gate node as the projection source.
        projection_key = f"dag-group:{coverage.group_idx}"
        projection = GroupCheckpointProjection(
            feature_id=coverage.feature_id,
            idempotency_key=f"{checkpoint_key}:projection",
            dag_sha256=coverage.dag_sha256,
            group_idx=coverage.group_idx,
            projection_key=projection_key,
            artifact_key=projection_key,
            source_table="evidence_nodes",
            source_id=checkpoint_gate_evidence_id,
            checkpoint=body,
            supersede=supersede,
        )
        projection_result = await store.project_group_checkpoint(projection)

        # Step 3: return the real ids the coordinator stamps onto covered lanes.
        return CheckpointProjection(
            checkpoint_projection_id=projection_result.row.id,
            checkpoint_gate_evidence_id=checkpoint_gate_evidence_id,
            checkpoint_evidence_id=checkpoint_evidence_id,
            body_sha256=body_sha256,
        )

    return project


# ── helpers ─────────────────────────────────────────────────────────────────


def _json_dict(value: Any) -> dict[str, Any]:
    """Coerce a JSONB column / dict / JSON string into a plain dict."""

    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    """Coerce a JSONB column / list / JSON string into a plain list."""

    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(decoded) if isinstance(decoded, list) else []
    return []
