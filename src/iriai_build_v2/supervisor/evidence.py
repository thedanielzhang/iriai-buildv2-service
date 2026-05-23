from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from ..execution_control.store import (
    ExecutionControlStore,
    fetch_control_plane_snapshot,
)
from .models import (
    ArtifactRecord,
    BridgeProbe,
    CurrentWorkflowSnapshot,
    EventRecord,
    FeatureSnapshot,
    GitPathFact,
    SupervisorObservation,
    WorktreeProbe,
)
from .stale_codex import detect_stale_codex_invocations

KEY_PREFIXES = (
    "dag-verify:",
    "dag-verify-graph:",
    "dag-repair-preflight:",
    "dag-authority-gate:",
    "dag-direct-repair-route:",
    "dag-repair-expanded-verify:",
    "dag-repair-lens:",
    "dag-verify-rca:",
    "dag-repair-dispatch:",
    "dag-fix:",
    "dag-task-contract:",
    "dag-contract-verdict:",
    "dag-sandbox-patch:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-task-product-reconcile:",
    "dag-commit-failure:",
    "dag-runtime-failure:",
    "dag-group:",
    "dag-path-canonicalization:",
    "dag-worktree-alias-preflight:",
    "dag-worktree-alias-canonicalization:",
    "dag-workspace-acl-normalization:",
    "dag-workspace-permission-repair:",
    "dag-writeability-preflight:",
    "runtime-workspace-binding:",
    "dag-runtime-workspace-binding:",
    "workspace-authority-",
    "workflow-blocker:",
    "bug-",
    "finding-ledger",
)
_MAX_BRIDGE_LOG_LINES = 500
_MAX_BRIDGE_ERROR_LINES = 100
_MAX_BRIDGE_LINE_CHARS = 1000
_CURRENT_EVENT_TYPES = {
    "dag_task_dispatch",
    "dag_task_start",
    "dag_task_finish",
    "agent_start",
    "agent_done",
    "agent_invocation_start",
    "agent_invocation_done",
    "dag_verify_start",
    "dag_verify_finish",
    "dag_commit_failed",
    "control_plane_runtime_failure",
}


def _bool_metadata(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_bool_metadata(value: Any) -> bool | None:
    if value is None:
        return None
    return _bool_metadata(value)


def _runtime_failure_typed_bool(
    route_decision: Any,
    key: str,
    fallback: Any,
) -> bool | None:
    if isinstance(route_decision, dict) and route_decision.get(key) is not None:
        return _bool_metadata(route_decision.get(key))
    return _optional_bool_metadata(fallback)


class FeatureStoreReader(Protocol):
    async def get_feature(self, feature_id: str) -> Any | None: ...

    async def get_events(self, feature_id: str) -> list[dict[str, Any]]: ...

    async def list_event_summaries(
        self,
        feature_id: str,
        *,
        after_id: int = 0,
        limit: int = 50,
        order: str = "asc",
        group_idx: int | None = None,
        preview_chars: int = 700,
    ) -> list[dict[str, Any]]: ...


class ArtifactStoreReader(Protocol):
    async def get_record(self, key: str, *, feature: Any) -> dict[str, Any] | None: ...

    async def list_records(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...],
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ) -> list[dict[str, Any]]: ...


class DashboardClient(Protocol):
    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...


