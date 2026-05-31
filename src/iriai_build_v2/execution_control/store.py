from __future__ import annotations

import json
import hashlib
import os
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from ..public_dashboard import PublicDashboardOutbox

from .models import (
    CompatibilityProjection,
    CommitFailureProjection,
    ContractVerdict,
    DispatchAttemptRequest,
    DispatchAttemptResult,
    DispatchOutcome,
    EvidenceNode,
    EvidenceNodeResult,
    ExecutionControlError,
    ExecutionJournalResult,
    ExecutionJournalRow,
    ExecutionJournalWrite,
    GroupCheckpointProjection,
    IdempotencyConflict,
    MissingCompatibilityProjection,
    MissingRequiredProjection,
    PatchSummary,
    PromptContextEvidence,
    ProjectionLink,
    RawOutputEvidence,
    RegroupActiveProjection,
    RegroupProjection,
    RuntimeFailureEvidence,
    RuntimeFailureResult,
    RuntimeInvocationEvidence,
    RuntimeWorkspaceBinding,
    RuntimeWorkspaceBindingResult,
    SUPPORTED_PROJECTION_PREFIXES,
    SandboxLease,
    SandboxLeaseResult,
    SandboxRepoBinding,
    SandboxRepoBindingResult,
    StructuredOutputEvidence,
    TaskResultProjectionFromAttempt,
    TaskContractResult,
    TaskDeliverableContract,
    TaskResultProjection,
    UnsupportedCompatibilityProjection,
    VerifyProjection,
    VerificationGraphNodeEvidence,
    VerificationGraphProjection,
    WorkspacePreflightEvidence,
    WorkspaceRegistryEvidence,
    WorkspaceSnapshotEvidence,
    WorkspaceSnapshotResult,
    WorkspaceSnapshotRow,
    projection_idempotency_key,
    sandbox_manifest_projection_key,
    stable_digest,
    stable_json,
)

# NOTE: the typed Slice-10a snapshot models
# (``workflows.develop.execution.snapshots``) are imported LAZILY inside the
# typed snapshot builders below, never at module top. ``execution_control``'s
# package ``__init__`` imports ``.store``; a top-level import here would run
# ``workflows/develop/execution/__init__.py`` -> ``journal.py`` ->
# ``from iriai_build_v2.execution_control import __all__`` against a
# partially-initialized package and raise ``ImportError``. The lazy import
# mirrors the Slice-09 leaf-import discipline (``regroup_overlay.py`` /
# ``dag_regroup.py`` import the async typed modules lazily for the same
# reason) and ``snapshots.py``'s own lazy ``stable_digest`` import.

LEGACY_VISIBLE_ENTRY_TYPES: frozenset[str] = frozenset({
    "task_result",
    "task_contract",
    "verify_result",
    "commit_failure",
    "group_checkpoint",
    "sandbox_lease",
    "sandbox_manifest",
    "sandbox_patch_summary",
    "contract_verdict",
    "regroup_overlay",
    "regroup_active",
})

PROJECTION_KEY_PREFIXES: dict[str, tuple[str, ...]] = {
    "task_result": ("dag-task:",),
    "task_contract": ("dag-task-contract:",),
    "verify_result": ("dag-verify:",),
    "commit_failure": ("dag-commit-failure:",),
    "group_checkpoint": ("dag-group:",),
    "sandbox_lease": ("dag-sandbox:",),
    "sandbox_manifest": ("dag-sandbox:",),
    "sandbox_patch_summary": ("dag-sandbox-patch:",),
    "contract_verdict": ("dag-contract-verdict:",),
    "regroup_overlay": ("dag-regroup:",),
    "regroup_active": ("dag-regroup-active:",),
}

PROJECTION_KINDS: dict[str, str] = {
    "task_result": "task_result",
    "task_contract": "task_contract",
    "verify_result": "verify_result",
    "commit_failure": "commit_failure",
    "group_checkpoint": "group_checkpoint",
    "sandbox_lease": "sandbox_manifest",
    "sandbox_manifest": "sandbox_manifest",
    "sandbox_patch_summary": "sandbox_patch",
    "contract_verdict": "contract_verdict",
    "regroup_overlay": "regroup_overlay",
    "regroup_active": "regroup_active",
    "workspace_registry": "workspace_registry",
    "workspace_preflight": "workspace_preflight",
    "workspace_snapshot": "workspace_snapshot",
}

PROJECTION_OWNERS: dict[str, str] = {
    "task_result": "dispatcher",
    "task_contract": "contract_service",
    "verify_result": "verification_graph",
    "commit_failure": "merge_queue",
    "group_checkpoint": "merge_queue",
    "sandbox_lease": "sandbox_runner",
    "sandbox_manifest": "sandbox_runner",
    "sandbox_patch_summary": "sandbox_runner",
    "contract_verdict": "contract_service",
    "regroup_overlay": "regroup_overlay",
    "regroup_active": "regroup_overlay",
    "workspace_registry": "workspace_authority",
    "workspace_preflight": "workspace_authority",
    "workspace_snapshot": "workspace_authority",
}

PUBLIC_DASHBOARD_MAX_CONTENT_BYTES_ENV = "IRIAI_PUBLIC_DASHBOARD_MAX_CONTENT_BYTES"
PUBLIC_DASHBOARD_DEFAULT_MAX_CONTENT_BYTES = 32_000
WORKSPACE_PROJECTION_LIST_LIMIT = 50
WORKSPACE_PROJECTION_STRING_LIMIT = 4_000
VERIFICATION_GRAPH_NODE_LIST_LIMIT = 200
LEGACY_RESUME_ARTIFACT_ROW_LIMIT = 500
LEGACY_RESUME_ARTIFACT_VALUE_PREVIEW_CHARS = 4_000
_SANDBOX_TERMINAL_STATUSES = frozenset({
    "captured",
    "released",
    "retained",
    "failed",
    "poisoned",
})
_MERGE_QUEUE_ACTIVE_STATUSES = frozenset({
    "applying",
    "checkpointing",
    "claimed",
    "committing",
    "integrated",
    "leased",
    "pending",
    "queued",
    "rebasing",
    "running",
    "started",
    "verifying",
})
VERIFICATION_GRAPH_NODE_KINDS: frozenset[str] = frozenset({
    "gate_request",
    "candidate_manifest",
    "deterministic_gate",
    "context_package",
    "raw_verifier",
    "expanded_lens",
    "aggregate_verdict",
    "merge_gate",
    "checkpoint_gate",
})
VERIFICATION_GRAPH_REQUIRED_GATE_NODE_KINDS: frozenset[str] = frozenset({
    "candidate_manifest",
    "gate_request",
    "deterministic_gate",
    "context_package",
    "merge_gate",
    "checkpoint_gate",
})

CONTROL_PLANE_DEFAULT_BUDGETS: dict[str, int] = {
    "attempts": 20,
    "runtime_failures": 20,
    "workspace_snapshots": 20,
    "sandbox_snapshots": 20,
    "runtime_workspace_bindings": 20,
    "gates": 40,
    "verification_graph_nodes": 40,
    "projection_refs": 40,
}
CONTROL_PLANE_RUNTIME_FAILURE_SUMMARY_CHARS = 500
CONTROL_PLANE_QUERY_SECTIONS: tuple[str, ...] = (
    "attempts",
    "runtime_failures",
    "workspace_snapshots",
    "sandbox_snapshots",
    "runtime_workspace_bindings",
    "gate_nodes",
    "verification_graph_nodes",
    "gate_artifact_refs",
    "projection_refs",
)
CONTROL_PLANE_READINESS_GATE_NAMES: tuple[str, ...] = (
    "code-review",
    "security",
    "test-authoring",
    "qa",
    "integration",
    "verifier",
    "source-push",
    "implementation-report",
    "notify",
)
DISPATCHER_STATE_ORDER: dict[str, int] = {
    "requested": 0,
    "attempt_started": 10,
    "context_prepared": 20,
    "runtime_invoking": 30,
    "runtime_returned": 40,
    "cancelled": 45,
    "patch_capturing": 50,
    "output_normalizing": 55,
    "evidence_recording": 60,
    "succeeded": 100,
    "failed": 100,
    "incomplete": 100,
}
DISPATCHER_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "cancelled",
    "incomplete",
})
DISPATCHER_STATE_SQL_RANK = (
    "CASE dispatcher_state "
    "WHEN 'requested' THEN 0 "
    "WHEN 'attempt_started' THEN 10 "
    "WHEN 'context_prepared' THEN 20 "
    "WHEN 'runtime_invoking' THEN 30 "
    "WHEN 'runtime_returned' THEN 40 "
    "WHEN 'cancelled' THEN 45 "
    "WHEN 'patch_capturing' THEN 50 "
    "WHEN 'output_normalizing' THEN 55 "
    "WHEN 'evidence_recording' THEN 60 "
    "WHEN 'succeeded' THEN 100 "
    "WHEN 'failed' THEN 100 "
    "WHEN 'incomplete' THEN 100 "
    "ELSE 0 END"
)
DISPATCHER_RECOVERY_EVIDENCE_PAYLOAD_KEYS: tuple[str, ...] = (
    "duplicate_replay_recovery_evidence",
    "runtime_recovery_evidence",
    "stale_recovery_evidence",
    "recovery_evidence",
)


@dataclass(frozen=True)
class ControlPlaneSnapshot:
    """Bounded, store-owned snapshot contract for supervisor/dashboard readers."""

    feature_id: str
    snapshot_version: str
    source: str
    query: dict[str, Any]
    budgets: dict[str, int]
    degradation_reasons: list[str]
    truncation: dict[str, Any]
    gates: dict[str, Any]
    merge_queue: dict[str, Any]
    retry_budgets: list[dict[str, Any]]
    workspace_snapshots: list[dict[str, Any]]
    sandbox_snapshots: list[dict[str, Any]]
    runtime_workspace_bindings: list[dict[str, Any]]
    verification_graph_nodes: list[dict[str, Any]]
    checkpoint_refs: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    runtime_failures: list[dict[str, Any]]
    projection_refs: list[dict[str, Any]]

    @property
    def degraded(self) -> bool:
        return bool(self.degradation_reasons)

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "snapshot_version": self.snapshot_version,
            "feature_id": self.feature_id,
            "source": self.source,
            "query": self.query,
            "budget": dict(self.budgets),
            "budgets": dict(self.budgets),
            "degraded": self.degraded,
            "degradation_reasons": list(self.degradation_reasons),
            "truncation": self.truncation,
            "gates": self.gates,
            "merge_queue": self.merge_queue,
            "retry_budgets": self.retry_budgets,
            "workspace_snapshots": self.workspace_snapshots,
            "sandbox_snapshots": self.sandbox_snapshots,
            "runtime_workspace_bindings": self.runtime_workspace_bindings,
            "verification_graph_nodes": self.verification_graph_nodes,
            "checkpoint_refs": self.checkpoint_refs,
            "attempts": self.attempts,
            "runtime_failures": self.runtime_failures,
            "projection_refs": self.projection_refs,
        }


async def fetch_control_plane_snapshot(
    conn: Any,
    feature_id: str,
    *,
    budgets: dict[str, int] | None = None,
) -> ControlPlaneSnapshot:
    """Return a bounded typed execution-control snapshot without artifact bodies."""

    limits = _control_plane_budgets(budgets)
    degradation_reasons: list[str] = []
    attempts = await _control_plane_fetch(
        conn,
        "attempts",
        degradation_reasons,
        "SELECT id, entry_type, status, dispatcher_state, actor, runtime, "
        "group_idx, task_id, request_digest, "
        "payload->>'retry' AS retry, payload->>'attempt_no' AS attempt_no, "
        "CASE WHEN jsonb_typeof(payload->'retry_budget') = 'number' "
        "THEN payload->'retry_budget' ELSE jsonb_strip_nulls(jsonb_build_object("
        "'route', payload#>'{retry_budget,route}', "
        "'retry', payload#>'{retry_budget,retry}', "
        "'max_retries', COALESCE(payload#>'{retry_budget,max_retries}', payload->'max_retries'), "
        "'max_attempts', payload#>'{retry_budget,max_attempts}', "
        "'remaining_attempts', payload#>'{retry_budget,remaining_attempts}', "
        "'idempotency_key', payload#>'{retry_budget,idempotency_key}', "
        "'task_id', payload#>'{retry_budget,task_id}'"
        ")) END AS retry_budget, "
        "payload->>'runtime_policy_digest' AS runtime_policy_digest, "
        "CASE WHEN jsonb_typeof(payload->'workspace_snapshot_ids') = 'array' "
        "THEN payload->'workspace_snapshot_ids' ELSE '[]'::jsonb END AS workspace_snapshot_ids, "
        "created_at, updated_at "
        "FROM execution_journal_rows WHERE feature_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        limits["attempts"] + 1,
    )
    active_merge_queue_attempts = await _control_plane_fetch(
        conn,
        "merge_queue_active_attempts",
        degradation_reasons,
        "SELECT id, entry_type, "
        "COALESCE("
        "payload->>'merge_queue_status', "
        "payload->>'queue_status', "
        "payload->>'status', "
        "''"
        ") AS status, "
        "dispatcher_state, actor, runtime, "
        "group_idx, task_id, request_digest, "
        "payload->>'retry' AS retry, payload->>'attempt_no' AS attempt_no, "
        "CASE WHEN jsonb_typeof(payload->'retry_budget') = 'number' "
        "THEN payload->'retry_budget' ELSE jsonb_strip_nulls(jsonb_build_object("
        "'route', payload#>'{retry_budget,route}', "
        "'retry', payload#>'{retry_budget,retry}', "
        "'max_retries', COALESCE(payload#>'{retry_budget,max_retries}', payload->'max_retries'), "
        "'max_attempts', payload#>'{retry_budget,max_attempts}', "
        "'remaining_attempts', payload#>'{retry_budget,remaining_attempts}', "
        "'idempotency_key', payload#>'{retry_budget,idempotency_key}', "
        "'task_id', payload#>'{retry_budget,task_id}'"
        ")) END AS retry_budget, "
        "payload->>'runtime_policy_digest' AS runtime_policy_digest, "
        "CASE WHEN jsonb_typeof(payload->'workspace_snapshot_ids') = 'array' "
        "THEN payload->'workspace_snapshot_ids' ELSE '[]'::jsonb END AS workspace_snapshot_ids, "
        "created_at, updated_at "
        "FROM execution_journal_rows WHERE feature_id = $1 "
        "AND entry_type = ANY($2::text[]) "
        "AND lower(COALESCE("
        "payload->>'merge_queue_status', "
        "payload->>'queue_status', "
        "payload->>'status', "
        "''"
        ")) = ANY($3::text[]) "
        "ORDER BY id DESC LIMIT $4",
        feature_id,
        ["commit_failure", "group_checkpoint"],
        sorted(_MERGE_QUEUE_ACTIVE_STATUSES),
        limits["attempts"] + 1,
    )
    failures = await _control_plane_fetch(
        conn,
        "runtime_failures",
        degradation_reasons,
        "SELECT id, attempt_id, group_idx, stage, name, status, deterministic, "
        "COALESCE(metadata->>'failure_class', payload->>'failure_class') AS failure_class, "
        "COALESCE(metadata->>'failure_type', payload->>'failure_type') AS failure_type, "
        "COALESCE(payload#>>'{route_decision,route}', metadata->>'route', "
        "payload->>'route', payload#>>'{retry_budget,route}') AS route, "
        "COALESCE(metadata->>'operator_required', payload->>'operator_required') AS operator_required, "
        "COALESCE(metadata->>'retryable', payload->>'retryable') AS retryable, "
        "source_ref, substring(summary from 1 for 500) AS summary, "
        "char_length(summary) AS summary_length, "
        "octet_length(summary) AS summary_bytes, "
        "jsonb_strip_nulls(jsonb_build_object("
        "'route', payload#>'{retry_budget,route}', "
        "'retry', payload#>'{retry_budget,retry}', "
        "'max_retries', payload#>'{retry_budget,max_retries}', "
        "'max_attempts', payload#>'{retry_budget,max_attempts}', "
        "'remaining_attempts', payload#>'{retry_budget,remaining_attempts}', "
        "'idempotency_key', payload#>'{retry_budget,idempotency_key}', "
        "'task_id', payload#>'{retry_budget,task_id}'"
        ")) AS retry_budget, "
        "jsonb_strip_nulls(jsonb_build_object("
        "'route', payload#>'{route_decision,route}', "
        "'failure_class', payload#>'{route_decision,failure_class}', "
        "'failure_type', payload#>'{route_decision,failure_type}', "
        "'deterministic', payload#>'{route_decision,deterministic}', "
        "'retryable', payload#>'{route_decision,retryable}', "
        "'operator_required', payload#>'{route_decision,operator_required}'"
        ")) AS route_decision, "
        "created_at, finished_at "
        "FROM evidence_nodes WHERE feature_id = $1 "
        "AND kind = 'runtime_failure_context' "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        limits["runtime_failures"] + 1,
    )
    workspace_snapshots = await _control_plane_fetch(
        conn,
        "workspace_snapshots",
        degradation_reasons,
        "SELECT id, execution_journal_row_id, dag_sha256, group_idx, attempt_id, "
        "stage, repo_id, canonical_path, registry_digest, snapshot_digest, "
        "captured_at, created_at, updated_at "
        "FROM workspace_snapshots WHERE feature_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        limits["workspace_snapshots"] + 1,
    )
    sandbox_leases = await _control_plane_fetch(
        conn,
        "sandbox_snapshots",
        degradation_reasons,
        "SELECT id, execution_journal_row_id, dag_sha256, group_idx, attempt_no, "
        "mode, status, lease_owner, leased_until, lease_version, base_snapshot_ids, "
        "sandbox_root, sandbox_id, manifest_path, repo_ids, task_ids, "
        "contract_ids, lease_digest, created_at, updated_at "
        "FROM sandbox_leases WHERE feature_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        limits["sandbox_snapshots"] + 1,
    )
    runtime_workspace_bindings = await _control_plane_fetch(
        conn,
        "runtime_workspace_bindings",
        degradation_reasons,
        "SELECT id, sandbox_lease_id, attempt_id, runtime_name, cwd, "
        "workspace_override, manifest_path, status, role_metadata_digest, "
        "binding_digest, created_at, updated_at "
        "FROM runtime_workspace_bindings WHERE feature_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        limits["runtime_workspace_bindings"] + 1,
    )
    gate_nodes = await _control_plane_fetch(
        conn,
        "gate_nodes",
        degradation_reasons,
        "SELECT id, group_idx, stage, kind, name, status, deterministic, "
        "source_ref, artifact_key, created_at, updated_at "
        "FROM evidence_nodes WHERE feature_id = $1 "
        "AND kind = ANY($2::text[]) "
        "ORDER BY id DESC LIMIT $3",
        feature_id,
        [
            "gate_request",
            "deterministic_gate",
            "merge_gate",
            "checkpoint_gate",
            "aggregate_verdict",
        ],
        limits["gates"] + 1,
    )
    verification_graph_nodes = await _control_plane_fetch(
        conn,
        "verification_graph_nodes",
        degradation_reasons,
        "SELECT id, group_idx, stage, kind, name, status, deterministic, "
        "source_ref, artifact_key, metadata, created_at, updated_at "
        "FROM evidence_nodes WHERE feature_id = $1 "
        "AND kind = ANY($2::text[]) "
        "ORDER BY id DESC LIMIT $3",
        feature_id,
        sorted(VERIFICATION_GRAPH_NODE_KINDS),
        limits["verification_graph_nodes"] + 1,
    )
    gate_artifacts = await _control_plane_fetch(
        conn,
        "gate_artifact_refs",
        degradation_reasons,
        "SELECT DISTINCT ON (key) id, key, created_at "
        "FROM artifacts WHERE feature_id = $1 AND key LIKE 'dag-gate:%' "
        "ORDER BY key, id DESC LIMIT $2",
        feature_id,
        limits["gates"] + 1,
    )
    projection_refs = await _control_plane_fetch(
        conn,
        "projection_refs",
        degradation_reasons,
        "SELECT id, typed_row_id, artifact_id, source_table, source_id, "
        "projection_owner, projection_kind, projection_key, projection_sha256, "
        "legacy_event_id, dashboard_outbox_event_id, "
        "payload->>'group_idx' AS group_idx, payload->>'status' AS status, "
        "created_at "
        "FROM execution_artifact_projections WHERE feature_id = $1 "
        "AND (projection_owner = 'merge_queue' "
        "OR projection_kind IN ('commit_failure', 'group_checkpoint')) "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        limits["projection_refs"] + 1,
    )

    attempt_rows, attempts_truncated = _control_plane_bounded_rows(
        attempts,
        limits["attempts"],
    )
    active_merge_queue_rows, active_merge_queue_truncated = _control_plane_bounded_rows(
        active_merge_queue_attempts,
        limits["attempts"],
    )
    failure_rows, failures_truncated = _control_plane_bounded_rows(
        failures,
        limits["runtime_failures"],
    )
    workspace_rows, workspace_truncated = _control_plane_bounded_rows(
        workspace_snapshots,
        limits["workspace_snapshots"],
    )
    sandbox_rows, sandbox_truncated = _control_plane_bounded_rows(
        sandbox_leases,
        limits["sandbox_snapshots"],
    )
    runtime_binding_rows, runtime_bindings_truncated = _control_plane_bounded_rows(
        runtime_workspace_bindings,
        limits["runtime_workspace_bindings"],
    )
    gate_node_rows, gate_nodes_truncated = _control_plane_bounded_rows(
        gate_nodes,
        limits["gates"],
    )
    gate_artifact_rows, gate_artifacts_truncated = _control_plane_bounded_rows(
        gate_artifacts,
        limits["gates"],
    )
    verification_graph_node_rows, verification_graph_nodes_truncated = (
        _control_plane_bounded_rows(
            verification_graph_nodes,
            limits["verification_graph_nodes"],
        )
    )
    projection_rows, projection_truncated = _control_plane_bounded_rows(
        projection_refs,
        limits["projection_refs"],
    )

    attempt_summaries = [_control_plane_attempt(row) for row in attempt_rows]
    active_merge_queue_summaries = [
        _control_plane_attempt(row) for row in active_merge_queue_rows
    ]
    runtime_failure_summaries = [
        _control_plane_runtime_failure(row) for row in failure_rows
    ]
    projection_summaries = [
        _control_plane_projection_ref(row) for row in projection_rows
    ]
    gate_summaries = [_control_plane_gate_node(row) for row in gate_node_rows]
    verification_graph_node_summaries = [
        _control_plane_verification_graph_node(row)
        for row in verification_graph_node_rows
    ]
    gates = _control_plane_gates(gate_summaries, gate_artifact_rows)
    checkpoint_refs = _control_plane_checkpoint_refs(
        attempt_summaries,
        projection_summaries,
        limits["projection_refs"],
    )
    merge_queue = _control_plane_merge_queue(
        _control_plane_unique_summaries_by_id(
            [*active_merge_queue_summaries, *attempt_summaries]
        ),
        projection_summaries,
        limits["projection_refs"],
    )
    retry_budgets = _control_plane_retry_budgets(
        attempt_summaries,
        runtime_failure_summaries,
        limits["attempts"],
    )
    truncation = {
        "attempts": _control_plane_truncation_meta(
            attempt_rows,
            limits["attempts"],
            attempts_truncated,
        ),
        "merge_queue_active_attempts": _control_plane_truncation_meta(
            active_merge_queue_rows,
            limits["attempts"],
            active_merge_queue_truncated,
        ),
        "runtime_failures": _control_plane_truncation_meta(
            failure_rows,
            limits["runtime_failures"],
            failures_truncated,
        ),
        "workspace_snapshots": _control_plane_truncation_meta(
            workspace_rows,
            limits["workspace_snapshots"],
            workspace_truncated,
        ),
        "sandbox_snapshots": _control_plane_truncation_meta(
            sandbox_rows,
            limits["sandbox_snapshots"],
            sandbox_truncated,
        ),
        "runtime_workspace_bindings": _control_plane_truncation_meta(
            runtime_binding_rows,
            limits["runtime_workspace_bindings"],
            runtime_bindings_truncated,
        ),
        "gates": _control_plane_truncation_meta(
            [*gate_node_rows, *gate_artifact_rows],
            limits["gates"] * 2,
            gate_nodes_truncated or gate_artifacts_truncated,
        ),
        "verification_graph_nodes": _control_plane_truncation_meta(
            verification_graph_node_rows,
            limits["verification_graph_nodes"],
            verification_graph_nodes_truncated,
        ),
        "projection_refs": _control_plane_truncation_meta(
            projection_rows,
            limits["projection_refs"],
            projection_truncated,
        ),
    }
    source = _control_plane_source(
        typed_rows=[
            *attempt_rows,
            *active_merge_queue_rows,
            *failure_rows,
            *workspace_rows,
            *sandbox_rows,
            *runtime_binding_rows,
            *gate_node_rows,
            *verification_graph_node_rows,
            *projection_rows,
        ],
        legacy_rows=gate_artifact_rows,
        degraded=bool(degradation_reasons),
    )
    query = {
        "feature_id": feature_id,
        "sections": list(CONTROL_PLANE_QUERY_SECTIONS),
        "artifact_bodies": False,
        "runtime_failure_summary_chars": CONTROL_PLANE_RUNTIME_FAILURE_SUMMARY_CHARS,
    }
    material = {
        "feature_id": feature_id,
        "source": source,
        "query": query,
        "budgets": limits,
        "degraded": bool(degradation_reasons),
        "degradation_reasons": degradation_reasons,
        "truncation": truncation,
        "attempts": attempt_summaries,
        "runtime_failures": runtime_failure_summaries,
        "workspace_snapshots": [
            _control_plane_workspace_snapshot(row) for row in workspace_rows
        ],
        "sandbox_snapshots": [
            _control_plane_sandbox_snapshot(row) for row in sandbox_rows
        ],
        "runtime_workspace_bindings": [
            _control_plane_runtime_workspace_binding(row)
            for row in runtime_binding_rows
        ],
        "verification_graph_nodes": verification_graph_node_summaries,
        "gates": gates,
        "merge_queue": merge_queue,
        "retry_budgets": retry_budgets,
        "checkpoint_refs": checkpoint_refs,
        "projection_refs": projection_summaries,
    }
    snapshot_version = stable_digest(material)
    return ControlPlaneSnapshot(
        feature_id=feature_id,
        snapshot_version=snapshot_version,
        source=source,
        query=query,
        budgets=limits,
        degradation_reasons=degradation_reasons,
        truncation=truncation,
        gates=gates,
        merge_queue=merge_queue,
        retry_budgets=retry_budgets,
        workspace_snapshots=material["workspace_snapshots"],
        sandbox_snapshots=material["sandbox_snapshots"],
        runtime_workspace_bindings=material["runtime_workspace_bindings"],
        verification_graph_nodes=verification_graph_node_summaries,
        checkpoint_refs=checkpoint_refs,
        attempts=attempt_summaries,
        runtime_failures=runtime_failure_summaries,
        projection_refs=projection_summaries,
    )


async def _control_plane_fetch(
    conn: Any,
    section: str,
    degradation_reasons: list[str],
    query: str,
    *args: Any,
) -> list[Any]:
    try:
        return list(await conn.fetch(query, *args))
    except Exception as exc:
        degradation_reasons.append(f"{section}:{type(exc).__name__}")
        return []


def _control_plane_budgets(overrides: dict[str, int] | None) -> dict[str, int]:
    budgets = dict(CONTROL_PLANE_DEFAULT_BUDGETS)
    for key, value in (overrides or {}).items():
        if key not in budgets:
            continue
        try:
            budgets[key] = max(1, min(500, int(value)))
        except (TypeError, ValueError):
            continue
    return budgets


def _control_plane_bounded_rows(rows: list[Any], limit: int) -> tuple[list[Any], bool]:
    if len(rows) <= limit:
        return rows, False
    return rows[:limit], True


def _control_plane_unique_summaries_by_id(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    unique: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        key = row.get("id")
        if key is None:
            key = (row.get("entry_type"), row.get("group_idx"), row.get("updated_at"), idx)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _control_plane_truncation_meta(
    rows: list[Any],
    limit: int,
    truncated: bool,
) -> dict[str, Any]:
    return {
        "returned": len(rows),
        "limit": limit,
        "truncated": truncated,
    }


def _control_plane_source(
    *,
    typed_rows: list[Any],
    legacy_rows: list[Any],
    degraded: bool,
) -> str:
    if typed_rows and (legacy_rows or degraded):
        return "mixed"
    if typed_rows:
        return "typed"
    if legacy_rows:
        return "legacy_fallback"
    return "legacy_fallback" if degraded else "typed"


def _control_plane_row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = _record_get(row, key)
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None and default is not None else value


def _control_plane_payload(row: Any) -> dict[str, Any]:
    payload = _control_plane_row_get(row, "payload", {})
    decoded = _decode_json(payload, {})
    return decoded if isinstance(decoded, dict) else {}


def _control_plane_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "t", "1", "yes"}:
            return True
        if lowered in {"false", "f", "0", "no"}:
            return False
    if value is None:
        return None
    return bool(value)


def _control_plane_jsonish_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    parsed = _decode_json(value, None)
    return parsed if isinstance(parsed, list) else []


def _control_plane_jsonish_dict(value: Any) -> dict[str, Any] | None:
    parsed = _decode_json(value, None)
    if isinstance(parsed, dict) and parsed:
        return parsed
    return None


def _control_plane_jsonish_value(value: Any) -> Any:
    return _decode_json(value, value)


def _control_plane_attempt(row: Any) -> dict[str, Any]:
    legacy_payload = _control_plane_payload(row)
    retry_budget = _control_plane_jsonish_value(_control_plane_row_get(row, "retry_budget"))
    if retry_budget in (None, {}):
        retry_budget = legacy_payload.get("retry_budget") or legacy_payload.get("max_retries")
    retry = (
        _control_plane_jsonish_value(_control_plane_row_get(row, "retry"))
        or _control_plane_jsonish_value(_control_plane_row_get(row, "attempt_no"))
        or legacy_payload.get("retry")
        or legacy_payload.get("attempt_no")
    )
    workspace_snapshot_ids = _control_plane_jsonish_list(
        _control_plane_row_get(row, "workspace_snapshot_ids")
    )
    if not workspace_snapshot_ids:
        workspace_snapshot_ids = _control_plane_jsonish_list(
            legacy_payload.get("workspace_snapshot_ids")
        )
    return {
        "id": _control_plane_row_get(row, "id"),
        "entry_type": _control_plane_row_get(row, "entry_type"),
        "status": _control_plane_row_get(row, "status"),
        "dispatcher_state": _control_plane_row_get(row, "dispatcher_state"),
        "actor": _control_plane_row_get(row, "actor"),
        "runtime": _control_plane_row_get(row, "runtime"),
        "group_idx": _control_plane_row_get(row, "group_idx"),
        "task_id": _control_plane_row_get(row, "task_id"),
        "request_digest": _control_plane_row_get(row, "request_digest"),
        "retry": retry,
        "retry_budget": retry_budget if retry_budget != {} else None,
        "runtime_policy_digest": _control_plane_row_get(row, "runtime_policy_digest"),
        "workspace_snapshot_ids": workspace_snapshot_ids,
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "updated_at": _isoformat(_control_plane_row_get(row, "updated_at")),
    }


def _control_plane_workspace_snapshot(row: Any) -> dict[str, Any]:
    return {
        "id": _control_plane_row_get(row, "id"),
        "execution_journal_row_id": _control_plane_row_get(row, "execution_journal_row_id"),
        "dag_sha256": _control_plane_row_get(row, "dag_sha256"),
        "group_idx": _control_plane_row_get(row, "group_idx"),
        "attempt_id": _control_plane_row_get(row, "attempt_id"),
        "stage": _control_plane_row_get(row, "stage"),
        "repo_id": _control_plane_row_get(row, "repo_id"),
        "canonical_path": _control_plane_row_get(row, "canonical_path"),
        "registry_digest": _control_plane_row_get(row, "registry_digest"),
        "snapshot_digest": _control_plane_row_get(row, "snapshot_digest"),
        "captured_at": _isoformat(_control_plane_row_get(row, "captured_at")),
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "updated_at": _isoformat(_control_plane_row_get(row, "updated_at")),
    }


def _control_plane_sandbox_snapshot(row: Any) -> dict[str, Any]:
    return {
        "id": _control_plane_row_get(row, "id"),
        "execution_journal_row_id": _control_plane_row_get(row, "execution_journal_row_id"),
        "dag_sha256": _control_plane_row_get(row, "dag_sha256"),
        "group_idx": _control_plane_row_get(row, "group_idx"),
        "attempt_no": _control_plane_row_get(row, "attempt_no"),
        "mode": _control_plane_row_get(row, "mode"),
        "status": _control_plane_row_get(row, "status"),
        "lease_owner": _control_plane_row_get(row, "lease_owner"),
        "leased_until": _isoformat(_control_plane_row_get(row, "leased_until")),
        "lease_version": _control_plane_row_get(row, "lease_version"),
        "base_snapshot_ids": _control_plane_jsonish_list(
            _control_plane_row_get(row, "base_snapshot_ids")
        ),
        "sandbox_id": _control_plane_row_get(row, "sandbox_id"),
        "manifest_path": _control_plane_row_get(row, "manifest_path"),
        "repo_ids": _control_plane_jsonish_list(_control_plane_row_get(row, "repo_ids")),
        "task_ids": _control_plane_jsonish_list(_control_plane_row_get(row, "task_ids")),
        "contract_ids": _control_plane_jsonish_list(
            _control_plane_row_get(row, "contract_ids")
        ),
        "lease_digest": _control_plane_row_get(row, "lease_digest"),
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "updated_at": _isoformat(_control_plane_row_get(row, "updated_at")),
    }


def _control_plane_runtime_workspace_binding(row: Any) -> dict[str, Any]:
    return {
        "id": _control_plane_row_get(row, "id"),
        "sandbox_lease_id": _control_plane_row_get(row, "sandbox_lease_id"),
        "attempt_id": _control_plane_row_get(row, "attempt_id"),
        "runtime_name": _control_plane_row_get(row, "runtime_name"),
        "cwd": _control_plane_row_get(row, "cwd"),
        "workspace_override": _control_plane_row_get(row, "workspace_override"),
        "manifest_path": _control_plane_row_get(row, "manifest_path"),
        "status": _control_plane_row_get(row, "status"),
        "role_metadata_digest": _control_plane_row_get(row, "role_metadata_digest"),
        "binding_digest": _control_plane_row_get(row, "binding_digest"),
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "updated_at": _isoformat(_control_plane_row_get(row, "updated_at")),
    }


def _control_plane_gate_node(row: Any) -> dict[str, Any]:
    return {
        "id": _control_plane_row_get(row, "id"),
        "group_idx": _control_plane_row_get(row, "group_idx"),
        "stage": _control_plane_row_get(row, "stage"),
        "kind": _control_plane_row_get(row, "kind"),
        "name": _control_plane_row_get(row, "name"),
        "status": _control_plane_row_get(row, "status"),
        "deterministic": bool(_control_plane_row_get(row, "deterministic", False)),
        "source_ref": _control_plane_row_get(row, "source_ref"),
        "artifact_key": _control_plane_row_get(row, "artifact_key"),
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "updated_at": _isoformat(_control_plane_row_get(row, "updated_at")),
    }


def _control_plane_verification_graph_node(row: Any) -> dict[str, Any]:
    metadata = _control_plane_jsonish_dict(_control_plane_row_get(row, "metadata")) or {}
    return {
        "id": _control_plane_row_get(row, "id"),
        "group_idx": _control_plane_row_get(row, "group_idx"),
        "stage": _control_plane_row_get(row, "stage"),
        "kind": _control_plane_row_get(row, "kind"),
        "name": _control_plane_row_get(row, "name"),
        "status": _control_plane_row_get(row, "status"),
        "deterministic": bool(_control_plane_row_get(row, "deterministic", False)),
        "source_ref": _control_plane_row_get(row, "source_ref"),
        "artifact_key": _control_plane_row_get(row, "artifact_key"),
        "metadata": metadata,
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "updated_at": _isoformat(_control_plane_row_get(row, "updated_at")),
    }


def _control_plane_projection_ref(row: Any) -> dict[str, Any]:
    return {
        "id": _control_plane_row_get(row, "id"),
        "typed_row_id": _control_plane_row_get(row, "typed_row_id"),
        "artifact_id": _control_plane_row_get(row, "artifact_id"),
        "source_table": _control_plane_row_get(row, "source_table"),
        "source_id": _control_plane_row_get(row, "source_id"),
        "projection_owner": _control_plane_row_get(row, "projection_owner"),
        "projection_kind": _control_plane_row_get(row, "projection_kind"),
        "projection_key": _control_plane_row_get(row, "projection_key"),
        "projection_sha256": _control_plane_row_get(row, "projection_sha256"),
        "legacy_event_id": _control_plane_row_get(row, "legacy_event_id"),
        "dashboard_outbox_event_id": _control_plane_row_get(row, "dashboard_outbox_event_id"),
        "group_idx": _control_plane_jsonish_value(_control_plane_row_get(row, "group_idx")),
        "status": _control_plane_row_get(row, "status"),
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
    }


def _control_plane_runtime_failure(row: Any) -> dict[str, Any]:
    summary = str(_control_plane_row_get(row, "summary", "") or "")
    summary_length = int(_control_plane_row_get(row, "summary_length", len(summary)) or 0)
    retry_budget = _control_plane_jsonish_dict(
        _control_plane_row_get(row, "retry_budget")
    ) or {}
    route_decision = _control_plane_jsonish_dict(
        _control_plane_row_get(row, "route_decision")
    ) or {}
    route = (
        route_decision.get("route")
        or _control_plane_row_get(row, "route")
        or retry_budget.get("route")
    )
    failure_class = (
        route_decision.get("failure_class")
        or _control_plane_row_get(row, "failure_class")
    )
    failure_type = (
        route_decision.get("failure_type")
        or _control_plane_row_get(row, "failure_type")
    )
    deterministic = (
        route_decision.get("deterministic")
        if route_decision.get("deterministic") is not None
        else _control_plane_row_get(row, "deterministic", False)
    )
    return {
        "id": _control_plane_row_get(row, "id"),
        "attempt_id": _control_plane_row_get(row, "attempt_id"),
        "group_idx": _control_plane_row_get(row, "group_idx"),
        "stage": _control_plane_row_get(row, "stage"),
        "name": _control_plane_row_get(row, "name"),
        "status": _control_plane_row_get(row, "status"),
        "deterministic": _control_plane_bool(deterministic),
        "failure_class": failure_class,
        "failure_type": failure_type,
        "route": route,
        "operator_required": _control_plane_bool(
            route_decision.get("operator_required")
            if route_decision.get("operator_required") is not None
            else _control_plane_row_get(row, "operator_required")
        ),
        "retryable": _control_plane_bool(
            route_decision.get("retryable")
            if route_decision.get("retryable") is not None
            else _control_plane_row_get(row, "retryable")
        ),
        "retry_budget": retry_budget,
        "route_decision": route_decision,
        "source_ref": _control_plane_row_get(row, "source_ref"),
        "summary": summary,
        "summary_length": summary_length,
        "summary_bytes": int(_control_plane_row_get(row, "summary_bytes", 0) or 0),
        "summary_truncated": summary_length > len(summary),
        "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
        "finished_at": _isoformat(_control_plane_row_get(row, "finished_at")),
    }


def _control_plane_gates(
    gate_nodes: list[dict[str, Any]],
    gate_artifacts: list[Any],
) -> dict[str, Any]:
    artifact_names = {
        str(_control_plane_row_get(row, "key", "")).removeprefix("dag-gate:")
        for row in gate_artifacts
        if str(_control_plane_row_get(row, "key", "")).startswith("dag-gate:")
    }
    node_names = {
        str(node.get("name") or node.get("stage") or "")
        for node in gate_nodes
        if node.get("status") in {"approved", "succeeded", "passed"}
    }
    readiness = {
        name: name in artifact_names or name in node_names
        for name in CONTROL_PLANE_READINESS_GATE_NAMES
    }
    return {
        "readiness": readiness,
        "recent": gate_nodes,
        "artifact_refs": [
            {
                "id": _control_plane_row_get(row, "id"),
                "key": _control_plane_row_get(row, "key"),
                "created_at": _isoformat(_control_plane_row_get(row, "created_at")),
            }
            for row in gate_artifacts
        ],
    }


def _control_plane_checkpoint_refs(
    attempts: list[dict[str, Any]],
    projections: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    refs.extend(
        {
            "source": "execution_journal_rows",
            "typed_row_id": attempt.get("id"),
            "group_idx": attempt.get("group_idx"),
            "status": attempt.get("status"),
            "updated_at": attempt.get("updated_at"),
        }
        for attempt in attempts
        if attempt.get("entry_type") == "group_checkpoint"
    )
    refs.extend(
        {
            "source": projection.get("source_table") or "execution_artifact_projections",
            "typed_row_id": projection.get("typed_row_id"),
            "artifact_id": projection.get("artifact_id"),
            "projection_key": projection.get("projection_key"),
            "projection_sha256": projection.get("projection_sha256"),
            "created_at": projection.get("created_at"),
        }
        for projection in projections
        if projection.get("projection_kind") == "group_checkpoint"
    )
    return refs[:limit]


def _control_plane_merge_queue(
    attempts: list[dict[str, Any]],
    projections: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    item_rows = [
        attempt
        for attempt in attempts
        if attempt.get("entry_type") in {"commit_failure", "group_checkpoint"}
    ]
    projection_rows = [
        projection
        for projection in projections
        if projection.get("projection_owner") == "merge_queue"
        or projection.get("projection_kind") in {"commit_failure", "group_checkpoint"}
    ]
    items = [
        {
            "source": "execution_journal_rows",
            "typed_row_id": row.get("id"),
            "entry_type": row.get("entry_type"),
            "status": row.get("status"),
            "group_idx": row.get("group_idx"),
            "task_id": row.get("task_id"),
            "updated_at": row.get("updated_at"),
        }
        for row in item_rows
    ]
    items.extend(
        {
            "source": row.get("source_table") or "execution_artifact_projections",
            "typed_row_id": row.get("typed_row_id"),
            "artifact_id": row.get("artifact_id"),
            "projection_key": row.get("projection_key"),
            "projection_kind": row.get("projection_kind"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
        }
        for row in projection_rows
    )
    pending_count = sum(
        1
        for row in [*item_rows, *projection_rows]
        if str(row.get("status") or "").strip().lower() in _MERGE_QUEUE_ACTIVE_STATUSES
    )
    return {
        "items": items[:limit],
        "pending_count": pending_count,
        "checkpoint_count": sum(
            1
            for row in [*item_rows, *projection_rows]
            if row.get("entry_type") == "group_checkpoint"
            or row.get("projection_kind") == "group_checkpoint"
        ),
    }


def _control_plane_retry_budgets(
    attempts: list[dict[str, Any]],
    runtime_failures: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any], dict[str, Any]] = {}
    for attempt in reversed(attempts):
        key = (attempt.get("group_idx"), attempt.get("task_id"))
        if key == (None, None):
            continue
        budget = attempt.get("retry_budget")
        state = grouped.setdefault(
            key,
            {
                "group_idx": attempt.get("group_idx"),
                "task_id": attempt.get("task_id"),
                "attempts_used": 0,
                "retry_budget": budget,
                "last_status": None,
                "last_dispatcher_state": None,
                "last_attempt_id": None,
            },
        )
        state["attempts_used"] += 1
        if state["retry_budget"] is None:
            state["retry_budget"] = budget
        state["last_status"] = attempt.get("status")
        state["last_dispatcher_state"] = attempt.get("dispatcher_state")
        state["last_attempt_id"] = attempt.get("id")
    attempts_by_id = {attempt.get("id"): attempt for attempt in attempts}
    for failure in reversed(runtime_failures):
        budget = failure.get("retry_budget")
        if not isinstance(budget, dict):
            continue
        attempt = attempts_by_id.get(failure.get("attempt_id"), {})
        key = (
            failure.get("group_idx") if failure.get("group_idx") is not None else attempt.get("group_idx"),
            budget.get("task_id") or attempt.get("task_id"),
        )
        if key == (None, None):
            continue
        state = grouped.setdefault(
            key,
            {
                "group_idx": key[0],
                "task_id": key[1],
                "attempts_used": 0,
                "retry_budget": None,
                "last_status": None,
                "last_dispatcher_state": None,
                "last_attempt_id": None,
            },
        )
        if state["retry_budget"] is None:
            state["retry_budget"] = budget
        state["last_runtime_failure_id"] = failure.get("id")
        state["last_runtime_failure_route"] = failure.get("route") or budget.get("route")
        state["last_route_decision"] = failure.get("route_decision")
    for state in grouped.values():
        budget = state.get("retry_budget")
        if isinstance(budget, int):
            state["remaining"] = max(0, budget - int(state["attempts_used"]))
        elif isinstance(budget, dict):
            if isinstance(budget.get("remaining_attempts"), int):
                state["remaining"] = max(0, int(budget["remaining_attempts"]))
                continue
            retry_limit = budget.get("max_retries")
            if not isinstance(retry_limit, int):
                retry_limit = budget.get("max_attempts")
            if isinstance(retry_limit, int):
                state["remaining"] = max(
                    0,
                    int(retry_limit) - int(state["attempts_used"]),
                )
    return list(grouped.values())[:limit]


# ───────────────────────────────────────────────────────────────────────────
# Slice 10a — the TYPED control-plane snapshot store layer.
#
# This is the typed contract path (doc 10 § "Refactoring Steps" steps 1-2). It
# is ADDITIVE: the pre-Slice-10 dict-based ``fetch_control_plane_snapshot``
# (above) is untouched — later Slice 10 sub-slices switch the dashboard /
# supervisor consumers onto the typed contract. 10a delivers only the typed
# ``ControlPlaneSnapshot`` builder + the two ``ExecutionControlStore`` methods.
#
# BOUNDED-READ NON-NEGOTIABLE (doc 10):
#   * every list read is a keyed/keyset-indexed query with ``LIMIT cap + 1``
#     so truncation is explicit (the +1 sentinel row is detected, then dropped)
#   * a ~1.5s ``statement_timeout`` is applied at the store boundary; a timeout
#     returns a degraded partial snapshot, never an unbounded retry
#   * every query is feature-(and-optional-group)-scoped before any aggregate
#   * SUMMARY-ONLY columns: ids / digests / counts / bounded samples /
#     citations — NEVER artifact bodies, raw prompts, stdout/stderr, or full
#     dirty-path lists (full dirty-path lists live in ``workspace_snapshots.
#     payload`` and are reduced to count + bounded sample IN SQL).
#
# doc-10 logical-table mapping (see ``snapshots.py`` module docstring): the
# doc-10 ``SnapshotCursor`` table names ``execution_attempts`` /
# ``typed_failures`` / ``failure_route_budgets`` have no physical table; the
# physical backing is ``execution_journal_rows`` / ``evidence_nodes``.
# ───────────────────────────────────────────────────────────────────────────

# Snapshot-version cursor tables: each entry maps a doc-10 LOGICAL cursor table
# name to its physical ``(table, id_col, updated_at_col | None)``. The version
# digest is taken over MAX(id) + MAX(updated_at) of exactly these — doc 10
# § "Proposed Interfaces/Types". ``execution_journal_rows`` backs both
# ``execution_attempts`` and ``typed_failures``/``failure_route_budgets`` is
# distinct only logically; the digest still includes one cursor per logical
# name so a budget-only update advances the version.
_TYPED_SNAPSHOT_VERSION_CURSORS: tuple[tuple[str, str, str, str | None], ...] = (
    ("execution_attempts", "execution_journal_rows", "id", "updated_at"),
    ("workspace_snapshots", "workspace_snapshots", "id", "updated_at"),
    ("typed_failures", "evidence_nodes", "id", "updated_at"),
    ("failure_route_budgets", "evidence_nodes", "id", "updated_at"),
    ("merge_queue_items", "merge_queue_items", "id", "updated_at"),
    ("evidence_nodes", "evidence_nodes", "id", "updated_at"),
    ("sandbox_leases", "sandbox_leases", "id", "updated_at"),
    ("runtime_workspace_bindings", "runtime_workspace_bindings", "id", "updated_at"),
)

# evidence_nodes.kind values that carry a typed failure / route decision.
_TYPED_FAILURE_KINDS: tuple[str, ...] = (
    "runtime_failure_context",
    "failure_route_decision",
)

# evidence_nodes.kind values that are gate / aggregate-verdict nodes.
_TYPED_GATE_KINDS: tuple[str, ...] = (
    "gate_request",
    "deterministic_gate",
    "merge_gate",
    "checkpoint_gate",
    "aggregate_verdict",
)

# evidence_nodes.kind values that are checkpoint proof nodes (cited, not bodied).
_TYPED_CHECKPOINT_KINDS: tuple[str, ...] = (
    "checkpoint_gate",
    "merge_proof",
    "commit_proof",
)

# execution_journal_rows.entry_type -> ExecutionAttemptSummary.attempt_kind.
_ATTEMPT_KIND_BY_ENTRY_TYPE: dict[str, str] = {
    "dispatch_attempt": "task",
    "task_result": "task",
    "task_contract": "task",
    "verify_result": "verify",
    "contract_verdict": "verify",
    "commit_failure": "merge",
    "group_checkpoint": "checkpoint",
    "sandbox_lease": "task",
    "sandbox_manifest": "task",
    "sandbox_patch_summary": "repair",
    "regroup_overlay": "regroup",
    "regroup_active": "regroup",
}

_ATTEMPT_STATUSES: frozenset[str] = frozenset(
    {"started", "succeeded", "failed", "cancelled", "incomplete"}
)
_FAILURE_SEVERITIES: frozenset[str] = frozenset(
    {"info", "warning", "error", "fatal"}
)
_FAILURE_STATUSES: frozenset[str] = frozenset(
    {"open", "routed", "retrying", "resolved", "suppressed"}
)
_MERGE_QUEUE_STATUSES: frozenset[str] = frozenset(
    {
        "queued", "leased", "applying", "verifying", "committing",
        "integrated", "checkpointing", "done", "failed", "poisoned",
        "cancelled",
    }
)


def _typed_attempt_kind(entry_type: Any) -> str:
    return _ATTEMPT_KIND_BY_ENTRY_TYPE.get(str(entry_type or ""), "task")


def _typed_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _typed_int_list(value: Any, *, cap: int) -> list[int]:
    """Bounded list of ints from a JSONB column — capped to ``cap`` entries."""

    items = _decode_json(value, [])
    if not isinstance(items, list):
        return []
    out: list[int] = []
    for item in items:
        parsed = _typed_int(item)
        if parsed is not None:
            out.append(parsed)
        if len(out) >= cap:
            break
    return out


def _bounded_runtime_failure_context_text(value: Any, *, max_chars: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"


def _typed_str_sample(value: Any, *, cap: int) -> tuple[int, list[str]]:
    """Return ``(count, bounded_sample)`` for a JSONB string list.

    The full list (e.g. a full dirty-path list) is NEVER returned — only its
    length and the first ``cap`` entries. doc 10 § "Bounded-Read Constraints".
    """

    items = _decode_json(value, [])
    if not isinstance(items, list):
        return 0, []
    sample = [str(item) for item in items[:cap]]
    return len(items), sample


def _typed_dt(value: Any) -> datetime:
    """Coerce a row timestamp to an aware ``datetime`` (epoch fallback)."""

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _typed_dt_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


async def _typed_snapshot_fetch(
    conn: Any,
    section: str,
    degradation_reasons: list[str],
    query: str,
    *args: Any,
) -> list[Any]:
    """Run one bounded snapshot read; on error append a degradation reason.

    A failure (including a ``statement_timeout`` cancel) degrades that section
    to empty rather than raising — doc 10 § "Edge Cases": a timeout "returns
    degraded partial state"; it "never retries by dropping caps or reading raw
    artifact/event bodies".
    """

    try:
        return list(await conn.fetch(query, *args))
    except Exception as exc:  # pragma: no cover - exercised via timeout tests
        degradation_reasons.append(f"{section}:{type(exc).__name__}")
        return []


def _typed_bounded(rows: list[Any], cap: int) -> tuple[list[Any], bool]:
    """Split a ``LIMIT cap + 1`` result into ``(capped_rows, truncated)``.

    The ``cap + 1`` sentinel row is the explicit truncation signal — if it is
    present the list is truncated and the sentinel is dropped.
    """

    if len(rows) > cap:
        return rows[:cap], True
    return rows, False


async def _typed_snapshot_version_cursors(
    conn: Any,
    feature_id: str,
    degradation_reasons: list[str],
) -> list[Any]:
    """Compute the 8 doc-10 snapshot-version cursors with cheap aggregates.

    Each cursor is one feature-scoped ``MAX(id), MAX(updated_at)`` query over a
    typed table — no artifact bodies, no row scans beyond the indexed feature
    partition. doc 10: the version is "a stable digest over max ids and max
    ``updated_at`` values"; it "must be cheap".
    """

    from ..workflows.develop.execution.snapshots import SnapshotCursor

    cursors: list[Any] = []
    for logical, table, id_col, updated_col in _TYPED_SNAPSHOT_VERSION_CURSORS:
        select_updated = (
            f", MAX({updated_col}) AS max_updated"
            if updated_col is not None
            else ", NULL::timestamptz AS max_updated"
        )
        rows = await _typed_snapshot_fetch(
            conn,
            f"version:{logical}",
            degradation_reasons,
            f"SELECT MAX({id_col}) AS max_id{select_updated} "
            f"FROM {table} WHERE feature_id = $1",
            feature_id,
        )
        max_id = 0
        max_updated: datetime | None = None
        if rows:
            max_id = _typed_int(_record_get(rows[0], "max_id")) or 0
            max_updated = _typed_dt_or_none(_record_get(rows[0], "max_updated"))
        cursors.append(
            SnapshotCursor(
                table=logical,  # logical contract name — see snapshots.py
                max_id=max_id,
                max_updated_at=max_updated,
            )
        )
    return cursors


async def compute_typed_snapshot_version(conn: Any, feature_id: str) -> str:
    """Return the deterministic typed control-plane snapshot version digest.

    doc 10 § "Proposed Interfaces/Types": a stable digest over typed max-ids +
    max-``updated_at`` — NEVER over artifact bodies. Backs
    ``ExecutionControlStore.get_control_plane_snapshot_version``.
    """

    from ..workflows.develop.execution.snapshots import (
        control_plane_snapshot_version,
    )

    cursors = await _typed_snapshot_version_cursors(conn, feature_id, [])
    return control_plane_snapshot_version(cursors)


async def build_typed_control_plane_snapshot(
    conn: Any,
    query: Any,
) -> Any:
    """Build the typed :class:`ControlPlaneSnapshot` for one bounded query.

    Every list read below is feature-scoped, optionally group-scoped, uses a
    keyed index with ``LIMIT cap + 1``, and selects SUMMARY-ONLY columns. Backs
    ``ExecutionControlStore.get_control_plane_snapshot``. The caller wraps this
    in a ``statement_timeout``-bounded transaction.
    """

    from ..workflows.develop.execution.snapshots import (
        ControlPlaneSnapshot,
        EvidenceRef,
        ExecutionAttemptSummary,
        GateStatusSummary,
        MergeQueueSummary,
        RetryBudgetSummary,
        RuntimeBindingSummary,
        SandboxLeaseSummary,
        TypedFailureSummary,
        WorkspaceSnapshotSummary,
        control_plane_snapshot_version,
    )

    feature_id = str(query.feature_id)
    group_idx = query.group_idx
    budget = query.budget
    path_cap = int(budget.max_path_samples_per_snapshot)
    ref_cap = int(budget.max_evidence_refs)
    degradation_reasons: list[str] = []

    # Optional group scope: a literal SQL fragment + arg, applied to every read
    # BEFORE any aggregation (doc 10: "enforce feature and optional group scope
    # on every table read before joining or aggregating").
    def _group_clause(arg_index: int, column: str = "group_idx") -> str:
        if group_idx is None:
            return ""
        return f" AND {column} = ${arg_index}"

    # ── execution attempts (execution_journal_rows) ────────────────────────
    attempt_args: list[Any] = [feature_id]
    attempt_group = ""
    if group_idx is not None:
        attempt_args.append(group_idx)
        attempt_group = _group_clause(len(attempt_args))
    attempt_args.append(int(budget.max_attempts) + 1)
    attempt_rows_raw = await _typed_snapshot_fetch(
        conn,
        "attempts",
        degradation_reasons,
        "SELECT id, feature_id, dag_sha256, group_idx, task_id, entry_type, "
        "dispatcher_state, actor, runtime, status, request_digest, "
        "payload->>'retry' AS retry, payload->>'attempt_no' AS attempt_no, "
        "payload->>'workspace_snapshot_id' AS workspace_snapshot_id, "
        "CASE WHEN jsonb_typeof(payload->'workspace_snapshot_ids') = 'array' "
        "THEN payload->'workspace_snapshot_ids' ELSE '[]'::jsonb END "
        "AS workspace_snapshot_ids, "
        "created_at, updated_at "
        "FROM execution_journal_rows "
        f"WHERE feature_id = $1{attempt_group} "
        f"ORDER BY id DESC LIMIT ${len(attempt_args)}",
        *attempt_args,
    )
    attempt_rows, attempts_truncated = _typed_bounded(
        attempt_rows_raw, int(budget.max_attempts)
    )

    # ── workspace snapshots ────────────────────────────────────────────────
    ws_args: list[Any] = [feature_id]
    ws_group = ""
    if group_idx is not None:
        ws_args.append(group_idx)
        ws_group = _group_clause(len(ws_args))
    ws_args.append(int(budget.max_workspace_snapshots) + 1)
    # dirty/forbidden lists are reduced to count + bounded sample IN SQL so the
    # full list never crosses the store boundary.
    workspace_rows_raw = await _typed_snapshot_fetch(
        conn,
        "workspace_snapshots",
        degradation_reasons,
        "SELECT id, attempt_id, group_idx, repo_id, "
        "COALESCE(payload->>'role', '') AS role, canonical_path, "
        "COALESCE(payload->>'workspace_relative_path', '') AS workspace_relative_path, "
        "stage, COALESCE(payload->>'head_sha', '') AS head_sha, "
        "COALESCE(payload->>'index_digest', '') AS index_digest, "
        "COALESCE(payload->>'worktree_status_digest', '') AS worktree_status_digest, "
        "COALESCE((payload->>'no_dirty')::boolean, false) AS no_dirty, "
        "COALESCE(payload->>'safety_status', '') AS safety_status, "
        "COALESCE(jsonb_array_length("
        "CASE WHEN jsonb_typeof(payload->'dirty_paths') = 'array' "
        "THEN payload->'dirty_paths' ELSE '[]'::jsonb END), 0) AS dirty_path_count, "
        "CASE WHEN jsonb_typeof(payload->'dirty_paths') = 'array' "
        "THEN payload->'dirty_paths' ELSE '[]'::jsonb END AS dirty_paths, "
        "COALESCE(jsonb_array_length("
        "CASE WHEN jsonb_typeof(payload->'forbidden_paths') = 'array' "
        "THEN payload->'forbidden_paths' ELSE '[]'::jsonb END), 0) "
        "AS forbidden_path_count, "
        "CASE WHEN jsonb_typeof(payload->'forbidden_paths') = 'array' "
        "THEN payload->'forbidden_paths' ELSE '[]'::jsonb END AS forbidden_paths, "
        "captured_at, created_at "
        "FROM workspace_snapshots "
        f"WHERE feature_id = $1{ws_group} "
        f"ORDER BY id DESC LIMIT ${len(ws_args)}",
        *ws_args,
    )
    workspace_rows, workspace_truncated = _typed_bounded(
        workspace_rows_raw, int(budget.max_workspace_snapshots)
    )

    # ── typed failures (evidence_nodes — failure/route-decision kinds) ─────
    failure_args: list[Any] = [feature_id, list(_TYPED_FAILURE_KINDS)]
    failure_group = ""
    if group_idx is not None:
        failure_args.append(group_idx)
        failure_group = _group_clause(len(failure_args))
    failure_args.append(int(budget.max_failures) + 1)
    failure_rows_raw = await _typed_snapshot_fetch(
        conn,
        "typed_failures",
        degradation_reasons,
        "SELECT id, attempt_id, group_idx, kind, status, deterministic, "
        "COALESCE(metadata->>'failure_class', payload->>'failure_class', "
        "payload#>>'{route_decision,failure_class}', 'unknown') AS failure_class, "
        "COALESCE(metadata->>'failure_type', payload->>'failure_type', "
        "payload#>>'{route_decision,failure_type}', 'unclassified') AS failure_type, "
        "COALESCE(metadata->>'severity', payload->>'severity', "
        "payload#>>'{route_decision,severity}', 'error') AS severity, "
        "COALESCE(metadata->>'operator_required', payload->>'operator_required', "
        "payload#>>'{route_decision,operator_required}') AS operator_required, "
        "COALESCE(metadata->>'retryable', payload->>'retryable', "
        "payload#>>'{route_decision,retryable}') AS retryable, "
        "COALESCE(payload#>>'{route_decision,route}', metadata->>'route', "
        "payload->>'route') AS route, "
        "COALESCE(metadata->>'signature_hash', payload->>'signature_hash', "
        "payload#>>'{route_decision,signature_hash}', '') AS signature_hash, "
        # Slice 10c-2: project the REAL typed route-budget fields (the carried
        # P3-10a-1). The Slice-07 router writes `budget_remaining` /
        # `budget_exhausted` onto the `route_decision` payload and a nested
        # `retry_budget` dict (`remaining_attempts` / `max_attempts` /
        # `reservation_ordinal`); see implementation.py
        # `_route_decision_compat_payload`. These columns let `_typed_retry
        # _budgets` read a genuine budget instead of the conservative `0`.
        "COALESCE("
        "payload#>>'{route_decision,budget_remaining}', "
        "payload#>>'{retry_budget,remaining_attempts}', "
        "payload#>>'{route_decision,retry_budget,remaining_attempts}'"
        ") AS budget_remaining, "
        "COALESCE("
        "payload#>>'{route_decision,retry_budget,max_attempts}', "
        "payload#>>'{retry_budget,max_attempts}', "
        "payload#>>'{route_decision,retry_budget,max_retries}', "
        "payload#>>'{retry_budget,max_retries}'"
        ") AS budget_total, "
        "COALESCE("
        "payload#>>'{route_decision,reservation_ordinal}', "
        "payload#>>'{retry_budget,reservation_ordinal}', "
        "payload#>>'{route_decision,retry_budget,reservation_ordinal}'"
        ") AS budget_used, "
        "COALESCE("
        "(payload#>>'{route_decision,budget_exhausted}'), "
        "(payload#>>'{retry_budget,budget_exhausted}')"
        ") AS budget_exhausted, "
        "payload#>>'{route_decision,budget_key}' AS budget_key, "
        "substring(COALESCE(summary, '') from 1 for 500) AS summary, "
        "artifact_id, artifact_key, event_id, "
        "created_at, finished_at, updated_at "
        "FROM evidence_nodes "
        f"WHERE feature_id = $1 AND kind = ANY($2::text[]){failure_group} "
        f"ORDER BY id DESC LIMIT ${len(failure_args)}",
        *failure_args,
    )
    failure_rows, failures_truncated = _typed_bounded(
        failure_rows_raw, int(budget.max_failures)
    )

    # ── merge queue items ──────────────────────────────────────────────────
    mq_args: list[Any] = [feature_id]
    mq_group = ""
    if group_idx is not None:
        mq_args.append(group_idx)
        mq_group = _group_clause(len(mq_args))
    mq_args.append(int(budget.max_merge_items) + 1)
    merge_rows_raw = await _typed_snapshot_fetch(
        conn,
        "merge_queue_items",
        degradation_reasons,
        "SELECT id, feature_id, dag_sha256, group_idx, repo_id, status, "
        "priority, lease_owner, leased_until, lease_version, result_commit, "
        "failure_id, "
        "CASE WHEN jsonb_typeof(gate_evidence_ids) = 'array' "
        "THEN gate_evidence_ids ELSE '[]'::jsonb END AS gate_evidence_ids, "
        "updated_at "
        "FROM merge_queue_items "
        f"WHERE feature_id = $1{mq_group} "
        f"ORDER BY id DESC LIMIT ${len(mq_args)}",
        *mq_args,
    )
    merge_rows, merge_truncated = _typed_bounded(
        merge_rows_raw, int(budget.max_merge_items)
    )

    # ── sandbox leases ─────────────────────────────────────────────────────
    sandbox_args: list[Any] = [feature_id]
    sandbox_group = ""
    if group_idx is not None:
        sandbox_args.append(group_idx)
        sandbox_group = _group_clause(len(sandbox_args))
    sandbox_args.append(int(budget.max_attempts) + 1)
    sandbox_rows_raw = await _typed_snapshot_fetch(
        conn,
        "sandbox_leases",
        degradation_reasons,
        "SELECT id, feature_id, dag_sha256, group_idx, mode, status, "
        "sandbox_root, "
        "CASE WHEN jsonb_typeof(patch_summary_ids) = 'array' "
        "THEN patch_summary_ids ELSE '[]'::jsonb END AS patch_summary_ids, "
        "leased_until, updated_at "
        "FROM sandbox_leases "
        f"WHERE feature_id = $1{sandbox_group} "
        f"ORDER BY id DESC LIMIT ${len(sandbox_args)}",
        *sandbox_args,
    )
    sandbox_rows, sandbox_truncated = _typed_bounded(
        sandbox_rows_raw, int(budget.max_attempts)
    )

    # ── runtime workspace bindings ─────────────────────────────────────────
    # runtime_workspace_bindings has no group_idx column — feature-scoped only.
    binding_rows_raw = await _typed_snapshot_fetch(
        conn,
        "runtime_workspace_bindings",
        degradation_reasons,
        "SELECT id, sandbox_lease_id, attempt_id, runtime_name, status, cwd, "
        "updated_at "
        "FROM runtime_workspace_bindings "
        "WHERE feature_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        feature_id,
        int(budget.max_attempts) + 1,
    )
    binding_rows, bindings_truncated = _typed_bounded(
        binding_rows_raw, int(budget.max_attempts)
    )

    # ── gate nodes (evidence_nodes — gate kinds) ───────────────────────────
    gate_args: list[Any] = [feature_id, list(_TYPED_GATE_KINDS)]
    gate_group = ""
    if group_idx is not None:
        gate_args.append(group_idx)
        gate_group = _group_clause(len(gate_args))
    gate_args.append(int(budget.max_gate_results) + 1)
    gate_rows_raw = await _typed_snapshot_fetch(
        conn,
        "gates",
        degradation_reasons,
        "SELECT id, group_idx, kind, name, stage, status, deterministic, "
        "failure_id, created_at "
        "FROM evidence_nodes "
        f"WHERE feature_id = $1 AND kind = ANY($2::text[]){gate_group} "
        f"ORDER BY id DESC LIMIT ${len(gate_args)}",
        *gate_args,
    )
    gate_rows, gates_truncated = _typed_bounded(
        gate_rows_raw, int(budget.max_gate_results)
    )

    # ── checkpoint evidence refs (evidence_nodes — checkpoint/proof kinds) ──
    checkpoint_args: list[Any] = [feature_id, list(_TYPED_CHECKPOINT_KINDS)]
    checkpoint_group = ""
    if group_idx is not None:
        checkpoint_args.append(group_idx)
        checkpoint_group = _group_clause(len(checkpoint_args))
    checkpoint_args.append(ref_cap + 1)
    checkpoint_rows_raw = await _typed_snapshot_fetch(
        conn,
        "checkpoints",
        degradation_reasons,
        "SELECT id, kind, group_idx, status, "
        "substring(COALESCE(summary, '') from 1 for 200) AS summary, "
        "artifact_key "
        "FROM evidence_nodes "
        f"WHERE feature_id = $1 AND kind = ANY($2::text[]){checkpoint_group} "
        f"ORDER BY id DESC LIMIT ${len(checkpoint_args)}",
        *checkpoint_args,
    )
    checkpoint_rows, checkpoints_truncated = _typed_bounded(
        checkpoint_rows_raw, ref_cap
    )

    # ── version cursors (cheap aggregates) ─────────────────────────────────
    cursors = await _typed_snapshot_version_cursors(
        conn, feature_id, degradation_reasons
    )
    snapshot_version = control_plane_snapshot_version(cursors)

    # ── map rows -> typed summaries ────────────────────────────────────────
    attempt_summaries: list[Any] = []
    for row in attempt_rows:
        ws_id = _typed_int(_record_get(row, "workspace_snapshot_id"))
        if ws_id is None:
            ws_ids = _typed_int_list(
                _record_get(row, "workspace_snapshot_ids"), cap=1
            )
            ws_id = ws_ids[0] if ws_ids else None
        status = str(_record_get(row, "status") or "started")
        attempt_summaries.append(
            ExecutionAttemptSummary(
                attempt_id=_typed_int(_record_get(row, "id")) or 0,
                feature_id=str(_record_get(row, "feature_id") or feature_id),
                dag_sha256=str(_record_get(row, "dag_sha256") or ""),
                group_idx=_typed_int(_record_get(row, "group_idx")),
                task_id=(
                    str(_record_get(row, "task_id"))
                    if _record_get(row, "task_id") is not None
                    else None
                ),
                attempt_kind=_typed_attempt_kind(_record_get(row, "entry_type")),
                stage=str(_record_get(row, "dispatcher_state") or ""),
                retry=(
                    _typed_int(_record_get(row, "retry"))
                    or _typed_int(_record_get(row, "attempt_no"))
                    or 0
                ),
                status=status if status in _ATTEMPT_STATUSES else "started",
                actor=str(_record_get(row, "actor") or ""),
                runtime=str(_record_get(row, "runtime") or ""),
                input_digest=str(_record_get(row, "request_digest") or ""),
                workspace_snapshot_id=ws_id,
                latest_evidence_ids=[],
                started_at=_typed_dt(_record_get(row, "created_at")),
                finished_at=(
                    _typed_dt_or_none(_record_get(row, "updated_at"))
                    if status in DISPATCHER_TERMINAL_STATUSES
                    else None
                ),
                updated_at=_typed_dt(_record_get(row, "updated_at")),
            )
        )

    workspace_summaries: list[Any] = []
    for row in workspace_rows:
        _, dirty_sample = _typed_str_sample(
            _record_get(row, "dirty_paths"), cap=path_cap
        )
        _, forbidden_sample = _typed_str_sample(
            _record_get(row, "forbidden_paths"), cap=path_cap
        )
        workspace_summaries.append(
            WorkspaceSnapshotSummary(
                snapshot_id=_typed_int(_record_get(row, "id")) or 0,
                attempt_id=_typed_int(_record_get(row, "attempt_id")),
                group_idx=_typed_int(_record_get(row, "group_idx")),
                repo_id=str(_record_get(row, "repo_id") or ""),
                role=str(_record_get(row, "role") or ""),
                canonical_path=str(_record_get(row, "canonical_path") or ""),
                workspace_relative_path=str(
                    _record_get(row, "workspace_relative_path") or ""
                ),
                stage=str(_record_get(row, "stage") or ""),
                head_sha=str(_record_get(row, "head_sha") or ""),
                index_digest=str(_record_get(row, "index_digest") or ""),
                worktree_status_digest=str(
                    _record_get(row, "worktree_status_digest") or ""
                ),
                no_dirty=bool(_record_get(row, "no_dirty")),
                safety_status=str(_record_get(row, "safety_status") or ""),
                dirty_path_count=_typed_int(
                    _record_get(row, "dirty_path_count")
                ) or 0,
                dirty_path_sample=dirty_sample,
                forbidden_path_count=_typed_int(
                    _record_get(row, "forbidden_path_count")
                ) or 0,
                forbidden_path_sample=forbidden_sample,
                captured_at=_typed_dt(
                    _record_get(row, "captured_at")
                    or _record_get(row, "created_at")
                ),
            )
        )

    failure_summaries: list[Any] = []
    for row in failure_rows:
        severity = str(_record_get(row, "severity") or "error").lower()
        fstatus = str(_record_get(row, "status") or "open").lower()
        # evidence_nodes.status uses pending/running/approved/...; map to the
        # typed failure lifecycle (open/routed/retrying/resolved/suppressed).
        if fstatus not in _FAILURE_STATUSES:
            fstatus = (
                "resolved"
                if fstatus in {"approved", "skipped"}
                else "retrying"
                if fstatus == "running"
                else "open"
            )
        refs: list[Any] = []
        artifact_id = _typed_int(_record_get(row, "artifact_id"))
        if artifact_id is not None:
            refs.append(
                EvidenceRef(
                    table="artifacts",
                    id=artifact_id,
                    citation=f"artifact:{_record_get(row, 'artifact_key') or ''} "
                    f"id={artifact_id}",
                    kind="failure_artifact",
                    artifact_key=str(_record_get(row, "artifact_key") or ""),
                )
            )
        event_id = _typed_int(_record_get(row, "event_id"))
        if event_id is not None:
            refs.append(
                EvidenceRef(
                    table="events",
                    id=event_id,
                    citation=f"event:id={event_id}",
                    kind="failure_event",
                )
            )
        failure_summaries.append(
            TypedFailureSummary(
                failure_id=_typed_int(_record_get(row, "id")) or 0,
                attempt_id=_typed_int(_record_get(row, "attempt_id")),
                evidence_id=_typed_int(_record_get(row, "id")),
                failure_class=str(_record_get(row, "failure_class") or "unknown"),
                failure_type=str(
                    _record_get(row, "failure_type") or "unclassified"
                ),
                severity=severity if severity in _FAILURE_SEVERITIES else "error",
                deterministic=bool(_record_get(row, "deterministic")),
                operator_required=_control_plane_bool(
                    _record_get(row, "operator_required")
                ) or False,
                retryable=_control_plane_bool(
                    _record_get(row, "retryable")
                ) or False,
                status=fstatus,
                route=str(_record_get(row, "route") or ""),
                signature_hash=str(_record_get(row, "signature_hash") or ""),
                summary=str(_record_get(row, "summary") or ""),
                evidence_refs=refs[:ref_cap],
                created_at=_typed_dt(_record_get(row, "created_at")),
                resolved_at=_typed_dt_or_none(_record_get(row, "finished_at")),
            )
        )

    merge_summaries: list[Any] = []
    for row in merge_rows:
        status = str(_record_get(row, "status") or "queued").lower()
        merge_summaries.append(
            MergeQueueSummary(
                item_id=_typed_int(_record_get(row, "id")) or 0,
                feature_id=str(_record_get(row, "feature_id") or feature_id),
                dag_sha256=str(_record_get(row, "dag_sha256") or ""),
                group_idx=_typed_int(_record_get(row, "group_idx")) or 0,
                repo_id=str(_record_get(row, "repo_id") or ""),
                status=status if status in _MERGE_QUEUE_STATUSES else "queued",
                priority=_typed_int(_record_get(row, "priority")) or 0,
                lease_owner=(
                    str(_record_get(row, "lease_owner"))
                    if _record_get(row, "lease_owner") is not None
                    else None
                ),
                leased_until=_typed_dt_or_none(_record_get(row, "leased_until")),
                lease_version=_typed_int(_record_get(row, "lease_version")) or 0,
                result_commit=str(_record_get(row, "result_commit") or ""),
                failure_id=_typed_int(_record_get(row, "failure_id")),
                required_gate_evidence_ids=_typed_int_list(
                    _record_get(row, "gate_evidence_ids"), cap=ref_cap
                ),
                updated_at=_typed_dt(_record_get(row, "updated_at")),
            )
        )

    sandbox_summaries: list[Any] = []
    for row in sandbox_rows:
        sandbox_summaries.append(
            SandboxLeaseSummary(
                lease_id=_typed_int(_record_get(row, "id")) or 0,
                feature_id=str(_record_get(row, "feature_id") or feature_id),
                dag_sha256=str(_record_get(row, "dag_sha256") or ""),
                group_idx=_typed_int(_record_get(row, "group_idx")) or 0,
                mode=str(_record_get(row, "mode") or ""),
                status=str(_record_get(row, "status") or ""),
                sandbox_root=str(_record_get(row, "sandbox_root") or ""),
                patch_summary_ids=_typed_int_list(
                    _record_get(row, "patch_summary_ids"), cap=ref_cap
                ),
                leased_until=_typed_dt_or_none(_record_get(row, "leased_until")),
                updated_at=_typed_dt(_record_get(row, "updated_at")),
            )
        )

    binding_summaries: list[Any] = []
    for row in binding_rows:
        binding_summaries.append(
            RuntimeBindingSummary(
                binding_id=_typed_int(_record_get(row, "id")) or 0,
                sandbox_lease_id=_typed_int(
                    _record_get(row, "sandbox_lease_id")
                ) or 0,
                attempt_id=_typed_int(_record_get(row, "attempt_id")) or 0,
                runtime_name=str(_record_get(row, "runtime_name") or ""),
                status=str(_record_get(row, "status") or ""),
                cwd=str(_record_get(row, "cwd") or ""),
                updated_at=_typed_dt(_record_get(row, "updated_at")),
            )
        )

    gate_summaries: list[Any] = []
    for row in gate_rows:
        status = str(_record_get(row, "status") or "").lower()
        gate_summaries.append(
            GateStatusSummary(
                gate_name=str(
                    _record_get(row, "name")
                    or _record_get(row, "stage")
                    or _record_get(row, "kind")
                    or ""
                ),
                group_idx=_typed_int(_record_get(row, "group_idx")),
                approved=status in {"approved", "succeeded", "passed"},
                deterministic=bool(_record_get(row, "deterministic")),
                evidence_id=_typed_int(_record_get(row, "id")) or 0,
                failure_id=_typed_int(_record_get(row, "failure_id")),
                created_at=_typed_dt(_record_get(row, "created_at")),
            )
        )

    checkpoint_refs: list[Any] = []
    for row in checkpoint_rows:
        node_id = _typed_int(_record_get(row, "id")) or 0
        checkpoint_refs.append(
            EvidenceRef(
                table="evidence_nodes",
                id=node_id,
                citation=f"evidence_node:{_record_get(row, 'kind') or ''} "
                f"id={node_id}",
                kind=str(_record_get(row, "kind") or ""),
                summary=str(_record_get(row, "summary") or ""),
                artifact_key=str(_record_get(row, "artifact_key") or ""),
            )
        )

    # ── truncation metadata ────────────────────────────────────────────────
    # The `LIMIT cap + 1` probe proves a section is truncated but does NOT
    # reveal the true total (counting it would be a second unbounded read).
    # `omitted_counts[section] = 1` records "at least one omitted" — the
    # consumer reads it as a degraded/partial signal, not an exact count. The
    # `SnapshotCursor` carries the keyset position for a bounded continuation.
    omitted_counts: dict[str, int] = {}
    truncation_flags = {
        "active_attempts": attempts_truncated,
        "workspace_snapshots": workspace_truncated,
        "latest_failures": failures_truncated,
        "merge_queue": merge_truncated,
        "sandbox_leases": sandbox_truncated,
        "runtime_bindings": bindings_truncated,
        "gates": gates_truncated,
        "checkpoints": checkpoints_truncated,
    }
    for section, is_truncated in truncation_flags.items():
        if is_truncated:
            omitted_counts[section] = 1
    truncated = bool(omitted_counts)

    # ── retry budgets (derived from typed failure rows) ────────────────────
    retry_budgets = _typed_retry_budgets(
        failure_rows, int(budget.max_retry_budgets)
    )

    # ── source classification ──────────────────────────────────────────────
    typed_present = bool(
        attempt_rows
        or workspace_rows
        or failure_rows
        or merge_rows
        or sandbox_rows
        or binding_rows
        or gate_rows
    )
    if degradation_reasons and typed_present:
        source = "mixed"
    elif typed_present:
        source = "typed"
    else:
        source = "legacy_fallback"

    active_group = _typed_active_group(attempt_summaries, merge_summaries)
    return ControlPlaneSnapshot(
        feature_id=feature_id,
        snapshot_version=snapshot_version,
        generated_at=datetime.now(timezone.utc),
        source=source,
        degraded=bool(degradation_reasons),
        degradation_reasons=sorted(set(degradation_reasons)),
        truncated=truncated,
        omitted_counts=omitted_counts,
        cursors=cursors,
        active_group_idx=active_group,
        active_attempts=attempt_summaries,
        workspace_snapshots=workspace_summaries,
        latest_failures=failure_summaries,
        merge_queue=merge_summaries,
        retry_budgets=retry_budgets,
        sandbox_leases=sandbox_summaries,
        runtime_bindings=binding_summaries,
        gates=gate_summaries,
        checkpoints=checkpoint_refs,
        recommended_route="",
        recommended_action="observe",
        evidence_refs=[],
    )


def _typed_active_group(
    attempts: list[Any],
    merge_items: list[Any],
) -> int | None:
    """Pick the active group: newest non-terminal attempt, else merge item."""

    for attempt in attempts:
        if (
            attempt.group_idx is not None
            and attempt.status not in DISPATCHER_TERMINAL_STATUSES
        ):
            return attempt.group_idx
    for item in merge_items:
        if item.status not in {"done", "failed", "poisoned", "cancelled"}:
            return item.group_idx
    if attempts and attempts[0].group_idx is not None:
        return attempts[0].group_idx
    return None


def _typed_retry_budgets(failure_rows: list[Any], cap: int) -> list[Any]:
    """Derive bounded :class:`RetryBudgetSummary` rows from typed failure rows.

    Slice 10c-2 — the REAL typed budget source (resolves the carried
    P3-10a-1). The as-built control plane has no dedicated
    ``failure_route_budgets`` table (see the ``snapshots.py`` module docstring's
    doc-10 ambiguity resolution), but it does NOT need one: the Slice-07 typed
    failure router persists the genuine route budget ONTO the typed failure
    rows themselves. ``implementation.py:_route_decision_compat_payload``
    writes a ``route_decision`` payload carrying ``budget_remaining`` /
    ``budget_exhausted`` and a nested ``retry_budget`` dict
    (``remaining_attempts`` / ``max_attempts`` / ``reservation_ordinal``).
    The typed-failure SELECT in :func:`build_typed_control_plane_snapshot`
    now projects those fields (``budget_remaining`` / ``budget_total`` /
    ``budget_used`` / ``budget_exhausted``), so this derives a genuine
    per-``(route, signature_hash)`` budget with NO extra read.

    For each ``(route, signature_hash)`` group the LATEST typed failure row
    (failure rows arrive ``ORDER BY id DESC``, so the first row seen per key is
    the newest) supplies the authoritative budget: its ``budget_remaining`` is
    the router's last word on whether retry/repair budget is still available.
    Fail-safe: when a typed row carries no router budget at all (a legacy /
    pre-router failure), ``budget_remaining`` stays ``0`` and
    ``terminal_reason`` records ``no_typed_budget`` so the supervisor's
    "budget remains" gate is conservatively false — never an over-optimistic
    "retry has budget". The result is capped at ``cap`` entries.
    """

    from ..workflows.develop.execution.snapshots import RetryBudgetSummary

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in failure_rows:
        route = str(_record_get(row, "route") or "")
        if not route:
            continue
        signature = str(_record_get(row, "signature_hash") or "")
        key = (route, signature)
        # Failure rows arrive newest-first (`ORDER BY id DESC`). The newest row
        # per key is the router's current budget verdict; count every row in
        # the window as an observed occurrence for `budget_used` fallback.
        budget_remaining = _typed_int(_record_get(row, "budget_remaining"))
        budget_total = _typed_int(_record_get(row, "budget_total"))
        budget_used = _typed_int(_record_get(row, "budget_used"))
        budget_exhausted = _control_plane_bool(
            _record_get(row, "budget_exhausted")
        )
        if key not in grouped:
            grouped[key] = {
                "route": route,
                "signature": signature,
                "count": 0,
                "remaining": budget_remaining,
                "total": budget_total,
                "used": budget_used,
                "exhausted": bool(budget_exhausted),
                "has_typed_budget": budget_remaining is not None
                or budget_total is not None,
            }
        grouped[key]["count"] += 1
    summaries: list[Any] = []
    for entry in list(grouped.values())[:cap]:
        occurrences = int(entry["count"])
        has_typed = bool(entry["has_typed_budget"])
        # The router's `budget_remaining` is authoritative when present;
        # `budget_exhausted=true` pins remaining to 0 regardless.
        if entry["exhausted"]:
            remaining = 0
        elif entry["remaining"] is not None:
            remaining = max(0, int(entry["remaining"]))
        else:
            remaining = 0
        # `budget_used` is the router's reservation ordinal when present, else
        # the observed occurrence count in this bounded window.
        used = (
            int(entry["used"])
            if entry["used"] is not None
            else occurrences
        )
        # `budget_total` is the router's max-attempts when present, else
        # `used + remaining` (a faithful total for the derived row).
        total = (
            int(entry["total"])
            if entry["total"] is not None
            else used + remaining
        )
        used = min(used, total)
        terminal_reason = (
            ""
            if has_typed
            else "no_typed_budget"
        )
        if entry["exhausted"]:
            terminal_reason = "budget_exhausted"
        summaries.append(
            RetryBudgetSummary(
                scope="route",
                group_idx=None,
                route=entry["route"],
                failure_signature_hash=entry["signature"] or None,
                budget_total=max(0, total),
                budget_used=max(0, used),
                budget_remaining=remaining,
                terminal_reason=terminal_reason,
            )
        )
    return summaries


class ExecutionControlStore:
    def __init__(
        self,
        pool: Any,
        *,
        public_dashboard_outbox: "PublicDashboardOutbox | None" = None,
    ) -> None:
        self._pool = pool
        # Doc 10 § "Dashboard Integration Points" Slice 10g-1 wiring: when
        # configured, every typed projection that advances the snapshot
        # version (i.e. every successful projection-link insert in
        # _complete_missing_projections) enqueues a BOUNDED, SUMMARY-only
        # control_plane.snapshot_changed public outbox row inside the same
        # ``conn`` transaction so the fail-closed property holds end-to-end
        # (a failed outbox enqueue rolls back the projection link insert).
        # When ``None`` the helper is a no-op — this is the documented
        # disabled-outbox path, not a silent degrade.
        self._public_dashboard_outbox = public_dashboard_outbox

    async def record(
        self,
        write: ExecutionJournalWrite,
        *,
        projection_payload: dict[str, Any] | None = None,
    ) -> ExecutionJournalResult:
        self._validate(write)
        async with self._connection() as conn:
            async with self._transaction(conn):
                existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
                if existing is None:
                    row, created = await self._insert_typed_row(conn, write)
                else:
                    row = self._row_from_record(existing)
                    created = False
                self._validate_row_digest(row, write)

                await self._complete_missing_projections(
                    conn,
                    row,
                    write.compatibility_projections,
                    projection_payload=projection_payload,
                )
                links = await self._fetch_projection_links(conn, row.id)
                return ExecutionJournalResult(row=row, projection_links=tuple(links), created=created)

    async def record_success(
        self,
        write: ExecutionJournalWrite,
    ) -> ExecutionJournalResult:
        if write.status != "succeeded":
            raise ValueError("record_success requires status='succeeded'")
        return await self.record(write)

    async def get_by_idempotency_key(
        self,
        feature_id: str,
        idempotency_key: str,
    ) -> ExecutionJournalResult | None:
        async with self._connection() as conn:
            record = await self._fetch_existing(conn, feature_id, idempotency_key)
            if record is None:
                return None
            row = self._row_from_record(record)
            links = await self._fetch_projection_links(conn, row.id)
            return ExecutionJournalResult(row=row, projection_links=tuple(links), created=False)

    async def get_pre_promotion_contract_revalidation_inputs(
        self,
        *,
        feature_id: str,
        attempt_id: int,
        task_id: str,
        contract_id: int | None = None,
    ) -> dict[str, Any] | None:
        async with self._connection() as conn:
            attempt = await self._fetch_dispatch_attempt_by_id(conn, int(attempt_id))
            if attempt.feature_id != feature_id or str(attempt.task_id or "") != str(task_id):
                return None
            failure_id = (
                attempt.payload.get("typed_failure_id")
                or attempt.payload.get("runtime_failure_id")
            )
            if failure_id is None:
                return None
            failure = await self._fetch_evidence_node_by_id(conn, int(failure_id))
            if failure is None or failure.kind != "runtime_failure_context":
                return None
            failure_payload = _json_dict(failure.payload)
            failure_details = _json_dict(failure_payload.get("details"))
            failure_message = str(
                failure.summary
                or failure_details.get("message")
                or failure_payload.get("message")
                or ""
            )
            if (
                failure_payload.get("terminal_reason") != "patch_capture_failed"
                or "Task contract validation failed before sandbox promotion"
                not in failure_message
            ):
                return None

            verdict_rows = await conn.fetch(
                """
                SELECT *
                FROM evidence_nodes
                WHERE feature_id = $1
                  AND kind = 'contract_verdict'
                  AND status = 'rejected'
                  AND group_idx = $2
                  AND metadata->>'capture_validated_before_promotion' = 'true'
                ORDER BY id DESC
                LIMIT 50
                """,
                feature_id,
                attempt.group_idx,
            )
            expected_prefix = (
                f"dag-contract-verdict:g{attempt.group_idx if attempt.group_idx is not None else '-'}:"
                f"{task_id}:"
            )
            for verdict_record in verdict_rows:
                verdict = self._evidence_node_from_record(verdict_record)
                verdict_payload = _json_dict(verdict.payload)
                verdict_metadata = _json_dict(verdict.metadata)
                if contract_id is not None:
                    try:
                        verdict_contract_id = int(
                            verdict.contract_id
                            or verdict_payload.get("contract_id")
                            or 0
                        )
                    except (TypeError, ValueError):
                        verdict_contract_id = 0
                    if verdict_contract_id != int(contract_id):
                        continue
                task_ids = [str(item) for item in _json_list(verdict_metadata.get("task_ids"))]
                verdict_key = verdict.artifact_key or verdict.name
                if task_id not in task_ids and not str(verdict_key).startswith(expected_prefix):
                    continue
                patch_summary_id = (
                    verdict_metadata.get("captured_patch_summary_id")
                    or verdict_payload.get("captured_patch_summary_id")
                    or verdict_payload.get("patch_summary_id")
                )
                try:
                    patch_summary_id_int = int(patch_summary_id)
                except (TypeError, ValueError):
                    continue
                if patch_summary_id_int <= 0:
                    continue
                patch_summary = await self._fetch_evidence_node_by_id(
                    conn,
                    patch_summary_id_int,
                )
                if patch_summary is None or patch_summary.kind != "sandbox_patch_summary":
                    continue
                return {
                    "dispatch_attempt": attempt,
                    "runtime_failure": failure,
                    "contract_verdict": verdict,
                    "patch_summary": patch_summary,
                }
            return None

    async def get_pending_durable_merge_patch_evidence(
        self,
        *,
        feature_id: str,
        dag_sha256: str,
        group_idx: int,
        task_id: str,
    ) -> dict[str, Any] | None:
        """Recover patch-summary ids from the latest succeeded dispatch attempt.

        This is intentionally narrow: resume may use it only to enqueue an
        already-captured sandbox patch into the durable merge queue when a stale
        ``dag-task:*`` projection has the pending-queue note but lacks
        machine-readable ``patch_summary_ids``.
        """

        async with self._connection() as conn:
            record = await conn.fetchrow(
                """
                SELECT *
                FROM execution_journal_rows
                WHERE feature_id = $1
                  AND entry_type = 'dispatch_attempt'
                  AND dag_sha256 = $2
                  AND group_idx = $3
                  AND task_id = $4
                  AND status = 'succeeded'
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                feature_id,
                dag_sha256,
                int(group_idx),
                task_id,
            )
            if record is None:
                return None
            attempt = self._row_from_record(record)
            outcome = _json_dict(attempt.payload.get("dispatch_outcome"))
            if outcome.get("status") != "succeeded":
                return None
            patch_summary_ids = sorted(set(
                _typed_int_list(outcome.get("patch_summary_ids"), cap=100)
            ))
            if not patch_summary_ids:
                return None
            for patch_summary_id in patch_summary_ids:
                patch_summary = await self._fetch_evidence_node_by_id(
                    conn,
                    patch_summary_id,
                )
                if (
                    patch_summary is None
                    or patch_summary.kind != "sandbox_patch_summary"
                    or patch_summary.feature_id != feature_id
                ):
                    return None
            return {
                "dispatch_attempt_id": attempt.id,
                "idempotency_key": outcome.get("idempotency_key") or attempt.idempotency_key,
                "patch_summary_ids": patch_summary_ids,
                "structured_result_evidence_id": _typed_int(
                    outcome.get("structured_result_evidence_id")
                ),
            }

    async def get_latest_terminal_dispatch_outcome(
        self,
        *,
        feature_id: str,
        dag_sha256: str,
        group_idx: int,
        task_id: str,
    ) -> dict[str, Any] | None:
        """Return the LATEST terminal dispatch attempt for a task (any terminal
        status), so resume can derive infra-retry state from the most recent
        outcome instead of replaying every historical failure.

        A task whose newest terminal attempt ``succeeded`` has its earlier
        infra-failures SUPERSEDED — they must not be replayed or counted against
        the durable retry budget (which would burn it instantly into a spurious
        RootCauseAnalysis). Returns ``{status, attempt, idempotency_key,
        dispatch_attempt_id}`` for the newest terminal row, or ``None`` when the
        task has no terminal attempt yet. ``attempt`` is the per-task loop retry
        index (``payload['retry']``); a caller can re-dispatch exactly that index
        to replay the persisted result (the durable idempotency key is a hash, so
        the index — not the key — is the stable handle back into the loop). The
        ``updated_at DESC, id DESC`` tie-break matches
        ``get_pending_durable_merge_patch_evidence`` so "latest" is deterministic;
        a later FAILURE correctly outranks an earlier success.
        """

        async with self._connection() as conn:
            record = await conn.fetchrow(
                """
                SELECT *
                FROM execution_journal_rows
                WHERE feature_id = $1
                  AND entry_type = 'dispatch_attempt'
                  AND dag_sha256 = $2
                  AND group_idx = $3
                  AND task_id = $4
                  AND status = ANY($5::text[])
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                feature_id,
                dag_sha256,
                int(group_idx),
                task_id,
                list(DISPATCHER_TERMINAL_STATUSES),
            )
            if record is None:
                return None
            attempt = self._row_from_record(record)
            outcome = _json_dict(attempt.payload.get("dispatch_outcome"))
            return {
                "status": attempt.status or outcome.get("status"),
                "attempt": attempt.payload.get("retry"),
                "idempotency_key": (
                    outcome.get("idempotency_key") or attempt.idempotency_key
                ),
                "dispatch_attempt_id": attempt.id,
            }

    async def get_runtime_failure_context(
        self,
        *,
        feature_id: str,
        failure_id: int,
    ) -> dict[str, Any] | None:
        """Return bounded runtime-failure details for same-feature replay checks."""

        try:
            failure_id_int = int(failure_id)
        except (TypeError, ValueError):
            return None
        if failure_id_int <= 0:
            return None
        async with self._connection() as conn:
            record = await conn.fetchrow(
                """
                SELECT *
                FROM evidence_nodes
                WHERE feature_id = $1
                  AND id = $2
                  AND kind = 'runtime_failure_context'
                LIMIT 1
                """,
                feature_id,
                failure_id_int,
            )
            if record is None:
                return None
            failure = self._evidence_node_from_record(record)
            failure_payload = _json_dict(failure.payload)
            evidence_ids = _typed_int_list(
                failure_payload.get("evidence_ids"),
                cap=50,
            )
            patch_summaries: list[dict[str, Any]] = []
            for evidence_id in evidence_ids:
                patch = await self._fetch_evidence_node_by_id(conn, int(evidence_id))
                if (
                    patch is None
                    or patch.kind != "sandbox_patch_summary"
                    or patch.feature_id != feature_id
                ):
                    continue
                patch_payload = _json_dict(patch.payload)
                patch_summaries.append({
                    "id": patch.id,
                    "attempt_id": patch.attempt_id,
                    "sandbox_id": str(patch_payload.get("sandbox_id") or ""),
                    "diff_sha256": str(patch_payload.get("diff_sha256") or ""),
                    "changed_paths": [
                        str(item)
                        for item in _json_list(patch_payload.get("changed_paths"))[:50]
                    ],
                })
        payload = _json_dict(failure.payload)
        details = _json_dict(payload.get("details"))
        bounded_details = {
            str(key): _bounded_runtime_failure_context_text(value)
            for key, value in list(details.items())[:20]
        }
        return {
            "id": failure.id,
            "feature_id": failure.feature_id,
            "attempt_id": failure.attempt_id,
            "group_idx": failure.group_idx,
            "summary": _bounded_runtime_failure_context_text(failure.summary),
            "failure_class": _bounded_runtime_failure_context_text(
                payload.get("failure_class")
            ),
            "failure_type": _bounded_runtime_failure_context_text(
                payload.get("failure_type")
            ),
            "terminal_reason": _bounded_runtime_failure_context_text(
                payload.get("terminal_reason")
            ),
            "evidence_ids": evidence_ids,
            "sandbox_patch_summaries": patch_summaries,
            "message": _bounded_runtime_failure_context_text(payload.get("message")),
            "details": bounded_details,
        }

    async def start_dispatch_attempt(
        self,
        request: DispatchAttemptRequest,
    ) -> DispatchAttemptResult:
        retry_identity = _json_dict(request.retry_identity)
        if "retry" in retry_identity and int(retry_identity["retry"]) != int(request.retry):
            raise IdempotencyConflict(
                "dispatch retry identity does not match request retry"
            )
        idempotency_key = request.stable_idempotency_key
        payload = _dispatch_attempt_payload(request)
        write = ExecutionJournalWrite(
            feature_id=request.feature_id,
            idempotency_key=idempotency_key,
            entry_type="dispatch_attempt",
            status="started",
            payload=payload,
            actor=request.actor_role,
            dag_sha256=request.dag_sha256,
            group_idx=request.group_idx,
            task_id=request.task_id,
            request_digest=request.digest,
            dispatcher_state="attempt_started",
            runtime=request.runtime,
        )
        result = await self.record(write)
        return DispatchAttemptResult(attempt=result.row, created=result.created)

    async def record_prompt_context(
        self,
        evidence: PromptContextEvidence,
    ) -> EvidenceNodeResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, evidence.attempt_id)
                self._ensure_dispatch_attempt_mutable(row)
                fields = _prompt_context_fields(evidence, row)
                evidence_row, evidence_created = await self._insert_or_reuse_evidence_node(
                    conn,
                    fields,
                    execution_row=row,
                    kind="context_package",
                )
                context_package_identity = _prompt_context_identity_payload(evidence)
                updated = await self._update_dispatch_attempt_row(
                    conn,
                    row,
                    status="started",
                    dispatcher_state="context_prepared",
                    payload_patch={
                        "context_sha256": evidence.context_sha256,
                        "dispatcher_state": "context_prepared",
                        "prompt_context_evidence_id": evidence_row.id,
                        "prompt_ref": evidence.prompt_ref,
                        "prompt_sha256": evidence.prompt_sha256,
                        **context_package_identity,
                    },
                )
                return EvidenceNodeResult(
                    evidence=evidence_row,
                    execution=ExecutionJournalResult(
                        row=updated,
                        projection_links=tuple(await self._fetch_projection_links(conn, updated.id)),
                        created=False,
                    ),
                    created=evidence_created,
                )

    async def record_runtime_invocation(
        self,
        evidence: RuntimeInvocationEvidence,
    ) -> EvidenceNodeResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, evidence.attempt_id)
                self._ensure_dispatch_attempt_mutable(row)
                fields = _runtime_invocation_fields(evidence, row)
                evidence_row, evidence_created = await self._insert_or_reuse_evidence_node(
                    conn,
                    fields,
                    execution_row=row,
                    kind="runtime_invocation",
                )
                next_state = _runtime_invocation_dispatcher_state(evidence)
                prior_ids = _json_list(row.payload.get("runtime_invocation_evidence_ids"))
                updated = await self._update_dispatch_attempt_row(
                    conn,
                    row,
                    status="started",
                    dispatcher_state=next_state,
                    payload_patch={
                        "dispatcher_state": next_state,
                        "last_runtime_invocation_evidence_id": evidence_row.id,
                        "runtime_invocation_evidence_ids": _append_unique(
                            prior_ids,
                            evidence_row.id,
                        ),
                        "runtime_terminal_reason": evidence.terminal_reason,
                    },
                )
                return EvidenceNodeResult(
                    evidence=evidence_row,
                    execution=ExecutionJournalResult(
                        row=updated,
                        projection_links=tuple(await self._fetch_projection_links(conn, updated.id)),
                        created=False,
                    ),
                    created=evidence_created,
                )

    async def record_raw_output(
        self,
        evidence: RawOutputEvidence,
    ) -> EvidenceNodeResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, evidence.attempt_id)
                self._ensure_dispatch_attempt_mutable(row)
                fields = _raw_output_fields(evidence, row)
                evidence_row, evidence_created = await self._insert_or_reuse_evidence_node(
                    conn,
                    fields,
                    execution_row=row,
                    kind="raw_output",
                )
                raw_ids = _json_list(row.payload.get("raw_output_evidence_ids"))
                updated = await self._update_dispatch_attempt_row(
                    conn,
                    row,
                    status="started",
                    dispatcher_state=row.dispatcher_state,
                    payload_patch={
                        "last_raw_output_evidence_id": evidence_row.id,
                        "raw_output_evidence_ids": _append_unique(
                            raw_ids,
                            evidence_row.id,
                        ),
                    },
                )
                return EvidenceNodeResult(
                    evidence=evidence_row,
                    execution=ExecutionJournalResult(
                        row=updated,
                        projection_links=tuple(await self._fetch_projection_links(conn, updated.id)),
                        created=False,
                    ),
                    created=evidence_created,
                )

    async def record_structured_output(
        self,
        evidence: StructuredOutputEvidence,
    ) -> EvidenceNodeResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, evidence.attempt_id)
                self._ensure_dispatch_attempt_mutable(row)
                fields = _structured_output_fields(evidence, row)
                evidence_row, evidence_created = await self._insert_or_reuse_evidence_node(
                    conn,
                    fields,
                    execution_row=row,
                    kind="structured_result",
                )
                payload_patch = {
                    "dispatcher_state": "evidence_recording",
                    "last_structured_result_evidence_id": evidence_row.id,
                }
                if evidence.valid:
                    payload_patch["structured_result_evidence_id"] = evidence_row.id
                else:
                    payload_patch["invalid_structured_result_evidence_id"] = evidence_row.id
                updated = await self._update_dispatch_attempt_row(
                    conn,
                    row,
                    status="started",
                    dispatcher_state="evidence_recording",
                    payload_patch=payload_patch,
                )
                return EvidenceNodeResult(
                    evidence=evidence_row,
                    execution=ExecutionJournalResult(
                        row=updated,
                        projection_links=tuple(await self._fetch_projection_links(conn, updated.id)),
                        created=False,
                    ),
                    created=evidence_created,
                )

    async def record_runtime_failure(
        self,
        failure: RuntimeFailureEvidence,
    ) -> RuntimeFailureResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, failure.attempt_id)
                observational_idempotency_conflict = (
                    failure.failure_class == "dispatcher_internal"
                    and failure.failure_type == "idempotency_conflict"
                )
                if not observational_idempotency_conflict:
                    self._ensure_dispatch_attempt_mutable(row)
                fields = _runtime_failure_fields(failure, row)
                evidence_row, evidence_created = await self._insert_or_reuse_evidence_node(
                    conn,
                    fields,
                    execution_row=row,
                    kind="runtime_failure_context",
                )
                signature_hash = str(fields["payload"]["signature_hash"])
                if observational_idempotency_conflict:
                    updated = row
                else:
                    failure_payload = dict(fields["payload"])
                    payload_patch = {
                        "dispatcher_state": "evidence_recording",
                        "runtime_failure_id": evidence_row.id,
                        "runtime_failure_signature_hash": signature_hash,
                        "runtime_terminal_reason": failure.terminal_reason,
                        "typed_failure_id": evidence_row.id,
                    }
                    for key in ("route", "retry_budget", "route_decision"):
                        if failure_payload.get(key) is not None:
                            payload_patch[key] = failure_payload[key]
                    for key in DISPATCHER_RECOVERY_EVIDENCE_PAYLOAD_KEYS:
                        if failure_payload.get(key) is not None:
                            payload_patch[key] = failure_payload[key]
                    updated = await self._update_dispatch_attempt_row(
                        conn,
                        row,
                        status="started",
                        dispatcher_state="evidence_recording",
                        payload_patch=payload_patch,
                    )
                    _ = updated
                return RuntimeFailureResult(
                    evidence=evidence_row,
                    failure_id=evidence_row.id,
                    typed_failure_id=evidence_row.id,
                    signature_hash=signature_hash,
                    created=evidence_created,
                )

    async def record_verification_graph_node(
        self,
        evidence: VerificationGraphNodeEvidence | dict[str, Any],
    ) -> EvidenceNodeResult:
        node = _verification_graph_node_evidence(evidence)
        if node.kind not in VERIFICATION_GRAPH_NODE_KINDS:
            raise ExecutionControlError(f"unsupported verification graph node kind: {node.kind}")
        fields = _verification_graph_node_fields(node)
        async with self._connection() as conn:
            async with self._transaction(conn):
                return await self._record_verification_graph_node_in_transaction(
                    conn,
                    fields=fields,
                    node=node,
                )

    async def list_verification_graph_nodes(
        self,
        feature_id: str,
        *,
        dag_sha256: str = "",
        group_idx: int | None = None,
        stage: str = "",
        after_id: int = 0,
        limit: int = VERIFICATION_GRAPH_NODE_LIST_LIMIT,
    ) -> list[EvidenceNode]:
        clauses = ["feature_id = $1", "kind = ANY($2::text[])"]
        args: list[Any] = [feature_id, sorted(VERIFICATION_GRAPH_NODE_KINDS)]
        if dag_sha256:
            args.append(dag_sha256)
            clauses.append(f"payload->>'dag_sha256' = ${len(args)}")
        if group_idx is not None:
            args.append(group_idx)
            clauses.append(f"group_idx = ${len(args)}")
        if stage:
            args.append(stage)
            clauses.append(f"stage = ${len(args)}")
        args.append(max(0, int(after_id or 0)))
        clauses.append(f"id > ${len(args)}")
        args.append(min(VERIFICATION_GRAPH_NODE_LIST_LIMIT, max(1, int(limit or 1))))
        limit_placeholder = len(args)
        async with self._connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    id, feature_id, idempotency_key, execution_journal_row_id,
                    attempt_id, contract_id, snapshot_id, group_idx, stage,
                    kind, name, status, deterministic, source_ref, artifact_id,
                    artifact_key, event_id, input_refs, output_refs, failure_id,
                    verdict_id, content_hash, summary, metadata, '{{}}'::jsonb AS payload,
                    started_at, finished_at, created_at, updated_at
                FROM evidence_nodes
                WHERE {' AND '.join(clauses)}
                ORDER BY id ASC
                LIMIT ${limit_placeholder}
                """,
                *args,
            )
        return [self._evidence_node_from_record(row) for row in rows]

    async def record_verification_graph_projection(
        self,
        projection: VerificationGraphProjection | dict[str, Any],
    ) -> ExecutionJournalResult:
        graph_projection = _verification_graph_projection(projection)
        graph_payload = graph_projection.graph_payload
        self._validate_verification_graph_projection(graph_projection)
        async with self._connection() as conn:
            async with self._transaction(conn):
                aggregate_node = await self._record_verification_graph_payload_nodes(
                    conn,
                    graph_projection,
                )
                projection_body = (
                    graph_projection.projection_body
                    if graph_projection.projection_body is not None
                    else graph_payload.get("merged_verdict")
                    or graph_payload
                )
                compat = CompatibilityProjection(
                    key=graph_projection.projection_key,
                    value=projection_body,
                    idempotency_key=graph_projection.stable_idempotency_key,
                )
                write = ExecutionJournalWrite(
                    feature_id=graph_projection.feature_id,
                    idempotency_key=graph_projection.stable_idempotency_key,
                    entry_type="verify_result",
                    status="succeeded",
                    payload={
                        "aggregate_evidence_node_id": aggregate_node.id,
                        "approved": graph_projection.approved,
                        "graph_payload_digest": stable_digest(graph_payload),
                        "projection_key": graph_projection.projection_key,
                        "proof_digest": graph_projection.proof_digest,
                        "source_kind": "aggregate_verdict",
                        "stage": graph_projection.stage,
                    },
                    actor="verification_graph",
                    dag_sha256=graph_projection.dag_sha256,
                    group_idx=graph_projection.group_idx,
                    requires_legacy_visibility=True,
                    compatibility_projections=(),
                    request_digest=stable_digest({
                        "feature_id": graph_projection.feature_id,
                        "graph_payload_digest": stable_digest(graph_payload),
                        "idempotency_key": graph_projection.stable_idempotency_key,
                        "projection_key": graph_projection.projection_key,
                        "proof_digest": graph_projection.proof_digest,
                    }),
                )
                existing = await self._fetch_existing(
                    conn,
                    write.feature_id,
                    write.idempotency_key,
                )
                if existing is None:
                    row, journal_created = await self._insert_typed_row(conn, write)
                else:
                    row = self._row_from_record(existing)
                    journal_created = False
                self._validate_row_digest(row, write)
                projection_sha256 = _projection_value_sha256(compat)
                graph_record = await self._insert_or_reuse_evidence_graph(
                    conn,
                    projection=graph_projection,
                    row=row,
                    aggregate_node=aggregate_node,
                    projection_sha256=projection_sha256,
                )
                await self._persist_verification_graph_edges(
                    conn,
                    projection=graph_projection,
                    graph_id=int(_record_get(graph_record, "id")),
                )
                await self._complete_missing_projections(
                    conn,
                    row,
                    (compat,),
                    source_table="evidence_nodes",
                    source_id=aggregate_node.id,
                    projection_owner="verification_graph",
                    projection_kind="verify_result",
                    projection_payload={
                        "aggregate_evidence_node_id": aggregate_node.id,
                        "dag_sha256": graph_projection.dag_sha256,
                        "entry_type": "verify_result",
                        "evidence_graph_id": int(_record_get(graph_record, "id")),
                        "evidence_kind": "aggregate_verdict",
                        "graph_payload_digest": stable_digest(graph_payload),
                        "group_idx": graph_projection.group_idx,
                        "projection_key": graph_projection.projection_key,
                        "proof_digest": graph_projection.proof_digest,
                        "required_edge_ids": _verification_graph_required_edge_ids(
                            graph_projection.graph_payload
                        ),
                        "required_node_ids": _verification_graph_required_node_ids(
                            graph_projection.graph_payload
                        ),
                        "stage": graph_projection.stage,
                        "verifier_compatibility_links": (
                            _verification_graph_verifier_compatibility_links(
                                graph_projection.graph_payload
                            )
                        ),
                    },
                )
                links = await self._fetch_projection_links(conn, row.id)
                return ExecutionJournalResult(
                    row=row,
                    projection_links=tuple(links),
                    created=journal_created,
                )

    async def get_verified_verification_graph_projection(
        self,
        *,
        feature_id: str,
        projection_key: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
        proof_digest: str,
    ) -> dict[str, Any] | None:
        async with self._connection() as conn:
            graph = await self._fetch_verification_graph_projection(
                conn,
                feature_id=feature_id,
                projection_key=projection_key,
                dag_sha256=dag_sha256,
                group_idx=group_idx,
                stage=stage,
                proof_digest=proof_digest,
            )
            if graph is None:
                return None
            required_edge_ids = [
                str(item)
                for item in _decode_json(_record_get(graph, "required_edge_ids"), [])
            ]
            if len(required_edge_ids) > VERIFICATION_GRAPH_NODE_LIST_LIMIT:
                raise MissingRequiredProjection(
                    "verification graph reload requires a bounded edge set"
                )
            edges = await self._fetch_verification_graph_required_edges(
                conn,
                graph_id=int(_record_get(graph, "id")),
                limit=VERIFICATION_GRAPH_NODE_LIST_LIMIT,
            )
            found_edge_ids = {str(_record_get(edge, "graph_edge_id")) for edge in edges}
            expected_edge_ids = set(required_edge_ids)
            if found_edge_ids != expected_edge_ids:
                missing_edges = sorted(expected_edge_ids - found_edge_ids)
                extra_edges = sorted(found_edge_ids - expected_edge_ids)
                raise MissingRequiredProjection(
                    "verification graph reload required edge rows do not match "
                    f"typed graph proof; missing={missing_edges} extra={extra_edges}"
                )
            graph_payload = _json_dict(_record_get(graph, "payload"))
            if _verification_graph_payload_is_full(graph_payload):
                recorded_graph_digest = str(_record_get(graph, "graph_payload_digest") or "")
                if (
                    recorded_graph_digest
                    and stable_digest(graph_payload) != recorded_graph_digest
                ):
                    raise MissingRequiredProjection(
                        "verification graph reload graph payload digest mismatch"
                    )
            graph_node_status_by_id: dict[int, str] = {}
            for raw_node in _verification_graph_payload_nodes(graph_payload):
                node_id = _optional_int(raw_node.get("id"))
                if node_id is not None:
                    graph_node_status_by_id[node_id] = str(
                        raw_node.get("status") or ""
                    )
            required_node_ids = _verification_graph_durable_required_node_ids(graph_payload)
            if not required_node_ids:
                raise MissingRequiredProjection(
                    "verification graph reload is missing required node lineage metadata"
                )
            if len(required_node_ids) > VERIFICATION_GRAPH_NODE_LIST_LIMIT:
                raise MissingRequiredProjection(
                    "verification graph reload requires a bounded node set"
                )
            aggregate_node_id = _verification_graph_durable_aggregate_node_id(graph_payload)
            raw_node_id = _verification_graph_durable_raw_node_id(graph_payload)
            aggregate_metadata = _json_dict(graph_payload.get("aggregate"))
            raw_node_required = bool(proof_digest) or bool(
                aggregate_metadata.get("approved") or graph_payload.get("approved")
            )
            if raw_node_id is None and raw_node_required:
                raise MissingRequiredProjection(
                    "verification graph reload is missing raw verifier role metadata"
                )
            required_gate_ids = _verification_graph_durable_required_gate_ids(graph_payload)
            required_lens_ids = _verification_graph_durable_required_lens_ids(graph_payload)
            required_role_ids = sorted({
                *required_gate_ids,
                *([] if raw_node_id is None else [raw_node_id]),
                *required_lens_ids,
            })
            missing_role_ids = sorted(set(required_role_ids) - set(required_node_ids))
            if missing_role_ids:
                raise MissingRequiredProjection(
                    "verification graph reload role metadata is not bound to required nodes: "
                    f"{missing_role_ids}"
                )
            required_edge_paths: list[tuple[int, int]] = []
            required_node_kind_by_id: dict[int, str] = {}
            required_node_statuses: dict[int, str] = {}
            for edge in edges:
                if str(_record_get(edge, "kind") or "") != "requires" or not bool(
                    _record_get(edge, "required")
                ):
                    raise MissingRequiredProjection(
                        "verification graph reload found invalid required edge kind"
                    )
                from_evidence_id = _record_get(edge, "from_evidence_node_id")
                to_evidence_id = _record_get(edge, "to_evidence_node_id")
                if from_evidence_id is None or to_evidence_id is None:
                    raise MissingRequiredProjection(
                        "verification graph reload found required edge without typed node endpoints"
                    )
                from_node = await self._fetch_evidence_node_by_id(
                    conn,
                    int(from_evidence_id),
                )
                to_node = await self._fetch_evidence_node_by_id(
                    conn,
                    int(to_evidence_id),
                )
                if from_node is None or to_node is None:
                    raise MissingRequiredProjection(
                        "verification graph reload found missing typed edge endpoint nodes"
                    )
                from_graph_node_id = _optional_int(_record_get(edge, "from_graph_node_id"))
                to_graph_node_id = _optional_int(_record_get(edge, "to_graph_node_id"))
                if from_graph_node_id is None or to_graph_node_id is None:
                    raise MissingRequiredProjection(
                        "verification graph reload found required edge without graph node endpoints"
                    )
                if proof_digest:
                    if from_node.status != "approved" or to_node.status != "approved":
                        raise MissingRequiredProjection(
                            "verification graph reload found non-approved typed edge endpoint nodes"
                        )
                else:
                    for graph_node_id, typed_node in (
                        (from_graph_node_id, from_node),
                        (to_graph_node_id, to_node),
                    ):
                        expected_status = graph_node_status_by_id.get(graph_node_id)
                        if expected_status is None:
                            raise MissingRequiredProjection(
                                "verification graph reload found edge endpoint missing from graph payload"
                            )
                        if typed_node.status != expected_status:
                            raise MissingRequiredProjection(
                                "verification graph reload typed endpoint status does not match graph payload"
                            )
                if (
                    to_node.kind == "aggregate_verdict"
                    and to_node.id == int(_record_get(graph, "aggregate_evidence_node_id"))
                ):
                    if aggregate_node_id is None:
                        aggregate_node_id = to_graph_node_id
                elif to_node.kind not in VERIFICATION_GRAPH_NODE_KINDS:
                    raise MissingRequiredProjection(
                        "verification graph reload required edge does not target typed graph evidence"
                    )
                required_edge_paths.append((from_graph_node_id, to_graph_node_id))
                for graph_node_id, typed_node in (
                    (from_graph_node_id, from_node),
                    (to_graph_node_id, to_node),
                ):
                    if graph_node_id not in required_node_ids:
                        continue
                    existing_kind = required_node_kind_by_id.get(graph_node_id)
                    if existing_kind is not None and existing_kind != typed_node.kind:
                        raise MissingRequiredProjection(
                            "verification graph reload found conflicting typed node roles"
                        )
                    required_node_kind_by_id[graph_node_id] = typed_node.kind
                required_node_statuses[from_node.id] = from_node.status
                required_node_statuses[to_node.id] = to_node.status
            cyclic_path = _verification_graph_required_edge_cycle(
                required_edges=required_edge_paths
            )
            if cyclic_path:
                raise MissingRequiredProjection(
                    "verification graph reload has cyclic required edges: "
                    f"{cyclic_path}"
                )
            missing_reachability = _verification_graph_missing_required_reachability(
                required_node_ids=required_node_ids,
                aggregate_node_id=aggregate_node_id,
                required_edges=required_edge_paths,
            )
            if missing_reachability:
                raise MissingRequiredProjection(
                    "verification graph reload has missing required-node reachability: "
                    f"{missing_reachability}"
                )
            _validate_verification_graph_required_node_roles(
                node_kind_by_id=required_node_kind_by_id,
                raw_node_id=raw_node_id,
                required_gate_ids=required_gate_ids,
                required_lens_ids=required_lens_ids,
                require_raw_verifier=raw_node_required,
                context="verification graph reload",
            )
            _verification_graph_required_compatibility_links(
                graph_payload,
                required_verifier_node_ids=[
                    *([] if raw_node_id is None else [raw_node_id]),
                    *required_lens_ids,
                ],
                context="verification graph reload",
            )
            links = await self._fetch_projection_links(
                conn,
                int(_record_get(graph, "execution_journal_row_id")),
            )
            matching_links = [
                link for link in links
                if link.projection_key == projection_key
                and str(link.payload.get("proof_digest") or "") == proof_digest
            ]
            if not matching_links:
                raise MissingRequiredProjection(
                    "verification graph reload is missing projection link metadata"
                )
            return {
                "graph": _verification_graph_record_metadata(graph),
                "required_edges": [
                    _verification_graph_edge_record_metadata(edge)
                    for edge in edges
                ],
                "required_node_statuses": required_node_statuses,
                "projection_links": [
                    _projection_link_metadata(link)
                    for link in matching_links
                ],
            }

    async def get_latest_verified_verification_graph_projection(
        self,
        *,
        feature_id: str,
        projection_key: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
    ) -> dict[str, Any] | None:
        async with self._connection() as conn:
            graph = await self._fetch_latest_verification_graph_projection(
                conn,
                feature_id=feature_id,
                projection_key=projection_key,
                dag_sha256=dag_sha256,
                group_idx=group_idx,
                stage=stage,
            )
        if graph is None:
            return None
        proof_digest = str(_record_get(graph, "proof_digest") or "")
        return await self.get_verified_verification_graph_projection(
            feature_id=feature_id,
            projection_key=projection_key,
            dag_sha256=dag_sha256,
            group_idx=group_idx,
            stage=stage,
            proof_digest=proof_digest,
        )

    async def finish_dispatch_attempt(
        self,
        outcome: DispatchOutcome,
    ) -> DispatchOutcome:
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, outcome.attempt_id)
                existing_digest = row.payload.get("dispatch_outcome_digest")
                if row.status != "started":
                    existing_outcome = _json_dict(row.payload.get("dispatch_outcome"))
                    if existing_outcome:
                        existing = DispatchOutcome(**existing_outcome)
                        same_terminal_identity = (
                            outcome.status == existing.status
                            and outcome.structured_result_evidence_id
                            == existing.structured_result_evidence_id
                            and outcome.runtime_failure_id == existing.runtime_failure_id
                            and outcome.typed_failure_id == existing.typed_failure_id
                        )
                        if outcome.digest == existing.digest or same_terminal_identity:
                            return existing
                        raise IdempotencyConflict(
                            "dispatch attempt finish conflicts with existing terminal outcome"
                        )
                    if existing_digest == outcome.digest:
                        return outcome
                    raise IdempotencyConflict(
                        "dispatch attempt finish conflicts with existing terminal outcome"
                    )
                await self._validate_dispatch_outcome(conn, row, outcome)
                terminal_outcome = outcome
                if outcome.status == "succeeded":
                    if outcome.structured_result_evidence_id is None:
                        raise ExecutionControlError(
                            "successful dispatch outcome requires structured result evidence"
                        )
                    projection_result = await self._project_task_result_from_attempt_in_txn(
                        conn,
                        row,
                        structured_result_evidence_id=outcome.structured_result_evidence_id,
                        idempotency_key=None,
                    )
                    projection_ids = sorted({
                        link.artifact_id
                        for link in projection_result.projection_links
                        if link.projection_key.startswith("dag-task:")
                    })
                    terminal_outcome = replace(
                        outcome,
                        compatibility_artifact_ids=sorted({
                            *[int(item) for item in outcome.compatibility_artifact_ids],
                            *projection_ids,
                        }),
                    )
                await self._update_dispatch_attempt_row(
                    conn,
                    row,
                    status=terminal_outcome.status,
                    dispatcher_state=terminal_outcome.state,
                    payload_patch={
                        "dispatch_outcome": terminal_outcome.normalized_outcome(),
                        "dispatch_outcome_digest": terminal_outcome.digest,
                        "dispatcher_state": terminal_outcome.state,
                    },
                )
                return terminal_outcome

    async def recover_late_runtime_completion(
        self,
        *,
        attempt_id: int,
        runtime_invocation: RuntimeInvocationEvidence,
        structured_output: StructuredOutputEvidence,
        raw_output: RawOutputEvidence | None = None,
        patch_summary_ids: list[int] | None = None,
        recovery_metadata: dict[str, Any] | None = None,
    ) -> DispatchOutcome:
        """Convert a terminal timeout into success after strict late-result validation."""

        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, int(attempt_id))
                existing_outcome = _json_dict(row.payload.get("dispatch_outcome"))
                if row.status == "succeeded" and existing_outcome:
                    return DispatchOutcome(**existing_outcome)
                if row.status != "failed" or row.dispatcher_state != "failed":
                    raise ExecutionControlError(
                        "late runtime completion recovery requires a failed terminal dispatch attempt"
                    )
                if existing_outcome.get("status") != "failed" or existing_outcome.get(
                    "runtime_terminal_reason"
                ) not in {"timeout", "watchdog_stall"}:
                    raise ExecutionControlError(
                        "late runtime completion recovery requires a timeout terminal outcome"
                    )
                failure_id = row.payload.get("typed_failure_id") or row.payload.get(
                    "runtime_failure_id"
                )
                if failure_id is None:
                    raise ExecutionControlError(
                        "late runtime completion recovery requires timeout failure evidence"
                    )
                failure = await self._fetch_evidence_node_by_id(conn, int(failure_id))
                if failure is None or failure.kind != "runtime_failure_context":
                    raise ExecutionControlError("timeout failure evidence not found")
                failure_payload = _json_dict(failure.payload)
                if (
                    failure_payload.get("failure_class") != "runtime_timeout"
                    or failure_payload.get("failure_type") != "watchdog_timeout"
                ):
                    raise ExecutionControlError(
                        "late runtime completion recovery requires runtime_timeout/watchdog_timeout"
                    )
                if runtime_invocation.attempt_id != row.id or structured_output.attempt_id != row.id:
                    raise ExecutionControlError(
                        "late runtime completion evidence belongs to a different attempt"
                    )
                if str(runtime_invocation.runtime or "") != str(row.runtime or ""):
                    raise ExecutionControlError(
                        "late runtime completion runtime does not match dispatch attempt"
                    )

                resolved_patch_ids = sorted(set(
                    int(item)
                    for item in (
                        patch_summary_ids
                        if patch_summary_ids is not None
                        else _json_list(failure_payload.get("evidence_ids"))
                    )
                ))
                if not resolved_patch_ids:
                    raise ExecutionControlError(
                        "late runtime completion recovery requires captured patch evidence"
                    )
                for patch_id in resolved_patch_ids:
                    patch = await self._fetch_evidence_node_by_id(conn, int(patch_id))
                    if (
                        patch is None
                        or patch.kind != "sandbox_patch_summary"
                        or patch.feature_id != row.feature_id
                        or (
                            patch.attempt_id is not None
                            and int(patch.attempt_id) != int(row.id)
                        )
                    ):
                        raise ExecutionControlError(
                            "late runtime completion patch evidence does not match attempt"
                        )

                invocation_fields = _runtime_invocation_fields(runtime_invocation, row)
                invocation_evidence, _ = await self._insert_or_reuse_evidence_node(
                    conn,
                    invocation_fields,
                    execution_row=row,
                    kind="runtime_invocation",
                )

                raw_evidence_id: int | None = None
                if raw_output is not None:
                    if raw_output.attempt_id != row.id:
                        raise ExecutionControlError(
                            "late raw output evidence belongs to a different attempt"
                        )
                    raw_fields = _raw_output_fields(raw_output, row)
                    raw_evidence, _ = await self._insert_or_reuse_evidence_node(
                        conn,
                        raw_fields,
                        execution_row=row,
                        kind="raw_output",
                    )
                    raw_evidence_id = raw_evidence.id
                    if structured_output.raw_text_ref is None:
                        structured_output = replace(
                            structured_output,
                            raw_text_ref=raw_evidence_id,
                        )

                structured_fields = _structured_output_fields(structured_output, row)
                structured_evidence, _ = await self._insert_or_reuse_evidence_node(
                    conn,
                    structured_fields,
                    execution_row=row,
                    kind="structured_result",
                )

                provisional_payload = dict(row.payload)
                provisional_payload.update({
                    "dispatcher_state": "evidence_recording",
                    "runtime_terminal_reason": "completed",
                    "last_runtime_invocation_evidence_id": invocation_evidence.id,
                    "structured_result_evidence_id": structured_evidence.id,
                })
                provisional_row = replace(
                    row,
                    status="succeeded",
                    dispatcher_state="succeeded",
                    payload=provisional_payload,
                )
                outcome = DispatchOutcome(
                    attempt_id=row.id,
                    state="succeeded",
                    status="succeeded",
                    runtime_terminal_reason="completed",
                    structured_result_evidence_id=structured_evidence.id,
                    raw_text_ref=raw_evidence_id,
                    patch_summary_ids=resolved_patch_ids,
                    compatibility_artifact_ids=[],
                    runtime_failure_id=None,
                    typed_failure_id=None,
                    idempotency_key=row.idempotency_key,
                    metadata={
                        "late_runtime_completion_recovery": True,
                        **_json_dict(recovery_metadata),
                    },
                )
                await self._validate_dispatch_outcome(conn, provisional_row, outcome)
                projection_result = await self._project_task_result_from_attempt_in_txn(
                    conn,
                    provisional_row,
                    structured_result_evidence_id=structured_evidence.id,
                    idempotency_key=None,
                )
                projection_ids = sorted({
                    link.artifact_id
                    for link in projection_result.projection_links
                    if link.projection_key.startswith("dag-task:")
                })
                terminal_outcome = replace(
                    outcome,
                    compatibility_artifact_ids=sorted(projection_ids),
                )

                payload = dict(row.payload)
                payload.pop("runtime_failure_id", None)
                payload.pop("typed_failure_id", None)
                payload.pop("runtime_failure_signature_hash", None)
                payload.update({
                    "dispatcher_state": "succeeded",
                    "runtime_terminal_reason": "completed",
                    "last_runtime_invocation_evidence_id": invocation_evidence.id,
                    "runtime_invocation_evidence_ids": _append_unique(
                        _json_list(row.payload.get("runtime_invocation_evidence_ids")),
                        invocation_evidence.id,
                    ),
                    "structured_result_evidence_id": structured_evidence.id,
                    "last_structured_result_evidence_id": structured_evidence.id,
                    "patch_summary_ids": resolved_patch_ids,
                    "compatibility_artifact_ids": list(
                        terminal_outcome.compatibility_artifact_ids
                    ),
                    "dispatch_outcome": terminal_outcome.normalized_outcome(),
                    "dispatch_outcome_digest": terminal_outcome.digest,
                    "late_runtime_completion_recovery": {
                        "recovered_from_runtime_failure_id": row.payload.get(
                            "runtime_failure_id"
                        ),
                        "recovered_from_typed_failure_id": row.payload.get("typed_failure_id"),
                        "runtime_invocation_evidence_id": invocation_evidence.id,
                        "raw_output_evidence_id": raw_evidence_id,
                        "structured_result_evidence_id": structured_evidence.id,
                        "patch_summary_ids": resolved_patch_ids,
                        **_json_dict(recovery_metadata),
                    },
                })
                if raw_evidence_id is not None:
                    payload.update({
                        "last_raw_output_evidence_id": raw_evidence_id,
                        "raw_output_evidence_ids": _append_unique(
                            _json_list(row.payload.get("raw_output_evidence_ids")),
                            raw_evidence_id,
                        ),
                    })
                updated = await conn.fetchrow(
                    """
                    UPDATE execution_journal_rows
                    SET
                        status = $1,
                        dispatcher_state = $2,
                        payload = $3::jsonb,
                        updated_at = NOW()
                    WHERE id = $4
                      AND entry_type = 'dispatch_attempt'
                      AND status = 'failed'
                      AND dispatcher_state = 'failed'
                    RETURNING *
                    """,
                    terminal_outcome.status,
                    terminal_outcome.state,
                    stable_json(payload),
                    row.id,
                )
                if updated is None:
                    raise ExecutionControlError(
                        "dispatch attempt changed before late completion recovery"
                    )
                return terminal_outcome

    async def project_task_result_from_attempt(
        self,
        projection: TaskResultProjectionFromAttempt | int,
        *,
        structured_result_evidence_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionJournalResult:
        if isinstance(projection, TaskResultProjectionFromAttempt):
            attempt_id = projection.attempt_id
            evidence_id = projection.structured_result_evidence_id
            projection_idempotency = projection.idempotency_key
        else:
            attempt_id = int(projection)
            evidence_id = structured_result_evidence_id
            projection_idempotency = idempotency_key or ""
        async with self._connection() as conn:
            async with self._transaction(conn):
                row = await self._fetch_dispatch_attempt_by_id(conn, attempt_id)
                return await self._project_task_result_from_attempt_in_txn(
                    conn,
                    row,
                    structured_result_evidence_id=evidence_id,
                    idempotency_key=projection_idempotency,
                )

    async def _project_task_result_from_attempt_in_txn(
        self,
        conn: Any,
        row: ExecutionJournalRow,
        *,
        structured_result_evidence_id: int | None,
        idempotency_key: str | None = None,
    ) -> ExecutionJournalResult:
        if row.status not in {"started", "succeeded"}:
            raise ExecutionControlError(
                "cannot project task result from non-succeeded dispatch attempt"
            )
        if row.status == "started" and row.dispatcher_state != "evidence_recording":
            raise ExecutionControlError(
                "cannot project task result before dispatcher evidence recording"
            )
        evidence_id = structured_result_evidence_id
        if evidence_id is None:
            evidence_id = row.payload.get("structured_result_evidence_id")
        if evidence_id is None:
            raise ExecutionControlError(
                "successful dispatch attempt lacks structured-result evidence"
            )
        evidence = await self._fetch_evidence_node_by_id(conn, int(evidence_id))
        if evidence is None:
            raise ExecutionControlError("structured-result evidence not found")
        self._validate_structured_result_for_projection(row, evidence)
        body = _structured_result_projection_body(evidence)
        task_id = str(row.task_id or evidence.payload.get("task_id") or "")
        if not task_id:
            raise ExecutionControlError("dispatch task projection requires task_id")
        body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        projection_key = f"dag-task:{task_id}"
        compat = CompatibilityProjection(
            key=projection_key,
            value=body,
            idempotency_key=(
                idempotency_key
                or (
                    f"idem:dispatch-task-projection:{row.feature_id}:"
                    f"{row.id}:{evidence.id}:{body_sha}"
                )
            ),
        )
        await self._complete_missing_projections(
            conn,
            row,
            (compat,),
            source_table="evidence_nodes",
            source_id=evidence.id,
            projection_owner="dispatcher",
            projection_kind="task_result",
            projection_payload={
                "attempt_id": row.id,
                "entry_type": "dispatch_attempt",
                "evidence_kind": "structured_result",
                "evidence_node_id": evidence.id,
                "projection_authority": "dispatcher_attempt",
            },
        )
        links = await self._fetch_projection_links(conn, row.id)
        artifact_ids = sorted({
            link.artifact_id
            for link in links
            if link.projection_key == projection_key
        })
        updated = row
        if row.status == "started":
            updated = await self._update_dispatch_attempt_row(
                conn,
                row,
                status=row.status,
                dispatcher_state=row.dispatcher_state,
                payload_patch={
                    "compatibility_artifact_ids": artifact_ids,
                    "task_result_projection_key": projection_key,
                },
            )
        links = await self._fetch_projection_links(conn, updated.id)
        return ExecutionJournalResult(
            row=updated,
            projection_links=tuple(links),
            created=False,
        )

    async def put_task_contract(
        self,
        contract: TaskDeliverableContract,
    ) -> TaskContractResult:
        fields = _task_contract_fields(contract)
        projection_key = f"dag-task-contract:{fields['task_id']}"
        idempotency_key = str(fields["idempotency_key"])
        request_digest = stable_digest({
            "entry_type": "task_contract",
            "feature_id": fields["feature_id"],
            "idempotency_key": idempotency_key,
            "contract_digest": fields["contract_digest"],
            "normalized_contract_json": fields["normalized_contract_json"],
            "projection_key": projection_key,
        })
        write = ExecutionJournalWrite(
            feature_id=str(fields["feature_id"]),
            idempotency_key=idempotency_key,
            entry_type="task_contract",
            status="succeeded",
            payload={
                "contract_digest": fields["contract_digest"],
                "projection_key": projection_key,
                "repo_id": fields["repo_id"],
                "task_id": fields["task_id"],
            },
            actor="contract_service",
            dag_sha256=str(fields["dag_sha256"]),
            group_idx=int(fields["group_idx"]),
            task_id=str(fields["task_id"]),
            requires_legacy_visibility=True,
            request_digest=request_digest,
        )
        async with self._connection() as conn:
            async with self._transaction(conn):
                existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
                if existing is None:
                    row, journal_created = await self._insert_typed_row(conn, write)
                else:
                    row = self._row_from_record(existing)
                    journal_created = False
                self._validate_row_digest(row, write)
                contract_row, contract_created = await self._insert_or_reuse_task_contract(
                    conn,
                    fields,
                    execution_row=row,
                )
                projection = CompatibilityProjection(
                    key=projection_key,
                    value=_task_contract_projection_value(contract_row),
                    idempotency_key=f"{idempotency_key}:projection",
                )
                await self._complete_missing_projections(
                    conn,
                    row,
                    (projection,),
                    source_table="task_deliverable_contracts",
                    source_id=contract_row.id,
                    projection_owner="contract_service",
                    projection_kind="task_contract",
                    projection_payload={
                        "entry_type": "task_contract",
                        "task_contract_id": contract_row.id,
                    },
                )
                links = await self._fetch_projection_links(conn, row.id)
                execution = ExecutionJournalResult(
                    row=row,
                    projection_links=tuple(links),
                    created=journal_created,
                )
                return TaskContractResult(
                    contract=contract_row,
                    execution=execution,
                    created=contract_created,
                )

    async def record_patch_summary(
        self,
        summary: PatchSummary,
    ) -> EvidenceNodeResult:
        fields = _patch_summary_fields(summary)
        return await self._record_evidence_with_projection(
            fields=fields,
            entry_type="sandbox_patch_summary",
            kind="sandbox_patch_summary",
            actor="sandbox_runner",
            projection_owner="sandbox_runner",
            projection_kind="sandbox_patch",
            projection_key=str(fields["projection_key"]),
            projection_value_factory=_patch_summary_projection_value,
        )

    async def record_contract_verdict(
        self,
        verdict: ContractVerdict,
    ) -> EvidenceNodeResult:
        fields = _contract_verdict_fields(verdict)
        async with self._connection() as conn:
            async with self._transaction(conn):
                contract = None
                patch_summary = None
                if (
                    not fields["feature_id"]
                    or fields["group_idx"] is None
                    or not fields["task_id"]
                ):
                    contract = await self._fetch_task_contract_by_id(
                        conn,
                        int(fields["contract_id"]),
                    )
                    if contract is not None:
                        fields["feature_id"] = fields["feature_id"] or contract.feature_id
                        fields["dag_sha256"] = fields["dag_sha256"] or contract.dag_sha256
                        fields["group_idx"] = (
                            fields["group_idx"]
                            if fields["group_idx"] is not None
                            else contract.group_idx
                        )
                        fields["task_id"] = fields["task_id"] or contract.task_id
                if not fields["sandbox_id"]:
                    patch_summary = await self._fetch_evidence_node_by_id(
                        conn,
                        int(fields["patch_summary_id"]),
                    )
                    if patch_summary is not None:
                        fields["sandbox_id"] = str(
                            patch_summary.payload.get("sandbox_id") or ""
                        )
                fields["projection_key"] = (
                    f"dag-contract-verdict:g{fields['group_idx']}:"
                    f"{fields['task_id']}:{fields['sandbox_id']}"
                )
                return await self._record_evidence_with_projection_in_transaction(
                    conn,
                    fields=fields,
                    entry_type="contract_verdict",
                    kind="contract_verdict",
                    actor="contract_service",
                    projection_owner="contract_service",
                    projection_kind="contract_verdict",
                    projection_key=str(fields["projection_key"]),
                    projection_value_factory=_contract_verdict_projection_value,
                )

    async def project_task_result(self, projection: TaskResultProjection) -> ExecutionJournalResult:
        return await self._project(projection, entry_type="task_result")

    async def project_verify_result(self, projection: VerifyProjection) -> ExecutionJournalResult:
        return await self._project(projection, entry_type="verify_result")

    async def project_commit_failure(
        self,
        projection: CommitFailureProjection,
    ) -> ExecutionJournalResult:
        return await self._project(projection, entry_type="commit_failure")

    async def project_group_checkpoint(
        self,
        projection: GroupCheckpointProjection,
    ) -> ExecutionJournalResult:
        return await self._project_group_checkpoint(projection)

    async def project_regroup_overlay(self, projection: RegroupProjection) -> ExecutionJournalResult:
        return await self._project(projection, entry_type="regroup_overlay")

    async def project_regroup_active(
        self,
        projection: RegroupActiveProjection,
    ) -> ExecutionJournalResult:
        return await self._project(projection, entry_type="regroup_active")

    async def list_legacy_resume_artifacts(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...] | list[str],
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            preview_chars = LEGACY_RESUME_ARTIFACT_VALUE_PREVIEW_CHARS
            args: list[Any] = [
                feature_id,
                max(0, int(after_id or 0)),
                min(LEGACY_RESUME_ARTIFACT_ROW_LIMIT, max(1, int(limit or 500))),
                preview_chars,
            ]
            prefix_clause = ""
            if prefixes:
                prefix_clause = " AND (" + " OR ".join(
                    f"key LIKE ${idx + 5}" for idx, _prefix in enumerate(prefixes)
                ) + ")"
                args.extend(f"{prefix}%" for prefix in prefixes)
            rows = await conn.fetch(
                f"""
                SELECT
                    id,
                    feature_id,
                    key,
                    CASE
                        WHEN length(value) <= $4 THEN value
                        ELSE left(value, $4)
                    END AS value,
                    CASE
                        WHEN length(value) <= $4 THEN value
                        ELSE left(value, $4)
                    END AS value_preview,
                    length(value) AS value_chars,
                    octet_length(value) AS value_bytes,
                    length(value) > $4 AS summary_only,
                    created_at
                FROM artifacts
                WHERE feature_id = $1 AND id > $2{prefix_clause}
                ORDER BY id
                LIMIT $3
                """,
                *args,
            )
            return [_legacy_resume_artifact_summary(row, preview_chars) for row in rows]

    async def read_legacy_resume_artifacts(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...] | list[str],
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return await self.list_legacy_resume_artifacts(
            feature_id=feature_id,
            prefixes=prefixes,
            after_id=after_id,
            limit=limit,
        )

    async def record_workspace_registry(
        self,
        evidence: WorkspaceRegistryEvidence,
    ) -> ExecutionJournalResult:
        payload = _payload_dict(evidence.payload)
        registry_digest = evidence.registry_digest or str(payload.get("registry_digest") or "")
        _validate_workspace_digest(
            payload,
            registry_digest,
            field="registry_digest",
            evidence_kind="workspace registry",
        )
        value = _bounded_workspace_projection_value(payload)
        projection = CompatibilityProjection(
            key=evidence.artifact_key,
            value=value,
            idempotency_key=f"{evidence.idempotency_key}:projection",
        )
        write = ExecutionJournalWrite(
            feature_id=evidence.feature_id,
            idempotency_key=evidence.idempotency_key,
            entry_type="workspace_registry",
            status="succeeded",
            payload={
                "payload": payload,
                "registry_digest": registry_digest,
                "artifact_key": evidence.artifact_key,
            },
            actor=evidence.actor,
            dag_sha256=evidence.dag_sha256,
            group_idx=evidence.group_idx,
            requires_legacy_visibility=True,
            compatibility_projections=(projection,),
        )
        return await self.record(write)

    async def record_workspace_preflight(
        self,
        evidence: WorkspacePreflightEvidence,
    ) -> ExecutionJournalResult:
        payload = _payload_dict(evidence.payload)
        registry_digest = evidence.registry_digest or str(payload.get("registry_digest") or "")
        _validate_workspace_digest(
            payload,
            registry_digest,
            field="registry_digest",
            evidence_kind="workspace preflight",
        )
        value = _bounded_workspace_projection_value(payload)
        projection = CompatibilityProjection(
            key=evidence.artifact_key,
            value=value,
            idempotency_key=f"{evidence.idempotency_key}:projection",
        )
        write = ExecutionJournalWrite(
            feature_id=evidence.feature_id,
            idempotency_key=evidence.idempotency_key,
            entry_type="workspace_preflight",
            status="succeeded" if bool(payload.get("approved", True)) else "failed",
            payload={
                "payload": payload,
                "registry_digest": registry_digest,
                "artifact_key": evidence.artifact_key,
                "attempt_id": evidence.attempt_id,
                "stage": evidence.stage,
            },
            actor=evidence.actor,
            dag_sha256=evidence.dag_sha256,
            group_idx=evidence.group_idx,
            requires_legacy_visibility=True,
            compatibility_projections=(projection,),
        )
        return await self.record(write)

    async def record_workspace_snapshot(
        self,
        evidence: WorkspaceSnapshotEvidence,
    ) -> WorkspaceSnapshotResult:
        payload = _payload_dict(evidence.payload)
        registry_digest = evidence.registry_digest or str(payload.get("registry_digest") or "")
        _validate_workspace_digest(
            payload,
            registry_digest,
            field="registry_digest",
            evidence_kind="workspace snapshot",
        )
        snapshot_digest = _workspace_snapshot_digest(payload)
        idempotency_key = evidence.stable_idempotency_key
        projection = CompatibilityProjection(
            key=evidence.projection_key,
            value=_workspace_snapshot_projection_value(payload),
            idempotency_key=f"{idempotency_key}:projection",
        )
        write = ExecutionJournalWrite(
            feature_id=evidence.feature_id,
            idempotency_key=idempotency_key,
            entry_type="workspace_snapshot",
            status="succeeded",
            payload={
                "snapshot_digest": snapshot_digest,
                "registry_digest": registry_digest,
                "repo_id": evidence.repo_id or str(payload.get("repo_id") or ""),
                "stage": evidence.stage or str(payload.get("stage") or ""),
            },
            actor=evidence.actor,
            dag_sha256=evidence.dag_sha256 or str(payload.get("dag_sha256") or ""),
            group_idx=evidence.group_idx if evidence.group_idx is not None else payload.get("group_idx"),
            requires_legacy_visibility=True,
            compatibility_projections=(projection,),
        )
        self._validate(write)
        async with self._connection() as conn:
            async with self._transaction(conn):
                existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
                if existing is None:
                    row, journal_created = await self._insert_typed_row(conn, write)
                    self._validate_row_digest(row, write)
                else:
                    # Reused journal row. The workspace-snapshot idempotency key is a
                    # content identity (repo/stage/head/index/worktree-status), so a key
                    # match IS the same snapshot. Defer the strict request-digest
                    # equality to _validate_workspace_snapshot_record below, which
                    # recomputes the digest from the stored full payload: that lets a
                    # benign digest delta (e.g. a legacy row whose digest predates
                    # excluding the volatile attempt_id) re-record idempotently instead
                    # of dead-locking resume, while a genuinely different snapshot still
                    # conflicts there. Everything the request digest adds over the key
                    # (snapshot_digest, registry_digest, repo_id, stage) is re-checked
                    # there or is already part of the key.
                    row = self._row_from_record(existing)
                    journal_created = False
                snapshot, snapshot_created = await self._insert_or_reuse_workspace_snapshot(
                    conn,
                    evidence,
                    execution_row=row,
                    payload=payload,
                    registry_digest=registry_digest,
                    snapshot_digest=snapshot_digest,
                    idempotency_key=idempotency_key,
                )
                await self._complete_missing_projections(
                    conn,
                    row,
                    (projection,),
                    source_table="workspace_snapshots",
                    source_id=snapshot.id,
                    projection_owner="workspace_authority",
                    projection_kind="workspace_snapshot",
                    projection_payload={
                        "entry_type": "workspace_snapshot",
                        "workspace_snapshot_id": snapshot.id,
                    },
                )
                links = await self._fetch_projection_links(conn, row.id)
                execution = ExecutionJournalResult(
                    row=row,
                    projection_links=tuple(links),
                    created=journal_created,
                )
                return WorkspaceSnapshotResult(
                    snapshot=snapshot,
                    execution=execution,
                    created=snapshot_created,
                )

    async def allocate_sandbox_lease(
        self,
        lease: SandboxLease,
        *,
        repo_bindings: tuple[SandboxRepoBinding, ...] | list[SandboxRepoBinding] = (),
    ) -> SandboxLeaseResult:
        fields = _sandbox_lease_fields(lease)
        idempotency_key = str(fields["idempotency_key"])
        projection_key = str(fields["projection_key"])
        request_digest = stable_digest({
            "entry_type": "sandbox_manifest",
            "feature_id": fields["feature_id"],
            "idempotency_key": idempotency_key,
            "lease_digest": fields["lease_digest"],
            "projection_key": projection_key,
        })
        write = ExecutionJournalWrite(
            feature_id=str(fields["feature_id"]),
            idempotency_key=idempotency_key,
            entry_type="sandbox_manifest",
            status="succeeded",
            payload={
                "attempt_no": fields["attempt_no"],
                "lease_digest": fields["lease_digest"],
                "mode": fields["mode"],
                "projection_key": projection_key,
                "sandbox_id": fields["sandbox_id"],
            },
            actor="sandbox_runner",
            dag_sha256=str(fields["dag_sha256"]),
            group_idx=int(fields["group_idx"]),
            requires_legacy_visibility=True,
            request_digest=request_digest,
        )
        async with self._connection() as conn:
            async with self._transaction(conn):
                existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
                if existing is None:
                    row, journal_created = await self._insert_typed_row(conn, write)
                else:
                    row = self._row_from_record(existing)
                    journal_created = False
                self._validate_row_digest(row, write)
                lease_row, lease_created = await self._insert_or_reuse_sandbox_lease(
                    conn,
                    fields,
                    execution_row=row,
                )
                repo_rows: list[SandboxRepoBinding] = []
                for binding in repo_bindings:
                    binding_fields = _sandbox_repo_binding_fields(binding, lease=lease_row)
                    binding_row, _binding_created = await self._insert_or_reuse_sandbox_repo_binding(
                        conn,
                        binding_fields,
                    )
                    repo_rows.append(binding_row)
                if not repo_rows:
                    repo_rows = await self._fetch_sandbox_repo_bindings_for_lease(
                        conn,
                        int(lease_row.id or 0),
                    )
                projection = CompatibilityProjection(
                    key=projection_key,
                    value=_sandbox_manifest_projection_value(lease_row, tuple(repo_rows)),
                    idempotency_key=f"{idempotency_key}:projection",
                )
                await self._complete_missing_projections(
                    conn,
                    row,
                    (projection,),
                    source_table="sandbox_leases",
                    source_id=lease_row.id,
                    projection_owner="sandbox_runner",
                    projection_kind="sandbox_manifest",
                    projection_payload={
                        "entry_type": "sandbox_manifest",
                        "sandbox_lease_id": lease_row.id,
                    },
                )
                links = await self._fetch_projection_links(conn, row.id)
                execution = ExecutionJournalResult(
                    row=row,
                    projection_links=tuple(links),
                    created=journal_created,
                )
                return SandboxLeaseResult(
                    lease=lease_row,
                    repo_bindings=tuple(repo_rows),
                    execution=execution,
                    created=lease_created,
                )

    async def record_sandbox_repo_binding(
        self,
        binding: SandboxRepoBinding,
    ) -> SandboxRepoBindingResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                lease = None
                if not binding.feature_id and binding.sandbox_lease_id:
                    lease = await self._fetch_sandbox_lease_by_id(conn, binding.sandbox_lease_id)
                fields = _sandbox_repo_binding_fields(binding, lease=lease)
                row, created = await self._insert_or_reuse_sandbox_repo_binding(conn, fields)
                return SandboxRepoBindingResult(binding=row, created=created)

    async def record_runtime_workspace_binding(
        self,
        binding: RuntimeWorkspaceBinding,
    ) -> RuntimeWorkspaceBindingResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                lease = None
                if not binding.feature_id and binding.sandbox_lease_id:
                    lease = await self._fetch_sandbox_lease_by_id(conn, binding.sandbox_lease_id)
                fields = _runtime_workspace_binding_fields(binding, lease=lease)
                if int(fields["sandbox_lease_id"]) <= 0:
                    raise ExecutionControlError(
                        "runtime workspace binding requires sandbox lease id"
                    )
                row, created = await self._insert_or_reuse_runtime_workspace_binding(
                    conn,
                    fields,
                )
                return RuntimeWorkspaceBindingResult(binding=row, created=created)

    async def get_sandbox_lease_by_idempotency_key(
        self,
        feature_id: str,
        idempotency_key: str,
    ) -> SandboxLease | None:
        async with self._connection() as conn:
            row = await self._fetch_sandbox_lease_by_idempotency(
                conn,
                feature_id,
                idempotency_key,
            )
        if row is None:
            return None
        return self._sandbox_lease_from_record(row)

    async def update_sandbox_lease(
        self,
        lease: SandboxLease,
    ) -> SandboxLease:
        fields = _sandbox_lease_fields(lease)
        lease_id = int(
            _field(lease, "id", 0)
            or _field(lease, "sandbox_lease_id", 0)
            or 0
        )
        async with self._connection() as conn:
            async with self._transaction(conn):
                current = None
                if lease_id:
                    current = await self._fetch_sandbox_lease_by_id(conn, lease_id)
                else:
                    current = await self._fetch_sandbox_lease_by_idempotency(
                        conn,
                        str(fields["feature_id"]),
                        str(fields["idempotency_key"]),
                    )
                if current is None:
                    raise ExecutionControlError("sandbox lease update target not found")
                current_status = str(current.status)
                next_status = str(fields["status"])
                if (
                    current_status in _SANDBOX_TERMINAL_STATUSES
                    and next_status not in _SANDBOX_TERMINAL_STATUSES
                ):
                    raise ExecutionControlError(
                        "terminal sandbox lease cannot transition back to active"
                    )
                expected_version = int(fields["lease_version"] or 0)
                if expected_version != int(current.lease_version or 0):
                    raise ExecutionControlError("sandbox lease version conflict")
                row = await conn.fetchrow(
                    """
                    UPDATE sandbox_leases
                    SET
                        status = $1,
                        patch_summary_ids = $2::jsonb,
                        lease_version = lease_version + 1,
                        payload = COALESCE(payload, '{}'::jsonb) || $3::jsonb,
                        updated_at = NOW()
                    WHERE (
                        ($4::bigint IS NOT NULL AND id = $4)
                        OR (
                            $4::bigint IS NULL
                            AND feature_id = $5
                            AND idempotency_key = $6
                        )
                    )
                    AND lease_version = $7
                    RETURNING *
                    """,
                    fields["status"],
                    stable_json(fields["patch_summary_ids"]),
                    stable_json(
                        {
                            "status": fields["status"],
                            "patch_summary_ids": fields["patch_summary_ids"],
                            "updated_by": "sandbox_runner",
                        }
                    ),
                    lease_id if lease_id else None,
                    fields["feature_id"],
                    fields["idempotency_key"],
                    expected_version,
                )
                if row is None:
                    raise ExecutionControlError("sandbox lease version conflict")
                return self._sandbox_lease_from_record(row)

    async def list_active_sandbox_leases(
        self,
        owner: str | None = None,
        *,
        owner_prefix: str | None = None,
        feature_id: str | None = None,
    ) -> list[SandboxLease]:
        clauses = [
            "status NOT IN ('captured', 'released', 'retained', 'failed', 'poisoned')"
        ]
        args: list[Any] = []
        if feature_id:
            args.append(str(feature_id))
            clauses.append(f"feature_id = ${len(args)}")
        if owner:
            args.append(str(owner))
            clauses.append(f"lease_owner = ${len(args)}")
        if owner_prefix:
            args.append(f"{owner_prefix}%")
            clauses.append(f"lease_owner LIKE ${len(args)}")
        async with self._connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                FROM sandbox_leases
                WHERE {' AND '.join(clauses)}
                ORDER BY id ASC
                """,
                *args,
            )
        return [self._sandbox_lease_from_record(row) for row in rows]

    async def get_sandbox_lease_by_attempt_no(
        self,
        *,
        feature_id: str,
        attempt_no: int,
    ) -> SandboxLease | None:
        async with self._connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM sandbox_leases
                WHERE feature_id = $1 AND attempt_no = $2
                ORDER BY id DESC
                LIMIT 1
                """,
                str(feature_id),
                int(attempt_no),
            )
        if row is None:
            return None
        return self._sandbox_lease_from_record(row)

    # ── Slice 10a — typed control-plane snapshot reads ─────────────────────

    async def get_control_plane_snapshot(
        self,
        query: Any,
        *,
        conn: Any | None = None,
    ) -> Any:
        """Return the typed, bounded :class:`ControlPlaneSnapshot` for a query.

        doc 10 § "Refactoring Steps" step 1. Every read inside the builder is
        feature-(and-optional-group)-scoped, keyset-indexed, ``LIMIT cap + 1``
        bounded, and SUMMARY-ONLY. This method wraps the builder in a
        transaction with a ``SET LOCAL statement_timeout`` at the store
        boundary (doc 10 § "Bounded-Read Constraints"); on timeout the builder
        degrades the affected section to empty + a ``degradation_reasons``
        entry rather than retrying unbounded.

        ``query`` is a
        :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshotQuery`
        (its ``budget`` is already clamped to the ceiling by the model
        validator — a caller cannot widen a store read).

        When ``conn`` is provided (keyword-only), the bounded snapshot read
        participates in the caller's transaction — required for Slice 10g-1
        cross-connection correctness so the version-read sees the caller's
        uncommitted typed inserts under READ-COMMITTED isolation. When
        ``conn`` is ``None`` (the default, for non-transactional callers like
        the dashboard route), a fresh pooled connection is opened as before.
        """

        timeout_ms = int(query.budget.query_timeout_ms)
        if conn is None:
            async with self._connection() as new_conn:
                async with self._transaction(new_conn):
                    await self._set_local_statement_timeout(new_conn, timeout_ms)
                    return await build_typed_control_plane_snapshot(new_conn, query)
        # ``conn`` was supplied — the read MUST participate in the caller's
        # transaction (do NOT open a new pool connection, or the cross-
        # connection stale-read bug from Slice 10g-1 P2 returns). The caller
        # already owns the surrounding transaction; we still apply the local
        # statement timeout for parity with the pool path.
        await self._set_local_statement_timeout(conn, timeout_ms)
        return await build_typed_control_plane_snapshot(conn, query)

    async def get_control_plane_snapshot_version(
        self,
        feature_id: str,
        *,
        conn: Any | None = None,
    ) -> str:
        """Return the deterministic typed snapshot version digest.

        doc 10 § "Proposed Interfaces/Types": a stable digest over typed
        max-ids + max-``updated_at`` from ``execution_attempts``,
        ``typed_failures``, ``failure_route_budgets``, ``merge_queue_items``,
        ``evidence_nodes``, ``workspace_snapshots``, ``sandbox_leases``, and
        ``runtime_workspace_bindings`` — NEVER over artifact bodies. It is the
        dashboard ETag seed and the Slack/outbox idempotency seed; it must be
        cheap (eight feature-scoped ``MAX()`` aggregates). A budget-only or
        sandbox-only update still advances the version because every logical
        cursor table is in the digest.

        When ``conn`` is provided (keyword-only), the version digest is
        computed on that connection — required for Slice 10g-1 cross-
        connection correctness so the digest reflects the caller's
        uncommitted typed inserts under READ-COMMITTED isolation (without
        this, an active projection transaction's pre-transaction
        ``snapshot_version`` could collide with an already-emitted outbox
        ``event_id`` and silently drop the new emission via
        ``ON CONFLICT (event_id) DO NOTHING``). When ``conn`` is ``None``
        (the default), a fresh pooled connection is opened as before.
        """

        # The version digest is eight cheap aggregates — still timeout-bounded
        # at the store boundary so a degenerate plan cannot stall a caller.
        if conn is None:
            async with self._connection() as new_conn:
                async with self._transaction(new_conn):
                    await self._set_local_statement_timeout(new_conn, 1_500)
                    return await compute_typed_snapshot_version(
                        new_conn, str(feature_id)
                    )
        # ``conn`` was supplied — execute on it directly so the digest sees
        # the caller's uncommitted typed inserts. A new pool connection here
        # would silently undo the Slice 10g-1 P2 fix.
        await self._set_local_statement_timeout(conn, 1_500)
        return await compute_typed_snapshot_version(conn, str(feature_id))

    @staticmethod
    async def _set_local_statement_timeout(conn: Any, timeout_ms: int) -> None:
        """Apply a transaction-local statement timeout at the store boundary.

        ``SET LOCAL`` scopes the timeout to the enclosing transaction only, so
        it cannot leak onto a pooled connection reused by another caller
        (unlike ``db_safety.install_safety_indexes``'s session-level ``SET``,
        which runs on a dedicated short-lived connection). A non-positive value
        is ignored — the model validator already rejects non-positive budgets,
        so this is just defence in depth.
        """

        if timeout_ms <= 0:
            return
        execute = getattr(conn, "execute", None)
        if execute is None:  # pragma: no cover - in-memory fakes
            return
        # statement_timeout takes an integer-millisecond string; the value is
        # an int (never caller text) so this is not an injection surface.
        await execute(f"SET LOCAL statement_timeout = '{int(timeout_ms)}ms'")

    async def _record_evidence_with_projection(
        self,
        *,
        fields: dict[str, Any],
        entry_type: str,
        kind: str,
        actor: str,
        projection_owner: str,
        projection_kind: str,
        projection_key: str,
        projection_value_factory: Any,
    ) -> EvidenceNodeResult:
        async with self._connection() as conn:
            async with self._transaction(conn):
                return await self._record_evidence_with_projection_in_transaction(
                    conn,
                    fields=fields,
                    entry_type=entry_type,
                    kind=kind,
                    actor=actor,
                    projection_owner=projection_owner,
                    projection_kind=projection_kind,
                    projection_key=projection_key,
                    projection_value_factory=projection_value_factory,
                )

    async def _record_evidence_with_projection_in_transaction(
        self,
        conn: Any,
        *,
        fields: dict[str, Any],
        entry_type: str,
        kind: str,
        actor: str,
        projection_owner: str,
        projection_kind: str,
        projection_key: str,
        projection_value_factory: Any,
    ) -> EvidenceNodeResult:
        payload = dict(fields["payload"])
        digest_payload = (
            _patch_summary_digest_payload(payload)
            if entry_type == "sandbox_patch_summary"
            else payload
        )
        content_hash = str(fields["content_hash"])
        idempotency_key = str(fields["idempotency_key"])
        request_digest = stable_digest({
            "content_hash": content_hash,
            "entry_type": entry_type,
            "feature_id": fields["feature_id"],
            "idempotency_key": idempotency_key,
            "kind": kind,
            "payload": digest_payload,
            "projection_key": projection_key,
        })
        write = ExecutionJournalWrite(
            feature_id=str(fields["feature_id"]),
            idempotency_key=idempotency_key,
            entry_type=entry_type,
            status="succeeded",
            payload={
                "content_hash": content_hash,
                "kind": kind,
                "projection_key": projection_key,
            },
            actor=actor,
            dag_sha256=str(fields.get("dag_sha256") or ""),
            group_idx=fields.get("group_idx"),
            task_id=fields.get("task_id") or None,
            requires_legacy_visibility=True,
            request_digest=request_digest,
        )
        existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
        if existing is None:
            row, journal_created = await self._insert_typed_row(conn, write)
        else:
            row = self._row_from_record(existing)
            journal_created = False
        self._validate_row_digest(row, write)
        evidence, evidence_created = await self._insert_or_reuse_evidence_node(
            conn,
            fields,
            execution_row=row,
            kind=kind,
        )
        projection = CompatibilityProjection(
            key=projection_key,
            value=projection_value_factory(evidence),
            idempotency_key=f"{idempotency_key}:projection",
        )
        await self._complete_missing_projections(
            conn,
            row,
            (projection,),
            source_table="evidence_nodes",
            source_id=evidence.id,
            projection_owner=projection_owner,
            projection_kind=projection_kind,
                projection_payload={
                    "entry_type": entry_type,
                    "evidence_node_id": evidence.id,
                    "evidence_kind": kind,
                    "snapshot_id": evidence.snapshot_id,
                    "input_refs": list(evidence.input_refs),
                },
            )
        links = await self._fetch_projection_links(conn, row.id)
        execution = ExecutionJournalResult(
            row=row,
            projection_links=tuple(links),
            created=journal_created,
        )
        return EvidenceNodeResult(
            evidence=evidence,
            execution=execution,
            created=evidence_created,
        )

    async def _record_verification_graph_node_in_transaction(
        self,
        conn: Any,
        *,
        fields: dict[str, Any],
        node: VerificationGraphNodeEvidence,
    ) -> EvidenceNodeResult:
        request_digest = stable_digest({
            "content_hash": fields["content_hash"],
            "feature_id": fields["feature_id"],
            "idempotency_key": fields["idempotency_key"],
            "kind": node.kind,
            "payload": fields["payload"],
            "status": node.status,
        })
        write = ExecutionJournalWrite(
            feature_id=node.feature_id,
            idempotency_key=node.idempotency_key,
            entry_type="verification_graph_node",
            status="succeeded",
            payload={
                "content_hash": fields["content_hash"],
                "kind": node.kind,
                "stage": node.stage,
                "status": node.status,
            },
            actor="verification_graph",
            dag_sha256=node.dag_sha256,
            group_idx=node.group_idx,
            request_digest=request_digest,
        )
        existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
        if existing is None:
            row, journal_created = await self._insert_typed_row(conn, write)
        else:
            row = self._row_from_record(existing)
            journal_created = False
        self._validate_row_digest(row, write)
        evidence, evidence_created = await self._insert_or_reuse_evidence_node(
            conn,
            fields,
            execution_row=row,
            kind=node.kind,
        )
        return EvidenceNodeResult(
            evidence=evidence,
            execution=ExecutionJournalResult(
                row=row,
                projection_links=tuple(await self._fetch_projection_links(conn, row.id)),
                created=journal_created,
            ),
            created=evidence_created,
        )

    async def _record_verification_graph_payload_nodes(
        self,
        conn: Any,
        projection: VerificationGraphProjection,
    ) -> EvidenceNode:
        payload = projection.graph_payload
        raw_nodes = _json_list(payload.get("nodes"))
        aggregate_payload = payload.get("aggregate_node")
        if isinstance(aggregate_payload, dict):
            aggregate_id = aggregate_payload.get("id")
            raw_nodes = [
                aggregate_payload
                if isinstance(node, dict) and node.get("id") == aggregate_id
                else node
                for node in raw_nodes
            ]
            if not any(
                isinstance(node, dict) and node.get("id") == aggregate_id
                for node in raw_nodes
            ):
                raw_nodes.append(aggregate_payload)
        aggregate_evidence: EvidenceNode | None = None
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                continue
            node = _verification_graph_node_from_graph_payload(
                raw_node,
                projection=projection,
            )
            result = await self._record_verification_graph_node_in_transaction(
                conn,
                fields=_verification_graph_node_fields(node),
                node=node,
            )
            if node.kind == "aggregate_verdict":
                aggregate_evidence = result.evidence
        if aggregate_evidence is None:
            raise MissingRequiredProjection(
                "verification graph projection requires an aggregate_verdict node"
            )
        return aggregate_evidence

    async def _insert_or_reuse_evidence_graph(
        self,
        conn: Any,
        *,
        projection: VerificationGraphProjection,
        row: ExecutionJournalRow,
        aggregate_node: EvidenceNode,
        projection_sha256: str,
    ) -> Any:
        graph_payload_digest = stable_digest(projection.graph_payload)
        required_edge_ids = _verification_graph_required_edge_ids(projection.graph_payload)
        idempotency_key = f"{projection.stable_idempotency_key}:graph"
        existing = await self._fetch_evidence_graph_by_idempotency(
            conn,
            projection.feature_id,
            idempotency_key,
        )
        if existing is not None:
            self._validate_evidence_graph_record(
                existing,
                row_id=row.id,
                aggregate_node_id=aggregate_node.id,
                projection=projection,
                projection_sha256=projection_sha256,
                graph_payload_digest=graph_payload_digest,
            )
            return existing
        record = await conn.fetchrow(
            """
            INSERT INTO evidence_graphs (
                feature_id, idempotency_key, execution_journal_row_id,
                aggregate_evidence_node_id, projection_key, projection_sha256,
                dag_sha256, group_idx, stage, proof_digest,
                graph_payload_digest, required_edge_ids, payload
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12::jsonb, $13::jsonb
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            projection.feature_id,
            idempotency_key,
            row.id,
            aggregate_node.id,
            projection.projection_key,
            projection_sha256,
            projection.dag_sha256,
            projection.group_idx,
            projection.stage,
            projection.proof_digest,
            graph_payload_digest,
            stable_json(required_edge_ids),
            stable_json(projection.graph_payload),
        )
        if record is None:
            existing_after_conflict = await self._fetch_evidence_graph_by_idempotency(
                conn,
                projection.feature_id,
                idempotency_key,
            )
            if existing_after_conflict is None:
                raise RuntimeError("evidence graph insert conflict could not be reloaded")
            self._validate_evidence_graph_record(
                existing_after_conflict,
                row_id=row.id,
                aggregate_node_id=aggregate_node.id,
                projection=projection,
                projection_sha256=projection_sha256,
                graph_payload_digest=graph_payload_digest,
            )
            return existing_after_conflict
        return record

    async def _persist_verification_graph_edges(
        self,
        conn: Any,
        *,
        projection: VerificationGraphProjection,
        graph_id: int,
    ) -> None:
        raw_edges = [
            edge for edge in _json_list(projection.graph_payload.get("edges"))
            if isinstance(edge, dict)
        ]
        required_edge_ids = {
            str(item) for item in _verification_graph_required_edge_ids(projection.graph_payload)
        }
        evidence_ids = await self._verification_graph_node_evidence_ids(conn, projection)
        aggregate_node_id = _optional_int(_json_dict(projection.graph_payload.get("aggregate")).get("node_id"))
        required_node_ids = {
            str(item)
            for item in _verification_graph_required_node_ids(projection.graph_payload)
        }
        required_edge_paths = [
            (
                _optional_int(edge.get("from_node_id") or edge.get("from") or edge.get("source")),
                _optional_int(edge.get("to_node_id") or edge.get("to") or edge.get("target")),
            )
            for edge in raw_edges
            if str(edge.get("id") or "") in required_edge_ids
        ]
        cyclic_path = _verification_graph_required_edge_cycle(
            required_edges=[
                (from_node_id, to_node_id)
                for from_node_id, to_node_id in required_edge_paths
                if from_node_id is not None and to_node_id is not None
            ]
        )
        if cyclic_path:
            raise MissingRequiredProjection(
                "approved verification graph required edges must be acyclic: "
                f"{cyclic_path}"
            )
        for idx, raw_edge in enumerate(raw_edges):
            graph_edge_id = str(raw_edge.get("id") or f"edge-{idx}")
            from_graph_node_id = str(
                raw_edge.get("from_node_id")
                or raw_edge.get("from")
                or raw_edge.get("source")
                or ""
            )
            to_graph_node_id = str(
                raw_edge.get("to_node_id")
                or raw_edge.get("to")
                or raw_edge.get("target")
                or ""
            )
            edge_payload = {
                "edge": raw_edge,
                "projection_key": projection.projection_key,
            }
            required = graph_edge_id in required_edge_ids
            required_target_ids = {str(item) for item in required_node_ids}
            if aggregate_node_id is not None:
                required_target_ids.add(str(aggregate_node_id))
            if required and (
                str(raw_edge.get("kind") or "") != "requires"
                or from_graph_node_id not in required_node_ids
                or to_graph_node_id not in required_target_ids
                or evidence_ids.get(from_graph_node_id) is None
                or evidence_ids.get(to_graph_node_id) is None
            ):
                raise MissingRequiredProjection(
                    "approved verification graph required edge must connect approved required lineage nodes"
                )
            edge_digest = stable_digest({
                "from_evidence_node_id": evidence_ids.get(from_graph_node_id),
                "from_graph_node_id": from_graph_node_id,
                "graph_edge_id": graph_edge_id,
                "kind": str(raw_edge.get("kind") or ""),
                "payload": edge_payload,
                "required": required,
                "to_evidence_node_id": evidence_ids.get(to_graph_node_id),
                "to_graph_node_id": to_graph_node_id,
            })
            await self._insert_or_reuse_evidence_edge(
                conn,
                feature_id=projection.feature_id,
                graph_id=graph_id,
                graph_edge_id=graph_edge_id,
                from_graph_node_id=from_graph_node_id,
                to_graph_node_id=to_graph_node_id,
                from_evidence_node_id=evidence_ids.get(from_graph_node_id),
                to_evidence_node_id=evidence_ids.get(to_graph_node_id),
                kind=str(raw_edge.get("kind") or ""),
                required=required,
                edge_digest=edge_digest,
                payload=edge_payload,
                idempotency_key=(
                    f"{projection.stable_idempotency_key}:graph:{graph_id}:edge:{graph_edge_id}"
                ),
            )

    async def _verification_graph_node_evidence_ids(
        self,
        conn: Any,
        projection: VerificationGraphProjection,
    ) -> dict[str, int]:
        evidence_ids: dict[str, int] = {}
        raw_nodes = [
            node for node in _json_list(projection.graph_payload.get("nodes"))
            if isinstance(node, dict)
        ]
        aggregate_payload = projection.graph_payload.get("aggregate_node")
        if isinstance(aggregate_payload, dict):
            aggregate_id = aggregate_payload.get("id")
            raw_nodes = [
                aggregate_payload if node.get("id") == aggregate_id else node
                for node in raw_nodes
            ]
            if not any(node.get("id") == aggregate_id for node in raw_nodes):
                raw_nodes.append(aggregate_payload)
        for raw_node in raw_nodes:
            graph_node_id = raw_node.get("id")
            if graph_node_id is None:
                continue
            node = _verification_graph_node_from_graph_payload(
                raw_node,
                projection=projection,
            )
            record = await self._fetch_evidence_by_idempotency(
                conn,
                projection.feature_id,
                node.idempotency_key,
            )
            if record is not None:
                evidence_ids[str(graph_node_id)] = int(_record_get(record, "id"))
        return evidence_ids

    async def _insert_or_reuse_evidence_edge(
        self,
        conn: Any,
        *,
        feature_id: str,
        graph_id: int,
        graph_edge_id: str,
        from_graph_node_id: str,
        to_graph_node_id: str,
        from_evidence_node_id: int | None,
        to_evidence_node_id: int | None,
        kind: str,
        required: bool,
        edge_digest: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> Any:
        existing = await self._fetch_evidence_edge_by_idempotency(
            conn,
            feature_id,
            idempotency_key,
        )
        if existing is not None:
            self._validate_evidence_edge_record(
                existing,
                graph_id=graph_id,
                graph_edge_id=graph_edge_id,
                edge_digest=edge_digest,
            )
            return existing
        record = await conn.fetchrow(
            """
            INSERT INTO evidence_edges (
                feature_id, idempotency_key, evidence_graph_id, graph_edge_id,
                from_graph_node_id, to_graph_node_id, from_evidence_node_id,
                to_evidence_node_id, kind, required, edge_digest, payload
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12::jsonb
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            feature_id,
            idempotency_key,
            graph_id,
            graph_edge_id,
            from_graph_node_id,
            to_graph_node_id,
            from_evidence_node_id,
            to_evidence_node_id,
            kind,
            required,
            edge_digest,
            stable_json(payload),
        )
        if record is None:
            existing_after_conflict = await self._fetch_evidence_edge_by_idempotency(
                conn,
                feature_id,
                idempotency_key,
            )
            if existing_after_conflict is None:
                raise RuntimeError("evidence edge insert conflict could not be reloaded")
            self._validate_evidence_edge_record(
                existing_after_conflict,
                graph_id=graph_id,
                graph_edge_id=graph_edge_id,
                edge_digest=edge_digest,
            )
            return existing_after_conflict
        return record

    async def _project(self, projection: Any, *, entry_type: str) -> ExecutionJournalResult:
        key = _projection_key(projection)
        _validate_projection_key_family(entry_type, key)
        value = _projection_value(projection)
        if value is None:
            raise MissingRequiredProjection(
                f"legacy projection {key or '<missing>'} requires a projection body"
            )
        compat = CompatibilityProjection(
            key=key,
            value=value,
            idempotency_key=str(getattr(projection, "idempotency_key", "") or ""),
        )
        write = ExecutionJournalWrite(
            feature_id=str(getattr(projection, "feature_id")),
            idempotency_key=str(getattr(projection, "idempotency_key")),
            entry_type=entry_type,
            status="succeeded",
            payload=_projection_payload(projection),
            actor=str(getattr(projection, "source_kind", "") or getattr(projection, "source_table", "") or ""),
            dag_sha256=str(getattr(projection, "dag_sha256", "") or ""),
            group_idx=getattr(projection, "group_idx", None),
            task_id=getattr(projection, "task_id", None),
            requires_legacy_visibility=True,
            compatibility_projections=(compat,),
        )
        projection_payload = (
            _group_checkpoint_projection_payload(projection)
            if entry_type == "group_checkpoint"
            else None
        )
        return await self.record(write, projection_payload=projection_payload)

    async def _project_group_checkpoint(
        self,
        projection: Any,
    ) -> ExecutionJournalResult:
        key = _projection_key(projection)
        _validate_projection_key_family("group_checkpoint", key)
        value = _projection_value(projection)
        if value is None:
            raise MissingRequiredProjection(
                f"legacy projection {key or '<missing>'} requires a projection body"
            )
        compat = CompatibilityProjection(
            key=key,
            value=value,
            idempotency_key=str(getattr(projection, "idempotency_key", "") or ""),
        )
        write = ExecutionJournalWrite(
            feature_id=str(getattr(projection, "feature_id")),
            idempotency_key=str(getattr(projection, "idempotency_key")),
            entry_type="group_checkpoint",
            status="succeeded",
            payload=_projection_payload(projection),
            actor=str(
                getattr(projection, "source_kind", "")
                or getattr(projection, "source_table", "")
                or ""
            ),
            dag_sha256=str(getattr(projection, "dag_sha256", "") or ""),
            group_idx=getattr(projection, "group_idx", None),
            task_id=getattr(projection, "task_id", None),
            requires_legacy_visibility=True,
            compatibility_projections=(compat,),
        )
        self._validate(write)
        projection_payload = _group_checkpoint_projection_payload(projection)
        async with self._connection() as conn:
            async with self._transaction(conn):
                existing = await self._fetch_existing(
                    conn,
                    write.feature_id,
                    write.idempotency_key,
                )
                if existing is None:
                    row, created = await self._insert_typed_row(conn, write)
                else:
                    row = self._row_from_record(existing)
                    created = False
                self._validate_row_digest(row, write)
                gate_source_id = _optional_int(getattr(projection, "source_id", None))
                if (
                    gate_source_id is None
                    or str(getattr(projection, "source_table", "") or "") != "evidence_nodes"
                ):
                    raise MissingRequiredProjection(
                        "dag-group checkpoint projection requires existing "
                        "checkpoint_gate evidence source"
                    )
                gate = await self._fetch_evidence_node_by_id(conn, gate_source_id)
                if (
                    gate is None
                    or gate.feature_id != write.feature_id
                    or gate.kind != "checkpoint_gate"
                    or gate.status != "approved"
                ):
                    raise MissingRequiredProjection(
                        "dag-group checkpoint projection source must be an "
                        "approved checkpoint_gate evidence node"
                    )
                await self._complete_missing_projections(
                    conn,
                    row,
                    (compat,),
                    source_table="evidence_nodes",
                    source_id=gate.id,
                    projection_owner="merge_queue",
                    projection_kind="group_checkpoint",
                    projection_payload={
                        **projection_payload,
                        "evidence_kind": "checkpoint_gate",
                        "evidence_node_id": gate.id,
                    },
                )
                links = await self._fetch_projection_links(conn, row.id)
                return ExecutionJournalResult(
                    row=row,
                    projection_links=tuple(links),
                    created=created,
                )

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        acquire_method = getattr(self._pool, "acquire", None)
        if acquire_method is None:
            yield self._pool
            return
        acquire = acquire_method()
        if hasattr(acquire, "__aenter__"):
            async with acquire as conn:
                yield conn
            return
        conn = await acquire
        try:
            yield conn
        finally:
            release = getattr(self._pool, "release", None)
            if release is not None:
                await release(conn)

    @asynccontextmanager
    async def _transaction(self, conn: Any) -> AsyncIterator[None]:
        transaction = getattr(conn, "transaction", None)
        if transaction is None:
            with nullcontext():
                yield
            return
        tx = transaction()
        if hasattr(tx, "__aenter__"):
            async with tx:
                yield
            return
        await tx.start()
        try:
            yield
        except Exception:
            await tx.rollback()
            raise
        else:
            await tx.commit()

    def _validate(self, write: ExecutionJournalWrite) -> None:
        projections = tuple(write.compatibility_projections)
        requires_projection = (
            write.requires_legacy_visibility
            or write.entry_type in LEGACY_VISIBLE_ENTRY_TYPES
        )
        if write.status == "succeeded" and requires_projection and not projections:
            raise MissingCompatibilityProjection(
                "legacy-visible typed success requires compatibility projections"
            )
        for projection in projections:
            if not _supported_projection_key(projection.key):
                raise UnsupportedCompatibilityProjection(
                    f"unsupported compatibility projection key: {projection.key}"
                )
            _validate_projection_key_family(write.entry_type, projection.key)

    def _validate_row_digest(
        self,
        row: ExecutionJournalRow,
        write: ExecutionJournalWrite,
    ) -> None:
        if row.request_digest != write.digest:
            raise IdempotencyConflict(
                "execution journal idempotency key was reused with a different request"
            )

    async def _fetch_existing(self, conn: Any, feature_id: str, idempotency_key: str) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM execution_journal_rows
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_dispatch_attempt_by_id(
        self,
        conn: Any,
        attempt_id: int,
    ) -> ExecutionJournalRow:
        record = await conn.fetchrow(
            """
            SELECT *
            FROM execution_journal_rows
            WHERE id = $1
            LIMIT 1
            """,
            int(attempt_id),
        )
        if record is None:
            raise ExecutionControlError("dispatch attempt not found")
        row = self._row_from_record(record)
        if row.entry_type != "dispatch_attempt":
            raise ExecutionControlError("execution row is not a dispatch attempt")
        return row

    def _ensure_dispatch_attempt_mutable(self, row: ExecutionJournalRow) -> None:
        if row.status != "started":
            raise ExecutionControlError(
                "terminal dispatch attempt cannot record additional runtime evidence"
            )

    async def _update_dispatch_attempt_row(
        self,
        conn: Any,
        row: ExecutionJournalRow,
        *,
        status: str,
        dispatcher_state: str,
        payload_patch: dict[str, Any],
    ) -> ExecutionJournalRow:
        target_rank = _dispatcher_state_rank(dispatcher_state, status=status)
        candidate = row
        for _attempt in range(3):
            payload = dict(candidate.payload)
            payload.update({
                key: value
                for key, value in payload_patch.items()
                if value is not None
            })
            updated = await conn.fetchrow(
                f"""
                UPDATE execution_journal_rows
                SET
                    status = $1,
                    dispatcher_state = $2,
                    payload = $3::jsonb,
                    updated_at = NOW()
                WHERE id = $4 AND entry_type = 'dispatch_attempt'
                AND status = 'started'
                AND dispatcher_state = $5
                AND {DISPATCHER_STATE_SQL_RANK} <= $6
                RETURNING *
                """,
                status,
                dispatcher_state,
                stable_json(payload),
                candidate.id,
                candidate.dispatcher_state,
                target_rank,
            )
            if updated is not None:
                return self._row_from_record(updated)
            current = await self._fetch_dispatch_attempt_by_id(conn, row.id)
            if current.status != "started":
                raise ExecutionControlError(
                    "dispatch attempt is no longer mutable"
                )
            if _dispatcher_state_rank(current.dispatcher_state) > target_rank:
                return current
            candidate = current
        raise ExecutionControlError(
            "dispatch attempt changed concurrently"
        )

    async def _validate_dispatch_outcome(
        self,
        conn: Any,
        row: ExecutionJournalRow,
        outcome: DispatchOutcome,
    ) -> None:
        terminal_states = {"succeeded", "failed", "cancelled", "incomplete"}
        if outcome.state not in terminal_states:
            raise ExecutionControlError("dispatch outcome must use a terminal state")
        if outcome.status != outcome.state:
            raise ExecutionControlError("dispatch outcome status and state must match")
        if outcome.status == "succeeded":
            if outcome.structured_result_evidence_id is None:
                raise ExecutionControlError(
                    "succeeded dispatch outcome requires structured-result evidence"
                )
            evidence = await self._fetch_evidence_node_by_id(
                conn,
                int(outcome.structured_result_evidence_id),
            )
            if evidence is None:
                raise ExecutionControlError("structured-result evidence not found")
            self._validate_structured_result_for_projection(row, evidence)
        if outcome.status == "failed":
            failure_id = outcome.runtime_failure_id or outcome.typed_failure_id
            if failure_id is None:
                raise ExecutionControlError(
                    "failed dispatch outcome requires typed runtime failure evidence"
                )
            failure = await self._fetch_evidence_node_by_id(conn, int(failure_id))
            if failure is None or failure.kind != "runtime_failure_context":
                raise ExecutionControlError("runtime failure evidence not found")

    def _validate_structured_result_for_projection(
        self,
        row: ExecutionJournalRow,
        evidence: EvidenceNode,
    ) -> None:
        if evidence.kind != "structured_result":
            raise ExecutionControlError("task projection source must be structured_result evidence")
        if evidence.execution_journal_row_id != row.id or evidence.attempt_id != row.id:
            raise ExecutionControlError("structured-result evidence belongs to a different attempt")
        if evidence.status != "approved":
            raise ExecutionControlError("invalid structured output cannot be projected")
        payload = evidence.payload
        if not bool(payload.get("valid")):
            raise ExecutionControlError("invalid structured output cannot be projected")
        normalized_payload = _json_dict(payload.get("normalized_payload"))
        if not normalized_payload:
            raise ExecutionControlError("structured output lacks normalized payload")
        task_id = str(normalized_payload.get("task_id") or "")
        if row.task_id and task_id != row.task_id:
            raise ExecutionControlError("structured output task_id does not match attempt")

    async def _insert_typed_row(
        self,
        conn: Any,
        write: ExecutionJournalWrite,
    ) -> tuple[ExecutionJournalRow, bool]:
        row = await conn.fetchrow(
            """
            INSERT INTO execution_journal_rows (
                feature_id, idempotency_key, entry_type, status, actor,
                dag_sha256, group_idx, task_id, request_digest, payload,
                requires_legacy_visibility, projection_mode,
                dispatcher_state, runtime
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb,
                $11, $12, $13, $14
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            write.feature_id,
            write.idempotency_key,
            write.entry_type,
            write.status,
            write.actor,
            write.dag_sha256,
            write.group_idx,
            write.task_id,
            write.digest,
            stable_json(write.payload),
            write.requires_legacy_visibility,
            write.projection_mode,
            write.dispatcher_state,
            write.runtime,
        )
        if row is None:
            existing = await self._fetch_existing(conn, write.feature_id, write.idempotency_key)
            if existing is None:
                raise RuntimeError("execution journal insert conflict could not be reloaded")
            return self._row_from_record(existing), False
        return self._row_from_record(row), True

    async def _complete_missing_projections(
        self,
        conn: Any,
        row: ExecutionJournalRow,
        projections: tuple[CompatibilityProjection, ...],
        *,
        source_table: str = "execution_journal_rows",
        source_id: int | None = None,
        projection_owner: str | None = None,
        projection_kind: str | None = None,
        projection_payload: dict[str, Any] | None = None,
    ) -> None:
        source_id = row.id if source_id is None else source_id
        projection_owner = projection_owner or PROJECTION_OWNERS.get(row.entry_type, "")
        projection_kind = projection_kind or PROJECTION_KINDS.get(row.entry_type, row.entry_type)
        if projection_payload is None:
            projection_payload = (
                {"entry_type": row.entry_type, **row.payload}
                if row.entry_type == "group_checkpoint"
                else {"entry_type": row.entry_type}
            )
        for projection in projections:
            if not _supported_projection_key(projection.key):
                raise UnsupportedCompatibilityProjection(
                    f"unsupported compatibility projection key: {projection.key}"
                )
            _validate_projection_key_family(row.entry_type, projection.key)
            idempotency_key = projection_idempotency_key(
                feature_id=row.feature_id,
                typed_row_id=row.id,
                projection=projection,
            )
            await self._lock_projection_idempotency(conn, row.feature_id, idempotency_key)
            existing = await self._fetch_projection_by_idempotency(
                conn,
                row.feature_id,
                idempotency_key,
            )
            if existing is not None:
                self._validate_projection_record(
                    existing,
                    projection,
                    typed_row_id=row.id,
                    source_table=source_table,
                    source_id=source_id,
                    projection_owner=projection_owner,
                    projection_kind=projection_kind,
                    projection_payload=projection_payload,
                )
                continue
            artifact_id = await self._insert_or_reuse_artifact(conn, row.feature_id, projection)
            legacy_event_id = await self._insert_legacy_event(
                conn,
                row,
                projection,
            )
            dashboard_outbox_event_id = await self._insert_dashboard_outbox(
                conn,
                row.feature_id,
                artifact_id,
                projection,
            )
            inserted = await conn.fetchrow(
                """
                INSERT INTO execution_artifact_projections (
                    artifact_id, typed_row_id, feature_id, source_table,
                    source_id, projection_owner, projection_kind,
                    projection_key, projection_sha256, legacy_event_id,
                    dashboard_outbox_event_id, payload, idempotency_key
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13)
                ON CONFLICT (feature_id, idempotency_key) DO NOTHING
                RETURNING *
                """,
                artifact_id,
                row.id,
                row.feature_id,
                source_table,
                source_id,
                projection_owner,
                projection_kind,
                projection.key,
                _projection_value_sha256(projection),
                legacy_event_id,
                dashboard_outbox_event_id,
                stable_json(projection_payload),
                idempotency_key,
            )
            if inserted is None:
                existing_after_conflict = await self._fetch_projection_by_idempotency(
                    conn,
                    row.feature_id,
                    idempotency_key,
                )
                if existing_after_conflict is None:
                    raise RuntimeError("projection link insert conflict could not be reloaded")
                self._validate_projection_record(
                    existing_after_conflict,
                    projection,
                    typed_row_id=row.id,
                    source_table=source_table,
                    source_id=source_id,
                    projection_owner=projection_owner,
                    projection_kind=projection_kind,
                    projection_payload=projection_payload,
                )
                if int(_record_get(existing_after_conflict, "artifact_id")) != artifact_id:
                    raise IdempotencyConflict(
                        "projection conflict would publish an unlinked legacy artifact"
                    )
        # Doc 10 § "Dashboard Integration Points" Slice 10g-1 wiring: after
        # every typed projection completes its projection-link insert(s),
        # enqueue a doc-10-compliant control_plane.snapshot_changed public
        # outbox row inside the SAME ``conn`` transaction so the fail-closed
        # property holds end-to-end (a configured-outbox enqueue failure
        # rolls back the projection link insert atomically with the outbox
        # enqueue). The helper itself is idempotent on
        # ``(feature_id, snapshot_version)`` — a racing double-call still
        # enqueues exactly one row. Passing ``previous_snapshot_version=None``
        # makes the helper unconditionally compare against the empty string
        # (see ``public_dashboard.project_control_plane_snapshot_if_changed``)
        # so the helper always reads the current typed snapshot version and
        # routes through ``ON CONFLICT (event_id) DO NOTHING`` for dedupe.
        # When the outbox handle is ``None`` (the documented disabled-outbox
        # path) we SKIP even calling the helper — this is not a silent
        # degrade, it is the configured-as-disabled path.
        if self._public_dashboard_outbox is not None:
            from ..public_dashboard import project_control_plane_snapshot_if_changed

            await project_control_plane_snapshot_if_changed(
                self._public_dashboard_outbox,
                self,
                row.feature_id,
                previous_snapshot_version=None,
                scope="dashboard",
                conn=conn,
            )

    async def _insert_or_reuse_artifact(
        self,
        conn: Any,
        feature_id: str,
        projection: CompatibilityProjection,
    ) -> int:
        value = _serialize_artifact_value(projection.value)
        existing = await conn.fetchrow(
            """
            SELECT id
            FROM artifacts
            WHERE feature_id = $1 AND key = $2 AND value = $3
            ORDER BY id DESC
            LIMIT 1
            """,
            feature_id,
            projection.key,
            value,
        )
        if existing is not None:
            return int(_record_get(existing, "id"))
        artifact_id = await _fetchval(
            conn,
            """
            INSERT INTO artifacts (feature_id, key, value)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            feature_id,
            projection.key,
            value,
        )
        return int(artifact_id)

    async def _insert_or_reuse_task_contract(
        self,
        conn: Any,
        fields: dict[str, Any],
        *,
        execution_row: ExecutionJournalRow,
    ) -> tuple[TaskDeliverableContract, bool]:
        existing = await self._fetch_task_contract_by_idempotency(
            conn,
            str(fields["feature_id"]),
            str(fields["idempotency_key"]),
        )
        if existing is not None:
            contract = self._task_contract_from_record(existing)
            self._validate_task_contract_record(
                contract,
                execution_row_id=execution_row.id,
                contract_digest=str(fields["contract_digest"]),
            )
            return contract, False
        if str(fields["status"]) == "active":
            await self._lock_task_contract_scope(conn, fields)
            active = await self._fetch_active_task_contract_for_scope(conn, fields)
            if active is not None:
                active_contract = self._task_contract_from_record(active)
                if active_contract.contract_digest == str(fields["contract_digest"]):
                    raise IdempotencyConflict(
                        "task contract active scope already has the same digest "
                        "with a different idempotency key"
                    )
                if active_contract.id is None:
                    raise RuntimeError("active task contract scope row is missing an id")
                await self._supersede_task_contract(conn, int(active_contract.id))
        row = await conn.fetchrow(
            """
            INSERT INTO task_deliverable_contracts (
                feature_id, idempotency_key, execution_journal_row_id,
                dag_sha256, source_dag_artifact_id, source_dag_sha256,
                group_idx, task_id, repo_id, repo_path, required_paths,
                allowed_paths, read_only_paths, forbidden_paths,
                generated_outputs, acceptance_criteria, verification_gates,
                execution_policy, non_goals, dependency_task_ids,
                unknown_write_set, compile_warnings, normalized_contract_json,
                contract_digest, status, payload
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11::jsonb, $12::jsonb, $13::jsonb, $14::jsonb, $15::jsonb,
                $16::jsonb, $17::jsonb, $18::jsonb, $19::jsonb, $20::jsonb,
                $21, $22::jsonb, $23::jsonb, $24, $25, $26::jsonb
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            fields["feature_id"],
            fields["idempotency_key"],
            execution_row.id,
            fields["dag_sha256"],
            fields["source_dag_artifact_id"],
            fields["source_dag_sha256"],
            fields["group_idx"],
            fields["task_id"],
            fields["repo_id"],
            fields["repo_path"],
            stable_json(fields["required_paths"]),
            stable_json(fields["allowed_paths"]),
            stable_json(fields["read_only_paths"]),
            stable_json(fields["forbidden_paths"]),
            stable_json(fields["generated_outputs"]),
            stable_json(fields["acceptance_criteria"]),
            stable_json(fields["verification_gates"]),
            stable_json(fields["execution_policy"]),
            stable_json(fields["non_goals"]),
            stable_json(fields["dependency_task_ids"]),
            fields["unknown_write_set"],
            stable_json(fields["compile_warnings"]),
            stable_json(fields["normalized_contract_json"]),
            fields["contract_digest"],
            fields["status"],
            stable_json(fields["payload"]),
        )
        if row is None:
            existing_after_conflict = await self._fetch_task_contract_by_idempotency(
                conn,
                str(fields["feature_id"]),
                str(fields["idempotency_key"]),
            )
            if existing_after_conflict is None:
                raise RuntimeError("task contract insert conflict could not be reloaded")
            contract = self._task_contract_from_record(existing_after_conflict)
            self._validate_task_contract_record(
                contract,
                execution_row_id=execution_row.id,
                contract_digest=str(fields["contract_digest"]),
            )
            return contract, False
        return self._task_contract_from_record(row), True

    async def _lock_task_contract_scope(self, conn: Any, fields: dict[str, Any]) -> None:
        scope_key = (
            f"task-contract-scope:{fields['dag_sha256']}:"
            f"g{fields['group_idx']}:{fields['task_id']}"
        )
        lock_key = _advisory_lock_key(str(fields["feature_id"]), scope_key)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

    async def _fetch_active_task_contract_for_scope(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM task_deliverable_contracts
            WHERE feature_id = $1
              AND dag_sha256 = $2
              AND group_idx = $3
              AND task_id = $4
              AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            str(fields["feature_id"]),
            str(fields["dag_sha256"]),
            int(fields["group_idx"]),
            str(fields["task_id"]),
        )

    async def _supersede_task_contract(self, conn: Any, contract_id: int) -> None:
        await conn.execute(
            """
            UPDATE task_deliverable_contracts
            SET status = 'superseded',
                updated_at = NOW()
            WHERE id = $1 AND status = 'active'
            """,
            contract_id,
        )

    async def _insert_or_reuse_evidence_node(
        self,
        conn: Any,
        fields: dict[str, Any],
        *,
        execution_row: ExecutionJournalRow,
        kind: str,
    ) -> tuple[EvidenceNode, bool]:
        existing = await self._fetch_evidence_by_idempotency(
            conn,
            str(fields["feature_id"]),
            str(fields["idempotency_key"]),
        )
        if existing is not None:
            evidence = self._evidence_node_from_record(existing)
            self._validate_evidence_node_record(
                evidence,
                execution_row_id=execution_row.id,
                kind=kind,
                content_hash=str(fields["content_hash"]),
            )
            return evidence, False
        row = await conn.fetchrow(
            """
            INSERT INTO evidence_nodes (
                feature_id, idempotency_key, execution_journal_row_id,
                attempt_id, contract_id, snapshot_id, group_idx, stage,
                kind, name, status, deterministic, source_ref, artifact_id,
                artifact_key, event_id, input_refs, output_refs, failure_id,
                verdict_id, content_hash, summary, metadata, payload,
                started_at, finished_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17::jsonb, $18::jsonb,
                $19, $20, $21, $22, $23::jsonb, $24::jsonb,
                COALESCE($25, NOW()), $26
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            fields["feature_id"],
            fields["idempotency_key"],
            execution_row.id,
            fields.get("attempt_id"),
            fields.get("contract_id"),
            fields.get("snapshot_id"),
            fields.get("group_idx"),
            fields.get("stage") or "",
            kind,
            fields.get("name") or "",
            fields.get("status") or "approved",
            bool(fields.get("deterministic", True)),
            fields.get("source_ref") or "",
            fields.get("artifact_id"),
            fields.get("artifact_key") or "",
            fields.get("event_id"),
            stable_json(fields.get("input_refs") or []),
            stable_json(fields.get("output_refs") or []),
            fields.get("failure_id"),
            fields.get("verdict_id"),
            fields["content_hash"],
            fields.get("summary") or "",
            stable_json(fields.get("metadata") or {}),
            stable_json(fields["payload"]),
            fields.get("started_at"),
            fields.get("finished_at"),
        )
        if row is None:
            existing_after_conflict = await self._fetch_evidence_by_idempotency(
                conn,
                str(fields["feature_id"]),
                str(fields["idempotency_key"]),
            )
            if existing_after_conflict is None:
                raise RuntimeError("evidence node insert conflict could not be reloaded")
            evidence = self._evidence_node_from_record(existing_after_conflict)
            self._validate_evidence_node_record(
                evidence,
                execution_row_id=execution_row.id,
                kind=kind,
                content_hash=str(fields["content_hash"]),
            )
            return evidence, False
        return self._evidence_node_from_record(row), True

    async def _insert_legacy_event(
        self,
        conn: Any,
        row: ExecutionJournalRow,
        projection: CompatibilityProjection,
    ) -> int | None:
        event_type, content, metadata = _legacy_event(row, projection)
        if event_type is None:
            return None
        event_id = await _fetchval(
            conn,
            """
            INSERT INTO events (feature_id, event_type, source, content, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            row.feature_id,
            event_type,
            "implementation",
            content,
            stable_json(metadata),
        )
        return int(event_id) if event_id is not None else None

    async def _insert_dashboard_outbox(
        self,
        conn: Any,
        feature_id: str,
        artifact_id: int,
        projection: CompatibilityProjection,
    ) -> str:
        value = _serialize_artifact_value(projection.value)
        event_id = f"artifact-write:{artifact_id}"
        payload = {
            "source_artifact_id": artifact_id,
            "artifact_key": projection.key,
            "sha256": _projection_value_sha256(projection),
            "size_bytes": len(value.encode("utf-8")),
            "content_type": _guess_content_type(projection.key, value),
            "visibility": "internal",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "publish_artifact_candidate": _is_public_artifact_key(projection.key),
        }
        max_content_bytes = _public_dashboard_max_content_bytes()
        if _is_public_artifact_key(projection.key) and max_content_bytes:
            payload["content"] = _bounded_text(value, max_content_bytes)
        inserted = await conn.fetchrow(
            """
            INSERT INTO public_dashboard_outbox (
                event_id, feature_id, event_type, schema_version,
                visibility, payload, status
            )
            VALUES ($1, $2, $3, 1, 'internal', $4::jsonb, 'pending')
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            event_id,
            feature_id,
            "artifact.written",
            stable_json(payload),
        )
        if inserted is None:
            return event_id
        return str(_record_get(inserted, "event_id") or event_id)

    async def _insert_or_reuse_workspace_snapshot(
        self,
        conn: Any,
        evidence: WorkspaceSnapshotEvidence,
        *,
        execution_row: ExecutionJournalRow,
        payload: dict[str, Any],
        registry_digest: str,
        snapshot_digest: str,
        idempotency_key: str,
    ) -> tuple[WorkspaceSnapshotRow, bool]:
        existing = await self._fetch_workspace_snapshot_by_idempotency(
            conn,
            evidence.feature_id,
            idempotency_key,
        )
        if existing is not None:
            row = self._snapshot_from_record(existing)
            self._validate_workspace_snapshot_record(
                row,
                execution_row_id=execution_row.id,
                snapshot_digest=snapshot_digest,
                registry_digest=registry_digest,
                payload=payload,
            )
            return row, False
        row = await conn.fetchrow(
            """
            INSERT INTO workspace_snapshots (
                feature_id, idempotency_key, execution_journal_row_id,
                dag_sha256, group_idx, attempt_id, stage, repo_id,
                canonical_path, registry_digest, snapshot_digest, payload,
                captured_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13)
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            evidence.feature_id,
            idempotency_key,
            execution_row.id,
            evidence.dag_sha256 or str(payload.get("dag_sha256") or ""),
            evidence.group_idx if evidence.group_idx is not None else payload.get("group_idx"),
            evidence.attempt_id if evidence.attempt_id is not None else payload.get("attempt_id"),
            evidence.stage or str(payload.get("stage") or ""),
            evidence.repo_id or str(payload.get("repo_id") or ""),
            evidence.canonical_path or str(payload.get("canonical_path") or ""),
            registry_digest,
            snapshot_digest,
            stable_json(payload),
            evidence.captured_at or _parse_datetime(payload.get("captured_at")),
        )
        if row is None:
            existing_after_conflict = await self._fetch_workspace_snapshot_by_idempotency(
                conn,
                evidence.feature_id,
                idempotency_key,
            )
            if existing_after_conflict is None:
                raise RuntimeError("workspace snapshot insert conflict could not be reloaded")
            snapshot = self._snapshot_from_record(existing_after_conflict)
            self._validate_workspace_snapshot_record(
                snapshot,
                execution_row_id=execution_row.id,
                snapshot_digest=snapshot_digest,
                registry_digest=registry_digest,
                payload=payload,
            )
            return snapshot, False
        return self._snapshot_from_record(row), True

    async def _insert_or_reuse_sandbox_lease(
        self,
        conn: Any,
        fields: dict[str, Any],
        *,
        execution_row: ExecutionJournalRow,
    ) -> tuple[SandboxLease, bool]:
        existing = await self._fetch_sandbox_lease_by_idempotency(
            conn,
            str(fields["feature_id"]),
            str(fields["idempotency_key"]),
        )
        if existing is not None:
            lease = self._sandbox_lease_from_record(existing)
            self._validate_sandbox_lease_record(
                lease,
                execution_row_id=execution_row.id,
                lease_digest=str(fields["lease_digest"]),
            )
            if (
                str(lease.status) in _SANDBOX_TERMINAL_STATUSES
                and str(fields["status"]) not in _SANDBOX_TERMINAL_STATUSES
            ):
                raise IdempotencyConflict(
                    "terminal sandbox lease cannot be reused for active allocation"
                )
            return lease, False
        await self._lock_sandbox_lease_scope(conn, fields)
        active_scope = await self._fetch_sandbox_lease_by_scope(conn, fields)
        if active_scope is not None:
            active = self._sandbox_lease_from_record(active_scope)
            if (
                str(active.status) in _SANDBOX_TERMINAL_STATUSES
                and str(fields["status"]) not in _SANDBOX_TERMINAL_STATUSES
            ):
                raise IdempotencyConflict(
                    "terminal sandbox lease scope cannot be reused for active allocation"
                )
            if active.lease_digest != str(fields["lease_digest"]):
                raise IdempotencyConflict(
                    "sandbox lease scope already exists with a different lease digest"
                )
            return active, False
        row = await conn.fetchrow(
            """
            INSERT INTO sandbox_leases (
                feature_id, idempotency_key, execution_journal_row_id,
                dag_sha256, group_idx, attempt_no, mode, status,
                lease_owner, leased_until, lease_version, base_snapshot_ids,
                sandbox_root, sandbox_id, manifest_path, repo_ids,
                base_commits, task_ids, contract_ids, writable_roots,
                readonly_roots, blocked_roots, patch_summary_ids,
                lease_digest, payload
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12::jsonb, $13, $14, $15, $16::jsonb, $17::jsonb,
                $18::jsonb, $19::jsonb, $20::jsonb, $21::jsonb,
                $22::jsonb, $23::jsonb, $24, $25::jsonb
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            fields["feature_id"],
            fields["idempotency_key"],
            execution_row.id,
            fields["dag_sha256"],
            fields["group_idx"],
            fields["attempt_no"],
            fields["mode"],
            fields["status"],
            fields["lease_owner"],
            fields["leased_until"],
            fields["lease_version"],
            stable_json(fields["base_snapshot_ids"]),
            fields["sandbox_root"],
            fields["sandbox_id"],
            fields["manifest_path"],
            stable_json(fields["repo_ids"]),
            stable_json(fields["base_commits"]),
            stable_json(fields["task_ids"]),
            stable_json(fields["contract_ids"]),
            stable_json(fields["writable_roots"]),
            stable_json(fields["readonly_roots"]),
            stable_json(fields["blocked_roots"]),
            stable_json(fields["patch_summary_ids"]),
            fields["lease_digest"],
            stable_json(fields["payload"]),
        )
        if row is None:
            existing_after_conflict = await self._fetch_sandbox_lease_by_idempotency(
                conn,
                str(fields["feature_id"]),
                str(fields["idempotency_key"]),
            )
            if existing_after_conflict is None:
                raise RuntimeError("sandbox lease insert conflict could not be reloaded")
            lease = self._sandbox_lease_from_record(existing_after_conflict)
            self._validate_sandbox_lease_record(
                lease,
                execution_row_id=execution_row.id,
                lease_digest=str(fields["lease_digest"]),
            )
            if (
                str(lease.status) in _SANDBOX_TERMINAL_STATUSES
                and str(fields["status"]) not in _SANDBOX_TERMINAL_STATUSES
            ):
                raise IdempotencyConflict(
                    "terminal sandbox lease cannot be reused for active allocation"
                )
            return lease, False
        return self._sandbox_lease_from_record(row), True

    async def _insert_or_reuse_sandbox_repo_binding(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> tuple[SandboxRepoBinding, bool]:
        existing = await self._fetch_sandbox_repo_binding_by_idempotency(
            conn,
            str(fields["feature_id"]),
            str(fields["idempotency_key"]),
        )
        if existing is not None:
            binding = self._sandbox_repo_binding_from_record(existing)
            self._validate_sandbox_repo_binding_record(
                binding,
                sandbox_lease_id=int(fields["sandbox_lease_id"]),
                binding_digest=str(fields["binding_digest"]),
            )
            return binding, False
        await self._lock_sandbox_repo_binding_scope(conn, fields)
        scoped = await self._fetch_sandbox_repo_binding_by_scope(conn, fields)
        if scoped is not None:
            binding = self._sandbox_repo_binding_from_record(scoped)
            if binding.binding_digest != str(fields["binding_digest"]):
                raise IdempotencyConflict(
                    "sandbox repo binding scope already exists with a different binding"
                )
            return binding, False
        row = await conn.fetchrow(
            """
            INSERT INTO sandbox_repo_bindings (
                feature_id, idempotency_key, sandbox_lease_id, repo_id,
                sandbox_repo_root, canonical_repo_root, base_snapshot_id,
                base_commit, writable, writable_roots, readonly_roots,
                blocked_canonical_roots, status, binding_digest, payload
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb,
                $11::jsonb, $12::jsonb, $13, $14, $15::jsonb
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            fields["feature_id"],
            fields["idempotency_key"],
            fields["sandbox_lease_id"],
            fields["repo_id"],
            fields["sandbox_repo_root"],
            fields["canonical_repo_root"],
            fields["base_snapshot_id"],
            fields["base_commit"],
            fields["writable"],
            stable_json(fields["writable_roots"]),
            stable_json(fields["readonly_roots"]),
            stable_json(fields["blocked_canonical_roots"]),
            fields["status"],
            fields["binding_digest"],
            stable_json(fields["payload"]),
        )
        if row is None:
            existing_after_conflict = await self._fetch_sandbox_repo_binding_by_idempotency(
                conn,
                str(fields["feature_id"]),
                str(fields["idempotency_key"]),
            )
            if existing_after_conflict is None:
                raise RuntimeError("sandbox repo binding insert conflict could not be reloaded")
            binding = self._sandbox_repo_binding_from_record(existing_after_conflict)
            self._validate_sandbox_repo_binding_record(
                binding,
                sandbox_lease_id=int(fields["sandbox_lease_id"]),
                binding_digest=str(fields["binding_digest"]),
            )
            return binding, False
        return self._sandbox_repo_binding_from_record(row), True

    async def _insert_or_reuse_runtime_workspace_binding(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> tuple[RuntimeWorkspaceBinding, bool]:
        existing = await self._fetch_runtime_workspace_binding_by_idempotency(
            conn,
            str(fields["feature_id"]),
            str(fields["idempotency_key"]),
        )
        if existing is not None:
            binding = self._runtime_workspace_binding_from_record(existing)
            self._validate_runtime_workspace_binding_record(
                binding,
                sandbox_lease_id=int(fields["sandbox_lease_id"]),
                binding_digest=str(fields["binding_digest"]),
            )
            return binding, False
        await self._lock_runtime_workspace_binding_scope(conn, fields)
        scoped = await self._fetch_runtime_workspace_binding_by_scope(conn, fields)
        if scoped is not None:
            binding = self._runtime_workspace_binding_from_record(scoped)
            if binding.binding_digest != str(fields["binding_digest"]):
                raise IdempotencyConflict(
                    "runtime workspace binding scope already exists with a different binding"
                )
            return binding, False
        row = await conn.fetchrow(
            """
            INSERT INTO runtime_workspace_bindings (
                feature_id, idempotency_key, sandbox_lease_id, attempt_id,
                runtime_name, cwd, workspace_override, manifest_path,
                repo_roots, writable_roots, readonly_roots, blocked_roots,
                env, role_metadata, role_metadata_digest, status,
                binding_digest, payload
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb,
                $11::jsonb, $12::jsonb, $13::jsonb, $14::jsonb, $15,
                $16, $17, $18::jsonb
            )
            ON CONFLICT (feature_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            fields["feature_id"],
            fields["idempotency_key"],
            fields["sandbox_lease_id"],
            fields["attempt_id"],
            fields["runtime_name"],
            fields["cwd"],
            fields["workspace_override"],
            fields["manifest_path"],
            stable_json(fields["repo_roots"]),
            stable_json(fields["writable_roots"]),
            stable_json(fields["readonly_roots"]),
            stable_json(fields["blocked_roots"]),
            stable_json(fields["env"]),
            stable_json(fields["role_metadata"]),
            fields["role_metadata_digest"],
            fields["status"],
            fields["binding_digest"],
            stable_json(fields["payload"]),
        )
        if row is None:
            existing_after_conflict = await self._fetch_runtime_workspace_binding_by_idempotency(
                conn,
                str(fields["feature_id"]),
                str(fields["idempotency_key"]),
            )
            if existing_after_conflict is None:
                raise RuntimeError(
                    "runtime workspace binding insert conflict could not be reloaded"
                )
            binding = self._runtime_workspace_binding_from_record(existing_after_conflict)
            self._validate_runtime_workspace_binding_record(
                binding,
                sandbox_lease_id=int(fields["sandbox_lease_id"]),
                binding_digest=str(fields["binding_digest"]),
            )
            return binding, False
        return self._runtime_workspace_binding_from_record(row), True

    async def _fetch_workspace_snapshot_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM workspace_snapshots
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_sandbox_lease_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM sandbox_leases
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_sandbox_lease_by_id(self, conn: Any, lease_id: int) -> SandboxLease | None:
        record = await conn.fetchrow(
            """
            SELECT *
            FROM sandbox_leases
            WHERE id = $1
            LIMIT 1
            """,
            int(lease_id),
        )
        if record is None:
            return None
        return self._sandbox_lease_from_record(record)

    async def _fetch_sandbox_lease_by_scope(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM sandbox_leases
            WHERE feature_id = $1
              AND dag_sha256 = $2
              AND group_idx = $3
              AND attempt_no = $4
              AND mode = $5
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            str(fields["feature_id"]),
            str(fields["dag_sha256"]),
            int(fields["group_idx"]),
            int(fields["attempt_no"]),
            str(fields["mode"]),
        )

    async def _fetch_sandbox_repo_binding_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM sandbox_repo_bindings
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_sandbox_repo_binding_by_scope(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM sandbox_repo_bindings
            WHERE sandbox_lease_id = $1 AND repo_id = $2
            LIMIT 1
            FOR UPDATE
            """,
            int(fields["sandbox_lease_id"]),
            str(fields["repo_id"]),
        )

    async def _fetch_sandbox_repo_bindings_for_lease(
        self,
        conn: Any,
        sandbox_lease_id: int,
    ) -> list[SandboxRepoBinding]:
        rows = await conn.fetch(
            """
            SELECT *
            FROM sandbox_repo_bindings
            WHERE sandbox_lease_id = $1
            ORDER BY repo_id, id
            """,
            int(sandbox_lease_id),
        )
        return [self._sandbox_repo_binding_from_record(row) for row in rows]

    async def _fetch_runtime_workspace_binding_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM runtime_workspace_bindings
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_runtime_workspace_binding_by_scope(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM runtime_workspace_bindings
            WHERE sandbox_lease_id = $1
              AND runtime_name = $2
              AND attempt_id = $3
            LIMIT 1
            FOR UPDATE
            """,
            int(fields["sandbox_lease_id"]),
            str(fields["runtime_name"]),
            int(fields["attempt_id"]),
        )

    async def _fetch_task_contract_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM task_deliverable_contracts
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_task_contract_by_id(
        self,
        conn: Any,
        contract_id: int,
    ) -> TaskDeliverableContract | None:
        record = await conn.fetchrow(
            """
            SELECT *
            FROM task_deliverable_contracts
            WHERE id = $1
            LIMIT 1
            """,
            contract_id,
        )
        if record is None:
            return None
        return self._task_contract_from_record(record)

    async def _fetch_evidence_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM evidence_nodes
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_evidence_node_by_id(
        self,
        conn: Any,
        evidence_id: int,
    ) -> EvidenceNode | None:
        record = await conn.fetchrow(
            """
            SELECT *
            FROM evidence_nodes
            WHERE id = $1
            LIMIT 1
            """,
            evidence_id,
        )
        if record is None:
            return None
        return self._evidence_node_from_record(record)

    async def _fetch_evidence_graph_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM evidence_graphs
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _fetch_verification_graph_projection(
        self,
        conn: Any,
        *,
        feature_id: str,
        projection_key: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
        proof_digest: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM evidence_graphs
            WHERE feature_id = $1
              AND projection_key = $2
              AND dag_sha256 = $3
              AND group_idx = $4
              AND stage = $5
              AND proof_digest = $6
            ORDER BY id DESC
            LIMIT 1
            """,
            feature_id,
            projection_key,
            dag_sha256,
            group_idx,
            stage,
            proof_digest,
        )

    async def _fetch_latest_verification_graph_projection(
        self,
        conn: Any,
        *,
        feature_id: str,
        projection_key: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM evidence_graphs
            WHERE feature_id = $1
              AND projection_key = $2
              AND dag_sha256 = $3
              AND group_idx = $4
              AND stage = $5
            ORDER BY id DESC
            LIMIT 1
            """,
            feature_id,
            projection_key,
            dag_sha256,
            group_idx,
            stage,
        )

    async def _fetch_verification_graph_required_edges(
        self,
        conn: Any,
        *,
        graph_id: int,
        limit: int,
    ) -> list[Any]:
        rows = await conn.fetch(
            """
            SELECT *
            FROM evidence_edges
            WHERE evidence_graph_id = $1 AND required = TRUE
            ORDER BY id ASC
            LIMIT $2
            """,
            graph_id,
            limit,
        )
        return list(rows)

    async def _fetch_evidence_edge_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM evidence_edges
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    def _validate_evidence_graph_record(
        self,
        record: Any,
        *,
        row_id: int,
        aggregate_node_id: int,
        projection: VerificationGraphProjection,
        projection_sha256: str,
        graph_payload_digest: str,
    ) -> None:
        if int(_record_get(record, "execution_journal_row_id")) != row_id:
            raise IdempotencyConflict(
                "verification graph idempotency key belongs to a different typed row"
            )
        if int(_record_get(record, "aggregate_evidence_node_id")) != aggregate_node_id:
            raise IdempotencyConflict(
                "verification graph idempotency key belongs to a different aggregate node"
            )
        checks = {
            "projection_key": projection.projection_key,
            "projection_sha256": projection_sha256,
            "dag_sha256": projection.dag_sha256,
            "stage": projection.stage,
            "proof_digest": projection.proof_digest,
            "graph_payload_digest": graph_payload_digest,
        }
        for field_name, expected in checks.items():
            if str(_record_get(record, field_name) or "") != str(expected or ""):
                raise IdempotencyConflict(
                    f"verification graph idempotency key was reused with a different {field_name}"
                )
        if _record_get(record, "group_idx") != projection.group_idx:
            raise IdempotencyConflict(
                "verification graph idempotency key was reused with a different group_idx"
            )

    def _validate_evidence_edge_record(
        self,
        record: Any,
        *,
        graph_id: int,
        graph_edge_id: str,
        edge_digest: str,
    ) -> None:
        if int(_record_get(record, "evidence_graph_id")) != graph_id:
            raise IdempotencyConflict(
                "verification graph edge idempotency key belongs to a different graph"
            )
        if str(_record_get(record, "graph_edge_id")) != graph_edge_id:
            raise IdempotencyConflict(
                "verification graph edge idempotency key belongs to a different edge"
            )
        if str(_record_get(record, "edge_digest")) != edge_digest:
            raise IdempotencyConflict(
                "verification graph edge idempotency key was reused with a different edge"
            )

    def _validate_workspace_snapshot_record(
        self,
        row: WorkspaceSnapshotRow,
        *,
        execution_row_id: int,
        snapshot_digest: str,
        registry_digest: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if row.execution_journal_row_id != execution_row_id:
            raise IdempotencyConflict(
                "workspace snapshot idempotency key belongs to a different typed row"
            )
        if row.snapshot_digest != snapshot_digest and (
            # A stored snapshot whose digest differs ONLY because it was written
            # before a volatile field (attempt_id) was excluded is the same
            # workspace state. Recompute the stable digest from the stored full
            # payload; accept if it now matches, otherwise the snapshot genuinely
            # differs and the conflict is real.
            _workspace_snapshot_digest(row.payload) != snapshot_digest
        ):
            raise IdempotencyConflict(
                "workspace snapshot idempotency key was reused with a different snapshot "
                f"(differing stable keys: {_workspace_snapshot_differing_keys(row.payload, payload)})"
            )
        if row.registry_digest != registry_digest:
            raise IdempotencyConflict(
                "workspace snapshot registry digest mismatch"
            )

    def _validate_sandbox_lease_record(
        self,
        row: SandboxLease,
        *,
        execution_row_id: int,
        lease_digest: str,
    ) -> None:
        if row.execution_journal_row_id != execution_row_id:
            raise IdempotencyConflict(
                "sandbox lease idempotency key belongs to a different typed row"
            )
        if row.lease_digest != lease_digest:
            raise IdempotencyConflict(
                "sandbox lease idempotency key was reused with a different lease"
            )

    def _validate_sandbox_repo_binding_record(
        self,
        row: SandboxRepoBinding,
        *,
        sandbox_lease_id: int,
        binding_digest: str,
    ) -> None:
        if row.sandbox_lease_id != sandbox_lease_id:
            raise IdempotencyConflict(
                "sandbox repo binding idempotency key belongs to a different lease"
            )
        if row.binding_digest != binding_digest:
            raise IdempotencyConflict(
                "sandbox repo binding idempotency key was reused with a different binding"
            )

    def _validate_runtime_workspace_binding_record(
        self,
        row: RuntimeWorkspaceBinding,
        *,
        sandbox_lease_id: int,
        binding_digest: str,
    ) -> None:
        if row.sandbox_lease_id != sandbox_lease_id:
            raise IdempotencyConflict(
                "runtime workspace binding idempotency key belongs to a different lease"
            )
        if row.binding_digest != binding_digest:
            raise IdempotencyConflict(
                "runtime workspace binding idempotency key was reused with a different binding"
            )

    def _validate_task_contract_record(
        self,
        row: TaskDeliverableContract,
        *,
        execution_row_id: int,
        contract_digest: str,
    ) -> None:
        if row.execution_journal_row_id != execution_row_id:
            raise IdempotencyConflict(
                "task contract idempotency key belongs to a different typed row"
            )
        if row.contract_digest != contract_digest:
            raise IdempotencyConflict(
                "task contract idempotency key was reused with a different contract"
            )

    def _validate_evidence_node_record(
        self,
        row: EvidenceNode,
        *,
        execution_row_id: int,
        kind: str,
        content_hash: str,
    ) -> None:
        if row.execution_journal_row_id != execution_row_id:
            raise IdempotencyConflict(
                "evidence idempotency key belongs to a different typed row"
            )
        if row.kind != kind:
            raise IdempotencyConflict(
                "evidence idempotency key belongs to a different evidence kind"
            )
        if row.content_hash != content_hash:
            raise IdempotencyConflict(
                "evidence idempotency key was reused with different content"
            )

    async def _fetch_projection_by_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> Any | None:
        return await conn.fetchrow(
            """
            SELECT *
            FROM execution_artifact_projections
            WHERE feature_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            feature_id,
            idempotency_key,
        )

    async def _lock_projection_idempotency(
        self,
        conn: Any,
        feature_id: str,
        idempotency_key: str,
    ) -> None:
        lock_key = _advisory_lock_key(feature_id, idempotency_key)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

    async def _lock_sandbox_lease_scope(self, conn: Any, fields: dict[str, Any]) -> None:
        scope_key = (
            f"sandbox-lease:{fields['dag_sha256']}:"
            f"g{fields['group_idx']}:attempt-{fields['attempt_no']}:{fields['mode']}"
        )
        lock_key = _advisory_lock_key(str(fields["feature_id"]), scope_key)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

    async def _lock_sandbox_repo_binding_scope(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> None:
        scope_key = f"sandbox-repo:{fields['sandbox_lease_id']}:{fields['repo_id']}"
        lock_key = _advisory_lock_key(str(fields["feature_id"]), scope_key)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

    async def _lock_runtime_workspace_binding_scope(
        self,
        conn: Any,
        fields: dict[str, Any],
    ) -> None:
        scope_key = (
            f"runtime-binding:{fields['sandbox_lease_id']}:"
            f"{fields['runtime_name']}:{fields['attempt_id']}"
        )
        lock_key = _advisory_lock_key(str(fields["feature_id"]), scope_key)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

    def _validate_projection_record(
        self,
        record: Any,
        projection: CompatibilityProjection,
        *,
        typed_row_id: int,
        source_table: str | None = None,
        source_id: int | None = None,
        projection_owner: str | None = None,
        projection_kind: str | None = None,
        projection_payload: dict[str, Any] | None = None,
    ) -> None:
        existing_sha = (
            _record_get(record, "projection_sha256")
            or _record_get(record, "body_sha256")
            or _record_get(record, "projection_digest")
        )
        if existing_sha != _projection_value_sha256(projection):
            raise IdempotencyConflict(
                "projection idempotency key was reused with a different projection"
            )
        if int(_record_get(record, "typed_row_id")) != typed_row_id:
            raise IdempotencyConflict(
                "projection idempotency key belongs to a different typed row"
            )
        if str(_record_get(record, "projection_key")) != projection.key:
            raise IdempotencyConflict(
                "projection idempotency key belongs to a different projection key"
            )
        if source_table is not None and str(_record_get(record, "source_table") or "") != source_table:
            raise IdempotencyConflict(
                "projection idempotency key belongs to a different projection source"
            )
        if source_id is not None:
            existing_source_id = _optional_int(_record_get(record, "source_id"))
            if existing_source_id != int(source_id):
                raise IdempotencyConflict(
                    "projection idempotency key belongs to a different projection source"
                )
        if projection_owner is not None and str(_record_get(record, "projection_owner") or "") != projection_owner:
            raise IdempotencyConflict(
                "projection idempotency key belongs to a different projection owner"
            )
        if projection_kind is not None and str(_record_get(record, "projection_kind") or "") != projection_kind:
            raise IdempotencyConflict(
                "projection idempotency key belongs to a different projection kind"
            )
        expected_payload = projection_payload or {}
        if expected_payload:
            existing_payload = _decode_json(_record_get(record, "payload"), {})
            if not isinstance(existing_payload, dict):
                raise IdempotencyConflict(
                    "projection idempotency key belongs to an invalid projection payload"
                )
            for key in ("evidence_kind", "evidence_node_id"):
                if key not in expected_payload:
                    continue
                if existing_payload.get(key) != expected_payload.get(key):
                    raise IdempotencyConflict(
                        "projection idempotency key belongs to a different projection source payload"
                    )

    async def _fetch_projection_links(self, conn: Any, typed_row_id: int) -> list[ProjectionLink]:
        rows = await conn.fetch(
            """
            SELECT *
            FROM execution_artifact_projections
            WHERE typed_row_id = $1
            ORDER BY id
            """,
            typed_row_id,
        )
        return [self._link_from_record(row) for row in rows]

    def _row_from_record(self, row: Any) -> ExecutionJournalRow:
        payload = _decode_json(_record_get(row, "payload"), {})
        return ExecutionJournalRow(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            entry_type=str(_record_get(row, "entry_type")),
            status=str(_record_get(row, "status")),
            request_digest=str(_record_get(row, "request_digest")),
            payload=payload,
            actor=str(_record_get(row, "actor") or ""),
            dag_sha256=str(_record_get(row, "dag_sha256") or ""),
            group_idx=_record_get(row, "group_idx"),
            task_id=_record_get(row, "task_id"),
            requires_legacy_visibility=bool(_record_get(row, "requires_legacy_visibility")),
            projection_mode=str(_record_get(row, "projection_mode") or "legacy_compatibility"),
            dispatcher_state=str(_record_get(row, "dispatcher_state") or "requested"),
            runtime=str(_record_get(row, "runtime") or ""),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _link_from_record(self, row: Any) -> ProjectionLink:
        return ProjectionLink(
            id=int(_record_get(row, "id")),
            typed_row_id=int(_record_get(row, "typed_row_id")),
            artifact_id=int(_record_get(row, "artifact_id")),
            feature_id=str(_record_get(row, "feature_id")),
            projection_key=str(_record_get(row, "projection_key")),
            projection_sha256=str(
                _record_get(row, "projection_sha256")
                or _record_get(row, "body_sha256")
                or _record_get(row, "projection_digest")
            ),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            legacy_event_id=_record_get(row, "legacy_event_id"),
            dashboard_outbox_event_id=_record_get(row, "dashboard_outbox_event_id"),
            source_table=str(_record_get(row, "source_table") or "execution_journal_rows"),
            source_id=_record_get(row, "source_id"),
            projection_owner=str(_record_get(row, "projection_owner") or ""),
            projection_kind=str(_record_get(row, "projection_kind") or ""),
            payload=_decode_json(_record_get(row, "payload"), {}),
            created_at=_record_get(row, "created_at"),
        )

    def _snapshot_from_record(self, row: Any) -> WorkspaceSnapshotRow:
        return WorkspaceSnapshotRow(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            execution_journal_row_id=int(_record_get(row, "execution_journal_row_id")),
            dag_sha256=str(_record_get(row, "dag_sha256") or ""),
            group_idx=_record_get(row, "group_idx"),
            attempt_id=_record_get(row, "attempt_id"),
            stage=str(_record_get(row, "stage") or ""),
            repo_id=str(_record_get(row, "repo_id") or ""),
            canonical_path=str(_record_get(row, "canonical_path") or ""),
            registry_digest=str(_record_get(row, "registry_digest") or ""),
            snapshot_digest=str(_record_get(row, "snapshot_digest")),
            payload=_decode_json(_record_get(row, "payload"), {}),
            captured_at=_record_get(row, "captured_at"),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _sandbox_lease_from_record(self, row: Any) -> SandboxLease:
        return SandboxLease(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            execution_journal_row_id=_record_get(row, "execution_journal_row_id"),
            dag_sha256=str(_record_get(row, "dag_sha256") or ""),
            group_idx=int(_record_get(row, "group_idx")),
            attempt_no=int(_record_get(row, "attempt_no")),
            mode=str(_record_get(row, "mode") or "wave"),
            status=str(_record_get(row, "status") or "allocating"),
            lease_owner=str(_record_get(row, "lease_owner") or ""),
            owner=str(_record_get(row, "lease_owner") or ""),
            leased_until=_record_get(row, "leased_until"),
            expires_at=_record_get(row, "leased_until"),
            lease_version=int(_record_get(row, "lease_version") or 0),
            base_snapshot_ids=[
                int(item) for item in _decode_json(_record_get(row, "base_snapshot_ids"), [])
            ],
            sandbox_root=str(_record_get(row, "sandbox_root") or ""),
            root=str(_record_get(row, "sandbox_root") or ""),
            sandbox_id=str(_record_get(row, "sandbox_id") or ""),
            manifest_path=str(_record_get(row, "manifest_path") or ""),
            repo_ids=[str(item) for item in _decode_json(_record_get(row, "repo_ids"), [])],
            repo_roots={},
            base_commits={
                str(key): str(value)
                for key, value in _decode_json(_record_get(row, "base_commits"), {}).items()
            },
            task_ids=[str(item) for item in _decode_json(_record_get(row, "task_ids"), [])],
            contract_ids=[
                int(item) for item in _decode_json(_record_get(row, "contract_ids"), [])
            ],
            writable_roots=[
                str(item) for item in _decode_json(_record_get(row, "writable_roots"), [])
            ],
            readonly_roots=[
                str(item) for item in _decode_json(_record_get(row, "readonly_roots"), [])
            ],
            blocked_roots=[
                str(item) for item in _decode_json(_record_get(row, "blocked_roots"), [])
            ],
            patch_summary_ids=[
                int(item) for item in _decode_json(_record_get(row, "patch_summary_ids"), [])
            ],
            lease_digest=str(_record_get(row, "lease_digest")),
            payload=_decode_json(_record_get(row, "payload"), {}),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _sandbox_repo_binding_from_record(self, row: Any) -> SandboxRepoBinding:
        return SandboxRepoBinding(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            sandbox_lease_id=int(_record_get(row, "sandbox_lease_id")),
            repo_id=str(_record_get(row, "repo_id")),
            sandbox_repo_root=str(_record_get(row, "sandbox_repo_root") or ""),
            canonical_repo_root=str(_record_get(row, "canonical_repo_root") or ""),
            base_snapshot_id=int(_record_get(row, "base_snapshot_id") or 0),
            base_commit=str(_record_get(row, "base_commit") or ""),
            writable=bool(_record_get(row, "writable")),
            writable_roots=[
                str(item) for item in _decode_json(_record_get(row, "writable_roots"), [])
            ],
            readonly_roots=[
                str(item) for item in _decode_json(_record_get(row, "readonly_roots"), [])
            ],
            blocked_canonical_roots=[
                str(item)
                for item in _decode_json(_record_get(row, "blocked_canonical_roots"), [])
            ],
            status=str(_record_get(row, "status") or "active"),
            binding_digest=str(_record_get(row, "binding_digest")),
            payload=_decode_json(_record_get(row, "payload"), {}),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _runtime_workspace_binding_from_record(self, row: Any) -> RuntimeWorkspaceBinding:
        return RuntimeWorkspaceBinding(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            sandbox_lease_id=int(_record_get(row, "sandbox_lease_id")),
            attempt_id=int(_record_get(row, "attempt_id") or 0),
            runtime_name=str(_record_get(row, "runtime_name") or ""),
            runtime=str(_record_get(row, "runtime_name") or ""),
            cwd=str(_record_get(row, "cwd") or ""),
            workspace_override=str(_record_get(row, "workspace_override") or ""),
            manifest_path=str(_record_get(row, "manifest_path") or ""),
            repo_roots={
                str(key): str(value)
                for key, value in _decode_json(_record_get(row, "repo_roots"), {}).items()
            },
            writable_roots=[
                str(item) for item in _decode_json(_record_get(row, "writable_roots"), [])
            ],
            readonly_roots=[
                str(item) for item in _decode_json(_record_get(row, "readonly_roots"), [])
            ],
            blocked_roots=[
                str(item) for item in _decode_json(_record_get(row, "blocked_roots"), [])
            ],
            env={
                str(key): str(value)
                for key, value in _decode_json(_record_get(row, "env"), {}).items()
            },
            role_metadata=_decode_json(_record_get(row, "role_metadata"), {}),
            role_metadata_digest=str(_record_get(row, "role_metadata_digest") or ""),
            status=str(_record_get(row, "status") or "bound"),
            binding_digest=str(_record_get(row, "binding_digest")),
            payload=_decode_json(_record_get(row, "payload"), {}),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _task_contract_from_record(self, row: Any) -> TaskDeliverableContract:
        return TaskDeliverableContract(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            execution_journal_row_id=_record_get(row, "execution_journal_row_id"),
            dag_sha256=str(_record_get(row, "dag_sha256") or ""),
            source_dag_artifact_id=_record_get(row, "source_dag_artifact_id"),
            source_dag_sha256=str(_record_get(row, "source_dag_sha256") or ""),
            group_idx=int(_record_get(row, "group_idx")),
            task_id=str(_record_get(row, "task_id")),
            repo_id=str(_record_get(row, "repo_id") or ""),
            repo_path=str(_record_get(row, "repo_path") or ""),
            required_paths=_decode_json(_record_get(row, "required_paths"), []),
            allowed_paths=_decode_json(_record_get(row, "allowed_paths"), []),
            read_only_paths=_decode_json(_record_get(row, "read_only_paths"), []),
            forbidden_paths=_decode_json(_record_get(row, "forbidden_paths"), []),
            generated_outputs=_decode_json(_record_get(row, "generated_outputs"), []),
            acceptance_criteria=_decode_json(_record_get(row, "acceptance_criteria"), []),
            verification_gates=_decode_json(_record_get(row, "verification_gates"), []),
            execution_policy=_decode_json(_record_get(row, "execution_policy"), {}),
            non_goals=_decode_json(_record_get(row, "non_goals"), []),
            dependency_task_ids=_decode_json(_record_get(row, "dependency_task_ids"), []),
            unknown_write_set=bool(_record_get(row, "unknown_write_set")),
            compile_warnings=_decode_json(_record_get(row, "compile_warnings"), []),
            normalized_contract_json=_decode_json(
                _record_get(row, "normalized_contract_json"),
                {},
            ),
            contract_digest=str(_record_get(row, "contract_digest")),
            status=str(_record_get(row, "status") or "active"),
            payload=_decode_json(_record_get(row, "payload"), {}),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _evidence_node_from_record(self, row: Any) -> EvidenceNode:
        return EvidenceNode(
            id=int(_record_get(row, "id")),
            feature_id=str(_record_get(row, "feature_id")),
            idempotency_key=str(_record_get(row, "idempotency_key")),
            execution_journal_row_id=_record_get(row, "execution_journal_row_id"),
            attempt_id=_record_get(row, "attempt_id"),
            contract_id=_record_get(row, "contract_id"),
            snapshot_id=_record_get(row, "snapshot_id"),
            group_idx=_record_get(row, "group_idx"),
            stage=str(_record_get(row, "stage") or ""),
            kind=str(_record_get(row, "kind")),
            name=str(_record_get(row, "name") or ""),
            status=str(_record_get(row, "status")),
            deterministic=bool(_record_get(row, "deterministic")),
            source_ref=str(_record_get(row, "source_ref") or ""),
            artifact_id=_record_get(row, "artifact_id"),
            artifact_key=str(_record_get(row, "artifact_key") or ""),
            event_id=_record_get(row, "event_id"),
            input_refs=_decode_json(_record_get(row, "input_refs"), []),
            output_refs=_decode_json(_record_get(row, "output_refs"), []),
            failure_id=_record_get(row, "failure_id"),
            verdict_id=_record_get(row, "verdict_id"),
            content_hash=str(_record_get(row, "content_hash")),
            summary=str(_record_get(row, "summary") or ""),
            metadata=_decode_json(_record_get(row, "metadata"), {}),
            payload=_decode_json(_record_get(row, "payload"), {}),
            started_at=_record_get(row, "started_at"),
            finished_at=_record_get(row, "finished_at"),
            created_at=_record_get(row, "created_at"),
            updated_at=_record_get(row, "updated_at"),
        )

    def _validate_verification_graph_projection(
        self,
        projection: VerificationGraphProjection,
    ) -> None:
        payload = projection.graph_payload
        _validate_verification_graph_lineage(projection)
        if not projection.projection_key.startswith("dag-verify:"):
            raise UnsupportedCompatibilityProjection(
                f"verification graph projection must use dag-verify:*; got {projection.projection_key}"
            )
        compat = payload.get("compatibility_projection")
        if not isinstance(compat, dict) or compat.get("source_kind") != "aggregate_verdict":
            raise MissingRequiredProjection(
                "dag-verify compatibility projection must cite aggregate_verdict source_kind"
            )
        if compat.get("key") != projection.projection_key:
            raise MissingRequiredProjection(
                "verification graph compatibility projection key mismatch"
            )
        aggregate = payload.get("aggregate")
        if not isinstance(aggregate, dict) or aggregate.get("node_id") is None:
            raise MissingRequiredProjection(
                "verification graph projection requires aggregate node metadata"
            )
        aggregate_node = payload.get("aggregate_node")
        if not isinstance(aggregate_node, dict) or aggregate_node.get("id") is None:
            raise MissingRequiredProjection(
                "verification graph projection requires aggregate node metadata"
            )
        aggregate_node_id = _optional_int(aggregate_node.get("id"))
        if aggregate_node_id is None:
            raise MissingRequiredProjection(
                "verification graph projection aggregate node id must be an integer"
            )
        aggregate_metadata_node_id = _optional_int(aggregate.get("node_id"))
        if aggregate_metadata_node_id is None:
            raise MissingRequiredProjection(
                "verification graph projection aggregate metadata node id must be an integer"
            )
        if aggregate_node_id != aggregate_metadata_node_id:
            raise MissingRequiredProjection(
                "verification graph projection aggregate node id does not match aggregate metadata"
            )
        proof = payload.get("proof")
        if projection.approved:
            if not isinstance(proof, dict) or not proof.get("proof_digest"):
                raise MissingRequiredProjection(
                    "approved verification graph projection requires aggregate proof"
                )
            projection_keys = proof.get("projection_keys")
            if not isinstance(projection_keys, list) or projection.projection_key not in projection_keys:
                raise MissingRequiredProjection(
                    "approved verification graph proof must cite dag-verify projection key"
                )
            _validate_verification_graph_approval_proof(projection, proof)


def _validate_verification_graph_lineage(
    projection: VerificationGraphProjection,
) -> None:
    payload = projection.graph_payload
    lineage_checks = {
        "feature_id": projection.feature_id,
        "dag_sha256": projection.dag_sha256,
        "group_idx": projection.group_idx,
        "stage": projection.stage,
    }
    for field_name, expected in lineage_checks.items():
        actual = payload.get(field_name)
        if actual is not None and str(actual) != str(expected):
            raise MissingRequiredProjection(
                f"verification graph payload {field_name} does not match projection"
            )

    for raw_node in _verification_graph_payload_nodes(payload):
        for field_name in ("feature_id", "dag_sha256", "group_idx", "stage"):
            if field_name not in raw_node:
                continue
            actual = raw_node.get(field_name)
            expected = lineage_checks[field_name]
            if actual is not None and str(actual) != str(expected):
                node_id = raw_node.get("id")
                raise MissingRequiredProjection(
                    f"verification graph node {node_id} has stale {field_name} lineage"
                )


def _validate_verification_graph_approval_proof(
    projection: VerificationGraphProjection,
    proof: dict[str, Any],
) -> None:
    payload = projection.graph_payload
    aggregate = _json_dict(payload.get("aggregate"))
    if not aggregate.get("approved"):
        raise MissingRequiredProjection(
            "approved verification graph proof requires approved aggregate metadata"
        )
    required_gate_ids = _int_list(aggregate.get("required_gate_node_ids"))
    raw_node_id = _optional_int(aggregate.get("raw_verdict_node_id"))
    required_lens_ids = _int_list(aggregate.get("required_lens_node_ids"))
    if raw_node_id is None:
        raise MissingRequiredProjection(
            "approved verification graph proof requires raw verifier node"
        )
    required_lineage_ids = _int_list(proof.get("required_lineage_node_ids"))
    required_node_ids = sorted(set([
        *required_gate_ids,
        raw_node_id,
        *required_lens_ids,
        *required_lineage_ids,
    ]))
    node_by_id = _verification_graph_node_map(payload)
    missing_nodes = [node_id for node_id in required_node_ids if node_id not in node_by_id]
    if missing_nodes:
        raise MissingRequiredProjection(
            f"approved verification graph proof is missing required nodes: {missing_nodes}"
        )
    non_approved = [
        node_id for node_id in required_node_ids
        if str(node_by_id[node_id].get("status") or "") != "approved"
    ]
    if non_approved:
        raise MissingRequiredProjection(
            f"approved verification graph proof has non-approved required nodes: {non_approved}"
        )
    _validate_verification_graph_required_node_roles(
        node_kind_by_id={
            node_id: str(node_by_id[node_id].get("kind") or "")
            for node_id in required_node_ids
        },
        raw_node_id=raw_node_id,
        required_gate_ids=required_gate_ids,
        required_lens_ids=required_lens_ids,
        context="approved verification graph proof",
    )
    verifier_compatibility_links = _verification_graph_required_compatibility_links(
        payload,
        required_verifier_node_ids=[raw_node_id, *required_lens_ids],
        context="approved verification graph proof",
    )
    proof_compatibility_links = _verification_graph_verifier_compatibility_links(proof)
    if proof_compatibility_links != verifier_compatibility_links:
        raise MissingRequiredProjection(
            "approved verification graph proof verifier compatibility links do not match graph payload"
        )

    expected_status_digest = stable_digest(
        [
            {"node_id": node_id, "status": str(node_by_id[node_id].get("status"))}
            for node_id in sorted(required_node_ids)
        ]
    )
    if str(proof.get("required_node_status_digest") or "") != expected_status_digest:
        raise MissingRequiredProjection(
            "approved verification graph proof has stale required node status digest"
        )

    graph_payload_digest = str(proof.get("graph_payload_digest") or "")
    if not graph_payload_digest:
        raise MissingRequiredProjection(
            "approved verification graph proof requires graph payload digest"
        )
    if graph_payload_digest != _verification_graph_payload_digest_for_proof(payload):
        raise MissingRequiredProjection(
            "approved verification graph proof graph payload digest does not match graph"
        )

    required_edge_ids = _int_list(proof.get("required_edge_ids"))
    payload_required_edge_ids = _int_list(
        _verification_graph_payload_required_edge_ids(payload)
    )
    if sorted(required_edge_ids) != sorted(payload_required_edge_ids):
        raise MissingRequiredProjection(
            "approved verification graph proof required edges do not match graph payload"
        )
    aggregate_node_id = _optional_int(aggregate.get("node_id"))
    edge_by_id = {
        str(edge.get("id")): edge
        for edge in _json_list(payload.get("edges"))
        if isinstance(edge, dict) and edge.get("id") is not None
    }
    missing_edges = [
        edge_id for edge_id in required_edge_ids
        if str(edge_id) not in edge_by_id
    ]
    if missing_edges:
        raise MissingRequiredProjection(
            f"approved verification graph proof is missing required edge rows: {missing_edges}"
        )
    malformed_edges: list[int] = []
    for edge_id in required_edge_ids:
        edge = edge_by_id.get(str(edge_id), {})
        from_node_id = _optional_int(
            edge.get("from_node_id") or edge.get("from") or edge.get("source")
        )
        to_node_id = _optional_int(
            edge.get("to_node_id") or edge.get("to") or edge.get("target")
        )
        if (
            str(edge.get("kind") or "") != "requires"
            or from_node_id not in required_node_ids
            or (
                to_node_id != aggregate_node_id
                and to_node_id not in required_node_ids
            )
        ):
            malformed_edges.append(edge_id)
    if malformed_edges:
        raise MissingRequiredProjection(
            f"approved verification graph proof has invalid required edge lineage: {malformed_edges}"
        )
    required_edge_paths = [
        (
            _optional_int(
                edge_by_id[str(edge_id)].get("from_node_id")
                or edge_by_id[str(edge_id)].get("from")
                or edge_by_id[str(edge_id)].get("source")
            ),
            _optional_int(
                edge_by_id[str(edge_id)].get("to_node_id")
                or edge_by_id[str(edge_id)].get("to")
                or edge_by_id[str(edge_id)].get("target")
            ),
        )
        for edge_id in required_edge_ids
    ]
    missing_reachability = _verification_graph_missing_required_reachability(
        required_node_ids=required_node_ids,
        aggregate_node_id=aggregate_node_id,
        required_edges=[
            (from_node_id, to_node_id)
            for from_node_id, to_node_id in required_edge_paths
            if from_node_id is not None and to_node_id is not None
        ],
    )
    cyclic_path = _verification_graph_required_edge_cycle(
        required_edges=[
            (from_node_id, to_node_id)
            for from_node_id, to_node_id in required_edge_paths
            if from_node_id is not None and to_node_id is not None
        ]
    )
    if cyclic_path:
        raise MissingRequiredProjection(
            "approved verification graph proof has cyclic required edges: "
            f"{cyclic_path}"
        )
    if missing_reachability:
        raise MissingRequiredProjection(
            "approved verification graph proof has missing required-node reachability: "
            f"{missing_reachability}"
        )

    expected_projection_keys = sorted({projection.projection_key})
    projection_keys = sorted(str(key) for key in _json_list(proof.get("projection_keys")))
    if projection_keys != expected_projection_keys:
        raise MissingRequiredProjection(
            "approved verification graph proof projection keys do not match projection"
        )

    proof_binding = {
        "feature_id": projection.feature_id,
        "dag_sha256": projection.dag_sha256,
        "group_idx": projection.group_idx,
        "stage": projection.stage,
    }
    for field_name, expected in proof_binding.items():
        if field_name not in proof or str(proof.get(field_name)) != str(expected):
            raise MissingRequiredProjection(
                f"approved verification graph proof must bind {field_name}"
            )

    if _optional_int(proof.get("aggregate_node_id")) != aggregate_node_id:
        raise MissingRequiredProjection(
            "approved verification graph proof aggregate node does not match graph"
        )
    if _optional_int(proof.get("aggregate_verdict_id")) != _optional_int(
        aggregate.get("merged_verdict_id")
    ):
        raise MissingRequiredProjection(
            "approved verification graph proof aggregate verdict does not match graph"
        )
    if _optional_int(proof.get("raw_verifier_node_id")) != raw_node_id:
        raise MissingRequiredProjection(
            "approved verification graph proof raw verifier does not match graph"
        )
    if sorted(_int_list(proof.get("required_lens_node_ids"))) != sorted(required_lens_ids):
        raise MissingRequiredProjection(
            "approved verification graph proof required lenses do not match graph"
        )

    recomputed = stable_digest({
        "feature_id": projection.feature_id,
        "dag_sha256": projection.dag_sha256,
        "group_idx": projection.group_idx,
        "stage": projection.stage,
        "aggregate_node_id": aggregate_node_id,
        "aggregate_verdict_id": _optional_int(aggregate.get("merged_verdict_id")),
        "required_edge_ids": sorted(required_edge_ids),
        "required_lineage_node_ids": required_node_ids,
        "required_node_status_digest": expected_status_digest,
        "raw_verifier_node_id": raw_node_id,
        "required_lens_node_ids": required_lens_ids,
        "projection_keys": expected_projection_keys,
        "verifier_compatibility_links": verifier_compatibility_links,
        "graph_payload_digest": graph_payload_digest,
    })
    if str(proof.get("proof_digest") or "") != recomputed:
        raise MissingRequiredProjection(
            "approved verification graph proof digest does not match graph"
        )
    if projection.proof_digest and projection.proof_digest != recomputed:
        raise MissingRequiredProjection(
            "verification graph projection proof digest does not match graph"
        )


def _validate_verification_graph_required_node_roles(
    *,
    node_kind_by_id: dict[int, str],
    raw_node_id: int | None,
    required_gate_ids: list[int],
    required_lens_ids: list[int],
    context: str,
    require_raw_verifier: bool = True,
) -> None:
    mismatches: list[str] = []
    if raw_node_id is None:
        if require_raw_verifier:
            mismatches.append("raw_verdict_node_id <missing> must be raw_verifier")
    else:
        raw_kind = node_kind_by_id.get(raw_node_id, "")
        if raw_kind != "raw_verifier":
            mismatches.append(
                f"raw_verdict_node_id {raw_node_id} must be raw_verifier "
                f"(got {raw_kind or '<missing>'})"
            )

    for node_id in sorted(set(required_gate_ids)):
        gate_kind = node_kind_by_id.get(node_id, "")
        if gate_kind not in VERIFICATION_GRAPH_REQUIRED_GATE_NODE_KINDS:
            mismatches.append(
                f"required_gate_node_id {node_id} must be a gate node "
                f"(got {gate_kind or '<missing>'})"
            )

    for node_id in sorted(set(required_lens_ids)):
        lens_kind = node_kind_by_id.get(node_id, "")
        if lens_kind != "expanded_lens":
            mismatches.append(
                f"required_lens_node_id {node_id} must be expanded_lens "
                f"(got {lens_kind or '<missing>'})"
            )

    if mismatches:
        raise MissingRequiredProjection(
            f"{context} has required node role mismatches: {mismatches}"
        )


def _verification_graph_required_compatibility_links(
    payload: dict[str, Any],
    *,
    required_verifier_node_ids: list[int],
    context: str,
) -> dict[str, dict[str, Any]]:
    available = _verification_graph_verifier_compatibility_links(payload)
    required: dict[str, dict[str, Any]] = {}
    missing: list[int] = []
    invalid: list[str] = []
    for node_id in sorted(set(required_verifier_node_ids)):
        key = str(node_id)
        link = available.get(key)
        if link is None:
            missing.append(node_id)
            continue
        for field_name in (
            "raw_output_verifier_node_id",
            "parsed_verdict_verifier_node_id",
            "projection_verifier_node_id",
        ):
            if _optional_int(link.get(field_name)) != node_id:
                invalid.append(f"{key}.{field_name}")
        if _optional_int(link.get("context_package_node_id")) is None:
            invalid.append(f"{key}.context_package_node_id")
        if link.get("context_hash_matches") is not True:
            invalid.append(f"{key}.context_hash_matches")
        required[key] = link
    if missing:
        raise MissingRequiredProjection(
            f"{context} is missing verifier compatibility links: {missing}"
        )
    if invalid:
        raise MissingRequiredProjection(
            f"{context} has incompatible verifier links: {invalid}"
        )
    return required


def _verification_graph_verifier_compatibility_links(
    payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_links = payload.get("verifier_compatibility_links")
    if raw_links is None:
        aggregate = _json_dict(payload.get("aggregate"))
        raw_links = aggregate.get("verifier_compatibility_links")

    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(raw_links, list):
        items = []
        for item in raw_links:
            if isinstance(item, dict):
                node_id = (
                    item.get("node_id")
                    or item.get("verifier_node_id")
                    or item.get("projection_verifier_node_id")
                )
                items.append((node_id, item))
    elif isinstance(raw_links, dict) and any(
        field in raw_links
        for field in (
            "raw_output_verifier_node_id",
            "parsed_verdict_verifier_node_id",
            "projection_verifier_node_id",
        )
    ):
        node_id = (
            raw_links.get("node_id")
            or raw_links.get("verifier_node_id")
            or raw_links.get("projection_verifier_node_id")
        )
        items = [(node_id, raw_links)]
    elif isinstance(raw_links, dict):
        items = list(raw_links.items())
    else:
        items = []

    for raw_node_id, raw_link in items:
        if not isinstance(raw_link, dict):
            continue
        node_id = _optional_int(raw_node_id)
        if node_id is None:
            node_id = _optional_int(
                raw_link.get("node_id")
                or raw_link.get("verifier_node_id")
                or raw_link.get("projection_verifier_node_id")
            )
        if node_id is None:
            continue
        normalized[str(node_id)] = {
            "raw_output_verifier_node_id": _optional_int(
                raw_link.get("raw_output_verifier_node_id")
            ),
            "parsed_verdict_verifier_node_id": _optional_int(
                raw_link.get("parsed_verdict_verifier_node_id")
            ),
            "projection_verifier_node_id": _optional_int(
                raw_link.get("projection_verifier_node_id")
            ),
            "context_package_node_id": _optional_int(
                raw_link.get("context_package_node_id")
            ),
            "context_hash_matches": raw_link.get("context_hash_matches") is True,
        }
    return normalized


def _verification_graph_required_edge_cycle(
    *,
    required_edges: list[tuple[int, int]],
) -> list[int]:
    adjacency: dict[int, set[int]] = {}
    for from_node_id, to_node_id in required_edges:
        adjacency.setdefault(from_node_id, set()).add(to_node_id)

    visited: set[int] = set()
    visiting: set[int] = set()
    stack: list[int] = []

    def visit(node_id: int) -> list[int]:
        if node_id in visiting:
            try:
                return [*stack[stack.index(node_id):], node_id]
            except ValueError:
                return [node_id]
        if node_id in visited:
            return []
        visiting.add(node_id)
        stack.append(node_id)
        for next_node_id in sorted(adjacency.get(node_id, set())):
            cycle = visit(next_node_id)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)
        return []

    for node_id in sorted(adjacency):
        cycle = visit(node_id)
        if cycle:
            return cycle
    return []


def _verification_graph_missing_required_reachability(
    *,
    required_node_ids: list[int],
    aggregate_node_id: int | None,
    required_edges: list[tuple[int, int]],
) -> list[int]:
    if aggregate_node_id is None:
        return sorted(set(required_node_ids))
    adjacency: dict[int, set[int]] = {}
    for from_node_id, to_node_id in required_edges:
        adjacency.setdefault(from_node_id, set()).add(to_node_id)

    missing: list[int] = []
    for node_id in sorted(set(required_node_ids)):
        if node_id == aggregate_node_id:
            continue
        if not _verification_graph_can_reach_aggregate(
            start_node_id=node_id,
            aggregate_node_id=aggregate_node_id,
            adjacency=adjacency,
        ):
            missing.append(node_id)
    return missing


def _verification_graph_can_reach_aggregate(
    *,
    start_node_id: int,
    aggregate_node_id: int,
    adjacency: dict[int, set[int]],
) -> bool:
    frontier = [start_node_id]
    visited: set[int] = set()
    while frontier:
        node_id = frontier.pop()
        if node_id == aggregate_node_id:
            return True
        if node_id in visited:
            continue
        visited.add(node_id)
        frontier.extend(
            next_node_id for next_node_id in adjacency.get(node_id, set())
            if next_node_id not in visited
        )
    return False


def _dispatch_attempt_payload(request: DispatchAttemptRequest) -> dict[str, Any]:
    retry_identity = _json_dict(request.retry_identity)
    actor_metadata = _json_dict(request.actor_metadata)
    return {
        "actor_metadata": actor_metadata,
        "actor_role": request.actor_role,
        "base_commit_by_repo": {
            str(key): str(value)
            for key, value in sorted(request.base_commit_by_repo.items())
        },
        "cancellation_token": request.cancellation_token,
        "contract_ids": sorted(int(item) for item in request.contract_ids),
        "dag_sha256": request.dag_sha256,
        "dispatch_request_digest": request.digest,
        "dispatcher_state": "attempt_started",
        "feature_id": request.feature_id,
        "group_idx": int(request.group_idx),
        "idempotency_key": request.stable_idempotency_key,
        "prior_evidence_ids": sorted(int(item) for item in request.prior_evidence_ids),
        "retry": int(request.retry),
        "retry_identity": retry_identity,
        "runtime": str(actor_metadata.get("runtime") or ""),
        "runtime_policy": request.runtime_policy,
        "runtime_policy_digest": request.runtime_policy_digest,
        "sandbox_id": request.sandbox_id,
        "task_id": request.task_id,
        "task_name": request.task_name,
        "workspace_snapshot_ids": sorted(int(item) for item in request.workspace_snapshot_ids),
    }


def _verification_graph_node_evidence(
    evidence: VerificationGraphNodeEvidence | dict[str, Any],
) -> VerificationGraphNodeEvidence:
    if isinstance(evidence, VerificationGraphNodeEvidence):
        return evidence
    return VerificationGraphNodeEvidence(
        feature_id=str(evidence["feature_id"]),
        idempotency_key=str(evidence["idempotency_key"]),
        kind=str(evidence["kind"]),
        status=str(evidence.get("status") or "approved"),
        payload=_json_dict(evidence.get("payload")),
        content_hash=str(evidence.get("content_hash") or ""),
        dag_sha256=str(evidence.get("dag_sha256") or ""),
        group_idx=evidence.get("group_idx"),
        stage=str(evidence.get("stage") or ""),
        name=str(evidence.get("name") or ""),
        deterministic=bool(evidence.get("deterministic", True)),
        input_refs=_json_list(evidence.get("input_refs")),
        output_refs=_json_list(evidence.get("output_refs")),
        failure_id=evidence.get("failure_id"),
        verdict_id=evidence.get("verdict_id"),
        summary=str(evidence.get("summary") or ""),
        metadata=_json_dict(evidence.get("metadata")),
        attempt_id=evidence.get("attempt_id"),
        contract_id=evidence.get("contract_id"),
        snapshot_id=evidence.get("snapshot_id"),
        artifact_id=evidence.get("artifact_id"),
        artifact_key=str(evidence.get("artifact_key") or ""),
        event_id=evidence.get("event_id"),
    )


def _verification_graph_node_fields(
    node: VerificationGraphNodeEvidence,
) -> dict[str, Any]:
    payload = {
        **node.payload,
        "dag_sha256": node.dag_sha256,
        "feature_id": node.feature_id,
        "group_idx": node.group_idx,
        "stage": node.stage,
    }
    return {
        "feature_id": node.feature_id,
        "idempotency_key": node.idempotency_key,
        "attempt_id": node.attempt_id,
        "contract_id": node.contract_id,
        "snapshot_id": node.snapshot_id,
        "group_idx": node.group_idx,
        "stage": node.stage,
        "name": node.name,
        "status": node.status,
        "deterministic": node.deterministic,
        "source_ref": "verification_graph",
        "artifact_id": node.artifact_id,
        "artifact_key": node.artifact_key,
        "event_id": node.event_id,
        "input_refs": node.input_refs,
        "output_refs": node.output_refs,
        "failure_id": node.failure_id,
        "verdict_id": node.verdict_id,
        "content_hash": node.stable_content_hash,
        "summary": node.summary,
        "metadata": node.metadata,
        "payload": payload,
        "started_at": None,
        "finished_at": None,
    }


def _verification_graph_projection(
    projection: VerificationGraphProjection | dict[str, Any],
) -> VerificationGraphProjection:
    if isinstance(projection, VerificationGraphProjection):
        return projection
    graph_payload = _json_dict(projection)
    projection_key = str(
        projection.get("projection_key")
        or graph_payload.get("projection_key")
        or ""
    )
    proof = graph_payload.get("proof")
    proof_digest = ""
    if isinstance(proof, dict):
        proof_digest = str(proof.get("proof_digest") or "")
    aggregate_node = graph_payload.get("aggregate_node")
    aggregate_node_id = None
    if isinstance(aggregate_node, dict) and aggregate_node.get("id") is not None:
        aggregate_node_id = _optional_int(aggregate_node.get("id"))
    raw_group_idx = projection.get("group_idx", graph_payload.get("group_idx"))
    group_idx = _optional_int(raw_group_idx)
    if raw_group_idx is not None and group_idx is None:
        raise MissingRequiredProjection(
            "verification graph projection group_idx must be an integer"
        )
    return VerificationGraphProjection(
        feature_id=str(projection.get("feature_id") or graph_payload.get("feature_id") or ""),
        projection_key=projection_key,
        graph_payload=graph_payload,
        aggregate_node_id=aggregate_node_id,
        idempotency_key=str(projection.get("idempotency_key") or ""),
        dag_sha256=str(projection.get("dag_sha256") or graph_payload.get("dag_sha256") or ""),
        group_idx=group_idx,
        stage=str(projection.get("stage") or graph_payload.get("stage") or ""),
        projection_body=projection.get("projection_body"),
        approved=bool(projection.get("approved", graph_payload.get("approved", False))),
        proof_digest=proof_digest,
    )


def _verification_graph_node_from_graph_payload(
    raw_node: dict[str, Any],
    *,
    projection: VerificationGraphProjection,
) -> VerificationGraphNodeEvidence:
    node_id = raw_node.get("id")
    kind = str(raw_node.get("kind") or "")
    stage = str(raw_node.get("stage") or projection.stage)
    name = str(raw_node.get("name") or kind)
    payload = {
        "graph_node": raw_node,
        "projection_key": projection.projection_key,
    }
    return VerificationGraphNodeEvidence(
        feature_id=projection.feature_id,
        idempotency_key=str(
            raw_node.get("idempotency_key")
            or (
                f"verify-graph:{projection.feature_id}:{projection.dag_sha256}:"
                f"g{projection.group_idx}:{stage}:{kind}:{name}:{node_id}"
            )
        ),
        kind=kind,
        status=str(raw_node.get("status") or "approved"),
        payload=payload,
        content_hash=str(raw_node.get("output_hash") or raw_node.get("input_hash") or ""),
        dag_sha256=projection.dag_sha256,
        group_idx=projection.group_idx,
        stage=stage,
        name=name,
        deterministic=bool(raw_node.get("deterministic", True)),
        input_refs=_json_list(raw_node.get("input_refs")),
        output_refs=_json_list(raw_node.get("output_refs")),
        failure_id=raw_node.get("failure_id"),
        verdict_id=None,
        summary=str(raw_node.get("summary") or ""),
        metadata=_json_dict(raw_node.get("metadata")),
    )


def _verification_graph_payload_required_edge_ids(payload: dict[str, Any]) -> list[Any]:
    return [
        edge.get("id")
        for edge in _json_list(payload.get("edges"))
        if isinstance(edge, dict)
        and str(edge.get("kind") or "") == "requires"
        and edge.get("id") is not None
    ]


def _verification_graph_required_edge_ids(payload: dict[str, Any]) -> list[Any]:
    if "required_edge_ids" in payload:
        return _json_list(payload.get("required_edge_ids"))
    payload_edge_ids = _verification_graph_payload_required_edge_ids(payload)
    if payload_edge_ids:
        return payload_edge_ids
    proof = payload.get("proof")
    if isinstance(proof, dict):
        return _json_list(proof.get("required_edge_ids"))
    return []


def _verification_graph_required_node_ids(payload: dict[str, Any]) -> list[int]:
    aggregate = _json_dict(payload.get("aggregate"))
    proof = _json_dict(payload.get("proof"))
    raw_node_id = _optional_int(aggregate.get("raw_verdict_node_id"))
    ids = [
        *_int_list(aggregate.get("required_gate_node_ids")),
        *([] if raw_node_id is None else [raw_node_id]),
        *_int_list(aggregate.get("required_lens_node_ids")),
        *_int_list(proof.get("required_lineage_node_ids")),
    ]
    return sorted(set(ids))


def _verification_graph_payload_is_full(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload.get("nodes"), list)
        and isinstance(payload.get("edges"), list)
        and isinstance(payload.get("aggregate"), dict)
        and isinstance(payload.get("aggregate_node"), dict)
    )


def _verification_graph_durable_required_node_ids(payload: dict[str, Any]) -> list[int]:
    if "required_node_ids" in payload:
        return _int_list(payload.get("required_node_ids"))
    return _verification_graph_required_node_ids(payload)


def _verification_graph_durable_aggregate_node_id(payload: dict[str, Any]) -> int | None:
    if "aggregate_node_id" in payload:
        return _optional_int(payload.get("aggregate_node_id"))
    aggregate_node_id = _optional_int(_json_dict(payload.get("aggregate")).get("node_id"))
    if aggregate_node_id is not None:
        return aggregate_node_id
    return _optional_int(_json_dict(payload.get("aggregate_node")).get("id"))


def _verification_graph_durable_raw_node_id(payload: dict[str, Any]) -> int | None:
    if "raw_verdict_node_id" in payload:
        return _optional_int(payload.get("raw_verdict_node_id"))
    if "raw_verifier_node_id" in payload:
        return _optional_int(payload.get("raw_verifier_node_id"))
    return _optional_int(_json_dict(payload.get("aggregate")).get("raw_verdict_node_id"))


def _verification_graph_durable_required_gate_ids(payload: dict[str, Any]) -> list[int]:
    if "required_gate_node_ids" in payload:
        return _int_list(payload.get("required_gate_node_ids"))
    return _int_list(_json_dict(payload.get("aggregate")).get("required_gate_node_ids"))


def _verification_graph_durable_required_lens_ids(payload: dict[str, Any]) -> list[int]:
    if "required_lens_node_ids" in payload:
        return _int_list(payload.get("required_lens_node_ids"))
    return _int_list(_json_dict(payload.get("aggregate")).get("required_lens_node_ids"))


def _verification_graph_payload_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [
        node for node in _json_list(payload.get("nodes"))
        if isinstance(node, dict)
    ]
    aggregate_node = payload.get("aggregate_node")
    if isinstance(aggregate_node, dict):
        aggregate_id = aggregate_node.get("id")
        nodes = [
            aggregate_node if node.get("id") == aggregate_id else node
            for node in nodes
        ]
        if not any(node.get("id") == aggregate_id for node in nodes):
            nodes.append(aggregate_node)
    return nodes


def _verification_graph_node_map(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    mapped: dict[int, dict[str, Any]] = {}
    for node in _verification_graph_payload_nodes(payload):
        node_id = _optional_int(node.get("id"))
        if node_id is not None:
            mapped[node_id] = node
    return mapped


def _verification_graph_payload_digest_for_proof(payload: dict[str, Any]) -> str:
    canonical = json.loads(stable_json(payload))
    proof = canonical.get("proof")
    if isinstance(proof, dict):
        proof.pop("proof_digest", None)
        proof.pop("graph_payload_digest", None)
    return stable_digest(canonical)


def _int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in _json_list(value):
        parsed = _optional_int(item)
        if parsed is not None:
            result.append(parsed)
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _verification_graph_record_metadata(record: Any) -> dict[str, Any]:
    return {
        "id": int(_record_get(record, "id")),
        "feature_id": str(_record_get(record, "feature_id")),
        "idempotency_key": str(_record_get(record, "idempotency_key")),
        "execution_journal_row_id": int(_record_get(record, "execution_journal_row_id")),
        "aggregate_evidence_node_id": int(_record_get(record, "aggregate_evidence_node_id")),
        "projection_key": str(_record_get(record, "projection_key")),
        "projection_sha256": str(_record_get(record, "projection_sha256")),
        "dag_sha256": str(_record_get(record, "dag_sha256") or ""),
        "group_idx": _record_get(record, "group_idx"),
        "stage": str(_record_get(record, "stage") or ""),
        "proof_digest": str(_record_get(record, "proof_digest")),
        "graph_payload_digest": str(_record_get(record, "graph_payload_digest")),
        "required_edge_ids": _decode_json(_record_get(record, "required_edge_ids"), []),
        "payload": _decode_json(_record_get(record, "payload"), {}),
    }


def _verification_graph_edge_record_metadata(record: Any) -> dict[str, Any]:
    return {
        "id": int(_record_get(record, "id")),
        "feature_id": str(_record_get(record, "feature_id")),
        "evidence_graph_id": int(_record_get(record, "evidence_graph_id")),
        "graph_edge_id": str(_record_get(record, "graph_edge_id")),
        "from_graph_node_id": str(_record_get(record, "from_graph_node_id") or ""),
        "to_graph_node_id": str(_record_get(record, "to_graph_node_id") or ""),
        "from_evidence_node_id": _record_get(record, "from_evidence_node_id"),
        "to_evidence_node_id": _record_get(record, "to_evidence_node_id"),
        "kind": str(_record_get(record, "kind") or ""),
        "required": bool(_record_get(record, "required")),
        "edge_digest": str(_record_get(record, "edge_digest")),
        "payload": _decode_json(_record_get(record, "payload"), {}),
    }


def _projection_link_metadata(link: ProjectionLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "typed_row_id": link.typed_row_id,
        "artifact_id": link.artifact_id,
        "feature_id": link.feature_id,
        "projection_key": link.projection_key,
        "projection_sha256": link.projection_sha256,
        "idempotency_key": link.idempotency_key,
        "source_table": link.source_table,
        "source_id": link.source_id,
        "projection_owner": link.projection_owner,
        "projection_kind": link.projection_kind,
        "payload": link.payload,
    }


def _prompt_context_fields(
    evidence: PromptContextEvidence,
    row: ExecutionJournalRow,
) -> dict[str, Any]:
    context_package_identity = _prompt_context_identity_payload(evidence)
    payload = {
        "context_file_paths": [str(item) for item in evidence.context_file_paths],
        "context_file_refs": [int(item) for item in evidence.context_file_refs],
        "context_sha256": evidence.context_sha256,
        "excluded_evidence_ids": sorted(int(item) for item in evidence.excluded_evidence_ids),
        "included_contract_ids": sorted(int(item) for item in evidence.included_contract_ids),
        "included_evidence_ids": sorted(int(item) for item in evidence.included_evidence_ids),
        "prompt_ref": int(evidence.prompt_ref),
        "prompt_sha256": evidence.prompt_sha256,
        "prompt_summary": evidence.prompt_summary,
        "truncation_notes": [str(item) for item in evidence.truncation_notes],
    }
    payload.update(_prompt_context_payload_without_provider_payloads(evidence.payload))
    payload.update(context_package_identity)
    content_hash = (
        stable_digest(payload)
        if context_package_identity
        else evidence.context_sha256 or stable_digest(payload)
    )
    return {
        "feature_id": row.feature_id,
        "idempotency_key": evidence.stable_idempotency_key,
        "attempt_id": row.id,
        "group_idx": row.group_idx,
        "stage": evidence.stage,
        "name": f"prompt-context:{row.task_id or row.id}",
        "status": "approved",
        "deterministic": True,
        "source_ref": str(evidence.prompt_ref),
        "artifact_id": evidence.prompt_ref,
        "artifact_key": "",
        "input_refs": payload["included_evidence_ids"],
        "output_refs": payload["context_file_refs"],
        "content_hash": content_hash,
        "summary": evidence.prompt_summary,
        "metadata": _prompt_context_payload_without_provider_payloads(evidence.metadata),
        "payload": payload,
    }


_PROMPT_CONTEXT_PROVIDER_PAYLOAD_KEYS = {
    "omitted_ref_counts",
    "omitted_refs",
    "page_refs",
    "payloads",
    "provider_payloads",
    "provider_record_order",
    "provider_records",
    "provider_state",
    "provider_state_order",
    "provider_state_refs",
    "records",
    "rendered_context",
    "rendered_preview",
}


def _prompt_context_identity_payload(evidence: PromptContextEvidence) -> dict[str, Any]:
    fields = (
        "context_package_id",
        "context_package_digest",
        "context_package_ref",
        "context_package_kind",
        "context_package_completeness",
        "context_package_page_refs",
        "context_package_feature_id",
        "context_package_task_id",
        "context_package_source_dag_artifact_id",
        "context_package_dag_sha256",
        "context_package_evidence_snapshot_digest",
        "context_package_provider_state_digest",
        "context_package_advisory_only",
    )
    payload: dict[str, Any] = {}
    for field in fields:
        value = getattr(evidence, field, None)
        if value is None or value == "":
            continue
        if field == "context_package_page_refs":
            value = _prompt_context_page_ref_identity(value)
            if not value:
                continue
        payload[field] = value
    return payload


def _prompt_context_page_ref_identity(value: Any) -> list[dict[str, Any]]:
    raw_refs = value if isinstance(value, list) else [value]
    allowed = {
        "artifact_id",
        "authority",
        "commit_hash",
        "completeness",
        "created_at",
        "digest",
        "event_id",
        "feature_id",
        "journal_anchor",
        "page_ref_id",
        "page_refs",
        "preview_only",
        "quality",
        "ref_id",
        "slice_id",
        "source_ref_id",
    }
    refs: list[dict[str, Any]] = []
    for raw_ref in raw_refs:
        data = _json_dict(raw_ref)
        ref = {
            str(key): _strip_prompt_context_provider_payloads(item)
            for key, item in data.items()
            if str(key) in allowed and item is not None and item != ""
        }
        if "preview_only" in ref:
            preview_only = _prompt_context_page_ref_preview_only(ref["preview_only"])
            if preview_only is True:
                continue
            if preview_only is not None:
                ref["preview_only"] = preview_only
        if ref:
            refs.append(ref)
    return refs


def _prompt_context_page_ref_preview_only(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return bool(value)


def _prompt_context_payload_without_provider_payloads(value: Any) -> dict[str, Any]:
    payload = _json_dict(value)
    stripped = _strip_prompt_context_provider_payloads(payload)
    return stripped if isinstance(stripped, dict) else {}


def _strip_prompt_context_provider_payloads(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_prompt_context_provider_payloads(item)
            for key, item in value.items()
            if str(key) not in _PROMPT_CONTEXT_PROVIDER_PAYLOAD_KEYS
        }
    if isinstance(value, list):
        return [_strip_prompt_context_provider_payloads(item) for item in value]
    return value


def _raw_output_fields(
    evidence: RawOutputEvidence,
    row: ExecutionJournalRow,
) -> dict[str, Any]:
    payload = {
        "invocation_id": evidence.invocation_id,
        "provider_error_code": evidence.provider_error_code,
        "provider_request_id": evidence.provider_request_id,
        "raw_artifact_id": evidence.raw_artifact_id,
        "raw_text": evidence.raw_text,
        "raw_text_sha256": evidence.raw_text_sha256,
        "runtime": evidence.runtime,
        "status": evidence.status,
        "terminal_reason": evidence.terminal_reason,
    }
    payload.update(_json_dict(evidence.payload))
    return {
        "feature_id": row.feature_id,
        "idempotency_key": evidence.stable_idempotency_key,
        "attempt_id": row.id,
        "group_idx": row.group_idx,
        "stage": evidence.stage,
        "name": f"raw-output:{evidence.invocation_id}",
        "status": "approved" if evidence.status == "completed" else "failed",
        "deterministic": False,
        "source_ref": evidence.invocation_id,
        "artifact_id": evidence.raw_artifact_id,
        "artifact_key": "",
        "input_refs": [],
        "output_refs": [evidence.raw_artifact_id] if evidence.raw_artifact_id is not None else [],
        "content_hash": stable_digest(payload),
        "summary": evidence.terminal_reason or evidence.status,
        "metadata": _json_dict(evidence.metadata),
        "payload": payload,
    }


def _runtime_invocation_fields(
    evidence: RuntimeInvocationEvidence,
    row: ExecutionJournalRow,
) -> dict[str, Any]:
    payload = {
        "actor_metadata": _json_dict(evidence.actor_metadata),
        "actor_name": evidence.actor_name,
        "actor_role": evidence.actor_role,
        "adapter_retry_count": int(evidence.adapter_retry_count),
        "adapter_retry_ids": [str(item) for item in evidence.adapter_retry_ids],
        "cancellation_token": evidence.cancellation_token,
        "elapsed_ms": evidence.elapsed_ms,
        "invocation_id": evidence.invocation_id,
        "output_schema": evidence.output_schema,
        "output_schema_digest": evidence.output_schema_digest,
        "output_type_name": evidence.output_type_name,
        "phase": evidence.phase,
        "process_started": bool(evidence.process_started),
        "prompt_ref": evidence.prompt_ref,
        "prompt_sha256": evidence.prompt_sha256,
        "provider_error_code": evidence.provider_error_code,
        "provider_request_id": evidence.provider_request_id,
        "raw_artifact_id": evidence.raw_artifact_id,
        "raw_text_ref": evidence.raw_text_ref,
        "retry_within_invocation": bool(evidence.retry_within_invocation),
        "runtime": evidence.runtime,
        "runtime_workspace_binding_id": evidence.runtime_workspace_binding_id,
        "status": evidence.status,
        "stderr_artifact_id": evidence.stderr_artifact_id,
        "stdout_artifact_id": evidence.stdout_artifact_id,
        "terminal_reason": evidence.terminal_reason,
        "timeout_seconds": evidence.timeout_seconds,
        "usage": _json_dict(evidence.usage),
    }
    payload.update(_json_dict(evidence.payload))
    evidence_status = "running"
    if evidence.status == "completed":
        evidence_status = "approved"
    elif evidence.status in {"failed", "cancelled"}:
        evidence_status = "failed"
    return {
        "feature_id": row.feature_id,
        "idempotency_key": evidence.stable_idempotency_key,
        "attempt_id": row.id,
        "group_idx": row.group_idx,
        "stage": "runtime",
        "name": f"runtime-invocation:{evidence.invocation_id}",
        "status": evidence_status,
        "deterministic": False,
        "source_ref": evidence.invocation_id,
        "artifact_id": evidence.raw_artifact_id,
        "artifact_key": "",
        "input_refs": [item for item in (evidence.prompt_ref, evidence.runtime_workspace_binding_id) if item is not None],
        "output_refs": [
            item
            for item in (
                evidence.raw_artifact_id,
                evidence.stdout_artifact_id,
                evidence.stderr_artifact_id,
            )
            if item is not None
        ],
        "content_hash": stable_digest(payload),
        "summary": evidence.terminal_reason or evidence.status,
        "metadata": _json_dict(evidence.metadata),
        "payload": payload,
    }


def _runtime_invocation_dispatcher_state(evidence: RuntimeInvocationEvidence) -> str:
    if evidence.status == "completed":
        return "runtime_returned"
    if evidence.status == "cancelled":
        return "cancelled"
    if evidence.status == "failed":
        return "patch_capturing" if evidence.process_started else "evidence_recording"
    return "runtime_invoking"


def _dispatcher_state_rank(dispatcher_state: str, *, status: str = "started") -> int:
    if status in DISPATCHER_TERMINAL_STATUSES:
        return DISPATCHER_STATE_ORDER["succeeded"]
    return DISPATCHER_STATE_ORDER.get(dispatcher_state, 0)


def _structured_output_fields(
    evidence: StructuredOutputEvidence,
    row: ExecutionJournalRow,
) -> dict[str, Any]:
    normalized_payload = _json_dict(evidence.normalized_payload)
    original_payload = _json_dict(evidence.original_payload)
    projection_body = evidence.projection_body
    if projection_body is None and normalized_payload:
        projection_body = stable_json(normalized_payload)
    payload = {
        "corrected_fields": _json_dict(evidence.corrected_fields),
        "normalized_payload": normalized_payload,
        "original_payload": original_payload,
        "projection_body": projection_body,
        "raw_artifact_id": evidence.raw_artifact_id,
        "raw_text_ref": evidence.raw_text_ref,
        "schema_digest": evidence.schema_digest,
        "schema_name": evidence.schema_name,
        "task_id": normalized_payload.get("task_id") or row.task_id,
        "task_id_matches_request": bool(evidence.task_id_matches_request),
        "valid": bool(evidence.valid),
        "validation_errors": [str(item) for item in evidence.validation_errors],
    }
    payload.update(_json_dict(evidence.payload))
    return {
        "feature_id": row.feature_id,
        "idempotency_key": evidence.stable_idempotency_key,
        "attempt_id": row.id,
        "group_idx": row.group_idx,
        "stage": evidence.stage,
        "name": f"structured-result:{row.task_id or row.id}",
        "status": "approved" if evidence.valid else "rejected",
        "deterministic": True,
        "source_ref": evidence.schema_name,
        "artifact_id": evidence.raw_artifact_id,
        "artifact_key": "",
        "input_refs": [item for item in (evidence.raw_text_ref, evidence.raw_artifact_id) if item is not None],
        "output_refs": [],
        "content_hash": evidence.content_hash,
        "summary": "; ".join(str(item) for item in evidence.validation_errors[:3]),
        "metadata": _json_dict(evidence.metadata),
        "payload": payload,
    }


def _runtime_failure_fields(
    failure: RuntimeFailureEvidence,
    row: ExecutionJournalRow,
) -> dict[str, Any]:
    signature_hash = failure.stable_signature_hash
    payload = {
        "deterministic": bool(failure.deterministic),
        "evidence_ids": sorted(int(item) for item in failure.evidence_ids),
        "failure_class": failure.failure_class,
        "failure_type": failure.failure_type,
        "operator_required": bool(failure.operator_required),
        "provider_error_code": failure.provider_error_code,
        "provider_request_id": failure.provider_request_id,
        "retryable": bool(failure.retryable),
        "runtime": failure.runtime,
        "signature_hash": signature_hash,
        "terminal_reason": failure.terminal_reason,
    }
    extra_payload = _json_dict(failure.payload)
    protected_keys = {
        "deterministic",
        "evidence_ids",
        "failure_class",
        "failure_type",
        "operator_required",
        "retryable",
        "signature_hash",
    }
    route_decision = _json_dict(extra_payload.get("route_decision"))
    route_decision_route = route_decision.get("route")
    for key, value in extra_payload.items():
        if key in protected_keys:
            if value != payload.get(key):
                payload[f"legacy_{key}"] = value
            continue
        if key == "route" and route_decision_route is not None and value != route_decision_route:
            payload["legacy_route"] = value
            continue
        payload[key] = value
    return {
        "feature_id": row.feature_id,
        "idempotency_key": failure.stable_idempotency_key,
        "attempt_id": row.id,
        "group_idx": row.group_idx,
        "stage": failure.stage,
        "name": f"{failure.failure_class}/{failure.failure_type}",
        "status": "failed",
        "deterministic": bool(failure.deterministic),
        "source_ref": signature_hash,
        "artifact_id": None,
        "artifact_key": "",
        "input_refs": payload["evidence_ids"],
        "output_refs": [],
        "failure_id": None,
        "content_hash": stable_digest(payload),
        "summary": failure.summary,
        "metadata": _json_dict(failure.metadata),
        "payload": payload,
    }


def _structured_result_projection_body(evidence: EvidenceNode) -> str:
    payload = evidence.payload
    projection_body = payload.get("projection_body")
    if projection_body is not None:
        return str(projection_body)
    normalized_payload = _json_dict(payload.get("normalized_payload"))
    if normalized_payload:
        return stable_json(normalized_payload)
    raise ExecutionControlError("structured result lacks projection body")


def _append_unique(values: list[Any], value: Any) -> list[Any]:
    result = list(values)
    if value not in result:
        result.append(value)
    return result


def _sandbox_lease_fields(lease: Any) -> dict[str, Any]:
    payload = _json_dict(_field(lease, "payload", {}))
    base_snapshot_ids = [int(item) for item in _json_list(_field(lease, "base_snapshot_ids", []))]
    repo_roots = _json_dict(_field(lease, "repo_roots", {}))
    repo_ids = [str(item) for item in _json_list(_field(lease, "repo_ids", []))]
    if not repo_ids and repo_roots:
        repo_ids = sorted(str(key) for key in repo_roots)
    base_commits = {
        str(key): str(value)
        for key, value in _json_dict(_field(lease, "base_commits", {})).items()
    }
    task_ids = [str(item) for item in _json_list(_field(lease, "task_ids", []))]
    contract_ids = [int(item) for item in _json_list(_field(lease, "contract_ids", []))]
    group_idx = int(_field(lease, "group_idx", 0) or 0)
    attempt_no = int(_field(lease, "attempt_no", 0) or 0)
    mode = str(_field(lease, "mode", "wave") or "wave")
    sandbox_id = str(_field(lease, "sandbox_id", "") or f"g{group_idx}:attempt-{attempt_no}")
    leased_until = (
        _field(lease, "leased_until", None)
        or _field(lease, "expires_at", None)
        or payload.get("leased_until")
        or payload.get("expires_at")
    )
    if not isinstance(leased_until, datetime):
        leased_until = _parse_datetime(leased_until)
    fields = {
        "feature_id": str(_field(lease, "feature_id", payload.get("feature_id") or "")),
        "dag_sha256": str(_field(lease, "dag_sha256", payload.get("dag_sha256") or "")),
        "group_idx": group_idx,
        "attempt_no": attempt_no,
        "mode": mode,
        "status": str(_field(lease, "status", "allocating") or "allocating"),
        "lease_owner": str(
            _field(lease, "lease_owner", "")
            or _field(lease, "owner", "")
            or "sandbox_runner"
        ),
        "leased_until": leased_until or datetime.now(timezone.utc),
        "lease_version": int(_field(lease, "lease_version", 0) or 0),
        "base_snapshot_ids": base_snapshot_ids,
        "sandbox_root": str(
            _field(lease, "sandbox_root", "")
            or _field(lease, "root", "")
            or payload.get("sandbox_root")
            or payload.get("root")
            or ""
        ),
        "sandbox_id": sandbox_id,
        "manifest_path": str(_field(lease, "manifest_path", payload.get("manifest_path") or "")),
        "repo_ids": repo_ids,
        "base_commits": base_commits,
        "task_ids": task_ids,
        "contract_ids": contract_ids,
        "writable_roots": [
            str(item) for item in _json_list(_field(lease, "writable_roots", []))
        ],
        "readonly_roots": [
            str(item) for item in _json_list(_field(lease, "readonly_roots", []))
        ],
        "blocked_roots": [
            str(item) for item in _json_list(_field(lease, "blocked_roots", []))
        ],
        "patch_summary_ids": [
            int(item) for item in _json_list(_field(lease, "patch_summary_ids", []))
        ],
        "payload": payload,
    }
    fields["idempotency_key"] = str(
        _field(lease, "idempotency_key", "")
        or (
            SandboxLease(
                feature_id=fields["feature_id"],
                dag_sha256=fields["dag_sha256"],
                group_idx=fields["group_idx"],
                attempt_no=fields["attempt_no"],
                mode=fields["mode"],
                repo_ids=fields["repo_ids"],
                base_commits=fields["base_commits"],
                contract_ids=fields["contract_ids"],
            ).stable_idempotency_key
        )
    )
    fields["lease_digest"] = str(
        _field(lease, "lease_digest", "")
        or (
            SandboxLease(
                sandbox_id=fields["sandbox_id"],
                sandbox_root=fields["sandbox_root"],
                manifest_path=fields["manifest_path"],
                base_snapshot_ids=fields["base_snapshot_ids"],
                repo_ids=fields["repo_ids"],
                base_commits=fields["base_commits"],
                mode=fields["mode"],
                lease_owner=fields["lease_owner"],
                task_ids=fields["task_ids"],
                contract_ids=fields["contract_ids"],
                writable_roots=fields["writable_roots"],
                readonly_roots=fields["readonly_roots"],
                blocked_roots=fields["blocked_roots"],
            ).stable_lease_digest
        )
    )
    fields["projection_key"] = sandbox_manifest_projection_key(
        group_idx=fields["group_idx"],
        attempt_no=fields["attempt_no"],
    )
    return fields


def _sandbox_repo_binding_fields(
    binding: Any,
    *,
    lease: SandboxLease | None = None,
) -> dict[str, Any]:
    payload = _json_dict(_field(binding, "payload", {}))
    lease_id = int(
        _field(binding, "sandbox_lease_id", 0)
        or (lease.id if lease is not None and lease.id is not None else 0)
    )
    feature_id = str(
        _field(binding, "feature_id", "")
        or (lease.feature_id if lease is not None else "")
        or payload.get("feature_id")
        or ""
    )
    fields = {
        "feature_id": feature_id,
        "sandbox_lease_id": lease_id,
        "repo_id": str(_field(binding, "repo_id", payload.get("repo_id") or "")),
        "sandbox_repo_root": str(
            _field(binding, "sandbox_repo_root", payload.get("sandbox_repo_root") or "")
        ),
        "canonical_repo_root": str(
            _field(binding, "canonical_repo_root", payload.get("canonical_repo_root") or "")
        ),
        "base_snapshot_id": int(
            _field(binding, "base_snapshot_id", payload.get("base_snapshot_id") or 0) or 0
        ),
        "base_commit": str(_field(binding, "base_commit", payload.get("base_commit") or "")),
        "writable": bool(_field(binding, "writable", payload.get("writable", True))),
        "writable_roots": [
            str(item) for item in _json_list(_field(binding, "writable_roots", []))
        ],
        "readonly_roots": [
            str(item) for item in _json_list(_field(binding, "readonly_roots", []))
        ],
        "blocked_canonical_roots": [
            str(item)
            for item in _json_list(_field(binding, "blocked_canonical_roots", []))
        ],
        "status": str(_field(binding, "status", "active") or "active"),
        "payload": payload,
    }
    fields["binding_digest"] = str(
        _field(binding, "binding_digest", "")
        or (
            SandboxRepoBinding(
                sandbox_lease_id=fields["sandbox_lease_id"],
                repo_id=fields["repo_id"],
                sandbox_repo_root=fields["sandbox_repo_root"],
                canonical_repo_root=fields["canonical_repo_root"],
                base_snapshot_id=fields["base_snapshot_id"],
                base_commit=fields["base_commit"],
                writable=fields["writable"],
                writable_roots=fields["writable_roots"],
                readonly_roots=fields["readonly_roots"],
                blocked_canonical_roots=fields["blocked_canonical_roots"],
            ).stable_binding_digest
        )
    )
    fields["idempotency_key"] = str(
        _field(binding, "idempotency_key", "")
        or (
            f"idem:sandbox-repo-binding:{fields['sandbox_lease_id']}:"
            f"{fields['repo_id']}:{fields['binding_digest']}"
        )
    )
    return fields


def _runtime_workspace_binding_fields(
    binding: Any,
    *,
    lease: SandboxLease | None = None,
) -> dict[str, Any]:
    payload = _json_dict(_field(binding, "payload", {}))
    role_metadata = _json_dict(_field(binding, "role_metadata", {}))
    runtime_name = str(
        _field(binding, "runtime_name", "")
        or _field(binding, "runtime", "")
        or payload.get("runtime_name")
        or payload.get("runtime")
        or ""
    )
    lease_id = int(
        _field(binding, "sandbox_lease_id", 0)
        or (lease.id if lease is not None and lease.id is not None else 0)
    )
    feature_id = str(
        _field(binding, "feature_id", "")
        or (lease.feature_id if lease is not None else "")
        or payload.get("feature_id")
        or ""
    )
    role_metadata_digest = str(
        _field(binding, "role_metadata_digest", "")
        or stable_digest(role_metadata)
    )
    fields = {
        "feature_id": feature_id,
        "sandbox_lease_id": lease_id,
        "attempt_id": int(_field(binding, "attempt_id", payload.get("attempt_id") or 0) or 0),
        "runtime_name": runtime_name,
        "cwd": str(_field(binding, "cwd", payload.get("cwd") or "")),
        "workspace_override": str(
            _field(binding, "workspace_override", payload.get("workspace_override") or "")
        ),
        "manifest_path": str(
            _field(binding, "manifest_path", payload.get("manifest_path") or "")
        ),
        "repo_roots": {
            str(key): str(value)
            for key, value in _json_dict(_field(binding, "repo_roots", {})).items()
        },
        "writable_roots": [
            str(item) for item in _json_list(_field(binding, "writable_roots", []))
        ],
        "readonly_roots": [
            str(item) for item in _json_list(_field(binding, "readonly_roots", []))
        ],
        "blocked_roots": [
            str(item) for item in _json_list(_field(binding, "blocked_roots", []))
        ],
        "env": {
            str(key): str(value)
            for key, value in _json_dict(_field(binding, "env", {})).items()
        },
        "role_metadata": role_metadata,
        "role_metadata_digest": role_metadata_digest,
        "status": str(_field(binding, "status", "bound") or "bound"),
        "payload": payload,
    }
    fields["binding_digest"] = str(
        _field(binding, "binding_digest", "")
        or (
            RuntimeWorkspaceBinding(
                sandbox_lease_id=fields["sandbox_lease_id"],
                attempt_id=fields["attempt_id"],
                runtime_name=fields["runtime_name"],
                runtime=fields["runtime_name"],
                cwd=fields["cwd"],
                workspace_override=fields["workspace_override"],
                manifest_path=fields["manifest_path"],
                repo_roots=fields["repo_roots"],
                writable_roots=fields["writable_roots"],
                readonly_roots=fields["readonly_roots"],
                blocked_roots=fields["blocked_roots"],
                env=fields["env"],
                role_metadata=fields["role_metadata"],
                role_metadata_digest=fields["role_metadata_digest"],
            ).stable_binding_digest
        )
    )
    fields["idempotency_key"] = str(
        _field(binding, "idempotency_key", "")
        or (
            f"idem:runtime-workspace-binding:{fields['sandbox_lease_id']}:"
            f"{fields['attempt_id']}:{fields['runtime_name']}:{fields['binding_digest']}"
        )
    )
    return fields


def _task_contract_fields(contract: Any) -> dict[str, Any]:
    normalized = _field(contract, "normalized_contract_json", {}) or {}
    normalized = _payload_dict(normalized) if not isinstance(normalized, dict) else dict(normalized)
    fields = {
        "feature_id": str(_field(contract, "feature_id", normalized.get("feature_id") or "")),
        "dag_sha256": str(_field(contract, "dag_sha256", normalized.get("dag_sha256") or "")),
        "source_dag_artifact_id": _field(
            contract,
            "source_dag_artifact_id",
            normalized.get("source_dag_artifact_id"),
        ),
        "source_dag_sha256": str(
            _field(contract, "source_dag_sha256", normalized.get("source_dag_sha256") or "")
        ),
        "group_idx": _field(contract, "group_idx", normalized.get("group_idx") or 0),
        "task_id": str(_field(contract, "task_id", normalized.get("task_id") or "")),
        "repo_id": str(_field(contract, "repo_id", normalized.get("repo_id") or "")),
        "repo_path": str(_field(contract, "repo_path", normalized.get("repo_path") or "")),
        "required_paths": _json_list(_field(contract, "required_paths", normalized.get("required_paths"))),
        "allowed_paths": _json_list(_field(contract, "allowed_paths", normalized.get("allowed_paths"))),
        "read_only_paths": _json_list(
            _field(contract, "read_only_paths", normalized.get("read_only_paths"))
        ),
        "forbidden_paths": _json_list(
            _field(contract, "forbidden_paths", normalized.get("forbidden_paths"))
        ),
        "generated_outputs": _json_list(
            _field(contract, "generated_outputs", normalized.get("generated_outputs"))
        ),
        "acceptance_criteria": _json_list(
            _field(contract, "acceptance_criteria", normalized.get("acceptance_criteria"))
        ),
        "verification_gates": _json_list(
            _field(contract, "verification_gates", normalized.get("verification_gates"))
        ),
        "execution_policy": _json_dict(
            _field(contract, "execution_policy", normalized.get("execution_policy"))
        ),
        "non_goals": _json_list(_field(contract, "non_goals", normalized.get("non_goals"))),
        "dependency_task_ids": _json_list(
            _field(contract, "dependency_task_ids", normalized.get("dependency_task_ids"))
        ),
        "unknown_write_set": bool(
            _field(contract, "unknown_write_set", normalized.get("unknown_write_set") or False)
        ),
        "compile_warnings": _json_list(
            _field(contract, "compile_warnings", normalized.get("compile_warnings"))
        ),
        "status": str(_field(contract, "status", normalized.get("status") or "active")),
        "payload": _json_dict(_field(contract, "payload", {})),
    }
    if not normalized:
        normalized = {
            key: value
            for key, value in fields.items()
            if key not in {"idempotency_key", "payload", "status"}
        }
    fields["normalized_contract_json"] = normalized
    fields["contract_digest"] = str(
        _field(contract, "contract_digest", "") or stable_digest(normalized)
    )
    fields["idempotency_key"] = str(
        _field(contract, "idempotency_key", "")
        or (
            f"idem:task-contract:{fields['feature_id']}:{fields['dag_sha256']}:"
            f"g{fields['group_idx']}:{fields['task_id']}:{fields['contract_digest']}"
        )
    )
    fields["group_idx"] = int(fields["group_idx"])
    return fields


def _patch_summary_fields(summary: Any) -> dict[str, Any]:
    payload = {
        "sandbox_id": str(_field(summary, "sandbox_id", "")),
        "contract_ids": _json_list(_field(summary, "contract_ids", [])),
        "repo_id": str(_field(summary, "repo_id", "")),
        "base_commit": _field(summary, "base_commit", None),
        "workspace_snapshot_id": _field(summary, "workspace_snapshot_id", None),
        "base_snapshot_id": _field(summary, "base_snapshot_id", None),
        "base_snapshot_ids": _json_list(_field(summary, "base_snapshot_ids", [])),
        "changed_paths": _json_list(_field(summary, "changed_paths", [])),
        "created_paths": _json_list(_field(summary, "created_paths", [])),
        "modified_paths": _json_list(_field(summary, "modified_paths", [])),
        "deleted_paths": _json_list(_field(summary, "deleted_paths", [])),
        "renamed_paths": _json_dict(_field(summary, "renamed_paths", {})),
        "diff_sha256": str(_field(summary, "diff_sha256", "")),
        "diff_artifact_id": _field(summary, "diff_artifact_id", None),
        "summary_artifact_id": _field(summary, "summary_artifact_id", None),
    }
    payload.update(_json_dict(_field(summary, "payload", {})))
    metadata = _json_dict(_field(summary, "metadata", {}))
    for key in ("workspace_snapshot_id", "base_snapshot_id", "base_snapshot_ids"):
        if payload.get(key) in (None, [], "") and metadata.get(key) not in (None, [], ""):
            payload[key] = metadata[key]
    group_idx = _field(summary, "group_idx", None)
    attempt_no = _field(summary, "attempt_no", None)
    repo_id = str(payload["repo_id"])
    task_id = str(_field(summary, "task_id", "") or "")
    stage = str(_field(summary, "stage", "") or "")
    contract_identity = ",".join(str(item) for item in payload["contract_ids"]) or "-"
    projection_key = (
        f"dag-sandbox-patch:g{group_idx if group_idx is not None else '-'}:"
        f"attempt-{attempt_no if attempt_no is not None else '-'}:repo-{repo_id}"
    )
    idempotency_key = str(
        _field(summary, "idempotency_key", "")
        or (
            f"idem:sandbox-patch:{_field(summary, 'feature_id', '')}:"
            f"{_field(summary, 'dag_sha256', '')}:g{group_idx if group_idx is not None else '-'}:"
            f"attempt-{attempt_no if attempt_no is not None else '-'}:repo-{repo_id}:"
            f"task-{task_id or '-'}:stage-{stage or '-'}:"
            f"contracts-{contract_identity}:"
            f"{payload['sandbox_id']}:{payload['base_commit'] or ''}:{payload['diff_sha256']}"
        )
    )
    return {
        "feature_id": str(_field(summary, "feature_id", "")),
        "dag_sha256": str(_field(summary, "dag_sha256", "")),
        "group_idx": group_idx,
        "attempt_id": _field(summary, "attempt_id", None),
        "attempt_no": attempt_no,
        "task_id": task_id,
        "stage": stage,
        "kind": "sandbox_patch_summary",
        "name": str(_field(summary, "name", "") or projection_key),
        "status": str(_field(summary, "status", "") or "approved"),
        "deterministic": bool(_field(summary, "deterministic", True)),
        "source_ref": str(_field(summary, "source_ref", "") or payload["sandbox_id"]),
        "artifact_id": payload["summary_artifact_id"] or payload["diff_artifact_id"],
        "artifact_key": projection_key,
        "snapshot_id": payload.get("workspace_snapshot_id"),
        "input_refs": sorted(
            {
                item
                for item in [
                    *_json_list(_field(summary, "input_refs", [])),
                    *(
                        [payload.get("workspace_snapshot_id")]
                        if payload.get("workspace_snapshot_id") is not None
                        else []
                    ),
                    *(
                        [payload.get("base_snapshot_id")]
                        if payload.get("base_snapshot_id") is not None
                        else []
                    ),
                ]
                if item is not None
            },
            key=str,
        ),
        "output_refs": _json_list(_field(summary, "output_refs", [])),
        "content_hash": str(payload["diff_sha256"] or stable_digest(payload)),
        "summary": str(_field(summary, "summary", "") or ""),
        "metadata": metadata,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "projection_key": projection_key,
    }


def _patch_summary_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stable_payload = dict(payload)
    stable_payload.pop("diff_artifact_id", None)
    stable_payload.pop("summary_artifact_id", None)
    return stable_payload


def _contract_verdict_fields(verdict: Any) -> dict[str, Any]:
    approved = bool(_field(verdict, "approved", False))
    payload = {
        "contract_id": _field(verdict, "contract_id", 0),
        "patch_summary_id": _field(verdict, "patch_summary_id", 0),
        "approved": approved,
        "violation_codes": _json_list(_field(verdict, "violation_codes", [])),
        "violations": _json_list(_field(verdict, "violations", [])),
        "required_evidence_node_ids": _json_list(
            _field(verdict, "required_evidence_node_ids", [])
        ),
        "workspace_snapshot_id": _field(verdict, "workspace_snapshot_id", None),
    }
    payload.update(_json_dict(_field(verdict, "payload", {})))
    group_idx = _field(verdict, "group_idx", None)
    task_id = str(_field(verdict, "task_id", "") or "")
    sandbox_id = str(_field(verdict, "sandbox_id", "") or "")
    projection_key = f"dag-contract-verdict:g{group_idx}:{task_id}:{sandbox_id}"
    idempotency_key = str(
        _field(verdict, "idempotency_key", "")
        or (
            f"idem:contract-verdict:{_field(verdict, 'feature_id', '')}:"
            f"{_field(verdict, 'dag_sha256', '')}:g{group_idx if group_idx is not None else '-'}:"
            f"{task_id}:{sandbox_id}:{payload['contract_id']}:{payload['patch_summary_id']}:"
            f"{stable_digest(payload)}"
        )
    )
    return {
        "feature_id": str(_field(verdict, "feature_id", "")),
        "dag_sha256": str(_field(verdict, "dag_sha256", "")),
        "group_idx": group_idx,
        "task_id": task_id,
        "sandbox_id": sandbox_id,
        "attempt_id": _field(verdict, "attempt_id", None),
        "contract_id": payload["contract_id"],
        "snapshot_id": payload["workspace_snapshot_id"],
        "stage": str(_field(verdict, "stage", "") or ""),
        "kind": "contract_verdict",
        "name": str(_field(verdict, "name", "") or projection_key),
        "status": "approved" if approved else "rejected",
        "deterministic": bool(_field(verdict, "deterministic", True)),
        "source_ref": sandbox_id,
        "artifact_id": _field(verdict, "artifact_id", None),
        "artifact_key": projection_key,
        "input_refs": _json_list(_field(verdict, "input_refs", [])),
        "output_refs": _json_list(_field(verdict, "output_refs", [])),
        "content_hash": stable_digest(payload),
        "summary": str(_field(verdict, "summary", "") or ""),
        "metadata": _json_dict(_field(verdict, "metadata", {})),
        "payload": payload,
        "idempotency_key": idempotency_key,
        "projection_key": projection_key,
        "patch_summary_id": payload["patch_summary_id"],
    }


def _sandbox_manifest_projection_value(
    lease: SandboxLease,
    repo_bindings: tuple[SandboxRepoBinding, ...],
) -> str:
    repo_roots = {
        binding.repo_id: binding.sandbox_repo_root
        for binding in sorted(repo_bindings, key=lambda item: item.repo_id)
    }
    repo_base_commits = {
        binding.repo_id: binding.base_commit
        for binding in sorted(repo_bindings, key=lambda item: item.repo_id)
        if binding.base_commit
    }
    value = {
        "sandbox_lease_id": lease.id,
        "sandbox_id": lease.sandbox_id,
        "dag_sha256": lease.dag_sha256,
        "group_idx": lease.group_idx,
        "attempt_no": lease.attempt_no,
        "mode": lease.mode,
        "status": lease.status,
        "lease_owner": lease.lease_owner,
        "leased_until": (
            lease.leased_until.isoformat()
            if isinstance(lease.leased_until, datetime)
            else str(lease.leased_until or "")
        ),
        "sandbox_root": lease.sandbox_root,
        "manifest_path": lease.manifest_path,
        "repo_ids": lease.repo_ids,
        "repo_count": len(repo_bindings),
        "repo_roots": repo_roots,
        "base_snapshot_ids": lease.base_snapshot_ids,
        "base_commits": repo_base_commits or lease.base_commits,
        "task_ids": lease.task_ids,
        "contract_ids": lease.contract_ids,
        "writable_roots": lease.writable_roots,
        "readonly_roots": lease.readonly_roots,
        "blocked_roots": lease.blocked_roots,
        "patch_summary_ids": lease.patch_summary_ids,
        "lease_digest": lease.lease_digest,
    }
    return stable_json(_bounded_workspace_projection_payload(value))


def _task_contract_projection_value(contract: TaskDeliverableContract) -> str:
    payload = {
        "contract_id": contract.id,
        "contract_digest": contract.contract_digest,
        "dag_sha256": contract.dag_sha256,
        "group_idx": contract.group_idx,
        "task_id": contract.task_id,
        "repo_id": contract.repo_id,
        "path_counts": {
            "required_paths": len(contract.required_paths),
            "allowed_paths": len(contract.allowed_paths),
            "read_only_paths": len(contract.read_only_paths),
            "forbidden_paths": len(contract.forbidden_paths),
            "generated_outputs": len(contract.generated_outputs),
        },
        "unknown_write_set": contract.unknown_write_set,
        "gates": [
            {
                "id": _field(gate, "id", ""),
                "gate_kind": _field(gate, "gate_kind", ""),
                "name": _field(gate, "name", ""),
                "blocks_merge": bool(_field(gate, "blocks_merge", True)),
            }
            for gate in contract.verification_gates
        ],
        "compile_warnings": contract.compile_warnings,
    }
    return stable_json(_bounded_workspace_projection_payload(payload))


def _patch_summary_projection_value(evidence: EvidenceNode) -> str:
    payload = evidence.payload
    changed_paths = _json_list(payload.get("changed_paths"))
    created_paths = _json_list(payload.get("created_paths"))
    modified_paths = _json_list(payload.get("modified_paths"))
    deleted_paths = _json_list(payload.get("deleted_paths"))
    renamed_paths = _json_dict(payload.get("renamed_paths"))
    value = {
        "evidence_node_id": evidence.id,
        "patch_summary_id": evidence.id,
        "sandbox_id": payload.get("sandbox_id") or "",
        "contract_ids": _json_list(payload.get("contract_ids")),
        "repo_id": payload.get("repo_id") or "",
        "base_commit": payload.get("base_commit"),
        "workspace_snapshot_id": payload.get("workspace_snapshot_id"),
        "base_snapshot_id": payload.get("base_snapshot_id"),
        "base_snapshot_ids": _json_list(payload.get("base_snapshot_ids")),
        "diff_sha256": payload.get("diff_sha256") or evidence.content_hash,
        "diff_artifact_id": payload.get("diff_artifact_id"),
        "summary_artifact_id": payload.get("summary_artifact_id"),
        "path_counts": {
            "changed_paths": len(changed_paths),
            "created_paths": len(created_paths),
            "modified_paths": len(modified_paths),
            "deleted_paths": len(deleted_paths),
            "renamed_paths": len(renamed_paths),
        },
        "changed_paths": changed_paths[:WORKSPACE_PROJECTION_LIST_LIMIT],
        "created_paths": created_paths[:WORKSPACE_PROJECTION_LIST_LIMIT],
        "modified_paths": modified_paths[:WORKSPACE_PROJECTION_LIST_LIMIT],
        "deleted_paths": deleted_paths[:WORKSPACE_PROJECTION_LIST_LIMIT],
        "renamed_paths": dict(list(sorted(renamed_paths.items()))[:WORKSPACE_PROJECTION_LIST_LIMIT]),
    }
    return stable_json(_bounded_workspace_projection_payload(value))


def _contract_verdict_projection_value(evidence: EvidenceNode) -> str:
    payload = evidence.payload
    metadata = _json_dict(evidence.metadata)
    violations = _json_list(payload.get("violations"))
    required_evidence_node_ids = _json_list(payload.get("required_evidence_node_ids"))
    value = {
        "evidence_node_id": evidence.id,
        "verdict_id": evidence.id,
        "contract_id": payload.get("contract_id"),
        "patch_summary_id": payload.get("patch_summary_id"),
        "captured_patch_summary_id": (
            metadata.get("captured_patch_summary_id")
            or payload.get("captured_patch_summary_id")
            or payload.get("patch_summary_id")
        ),
        "diff_artifact_id": metadata.get("diff_artifact_id") or payload.get("diff_artifact_id"),
        "actual_sandbox_id": metadata.get("actual_sandbox_id") or payload.get("actual_sandbox_id"),
        "approved": bool(payload.get("approved")),
        "violation_codes": _json_list(payload.get("violation_codes"))[
            :WORKSPACE_PROJECTION_LIST_LIMIT
        ],
        "violation_count": len(violations),
        "violations": violations[:WORKSPACE_PROJECTION_LIST_LIMIT],
        "required_evidence_node_ids": required_evidence_node_ids[
            :WORKSPACE_PROJECTION_LIST_LIMIT
        ],
        "workspace_snapshot_id": payload.get("workspace_snapshot_id"),
    }
    return stable_json(_bounded_workspace_projection_payload(value))


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    if hasattr(value, name):
        return getattr(value, name)
    if is_dataclass(value):
        return asdict(value).get(name, default)
    return default


def _json_list(value: Any) -> list[Any]:
    decoded = _decode_json(value, [])
    if decoded is None:
        return []
    if isinstance(decoded, list):
        return decoded
    if isinstance(decoded, tuple):
        return list(decoded)
    if isinstance(decoded, (set, frozenset)):
        return sorted(decoded, key=str)
    return [decoded]


def _json_dict(value: Any) -> dict[str, Any]:
    decoded = _decode_json(value, {})
    if decoded is None:
        return {}
    if isinstance(decoded, dict):
        return dict(decoded)
    model_dump = getattr(decoded, "model_dump", None)
    if model_dump is not None:
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {"value": dumped}
    if is_dataclass(decoded):
        dumped = asdict(decoded)
        return dict(dumped) if isinstance(dumped, dict) else {"value": dumped}
    return {"value": decoded}


def _supported_projection_key(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in SUPPORTED_PROJECTION_PREFIXES)


def _validate_projection_key_family(entry_type: str, key: str) -> None:
    prefixes = PROJECTION_KEY_PREFIXES.get(entry_type)
    if prefixes is None:
        return
    if not any(key.startswith(prefix) for prefix in prefixes):
        expected = ", ".join(prefixes)
        raise UnsupportedCompatibilityProjection(
            f"{entry_type} projections must use one of: {expected}; got {key}"
        )


def _serialize_artifact_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    model_dump_json = getattr(value, "model_dump_json", None)
    if model_dump_json is not None:
        return model_dump_json()
    return json.dumps(value, sort_keys=True, default=str)


def _projection_key(projection: Any) -> str:
    key = getattr(projection, "projection_key", None) or getattr(projection, "artifact_key", None)
    if not key:
        raise MissingRequiredProjection("projection requires a legacy artifact key")
    return str(key)


def _projection_value(projection: Any) -> Any | None:
    for name in ("projection_body", "artifact_body", "body", "value"):
        if hasattr(projection, name):
            value = getattr(projection, name)
            if value is not None:
                return value
    for name in ("implementation_result", "verdict"):
        if not hasattr(projection, name):
            continue
        value = getattr(projection, name)
        if value is None:
            continue
        if name == "verdict":
            return _legacy_to_str(value)
        model_dump_json = getattr(value, "model_dump_json", None)
        if model_dump_json is not None:
            return model_dump_json()
        return value
    if hasattr(projection, "commit_failure_payload"):
        payload = getattr(projection, "commit_failure_payload")
        if payload is not None:
            return json.dumps(payload, indent=2)
    if hasattr(projection, "checkpoint"):
        checkpoint = getattr(projection, "checkpoint")
        if checkpoint is not None:
            return json.dumps(checkpoint)
    return None


def _projection_value_sha256(projection: CompatibilityProjection) -> str:
    return hashlib.sha256(
        _serialize_artifact_value(projection.value).encode("utf-8")
    ).hexdigest()


def _projection_payload(projection: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name in (
        "source_table",
        "source_id",
        "source_kind",
        "stage",
        "overlay_slug",
        "failure_class",
        "projection_key",
        "artifact_key",
        "legacy_event_type",
        "legacy_event_content",
        "legacy_event_metadata",
    ):
        if hasattr(projection, name):
            payload[name] = getattr(projection, name)
    return payload


def _group_checkpoint_projection_payload(projection: Any) -> dict[str, Any]:
    payload = {"entry_type": "group_checkpoint", **_projection_payload(projection)}
    for name in ("group_idx", "checkpoint", "status"):
        if hasattr(projection, name):
            payload[name] = getattr(projection, name)
    return payload


def _group_checkpoint_gate_fields(
    projection: Any,
    row: ExecutionJournalRow,
    *,
    projection_key: str,
    projection_sha256: str,
    projection_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        **projection_payload,
        "dag_sha256": row.dag_sha256,
        "feature_id": row.feature_id,
        "projection_key": projection_key,
        "projection_sha256": projection_sha256,
        "typed_row_id": row.id,
    }
    source_id = getattr(projection, "source_id", None)
    input_refs = [int(source_id)] if isinstance(source_id, int) else []
    group_idx = row.group_idx if row.group_idx is not None else getattr(projection, "group_idx", None)
    status = str(getattr(projection, "status", "") or "approved")
    return {
        "feature_id": row.feature_id,
        "idempotency_key": f"{row.idempotency_key}:checkpoint-gate",
        "attempt_id": None,
        "contract_id": None,
        "snapshot_id": None,
        "group_idx": group_idx,
        "stage": str(getattr(projection, "stage", "") or ""),
        "name": str(getattr(projection, "name", "") or f"checkpoint:g{group_idx}"),
        "status": status,
        "deterministic": True,
        "source_ref": projection_key,
        "artifact_id": None,
        "artifact_key": projection_key,
        "event_id": None,
        "input_refs": input_refs,
        "output_refs": [],
        "failure_id": None,
        "verdict_id": None,
        "content_hash": stable_digest(payload),
        "summary": str(getattr(projection, "summary", "") or status),
        "metadata": {
            "source_table": str(getattr(projection, "source_table", "") or ""),
            "source_id": source_id,
        },
        "payload": payload,
        "started_at": None,
        "finished_at": None,
    }


def _legacy_event(
    row: ExecutionJournalRow,
    projection: CompatibilityProjection,
) -> tuple[str | None, str | None, dict[str, Any]]:
    payload = row.payload
    if payload.get("legacy_event_type"):
        metadata = _decode_json(payload.get("legacy_event_metadata"), {})
        return (
            str(payload["legacy_event_type"]),
            payload.get("legacy_event_content"),
            metadata if isinstance(metadata, dict) else {},
        )
    if row.entry_type == "commit_failure":
        stage = str(payload.get("stage") or "")
        content = payload.get("legacy_event_content") or (
            f"g{row.group_idx}:{stage}" if row.group_idx is not None and stage else projection.key
        )
        return (
            "dag_commit_failed",
            str(content),
            {
                "group_idx": row.group_idx,
                "stage": stage,
                "projection_key": projection.key,
            },
        )
    if row.entry_type == "group_checkpoint":
        content = payload.get("legacy_event_content") or (
            f"group {row.group_idx}" if row.group_idx is not None else projection.key
        )
        return (
            "dag_group_checkpoint",
            str(content),
            {
                "group_idx": row.group_idx,
                "projection_key": projection.key,
            },
        )
    return None, None, {}


def _advisory_lock_key(feature_id: str, idempotency_key: str) -> int:
    digest = stable_digest({"feature_id": feature_id, "idempotency_key": idempotency_key})
    return int(digest[:16], 16) - (1 << 63)


def _legacy_to_str(value: Any) -> str:
    try:
        from iriai_compose import to_str as compose_to_str
    except ModuleNotFoundError:
        compose_to_str = None
    if compose_to_str is not None:
        return str(compose_to_str(value))
    model_dump_json = getattr(value, "model_dump_json", None)
    if model_dump_json is not None:
        try:
            return model_dump_json(indent=2)
        except TypeError:
            return model_dump_json()
    return str(value)


def _is_public_artifact_key(key: str) -> bool:
    return key == "public-summary" or key.startswith("public-")


def _guess_content_type(key: str, value: str) -> str:
    key_lower = key.lower()
    stripped = value.lstrip()
    if key_lower.endswith(".json") or stripped.startswith(("{", "[")):
        return "application/json"
    if key_lower.endswith(".html") or stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        return "text/html"
    if key_lower.endswith(".md") or stripped.startswith("#"):
        return "text/markdown"
    return "text/plain"


def _bounded_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{clipped}\n[truncated public dashboard payload: {len(encoded)} bytes]"


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _public_dashboard_max_content_bytes() -> int:
    raw = os.getenv(PUBLIC_DASHBOARD_MAX_CONTENT_BYTES_ENV)
    if raw is None:
        return PUBLIC_DASHBOARD_DEFAULT_MAX_CONTENT_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        return PUBLIC_DASHBOARD_DEFAULT_MAX_CONTENT_BYTES


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if model_dump is not None:
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {"value": dumped}
    if isinstance(value, str):
        decoded = _decode_json(value, None)
        return dict(decoded) if isinstance(decoded, dict) else {"value": value}
    return {"value": value}


def _legacy_resume_artifact_summary(row: Any, preview_chars: int) -> dict[str, Any]:
    record = dict(row)
    value = "" if record.get("value") is None else str(record.get("value"))
    bounded = value[:preview_chars]
    value_chars = record.get("value_chars")
    if value_chars is None:
        value_chars = len(value)
    summary_only = bool(record.get("summary_only")) or len(value) > preview_chars
    record["value"] = bounded
    record["value_preview"] = record.get("value_preview") or bounded
    record["value_chars"] = int(value_chars)
    record["value_bytes"] = int(record.get("value_bytes") or len(value.encode("utf-8")))
    record["summary_only"] = summary_only
    return record


def _payload_projection_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    model_dump_json = getattr(value, "model_dump_json", None)
    if model_dump_json is not None:
        return str(model_dump_json())
    return stable_json(_payload_dict(value))


def _workspace_snapshot_digest(payload: dict[str, Any]) -> str:
    return stable_digest(_workspace_snapshot_stable_payload(payload))


def _workspace_snapshot_differing_keys(
    stored: dict[str, Any], new: dict[str, Any] | None
) -> str:
    if new is None:
        return "(unknown)"
    a = _workspace_snapshot_stable_payload(stored)
    b = _workspace_snapshot_stable_payload(new)
    keys = sorted(k for k in (set(a) | set(b)) if a.get(k) != b.get(k))
    return ",".join(keys) or "(none)"


def _workspace_snapshot_projection_value(payload: dict[str, Any]) -> str:
    return _bounded_workspace_projection_value(_workspace_snapshot_stable_payload(payload))


def _workspace_snapshot_stable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {
            "acl_artifact_id",
            # Per-attempt bookkeeping counter (retry*1000+repair_idx on the
            # repair-dispatch path), NOT part of the workspace identity that the
            # idempotency key encodes. Leaving it in the digest made a resume
            # re-record the same snapshot under the same key with a different
            # digest -> IdempotencyConflict that dead-locked resume.
            "attempt_id",
            "captured_at",
            "compatibility_projection_artifact_ids",
            "registry_artifact_id",
            "validated_at",
        }
    }


def _bounded_workspace_projection_value(value: Any) -> str:
    return stable_json(_bounded_workspace_projection_payload(value))


def _bounded_workspace_projection_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return {"bounded": "max_depth"}
    if isinstance(value, dict):
        return {
            str(key): _bounded_workspace_projection_payload(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else list(value)
        bounded = [
            _bounded_workspace_projection_payload(item, depth=depth + 1)
            for item in items[:WORKSPACE_PROJECTION_LIST_LIMIT]
        ]
        if len(items) > WORKSPACE_PROJECTION_LIST_LIMIT:
            bounded.append({
                "bounded": "list_truncated",
                "omitted": len(items) - WORKSPACE_PROJECTION_LIST_LIMIT,
            })
        return bounded
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) <= WORKSPACE_PROJECTION_STRING_LIMIT:
            return value
        clipped = encoded[:WORKSPACE_PROJECTION_STRING_LIMIT].decode("utf-8", errors="ignore")
        return f"{clipped}...[truncated {len(encoded) - WORKSPACE_PROJECTION_STRING_LIMIT} bytes]"
    return value


def _validate_workspace_digest(
    payload: dict[str, Any],
    digest: str,
    *,
    field: str,
    evidence_kind: str,
) -> None:
    payload_digest = str(payload.get(field) or "")
    if digest and payload_digest and digest != payload_digest:
        raise IdempotencyConflict(
            f"stale {evidence_kind} evidence: {field} mismatch"
        )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _decode_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _record_get(row: Any, key: str) -> Any:
    if hasattr(row, "get"):
        return row.get(key)
    return row[key]


async def _fetchval(conn: Any, query: str, *args: Any) -> Any:
    fetchval = getattr(conn, "fetchval", None)
    if fetchval is not None:
        return await fetchval(query, *args)
    row = await conn.fetchrow(query, *args)
    if row is None:
        return None
    return _record_get(row, "id")
