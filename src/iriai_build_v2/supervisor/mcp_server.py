from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..config import DATABASE_URL, DASHBOARD_BASE_URL
from ..db import create_pool
from ..execution_control.store import fetch_control_plane_snapshot
from ..storage import PostgresArtifactStore, PostgresFeatureStore
from .evidence import (
    KEY_PREFIXES,
    _artifact_record,
    _event_record,
    _load_control_plane_failure_events,
    build_current_workflow_snapshot,
    evidence_mode_for_snapshot,
    load_typed_control_plane_snapshot,
    probe_bridge,
)
from .models import SupervisorInvestigationRequest
from .read_only import assert_read_only_supervisor_handles
from .tools import (
    SupervisorEvidenceToolbox,
    _artifact_text,
    artifact_summary,
)

_DEFAULT_INDEX_LIMIT = 50
_MAX_INDEX_LIMIT = 200
_DEFAULT_DETAIL_CHARS = 40_000
_MAX_DETAIL_CHARS = 120_000
_MAX_CHUNK_REFS = 30
_MAX_BRIDGE_LOG_LINES = 40
_MAX_BRIDGE_ERROR_LINES = 20
_MAX_BRIDGE_LINE_CHARS = 700
_MAX_EVENT_METADATA_TEXT_CHARS = 700
_MAX_EVENT_TASK_IDS = 30
_SNAPSHOT_EVENT_LIMIT = 500
_SNAPSHOT_ARTIFACT_LIMIT = 200
_SNAPSHOT_MATERIAL_LIMIT = 18
_SNAPSHOT_RECOMMENDED_DETAIL_LIMIT = 5
_SNAPSHOT_RECENT_CONTEXT_ID_WINDOW = 5_000
_SPILL_LOGICAL_BYTES_SQL = (
    "CASE WHEN value LIKE '{\"__iriai_spill_v1__\"%' THEN "
    "COALESCE(NULLIF(substring(value from '\"bytes\"\\s*:\\s*([0-9]+)'), '')::bigint, "
    "pg_column_size(value)::bigint) "
    "ELSE pg_column_size(value)::bigint END"
)
_CURRENT_GROUP_MATERIAL_PREFIXES = (
    "dag-verify:",
    "dag-repair-preflight:",
    "dag-authority-gate:",
    "dag-direct-repair-route:",
    "dag-repair-expanded-verify:",
    "dag-repair-lens:",
    "dag-verify-rca:",
    "dag-repair-dispatch:",
    "dag-fix:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-task-product-reconcile:",
    "dag-commit-failure:",
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
)
_UNGROUPED_MATERIAL_PREFIXES = (
    "bug-rca:",
    "bug-reverify:",
    "bug-fix:",
    "bug-artifact-repair:",
    "runtime-workspace-binding:",
    "dag-runtime-workspace-binding:",
)
_DETAIL_PRIORITY_PREFIXES = (
    "bug-reverify:",
    "dag-verify:",
    "bug-rca:",
    "dag-repair-expanded-verify:",
    "dag-repair-lens:",
    "dag-fix:",
    "dag-authority-gate:",
    "dag-workspace-acl-normalization:",
    "dag-workspace-permission-repair:",
    "dag-writeability-preflight:",
    "dag-worktree-alias-preflight:",
    "dag-worktree-alias-canonicalization:",
    "workspace-authority-",
    "runtime-workspace-binding:",
    "dag-runtime-workspace-binding:",
    "dag-repair-preflight:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-task-product-reconcile:",
    "dag-direct-repair-route:",
    "dag-commit-failure:",
)
_DEFAULT_SUPPRESSED_INDEX_KEYS = {
    "bug-fix-attempts",
    "finding-ledger",
}