async def collect_evidence(
    *,
    feature_id: str,
    feature_store: FeatureStoreReader,
    artifact_store: ArtifactStoreReader | Any,
    cursor: int = 0,
    event_cursor: int | None = None,
    artifact_cursor: int | None = None,
    dashboard_url: str | None = None,
    dashboard_client: DashboardClient | None = None,
    bridge_log_cursor: int = 0,
    worktree_roots: list[Path | str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> SupervisorObservation:
    feature = await feature_store.get_feature(feature_id)
    feature_snapshot = _feature_snapshot(feature, feature_id)
    event_start_cursor = cursor if event_cursor is None else event_cursor
    artifact_start_cursor = cursor if artifact_cursor is None else artifact_cursor
    base_events = await _load_all_events(feature_store, feature_id)
    artifacts = await _load_artifacts(
        artifact_store,
        feature,
        feature_id,
        artifact_start_cursor,
    )
    control_plane_events = await _load_control_plane_failure_events(
        artifact_store,
        feature_id,
    )
    control_plane_snapshot = await _load_control_plane_snapshot(
        artifact_store,
        feature_id,
    )
    # Slice 10e — doc-10 § "Refactoring Steps" step 4: read the typed snapshot
    # FIRST. The typed `ControlPlaneSnapshot` populates `control_plane`;
    # `evidence_mode` follows its `source`. When the typed read is unavailable
    # (no pool / a store error) `typed_control_plane` is `None` and
    # `evidence_mode` is `legacy_fallback` (NOT `typed`) — fail-safe, the
    # classifier stays on the legacy artifact path.
    typed_control_plane = await load_typed_control_plane_snapshot(
        artifact_store,
        feature_id,
    )
    evidence_mode = evidence_mode_for_snapshot(typed_control_plane)
    all_events = _sort_events_by_recency([*base_events, *control_plane_events])
    events = [
        record
        for record in base_events
        if (record.id or 0) > event_start_cursor
    ]
    events.extend(control_plane_events)
    events = _sort_events_by_recency(events)
    bridge = None
    if dashboard_url or dashboard_client:
        bridge = await probe_bridge(
            dashboard_url=dashboard_url,
            client=dashboard_client,
            after=bridge_log_cursor,
        )
    current = build_current_workflow_snapshot(
        events=all_events,
        artifacts=artifacts,
        bridge=bridge,
        phase=feature_snapshot.phase,
    )
    stale_codex_invocations = detect_stale_codex_invocations(
        feature_id=feature_id,
        bridge=bridge,
        events=all_events,
        current=current,
    )
    worktrees = [
        probe_worktree(Path(root), forbidden_paths=forbidden_paths or [])
        for root in (worktree_roots or [])
    ]
    next_event_cursor = max(
        [event_start_cursor] + [event.id or event_start_cursor for event in base_events]
    )
    next_artifact_cursor = max(
        [artifact_start_cursor] + [artifact.id or artifact_start_cursor for artifact in artifacts]
    )
    next_cursor = max(cursor, next_event_cursor, next_artifact_cursor)
    return SupervisorObservation(
        feature_id=feature_id,
        phase=feature_snapshot.phase,
        event_cursor=event_start_cursor,
        next_event_cursor=next_event_cursor,
        artifact_cursor=artifact_start_cursor,
        next_artifact_cursor=next_artifact_cursor,
        bridge_log_cursor=bridge.log_cursor if bridge is not None else bridge_log_cursor,
        cursor=cursor,
        next_cursor=next_cursor,
        feature=feature_snapshot,
        events=events,
        artifacts=artifacts,
        bridge=bridge,
        current=current,
        control_plane_snapshot=control_plane_snapshot,
        control_plane=typed_control_plane,
        evidence_mode=evidence_mode,
        worktrees=worktrees,
        stale_codex_invocations=stale_codex_invocations,
        query_labels=[
            "feature",
            "events",
            "artifacts",
            "control_plane",
            "control_plane_typed",
            "bridge",
            "worktrees",
        ],
    )


async def probe_bridge(
    *,
    dashboard_url: str | None = None,
    client: DashboardClient | None = None,
    after: int = 0,
) -> BridgeProbe:
    probe = BridgeProbe(dashboard_url=dashboard_url, log_cursor=after)
    try:
        if client is None:
            if not dashboard_url:
                return probe.model_copy(update={"errors": ["dashboard_url missing"]})
            client = UrlDashboardClient(dashboard_url)
        status = await client.get_json("/api/bridge/status")
        logs = await client.get_json("/api/bridge/logs", {"after": after})
        lines = [str(line) for line in logs.get("lines", [])]
        if after:
            try:
                tail_logs = await client.get_json("/api/bridge/logs")
                lines = _dedupe_strings(
                    [*[str(line) for line in tail_logs.get("lines", [])], *lines]
                )
            except Exception:
                pass
        errors = [line for line in lines if _bridge_log_is_error(line)]
        truncated_line_count = max(0, len(lines) - _MAX_BRIDGE_LOG_LINES)
        truncated_error_count = max(0, len(errors) - _MAX_BRIDGE_ERROR_LINES)
        lines = [_shorten_log_line(line) for line in lines[-_MAX_BRIDGE_LOG_LINES:]]
        errors = [_shorten_log_line(line) for line in errors[-_MAX_BRIDGE_ERROR_LINES:]]
        return probe.model_copy(
            update={
                "ok": True,
                "status": status,
                "log_cursor": int(logs.get("cursor", after) or 0),
                "log_lines": lines,
                "errors": errors,
                "truncated_log_line_count": truncated_line_count,
                "truncated_error_count": truncated_error_count,
            }
        )
    except Exception as exc:
        return probe.model_copy(update={"errors": [f"{type(exc).__name__}: {exc}"]})


class UrlDashboardClient:
    def __init__(self, base_url: str, *, timeout: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_json, "GET", path, params, None)

    async def post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_json, "POST", path, None, payload or {})

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}{query}",
            data=data,
            method=method,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw or "{}")


def probe_worktree(
    root: Path,
    *,
    forbidden_paths: list[str] | None = None,
    direct_repo_roots: list[Path] | None = None,
) -> WorktreeProbe:
    root = root.resolve()
    probe = WorktreeProbe(root=str(root))
    if not root.exists():
        return probe.model_copy(update={"ok": False, "errors": [f"{root} does not exist"]})
    direct_repo_set = {root}
    direct_repo_set.update(path.resolve() for path in (direct_repo_roots or []))
    embedded = _find_embedded_git_dirs(root, direct_repo_set)
    pending, proposed = _find_pending_or_proposed(root)
    unwritable = _find_unwritable_targets(root, forbidden_paths or [])
    dirty, gitlinks, branch, git_errors = _git_facts(root)
    forbidden = _forbidden_facts(root, forbidden_paths or [], dirty)
    return WorktreeProbe(
        root=str(root),
        ok=not git_errors,
        branch=branch,
        dirty_paths=dirty,
        embedded_git_paths=embedded,
        gitlinks=gitlinks,
        forbidden_paths=forbidden,
        pending_paths=pending,
        proposed_paths=proposed,
        unwritable_paths=unwritable,
        errors=git_errors,
    )


async def _load_all_events(
    feature_store: FeatureStoreReader,
    feature_id: str,
) -> list[EventRecord]:
    if hasattr(feature_store, "list_event_summaries"):
        rows = await feature_store.list_event_summaries(
            feature_id,
            after_id=0,
            limit=500,
            order="desc",
            preview_chars=700,
        )
        records = [_event_record(row) for row in rows]
        return sorted(records, key=lambda record: record.id or 0)
    get_events = getattr(feature_store, "get_events", None)
    if callable(get_events):
        rows = await get_events(feature_id)
        records = [_event_record(row) for row in rows]
        return _sort_events_by_recency(records)[-500:]
    return []


async def _load_artifacts(
    artifact_store: Any,
    feature: Any,
    feature_id: str,
    cursor: int,
) -> list[ArtifactRecord]:
    if hasattr(artifact_store, "list_record_summaries"):
        rows = await _list_artifact_record_summaries(
            artifact_store,
            feature_id=feature_id,
            prefixes=KEY_PREFIXES,
            after_id=cursor,
            limit=500,
            order="asc",
        )
        latest_rows: list[dict[str, Any]] = []
        for prefix in KEY_PREFIXES:
            latest_rows.extend(
                await _list_artifact_record_summaries(
                    artifact_store,
                    feature_id=feature_id,
                    prefixes=(prefix,),
                    after_id=0,
                    limit=50,
                    order="desc",
                )
            )
        return sorted(
            _dedupe_artifact_records(
                [*_artifact_records(rows), *_artifact_records(latest_rows)]
            ),
            key=lambda item: item.id or 0,
        )
    pool = getattr(artifact_store, "_pool", None)
    if pool is not None:
        limit = 500
        clauses = " OR ".join(f"key LIKE ${idx + 4}" for idx, _prefix in enumerate(KEY_PREFIXES))
        rows = await pool.fetch(
            f"""
            SELECT id, key, created_at,
                   pg_column_size(value)::bigint AS stored_bytes,
                   substring(value from 1 for 2000) AS value_preview,
                   TRUE AS summary_only
            FROM artifacts
            WHERE feature_id = $1 AND id > $2 AND ({clauses})
            ORDER BY id
            LIMIT $3
            """,
            feature_id,
            cursor,
            limit,
            *[f"{prefix}%" for prefix in KEY_PREFIXES],
        )
        return [_artifact_record(dict(row)) for row in rows]
    if hasattr(artifact_store, "list_records"):
        return [_legacy_artifact_projection_unsupported_record(feature_id)]
    if feature is None or not hasattr(artifact_store, "get_record"):
        return []
    records: list[ArtifactRecord] = []
    for key in _candidate_keys(feature):
        row = await artifact_store.get_record(key, feature=feature)
        if row is None:
            continue
        row = {**row, "key": key}
        record = _artifact_record(row)
        if (record.id or 0) > cursor:
            records.append(record)
    return sorted(records, key=lambda item: item.id or 0)


def _legacy_artifact_projection_unsupported_record(feature_id: str) -> ArtifactRecord:
    payload = {
        "failure_class": "artifact_evidence",
        "failure_type": "legacy_artifact_projection_unbounded",
        "route": "workflow_unblock",
        "deterministic_workflow_blocker": True,
        "summary": (
            "Artifact store exposes list_records without bounded summaries; "
            "broad legacy blocker projections cannot be read safely."
        ),
        "feature_id": feature_id,
    }
    preview = json.dumps(payload, sort_keys=True)
    return ArtifactRecord(
        id=None,
        key="workflow-blocker:artifact-evidence-unsupported",
        value="",
        sha256=hashlib.sha256(preview.encode("utf-8")).hexdigest(),
        value_preview=preview,
        summary_only=True,
    )


async def _load_control_plane_failure_events(
    artifact_store: Any,
    feature_id: str,
    *,
    limit: int = 40,
) -> list[EventRecord]:
    loader = getattr(artifact_store, "list_control_plane_failure_summaries", None)
    if callable(loader):
        try:
            rows = await loader(feature_id=feature_id, limit=limit)
        except Exception:
            return []
    else:
        pool = getattr(artifact_store, "_pool", None)
        if pool is None:
            return []
        query = (
            "SELECT id, attempt_id, group_idx, stage, name, status, "
            "deterministic, source_ref, substring(summary from 1 for 500) AS summary, "
            "char_length(summary) AS summary_length, "
            "octet_length(summary) AS summary_bytes, created_at, "
            "payload->'route_decision' AS route_decision, "
            "payload->'retry_budget' AS retry_budget, "
            "payload->'affected_product_files' AS affected_product_files, "
            "payload->'canonical_product_files' AS canonical_product_files, "
            "payload->'concerns' AS concerns, "
            "payload->'issues' AS issues, "
            "payload->'product_defect' AS product_defect, "
            "payload->'product_evidence' AS product_evidence, "
            "payload->'product_files' AS product_files, "
            "payload->'product_paths' AS product_paths, "
            "payload->'semantic_product_failure' AS semantic_product_failure, "
            "payload->'test_failures' AS test_failures, "
            "payload->>'failure_class' AS failure_class, "
            "payload->>'failure_type' AS failure_type, "
            "payload->>'route' AS route, "
            "(payload->>'operator_required')::boolean AS operator_required, "
            "(payload->>'retryable')::boolean AS retryable "
            "FROM evidence_nodes WHERE feature_id = $1 "
            "AND kind = 'runtime_failure_context' "
            "ORDER BY id DESC LIMIT $2"
        )
        acquire = getattr(pool, "acquire", None)
        try:
            if callable(acquire):
                async with acquire() as conn:
                    rows = await conn.fetch(
                        query,
                        feature_id,
                        limit,
                    )
            else:
                fetch = getattr(pool, "fetch", None)
                if not callable(fetch):
                    return []
                rows = await fetch(
                    query,
                    feature_id,
                    limit,
                )
        except Exception:
            return []
    events: list[EventRecord] = []
    for row in rows:
        def get(key: str, default: Any = None) -> Any:
            if isinstance(row, dict):
                return row.get(key, default)
            try:
                return row[key]
            except (KeyError, IndexError, TypeError):
                return default

        raw_payload = get("payload", {}) or {}
        if isinstance(raw_payload, str):
            try:
                payload = json.loads(raw_payload)
            except Exception:
                payload = {}
        elif isinstance(raw_payload, dict):
            payload = dict(raw_payload)
        else:
            payload = {}
        for key in (
            "route_decision",
            "retry_budget",
            "affected_product_files",
            "canonical_product_files",
            "concerns",
            "issues",
            "product_defect",
            "product_evidence",
            "product_files",
            "product_paths",
            "semantic_product_failure",
            "test_failures",
        ):
            value = get(key)
            if value is not None:
                payload[key] = _decode_jsonish_metadata_value(value)
        row_id = get("id")
        name = str(get("name") or "")
        summary = str(get("summary") or "")
        summary_length = int(get("summary_length", len(summary)) or len(summary))
        summary_bytes = int(
            get("summary_bytes", len(summary.encode("utf-8")))
            or len(summary.encode("utf-8"))
        )
        route_decision = payload.get("route_decision")
        retry_budget = payload.get("retry_budget")
        deterministic = _runtime_failure_typed_bool(
            route_decision,
            "deterministic",
            get("deterministic", payload.get("deterministic")),
        )
        operator_required = _runtime_failure_typed_bool(
            route_decision,
            "operator_required",
            get("operator_required", payload.get("operator_required")),
        )
        retryable = _runtime_failure_typed_bool(
            route_decision,
            "retryable",
            get("retryable", payload.get("retryable")),
        )
        metadata = {
            "evidence_node_id": int(row_id) if row_id is not None else None,
            "group_idx": get("group_idx"),
            "attempt_id": get("attempt_id"),
            "failure_class": get("failure_class") or payload.get("failure_class"),
            "failure_type": get("failure_type") or payload.get("failure_type"),
            "route": get("route") or payload.get("route"),
            "status": get("status") or payload.get("status"),
            "deterministic": deterministic,
            "operator_required": operator_required,
            "retryable": retryable,
            "summary_length": summary_length,
            "summary_bytes": summary_bytes,
            "summary_truncated": summary_length > len(summary),
        }
        if isinstance(route_decision, dict):
            metadata["route_decision"] = route_decision
        if isinstance(retry_budget, dict):
            metadata["retry_budget"] = retry_budget
        for key in (
            "affected_product_files",
            "canonical_product_files",
            "concerns",
            "issues",
            "product_defect",
            "product_evidence",
            "product_files",
            "product_paths",
            "semantic_product_failure",
            "test_failures",
        ):
            if key in payload:
                metadata[key] = payload[key]
        events.append(
            EventRecord(
                id=None,
                event_type="control_plane_runtime_failure",
                source="execution_control",
                content=f"{name}: {summary}".strip(": "),
                metadata=metadata,
                created_at=get("created_at"),
            )
        )
    return events