class SupervisorEvidenceMcpService:
    """Read-only evidence API exposed to the workflow-supervisor agent."""

    def __init__(
        self,
        *,
        feature_store: Any | None = None,
        artifact_store: Any | None = None,
        database_url: str | None = None,
        dashboard_url: str | None = None,
        allowed_feature_id: str | None = None,
        worktree_roots: list[str | Path] | None = None,
        forbidden_paths: list[str] | None = None,
    ) -> None:
        self.feature_store = feature_store
        self.artifact_store = artifact_store
        self.database_url = database_url
        self._pool: Any | None = None
        self.dashboard_url = dashboard_url
        self.allowed_feature_id = allowed_feature_id
        self.worktree_roots = [Path(root) for root in (worktree_roots or [])]
        self.forbidden_paths = forbidden_paths or []
        # Slice 10c-1 — mechanical read-only enforcement (doc 10 § "Refactoring
        # Steps" step 8: "Enforce read-only policy in ... MCP service
        # construction"). The MCP evidence service is read-only/advisory: it
        # holds the feature/artifact READ surfaces and structurally NO
        # execution-authority `ExecutionControlStore`. `assert_read_only()`
        # fails closed if any held handle is/exposes the control-plane writer.
        # Eagerly assert for caller-supplied stores; lazily-created stores are
        # re-asserted in `_ensure_stores`.
        self.assert_read_only()

    def assert_read_only(self) -> None:
        """Fail closed unless every held store satisfies the read-only contract.

        doc 10 § "Read-Only And Audit Exception Policy": "Service wiring must
        make that contract mechanical." The supervisor evidence service holds
        only read/query handles — no execution-authority `ExecutionControlStore`.
        This raises
        :class:`~iriai_build_v2.supervisor.read_only.ReadOnlySupervisorViolation`
        if a control-plane writer ever reaches a held handle; it never degrades.
        """

        assert_read_only_supervisor_handles(
            feature_store=self.feature_store,
            artifact_store=self.artifact_store,
            # The execution-authority store is STRUCTURALLY ABSENT — the MCP
            # service has no slot for one. The explicit `None` makes the
            # absence an asserted contract, not an accident.
            execution_control_store=None,
        )

    async def _ensure_stores(self) -> None:
        if self.feature_store is not None and self.artifact_store is not None:
            return
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required for supervisor evidence MCP")
        self._pool = await create_pool(self.database_url)
        self.feature_store = PostgresFeatureStore(self._pool)
        self.artifact_store = PostgresArtifactStore(self._pool)
        # Re-assert: lazily-created stores must also satisfy the read-only
        # contract before any tool call reaches them.
        self.assert_read_only()

    def _feature_id(self, feature_id: str | None) -> str:
        candidate = (feature_id or self.allowed_feature_id or "").strip()
        if not candidate:
            raise ValueError("feature_id is required")
        if self.allowed_feature_id and candidate != self.allowed_feature_id:
            raise ValueError(
                f"feature_id {candidate!r} is outside supervisor scope {self.allowed_feature_id!r}"
            )
        return candidate

    def _toolbox(self, feature_id: str) -> SupervisorEvidenceToolbox:
        return SupervisorEvidenceToolbox(
            feature_id=feature_id,
            feature_store=self.feature_store,
            artifact_store=self.artifact_store,
            dashboard_url=self.dashboard_url,
            worktree_roots=self.worktree_roots,
            forbidden_paths=self.forbidden_paths,
        )

    async def get_current_snapshot(
        self,
        *,
        feature_id: str | None = None,
        include_bridge: bool = True,
    ) -> dict[str, Any]:
        scoped_feature_id = self._feature_id(feature_id)
        await self._ensure_stores()
        feature = await self.feature_store.get_feature(scoped_feature_id)
        events = await self._event_rows(
            feature_id=scoped_feature_id,
            after_id=0,
            limit=_SNAPSHOT_EVENT_LIMIT,
            order="desc",
        )
        control_plane_events = await _load_control_plane_failure_events(
            self.artifact_store,
            scoped_feature_id,
        )
        events = [*events, *control_plane_events]
        artifact_rows = await self._latest_artifact_rows(
            feature_id=scoped_feature_id,
            prefixes=KEY_PREFIXES,
            limit=_SNAPSHOT_ARTIFACT_LIMIT,
        )
        artifacts = [_artifact_record(row) for row in artifact_rows]
        bridge = (
            await probe_bridge(dashboard_url=self.dashboard_url)
            if include_bridge and self.dashboard_url
            else None
        )
        metadata = dict(getattr(feature, "metadata", {}) or {}) if feature is not None else {}
        phase = str(metadata.get("_db_phase") or metadata.get("phase") or "")
        snapshot = build_current_workflow_snapshot(
            events=events,
            artifacts=artifacts,
            bridge=bridge,
            phase=phase,
        )
        control_plane = await self._control_plane_snapshot(scoped_feature_id)
        # Slice 10e — doc-10 § "Refactoring Steps" step 4: the MCP snapshot
        # path reads the TYPED control-plane snapshot FIRST. `control_plane_typed`
        # is the bounded Slice-10a `ControlPlaneSnapshot` (summary-only, no
        # artifact bodies); `evidence_mode` follows its `source`. When the
        # typed read is unavailable, `typed_control_plane` is `None` and
        # `evidence_mode` is `legacy_fallback` (NOT `typed`) — fail-safe.
        typed_control_plane = await load_typed_control_plane_snapshot(
            self.artifact_store,
            scoped_feature_id,
            # match `_control_plane_snapshot`'s `artifact_store._pool or
            # feature_store._pool` pool resolution so the typed path is never
            # silently skipped when only the feature store carries the pool.
            fallback_pool_source=self.feature_store,
        )
        evidence_mode = evidence_mode_for_snapshot(typed_control_plane)
        return {
            "feature_id": scoped_feature_id,
            "snapshot": snapshot.model_dump(mode="json"),
            "control_plane": control_plane,
            "control_plane_typed": (
                typed_control_plane.model_dump(mode="json")
                if typed_control_plane is not None
                else None
            ),
            "evidence_mode": evidence_mode,
            "bridge": _compact_bridge_payload(bridge),
            "latest_material_artifacts": _snapshot_material_artifacts(
                artifacts,
                group_idx=snapshot.group_idx,
            ),
            "recommended_detail_artifact_ids": _recommended_detail_artifact_ids(
                artifacts,
                group_idx=snapshot.group_idx,
            ),
            "tool_usage_hint": (
                "For current-status questions, inspect get_current_snapshot first, "
                "then fetch at most three recommended artifact details unless the "
                "operator explicitly asks for deep history. Avoid readonly_sql for "
                "ordinary status questions."
            ),
            "citations": snapshot.citations,
        }

    async def list_artifact_index(
        self,
        *,
        feature_id: str | None = None,
        prefixes: list[str] | None = None,
        keys: list[str] | None = None,
        artifact_after_id: int = 0,
        limit: int = _DEFAULT_INDEX_LIMIT,
        order: str = "desc",
    ) -> dict[str, Any]:
        scoped_feature_id = self._feature_id(feature_id)
        await self._ensure_stores()
        capped_limit = _cap_limit(limit, _MAX_INDEX_LIMIT)
        artifact_prefixes = list(prefixes or KEY_PREFIXES)
        request = SupervisorInvestigationRequest(
            reason="MCP artifact index request",
            artifact_keys=list(keys or [])[:20],
            artifact_prefixes=artifact_prefixes[:20],
            artifact_after_id=max(0, int(artifact_after_id or 0)),
        )
        bundle = await self._toolbox(scoped_feature_id).gather(request)
        summaries = list(bundle.artifact_summaries)
        exact_keys = {str(key) for key in (keys or [])}
        omitted = [
            _suppressed_index_entry(summary)
            for summary in summaries
            if _suppress_default_index_summary(summary, exact_keys=exact_keys)
        ]
        summaries = [
            summary
            for summary in summaries
            if not _suppress_default_index_summary(summary, exact_keys=exact_keys)
        ]
        reverse = str(order).lower() == "desc"
        summaries = sorted(summaries, key=lambda item: item.id or 0, reverse=reverse)[:capped_limit]
        return {
            "feature_id": scoped_feature_id,
            "count": len(summaries),
            "artifacts": [_summary_index_entry(summary) for summary in summaries],
            "omitted_aggregate_artifacts": omitted[:20],
            "errors": bundle.errors,
        }

    async def get_artifact_detail(
        self,
        *,
        feature_id: str | None = None,
        artifact_id: int,
        max_chars: int = _DEFAULT_DETAIL_CHARS,
    ) -> dict[str, Any]:
        scoped_feature_id = self._feature_id(feature_id)
        await self._ensure_stores()
        limit = _cap_limit(max_chars, _MAX_DETAIL_CHARS)
        row = None
        if hasattr(self.artifact_store, "get_slice"):
            row = await self.artifact_store.get_slice(
                feature_id=scoped_feature_id,
                artifact_id=int(artifact_id),
                start=0,
                chars=limit,
            )
        else:
            chunks = await self._toolbox(scoped_feature_id)._artifact_chunks([f"{int(artifact_id)}:0"])
            if chunks:
                chunk = chunks[0]
                row = {
                    "id": artifact_id,
                    "key": chunk.key,
                    "created_at": None,
                    "text": chunk.text,
                    "total_chars": chunk.total_chars,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "stored_bytes": None,
                }
        if row is None:
            return {
                "feature_id": scoped_feature_id,
                "artifact_id": artifact_id,
                "found": False,
                "error": "artifact not found for feature",
            }
        text = str(row.get("text") or "")
        total_chars = int(row.get("total_chars") or len(text))
        record = _artifact_record(
            {
                "id": row.get("id"),
                "key": row.get("key"),
                "created_at": row.get("created_at"),
                "value": text,
                "stored_bytes": row.get("stored_bytes"),
                "value_preview": text,
            }
        )
        summary = artifact_summary(record)
        payload: dict[str, Any] = {
            "feature_id": scoped_feature_id,
            "found": True,
            "artifact": _summary_index_entry(summary),
            "total_chars": total_chars,
        }
        if total_chars <= limit:
            payload["value"] = text
            payload["text"] = text
            payload["truncated"] = False
            return payload
        chunk_count = max(1, (total_chars + 19_999) // 20_000)
        payload.update(
            {
                "excerpt": text,
                "truncated": True,
                "chunk_refs": [f"{record.id}:{idx}" for idx in range(chunk_count)][
                    :_MAX_CHUNK_REFS
                ],
            }
        )
        return payload

    async def get_artifact_chunk(
        self,
        *,
        feature_id: str | None = None,
        chunk_ref: str,
    ) -> dict[str, Any]:
        scoped_feature_id = self._feature_id(feature_id)
        await self._ensure_stores()
        chunks = await self._toolbox(scoped_feature_id)._artifact_chunks([chunk_ref])
        if not chunks:
            return {
                "feature_id": scoped_feature_id,
                "chunk_ref": chunk_ref,
                "found": False,
                "error": "artifact chunk not found",
            }
        return {
            "feature_id": scoped_feature_id,
            "found": True,
            "chunk": chunks[0].model_dump(mode="json"),
        }

    async def list_events(
        self,
        *,
        feature_id: str | None = None,
        after_id: int = 0,
        limit: int = 50,
        order: str = "desc",
        group_idx: int | None = None,
    ) -> dict[str, Any]:
        scoped_feature_id = self._feature_id(feature_id)
        await self._ensure_stores()
        events = await self._event_rows(
            feature_id=scoped_feature_id,
            after_id=max(0, int(after_id or 0)),
            limit=_cap_limit(limit, 200),
            order=order,
            group_idx=group_idx,
        )
        return {
            "feature_id": scoped_feature_id,
            "count": len(events),
            "events": [_compact_event_payload(event) for event in events],
        }

    async def get_bridge_status(self) -> dict[str, Any]:
        bridge = await probe_bridge(dashboard_url=self.dashboard_url)
        return bridge.model_dump(mode="json")

    async def get_bridge_logs(self, *, after: int = 0) -> dict[str, Any]:
        bridge = await probe_bridge(dashboard_url=self.dashboard_url, after=max(0, int(after or 0)))
        return {
            "dashboard_url": bridge.dashboard_url,
            "ok": bridge.ok,
            "log_cursor": bridge.log_cursor,
            "log_lines": bridge.log_lines,
            "errors": bridge.errors,
        }

    async def probe_worktree(self, *, repo_name: str | None = None) -> dict[str, Any]:
        from .evidence import probe_worktree

        probes = []
        for root in self.worktree_roots:
            if repo_name and root.name != repo_name and repo_name not in str(root):
                continue
            probes.append(
                probe_worktree(root, forbidden_paths=self.forbidden_paths).model_dump(mode="json")
            )
        return {"count": len(probes), "worktrees": probes}

    async def readonly_sql(
        self,
        *,
        feature_id: str | None = None,
        sql: str,
    ) -> dict[str, Any]:
        del sql
        self._feature_id(feature_id)
        return {
            "ok": False,
            "reason": "operator SQL is disabled; use database_summary or other named evidence APIs",
            "rows": [],
        }

    async def database_summary(
        self,
        *,
        feature_id: str | None = None,
        query_name: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        scoped_feature_id = self._feature_id(feature_id)
        await self._ensure_stores()
        pool = getattr(self.artifact_store, "_pool", None) or getattr(self.feature_store, "_pool", None)
        if pool is None:
            return {"ok": False, "reason": "postgres pool unavailable", "rows": []}
        capped_limit = _cap_limit(limit, 200)
        query = str(query_name or "").strip().lower().replace("-", "_")
        queries: dict[str, tuple[str, tuple[Any, ...]]] = {
            "artifact_counts": (
                f"""
                SELECT split_part(key, ':', 1) AS key_family,
                       count(*)::bigint AS rows,
                       COALESCE(sum(pg_column_size(value)), 0)::bigint AS db_stored_bytes,
                       COALESCE(sum({_SPILL_LOGICAL_BYTES_SQL}), 0)::bigint AS stored_bytes,
                       COALESCE(sum({_SPILL_LOGICAL_BYTES_SQL}), 0)::bigint AS logical_stored_bytes
                FROM artifacts
                WHERE feature_id = $1
                GROUP BY key_family
                ORDER BY stored_bytes DESC
                LIMIT $2
                """,
                (scoped_feature_id, capped_limit),
            ),
            "largest_artifacts": (
                f"""
                SELECT id, key, created_at,
                       pg_column_size(value)::bigint AS db_stored_bytes,
                       {_SPILL_LOGICAL_BYTES_SQL}::bigint AS stored_bytes,
                       {_SPILL_LOGICAL_BYTES_SQL}::bigint AS logical_stored_bytes
                FROM artifacts
                WHERE feature_id = $1
                ORDER BY stored_bytes DESC
                LIMIT $2
                """,
                (scoped_feature_id, capped_limit),
            ),
            "events_recent": (
                """
                SELECT id, event_type, source,
                       substring(content from 1 for 700) AS content,
                       metadata,
                       created_at,
                       COALESCE(pg_column_size(content), 0)::bigint AS content_bytes
                FROM events
                WHERE feature_id = $1
                ORDER BY id DESC
                LIMIT $2
                """,
                (scoped_feature_id, capped_limit),
            ),
            "outbox_pending": (
                """
                SELECT status,
                       count(*)::bigint AS rows,
                       COALESCE(sum(pg_column_size(payload)), 0)::bigint AS payload_bytes,
                       min(created_at) AS oldest_created_at,
                       max(created_at) AS newest_created_at
                FROM public_dashboard_outbox
                WHERE feature_id = $1
                GROUP BY status
                ORDER BY rows DESC
                LIMIT $2
                """,
                (scoped_feature_id, capped_limit),
            ),
            "table_sizes": (
                """
                SELECT relname,
                       pg_total_relation_size(relid)::bigint AS total_bytes,
                       pg_relation_size(relid)::bigint AS table_bytes,
                       n_live_tup::bigint AS live_rows
                FROM pg_stat_user_tables
                ORDER BY total_bytes DESC
                LIMIT $1
                """,
                (capped_limit,),
            ),
        }
        if query not in queries:
            return {
                "ok": False,
                "reason": "unknown database summary query",
                "available_queries": sorted(queries),
                "rows": [],
            }
        sql, args = queries[query]
        try:
            rows = await pool.fetch(sql, *args, timeout=3.0)
        except TypeError:
            rows = await pool.fetch(sql, *args)
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "rows": []}
        return {
            "ok": True,
            "feature_id": scoped_feature_id,
            "query_name": query,
            "row_count": len(rows),
            "rows": [_jsonable(dict(row)) for row in rows],
        }

    async def _event_rows(
        self,
        *,
        feature_id: str,
        after_id: int,
        limit: int,
        order: str,
        group_idx: int | None = None,
    ) -> list[Any]:
        if hasattr(self.feature_store, "list_event_summaries"):
            rows = await self.feature_store.list_event_summaries(
                feature_id,
                after_id=after_id,
                limit=limit,
                order=order,
                group_idx=group_idx,
                preview_chars=700,
            )
            return [_event_record(row) for row in rows]
        return []

    async def _latest_artifact_rows(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...],
        limit: int,
    ) -> list[dict[str, Any]]:
        if hasattr(self.artifact_store, "list_record_summaries"):
            try:
                return await self.artifact_store.list_record_summaries(
                    feature_id=feature_id,
                    prefixes=prefixes,
                    after_id=0,
                    limit=limit,
                    order="desc",
                )
            except TypeError:
                return await self.artifact_store.list_record_summaries(
                    feature_id=feature_id,
                    prefixes=prefixes,
                    after_id=0,
                    limit=limit,
                )
        return []

    async def _control_plane_snapshot(self, feature_id: str) -> dict[str, Any] | None:
        pool = getattr(self.artifact_store, "_pool", None) or getattr(
            self.feature_store,
            "_pool",
            None,
        )
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