def _decode_jsonish_metadata_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")) or stripped in {"true", "false", "null"}:
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


async def _load_control_plane_snapshot(
    artifact_store: Any,
    feature_id: str,
) -> dict[str, Any] | None:
    pool = getattr(artifact_store, "_pool", None)
    if pool is None:
        return None
    try:
        acquire = getattr(pool, "acquire", None)
        if callable(acquire):
            async with acquire() as conn:
                snapshot = await fetch_control_plane_snapshot(conn, feature_id)
        else:
            snapshot = await fetch_control_plane_snapshot(pool, feature_id)
        return snapshot.model_dump(mode="json")
    except Exception:
        return None


# Slice 10e — the doc-10 § "Refactoring Steps" step-4 `evidence_mode` wiring.
#
# `collect_evidence` / `SupervisorEvidenceMcpService.get_current_snapshot` are
# read paths; doc 10 step 4 says: "Update `collect_evidence` and
# `get_current_snapshot` to call the typed snapshot first. Legacy
# artifact/event summaries are used only when typed tables are absent or the
# typed query degrades." `load_typed_control_plane_snapshot` reads the
# Slice-10a bounded `ExecutionControlStore.get_control_plane_snapshot` (already
# `LIMIT cap+1` truncated, `SET LOCAL statement_timeout` bounded, feature-scoped
# and SUMMARY-ONLY — no artifact bodies); `evidence_mode_for_snapshot` derives
# the typed `SupervisorObservation.evidence_mode` from the snapshot's `source`.

# The supervisor calls the typed snapshot read with the doc-10 "supervisor"
# scope (doc 10 § "Proposed Interfaces/Types" — `SnapshotScope`).
_SUPERVISOR_SNAPSHOT_SCOPE = "supervisor"


async def load_typed_control_plane_snapshot(
    artifact_store: Any,
    feature_id: str,
    *,
    fallback_pool_source: Any | None = None,
) -> Any | None:
    """Return the Slice-10a bounded typed :class:`ControlPlaneSnapshot`, or None.

    doc 10 § "Refactoring Steps" step 4: the supervisor evidence collection
    reads the TYPED snapshot first. This resolves the Postgres pool off
    ``artifact_store._pool`` (and, when absent, ``fallback_pool_source._pool``
    — matching the legacy :func:`_load_control_plane_snapshot` /
    ``SupervisorEvidenceMcpService._control_plane_snapshot`` resolution so the
    typed path is never silently skipped on an asymmetric store wiring) and
    calls the bounded Slice-10a
    ``ExecutionControlStore.get_control_plane_snapshot`` — a feature-scoped,
    ``LIMIT cap+1``-truncated, ``statement_timeout``-bounded, SUMMARY-ONLY read
    (constraint: the typed read MUST stay bounded; this introduces no new
    unbounded or artifact-body read).

    FAIL-SAFE (doc 10 § "Edge Cases And Failure Handling": "Typed state
    unavailable: supervisor returns degraded legacy summary"): returns ``None``
    when there is no Postgres pool, or the typed read raises. The caller then
    derives a non-``"typed"`` ``evidence_mode`` and the classifier correctly
    falls back to the legacy artifact classifiers — fail-safe, never
    fail-open-to-wrong-mode.

    READ-ONLY contract (Slice 10c-1): ``ExecutionControlStore`` is constructed
    here purely to call its READ method ``get_control_plane_snapshot``; no
    writer is invoked. The supervisor evidence service holds no
    ``ExecutionControlStore`` *slot* (that is the Slice-10c-1 mechanical
    guarantee against an execution-authority WRITE); a transient store
    constructed for a single bounded READ does not breach that — it is the
    Slice-10a read path the dashboard already uses.
    """

    pool = getattr(artifact_store, "_pool", None)
    if pool is None and fallback_pool_source is not None:
        pool = getattr(fallback_pool_source, "_pool", None)
    if pool is None:
        return None
    try:
        from ..workflows.develop.execution.snapshots import (
            ControlPlaneSnapshotQuery,
        )

        query = ControlPlaneSnapshotQuery(
            feature_id=feature_id,
            scope=_SUPERVISOR_SNAPSHOT_SCOPE,
        )
        store = ExecutionControlStore(pool)
        return await store.get_control_plane_snapshot(query)
    except Exception:
        # Fail-safe: any typed-read failure (missing typed tables, a store
        # error, a degenerate query plan) yields no typed snapshot, so the
        # caller stays on the legacy path. NEVER `evidence_mode == "typed"`.
        return None


def evidence_mode_for_snapshot(snapshot: Any | None) -> str:
    """Derive the typed ``SupervisorObservation.evidence_mode`` from a snapshot.

    doc 10 § "Refactoring Steps" step 4 + STATUS.md: ``evidence_mode`` follows
    the typed snapshot's ``source`` (doc 10 § "Proposed Interfaces/Types": the
    snapshot carries ``source`` / ``degraded`` / ``degradation_reasons``):

    * ``"typed"`` — the normal path: a typed snapshot whose ``source ==
      "typed"`` and which did NOT degrade. The Slice-10c-2 typed-PRIMARY
      classifier path goes LIVE only for this mode.
    * ``"mixed"`` — a typed snapshot that partially degraded (doc 10 § "Edge
      Cases": "Typed query timeout: return ``source="mixed"``, ``degraded=true``"
      — the Slice-10a builder also flags ``degraded`` on a per-section bounded
      timeout). The legacy artifact classifiers run as the fallback.
    * ``"legacy_fallback"`` — no typed snapshot at all (the FAIL-SAFE: an old
      feature with no typed rows, or a typed read that was unavailable / raised
      — :func:`load_typed_control_plane_snapshot` returned ``None``), OR a typed
      snapshot whose own ``source`` is ``legacy_fallback``.

    The classifier (``SupervisorClassifier.classify`` / ``_Context``) treats
    ONLY ``evidence_mode == "typed"`` as typed-primary; ``"mixed"`` /
    ``"legacy_fallback"`` / ``""`` run the legacy artifact classifiers. So a
    missing or degraded typed snapshot can never silently activate the
    typed-primary path — fail-safe.
    """

    if snapshot is None:
        return "legacy_fallback"
    source = str(_snapshot_attr(snapshot, "source", "") or "").strip().lower()
    degraded = bool(_snapshot_attr(snapshot, "degraded", False))
    if source == "typed" and not degraded:
        return "typed"
    if source == "legacy_fallback":
        return "legacy_fallback"
    # A degraded typed snapshot, or any non-`typed`/non-`legacy_fallback`
    # source, is `mixed` — typed rows present but only partially trustworthy,
    # so the classifier uses the legacy fallback (never typed-primary).
    return "mixed"


def _snapshot_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off a typed snapshot (Pydantic model or dict) uniformly."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def _list_artifact_records(
    artifact_store: Any,
    *,
    feature_id: str,
    prefixes: tuple[str, ...],
    after_id: int,
    limit: int,
    order: str,
) -> list[dict[str, Any]]:
    try:
        return await artifact_store.list_records(
            feature_id=feature_id,
            prefixes=prefixes,
            after_id=after_id,
            limit=limit,
            order=order,
        )
    except TypeError:
        try:
            return await artifact_store.list_records(
                feature_id=feature_id,
                prefixes=prefixes,
                after_id=after_id,
                limit=limit,
            )
        except TypeError:
            return await artifact_store.list_records(
                feature_id=feature_id,
                prefixes=prefixes,
                after_id=after_id,
            )


async def _list_artifact_record_summaries(
    artifact_store: Any,
    *,
    feature_id: str,
    prefixes: tuple[str, ...],
    after_id: int,
    limit: int,
    order: str,
) -> list[dict[str, Any]]:
    if hasattr(artifact_store, "list_record_summaries"):
        try:
            return await artifact_store.list_record_summaries(
                feature_id=feature_id,
                prefixes=prefixes,
                after_id=after_id,
                limit=limit,
                order=order,
            )
        except TypeError:
            return await artifact_store.list_record_summaries(
                feature_id=feature_id,
                prefixes=prefixes,
                after_id=after_id,
                limit=limit,
            )
    pool = getattr(artifact_store, "_pool", None)
    if pool is None:
        return []
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    args: list[Any] = [feature_id, after_id, limit]
    prefix_clause = ""
    if prefixes:
        prefix_clause = " AND (" + " OR ".join(
            f"key LIKE ${idx + 4}" for idx, _prefix in enumerate(prefixes)
        ) + ")"
        args.extend(f"{prefix}%" for prefix in prefixes)
    rows = await pool.fetch(
        f"""
        SELECT id, key, created_at,
               pg_column_size(value)::bigint AS stored_bytes,
               substring(value from 1 for 2000) AS value_preview,
               TRUE AS summary_only
        FROM artifacts
        WHERE feature_id = $1 AND id > $2{prefix_clause}
        ORDER BY id {direction}
        LIMIT $3
        """,
        *args,
    )
    return [dict(row) for row in rows]