def create_mcp_server(service: SupervisorEvidenceMcpService) -> FastMCP:
    mcp = FastMCP(
        "iriai-supervisor-evidence",
        instructions=(
            "Read-only supervisor evidence tools. Use get_current_snapshot first "
            "for current status questions, then list artifact/event indexes and "
            "request exact details or chunks as needed."
        ),
    )

    @mcp.tool()
    async def get_current_snapshot(
        feature_id: str | None = None,
        include_bridge: bool = True,
    ) -> dict[str, Any]:
        """Return the live workflow snapshot for a feature."""
        return await service.get_current_snapshot(
            feature_id=feature_id,
            include_bridge=include_bridge,
        )

    @mcp.tool()
    async def list_artifact_index(
        feature_id: str | None = None,
        prefixes: list[str] | None = None,
        keys: list[str] | None = None,
        artifact_after_id: int = 0,
        limit: int = _DEFAULT_INDEX_LIMIT,
        order: str = "desc",
    ) -> dict[str, Any]:
        """Return compact artifact index entries; use detail/chunk tools for raw values."""
        return await service.list_artifact_index(
            feature_id=feature_id,
            prefixes=prefixes,
            keys=keys,
            artifact_after_id=artifact_after_id,
            limit=limit,
            order=order,
        )

    @mcp.tool()
    async def get_artifact_detail(
        feature_id: str | None = None,
        artifact_id: int = 0,
        max_chars: int = _DEFAULT_DETAIL_CHARS,
    ) -> dict[str, Any]:
        """Return one artifact by id, bounded by max_chars with chunk refs for large rows."""
        return await service.get_artifact_detail(
            feature_id=feature_id,
            artifact_id=artifact_id,
            max_chars=max_chars,
        )

    @mcp.tool()
    async def get_artifact_chunk(
        feature_id: str | None = None,
        chunk_ref: str = "",
    ) -> dict[str, Any]:
        """Return a bounded raw artifact chunk such as '1461505:0'."""
        return await service.get_artifact_chunk(feature_id=feature_id, chunk_ref=chunk_ref)

    @mcp.tool()
    async def list_events(
        feature_id: str | None = None,
        after_id: int = 0,
        limit: int = 50,
        order: str = "desc",
        group_idx: int | None = None,
    ) -> dict[str, Any]:
        """Return event rows, optionally filtered to a group."""
        return await service.list_events(
            feature_id=feature_id,
            after_id=after_id,
            limit=limit,
            order=order,
            group_idx=group_idx,
        )

    @mcp.tool()
    async def get_bridge_status() -> dict[str, Any]:
        """Return dashboard bridge status."""
        return await service.get_bridge_status()

    @mcp.tool()
    async def get_bridge_logs(after: int = 0) -> dict[str, Any]:
        """Return bridge log lines after a cursor."""
        return await service.get_bridge_logs(after=after)

    @mcp.tool()
    async def probe_worktree(repo_name: str | None = None) -> dict[str, Any]:
        """Probe configured feature worktrees only."""
        return await service.probe_worktree(repo_name=repo_name)

    @mcp.tool()
    async def readonly_sql(feature_id: str | None = None, sql: str = "") -> dict[str, Any]:
        """Run a feature-scoped read-only SELECT/WITH query with feature_id bound as $1."""
        return await service.readonly_sql(feature_id=feature_id, sql=sql)

    @mcp.tool()
    async def database_summary(
        feature_id: str | None = None,
        query_name: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Run a named bounded database summary query without artifact/event bodies."""
        return await service.database_summary(
            feature_id=feature_id,
            query_name=query_name,
            limit=limit,
        )

    return mcp


def _default_service() -> SupervisorEvidenceMcpService:
    dashboard_url = os.environ.get("IRIAI_DASHBOARD_BASE_URL", DASHBOARD_BASE_URL).rstrip("/")
    return SupervisorEvidenceMcpService(
        database_url=os.environ.get("DATABASE_URL", DATABASE_URL),
        dashboard_url=dashboard_url or None,
        allowed_feature_id=os.environ.get("IRIAI_SUPERVISOR_FEATURE_ID") or None,
        worktree_roots=_path_list_env("IRIAI_SUPERVISOR_WORKTREE_ROOTS"),
        forbidden_paths=_list_env("IRIAI_SUPERVISOR_FORBIDDEN_PATHS"),
    )


def _path_list_env(name: str) -> list[Path]:
    return [Path(value) for value in _list_env(name)]


def _list_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return []
    return [item for item in raw.split(os.pathsep) if item]


def _summary_index_entry(summary: Any) -> dict[str, Any]:
    return {
        "id": summary.id,
        "key": summary.key,
        "citation": summary.citation,
        "created_at": summary.created_at.isoformat() if summary.created_at else None,
        "size_chars": summary.size_chars,
        "sha256": summary.sha256,
        "status": summary.status,
        "approved": summary.approved,
        "route": summary.route,
        "reason": summary.reason,
        "summary_preview": _shorten(summary.summary, 220),
        "concern_count": len(summary.concerns),
        "concern_samples": [_shorten(item, 220) for item in summary.concerns[:3]],
        "gap_count": len(summary.gaps),
        "gap_samples": [_shorten(item, 220) for item in summary.gaps[:3]],
        "path_count": len(summary.path_snippets),
        "path_samples": summary.path_snippets[:5],
        "chunk_refs": summary.chunk_refs[:_MAX_CHUNK_REFS],
        "detail_available": summary.detail_available,
    }


def _snapshot_material_artifacts(
    artifacts: list[Any],
    *,
    group_idx: int | None,
) -> list[dict[str, Any]]:
    candidates = _current_group_material_records(artifacts, group_idx=group_idx)
    return [
        _summary_index_entry(artifact_summary(record))
        for record in candidates[:_SNAPSHOT_MATERIAL_LIMIT]
    ]


def _recommended_detail_artifact_ids(
    artifacts: list[Any],
    *,
    group_idx: int | None,
) -> list[int]:
    records = _current_group_material_records(artifacts, group_idx=group_idx)
    ids: list[int] = []
    for prefix in _DETAIL_PRIORITY_PREFIXES:
        for record in records:
            if record.id is None or record.id in ids:
                continue
            if record.key.startswith(prefix):
                ids.append(record.id)
                break
        if len(ids) >= _SNAPSHOT_RECOMMENDED_DETAIL_LIMIT:
            break
    if len(ids) < _SNAPSHOT_RECOMMENDED_DETAIL_LIMIT:
        for record in records:
            if record.id is None or record.id in ids:
                continue
            ids.append(record.id)
            if len(ids) >= _SNAPSHOT_RECOMMENDED_DETAIL_LIMIT:
                break
    return ids


def _current_group_material_records(
    artifacts: list[Any],
    *,
    group_idx: int | None,
) -> list[Any]:
    newest_first = sorted(
        artifacts,
        key=lambda artifact: artifact.id or 0,
        reverse=True,
    )
    group_records = [
        artifact
        for artifact in newest_first
        if _is_group_material_artifact(artifact.key, group_idx=group_idx)
    ]
    newest_group_id = max((artifact.id or 0 for artifact in group_records), default=0)
    recent_context_floor = max(0, newest_group_id - _SNAPSHOT_RECENT_CONTEXT_ID_WINDOW)
    ungrouped_context = [
        artifact
        for artifact in newest_first
        if _is_recent_ungrouped_material(
            artifact,
            group_idx=group_idx,
            recent_context_floor=recent_context_floor,
        )
    ]
    return sorted(
        _dedupe_artifacts_by_id([*group_records, *ungrouped_context]),
        key=lambda artifact: artifact.id or 0,
        reverse=True,
    )


def _is_group_material_artifact(key: str, *, group_idx: int | None) -> bool:
    if not key.startswith(_CURRENT_GROUP_MATERIAL_PREFIXES):
        return False
    group = _artifact_group_from_key(key)
    if group_idx is None:
        return group is not None
    return group == group_idx


def _is_recent_ungrouped_material(
    artifact: Any,
    *,
    group_idx: int | None,
    recent_context_floor: int,
) -> bool:
    if not artifact.key.startswith(_UNGROUPED_MATERIAL_PREFIXES):
        return False
    if _artifact_group_from_key(artifact.key) is not None:
        return _artifact_group_from_key(artifact.key) == group_idx
    if recent_context_floor and (artifact.id or 0) < recent_context_floor:
        return False
    text = _artifact_text(artifact.value)
    if group_idx is None:
        return True
    return bool(
        re.search(
            rf"\b(?:g|group)\s*[-#:=]?\s*{int(group_idx)}\b",
            text,
            re.IGNORECASE,
        )
    ) or (artifact.id or 0) >= recent_context_floor


def _artifact_group_from_key(key: str) -> int | None:
    match = re.search(r"(?:^|:)g(?P<group>\d+)(?::|$|-)", key)
    if match:
        return int(match.group("group"))
    match = re.search(r"^dag-group:(?P<group>\d+)(?::|$)", key)
    return int(match.group("group")) if match else None


def _dedupe_artifacts_by_id(records: list[Any]) -> list[Any]:
    seen: set[tuple[int | None, str]] = set()
    result: list[Any] = []
    for record in records:
        token = (record.id, record.key)
        if token in seen:
            continue
        seen.add(token)
        result.append(record)
    return result


def _compact_bridge_payload(bridge: Any | None) -> dict[str, Any] | None:
    if bridge is None:
        return None
    payload = bridge.model_dump(mode="json")
    log_lines = list(payload.get("log_lines") or [])
    errors = list(payload.get("errors") or [])
    payload["log_lines"] = [
        _shorten(line, _MAX_BRIDGE_LINE_CHARS)
        for line in log_lines[-_MAX_BRIDGE_LOG_LINES:]
    ]
    payload["errors"] = [
        _shorten(line, _MAX_BRIDGE_LINE_CHARS)
        for line in errors[-_MAX_BRIDGE_ERROR_LINES:]
    ]
    payload["truncated_log_line_count"] = max(0, len(log_lines) - len(payload["log_lines"]))
    payload["truncated_error_count"] = max(0, len(errors) - len(payload["errors"]))
    return payload


def _compact_event_payload(event: Any) -> dict[str, Any]:
    metadata = dict(event.metadata or {})
    if "task_ids" in metadata and isinstance(metadata["task_ids"], list):
        task_ids = list(metadata["task_ids"])
        metadata["task_ids"] = task_ids[:_MAX_EVENT_TASK_IDS]
        if len(task_ids) > _MAX_EVENT_TASK_IDS:
            metadata["task_ids_truncated_count"] = len(task_ids) - _MAX_EVENT_TASK_IDS
    for key, value in list(metadata.items()):
        if isinstance(value, str):
            metadata[key] = _shorten(value, _MAX_EVENT_METADATA_TEXT_CHARS)
        elif not isinstance(value, (type(None), bool, int, float, list, dict)):
            metadata[key] = _shorten(value, _MAX_EVENT_METADATA_TEXT_CHARS)
    return {
        "id": event.id,
        "event_type": event.event_type,
        "source": _shorten(event.source, 240),
        "content": _shorten(event.content, 500) if event.content is not None else None,
        "metadata": metadata,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "citation": event.citation,
    }


def _suppress_default_index_summary(summary: Any, *, exact_keys: set[str]) -> bool:
    if summary.key in exact_keys:
        return False
    return summary.key in _DEFAULT_SUPPRESSED_INDEX_KEYS


def _suppressed_index_entry(summary: Any) -> dict[str, Any]:
    return {
        "id": summary.id,
        "key": summary.key,
        "citation": summary.citation,
        "created_at": summary.created_at.isoformat() if summary.created_at else None,
        "size_chars": summary.size_chars,
        "reason": "aggregate artifact omitted from broad index; request exact detail by id/key if needed",
    }


def _event_mentions_group(event: Any, group_idx: int) -> bool:
    target = f"g{group_idx}"
    metadata = event.metadata or {}
    for key in ("group_idx", "group", "group_index", "dag_group"):
        value = metadata.get(key)
        if value == group_idx or str(value).lower() in {str(group_idx), target}:
            return True
    haystack = " ".join(
        str(value)
        for value in (event.event_type, event.source, event.content, metadata)
        if value is not None
    ).lower()
    return target in haystack or f"group={group_idx}" in haystack


def _cap_limit(value: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_INDEX_LIMIT
    return max(1, min(maximum, parsed))


def _shorten(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 16)]}... [truncated]"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def main() -> None:
    create_mcp_server(_default_service()).run(transport="stdio")


if __name__ == "__main__":
    main()