def _candidate_keys(feature: Any) -> list[str]:
    metadata = getattr(feature, "metadata", {}) or {}
    groups = metadata.get("supervisor_groups") or metadata.get("dag_groups") or []
    retries = metadata.get("supervisor_retries") or ["initial", "retry-initial", "retry-0", "retry-1"]
    task_ids = metadata.get("supervisor_task_ids") or metadata.get("dag_task_ids") or []
    repo_ids = metadata.get("supervisor_repo_ids") or metadata.get("repo_ids") or ["main"]
    keys: list[str] = []
    for group in groups:
        group_text = str(group).removeprefix("g")
        keys.append(f"dag-group:{group_text}")
        for task_id in task_ids:
            keys.append(f"dag-task-contract:{task_id}")
        for retry in retries:
            keys.extend(
                [
                    f"dag-verify:g{group_text}:{retry}",
                    f"dag-verify-graph:g{group_text}:{retry}",
                    f"dag-repair-preflight:g{group_text}:{retry}",
                    f"dag-authority-gate:g{group_text}:{retry}",
                    f"dag-direct-repair-route:g{group_text}:{retry}",
                    f"dag-repair-expanded-verify:g{group_text}:{retry}",
                    f"dag-verify-rca:g{group_text}:{retry}",
                    f"dag-repair-dispatch:g{group_text}:{retry}",
                    f"dag-fix:g{group_text}:{retry}",
                    f"dag-task-reconcile:g{group_text}:{retry}",
                    f"dag-task-spec-reconcile:g{group_text}:{retry}",
                    f"dag-task-product-reconcile:g{group_text}:{retry}",
                    f"dag-commit-failure:g{group_text}:{retry}",
                    f"dag-runtime-failure:g{group_text}:verify-{retry}",
                    f"dag-runtime-failure:g{group_text}:rca-{retry}",
                    f"dag-worktree-alias-preflight:g{group_text}:{retry}",
                    f"dag-worktree-alias-canonicalization:g{group_text}:{retry}",
                    f"workspace-authority-preflight:g{group_text}:{retry}",
                    f"workspace-authority-routes:g{group_text}:{retry}",
                    f"workspace-authority-snapshot:g{group_text}:{retry}",
                ]
            )
            for task_id in task_ids:
                keys.append(f"dag-contract-verdict:g{group_text}:{task_id}:{retry}")
            for repo_id in repo_ids:
                keys.append(f"dag-sandbox-patch:g{group_text}:{retry}:repo-{repo_id}")
        keys.append(f"dag-worktree-alias-preflight:g{group_text}:initial-dispatch")
        keys.extend(
            [
                f"workspace-authority-registry:g{group_text}",
                f"workspace-authority-preflight:g{group_text}:initial-dispatch",
                f"workspace-authority-routes:g{group_text}:initial-dispatch",
                f"workspace-authority-snapshot:g{group_text}:initial-dispatch",
                "workflow-blocker:verify",
                "workflow-blocker:verifier",
                "dag-runtime-failure:source-push",
                "dag-runtime-failure:notify",
            ]
        )
    return keys


def _feature_snapshot(feature: Any, feature_id: str) -> FeatureSnapshot:
    if feature is None:
        return FeatureSnapshot(feature_id=feature_id)
    metadata = dict(getattr(feature, "metadata", {}) or {})
    return FeatureSnapshot(
        feature_id=getattr(feature, "id", feature_id),
        name=getattr(feature, "name", ""),
        slug=getattr(feature, "slug", ""),
        workflow_name=getattr(feature, "workflow_name", ""),
        workspace_id=getattr(feature, "workspace_id", ""),
        phase=str(metadata.get("_db_phase") or metadata.get("phase") or ""),
        metadata=metadata,
    )


def _artifact_record(row: dict[str, Any]) -> ArtifactRecord:
    value = row.get("value", row.get("value_preview", ""))
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return ArtifactRecord(
        id=row.get("id"),
        key=str(row.get("key", "")),
        value=value,
        created_at=row.get("created_at"),
        sha256=row.get("sha256")
        or (
            None
            if row.get("summary_only", False)
            else hashlib.sha256(text.encode("utf-8")).hexdigest()
        ),
        stored_bytes=row.get("stored_bytes"),
        value_preview=row.get("value_preview"),
        summary_only=bool(row.get("summary_only", False)),
    )


def _artifact_records(rows: list[dict[str, Any]]) -> list[ArtifactRecord]:
    records = [_artifact_record(row) for row in rows]
    return [record for record in records if record.key.startswith(KEY_PREFIXES)]


def _dedupe_artifact_records(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    seen: set[tuple[int | None, str, str | None]] = set()
    result: list[ArtifactRecord] = []
    for record in records:
        token = (record.id, record.key, record.sha256)
        if token in seen:
            continue
        seen.add(token)
        result.append(record)
    return result


def _event_record(row: dict[str, Any]) -> EventRecord:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {"raw": metadata}
    return EventRecord(
        id=row.get("id"),
        event_type=str(row.get("event_type") or row.get("type") or ""),
        source=str(row.get("source") or ""),
        content=row.get("content"),
        metadata=metadata,
        created_at=row.get("created_at"),
    )


def _sort_events_by_recency(events: list[EventRecord]) -> list[EventRecord]:
    return sorted(events, key=_event_recency_key)


def _event_recency_key(event: EventRecord) -> tuple[int, float, int]:
    sequence_id = _event_sequence_id(event)
    if event.created_at is not None:
        try:
            return (1, float(event.created_at.timestamp()), sequence_id)
        except Exception:
            pass
    return (0, float(sequence_id), sequence_id)


def _event_sequence_id(event: EventRecord) -> int:
    if event.id is not None:
        return int(event.id)
    metadata = event.metadata or {}
    evidence_node_id = metadata.get("evidence_node_id")
    try:
        return int(evidence_node_id)
    except (TypeError, ValueError):
        return 0


def build_current_workflow_snapshot(
    *,
    events: list[EventRecord],
    artifacts: list[ArtifactRecord],
    bridge: BridgeProbe | None,
    phase: str,
) -> CurrentWorkflowSnapshot:
    """Infer the live workflow position from current sources, not old artifacts.

    Events are the strongest signal because they record dispatch/invocation state.
    Artifact rows are secondary, and bridge logs are a fallback/supplement for
    active-agent UX when the DB has not emitted a fresh row yet.
    """

    sorted_events = _sort_events_by_recency(events)
    sorted_artifacts = sorted(artifacts, key=lambda artifact: artifact.id or 0)
    group_idx: int | None = None
    retry: int | None = None
    source = ""
    citations: list[str] = []

    for event in reversed(sorted_events):
        if event.event_type not in _CURRENT_EVENT_TYPES:
            continue
        group = _event_group(event)
        if group is None:
            continue
        group_idx = group
        retry = _event_retry(event)
        source = "event"
        citations.append(event.citation)
        break

    if group_idx is None:
        for artifact in reversed(sorted_artifacts):
            group = _artifact_group(artifact.key)
            if group is None:
                continue
            group_idx = group
            retry = _artifact_retry(artifact.key)
            source = "artifact"
            citations.append(artifact.citation)
            break

    if bridge is not None:
        bridge_group, bridge_retry, bridge_citation = _bridge_current_group(bridge)
        if group_idx is None and bridge_group is not None:
            group_idx = bridge_group
            retry = bridge_retry
            source = "bridge"
            citations.append(bridge_citation)

    active_agents = _active_agents(sorted_events, group_idx)
    queued_agents: list[str] = []
    if bridge is not None:
        bridge_active, bridge_queued = _bridge_agents(bridge, group_idx)
        active_agents = _dedupe_strings([*active_agents, *bridge_active])
        queued_agents = bridge_queued
        if (bridge_active or bridge_queued) and "dashboard:/api/bridge/logs" not in citations:
            citations.append("dashboard:/api/bridge/logs")

    state = _current_state(
        events=sorted_events,
        bridge=bridge,
        group_idx=group_idx,
        active_agents=active_agents,
    )
    latest_artifact_id = max((artifact.id or 0 for artifact in sorted_artifacts), default=0) or None
    latest_event_id = max((event.id or 0 for event in sorted_events), default=0) or None
    return CurrentWorkflowSnapshot(
        group_idx=group_idx,
        retry=retry,
        phase=phase,
        state=state,
        source=source,
        active_agents=active_agents,
        queued_agents=queued_agents,
        latest_event_id=latest_event_id,
        latest_artifact_id=latest_artifact_id,
        citations=_dedupe_strings(citations),
    )


def _current_state(
    *,
    events: list[EventRecord],
    bridge: BridgeProbe | None,
    group_idx: int | None,
    active_agents: list[str],
) -> str:
    if active_agents:
        if any(_actor_is_verifier(actor) for actor in active_agents):
            return "verifying"
        return "implementing"
    for event in reversed(events):
        if group_idx is not None and _event_group(event) != group_idx:
            continue
        if event.event_type in {"dag_verify_start", "dag_verify_finish"}:
            return "verifying"
        if event.event_type == "control_plane_runtime_failure":
            return "workflow_blocked"
        if event.event_type in {
            "dag_task_dispatch",
            "dag_task_start",
            "dag_task_finish",
            "agent_start",
            "agent_done",
            "agent_invocation_start",
            "agent_invocation_done",
        }:
            return "implementing"
    if bridge is not None and bridge.process_state == "running":
        return "running"
    return ""


def _active_agents(events: list[EventRecord], group_idx: int | None) -> list[str]:
    active: dict[str, None] = {}
    for event in events:
        group = _event_group(event)
        actor = _event_actor(event)
        actor_group = _group_from_text(actor)
        if group_idx is not None:
            if group is not None and group != group_idx:
                continue
            if group is None and actor_group != group_idx:
                continue
        actor = actor or event.source
        if not actor:
            continue
        if event.event_type == "agent_invocation_start":
            active[actor] = None
        elif event.event_type in {"agent_done", "agent_error", "agent_invocation_done"}:
            active.pop(actor, None)
    return sorted(active)


def _bridge_current_group(bridge: BridgeProbe) -> tuple[int | None, int | None, str]:
    for line in reversed(bridge.log_lines):
        group = _group_from_text(line)
        if group is not None:
            return group, _retry_from_text(line), "dashboard:/api/bridge/logs"
    return None, None, "dashboard:/api/bridge/logs"


def _bridge_agents(
    bridge: BridgeProbe,
    group_idx: int | None,
) -> tuple[list[str], list[str]]:
    active: dict[str, None] = {}
    queued: dict[str, None] = {}
    for line in bridge.log_lines:
        actor = _actor_from_bridge_line(line)
        if not actor:
            continue
        actor_group = _group_from_text(actor) or _group_from_text(line)
        if group_idx is not None and actor_group is not None and actor_group != group_idx:
            continue
        lowered = line.lower()
        if "agent concurrency queued" in lowered:
            queued[actor] = None
        elif "agent concurrency acquired" in lowered:
            active[actor] = None
            queued.pop(actor, None)
        elif "agent concurrency released" in lowered:
            active.pop(actor, None)
    return sorted(active), sorted(queued)


def _actor_from_bridge_line(line: str) -> str | None:
    match = re.search(r"\bactor=(?P<actor>[^\s]+)", line)
    if match:
        return match.group("actor").strip()
    match = re.search(r"\b(?P<actor>[a-z][\w-]*-g\d+[\w-]*)\b", line)
    return match.group("actor") if match else None


def _actor_is_verifier(actor: str) -> bool:
    lowered = actor.lower()
    return any(token in lowered for token in ("verify", "verifier", "smoke", "regression", "security"))


def _event_actor(event: EventRecord) -> str:
    metadata = event.metadata or {}
    for key in ("actor", "actor_name", "agent", "role", "runtime_actor"):
        value = metadata.get(key)
        if value:
            return str(value)
    text = f"{event.source} {event.content or ''}"
    match = re.search(r"\b(?P<actor>[a-z][\w-]*-g\d+[\w-]*)\b", text)
    return match.group("actor") if match else str(event.source or "")


def _event_group(event: EventRecord) -> int | None:
    metadata = event.metadata or {}
    for key in ("group_idx", "group", "group_id"):
        value = metadata.get(key)
        group = _coerce_group(value)
        if group is not None:
            return group
    text = f"{event.event_type} {event.source} {event.content or ''} {json.dumps(metadata, sort_keys=True, default=str)}"
    return _group_from_text(text)


def _event_retry(event: EventRecord) -> int | None:
    metadata = event.metadata or {}
    for key in ("retry", "attempt", "retry_idx"):
        retry = _retry_from_value(metadata.get(key))
        if retry is not None:
            return retry
    text = f"{event.event_type} {event.source} {event.content or ''} {json.dumps(metadata, sort_keys=True, default=str)}"
    return _retry_from_text(text)


def _artifact_group(key: str) -> int | None:
    match = re.search(r"(?:^|:)g(?P<group>\d+)(?::|$|-)", key)
    if match:
        return int(match.group("group"))
    match = re.search(r"^dag-group:(?P<group>\d+)(?::|$)", key)
    return int(match.group("group")) if match else None


def _artifact_retry(key: str) -> int | None:
    if key.endswith(":initial") or key.endswith(":retry-initial"):
        return 0
    return _retry_from_text(key)


def _group_from_text(text: str) -> int | None:
    match = re.search(r"(?:^|[^A-Za-z0-9])g(?P<group>\d+)(?:[^A-Za-z0-9]|$)", text)
    if match:
        return int(match.group("group"))
    match = re.search(r"\bgroup[=:\s-]+(?P<group>\d+)\b", text, re.IGNORECASE)
    return int(match.group("group")) if match else None


def _coerce_group(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip().removeprefix("g").removeprefix("G"))
    except ValueError:
        return None


def _retry_from_text(text: str) -> int | None:
    if re.search(r"(?:^|:)initial(?:$|:)", text):
        return 0
    match = re.search(r"\bretry[-_:=\s]+(?P<retry>\d+)\b", text, re.IGNORECASE)
    return int(match.group("retry")) if match else None


def _retry_from_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    if text in {"initial", "retry-initial"}:
        return 0
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _shorten_log_line(value: str) -> str:
    text = " ".join(str(value).split())
    if len(text) <= _MAX_BRIDGE_LINE_CHARS:
        return text
    return f"{text[: _MAX_BRIDGE_LINE_CHARS - 16]}... [truncated]"


def _bridge_log_is_error(line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ("traceback", "error", "exception", "crash", "disconnect"))


def _find_embedded_git_dirs(root: Path, direct_repo_roots: set[Path]) -> list[str]:
    embedded: list[str] = []
    for path in root.rglob(".git"):
        parent = path.parent.resolve()
        if parent in direct_repo_roots:
            continue
        embedded.append(_rel(root, path))
    return sorted(embedded)


def _find_pending_or_proposed(root: Path) -> tuple[list[str], list[str]]:
    pending: list[str] = []
    proposed: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        rel = _rel(root, path)
        if name.startswith("_pending_"):
            pending.append(rel)
        if name.endswith(".PROPOSED") or ".PROPOSED." in name:
            proposed.append(rel)
    return sorted(pending), sorted(proposed)


def _find_unwritable_targets(root: Path, paths: list[str]) -> list[str]:
    unwritable: list[str] = []
    for raw in paths:
        target = root / raw
        parent = target if target.exists() and target.is_dir() else target.parent
        if parent.exists() and not os.access(parent, os.W_OK):
            unwritable.append(raw)
    return sorted(unwritable)


def _git_facts(root: Path) -> tuple[list[GitPathFact], list[str], str | None, list[str]]:
    dirty: list[GitPathFact] = []
    gitlinks: list[str] = []
    errors: list[str] = []
    branch = None
    branch_result = _run_git(root, "branch", "--show-current")
    if branch_result.returncode == 0:
        branch = branch_result.stdout.strip() or None
    status_result = _run_git(root, "status", "--porcelain=v1")
    if status_result.returncode == 0:
        for line in status_result.stdout.splitlines():
            if not line:
                continue
            status = line[:2].strip()
            path = line[3:] if len(line) > 3 else line
            dirty.append(GitPathFact(path=path, reason="git-status", status=status))
    else:
        errors.append(status_result.stderr.strip() or status_result.stdout.strip() or "git status failed")
    ls_result = _run_git(root, "ls-files", "-s")
    if ls_result.returncode == 0:
        for line in ls_result.stdout.splitlines():
            parts = line.split(maxsplit=3)
            if len(parts) == 4 and parts[0] == "160000":
                gitlinks.append(parts[3])
    return dirty, sorted(gitlinks), branch, errors


def _forbidden_facts(
    root: Path,
    paths: list[str],
    dirty: list[GitPathFact],
) -> list[GitPathFact]:
    dirty_by_path = {fact.path: fact for fact in dirty}
    facts: list[GitPathFact] = []
    for raw in paths:
        path = root / raw
        if path.exists():
            facts.append(GitPathFact(path=raw, reason="exists-on-disk"))
        dirty_fact = dirty_by_path.get(raw)
        if dirty_fact is not None and dirty_fact.status != "D":
            facts.append(
                GitPathFact(path=raw, reason="tracked-or-staged", status=dirty_fact.status)
            )
    return facts


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
        )
    except Exception as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", f"{type(exc).__name__}: {exc}")


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
