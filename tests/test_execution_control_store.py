from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iriai_build_v2.models.outputs import ImplementationResult, Issue, Verdict


FEATURE_ID = "feature-typed"
DAG_SHA256 = "d" * 64


class _FakeRow(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class _FakeTransaction:
    conn: "_FakeConnection"
    snapshot: dict[str, Any] | None = None

    async def __aenter__(self) -> "_FakeTransaction":
        self.snapshot = self.conn.snapshot()
        self.conn.transaction_entries += 1
        return self

    async def __aexit__(self, exc_type, _exc, _tb) -> None:
        if exc_type is not None and self.snapshot is not None:
            self.conn.restore(self.snapshot)
            self.conn.transaction_rollbacks += 1
        else:
            self.conn.transaction_commits += 1


class _FakeAcquire:
    def __init__(self, conn: "_FakeConnection") -> None:
        self.conn = conn

    async def __aenter__(self) -> "_FakeConnection":
        return self.conn

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: "_FakeConnection | None" = None) -> None:
        self.conn = conn or _FakeConnection()
        self.acquire_count = 0

    def acquire(self) -> _FakeAcquire:
        self.acquire_count += 1
        return _FakeAcquire(self.conn)

    async def fetchrow(self, sql: str, *args: object) -> _FakeRow | None:
        return await self.conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: object) -> object:
        return await self.conn.fetchval(sql, *args)

    async def fetch(self, sql: str, *args: object) -> list[_FakeRow]:
        return await self.conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: object) -> str:
        return await self.conn.execute(sql, *args)


class _FakeConnection:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []
        self.typed_rows: list[dict[str, Any]] = []
        self.workspace_snapshots: list[dict[str, Any]] = []
        self.sandbox_leases: list[dict[str, Any]] = []
        self.sandbox_repo_bindings: list[dict[str, Any]] = []
        self.runtime_workspace_bindings: list[dict[str, Any]] = []
        self.task_contracts: list[dict[str, Any]] = []
        self.evidence_nodes: list[dict[str, Any]] = []
        self.evidence_graphs: list[dict[str, Any]] = []
        self.evidence_edges: list[dict[str, Any]] = []
        self.projection_links: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.public_dashboard_outbox: list[dict[str, Any]] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.transaction_entries = 0
        self.transaction_commits = 0
        self.transaction_rollbacks = 0
        self.skip_next_typed_select = False
        self.skip_next_projection_select = False
        self._next_artifact_id = 100
        self._next_typed_row_id = 200
        self._next_workspace_snapshot_id = 250
        self._next_sandbox_lease_id = 260
        self._next_sandbox_repo_binding_id = 265
        self._next_runtime_workspace_binding_id = 270
        self._next_task_contract_id = 275
        self._next_evidence_node_id = 285
        self._next_evidence_graph_id = 290
        self._next_evidence_edge_id = 295
        self._next_projection_id = 300
        self._next_event_id = 400
        self._next_outbox_id = 500

    def snapshot(self) -> dict[str, Any]:
        return {
            "artifacts": deepcopy(self.artifacts),
            "typed_rows": deepcopy(self.typed_rows),
            "workspace_snapshots": deepcopy(self.workspace_snapshots),
            "sandbox_leases": deepcopy(self.sandbox_leases),
            "sandbox_repo_bindings": deepcopy(self.sandbox_repo_bindings),
            "runtime_workspace_bindings": deepcopy(self.runtime_workspace_bindings),
            "task_contracts": deepcopy(self.task_contracts),
            "evidence_nodes": deepcopy(self.evidence_nodes),
            "evidence_graphs": deepcopy(self.evidence_graphs),
            "evidence_edges": deepcopy(self.evidence_edges),
            "projection_links": deepcopy(self.projection_links),
            "events": deepcopy(self.events),
            "public_dashboard_outbox": deepcopy(self.public_dashboard_outbox),
            "nexts": (
                self._next_artifact_id,
                self._next_typed_row_id,
                self._next_workspace_snapshot_id,
                self._next_sandbox_lease_id,
                self._next_sandbox_repo_binding_id,
                self._next_runtime_workspace_binding_id,
                self._next_task_contract_id,
                self._next_evidence_node_id,
                self._next_evidence_graph_id,
                self._next_evidence_edge_id,
                self._next_projection_id,
                self._next_event_id,
                self._next_outbox_id,
            ),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.artifacts = snapshot["artifacts"]
        self.typed_rows = snapshot["typed_rows"]
        self.workspace_snapshots = snapshot["workspace_snapshots"]
        self.sandbox_leases = snapshot["sandbox_leases"]
        self.sandbox_repo_bindings = snapshot["sandbox_repo_bindings"]
        self.runtime_workspace_bindings = snapshot["runtime_workspace_bindings"]
        self.task_contracts = snapshot["task_contracts"]
        self.evidence_nodes = snapshot["evidence_nodes"]
        self.evidence_graphs = snapshot["evidence_graphs"]
        self.evidence_edges = snapshot["evidence_edges"]
        self.projection_links = snapshot["projection_links"]
        self.events = snapshot["events"]
        self.public_dashboard_outbox = snapshot["public_dashboard_outbox"]
        (
            self._next_artifact_id,
            self._next_typed_row_id,
            self._next_workspace_snapshot_id,
            self._next_sandbox_lease_id,
            self._next_sandbox_repo_binding_id,
            self._next_runtime_workspace_binding_id,
            self._next_task_contract_id,
            self._next_evidence_node_id,
            self._next_evidence_graph_id,
            self._next_evidence_edge_id,
            self._next_projection_id,
            self._next_event_id,
            self._next_outbox_id,
        ) = snapshot["nexts"]

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        normalized = " ".join(sql.lower().split())
        if "pg_advisory" in normalized:
            return "SELECT 1"
        if "insert into" in normalized and "artifacts" in normalized:
            self._insert_artifact(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "execution_artifact_projections" in normalized:
            self._insert_projection_link(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "workspace_snapshots" in normalized:
            self._insert_workspace_snapshot(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "sandbox_leases" in normalized:
            self._insert_sandbox_lease(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "sandbox_repo_bindings" in normalized:
            self._insert_sandbox_repo_binding(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "runtime_workspace_bindings" in normalized:
            self._insert_runtime_workspace_binding(sql, args)
            return "INSERT 0 1"
        if "update" in normalized and "task_deliverable_contracts" in normalized:
            return self._update_task_contract(sql, args)
        if "insert into" in normalized and "task_deliverable_contracts" in normalized:
            self._insert_task_contract(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "evidence_nodes" in normalized:
            self._insert_evidence_node(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "evidence_graphs" in normalized:
            self._insert_evidence_graph(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "evidence_edges" in normalized:
            self._insert_evidence_edge(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "public_dashboard_outbox" in normalized:
            self._insert_outbox(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized and "events" in normalized:
            self._insert_event(sql, args)
            return "INSERT 0 1"
        if "insert into" in normalized:
            self._insert_typed_row(sql, args)
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> _FakeRow | None:
        self.calls.append((sql, args))
        normalized = " ".join(sql.lower().split())
        if "select" in normalized and "from execution_journal_rows" in normalized:
            row = self._select_typed_row(sql, args)
            return _FakeRow(row) if row is not None else None
        if "update" in normalized and "execution_journal_rows" in normalized:
            row = self._update_typed_row(sql, args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from artifacts" in normalized:
            row = self._select_artifact(sql, args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from execution_artifact_projections" in normalized:
            row = self._select_projection_link(args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from workspace_snapshots" in normalized:
            row = self._select_workspace_snapshot(args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from sandbox_leases" in normalized:
            row = self._select_sandbox_lease(sql, args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from sandbox_repo_bindings" in normalized:
            row = self._select_sandbox_repo_binding(args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from runtime_workspace_bindings" in normalized:
            row = self._select_runtime_workspace_binding(args)
            return _FakeRow(row) if row is not None else None
        if "update" in normalized and "sandbox_leases" in normalized:
            row = self._update_sandbox_lease(sql, args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from task_deliverable_contracts" in normalized:
            row = self._select_task_contract(sql, args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from evidence_nodes" in normalized:
            row = self._select_evidence_node(args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from evidence_graphs" in normalized:
            row = self._select_evidence_graph(args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from evidence_edges" in normalized:
            row = self._select_evidence_edge(args)
            return _FakeRow(row) if row is not None else None
        if "select" in normalized and "from public_dashboard_outbox" in normalized:
            row = self._select_outbox(args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "artifacts" in normalized:
            return _FakeRow(self._insert_artifact(sql, args))
        if "insert into" in normalized and "execution_artifact_projections" in normalized:
            row = self._insert_projection_link(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "workspace_snapshots" in normalized:
            row = self._insert_workspace_snapshot(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "sandbox_leases" in normalized:
            row = self._insert_sandbox_lease(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "sandbox_repo_bindings" in normalized:
            row = self._insert_sandbox_repo_binding(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "runtime_workspace_bindings" in normalized:
            row = self._insert_runtime_workspace_binding(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "task_deliverable_contracts" in normalized:
            row = self._insert_task_contract(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "evidence_nodes" in normalized:
            row = self._insert_evidence_node(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "evidence_graphs" in normalized:
            row = self._insert_evidence_graph(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "evidence_edges" in normalized:
            row = self._insert_evidence_edge(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "public_dashboard_outbox" in normalized:
            row = self._insert_outbox(sql, args)
            return _FakeRow(row) if row is not None else None
        if "insert into" in normalized and "events" in normalized:
            return _FakeRow(self._insert_event(sql, args))
        if "insert into" in normalized:
            row = self._insert_typed_row(sql, args)
            return _FakeRow(row) if row is not None else None
        return None

    async def fetchval(self, sql: str, *args: object) -> object:
        row = await self.fetchrow(sql, *args)
        if row is None:
            return None
        if "id" in row:
            return row["id"]
        return next(iter(row.values()))

    async def fetch(self, sql: str, *args: object) -> list[_FakeRow]:
        self.calls.append((sql, args))
        normalized = " ".join(sql.lower().split())
        if "from execution_journal_rows" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            list_args = [arg for arg in args if isinstance(arg, list)]
            entry_type_filter: set[str] = set()
            status_filter: set[str] = set()
            if "entry_type = any" in normalized and list_args:
                entry_type_filter = {str(item) for item in list_args[0]}
            if (
                (
                    "status) = any" in normalized
                    or "payload->>'status'" in normalized
                )
                and len(list_args) >= 2
            ):
                status_filter = {str(item).lower() for item in list_args[1]}
            rows = [
                row for row in self.typed_rows
                if feature_id is None or row["feature_id"] == feature_id
            ]
            if entry_type_filter:
                rows = [row for row in rows if str(row.get("entry_type")) in entry_type_filter]
            if status_filter:
                use_merge_queue_payload_status = "entry_type = any" in normalized and (
                    "payload->>'merge_queue_status'" in normalized
                    or "payload->>'queue_status'" in normalized
                    or "payload->>'status'" in normalized
                )
                rows = [
                    row
                    for row in rows
                    if (
                        _merge_queue_payload_status(row)
                        if use_merge_queue_payload_status
                        else str(row.get("status") or "").lower()
                    )
                    in status_filter
                ]
            rows.sort(key=lambda row: row["id"], reverse="order by id desc" in normalized)
            if limit is not None:
                rows = rows[:limit]
            if "payload->" in normalized:
                rows = [
                    _control_plane_attempt_row(
                        row,
                        merge_queue_status=("entry_type = any" in normalized and (
                            "payload->>'merge_queue_status'" in normalized
                            or "payload->>'queue_status'" in normalized
                            or "payload->>'status'" in normalized
                        )),
                    )
                    for row in rows
                ]
            return [_FakeRow(row) for row in rows]
        if "from artifacts" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            prefixes = [
                str(arg).rstrip("%")
                for arg in args
                if isinstance(arg, str) and arg.endswith("%")
            ]
            if "key like 'dag-gate:%'" in normalized:
                prefixes.append("dag-gate:")
            rows = [
                row for row in self.artifacts
                if (feature_id is None or row["feature_id"] == feature_id)
                and (not prefixes or any(row["key"].startswith(prefix) for prefix in prefixes))
            ]
            return [_FakeRow(row) for row in rows]
        if "from execution_artifact_projections" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            typed_row_id = None
            if "where typed_row_id = $1" in normalized:
                typed_row_id = args[0] if args and isinstance(args[0], int) else None
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            rows = [
                row for row in self.projection_links
                if (feature_id is None or row["feature_id"] == feature_id)
                and (typed_row_id is None or row["typed_row_id"] == typed_row_id)
            ]
            rows.sort(key=lambda row: row["id"], reverse="order by id desc" in normalized)
            if limit is not None:
                rows = rows[:limit]
            if "payload->" in normalized:
                rows = [_control_plane_projection_row(row) for row in rows]
            return [_FakeRow(row) for row in rows]
        if "from evidence_nodes" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            after_id = int_args[-2] if "id > $" in normalized and len(int_args) >= 2 else None
            group_idx_candidates = [
                arg for arg in int_args if arg not in {limit, after_id}
            ]
            group_idx = group_idx_candidates[0] if group_idx_candidates else None
            stage = _first_matching(
                args,
                lambda value: isinstance(value, str) and value in {"initial", "retry-0"},
            )
            kind_list = next((arg for arg in args if isinstance(arg, list)), [])
            kind_literal = (
                "runtime_failure_context"
                if "kind = 'runtime_failure_context'" in normalized
                else None
            )
            rows = [
                row for row in self.evidence_nodes
                if (feature_id is None or row["feature_id"] == feature_id)
                and (not kind_list or row["kind"] in kind_list)
                and (kind_literal is None or row["kind"] == kind_literal)
                and (group_idx is None or row["group_idx"] == group_idx)
                and (stage is None or row["stage"] == stage)
                and (after_id is None or row["id"] > after_id)
            ]
            rows.sort(key=lambda row: row["id"])
            if limit is not None:
                rows = rows[:limit]
            if "'{}'::jsonb as payload" in normalized:
                rows = [{**row, "payload": "{}"} for row in rows]
            elif "kind = 'runtime_failure_context'" in normalized and "payload->" in normalized:
                rows = [_control_plane_runtime_failure_row(row) for row in rows]
            return [_FakeRow(row) for row in rows]
        if "from evidence_edges" in normalized:
            graph_id = args[0] if args and isinstance(args[0], int) else None
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            rows = [
                row for row in self.evidence_edges
                if (graph_id is None or row["evidence_graph_id"] == graph_id)
                and ("required = true" not in normalized or row["required"] is True)
            ]
            rows.sort(key=lambda row: row["id"])
            if limit is not None:
                rows = rows[:limit]
            return [_FakeRow(row) for row in rows]
        if "from workspace_snapshots" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            rows = [
                row for row in self.workspace_snapshots
                if feature_id is None or row["feature_id"] == feature_id
            ]
            rows.sort(key=lambda row: row["id"], reverse="order by id desc" in normalized)
            if limit is not None:
                rows = rows[:limit]
            return [_FakeRow(row) for row in rows]
        if "from sandbox_leases" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            rows = [
                row for row in self.sandbox_leases
                if feature_id is None or row["feature_id"] == feature_id
            ]
            rows.sort(key=lambda row: row["id"], reverse="order by id desc" in normalized)
            if limit is not None:
                rows = rows[:limit]
            return [_FakeRow(row) for row in rows]
        if "from runtime_workspace_bindings" in normalized:
            feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
            int_args = [arg for arg in args if isinstance(arg, int)]
            limit = int_args[-1] if " limit $" in normalized and int_args else None
            rows = [
                row for row in self.runtime_workspace_bindings
                if feature_id is None or row["feature_id"] == feature_id
            ]
            rows.sort(key=lambda row: row["id"], reverse="order by id desc" in normalized)
            if limit is not None:
                rows = rows[:limit]
            rows = [_control_plane_runtime_workspace_binding_row(row) for row in rows]
            return [_FakeRow(row) for row in rows]
        if "from sandbox_repo_bindings" in normalized:
            sandbox_lease_id = _first_matching(args, lambda value: isinstance(value, int))
            rows = [
                row for row in self.sandbox_repo_bindings
                if sandbox_lease_id is None or row["sandbox_lease_id"] == sandbox_lease_id
            ]
            return [_FakeRow(row) for row in rows]
        return []

    def _insert_artifact(self, _sql: str, args: tuple[object, ...]) -> dict[str, Any]:
        key = _first_matching(args, _looks_like_projection_key)
        assert key is not None, (
            "artifact insert must include a legacy projection key; "
            f"args={args!r}"
        )
        body = _body_after_key(args, str(key))
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID) or FEATURE_ID
        body_text = _body_to_text(body)
        self._next_artifact_id += 1
        row = {
            "id": self._next_artifact_id,
            "feature_id": str(feature_id),
            "key": str(key),
            "value": body_text,
            "sha256": _sha256(body_text),
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.artifacts.append(row)
        return row

    def _insert_typed_row(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        idempotency_key = _first_matching(
            args,
            _looks_like_idempotency_key,
        )
        if idempotency_key is not None:
            existing = next(
                (row for row in self.typed_rows if row.get("idempotency_key") == idempotency_key),
                None,
            )
            if existing is not None:
                if "do nothing" in " ".join(sql.lower().split()):
                    return None
                return existing
        self._next_typed_row_id += 1
        row = {
            "id": self._next_typed_row_id,
            "table": _insert_table_name(sql),
            "feature_id": str(args[0]),
            "idempotency_key": idempotency_key,
            "entry_type": args[2],
            "status": args[3],
            "actor": args[4],
            "dag_sha256": args[5],
            "group_idx": args[6],
            "task_id": args[7],
            "request_digest": args[8],
            "payload": args[9],
            "requires_legacy_visibility": args[10],
            "projection_mode": args[11],
            "dispatcher_state": args[12] if len(args) > 12 else "requested",
            "runtime": args[13] if len(args) > 13 else "",
            "args": args,
        }
        self.typed_rows.append(row)
        return row

    def _insert_event(self, _sql: str, args: tuple[object, ...]) -> dict[str, Any]:
        self._next_event_id += 1
        row = {
            "id": self._next_event_id,
            "feature_id": str(args[0]),
            "event_type": args[1],
            "source": args[2],
            "content": args[3],
            "metadata": args[4],
            "args": args,
        }
        self.events.append(row)
        return row

    def _insert_outbox(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        event_id = str(args[0])
        existing = self._select_outbox((event_id,))
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        self._next_outbox_id += 1
        # Two INSERT shapes coexist in the bundle:
        #   * The legacy ``artifact.written`` writer in store.py
        #     (_insert_dashboard_outbox) passes 4 args:
        #     (event_id, feature_id, event_type, payload) and bakes
        #     schema_version/visibility/status into the SQL literal.
        #   * The doc-10 ``control_plane.snapshot_changed`` writer in
        #     public_dashboard.py
        #     (PublicDashboardOutbox.project_control_plane_snapshot_changed)
        #     passes 6 args:
        #     (event_id, feature_id, event_type, schema_version,
        #      visibility, payload).
        # Distinguish by arity so the fake mirrors both shapes; the row
        # exposes schema_version + visibility when the caller passed them.
        feature_id = str(args[1])
        event_type = args[2]
        if len(args) >= 6:
            schema_version = args[3]
            visibility = args[4]
            payload = args[5]
        else:
            schema_version = None
            visibility = "internal"
            payload = args[3]
        row = {
            "id": self._next_outbox_id,
            "event_id": event_id,
            "feature_id": feature_id,
            "event_type": event_type,
            "schema_version": schema_version,
            "visibility": visibility,
            "payload": payload,
            "status": "pending",
        }
        self.public_dashboard_outbox.append(row)
        return row

    def _insert_projection_link(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        projection_key = _first_matching(args, _looks_like_projection_key)
        assert projection_key is not None, (
            "projection link insert must include the legacy projection key; "
            f"args={args!r}"
        )
        idempotency_key = _first_matching(
            args,
            _looks_like_idempotency_key,
        )
        artifact_id = args[0]
        typed_row_id = args[1]
        feature_id = args[2]
        source_table = args[3]
        source_id = args[4]
        projection_owner = args[5]
        projection_kind = args[6]
        projection_sha256 = args[8]
        legacy_event_id = args[9]
        dashboard_outbox_event_id = args[10]
        payload = args[11]
        existing = next(
            (
                row for row in self.projection_links
                if row["feature_id"] == feature_id
                and row["projection_key"] == projection_key
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        self._next_projection_id += 1
        row = {
            "id": self._next_projection_id,
            "feature_id": feature_id,
            "typed_row_id": typed_row_id,
            "source_table": source_table,
            "source_id": source_id,
            "projection_owner": projection_owner,
            "projection_kind": projection_kind,
            "projection_key": projection_key,
            "artifact_id": artifact_id,
            "projection_sha256": projection_sha256,
            "legacy_event_id": legacy_event_id,
            "dashboard_outbox_event_id": dashboard_outbox_event_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        }
        self.projection_links.append(row)
        return row

    def _insert_workspace_snapshot(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        existing = next(
            (
                row for row in self.workspace_snapshots
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        self._next_workspace_snapshot_id += 1
        row = {
            "id": self._next_workspace_snapshot_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "execution_journal_row_id": args[2],
            "dag_sha256": args[3],
            "group_idx": args[4],
            "attempt_id": args[5],
            "stage": args[6],
            "repo_id": args[7],
            "canonical_path": args[8],
            "registry_digest": args[9],
            "snapshot_digest": args[10],
            "payload": args[11],
            "captured_at": args[12],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.workspace_snapshots.append(row)
        return row

    def _insert_sandbox_lease(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        existing = next(
            (
                row for row in self.sandbox_leases
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        scoped = next(
            (
                row for row in self.sandbox_leases
                if row["feature_id"] == feature_id
                and row["dag_sha256"] == args[3]
                and row["group_idx"] == args[4]
                and row["attempt_no"] == args[5]
                and row["mode"] == args[6]
            ),
            None,
        )
        if scoped is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return scoped
        self._next_sandbox_lease_id += 1
        row = {
            "id": self._next_sandbox_lease_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "execution_journal_row_id": args[2],
            "dag_sha256": args[3],
            "group_idx": args[4],
            "attempt_no": args[5],
            "mode": args[6],
            "status": args[7],
            "lease_owner": args[8],
            "leased_until": args[9],
            "lease_version": args[10],
            "base_snapshot_ids": args[11],
            "sandbox_root": args[12],
            "sandbox_id": args[13],
            "manifest_path": args[14],
            "repo_ids": args[15],
            "base_commits": args[16],
            "task_ids": args[17],
            "contract_ids": args[18],
            "writable_roots": args[19],
            "readonly_roots": args[20],
            "blocked_roots": args[21],
            "patch_summary_ids": args[22],
            "lease_digest": args[23],
            "payload": args[24],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.sandbox_leases.append(row)
        return row

    def _update_sandbox_lease(
        self,
        _sql: str,
        args: tuple[object, ...],
    ) -> dict[str, Any] | None:
        status = str(args[0])
        patch_summary_ids = args[1]
        payload_patch = json.loads(str(args[2]))
        lease_id = args[3]
        feature_id = str(args[4])
        idempotency_key = str(args[5])
        expected_version = int(args[6]) if len(args) > 6 else None
        for row in self.sandbox_leases:
            if lease_id is not None and row["id"] != lease_id:
                continue
            if lease_id is None and (
                row["feature_id"] != feature_id
                or row["idempotency_key"] != idempotency_key
            ):
                continue
            if expected_version is not None and int(row["lease_version"]) != expected_version:
                return None
            row["status"] = status
            row["patch_summary_ids"] = patch_summary_ids
            row["lease_version"] = int(row["lease_version"]) + 1
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"])
            payload.update(payload_patch)
            row["payload"] = json.dumps(payload, sort_keys=True, default=str)
            row["updated_at"] = datetime(2026, 5, 19, 2, tzinfo=timezone.utc)
            return row
        return None

    def _insert_sandbox_repo_binding(
        self,
        sql: str,
        args: tuple[object, ...],
    ) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        sandbox_lease_id = int(args[2])
        repo_id = str(args[3])
        existing = next(
            (
                row for row in self.sandbox_repo_bindings
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        scoped = next(
            (
                row for row in self.sandbox_repo_bindings
                if row["sandbox_lease_id"] == sandbox_lease_id
                and row["repo_id"] == repo_id
            ),
            None,
        )
        if scoped is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return scoped
        self._next_sandbox_repo_binding_id += 1
        row = {
            "id": self._next_sandbox_repo_binding_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "sandbox_lease_id": sandbox_lease_id,
            "repo_id": repo_id,
            "sandbox_repo_root": args[4],
            "canonical_repo_root": args[5],
            "base_snapshot_id": args[6],
            "base_commit": args[7],
            "writable": args[8],
            "writable_roots": args[9],
            "readonly_roots": args[10],
            "blocked_canonical_roots": args[11],
            "status": args[12],
            "binding_digest": args[13],
            "payload": args[14],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.sandbox_repo_bindings.append(row)
        return row

    def _insert_runtime_workspace_binding(
        self,
        sql: str,
        args: tuple[object, ...],
    ) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        sandbox_lease_id = int(args[2])
        attempt_id = int(args[3])
        runtime_name = str(args[4])
        existing = next(
            (
                row for row in self.runtime_workspace_bindings
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        scoped = next(
            (
                row for row in self.runtime_workspace_bindings
                if row["sandbox_lease_id"] == sandbox_lease_id
                and row["runtime_name"] == runtime_name
                and row["attempt_id"] == attempt_id
            ),
            None,
        )
        if scoped is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return scoped
        self._next_runtime_workspace_binding_id += 1
        row = {
            "id": self._next_runtime_workspace_binding_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "sandbox_lease_id": sandbox_lease_id,
            "attempt_id": attempt_id,
            "runtime_name": runtime_name,
            "cwd": args[5],
            "workspace_override": args[6],
            "manifest_path": args[7],
            "repo_roots": args[8],
            "writable_roots": args[9],
            "readonly_roots": args[10],
            "blocked_roots": args[11],
            "env": args[12],
            "role_metadata": args[13],
            "role_metadata_digest": args[14],
            "status": args[15],
            "binding_digest": args[16],
            "payload": args[17],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.runtime_workspace_bindings.append(row)
        return row

    def _insert_task_contract(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        dag_sha256 = str(args[3])
        group_idx = int(args[6])
        task_id = str(args[7])
        status = str(args[24])
        existing = next(
            (
                row for row in self.task_contracts
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        active_scope = next(
            (
                row for row in self.task_contracts
                if row["feature_id"] == feature_id
                and row["dag_sha256"] == dag_sha256
                and row["group_idx"] == group_idx
                and row["task_id"] == task_id
                and row["status"] == "active"
            ),
            None,
        )
        if active_scope is not None and status == "active":
            raise AssertionError("active task contract scope conflict")
        self._next_task_contract_id += 1
        row = {
            "id": self._next_task_contract_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "execution_journal_row_id": args[2],
            "dag_sha256": args[3],
            "source_dag_artifact_id": args[4],
            "source_dag_sha256": args[5],
            "group_idx": args[6],
            "task_id": args[7],
            "repo_id": args[8],
            "repo_path": args[9],
            "required_paths": args[10],
            "allowed_paths": args[11],
            "read_only_paths": args[12],
            "forbidden_paths": args[13],
            "generated_outputs": args[14],
            "acceptance_criteria": args[15],
            "verification_gates": args[16],
            "execution_policy": args[17],
            "non_goals": args[18],
            "dependency_task_ids": args[19],
            "unknown_write_set": args[20],
            "compile_warnings": args[21],
            "normalized_contract_json": args[22],
            "contract_digest": args[23],
            "status": args[24],
            "payload": args[25],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.task_contracts.append(row)
        return row

    def _update_task_contract(self, sql: str, args: tuple[object, ...]) -> str:
        normalized = " ".join(sql.lower().split())
        if "set status = 'superseded'" not in normalized:
            return "UPDATE 0"
        contract_id = int(args[0])
        count = 0
        for row in self.task_contracts:
            if row["id"] != contract_id:
                continue
            if "status = 'active'" in normalized and row["status"] != "active":
                continue
            row["status"] = "superseded"
            row["updated_at"] = datetime(2026, 5, 19, 1, tzinfo=timezone.utc)
            count += 1
        return f"UPDATE {count}"

    def _insert_evidence_node(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        existing = next(
            (
                row for row in self.evidence_nodes
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        self._next_evidence_node_id += 1
        row = {
            "id": self._next_evidence_node_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "execution_journal_row_id": args[2],
            "attempt_id": args[3],
            "contract_id": args[4],
            "snapshot_id": args[5],
            "group_idx": args[6],
            "stage": args[7],
            "kind": args[8],
            "name": args[9],
            "status": args[10],
            "deterministic": args[11],
            "source_ref": args[12],
            "artifact_id": args[13],
            "artifact_key": args[14],
            "event_id": args[15],
            "input_refs": args[16],
            "output_refs": args[17],
            "failure_id": args[18],
            "verdict_id": args[19],
            "content_hash": args[20],
            "summary": args[21],
            "metadata": args[22],
            "payload": args[23],
            "started_at": args[24],
            "finished_at": args[25],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.evidence_nodes.append(row)
        return row

    def _insert_evidence_graph(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        existing = next(
            (
                row for row in self.evidence_graphs
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        self._next_evidence_graph_id += 1
        row = {
            "id": self._next_evidence_graph_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "execution_journal_row_id": args[2],
            "aggregate_evidence_node_id": args[3],
            "projection_key": args[4],
            "projection_sha256": args[5],
            "dag_sha256": args[6],
            "group_idx": args[7],
            "stage": args[8],
            "proof_digest": args[9],
            "graph_payload_digest": args[10],
            "required_edge_ids": args[11],
            "payload": args[12],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.evidence_graphs.append(row)
        return row

    def _insert_evidence_edge(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = str(args[0])
        idempotency_key = str(args[1])
        existing = next(
            (
                row for row in self.evidence_edges
                if row["feature_id"] == feature_id
                and row["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            if "do nothing" in " ".join(sql.lower().split()):
                return None
            return existing
        self._next_evidence_edge_id += 1
        row = {
            "id": self._next_evidence_edge_id,
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
            "evidence_graph_id": args[2],
            "graph_edge_id": args[3],
            "from_graph_node_id": args[4],
            "to_graph_node_id": args[5],
            "from_evidence_node_id": args[6],
            "to_evidence_node_id": args[7],
            "kind": args[8],
            "required": args[9],
            "edge_digest": args[10],
            "payload": args[11],
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        }
        self.evidence_edges.append(row)
        return row

    def _select_artifact(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        key = _first_matching(args, _looks_like_projection_key)
        artifact_id = _first_matching(args, lambda value: isinstance(value, int))
        body_text = None
        if "value = $" in " ".join(sql.lower().split()) and key is not None:
            body_text = _body_to_text(_body_after_key(args, str(key)))
        matches = [
            row for row in self.artifacts
            if (feature_id is None or row["feature_id"] == feature_id)
            and (key is None or row["key"] == key)
            and (artifact_id is None or row["id"] == artifact_id)
            and (body_text is None or row["value"] == body_text)
        ]
        return matches[-1] if matches else None

    def _select_typed_row(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        if self.skip_next_typed_select:
            self.skip_next_typed_select = False
            return None
        normalized = " ".join(sql.lower().split())
        row_id = (
            args[0]
            if args and isinstance(args[0], int) and "where id = $1" in normalized
            else None
        )
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(
            args,
            _looks_like_idempotency_key,
        )
        matches = [
            row for row in self.typed_rows
            if (row_id is None or row["id"] == row_id)
            and (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
        ]
        return matches[-1] if matches else None

    def _update_typed_row(
        self,
        sql: str,
        args: tuple[object, ...],
    ) -> dict[str, Any] | None:
        normalized = " ".join(sql.lower().split())
        status = str(args[0])
        dispatcher_state = str(args[1])
        payload = args[2]
        row_id = int(args[3])
        expected_dispatcher_state = str(args[4]) if len(args) > 4 else None
        target_rank = int(args[5]) if len(args) > 5 else None
        for row in self.typed_rows:
            if row["id"] != row_id or row["entry_type"] != "dispatch_attempt":
                continue
            if "status = 'started'" in normalized and row["status"] != "started":
                return None
            if (
                "dispatcher_state = $5" in normalized
                and expected_dispatcher_state is not None
                and row["dispatcher_state"] != expected_dispatcher_state
            ):
                return None
            if (
                "case dispatcher_state" in normalized
                and target_rank is not None
                and _fake_dispatcher_state_rank(row["dispatcher_state"]) > target_rank
            ):
                return None
            row["status"] = status
            row["dispatcher_state"] = dispatcher_state
            row["payload"] = payload
            row["updated_at"] = datetime(2026, 5, 19, 2, tzinfo=timezone.utc)
            return row
        return None

    def _select_projection_link(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        if self.skip_next_projection_select:
            self.skip_next_projection_select = False
            return None
        projection_key = _first_matching(args, _looks_like_projection_key)
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(
            args,
            _looks_like_idempotency_key,
        )
        matches = [
            row for row in self.projection_links
            if (feature_id is None or row["feature_id"] == feature_id)
            and (projection_key is None or row["projection_key"] == projection_key)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
        ]
        return matches[-1] if matches else None

    def _select_workspace_snapshot(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        matches = [
            row for row in self.workspace_snapshots
            if (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
        ]
        return matches[-1] if matches else None

    def _select_sandbox_lease(
        self,
        sql: str,
        args: tuple[object, ...],
    ) -> dict[str, Any] | None:
        normalized = " ".join(sql.lower().split())
        lease_id = (
            args[0]
            if args and isinstance(args[0], int) and "where id = $1" in normalized
            else None
        )
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        dag_sha256 = _first_matching(args, _looks_like_sha256)
        group_idx = (
            _first_matching(args, lambda value: isinstance(value, int))
            if "group_idx" in normalized and lease_id is None
            else None
        )
        attempt_no = (
            args[3]
            if len(args) > 3 and "attempt_no = $4" in normalized
            else None
        )
        mode = (
            args[4]
            if len(args) > 4 and "mode = $5" in normalized
            else None
        )
        matches = [
            row for row in self.sandbox_leases
            if (lease_id is None or row["id"] == lease_id)
            and (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
            and (dag_sha256 is None or row["dag_sha256"] == dag_sha256)
            and (group_idx is None or row["group_idx"] == group_idx)
            and (attempt_no is None or row["attempt_no"] == attempt_no)
            and (mode is None or row["mode"] == mode)
        ]
        return matches[-1] if matches else None

    def _select_sandbox_repo_binding(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        sandbox_lease_id = args[0] if args and isinstance(args[0], int) else None
        repo_id = (
            args[1]
            if (
                len(args) > 1
                and isinstance(args[1], str)
                and args[1] != FEATURE_ID
                and not _looks_like_idempotency_key(args[1])
            )
            else None
        )
        matches = [
            row for row in self.sandbox_repo_bindings
            if (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
            and (sandbox_lease_id is None or row["sandbox_lease_id"] == sandbox_lease_id)
            and (repo_id is None or row["repo_id"] == repo_id)
        ]
        return matches[-1] if matches else None

    def _select_runtime_workspace_binding(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        sandbox_lease_id = args[0] if args and isinstance(args[0], int) else None
        runtime_name = (
            args[1]
            if (
                len(args) > 1
                and isinstance(args[1], str)
                and args[1] != FEATURE_ID
                and not _looks_like_idempotency_key(args[1])
            )
            else None
        )
        attempt_id = args[2] if len(args) > 2 and isinstance(args[2], int) else None
        matches = [
            row for row in self.runtime_workspace_bindings
            if (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
            and (sandbox_lease_id is None or row["sandbox_lease_id"] == sandbox_lease_id)
            and (runtime_name is None or row["runtime_name"] == runtime_name)
            and (attempt_id is None or row["attempt_id"] == attempt_id)
        ]
        return matches[-1] if matches else None

    def _select_task_contract(self, sql: str, args: tuple[object, ...]) -> dict[str, Any] | None:
        normalized = " ".join(sql.lower().split())
        contract_id = (
            args[0]
            if args and isinstance(args[0], int) and "where id = $1" in normalized
            else None
        )
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        dag_sha256 = _first_matching(args, _looks_like_sha256)
        group_idx = (
            _first_matching(args, lambda value: isinstance(value, int))
            if "group_idx" in normalized and contract_id is None
            else None
        )
        task_id = (
            _first_matching(
                args,
                lambda value: isinstance(value, str) and value.startswith("TASK-"),
            )
            if "task_id" in normalized
            else None
        )
        status = "active" if "status = 'active'" in normalized else None
        matches = [
            row for row in self.task_contracts
            if (contract_id is None or row["id"] == contract_id)
            and (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
            and (dag_sha256 is None or row["dag_sha256"] == dag_sha256)
            and (group_idx is None or row["group_idx"] == group_idx)
            and (task_id is None or row["task_id"] == task_id)
            and (status is None or row["status"] == status)
        ]
        return matches[-1] if matches else None

    def _select_evidence_node(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        int_args = [arg for arg in args if isinstance(arg, int)]
        evidence_id = int_args[0] if int_args else None
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        if (
            feature_id is None
            and args
            and isinstance(args[0], str)
            and not _looks_like_idempotency_key(args[0])
        ):
            feature_id = args[0]
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        matches = [
            row for row in self.evidence_nodes
            if (evidence_id is None or row["id"] == evidence_id)
            and (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
        ]
        return matches[-1] if matches else None

    def _select_evidence_graph(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        projection_key = _first_matching(args, _looks_like_projection_key)
        dag_sha256 = _first_matching(args, lambda value: value == DAG_SHA256)
        proof_digest = _first_matching(
            args,
            lambda value: isinstance(value, str)
            and len(value) == 64
            and value != DAG_SHA256,
        )
        int_args = [arg for arg in args if isinstance(arg, int)]
        group_idx = int_args[0] if int_args else None
        stage = _first_matching(
            args,
            lambda value: isinstance(value, str) and value in {"initial", "retry-0", "verify"},
        )
        matches = [
            row for row in self.evidence_graphs
            if (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
            and (projection_key is None or row["projection_key"] == projection_key)
            and (dag_sha256 is None or row["dag_sha256"] == dag_sha256)
            and (group_idx is None or row["group_idx"] == group_idx)
            and (stage is None or row["stage"] == stage)
            and (proof_digest is None or row["proof_digest"] == proof_digest)
        ]
        return matches[-1] if matches else None

    def _select_evidence_edge(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        feature_id = _first_matching(args, lambda value: value == FEATURE_ID)
        idempotency_key = _first_matching(args, _looks_like_idempotency_key)
        matches = [
            row for row in self.evidence_edges
            if (feature_id is None or row["feature_id"] == feature_id)
            and (idempotency_key is None or row["idempotency_key"] == idempotency_key)
        ]
        return matches[-1] if matches else None

    def _select_outbox(self, args: tuple[object, ...]) -> dict[str, Any] | None:
        # The fake recognises three event_id shapes:
        #   * ``artifact-write:{artifact_id}`` (the legacy ``artifact.written``
        #     writer in store.py:_insert_dashboard_outbox);
        #   * ``control-plane-snapshot:{feature_id}:{snapshot_version}:control_plane.snapshot_changed``
        #     (legacy literal shape — preserved for backward-compat read);
        #   * a 64-char lower-hex sha256 digest (the doc-10
        #     ``control_plane_snapshot_event_id`` produced by
        #     ``public_dashboard._idempotency_key``).
        event_id = _first_matching(
            args,
            lambda value: isinstance(value, str)
            and (
                value.startswith("artifact-write:")
                or value.startswith("control-plane-snapshot:")
                or _looks_like_sha256(value)
            ),
        )
        matches = [
            row for row in self.public_dashboard_outbox
            if event_id is None or row["event_id"] == event_id
        ]
        return matches[-1] if matches else None


def _execution_control_module():
    errors: list[str] = []
    for module_name in (
        "iriai_build_v2.execution_control.store",
        "iriai_build_v2.execution_control",
        "iriai_build_v2.workflows.develop.execution.journal",
    ):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            errors.append(f"{module_name}: {exc}")
            continue
        if hasattr(module, "ExecutionControlStore"):
            return module
        errors.append(f"{module_name}: missing ExecutionControlStore")
    pytest.fail(
        "ExecutionControlStore public API is not available yet. Expected it in "
        "iriai_build_v2.execution_control.store with projection dataclasses. "
        f"Import attempts: {errors}"
    )


def _store(pool: _FakePool):
    module = _execution_control_module()
    cls = module.ExecutionControlStore
    try:
        return cls(pool)
    except TypeError as exc:
        pytest.fail(
            "ExecutionControlStore must be constructible with an asyncpg-style pool "
            f"for these contract tests: {exc}"
        )


@pytest.mark.asyncio
async def test_runtime_failure_context_reader_scopes_and_bounds_details() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    long_detail = "x" * 4500
    collision_message = (
        "Sandbox binding failed: sandbox path already belongs to a different lease: "
        "/tmp/workspace/.iriai/features/feature/sandboxes/g77/attempt-2"
    )
    conn.evidence_nodes.append(
        {
            "id": 501,
            "feature_id": FEATURE_ID,
            "idempotency_key": "idem:failure:501",
            "execution_journal_row_id": 301,
            "attempt_id": 301,
            "contract_id": None,
            "snapshot_id": None,
            "group_idx": 77,
            "stage": "implementation",
            "kind": "runtime_failure_context",
            "name": "runtime failure",
            "status": "failed",
            "deterministic": True,
            "source_ref": "runtime",
            "artifact_id": None,
            "artifact_key": "",
            "event_id": None,
            "input_refs": "[]",
            "output_refs": "[]",
            "failure_id": None,
            "verdict_id": None,
            "content_hash": "failure-hash",
            "summary": "Sandbox binding failed",
            "metadata": "{}",
            "payload": json.dumps(
                {
                    "failure_class": "sandbox_binding",
                    "failure_type": "runtime_workspace_binding_failed",
                    "terminal_reason": "sandbox_binding_failed",
                    "evidence_ids": [777],
                    "details": {
                        "message": collision_message,
                        "large": long_detail,
                    },
                }
            ),
            "started_at": None,
            "finished_at": None,
            "created_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
        }
    )
    conn.evidence_nodes.append(
        {
            "id": 777,
            "feature_id": FEATURE_ID,
            "idempotency_key": "idem:patch:777",
            "execution_journal_row_id": 301,
            "attempt_id": None,
            "contract_id": None,
            "snapshot_id": None,
            "group_idx": 77,
            "stage": "implementation",
            "kind": "sandbox_patch_summary",
            "name": "patch summary",
            "status": "approved",
            "deterministic": True,
            "source_ref": "sandbox-physical-777",
            "artifact_id": None,
            "artifact_key": "",
            "event_id": None,
            "input_refs": "[]",
            "output_refs": "[]",
            "failure_id": None,
            "verdict_id": None,
            "content_hash": "patch-hash",
            "summary": "",
            "metadata": "{}",
            "payload": json.dumps(
                {
                    "sandbox_id": "sandbox-physical-777",
                    "diff_sha256": "d" * 64,
                    "changed_paths": ["src/app.py"],
                }
            ),
            "started_at": None,
            "finished_at": None,
            "created_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
        }
    )

    context = await store.get_runtime_failure_context(
        feature_id=FEATURE_ID,
        failure_id=501,
    )

    assert context is not None
    assert context["id"] == 501
    assert context["feature_id"] == FEATURE_ID
    assert context["evidence_ids"] == [777]
    assert context["sandbox_patch_summaries"] == [
        {
            "id": 777,
            "attempt_id": None,
            "sandbox_id": "sandbox-physical-777",
            "diff_sha256": "d" * 64,
            "changed_paths": ["src/app.py"],
        }
    ]
    assert "sandbox path already belongs" in context["details"]["message"]
    assert context["details"]["large"].endswith("...[truncated 500 chars]")
    assert await store.get_runtime_failure_context(
        feature_id="other-feature",
        failure_id=501,
    ) is None


def _fake_dispatcher_state_rank(dispatcher_state: str) -> int:
    return {
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
    }.get(dispatcher_state, 0)


def _projection(name: str, **overrides: Any) -> Any:
    module = _execution_control_module()
    cls = getattr(module, name, None)
    if cls is None:
        pytest.fail(f"Missing public projection input class {name}")
    values = {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA256,
        "task_id": "TASK-typed-1",
        "group_idx": 7,
        "stage": "retry-0",
        "overlay_slug": "g7-g9",
        "projection_key": "dag-task:TASK-typed-1",
        "artifact_key": "dag-task:TASK-typed-1",
        "projection_body": {"status": "done"},
        "artifact_body": {"status": "done"},
        "body": {"status": "done"},
        "value": {"status": "done"},
        "source_table": "evidence_nodes",
        "source_id": 200,
        "typed_row_id": 200,
        "source_kind": "structured_result",
        "idempotency_key": "idem:task:TASK-typed-1",
        **overrides,
    }
    body_text = _body_to_text(
        values.get("artifact_body")
        or values.get("projection_body")
        or values.get("body")
        or values.get("value")
    )
    values.setdefault("body_sha256", _sha256(body_text))
    values.setdefault("projection_sha256", _sha256(body_text))

    try:
        fields = getattr(cls, "model_fields", None)
        if fields is not None:
            return cls(**{key: value for key, value in values.items() if key in fields})
        signature = inspect.signature(cls)
        accepts_kwargs = any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        if accepts_kwargs:
            return cls(**values)
        return cls(**{key: value for key, value in values.items() if key in signature.parameters})
    except Exception as exc:  # pragma: no cover - message is for implementation bring-up.
        pytest.fail(
            f"Could not construct {name} from the expected projection contract fields. "
            f"Accepted public fields should include feature_id, projection key/body, "
            f"typed source identity, and idempotency_key. Error: {exc}"
        )


def _append_checkpoint_gate_evidence(
    conn: _FakeConnection,
    *,
    group_idx: int = 7,
    status: str = "approved",
) -> dict[str, Any]:
    conn._next_evidence_node_id += 1
    payload = {
        "dag_sha256": DAG_SHA256,
        "gate": "checkpoint_gate",
        "group_idx": group_idx,
    }
    row = {
        "id": conn._next_evidence_node_id,
        "feature_id": FEATURE_ID,
        "idempotency_key": f"idem:checkpoint-gate:g{group_idx}:{conn._next_evidence_node_id}",
        "execution_journal_row_id": 200,
        "attempt_id": None,
        "contract_id": None,
        "snapshot_id": None,
        "group_idx": group_idx,
        "stage": "checkpoint",
        "kind": "checkpoint_gate",
        "name": f"checkpoint_gate:g{group_idx}",
        "status": status,
        "deterministic": True,
        "source_ref": f"dag-verify:g{group_idx}:checkpoint",
        "artifact_id": None,
        "artifact_key": "",
        "event_id": None,
        "input_refs": "[]",
        "output_refs": "[]",
        "failure_id": None,
        "verdict_id": None,
        "content_hash": _sha256(json.dumps(payload, sort_keys=True)),
        "summary": "",
        "metadata": "{}",
        "payload": json.dumps(payload, sort_keys=True),
        "started_at": None,
        "finished_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    }
    conn.evidence_nodes.append(row)
    return row


def _with_checkpoint_gate_source(
    conn: _FakeConnection,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    group_idx = int(overrides.get("group_idx") or 7)
    gate = _append_checkpoint_gate_evidence(conn, group_idx=group_idx)
    return {
        **overrides,
        "source_table": "evidence_nodes",
        "source_id": gate["id"],
    }


def test_documented_journal_import_path_reexports_canonical_store() -> None:
    primary = importlib.import_module("iriai_build_v2.execution_control")
    documented = importlib.import_module(
        "iriai_build_v2.workflows.develop.execution.journal"
    )

    assert documented.ExecutionControlStore is primary.ExecutionControlStore
    assert documented.TaskResultProjection is primary.TaskResultProjection
    assert documented.GroupCheckpointProjection is primary.GroupCheckpointProjection


@pytest.mark.asyncio
async def test_pool_path_acquires_connection_and_transaction() -> None:
    conn = _FakeConnection()
    pool = _FakePool(conn)
    store = _store(pool)
    body = json.dumps({"task_id": "TASK-typed-1", "status": "done"}, sort_keys=True)
    projection = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )

    await _call_projection(store, "project_task_result", projection)

    assert pool.acquire_count == 1
    assert conn.transaction_entries == 1
    assert conn.transaction_commits == 1
    assert any("pg_advisory_xact_lock" in call[0] for call in conn.calls)


def _terminal_attempt_row(
    *,
    row_id: int,
    status: str,
    idempotency_key: str,
    hour: int,
) -> dict[str, Any]:
    """A minimal dispatch_attempt journal row for get_latest_terminal_* tests."""
    return {
        "id": row_id,
        "feature_id": FEATURE_ID,
        "idempotency_key": idempotency_key,
        "entry_type": "dispatch_attempt",
        "status": status,
        "dispatcher_state": status,
        "actor": "dispatcher",
        "runtime": "claude",
        "dag_sha256": "dag-sha-test",
        "group_idx": 78,
        "task_id": "TASK-9-3",
        "request_digest": f"digest-{row_id}",
        "payload": json.dumps({
            "dispatch_outcome": {"status": status, "idempotency_key": idempotency_key},
        }),
        "requires_legacy_visibility": False,
        "projection_mode": "legacy_compatibility",
        "created_at": datetime(2026, 5, 28, hour, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 28, hour, tzinfo=timezone.utc),
    }


@pytest.mark.asyncio
async def test_get_latest_terminal_dispatch_outcome_honors_superseding_success() -> None:
    # The 8ac124d6 root cause: stale failures followed by a later SUCCESS. The
    # newest terminal outcome must be the success, so resume stops replaying the
    # superseded failures.
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    conn.typed_rows.append(_terminal_attempt_row(
        row_id=66, status="failed",
        idempotency_key="dispatch:feature-typed:g78:t0:TASK-9-3:a0:implementation", hour=1))
    conn.typed_rows.append(_terminal_attempt_row(
        row_id=110, status="incomplete",
        idempotency_key="dispatch:feature-typed:g78:t0:TASK-9-3:a5:implementation", hour=2))
    conn.typed_rows.append(_terminal_attempt_row(
        row_id=135, status="succeeded",
        idempotency_key="dispatch:feature-typed:g78:t0:TASK-9-3:a6:implementation", hour=3))

    result = await store.get_latest_terminal_dispatch_outcome(
        feature_id=FEATURE_ID, dag_sha256="dag-sha-test", group_idx=78, task_id="TASK-9-3",
    )

    assert result is not None
    assert result["status"] == "succeeded"
    assert result["idempotency_key"].endswith(":a6:implementation")
    assert result["dispatch_attempt_id"] == 135


@pytest.mark.asyncio
async def test_get_latest_terminal_dispatch_outcome_returns_latest_failure_without_success() -> None:
    # A latest terminal FAILURE (no later success) must NOT look like a success,
    # so strict retry/RCA behavior is preserved for genuinely-current failures.
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    conn.typed_rows.append(_terminal_attempt_row(
        row_id=66, status="failed",
        idempotency_key="dispatch:feature-typed:g78:t0:TASK-9-3:a0:implementation", hour=1))
    conn.typed_rows.append(_terminal_attempt_row(
        row_id=105, status="failed",
        idempotency_key="dispatch:feature-typed:g78:t0:TASK-9-3:a4:implementation", hour=2))

    result = await store.get_latest_terminal_dispatch_outcome(
        feature_id=FEATURE_ID, dag_sha256="dag-sha-test", group_idx=78, task_id="TASK-9-3",
    )

    assert result is not None
    assert result["status"] == "failed"
    assert result["dispatch_attempt_id"] == 105


@pytest.mark.asyncio
async def test_get_latest_terminal_dispatch_outcome_none_when_no_attempts() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))

    result = await store.get_latest_terminal_dispatch_outcome(
        feature_id=FEATURE_ID, dag_sha256="dag-sha-test", group_idx=78, task_id="TASK-9-3",
    )

    assert result is None


@pytest.mark.asyncio
async def test_insert_race_revalidates_existing_typed_digest() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first_body = json.dumps({"task_id": "TASK-typed-1", "status": "done"}, sort_keys=True)
    first = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=first_body,
        artifact_body=first_body,
        body=first_body,
        value=first_body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )
    await _call_projection(store, "project_task_result", first)
    artifact_count = len(conn.artifacts)

    conflicting = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=first_body,
        artifact_body=first_body,
        body=first_body,
        value=first_body,
        task_id="TASK-typed-1",
        source_kind="different_actor",
        idempotency_key="idem:task:TASK-typed-1",
    )
    conn.skip_next_typed_select = True

    with pytest.raises(module.IdempotencyConflict):
        await _call_projection(store, "project_task_result", conflicting)

    assert len(conn.artifacts) == artifact_count
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_projection_conflict_rolls_back_unlinked_artifact() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first_body = json.dumps({"task_id": "TASK-typed-1", "status": "done"}, sort_keys=True)
    first = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=first_body,
        artifact_body=first_body,
        body=first_body,
        value=first_body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )
    await _call_projection(store, "project_task_result", first)
    artifact_count = len(conn.artifacts)

    conflicting_body = json.dumps({"task_id": "TASK-typed-1", "status": "different"}, sort_keys=True)
    conflicting = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=conflicting_body,
        artifact_body=conflicting_body,
        body=conflicting_body,
        value=conflicting_body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )
    conn.skip_next_projection_select = True

    with pytest.raises(module.IdempotencyConflict):
        await _call_projection(store, "project_task_result", conflicting)

    assert len(conn.artifacts) == artifact_count
    assert [row["value"] for row in conn.artifacts] == [first_body]
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_projection_idempotency_must_belong_to_current_typed_row() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    body = json.dumps({"task_id": "TASK-typed-1", "status": "done"}, sort_keys=True)
    shared_projection = module.CompatibilityProjection(
        key="dag-task:TASK-typed-1",
        value=body,
        idempotency_key="idem:projection:shared",
    )
    first = module.ExecutionJournalWrite(
        feature_id=FEATURE_ID,
        idempotency_key="idem:typed:one",
        entry_type="task_result",
        status="succeeded",
        payload={"task_id": "TASK-typed-1", "attempt": 1},
        task_id="TASK-typed-1",
        requires_legacy_visibility=True,
        compatibility_projections=(shared_projection,),
    )
    await store.record_success(first)

    second = module.ExecutionJournalWrite(
        feature_id=FEATURE_ID,
        idempotency_key="idem:typed:two",
        entry_type="task_result",
        status="succeeded",
        payload={"task_id": "TASK-typed-1", "attempt": 2},
        task_id="TASK-typed-1",
        requires_legacy_visibility=True,
        compatibility_projections=(shared_projection,),
    )

    with pytest.raises(module.IdempotencyConflict, match="different typed row"):
        await store.record_success(second)

    assert len(conn.typed_rows) == 1
    assert len(conn.projection_links) == 1
    assert len(conn.artifacts) == 1
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_public_legacy_success_entry_types_require_projection() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    write = module.ExecutionJournalWrite(
        feature_id=FEATURE_ID,
        idempotency_key="idem:task:missing-projection",
        entry_type="task_result",
        status="succeeded",
        payload={"task_id": "TASK-typed-1"},
        requires_legacy_visibility=False,
        compatibility_projections=(),
    )

    with pytest.raises(module.MissingCompatibilityProjection):
        await store.record_success(write)


@pytest.mark.asyncio
async def test_projection_methods_enforce_legacy_key_family() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body="{}",
        artifact_body="{}",
        body="{}",
        value="{}",
        group_idx=7,
        source_table="merge_queue_items",
        idempotency_key="idem:checkpoint:wrong-key",
    )

    with pytest.raises(module.UnsupportedCompatibilityProjection, match="group_checkpoint"):
        await _call_projection(store, "project_group_checkpoint", projection)


async def _call_projection(store: Any, method_name: str, projection: Any) -> Any:
    method = getattr(store, method_name, None)
    if method is None:
        pytest.fail(f"ExecutionControlStore is missing public method {method_name}")
    return await method(projection)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "projection_class", "projection_key", "body", "overrides"),
    [
        (
            "project_task_result",
            "TaskResultProjection",
            "dag-task:TASK-typed-1",
            {"task_id": "TASK-typed-1", "status": "done", "files_modified": ["app.py"]},
            {"task_id": "TASK-typed-1", "source_kind": "structured_result"},
        ),
        (
            "project_verify_result",
            "VerifyProjection",
            "dag-verify:g7:retry-0",
            {"group_idx": 7, "stage": "retry-0", "status": "failed", "concerns": ["pytest"]},
            {"group_idx": 7, "stage": "retry-0", "source_kind": "aggregate_verdict"},
        ),
        (
            "project_commit_failure",
            "CommitFailureProjection",
            "dag-commit-failure:g7:retry-0",
            {"group_idx": 7, "stage": "retry-0", "returncode": 1, "stderr": "precommit"},
            {"group_idx": 7, "stage": "retry-0", "failure_class": "commit_hygiene"},
        ),
        (
            "project_group_checkpoint",
            "GroupCheckpointProjection",
            "dag-group:7",
            {"group_idx": 7, "status": "done", "commit": "abc123", "results": ["TASK-typed-1"]},
            {"group_idx": 7, "source_table": "merge_queue_items", "status": "done"},
        ),
        (
            "project_regroup_overlay",
            "RegroupProjection",
            "dag-regroup:g7-g9",
            {"overlay_slug": "g7-g9", "status": "draft", "groups": [7, 8, 9]},
            {"overlay_slug": "g7-g9", "source_table": "execution_regroup_overlays"},
        ),
        (
            "project_regroup_active",
            "RegroupActiveProjection",
            "dag-regroup-active:g7-g9",
            {"overlay_slug": "g7-g9", "status": "active", "activated_by_group": 7},
            {"overlay_slug": "g7-g9", "source_table": "execution_regroup_overlays"},
        ),
    ],
)
async def test_typed_projection_writes_exact_legacy_artifact_parity(
    method_name: str,
    projection_class: str,
    projection_key: str,
    body: dict[str, Any],
    overrides: dict[str, Any],
) -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    if method_name == "project_group_checkpoint":
        overrides = _with_checkpoint_gate_source(conn, overrides)
    body_text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    projection = _projection(
        projection_class,
        projection_key=projection_key,
        artifact_key=projection_key,
        projection_body=body_text,
        artifact_body=body_text,
        body=body_text,
        value=body_text,
        idempotency_key=f"idem:{projection_key}",
        **overrides,
    )

    await _call_projection(store, method_name, projection)

    artifacts = [row for row in conn.artifacts if row["key"] == projection_key]
    assert len(artifacts) == 1, (
        f"{method_name} must synchronously create exactly one legacy artifact "
        f"projection for {projection_key}"
    )
    assert artifacts[0]["feature_id"] == FEATURE_ID
    assert artifacts[0]["value"] == body_text
    assert artifacts[0]["sha256"] == _sha256(body_text)
    expected_source_table = (
        "evidence_nodes"
        if method_name == "project_group_checkpoint"
        else "execution_journal_rows"
    )
    expected_source_id = (
        overrides.get("source_id") if method_name == "project_group_checkpoint" else None
    )
    _assert_projection_link(
        conn,
        projection_key,
        body_text,
        f"idem:{projection_key}",
        expected_source_table=expected_source_table,
        expected_source_id=expected_source_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "projection_class", "projection_key", "expected_body", "overrides"),
    [
        (
            "project_task_result",
            "TaskResultProjection",
            "dag-task:TASK-typed-1",
            ImplementationResult(
                task_id="TASK-typed-1",
                summary="done",
                status="completed",
                files_modified=["app.py"],
            ).model_dump_json(),
            {
                "implementation_result": ImplementationResult(
                    task_id="TASK-typed-1",
                    summary="done",
                    status="completed",
                    files_modified=["app.py"],
                ),
                "task_id": "TASK-typed-1",
                "source_kind": "structured_result",
            },
        ),
        (
            "project_verify_result",
            "VerifyProjection",
            "dag-verify:g7:retry-0",
            Verdict(
                approved=False,
                summary="pytest failed",
                concerns=[
                    Issue(severity="major", description="pytest", file="tests/test_app.py")
                ],
            ).model_dump_json(indent=2),
            {
                "verdict": Verdict(
                    approved=False,
                    summary="pytest failed",
                    concerns=[
                        Issue(severity="major", description="pytest", file="tests/test_app.py")
                    ],
                ),
                "group_idx": 7,
                "stage": "retry-0",
                "source_kind": "aggregate_verdict",
            },
        ),
        (
            "project_commit_failure",
            "CommitFailureProjection",
            "dag-commit-failure:g7:retry-0",
            json.dumps(
                {"group_idx": 7, "stage": "retry-0", "returncode": 1, "stderr": "precommit"},
                indent=2,
            ),
            {
                "commit_failure_payload": {
                    "group_idx": 7,
                    "stage": "retry-0",
                    "returncode": 1,
                    "stderr": "precommit",
                },
                "group_idx": 7,
                "stage": "retry-0",
                "failure_class": "commit_hygiene",
            },
        ),
        (
            "project_group_checkpoint",
            "GroupCheckpointProjection",
            "dag-group:7",
            json.dumps({
                "group_idx": 7,
                "status": "done",
                "commit_hash": "abc123",
                "results": ["TASK-typed-1"],
            }),
            {
                "checkpoint": {
                    "group_idx": 7,
                    "status": "done",
                    "commit_hash": "abc123",
                    "results": ["TASK-typed-1"],
                },
                "group_idx": 7,
                "source_table": "merge_queue_items",
                "status": "done",
            },
        ),
    ],
)
async def test_projection_compiles_legacy_serializer_byte_shapes(
    method_name: str,
    projection_class: str,
    projection_key: str,
    expected_body: str,
    overrides: dict[str, Any],
) -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    if method_name == "project_group_checkpoint":
        overrides = _with_checkpoint_gate_source(conn, overrides)
    projection = _projection(
        projection_class,
        projection_key=projection_key,
        artifact_key=projection_key,
        projection_body=None,
        artifact_body=None,
        body=None,
        value=None,
        idempotency_key=f"idem:serializer:{projection_key}",
        **overrides,
    )

    await _call_projection(store, method_name, projection)

    artifact = next(row for row in conn.artifacts if row["key"] == projection_key)
    assert artifact["value"] == expected_body
    assert artifact["sha256"] == _sha256(expected_body)
    expected_source_table = (
        "evidence_nodes"
        if method_name == "project_group_checkpoint"
        else "execution_journal_rows"
    )
    expected_source_id = (
        overrides.get("source_id") if method_name == "project_group_checkpoint" else None
    )
    _assert_projection_link(
        conn,
        projection_key,
        expected_body,
        f"idem:serializer:{projection_key}",
        expected_source_table=expected_source_table,
        expected_source_id=expected_source_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "projection_class", "projection_key", "event_type", "event_content", "overrides"),
    [
        (
            "project_commit_failure",
            "CommitFailureProjection",
            "dag-commit-failure:g7:retry-0",
            "dag_commit_failed",
            "g7:retry-0",
            {"group_idx": 7, "stage": "retry-0", "failure_class": "commit_hygiene"},
        ),
        (
            "project_group_checkpoint",
            "GroupCheckpointProjection",
            "dag-group:7",
            "dag_group_checkpoint",
            "group 7",
            {"group_idx": 7, "source_table": "merge_queue_items", "status": "done"},
        ),
    ],
)
async def test_commit_and_checkpoint_projection_link_legacy_event_and_outbox(
    method_name: str,
    projection_class: str,
    projection_key: str,
    event_type: str,
    event_content: str,
    overrides: dict[str, Any],
) -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    if method_name == "project_group_checkpoint":
        overrides = _with_checkpoint_gate_source(conn, overrides)
    body = json.dumps({"projection_key": projection_key}, sort_keys=True)
    projection = _projection(
        projection_class,
        projection_key=projection_key,
        artifact_key=projection_key,
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        idempotency_key=f"idem:lineage:{projection_key}",
        **overrides,
    )

    await _call_projection(store, method_name, projection)

    assert len(conn.events) == 1
    assert conn.events[0]["event_type"] == event_type
    assert conn.events[0]["content"] == event_content
    artifact_outbox = next(
        row for row in conn.public_dashboard_outbox if row["event_type"] == "artifact.written"
    )
    outbox_payload = json.loads(artifact_outbox["payload"])
    assert outbox_payload["source_artifact_id"] == conn.artifacts[0]["id"]
    assert outbox_payload["artifact_key"] == projection_key
    assert outbox_payload["sha256"] == _sha256(body)
    assert outbox_payload["size_bytes"] == len(body.encode("utf-8"))
    assert outbox_payload["content_type"] == "application/json"
    assert outbox_payload["visibility"] == "internal"
    assert outbox_payload["publish_artifact_candidate"] is False
    assert "created_at" in outbox_payload
    expected_source_table = (
        "evidence_nodes"
        if method_name == "project_group_checkpoint"
        else "execution_journal_rows"
    )
    expected_source_id = (
        overrides.get("source_id") if method_name == "project_group_checkpoint" else None
    )
    link = _assert_projection_link(
        conn,
        projection_key,
        body,
        f"idem:lineage:{projection_key}",
        expected_source_table=expected_source_table,
        expected_source_id=expected_source_id,
    )
    assert link["legacy_event_id"] == conn.events[0]["id"]
    assert link["dashboard_outbox_event_id"] == artifact_outbox["event_id"]


@pytest.mark.asyncio
async def test_group_checkpoint_projection_persists_typed_checkpoint_binding() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=7)
    checkpoint = {
        "group_idx": 7,
        "status": "done",
        "commit_hash": "abc123",
        "verify_projection_key": "dag-verify:g7:initial",
    }
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:7",
        artifact_key="dag-group:7",
        projection_body=None,
        artifact_body=None,
        body=None,
        value=None,
        checkpoint=checkpoint,
        group_idx=7,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:checkpoint:g7:binding",
    )

    result = await _call_projection(store, "project_group_checkpoint", projection)

    assert result.row.group_idx == 7
    assert result.row.payload["projection_key"] == "dag-group:7"
    assert result.row.payload["source_table"] == "evidence_nodes"
    assert result.row.payload["source_id"] == checkpoint_gate["id"]
    assert result.projection_links[0].payload["checkpoint"] == checkpoint
    assert result.projection_links[0].payload["group_idx"] == 7
    assert result.projection_links[0].payload["status"] == "done"
    assert result.projection_links[0].payload["projection_key"] == "dag-group:7"
    assert result.projection_links[0].source_table == "evidence_nodes"
    assert result.projection_links[0].source_id == checkpoint_gate["id"]
    assert result.projection_links[0].payload["evidence_kind"] == "checkpoint_gate"
    assert result.projection_links[0].payload["evidence_node_id"] == checkpoint_gate["id"]
    assert [row["id"] for row in conn.evidence_nodes if row["kind"] == "checkpoint_gate"] == [
        checkpoint_gate["id"]
    ]


@pytest.mark.asyncio
async def test_group_checkpoint_projection_replay_rejects_stale_source_link() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=7)
    checkpoint = {
        "group_idx": 7,
        "status": "done",
        "commit_hash": "abc123",
        "verify_projection_key": "dag-verify:g7:initial",
    }
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:7",
        artifact_key="dag-group:7",
        projection_body=None,
        artifact_body=None,
        body=None,
        value=None,
        checkpoint=checkpoint,
        group_idx=7,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:checkpoint:g7:stale-source",
    )

    await _call_projection(store, "project_group_checkpoint", projection)
    conn.projection_links[0]["source_table"] = "merge_queue_items"
    conn.projection_links[0]["source_id"] = 123
    conn.projection_links[0]["payload"] = json.dumps(
        {
            "group_idx": 7,
            "status": "done",
            "projection_key": "dag-group:7",
            "checkpoint": checkpoint,
        },
        sort_keys=True,
    )

    with pytest.raises(module.IdempotencyConflict, match="projection source"):
        await _call_projection(store, "project_group_checkpoint", projection)


@pytest.mark.asyncio
async def test_group_checkpoint_projection_replay_rejects_stale_source_payload() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=7)
    checkpoint = {
        "group_idx": 7,
        "status": "done",
        "commit_hash": "abc123",
        "verify_projection_key": "dag-verify:g7:initial",
    }
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:7",
        artifact_key="dag-group:7",
        projection_body=None,
        artifact_body=None,
        body=None,
        value=None,
        checkpoint=checkpoint,
        group_idx=7,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:checkpoint:g7:stale-source-payload",
    )

    await _call_projection(store, "project_group_checkpoint", projection)
    payload = json.loads(conn.projection_links[0]["payload"])
    payload["evidence_kind"] = "merge_gate"
    conn.projection_links[0]["payload"] = json.dumps(payload, sort_keys=True)

    with pytest.raises(module.IdempotencyConflict, match="source payload"):
        await _call_projection(store, "project_group_checkpoint", projection)


@pytest.mark.asyncio
async def test_group_checkpoint_projection_requires_existing_checkpoint_gate_source() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    body = json.dumps({"group_idx": 7, "status": "done"}, sort_keys=True)
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:7",
        artifact_key="dag-group:7",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        checkpoint={"group_idx": 7, "status": "done"},
        group_idx=7,
        status="done",
        source_table="merge_queue_items",
        source_id=None,
        idempotency_key="idem:checkpoint:g7:missing-gate",
    )

    with pytest.raises(module.MissingRequiredProjection, match="checkpoint_gate"):
        await _call_projection(store, "project_group_checkpoint", projection)

    assert conn.evidence_nodes == []
    assert conn.projection_links == []


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("wrong_kind", lambda row: row.update({"kind": "merge_gate"})),
        ("rejected", lambda row: row.update({"status": "rejected"})),
        ("wrong_feature", lambda row: row.update({"feature_id": "feat-other"})),
    ],
)
@pytest.mark.asyncio
async def test_group_checkpoint_projection_requires_approved_gate_source_cases(
    case: str,
    mutate,
) -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=7)
    mutate(checkpoint_gate)
    body = json.dumps({"group_idx": 7, "status": "done", "case": case}, sort_keys=True)
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:7",
        artifact_key="dag-group:7",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        checkpoint={"group_idx": 7, "status": "done", "case": case},
        group_idx=7,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key=f"idem:checkpoint:g7:{case}",
    )

    with pytest.raises(module.MissingRequiredProjection, match="approved checkpoint_gate"):
        await _call_projection(store, "project_group_checkpoint", projection)

    assert conn.projection_links == []


# ── Slice 10g-1 — doc-10-correct control_plane.snapshot_changed outbox ────
#
# These tests cover the Slice 10g-1 wiring of
# ``public_dashboard.project_control_plane_snapshot_if_changed`` into the tail
# of ``ExecutionControlStore._complete_missing_projections``. They replace the
# orphan full-dict outbox assertions that were surgically deleted from the 7
# legacy-shape tests above (see Slice 10g-1 journal). The shape under test is
# the bounded SUMMARY-only ``control_plane_snapshot_changed_payload`` —
# counters via ``len`` over typed summary lists, bounded ``evidence_refs``,
# NEVER artifact bodies / prompts / stdout/stderr.


def _outbox_snapshot(
    feature_id: str = FEATURE_ID,
    snapshot_version: str = "a" * 64,
    *,
    long_body: bool = False,
):
    """Build a bounded ``ControlPlaneSnapshot`` for the outbox-wiring tests.

    The summary rows optionally carry a 40 000-char body so a test can prove
    the bounded display event NEVER copies them across the public boundary.
    """

    from iriai_build_v2.workflows.develop.execution.snapshots import (
        ControlPlaneSnapshot,
        EvidenceRef,
        ExecutionAttemptSummary,
        TypedFailureSummary,
    )

    now = datetime(2026, 5, 22, 12, tzinfo=timezone.utc)
    body = "SECRET-BODY-" + ("z" * 40_000) if long_body else "short"
    attempt = ExecutionAttemptSummary(
        attempt_id=11,
        feature_id=feature_id,
        dag_sha256="d" * 64,
        group_idx=7,
        task_id="task-a",
        attempt_kind="merge",
        stage="merge",
        retry=1,
        status="started",
        actor="implementer",
        runtime="claude",
        input_digest=body,
        workspace_snapshot_id=None,
        latest_evidence_ids=[5],
        started_at=now,
        finished_at=None,
        updated_at=now,
    )
    failure = TypedFailureSummary(
        failure_id=21,
        attempt_id=11,
        evidence_id=5,
        failure_class="merge_conflict",
        failure_type="rebase_failed",
        severity="error",
        deterministic=True,
        operator_required=False,
        retryable=True,
        status="routed",
        route="retry_merge",
        signature_hash="s" * 16,
        summary=body,
        created_at=now,
        resolved_at=None,
    )
    ref = EvidenceRef(
        table="evidence_nodes",
        id=5,
        citation="evidence_nodes#5",
        kind="typed_failure",
        summary=body,
        artifact_key="dag-commit-failure:7",
    )
    return ControlPlaneSnapshot(
        feature_id=feature_id,
        snapshot_version=snapshot_version,
        generated_at=now,
        source="typed",
        active_group_idx=7,
        active_attempts=[attempt],
        latest_failures=[failure],
        recommended_route="retry_merge",
        recommended_action="recommend",
        evidence_refs=[ref],
    )


def _stub_snapshot_methods(
    store: Any,
    *,
    snapshot,
) -> None:
    """Replace the store's two Slice-10a snapshot reads with stubs.

    The wiring under test calls ``self.get_control_plane_snapshot_version``
    and ``self.get_control_plane_snapshot`` from inside the projection
    transaction. Stubbing them with a known typed snapshot isolates the
    outbox-wiring tests from the snapshot-builder SQL that the in-memory
    ``_FakeConnection`` does not fully simulate. The stubs accept (and
    ignore) the keyword-only ``conn`` argument that the Slice 10g-1 P2 fix
    now threads through — when the helper runs inside a projection
    transaction it passes ``conn=conn`` so the version-read participates in
    that transaction (the production store dispatch under READ-COMMITTED).
    """

    async def _version(_feature_id: str, *, conn: Any | None = None) -> str:
        return snapshot.snapshot_version

    async def _snapshot(_query, *, conn: Any | None = None) -> Any:
        return snapshot

    store.get_control_plane_snapshot_version = _version  # type: ignore[assignment]
    store.get_control_plane_snapshot = _snapshot  # type: ignore[assignment]


def _outbox_for(pool: Any):
    """Construct a ``PublicDashboardOutbox`` against the fake pool."""

    from iriai_build_v2.public_dashboard import PublicDashboardOutbox

    return PublicDashboardOutbox(pool, outbox_enabled=True)


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_wires_doc10_summary_outbox() -> None:
    """The projection seam enqueues exactly the doc-10 SUMMARY-only payload.

    Per the Slice 10g-1 wiring at the tail of
    ``_complete_missing_projections``, every successful typed projection
    enqueues a ``control_plane.snapshot_changed`` row whose payload is the
    bounded counters/route/citations shape projected by
    ``control_plane_snapshot_changed_payload`` — NOT the full-dict legacy
    shape. No artifact body, prompt, stdout/stderr, or unbounded list crosses
    the public boundary.
    """

    from iriai_build_v2.public_dashboard import (
        CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
        control_plane_snapshot_event_id,
    )

    conn = _FakeConnection()
    pool = _FakePool(conn)
    outbox = _outbox_for(pool)
    store = _store_with_outbox(pool, outbox=outbox)
    snapshot = _outbox_snapshot(snapshot_version="a" * 64, long_body=True)
    _stub_snapshot_methods(store, snapshot=snapshot)
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=7)
    body = json.dumps({"projection_key": "dag-group:7"}, sort_keys=True)
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:7",
        artifact_key="dag-group:7",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        group_idx=7,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:projection-outbox:g7:doc10",
    )

    await _call_projection(store, "project_group_checkpoint", projection)

    snapshot_rows = [
        row
        for row in conn.public_dashboard_outbox
        if row["event_type"] == CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT
    ]
    assert len(snapshot_rows) == 1
    # event_id keying — doc 10 § "Dashboard Integration Points".
    assert snapshot_rows[0]["event_id"] == control_plane_snapshot_event_id(
        FEATURE_ID, "a" * 64
    )
    assert snapshot_rows[0]["visibility"] == "public"
    payload = json.loads(snapshot_rows[0]["payload"])
    # Doc-10 SUMMARY-only shape — counters present, full lists absent.
    assert payload["feature_id"] == FEATURE_ID
    assert payload["snapshot_version"] == "a" * 64
    assert payload["counters"]["active_attempts"] == 1
    assert payload["counters"]["latest_failures"] == 1
    assert payload["counters"]["evidence_refs"] == 1
    assert payload["recommended_route"] == "retry_merge"
    assert payload["recommended_action"] == "recommend"
    for body_field in (
        "active_attempts",
        "latest_failures",
        "workspace_snapshots",
        "merge_queue",
        "retry_budgets",
        "sandbox_leases",
        "runtime_bindings",
        "gates",
        "checkpoints",
    ):
        assert body_field not in payload, body_field
    # The 40 000-char body never crosses the public boundary, anywhere.
    serialized = snapshot_rows[0]["payload"]
    assert "SECRET-BODY" not in serialized
    assert "z" * 200 not in serialized
    # Evidence refs are bounded summaries (id + citation + kind), without
    # body summary or artifact key.
    assert payload["evidence_refs"] == [
        {
            "table": "evidence_nodes",
            "id": 5,
            "citation": "evidence_nodes#5",
            "kind": "typed_failure",
        }
    ]


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_is_idempotent_on_repeat() -> None:
    """A re-projection at the same typed snapshot version → exactly one row.

    ``project_control_plane_snapshot_changed`` keys idempotency on
    ``(feature_id, snapshot_version)`` via
    ``ON CONFLICT (event_id) DO NOTHING``, so a racing or replayed projection
    of the same advanced version never enqueues a duplicate public
    notification.
    """

    from iriai_build_v2.public_dashboard import (
        CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
    )

    conn = _FakeConnection()
    pool = _FakePool(conn)
    outbox = _outbox_for(pool)
    store = _store_with_outbox(pool, outbox=outbox)
    _stub_snapshot_methods(store, snapshot=_outbox_snapshot(snapshot_version="b" * 64))
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=8)
    body = json.dumps({"projection_key": "dag-group:8"}, sort_keys=True)
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:8",
        artifact_key="dag-group:8",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        group_idx=8,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:projection-outbox:g8:idempotent",
    )

    await _call_projection(store, "project_group_checkpoint", projection)
    await _call_projection(store, "project_group_checkpoint", projection)

    snapshot_rows = [
        row
        for row in conn.public_dashboard_outbox
        if row["event_type"] == CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT
    ]
    assert len(snapshot_rows) == 1


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_uses_caller_conn_for_version_read() -> None:
    """Slice 10g-1 P2 — the version-read MUST execute on the caller's
    connection so it sees the active projection transaction's uncommitted
    typed inserts under Postgres READ-COMMITTED isolation.

    Without this property, the version-read on a NEW pooled connection
    returns the PRE-transaction ``snapshot_version``; the outbox INSERT
    collides on ``event_id = sha256(feature_id, V_prev, ...)`` with an
    already-emitted earlier transition; ``ON CONFLICT (event_id) DO
    NOTHING`` silently drops the new emission — a doc-10 contract
    violation and a silent degradation.

    The proof shape: spy on the two Slice-10a snapshot reads. When the
    projection-transaction wiring fires, both reads MUST receive
    ``conn=conn`` where ``conn`` is the SAME ``_FakeConnection`` instance
    backing the active projection transaction. If either read receives
    ``conn=None`` (the pre-fix shape), it would dispatch to a new pool
    connection in production — silently undoing the P2 fix.
    """

    from iriai_build_v2.public_dashboard import (
        CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
    )

    conn = _FakeConnection()
    pool = _FakePool(conn)
    outbox = _outbox_for(pool)
    store = _store_with_outbox(pool, outbox=outbox)
    snapshot = _outbox_snapshot(snapshot_version="d" * 64)

    # Spy on the two snapshot reads — record the ``conn`` kwarg each
    # invocation observes. Production passes ``conn=conn`` from inside
    # ``_complete_missing_projections`` after the P2 fix; the pre-fix
    # shape (no kwarg, or ``conn=None``) is what we are guarding against.
    version_conn_kwargs: list[Any] = []
    snapshot_conn_kwargs: list[Any] = []

    async def _spy_version(_feature_id: str, *, conn: Any | None = None) -> str:
        version_conn_kwargs.append(conn)
        return snapshot.snapshot_version

    async def _spy_snapshot(_query, *, conn: Any | None = None) -> Any:
        snapshot_conn_kwargs.append(conn)
        return snapshot

    store.get_control_plane_snapshot_version = _spy_version  # type: ignore[assignment]
    store.get_control_plane_snapshot = _spy_snapshot  # type: ignore[assignment]

    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=11)
    body = json.dumps({"projection_key": "dag-group:11"}, sort_keys=True)
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:11",
        artifact_key="dag-group:11",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        group_idx=11,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:projection-outbox:g11:caller-conn",
    )

    await _call_projection(store, "project_group_checkpoint", projection)

    # Both snapshot reads fired exactly once.
    assert len(version_conn_kwargs) == 1, version_conn_kwargs
    assert len(snapshot_conn_kwargs) == 1, snapshot_conn_kwargs
    # And BOTH received the SAME ``_FakeConnection`` instance backing the
    # active projection transaction — NOT ``None`` (the pre-fix shape that
    # would dispatch to a fresh pool connection in production).
    assert version_conn_kwargs[0] is conn, (
        "version-read must execute on the caller's connection — the P2 "
        "cross-connection stale-read fix is silently undone otherwise"
    )
    assert snapshot_conn_kwargs[0] is conn, (
        "bounded snapshot read must also execute on the caller's "
        "connection (same READ-COMMITTED visibility requirement)"
    )
    # And the outbox row still lands — the conn-threading change preserves
    # the surrounding wiring contract.
    snapshot_rows = [
        row
        for row in conn.public_dashboard_outbox
        if row["event_type"] == CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT
    ]
    assert len(snapshot_rows) == 1


@pytest.mark.asyncio
async def test_get_control_plane_snapshot_version_with_caller_conn_skips_pool() -> None:
    """Lower-level proof: when ``conn`` is passed to the typed store read,
    the store dispatches to THAT connection and NEVER touches the pool.

    A regression here would silently undo the Slice 10g-1 P2 fix even if
    the helper threads ``conn`` correctly — so we pin the store-level
    contract independently. The same property holds for
    ``get_control_plane_snapshot``.
    """

    from iriai_build_v2.workflows.develop.execution.snapshots import (
        ControlPlaneSnapshotQuery,
        SnapshotBudget,
    )

    pool_conn = _FakeConnection()
    pool = _FakePool(pool_conn)
    store = _store(pool)
    pool.acquire_count = 0

    caller_conn = _FakeConnection()
    # ``get_control_plane_snapshot_version`` with ``conn=caller_conn`` must
    # execute on ``caller_conn`` and MUST NOT acquire from the pool.
    await store.get_control_plane_snapshot_version(
        "feat-caller-conn", conn=caller_conn
    )
    assert pool.acquire_count == 0, (
        "passing conn=... must NOT open a new pool connection — the P2 "
        "cross-connection stale-read fix depends on this"
    )

    # ``get_control_plane_snapshot`` with ``conn=caller_conn`` — same rule.
    query = ControlPlaneSnapshotQuery(
        feature_id="feat-caller-conn",
        scope="dashboard",
        budget=SnapshotBudget(),
    )
    await store.get_control_plane_snapshot(query, conn=caller_conn)
    assert pool.acquire_count == 0, (
        "passing conn=... must NOT open a new pool connection for the "
        "bounded snapshot read either"
    )


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_is_noop_when_outbox_is_none() -> None:
    """When the store is constructed without an outbox handle, the projection
    seam SKIPS the wiring entirely — no row is enqueued and no fallback path
    runs. This is the documented disabled-outbox configuration, NOT a silent
    degrade.
    """

    from iriai_build_v2.public_dashboard import (
        CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
    )

    conn = _FakeConnection()
    pool = _FakePool(conn)
    # No public_dashboard_outbox passed → store defaults to None and the
    # wiring at _complete_missing_projections short-circuits.
    store = _store(pool)
    _stub_snapshot_methods(store, snapshot=_outbox_snapshot(snapshot_version="c" * 64))
    checkpoint_gate = _append_checkpoint_gate_evidence(conn, group_idx=9)
    body = json.dumps({"projection_key": "dag-group:9"}, sort_keys=True)
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:9",
        artifact_key="dag-group:9",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        group_idx=9,
        status="done",
        source_table="evidence_nodes",
        source_id=checkpoint_gate["id"],
        idempotency_key="idem:projection-outbox:g9:noop",
    )

    await _call_projection(store, "project_group_checkpoint", projection)

    # The compatibility artifact.written outbox row still lands (it has a
    # production caller in store.py independent of Slice 10g-1) — but no
    # control_plane.snapshot_changed row exists.
    snapshot_rows = [
        row
        for row in conn.public_dashboard_outbox
        if row["event_type"] == CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT
    ]
    assert snapshot_rows == []


def _store_with_outbox(pool: _FakePool, *, outbox: Any) -> Any:
    """Construct the typed store with the Slice 10g-1 outbox handle wired."""

    module = _execution_control_module()
    cls = module.ExecutionControlStore
    return cls(pool, public_dashboard_outbox=outbox)


@pytest.mark.asyncio
async def test_control_plane_snapshot_projects_scalars_without_payload_bodies() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    large_payload = "x" * 20_000
    conn.typed_rows.append({
        "id": 201,
        "feature_id": FEATURE_ID,
        "idempotency_key": "idem:attempt:large",
        "entry_type": "dispatch_attempt",
        "status": "started",
        "dispatcher_state": "attempt_started",
        "actor": "dispatcher",
        "runtime": "codex",
        "dag_sha256": DAG_SHA256,
        "group_idx": 9,
        "task_id": "TASK-typed-1",
        "request_digest": "attempt-digest",
        "payload": json.dumps({
            "retry": 2,
            "retry_budget": {
                "max_retries": 3,
                "idempotency_key": "idem:dispatch:TASK-typed-1",
                "large": large_payload,
            },
            "runtime_policy_digest": "policy-digest",
            "workspace_snapshot_ids": [10, 11],
            "raw_execution_body": large_payload,
        }),
        "requires_legacy_visibility": False,
        "projection_mode": "legacy_compatibility",
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    })
    conn.evidence_nodes.append({
        "id": 301,
        "feature_id": FEATURE_ID,
        "idempotency_key": "idem:failure:large",
        "execution_journal_row_id": 201,
        "attempt_id": 201,
        "contract_id": None,
        "snapshot_id": None,
        "group_idx": 9,
        "stage": "retry-0",
        "kind": "runtime_failure_context",
        "name": "runtime failure",
        "status": "failed",
        "deterministic": False,
        "source_ref": "runtime",
        "artifact_id": None,
        "artifact_key": "",
        "event_id": None,
        "input_refs": "[]",
        "output_refs": "[]",
        "failure_id": None,
        "verdict_id": None,
        "content_hash": "failure-hash",
        "summary": "runtime failed",
        "metadata": "{}",
        "payload": json.dumps({
            "failure_class": "runtime_provider",
            "failure_type": "provider_rate_limited",
            "route": "retry_dispatch",
            "retryable": True,
            "retry_budget": {
                "route": "retry_dispatch",
                "max_retries": 3,
                "large": large_payload,
            },
            "route_decision": {
                "route": "retry_dispatch",
                "failure_class": "runtime_provider",
                "failure_type": "provider_rate_limited",
                "retryable": True,
                "large": large_payload,
            },
            "raw_evidence_body": large_payload,
        }),
        "started_at": None,
        "finished_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    })
    conn.projection_links.append({
        "id": 401,
        "feature_id": FEATURE_ID,
        "typed_row_id": 201,
        "source_table": "execution_journal_rows",
        "source_id": 201,
        "projection_owner": "merge_queue",
        "projection_kind": "group_checkpoint",
        "projection_key": "dag-group:9",
        "artifact_id": 501,
        "projection_sha256": _sha256("{}"),
        "legacy_event_id": None,
        "dashboard_outbox_event_id": None,
        "payload": json.dumps({
            "group_idx": 9,
            "status": "ready",
            "raw_projection_body": large_payload,
        }),
        "idempotency_key": "idem:projection:large",
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    })
    for idx in range(2):
        conn.runtime_workspace_bindings.append({
            "id": 270 + idx,
            "feature_id": FEATURE_ID,
            "idempotency_key": f"idem:runtime-binding:{idx}",
            "sandbox_lease_id": 260,
            "attempt_id": 201,
            "runtime_name": "codex",
            "cwd": f"/sandbox/feature-typed/g9/attempt-{idx}/app",
            "workspace_override": f"/sandbox/feature-typed/g9/attempt-{idx}/app",
            "manifest_path": (
                f"/sandbox/feature-typed/g9/attempt-{idx}/sandbox-manifest.json"
            ),
            "repo_roots": json.dumps({"app": f"/sandbox/g9/{idx}/app"}),
            "writable_roots": "[]",
            "readonly_roots": "[]",
            "blocked_roots": "[]",
            "env": json.dumps({"SECRET_PAYLOAD": large_payload}),
            "role_metadata": json.dumps({"raw_runtime_binding_body": large_payload}),
            "role_metadata_digest": f"role-metadata-digest-{idx}",
            "status": "bound",
            "binding_digest": f"binding-digest-{idx}",
            "payload": json.dumps({"raw_runtime_binding_body": large_payload}),
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        })

    snapshot = await module.fetch_control_plane_snapshot(
        conn,
        FEATURE_ID,
        budgets={"runtime_workspace_bindings": 1},
    )
    dumped = snapshot.model_dump(mode="json")
    encoded = json.dumps(dumped, sort_keys=True)
    control_plane_queries = [
        " ".join(sql.lower().split())
        for sql, _args in conn.calls
        if "from execution_journal_rows" in sql.lower()
        or "from evidence_nodes" in sql.lower()
        or "from execution_artifact_projections" in sql.lower()
        or "from runtime_workspace_bindings" in sql.lower()
    ]

    assert snapshot.attempts[0]["retry_budget"]["max_retries"] == 3
    assert snapshot.runtime_failures[0]["retry_budget"]["max_retries"] == 3
    assert snapshot.projection_refs[0]["status"] == "ready"
    assert snapshot.runtime_workspace_bindings == [
        {
            "id": 271,
            "sandbox_lease_id": 260,
            "attempt_id": 201,
            "runtime_name": "codex",
            "cwd": "/sandbox/feature-typed/g9/attempt-1/app",
            "workspace_override": "/sandbox/feature-typed/g9/attempt-1/app",
            "manifest_path": "/sandbox/feature-typed/g9/attempt-1/sandbox-manifest.json",
            "status": "bound",
            "role_metadata_digest": "role-metadata-digest-1",
            "binding_digest": "binding-digest-1",
            "created_at": "2026-05-19T00:00:00+00:00",
            "updated_at": "2026-05-19T00:00:00+00:00",
        }
    ]
    assert dumped["runtime_workspace_bindings"] == snapshot.runtime_workspace_bindings
    assert dumped["budgets"]["runtime_workspace_bindings"] == 1
    assert "runtime_workspace_bindings" in snapshot.query["sections"]
    assert snapshot.budgets["runtime_workspace_bindings"] == 1
    assert snapshot.truncation["runtime_workspace_bindings"] == {
        "returned": 1,
        "limit": 1,
        "truncated": True,
    }
    assert large_payload not in encoded
    assert all(" payload," not in query for query in control_plane_queries)
    assert all(", payload " not in query for query in control_plane_queries)


@pytest.mark.asyncio
async def test_control_plane_snapshot_counts_active_merge_queue_states() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    conn.typed_rows.extend([
        {
            "id": 201,
            "feature_id": FEATURE_ID,
            "idempotency_key": "idem:merge-active",
            "entry_type": "group_checkpoint",
            "status": "started",
            "dispatcher_state": "",
            "actor": "merge_queue",
            "runtime": "",
            "group_idx": 7,
            "task_id": None,
            "request_digest": "",
            "dag_sha256": DAG_SHA256,
            "payload": json.dumps({"status": "checkpointing"}),
            "requires_legacy_visibility": True,
            "projection_mode": "legacy_compatibility",
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        },
        {
            "id": 202,
            "feature_id": FEATURE_ID,
            "idempotency_key": "idem:merge-done",
            "entry_type": "group_checkpoint",
            "status": "succeeded",
            "dispatcher_state": "",
            "actor": "merge_queue",
            "runtime": "",
            "group_idx": 8,
            "task_id": None,
            "request_digest": "",
            "dag_sha256": DAG_SHA256,
            "payload": json.dumps({"status": "done"}),
            "requires_legacy_visibility": True,
            "projection_mode": "legacy_compatibility",
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        },
    ])

    snapshot = await module.fetch_control_plane_snapshot(conn, FEATURE_ID)
    journal_queries = [
        " ".join(sql.lower().split())
        for sql, _args in conn.calls
        if "from execution_journal_rows" in sql.lower()
    ]

    assert snapshot.merge_queue["pending_count"] == 1
    assert any(
        item["status"] == "checkpointing"
        for item in snapshot.merge_queue["items"]
    )
    assert "select id, entry_type, status, dispatcher_state" in journal_queries[0]
    assert "coalesce(payload->>'merge_queue_status'" not in journal_queries[0]
    assert "coalesce(payload->>'merge_queue_status'" in journal_queries[1]
    assert ") as status, dispatcher_state" in journal_queries[1]


@pytest.mark.asyncio
async def test_control_plane_snapshot_keeps_active_merge_queue_rows_outside_attempt_window() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    active_row = {
        "id": 101,
        "feature_id": FEATURE_ID,
        "idempotency_key": "idem:merge-active-old",
        "entry_type": "group_checkpoint",
        "status": "started",
        "dispatcher_state": "",
        "actor": "merge_queue",
        "runtime": "",
        "group_idx": 7,
        "task_id": None,
        "request_digest": "",
        "dag_sha256": DAG_SHA256,
        "payload": json.dumps({"status": "checkpointing"}),
        "requires_legacy_visibility": True,
        "projection_mode": "legacy_compatibility",
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    }
    conn.typed_rows.append(active_row)
    for idx in range(30):
        conn.typed_rows.append({
            "id": 300 + idx,
            "feature_id": FEATURE_ID,
            "idempotency_key": f"idem:recent-dispatch:{idx}",
            "entry_type": "dispatch_attempt",
            "status": "succeeded",
            "dispatcher_state": "finished",
            "actor": "dispatcher",
            "runtime": "codex",
            "group_idx": idx,
            "task_id": f"TASK-{idx}",
            "request_digest": "",
            "dag_sha256": DAG_SHA256,
            "payload": "{}",
            "requires_legacy_visibility": False,
            "projection_mode": "typed",
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        })

    snapshot = await module.fetch_control_plane_snapshot(
        conn,
        FEATURE_ID,
        budgets={"attempts": 1, "projection_refs": 1},
    )

    assert [attempt["id"] for attempt in snapshot.attempts] == [329]
    assert snapshot.merge_queue["pending_count"] == 1
    assert snapshot.merge_queue["items"][0]["typed_row_id"] == 101
    assert snapshot.truncation["merge_queue_active_attempts"] == {
        "returned": 1,
        "limit": 1,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_typed_success_cannot_complete_without_required_legacy_projection() -> None:
    module = _execution_control_module()
    error_type = getattr(module, "MissingRequiredProjection", RuntimeError)
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    projection = _projection(
        "GroupCheckpointProjection",
        projection_key="dag-group:8",
        artifact_key=None,
        projection_body=None,
        artifact_body=None,
        body=None,
        value=None,
        group_idx=8,
        status="done",
        source_table="merge_queue_items",
        idempotency_key="idem:checkpoint:g8",
    )

    with pytest.raises(error_type, match="dag-group:8|projection|legacy"):
        await _call_projection(store, "project_group_checkpoint", projection)

    assert conn.typed_rows == []
    assert conn.artifacts == []
    assert conn.projection_links == []


@pytest.mark.asyncio
async def test_legacy_artifact_only_resume_does_not_require_typed_rows() -> None:
    conn = _FakeConnection()
    body = json.dumps({"group_idx": 4, "status": "done"}, sort_keys=True)
    conn.artifacts.append({
        "id": 10,
        "feature_id": FEATURE_ID,
        "key": "dag-group:4",
        "value": body,
        "sha256": _sha256(body),
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    })
    store = _store(_FakePool(conn))
    method = getattr(store, "list_legacy_resume_artifacts", None) or getattr(
        store,
        "read_legacy_resume_artifacts",
        None,
    )
    if method is None:
        pytest.fail(
            "ExecutionControlStore must expose list_legacy_resume_artifacts() "
            "or read_legacy_resume_artifacts() so legacy artifact-only features "
            "remain resumable without typed rows."
        )

    records = await method(feature_id=FEATURE_ID, prefixes=("dag-group:",))

    assert conn.typed_rows == []
    assert any(record["key"] == "dag-group:4" and record["value"] == body for record in records)


@pytest.mark.asyncio
async def test_legacy_resume_artifacts_return_bounded_value_summaries() -> None:
    conn = _FakeConnection()
    body = "x" * 5000
    conn.artifacts.append({
        "id": 10,
        "feature_id": FEATURE_ID,
        "key": "dag-group:4",
        "value": body,
        "sha256": _sha256(body),
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    })
    store = _store(_FakePool(conn))

    records = await store.list_legacy_resume_artifacts(
        feature_id=FEATURE_ID,
        prefixes=("dag-group:",),
    )

    assert len(records) == 1
    assert records[0]["value"] == body[:4000]
    assert records[0]["value_preview"] == body[:4000]
    assert records[0]["value_chars"] == 5000
    assert records[0]["value_bytes"] == 5000
    assert records[0]["summary_only"] is True
    normalized_sql = " ".join(conn.calls[-1][0].lower().split())
    assert "left(value, $4)" in normalized_sql
    assert "select id, feature_id, key, value, created_at" not in normalized_sql


@pytest.mark.asyncio
async def test_projection_idempotent_retry_dedupes_and_repairs_missing_links() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    body = json.dumps({"task_id": "TASK-typed-1", "status": "done"}, sort_keys=True)
    projection = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )

    await _call_projection(store, "project_task_result", projection)
    await _call_projection(store, "project_task_result", projection)

    assert len(conn.typed_rows) == 1
    assert len(conn.artifacts) == 1
    assert len(conn.projection_links) == 1

    conn.projection_links.clear()
    await _call_projection(store, "project_task_result", projection)

    assert len(conn.typed_rows) == 1
    assert len(conn.artifacts) == 1
    assert len(conn.projection_links) == 1
    _assert_projection_link(conn, "dag-task:TASK-typed-1", body, "idem:task:TASK-typed-1")


@pytest.mark.asyncio
async def test_missing_projection_link_repair_rejects_different_projection_body() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    body = json.dumps({"task_id": "TASK-typed-1", "status": "done"}, sort_keys=True)
    projection = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )
    await _call_projection(store, "project_task_result", projection)
    conn.projection_links.clear()
    artifact_count = len(conn.artifacts)

    changed_body = json.dumps({"task_id": "TASK-typed-1", "status": "changed"}, sort_keys=True)
    changed_projection = _projection(
        "TaskResultProjection",
        projection_key="dag-task:TASK-typed-1",
        artifact_key="dag-task:TASK-typed-1",
        projection_body=changed_body,
        artifact_body=changed_body,
        body=changed_body,
        value=changed_body,
        task_id="TASK-typed-1",
        source_kind="structured_result",
        idempotency_key="idem:task:TASK-typed-1",
    )

    with pytest.raises(module.IdempotencyConflict, match="different request"):
        await _call_projection(store, "project_task_result", changed_projection)

    assert len(conn.artifacts) == artifact_count
    assert conn.projection_links == []
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_projection_link_records_lineage_fields() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    body = json.dumps({"group_idx": 9, "stage": "initial", "status": "passed"}, sort_keys=True)
    projection = _projection(
        "VerifyProjection",
        projection_key="dag-verify:g9:initial",
        artifact_key="dag-verify:g9:initial",
        projection_body=body,
        artifact_body=body,
        body=body,
        value=body,
        group_idx=9,
        stage="initial",
        source_kind="aggregate_verdict",
        idempotency_key="idem:verify:g9:initial",
    )

    await _call_projection(store, "project_verify_result", projection)

    link = _assert_projection_link(
        conn,
        "dag-verify:g9:initial",
        body,
        "idem:verify:g9:initial",
    )
    assert link["feature_id"] == FEATURE_ID
    assert isinstance(link["typed_row_id"], int)
    assert isinstance(link["artifact_id"], int)
    assert link["projection_key"] == "dag-verify:g9:initial"
    assert link["projection_sha256"] == _sha256(body)
    assert link["idempotency_key"] == "idem:verify:g9:initial"


_DEFAULT_VERIFICATION_GRAPH_PROOF = object()


def _verification_graph_payload(
    *,
    projection_key: str = "dag-verify:g9:initial",
    approved: bool = True,
    source_kind: str = "aggregate_verdict",
    proof: dict[str, Any] | None | object = _DEFAULT_VERIFICATION_GRAPH_PROOF,
) -> dict[str, Any]:
    verifier_compatibility_links = {
        "2": {
            "raw_output_verifier_node_id": 2,
            "parsed_verdict_verifier_node_id": 2,
            "projection_verifier_node_id": 2,
            "context_package_node_id": 1,
            "context_hash_matches": True,
        }
    }
    payload = {
        "schema_version": "slice06.verification_graph.v1",
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA256,
        "group_idx": 9,
        "stage": "initial",
        "projection_key": projection_key,
        "compatibility_projection": {
            "key": projection_key,
            "source_kind": source_kind,
            "visibility": "legacy_dag_verify",
        },
        "verifier_compatibility_links": verifier_compatibility_links,
        "approved": approved,
        "nodes": [
            {
                "id": 1,
                "feature_id": FEATURE_ID,
                "group_idx": 9,
                "stage": "initial",
                "kind": "deterministic_gate",
                "name": "raw_gate_approval_requirements",
                "idempotency_key": "idem:verify-graph:g9:gate",
                "status": "approved",
                "deterministic": True,
                "input_hash": "gate-input",
            },
            {
                "id": 2,
                "feature_id": FEATURE_ID,
                "group_idx": 9,
                "stage": "initial",
                "kind": "raw_verifier",
                "name": "raw_verifier",
                "idempotency_key": "idem:verify-graph:g9:raw",
                "status": "approved",
                "deterministic": False,
                "input_hash": "raw-input",
            },
            {
                "id": 3,
                "feature_id": FEATURE_ID,
                "group_idx": 9,
                "stage": "initial",
                "kind": "aggregate_verdict",
                "name": "aggregate_verdict",
                "idempotency_key": "idem:verify-graph:g9:aggregate",
                "status": "approved" if approved else "rejected",
                "deterministic": True,
                "input_hash": "aggregate-input",
            },
        ],
        "edges": [
            {"id": 1, "from_node_id": 1, "to_node_id": 3, "kind": "requires"},
            {"id": 2, "from_node_id": 2, "to_node_id": 3, "kind": "requires"},
        ],
        "aggregate": {
            "node_id": 3,
            "approved": approved,
            "raw_verdict_node_id": 2,
            "required_gate_node_ids": [1],
            "required_lens_node_ids": [],
            "merged_verdict_id": 333,
            "failure_ids": [],
            "blocking_failure_class": None,
            "verifier_compatibility_links": verifier_compatibility_links,
        },
        "aggregate_node": {
            "id": 3,
            "feature_id": FEATURE_ID,
            "group_idx": 9,
            "stage": "initial",
            "kind": "aggregate_verdict",
            "name": "aggregate_verdict",
            "idempotency_key": "idem:verify-graph:g9:aggregate",
            "status": "approved" if approved else "rejected",
            "deterministic": True,
            "input_hash": "aggregate-input",
            "metadata": {"required_lens_slugs": []},
        },
        "merged_verdict": Verdict(approved=approved, summary="graph verdict").model_dump(mode="json"),
        "proof": None if proof is _DEFAULT_VERIFICATION_GRAPH_PROOF else proof,
    }
    if proof is _DEFAULT_VERIFICATION_GRAPH_PROOF:
        payload["proof"] = _valid_verification_graph_proof(payload, projection_key)
    return payload


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _valid_verification_graph_proof(
    payload: dict[str, Any],
    projection_key: str,
    *,
    graph_payload_digest: str = "",
) -> dict[str, Any]:
    aggregate = payload["aggregate"]
    required_node_ids = sorted({
        *aggregate["required_gate_node_ids"],
        aggregate["raw_verdict_node_id"],
        *aggregate["required_lens_node_ids"],
    })
    required_edge_ids = sorted(
        int(edge["id"])
        for edge in payload.get("edges", [])
        if isinstance(edge, dict) and edge.get("id") is not None
    )
    required_status_digest = _stable_digest([
        {"node_id": node_id, "status": "approved"}
        for node_id in required_node_ids
    ])
    proof_payload_without_digests = {
        "feature_id": payload["feature_id"],
        "dag_sha256": payload["dag_sha256"],
        "group_idx": payload["group_idx"],
        "stage": payload["stage"],
        "aggregate_node_id": aggregate["node_id"],
        "aggregate_verdict_id": aggregate["merged_verdict_id"],
        "required_edge_ids": required_edge_ids,
        "required_lineage_node_ids": required_node_ids,
        "required_node_status_digest": required_status_digest,
        "raw_verifier_node_id": aggregate["raw_verdict_node_id"],
        "required_lens_node_ids": aggregate["required_lens_node_ids"],
        "projection_keys": [projection_key],
        "verifier_compatibility_links": payload.get("verifier_compatibility_links", {}),
    }
    if not graph_payload_digest:
        canonical = json.loads(json.dumps(payload, sort_keys=True, default=str))
        canonical["proof"] = dict(proof_payload_without_digests)
        graph_payload_digest = _stable_digest(canonical)
    proof_payload = {
        **proof_payload_without_digests,
        "graph_payload_digest": graph_payload_digest,
    }
    return {**proof_payload, "proof_digest": _stable_digest(proof_payload)}


def _replace_evidence_graph_payload(
    conn: _FakeConnection,
    graph_payload: dict[str, Any],
) -> None:
    conn.evidence_graphs[0]["payload"] = json.dumps(
        graph_payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    conn.evidence_graphs[0]["graph_payload_digest"] = _stable_digest(graph_payload)


@pytest.mark.asyncio
async def test_verification_graph_node_replay_is_idempotent() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    node = module.VerificationGraphNodeEvidence(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        status="approved",
        idempotency_key="idem:verify-graph:g9:workspace",
        payload={"snapshot_ids": [1]},
    )

    first = await store.record_verification_graph_node(node)
    retry = await store.record_verification_graph_node(node)

    assert first.evidence.id == retry.evidence.id
    assert first.created is True
    assert retry.created is False
    assert len(conn.evidence_nodes) == 1
    assert conn.evidence_nodes[0]["kind"] == "deterministic_gate"


@pytest.mark.asyncio
async def test_verification_graph_node_rejects_same_key_different_content() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    node = module.VerificationGraphNodeEvidence(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        status="approved",
        idempotency_key="idem:verify-graph:g9:workspace",
        payload={"snapshot_ids": [1]},
    )
    await store.record_verification_graph_node(node)
    changed = module.VerificationGraphNodeEvidence(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        status="approved",
        idempotency_key="idem:verify-graph:g9:workspace",
        payload={"snapshot_ids": [2]},
    )

    with pytest.raises(module.IdempotencyConflict, match="different request|different content"):
        await store.record_verification_graph_node(changed)


@pytest.mark.asyncio
async def test_verification_graph_projection_links_to_aggregate_evidence_node() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    body_text = json.dumps(payload["merged_verdict"], sort_keys=True)

    result = await store.record_verification_graph_projection(payload)
    retry_store = _store(_FakePool(conn))
    retry = await retry_store.record_verification_graph_projection(payload)

    assert result.row.id == retry.row.id
    assert len([node for node in conn.evidence_nodes if node["kind"] == "aggregate_verdict"]) == 1
    assert len(conn.evidence_graphs) == 1
    assert len(conn.evidence_edges) == 2
    aggregate = next(node for node in conn.evidence_nodes if node["kind"] == "aggregate_verdict")
    graph = conn.evidence_graphs[0]
    assert graph["aggregate_evidence_node_id"] == aggregate["id"]
    assert graph["projection_key"] == "dag-verify:g9:initial"
    assert graph["proof_digest"] == payload["proof"]["proof_digest"]
    assert graph["required_edge_ids"] == "[1,2]"
    edge = conn.evidence_edges[0]
    assert edge["evidence_graph_id"] == graph["id"]
    assert edge["from_evidence_node_id"] is not None
    assert edge["to_evidence_node_id"] == aggregate["id"]
    assert edge["required"] is True
    link = _assert_projection_link(
        conn,
        "dag-verify:g9:initial",
        body_text,
        (
            "idem:verification-graph-projection:feature-typed:"
            f"dag-verify:g9:initial:{payload['proof']['proof_digest']}"
        ),
        expected_source_table="evidence_nodes",
        expected_source_id=aggregate["id"],
    )
    assert link["projection_owner"] == "verification_graph"
    assert link["projection_kind"] == "verify_result"
    assert result.projection_links[0].source_table == "evidence_nodes"
    assert result.projection_links[0].source_id == aggregate["id"]
    assert result.projection_links[0].payload["evidence_graph_id"] == graph["id"]
    assert result.projection_links[0].payload["graph_payload_digest"] == graph["graph_payload_digest"]
    assert result.projection_links[0].payload["required_edge_ids"] == [1, 2]
    assert result.projection_links[0].payload["required_node_ids"] == [1, 2]
    assert result.projection_links[0].payload["verifier_compatibility_links"]["2"] == {
        "raw_output_verifier_node_id": 2,
        "parsed_verdict_verifier_node_id": 2,
        "projection_verifier_node_id": 2,
        "context_package_node_id": 1,
        "context_hash_matches": True,
    }
    graph_payload = json.loads(graph["payload"])
    assert graph_payload["proof"] == payload["proof"]
    assert graph_payload["nodes"] == payload["nodes"]
    assert graph_payload["edges"] == payload["edges"]
    assert graph_payload["aggregate"] == payload["aggregate"]
    assert graph_payload["aggregate_node"] == payload["aggregate_node"]
    assert graph_payload["verifier_compatibility_links"]["2"][
        "projection_verifier_node_id"
    ] == 2
    listed = await store.list_verification_graph_nodes(
        FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
    )
    assert [node.kind for node in listed] == [
        "deterministic_gate",
        "raw_verifier",
        "aggregate_verdict",
    ]


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_forged_proof_digest() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    payload["proof"] = {**payload["proof"], "proof_digest": "0" * 64}

    with pytest.raises(module.MissingRequiredProjection, match="proof digest"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_stale_lineage() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    payload["nodes"][1]["feature_id"] = "feature-transplanted"

    with pytest.raises(module.MissingRequiredProjection, match="stale feature_id lineage"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed_id", ["aggregate-three", ["aggregate-three"]])
async def test_verification_graph_projection_rejects_malformed_aggregate_node_id(
    malformed_id: Any,
) -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    payload["aggregate_node"]["id"] = malformed_id
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    with pytest.raises(
        module.MissingRequiredProjection,
        match="aggregate node id must be an integer",
    ):
        await store.record_verification_graph_projection(payload)

    assert conn.typed_rows == []
    assert conn.evidence_nodes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed_group_idx", ["group-nine", {"group": 9}])
async def test_verification_graph_projection_rejects_malformed_group_idx(
    malformed_group_idx: Any,
) -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    payload["group_idx"] = malformed_group_idx

    with pytest.raises(
        module.MissingRequiredProjection,
        match="group_idx must be an integer",
    ):
        await store.record_verification_graph_projection(payload)

    assert conn.typed_rows == []
    assert conn.evidence_nodes == []


@pytest.mark.asyncio
async def test_verification_graph_projection_ignores_scalar_node_payloads() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload(proof=None)
    payload["nodes"].insert(1, "not-a-node")
    payload["proof"] = _valid_verification_graph_proof(
        payload,
        "dag-verify:g9:initial",
    )

    await store.record_verification_graph_projection(payload)

    assert len(conn.evidence_nodes) == 3
    assert {node["kind"] for node in conn.evidence_nodes} == {
        "aggregate_verdict",
        "deterministic_gate",
        "raw_verifier",
    }


@pytest.mark.asyncio
async def test_verification_graph_projection_handles_scalar_nodes_container() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload(proof=None)
    payload["nodes"] = 123
    payload["proof"] = _valid_verification_graph_proof(
        payload,
        "dag-verify:g9:initial",
    )

    with pytest.raises(module.MissingRequiredProjection, match="missing required nodes"):
        await store.record_verification_graph_projection(payload)

    assert conn.typed_rows == []
    assert conn.evidence_nodes == []


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_missing_required_node() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != 2]

    with pytest.raises(module.MissingRequiredProjection, match="missing required nodes"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_non_approved_required_node() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    payload["nodes"][1]["status"] = "rejected"

    with pytest.raises(module.MissingRequiredProjection, match="non-approved required nodes|status digest"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role_patch", "match"),
    [
        ("raw_verdict_node_id", "raw_verdict_node_id"),
        ("required_lens_node_ids", "required_lens_node_id"),
        ("required_gate_node_ids", "required_gate_node_id"),
    ],
)
async def test_verification_graph_projection_rejects_required_node_role_mismatch(
    role_patch: str,
    match: str,
) -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload(proof=None)
    if role_patch == "raw_verdict_node_id":
        payload["aggregate"]["raw_verdict_node_id"] = 1
    elif role_patch == "required_lens_node_ids":
        payload["aggregate"]["required_lens_node_ids"] = [1]
    else:
        payload["aggregate"]["required_gate_node_ids"] = [2]
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    with pytest.raises(module.MissingRequiredProjection, match=match):
        await store.record_verification_graph_projection(payload)

    assert conn.typed_rows == []
    assert conn.evidence_nodes == []


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_missing_required_edge_row() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    payload["edges"] = []

    with pytest.raises(
        module.MissingRequiredProjection,
        match="required edge|graph payload digest",
    ):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_invalid_required_edge_lineage() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    payload["edges"][0] = {
        "id": 1,
        "from_node_id": 2,
        "to_node_id": 1,
        "kind": "related",
    }
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    with pytest.raises(
        module.MissingRequiredProjection,
        match="required edges do not match graph payload|invalid required edge lineage",
    ):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_proof_that_omits_payload_required_edge() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    proof = dict(payload["proof"])
    proof["required_edge_ids"] = [1]
    proof.pop("graph_payload_digest", None)
    proof.pop("proof_digest", None)
    payload["proof"] = dict(proof)
    proof["graph_payload_digest"] = _stable_digest(payload)
    proof_payload = dict(proof)
    proof_payload.pop("proof_digest", None)
    proof["proof_digest"] = _stable_digest(proof_payload)
    payload["proof"] = proof

    with pytest.raises(
        module.MissingRequiredProjection,
        match="required edges do not match graph payload",
    ):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_missing_verifier_compatibility_links() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload(proof=None)
    payload.pop("verifier_compatibility_links")
    payload["aggregate"].pop("verifier_compatibility_links")
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    with pytest.raises(module.MissingRequiredProjection, match="verifier compatibility links"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_cyclic_required_edges() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload(proof=None)
    payload["edges"].extend([
        {"id": 3, "from_node_id": 1, "to_node_id": 2, "kind": "requires"},
        {"id": 4, "from_node_id": 2, "to_node_id": 1, "kind": "requires"},
    ])
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    with pytest.raises(module.MissingRequiredProjection, match="cyclic required edges"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_missing_required_node_reachability() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload(proof=None)
    payload["edges"] = [edge for edge in payload["edges"] if edge["id"] != 2]
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    with pytest.raises(module.MissingRequiredProjection, match="required-node reachability"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_verification_graph_projection_requires_graph_payload_digest() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload()
    proof = dict(payload["proof"])
    proof["graph_payload_digest"] = ""
    proof_payload = dict(proof)
    proof_payload.pop("proof_digest", None)
    proof["proof_digest"] = _stable_digest(proof_payload)
    payload["proof"] = proof

    with pytest.raises(module.MissingRequiredProjection, match="graph payload digest"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_get_verified_verification_graph_projection_reloads_durable_metadata() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    conn.artifacts.clear()
    recovery_store = _store(_FakePool(conn))

    reloaded = await recovery_store.get_verified_verification_graph_projection(
        feature_id=FEATURE_ID,
        projection_key="dag-verify:g9:initial",
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
        proof_digest=payload["proof"]["proof_digest"],
    )

    assert reloaded is not None
    assert reloaded["graph"]["projection_key"] == "dag-verify:g9:initial"
    assert reloaded["graph"]["proof_digest"] == payload["proof"]["proof_digest"]
    assert reloaded["required_edges"][0]["graph_edge_id"] == "1"
    assert reloaded["projection_links"][0]["projection_owner"] == "verification_graph"
    assert reloaded["projection_links"][0]["payload"]["evidence_graph_id"] == conn.evidence_graphs[0]["id"]
    assert reloaded["graph"]["payload"]["proof"] == payload["proof"]
    assert reloaded["graph"]["payload"]["nodes"] == payload["nodes"]
    assert reloaded["graph"]["payload"]["edges"] == payload["edges"]
    assert reloaded["graph"]["payload"]["aggregate"] == payload["aggregate"]
    assert reloaded["graph"]["payload"]["aggregate_node"] == payload["aggregate_node"]


@pytest.mark.asyncio
async def test_get_latest_verified_verification_graph_projection_recovers_after_artifact_gap() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    conn.artifacts.clear()
    recovery_store = _store(_FakePool(conn))

    reloaded = await recovery_store.get_latest_verified_verification_graph_projection(
        feature_id=FEATURE_ID,
        projection_key="dag-verify:g9:initial",
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
    )

    assert reloaded is not None
    assert reloaded["graph"]["proof_digest"] == payload["proof"]["proof_digest"]
    assert reloaded["graph"]["payload"]["projection_key"] == "dag-verify:g9:initial"
    assert reloaded["graph"]["payload"]["proof"] == payload["proof"]
    assert reloaded["graph"]["payload"]["nodes"] == payload["nodes"]
    assert reloaded["graph"]["payload"]["edges"] == payload["edges"]
    assert reloaded["graph"]["payload"]["aggregate"] == payload["aggregate"]
    assert reloaded["graph"]["payload"]["aggregate_node"] == payload["aggregate_node"]
    assert reloaded["projection_links"][0]["payload"]["proof_digest"] == payload["proof"]["proof_digest"]


@pytest.mark.asyncio
async def test_workflow_generated_verification_graph_payload_persists_in_real_store() -> None:
    from iriai_build_v2.workflows.develop.phases import implementation as implementation_module

    class _Artifacts:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    feature = SimpleNamespace(id=FEATURE_ID, slug="feature-typed")
    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={"execution_control_store": store},
    )

    await implementation_module._put_dag_verify_artifact(
        runner,
        feature,
        "dag-verify:g9:initial",
        Verdict(approved=True, summary="workflow graph approved"),
        group_idx=9,
        dag_sha256=DAG_SHA256,
        tasks=[
            implementation_module.ImplementationTask(
                id="TASK-typed-1",
                name="Task",
                description="Task",
            )
        ],
        results=[
            ImplementationResult(
                task_id="TASK-typed-1",
                summary="done",
            )
        ],
        required_lens_slugs=[],
    )

    graph_payload = json.loads(runner.artifacts.store["dag-verify-graph:g9:initial"])
    assert graph_payload["durable_projection"]["persisted"] is True
    assert "dag-verify:g9:initial" not in runner.artifacts.store
    reloaded = await store.get_latest_verified_verification_graph_projection(
        feature_id=FEATURE_ID,
        projection_key="dag-verify:g9:initial",
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
    )

    assert reloaded is not None
    assert reloaded["graph"]["payload"]["lineage"] == graph_payload["lineage"]
    assert reloaded["graph"]["payload"]["proof"] == graph_payload["proof"]
    assert reloaded["graph"]["payload"]["nodes"] == graph_payload["nodes"]
    assert reloaded["graph"]["payload"]["edges"] == graph_payload["edges"]
    assert (
        reloaded["graph"]["payload"]["verifier_compatibility_links"]
        == graph_payload["verifier_compatibility_links"]
    )
    assert (
        reloaded["graph"]["payload"]["aggregate"]["verifier_compatibility_links"]
        == graph_payload["aggregate"]["verifier_compatibility_links"]
    )
    assert reloaded["projection_links"][0]["payload"]["proof_digest"] == graph_payload["proof"]["proof_digest"]


@pytest.mark.asyncio
async def test_get_latest_verified_verification_graph_projection_recovers_failed_raw_without_proof_after_artifact_gap() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload(approved=False, proof=None)
    payload["nodes"][1]["status"] = "failed"
    payload["aggregate"]["failure_ids"] = [44]
    payload["aggregate"]["blocking_failure_class"] = "verifier_provider"
    payload["aggregate_node"]["status"] = "rejected"
    payload["merged_verdict"] = Verdict(
        approved=False,
        summary="raw verifier failed before product repair",
    ).model_dump(mode="json")

    await store.record_verification_graph_projection(payload)
    conn.artifacts.clear()
    recovery_store = _store(_FakePool(conn))

    reloaded = await recovery_store.get_latest_verified_verification_graph_projection(
        feature_id=FEATURE_ID,
        projection_key="dag-verify:g9:initial",
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
    )

    assert reloaded is not None
    assert reloaded["graph"]["proof_digest"] == ""
    assert reloaded["graph"]["payload"]["proof"] is None
    assert reloaded["graph"]["payload"]["nodes"][1]["status"] == "failed"
    assert reloaded["graph"]["payload"]["aggregate"]["blocking_failure_class"] == "verifier_provider"
    assert "failed" in set(reloaded["required_node_statuses"].values())
    assert reloaded["projection_links"][0]["projection_owner"] == "verification_graph"
    assert reloaded["projection_links"][0]["payload"]["proof_digest"] == ""


@pytest.mark.asyncio
async def test_get_latest_verified_verification_graph_projection_recovers_proofless_preflight_without_raw_verifier_after_artifact_gap() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload(approved=False, proof=None)
    payload["nodes"] = [
        {**node, "status": "rejected"} if node["id"] in {1, 3} else node
        for node in payload["nodes"]
        if node["kind"] != "raw_verifier"
    ]
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge["from_node_id"] != 2 and edge["to_node_id"] != 2
    ]
    payload["aggregate"]["raw_verdict_node_id"] = None
    payload["aggregate"]["failure_ids"] = [41]
    payload["aggregate"]["blocking_failure_class"] = "deterministic_gate"
    payload["aggregate"]["verifier_compatibility_links"] = {}
    payload["aggregate_node"]["status"] = "rejected"
    payload["verifier_compatibility_links"] = {}
    payload["merged_verdict"] = Verdict(
        approved=False,
        summary="programmatic preflight failed before raw verifier dispatch",
    ).model_dump(mode="json")

    await store.record_verification_graph_projection(payload)
    conn.artifacts.clear()
    recovery_store = _store(_FakePool(conn))

    reloaded = await recovery_store.get_latest_verified_verification_graph_projection(
        feature_id=FEATURE_ID,
        projection_key="dag-verify:g9:initial",
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
    )

    assert reloaded is not None
    assert reloaded["graph"]["proof_digest"] == ""
    assert reloaded["graph"]["payload"]["aggregate"]["raw_verdict_node_id"] is None
    assert reloaded["graph"]["payload"]["aggregate"]["blocking_failure_class"] == "deterministic_gate"
    assert not any(
        node["kind"] == "raw_verifier"
        for node in reloaded["graph"]["payload"]["nodes"]
    )
    assert "rejected" in set(reloaded["required_node_statuses"].values())
    assert reloaded["projection_links"][0]["payload"]["proof_digest"] == ""


@pytest.mark.asyncio
async def test_latest_proofless_verification_graph_reload_rejects_payload_digest_mismatch() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload(approved=False, proof=None)
    payload["nodes"][1]["status"] = "failed"
    payload["aggregate"]["failure_ids"] = [44]
    payload["aggregate"]["blocking_failure_class"] = "verifier_provider"
    payload["aggregate_node"]["status"] = "rejected"
    payload["merged_verdict"] = Verdict(
        approved=False,
        summary="raw verifier failed before product repair",
    ).model_dump(mode="json")

    await store.record_verification_graph_projection(payload)
    corrupted = deepcopy(payload)
    corrupted["nodes"][1]["status"] = "approved"
    conn.evidence_graphs[0]["payload"] = json.dumps(
        corrupted,
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.artifacts.clear()
    recovery_store = _store(_FakePool(conn))

    with pytest.raises(
        module.MissingRequiredProjection,
        match="graph payload digest mismatch",
    ):
        await recovery_store.get_latest_verified_verification_graph_projection(
            feature_id=FEATURE_ID,
            projection_key="dag-verify:g9:initial",
            dag_sha256=DAG_SHA256,
            group_idx=9,
            stage="initial",
        )


@pytest.mark.asyncio
async def test_verified_verification_graph_reload_rejects_extra_required_edges() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    extra = deepcopy(conn.evidence_edges[0])
    extra["id"] = conn.evidence_edges[0]["id"] + 100
    extra["graph_edge_id"] = "999"
    extra["idempotency_key"] = "idem:extra-required-edge"
    conn.evidence_edges.append(extra)

    with pytest.raises(module.MissingRequiredProjection, match="extra=\\['999'\\]"):
        await store.get_verified_verification_graph_projection(
            feature_id=FEATURE_ID,
            projection_key="dag-verify:g9:initial",
            dag_sha256=DAG_SHA256,
            group_idx=9,
            stage="initial",
            proof_digest=payload["proof"]["proof_digest"],
        )


@pytest.mark.asyncio
async def test_verified_verification_graph_reload_rejects_missing_required_node_reachability() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    conn.evidence_graphs[0]["required_edge_ids"] = "[1]"
    conn.evidence_edges = [
        edge for edge in conn.evidence_edges
        if edge["graph_edge_id"] == "1"
    ]

    with pytest.raises(module.MissingRequiredProjection, match="required-node reachability"):
        await store.get_verified_verification_graph_projection(
            feature_id=FEATURE_ID,
            projection_key="dag-verify:g9:initial",
            dag_sha256=DAG_SHA256,
            group_idx=9,
            stage="initial",
            proof_digest=payload["proof"]["proof_digest"],
        )


@pytest.mark.asyncio
async def test_verified_verification_graph_reload_rejects_missing_compatibility_links() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    graph_payload = json.loads(conn.evidence_graphs[0]["payload"])
    graph_payload.pop("verifier_compatibility_links")
    graph_payload["aggregate"].pop("verifier_compatibility_links")
    _replace_evidence_graph_payload(conn, graph_payload)

    with pytest.raises(module.MissingRequiredProjection, match="verifier compatibility links"):
        await store.get_verified_verification_graph_projection(
            feature_id=FEATURE_ID,
            projection_key="dag-verify:g9:initial",
            dag_sha256=DAG_SHA256,
            group_idx=9,
            stage="initial",
            proof_digest=payload["proof"]["proof_digest"],
        )


@pytest.mark.asyncio
async def test_verified_verification_graph_reload_rejects_cyclic_required_edges() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    gate_edge = next(edge for edge in conn.evidence_edges if edge["graph_edge_id"] == "1")
    raw_edge = next(edge for edge in conn.evidence_edges if edge["graph_edge_id"] == "2")
    conn.evidence_edges.extend([
        {
            **deepcopy(gate_edge),
            "id": gate_edge["id"] + 100,
            "idempotency_key": "idem:cycle-edge:3",
            "graph_edge_id": "3",
            "from_graph_node_id": "1",
            "to_graph_node_id": "2",
            "from_evidence_node_id": gate_edge["from_evidence_node_id"],
            "to_evidence_node_id": raw_edge["from_evidence_node_id"],
            "edge_digest": "cycle-edge-3",
        },
        {
            **deepcopy(raw_edge),
            "id": raw_edge["id"] + 100,
            "idempotency_key": "idem:cycle-edge:4",
            "graph_edge_id": "4",
            "from_graph_node_id": "2",
            "to_graph_node_id": "1",
            "from_evidence_node_id": raw_edge["from_evidence_node_id"],
            "to_evidence_node_id": gate_edge["from_evidence_node_id"],
            "edge_digest": "cycle-edge-4",
        },
    ])
    conn.evidence_graphs[0]["required_edge_ids"] = "[1,2,3,4]"

    with pytest.raises(module.MissingRequiredProjection, match="cyclic required edges"):
        await store.get_verified_verification_graph_projection(
            feature_id=FEATURE_ID,
            projection_key="dag-verify:g9:initial",
            dag_sha256=DAG_SHA256,
            group_idx=9,
            stage="initial",
            proof_digest=payload["proof"]["proof_digest"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("durable_patch", "match"),
    [
        ("raw_verdict_node_id", "raw_verdict_node_id"),
        ("required_lens_node_ids", "required_lens_node_id"),
    ],
)
async def test_verified_verification_graph_reload_rejects_required_node_role_mismatch(
    durable_patch: str,
    match: str,
) -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    graph_payload = json.loads(conn.evidence_graphs[0]["payload"])
    if durable_patch == "raw_verdict_node_id":
        graph_payload["aggregate"]["raw_verdict_node_id"] = 1
    else:
        graph_payload["aggregate"]["required_lens_node_ids"] = [1]
    _replace_evidence_graph_payload(conn, graph_payload)

    with pytest.raises(module.MissingRequiredProjection, match=match):
        await store.get_verified_verification_graph_projection(
            feature_id=FEATURE_ID,
            projection_key="dag-verify:g9:initial",
            dag_sha256=DAG_SHA256,
            group_idx=9,
            stage="initial",
            proof_digest=payload["proof"]["proof_digest"],
        )


@pytest.mark.asyncio
async def test_verification_graph_projection_rejects_same_proof_changed_graph_payload() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    await store.record_verification_graph_projection(payload)
    changed = deepcopy(payload)
    changed["edges"] = [
        *changed["edges"],
        {"id": 4, "from_node_id": 2, "to_node_id": 3, "kind": "requires"},
    ]

    with pytest.raises(module.MissingRequiredProjection, match="graph payload digest"):
        await store.record_verification_graph_projection(changed)

    assert len([row for row in conn.typed_rows if row["entry_type"] == "verify_result"]) == 1
    assert len(conn.evidence_graphs) == 1
    assert len(conn.evidence_edges) == 2


@pytest.mark.asyncio
async def test_verification_graph_projection_does_not_fk_synthetic_verdict_ids() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _verification_graph_payload()
    payload["nodes"][1]["verdict_id"] = 999999
    payload["aggregate_node"]["verdict_id"] = 333
    payload["proof"] = _valid_verification_graph_proof(payload, "dag-verify:g9:initial")

    await store.record_verification_graph_projection(payload)

    raw = next(node for node in conn.evidence_nodes if node["kind"] == "raw_verifier")
    aggregate = next(node for node in conn.evidence_nodes if node["kind"] == "aggregate_verdict")
    assert raw["verdict_id"] is None
    assert aggregate["verdict_id"] is None
    assert json.loads(raw["payload"])["graph_node"]["verdict_id"] == 999999
    assert json.loads(aggregate["payload"])["graph_node"]["verdict_id"] == 333


@pytest.mark.asyncio
async def test_list_verification_graph_nodes_is_bounded_and_cursored_without_payloads() -> None:
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    for idx in range(250):
        conn.evidence_nodes.append({
            "id": idx + 1,
            "feature_id": FEATURE_ID,
            "idempotency_key": f"idem:node:{idx}",
            "execution_journal_row_id": 1,
            "attempt_id": None,
            "contract_id": None,
            "snapshot_id": None,
            "group_idx": 9,
            "stage": "initial",
            "kind": "deterministic_gate",
            "name": f"gate-{idx}",
            "status": "approved",
            "deterministic": True,
            "source_ref": "verification_graph",
            "artifact_id": None,
            "artifact_key": "",
            "event_id": None,
            "input_refs": "[]",
            "output_refs": "[]",
            "failure_id": None,
            "verdict_id": None,
            "content_hash": f"hash-{idx}",
            "summary": "",
            "metadata": "{}",
            "payload": json.dumps({"large": "x" * 1000, "dag_sha256": DAG_SHA256}),
            "started_at": None,
            "finished_at": None,
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        })

    first_page = await store.list_verification_graph_nodes(
        FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
        limit=1000,
    )
    next_page = await store.list_verification_graph_nodes(
        FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=9,
        stage="initial",
        after_id=first_page[-1].id,
        limit=10,
    )

    assert len(first_page) == 200
    assert first_page[0].id == 1
    assert first_page[-1].id == 200
    assert all(node.payload == {} for node in first_page)
    assert [node.id for node in next_page] == list(range(201, 211))


@pytest.mark.asyncio
async def test_raw_only_verify_projection_without_aggregate_proof_is_rejected() -> None:
    module = _execution_control_module()
    store = _store(_FakePool(_FakeConnection()))
    payload = _verification_graph_payload(source_kind="raw_verifier", proof=None)

    with pytest.raises(module.MissingRequiredProjection, match="aggregate_verdict|proof"):
        await store.record_verification_graph_projection(payload)


@pytest.mark.asyncio
async def test_workspace_registry_projection_is_idempotent() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = {
        "feature_id": FEATURE_ID,
        "group_idx": 45,
        "registry_digest": "registry-digest-1",
        "repos": [{"repo_id": "app", "canonical_path": "/workspace/app"}],
    }
    evidence = module.WorkspaceRegistryEvidence(
        feature_id=FEATURE_ID,
        idempotency_key="idem:workspace-registry:g45",
        artifact_key="worktree-registry:g45",
        registry_digest="registry-digest-1",
        group_idx=45,
        payload=payload,
    )

    await store.record_workspace_registry(evidence)
    await store.record_workspace_registry(evidence)

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert len(conn.typed_rows) == 1
    assert len(conn.artifacts) == 1
    assert conn.artifacts[0]["key"] == "worktree-registry:g45"
    assert conn.artifacts[0]["value"] == body
    link = _assert_projection_link(
        conn,
        "worktree-registry:g45",
        body,
        "idem:workspace-registry:g45:projection",
    )
    assert link["source_table"] == "execution_journal_rows"


@pytest.mark.asyncio
async def test_workspace_preflight_rejects_stale_registry_digest() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    evidence = module.WorkspacePreflightEvidence(
        feature_id=FEATURE_ID,
        idempotency_key="idem:workspace-preflight:g45",
        artifact_key="dag-worktree-alias-preflight:g45:initial-dispatch",
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="initial-dispatch",
        registry_digest="registry-digest-new",
        payload={
            "approved": False,
            "registry_digest": "registry-digest-old",
            "blockers": [{"reason": "stale_registry"}],
        },
    )

    with pytest.raises(module.IdempotencyConflict, match="registry_digest mismatch"):
        await store.record_workspace_preflight(evidence)

    assert conn.typed_rows == []
    assert conn.artifacts == []
    assert conn.projection_links == []


@pytest.mark.asyncio
async def test_workspace_preflight_projection_body_is_bounded() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = {
        "approved": True,
        "registry_digest": "registry-digest-1",
        "resolutions": [
            {"path": f"app/generated/file_{idx}.py", "reason": "ok"}
            for idx in range(60)
        ],
    }
    evidence = module.WorkspacePreflightEvidence(
        feature_id=FEATURE_ID,
        idempotency_key="idem:workspace-preflight:g45:bounded",
        artifact_key="dag-worktree-alias-preflight:g45:initial-dispatch",
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="initial-dispatch",
        registry_digest="registry-digest-1",
        payload=payload,
    )

    await store.record_workspace_preflight(evidence)

    body = json.loads(conn.artifacts[0]["value"])
    assert len(body["resolutions"]) == 51
    assert body["resolutions"][-1] == {"bounded": "list_truncated", "omitted": 10}


@pytest.mark.asyncio
async def test_workspace_snapshot_projection_links_to_snapshot_row() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _workspace_snapshot_payload(captured_at="2026-05-19T12:00:00+00:00")
    evidence = module.WorkspaceSnapshotEvidence(
        feature_id=FEATURE_ID,
        payload=payload,
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="retry-0",
        repo_id="app",
        canonical_path="/workspace/app",
        registry_digest="registry-digest-1",
        head_sha="abc123",
        index_digest="index-digest-1",
        worktree_status_digest="status-digest-1",
    )

    result = await store.record_workspace_snapshot(evidence)

    assert len(conn.workspace_snapshots) == 1
    assert result.snapshot.id == conn.workspace_snapshots[0]["id"]
    assert result.snapshot.idempotency_key == evidence.stable_idempotency_key
    body = _workspace_snapshot_artifact_body(payload)
    assert len(conn.artifacts) == 1
    assert conn.artifacts[0]["key"] == evidence.projection_key
    link = _assert_projection_link(
        conn,
        evidence.projection_key,
        body,
        f"{evidence.stable_idempotency_key}:projection",
        expected_source_table="workspace_snapshots",
        expected_source_id=result.snapshot.id,
    )
    assert link["projection_owner"] == "workspace_authority"
    assert link["projection_kind"] == "workspace_snapshot"


@pytest.mark.asyncio
async def test_workspace_snapshot_retry_ignores_volatile_capture_times() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first = module.WorkspaceSnapshotEvidence(
        feature_id=FEATURE_ID,
        payload=_workspace_snapshot_payload(captured_at="2026-05-19T12:00:00+00:00"),
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="retry-0",
        repo_id="app",
        canonical_path="/workspace/app",
        registry_digest="registry-digest-1",
        head_sha="abc123",
        index_digest="index-digest-1",
        worktree_status_digest="status-digest-1",
    )
    retry = module.WorkspaceSnapshotEvidence(
        feature_id=FEATURE_ID,
        payload=_workspace_snapshot_payload(captured_at="2026-05-19T12:00:05+00:00"),
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="retry-0",
        repo_id="app",
        canonical_path="/workspace/app",
        registry_digest="registry-digest-1",
        head_sha="abc123",
        index_digest="index-digest-1",
        worktree_status_digest="status-digest-1",
    )

    assert first.stable_idempotency_key == retry.stable_idempotency_key
    await store.record_workspace_snapshot(first)
    await store.record_workspace_snapshot(retry)

    assert len(conn.typed_rows) == 1
    assert len(conn.workspace_snapshots) == 1
    assert len(conn.artifacts) == 1
    assert len(conn.projection_links) == 1


@pytest.mark.asyncio
async def test_workspace_snapshot_idempotency_uses_payload_fallbacks_and_bounds_projection() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    payload = _workspace_snapshot_payload(
        dirty_paths=[f"src/generated_{idx}.py" for idx in range(60)],
    )
    evidence = module.WorkspaceSnapshotEvidence(
        feature_id=FEATURE_ID,
        payload=payload,
        registry_digest="registry-digest-1",
    )

    assert "g45:retry-0:app:abc123:index-digest-1:status-digest-1" in (
        evidence.stable_idempotency_key
    )
    await store.record_workspace_snapshot(evidence)

    body = json.loads(conn.artifacts[0]["value"])
    assert len(body["dirty_paths"]) == 51
    assert body["dirty_paths"][-1] == {"bounded": "list_truncated", "omitted": 10}


@pytest.mark.asyncio
async def test_workspace_snapshot_idempotency_rejects_digest_mismatch() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first = module.WorkspaceSnapshotEvidence(
        feature_id=FEATURE_ID,
        payload=_workspace_snapshot_payload(dirty_paths=[]),
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="retry-0",
        repo_id="app",
        canonical_path="/workspace/app",
        registry_digest="registry-digest-1",
        head_sha="abc123",
        index_digest="index-digest-1",
        worktree_status_digest="status-digest-1",
    )
    conflicting = module.WorkspaceSnapshotEvidence(
        feature_id=FEATURE_ID,
        payload=_workspace_snapshot_payload(dirty_paths=["src/app.py"]),
        dag_sha256=DAG_SHA256,
        group_idx=45,
        stage="retry-0",
        repo_id="app",
        canonical_path="/workspace/app",
        registry_digest="registry-digest-1",
        head_sha="abc123",
        index_digest="index-digest-1",
        worktree_status_digest="status-digest-1",
    )

    await store.record_workspace_snapshot(first)
    with pytest.raises(module.IdempotencyConflict, match="different request|different snapshot"):
        await store.record_workspace_snapshot(conflicting)

    assert len(conn.workspace_snapshots) == 1
    assert len(conn.artifacts) == 1
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_sandbox_lease_allocation_replays_and_repairs_manifest_projection() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    lease = _sandbox_lease(module, blocked_roots=[f"/workspace/blocked-{idx}" for idx in range(60)])
    repo = _sandbox_repo_binding(module)

    first = await store.allocate_sandbox_lease(lease, repo_bindings=(repo,))
    retry = await store.allocate_sandbox_lease(lease, repo_bindings=(repo,))

    assert first.lease.id == retry.lease.id
    assert first.repo_bindings[0].id == retry.repo_bindings[0].id
    assert len(conn.typed_rows) == 1
    assert len(conn.sandbox_leases) == 1
    assert len(conn.sandbox_repo_bindings) == 1
    assert len(conn.artifacts) == 1
    body = json.loads(conn.artifacts[0]["value"])
    assert body["sandbox_lease_id"] == first.lease.id
    assert body["repo_roots"] == {"app": "/sandbox/feature-typed/g7/attempt-2/app"}
    assert len(body["blocked_roots"]) == 51
    assert body["blocked_roots"][-1] == {"bounded": "list_truncated", "omitted": 10}
    _assert_projection_link(
        conn,
        "dag-sandbox:g7:attempt-2",
        conn.artifacts[0]["value"],
        f"{lease.stable_idempotency_key}:projection",
        expected_source_table="sandbox_leases",
        expected_source_id=first.lease.id,
    )

    conn.projection_links.clear()
    repaired = await store.allocate_sandbox_lease(lease, repo_bindings=(repo,))

    assert repaired.lease.id == first.lease.id
    assert len(conn.artifacts) == 1
    assert len(conn.projection_links) == 1


@pytest.mark.asyncio
async def test_sandbox_lease_lookup_and_lifecycle_status_update() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    lease = _sandbox_lease(module)

    allocated = await store.allocate_sandbox_lease(
        lease,
        repo_bindings=(_sandbox_repo_binding(module),),
    )
    fetched = await store.get_sandbox_lease_by_idempotency_key(
        FEATURE_ID,
        allocated.lease.idempotency_key,
    )
    assert fetched is not None
    assert fetched.id == allocated.lease.id
    assert hasattr(fetched, "sandbox_root")
    assert not isinstance(fetched, dict)

    updated = await store.update_sandbox_lease(
        _sandbox_lease(
            module,
            id=allocated.lease.id,
            idempotency_key=allocated.lease.idempotency_key,
            status="captured",
            patch_summary_ids=[285],
        )
    )

    assert updated.id == allocated.lease.id
    assert updated.status == "captured"
    assert updated.patch_summary_ids == [285]
    assert updated.lease_version == allocated.lease.lease_version + 1


@pytest.mark.asyncio
async def test_sandbox_lease_status_update_edits_typed_row_in_place() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    allocated = await store.allocate_sandbox_lease(
        _sandbox_lease(module),
        repo_bindings=(_sandbox_repo_binding(module),),
    )

    updated = await store.update_sandbox_lease(
        _sandbox_lease(
            module,
            id=allocated.lease.id,
            idempotency_key=allocated.lease.idempotency_key,
            status="retained",
            lease_version=allocated.lease.lease_version,
        )
    )

    # Lease status transition contract: a status update edits the existing
    # typed lease row in place rather than inserting a second typed row. The
    # full-dict snapshot-outbox assertions that previously lived here
    # described a writer that was never built (Slice 10g-1 journal); the
    # doc-10-correct SUMMARY-only projection is covered by the dedicated
    # test_project_control_plane_snapshot_changed_* suite below.
    assert conn.sandbox_leases[0]["status"] == "retained"
    assert conn.sandbox_leases[0]["lease_version"] == updated.lease_version
    assert not conn.typed_rows[1:]


@pytest.mark.asyncio
async def test_sandbox_lease_update_rejects_terminal_resurrection() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    allocated = await store.allocate_sandbox_lease(
        _sandbox_lease(module),
        repo_bindings=(_sandbox_repo_binding(module),),
    )
    released = await store.update_sandbox_lease(
        _sandbox_lease(
            module,
            id=allocated.lease.id,
            idempotency_key=allocated.lease.idempotency_key,
            status="released",
            lease_version=allocated.lease.lease_version,
        )
    )

    with pytest.raises(
        module.ExecutionControlError,
        match="terminal sandbox lease cannot transition back to active",
    ):
        await store.update_sandbox_lease(
            _sandbox_lease(
                module,
                id=allocated.lease.id,
                idempotency_key=allocated.lease.idempotency_key,
                status="running",
                lease_version=released.lease_version,
            )
        )

    assert conn.sandbox_leases[0]["status"] == "released"
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_sandbox_lease_allocation_rejects_terminal_replay() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    lease = _sandbox_lease(module)
    allocated = await store.allocate_sandbox_lease(
        lease,
        repo_bindings=(_sandbox_repo_binding(module),),
    )
    await store.update_sandbox_lease(
        _sandbox_lease(
            module,
            id=allocated.lease.id,
            idempotency_key=allocated.lease.idempotency_key,
            status="released",
            lease_version=allocated.lease.lease_version,
        )
    )

    with pytest.raises(module.IdempotencyConflict, match="terminal sandbox lease"):
        await store.allocate_sandbox_lease(lease, repo_bindings=(_sandbox_repo_binding(module),))

    assert len(conn.sandbox_leases) == 1
    assert conn.sandbox_leases[0]["status"] == "released"
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_sandbox_lease_allocation_rejects_terminal_scope_with_new_key() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first = _sandbox_lease(module, idempotency_key="idem:sandbox:first")
    allocated = await store.allocate_sandbox_lease(
        first,
        repo_bindings=(_sandbox_repo_binding(module),),
    )
    await store.update_sandbox_lease(
        _sandbox_lease(
            module,
            id=allocated.lease.id,
            idempotency_key=allocated.lease.idempotency_key,
            status="failed",
            lease_version=allocated.lease.lease_version,
        )
    )
    second = _sandbox_lease(
        module,
        idempotency_key="idem:sandbox:second",
        sandbox_root="/sandbox/feature-typed/g7/attempt-2-retry",
        sandbox_id="sandbox-456",
        manifest_path="/sandbox/feature-typed/g7/attempt-2-retry/sandbox-manifest.json",
    )

    with pytest.raises(module.IdempotencyConflict, match="terminal sandbox lease scope"):
        await store.allocate_sandbox_lease(
            second,
            repo_bindings=(_sandbox_repo_binding(module),),
        )

    assert len(conn.sandbox_leases) == 1
    assert conn.sandbox_leases[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_sandbox_lease_idempotency_rejects_digest_conflict() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first = _sandbox_lease(module, idempotency_key="idem:sandbox:shared")
    conflicting = _sandbox_lease(
        module,
        idempotency_key="idem:sandbox:shared",
        base_commits={"app": "def456"},
        sandbox_root="/sandbox/feature-typed/g7/attempt-2-conflict",
    )

    await store.allocate_sandbox_lease(first, repo_bindings=(_sandbox_repo_binding(module),))
    with pytest.raises(module.IdempotencyConflict, match="different request|different lease"):
        await store.allocate_sandbox_lease(
            conflicting,
            repo_bindings=(_sandbox_repo_binding(module, base_commit="def456"),),
        )

    assert len(conn.sandbox_leases) == 1
    assert len(conn.sandbox_repo_bindings) == 1
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_sandbox_repo_binding_uniqueness_rejects_scope_conflict() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    lease_result = await store.allocate_sandbox_lease(
        _sandbox_lease(module),
        repo_bindings=(_sandbox_repo_binding(module),),
    )
    conflicting = _sandbox_repo_binding(
        module,
        sandbox_lease_id=lease_result.lease.id,
        sandbox_repo_root="/sandbox/feature-typed/g7/attempt-2/app-conflict",
        idempotency_key="idem:sandbox-repo-binding:conflict",
    )

    with pytest.raises(module.IdempotencyConflict, match="repo binding scope"):
        await store.record_sandbox_repo_binding(conflicting)

    assert len(conn.sandbox_repo_bindings) == 1
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_sandbox_runtime_binding_uniqueness_rejects_scope_conflict() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    lease_result = await store.allocate_sandbox_lease(
        _sandbox_lease(module),
        repo_bindings=(_sandbox_repo_binding(module),),
    )
    binding = _runtime_workspace_binding(module, sandbox_lease_id=lease_result.lease.id)

    first = await store.record_runtime_workspace_binding(binding)
    retry = await store.record_runtime_workspace_binding(binding)

    assert first.binding.id == retry.binding.id
    assert len(conn.runtime_workspace_bindings) == 1

    conflicting = _runtime_workspace_binding(
        module,
        sandbox_lease_id=lease_result.lease.id,
        cwd="/sandbox/feature-typed/g7/attempt-2/app-conflict",
        workspace_override="/sandbox/feature-typed/g7/attempt-2/app-conflict",
        idempotency_key="idem:runtime-workspace-binding:conflict",
    )
    with pytest.raises(module.IdempotencyConflict, match="runtime workspace binding scope"):
        await store.record_runtime_workspace_binding(conflicting)

    assert len(conn.runtime_workspace_bindings) == 1
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_sandbox_runtime_binding_rejects_missing_lease_id() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    binding = _runtime_workspace_binding(
        module,
        sandbox_lease_id=0,
        idempotency_key="idem:runtime-workspace-binding:missing-lease",
    )

    with pytest.raises(module.ExecutionControlError, match="sandbox lease id"):
        await store.record_runtime_workspace_binding(binding)

    assert conn.runtime_workspace_bindings == []
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_put_task_contract_replays_and_repairs_missing_projection() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    contract = _task_contract(module, idempotency_key="idem:contract:TASK-typed-1")

    first = await store.put_task_contract(contract)
    retry = await store.put_task_contract(contract)

    assert first.contract.id == retry.contract.id
    assert len(conn.typed_rows) == 1
    assert len(conn.task_contracts) == 1
    assert len(conn.artifacts) == 1
    assert len(conn.projection_links) == 1
    body = conn.artifacts[0]["value"]
    projection = json.loads(body)
    assert projection["contract_id"] == first.contract.id
    assert projection["contract_digest"] == contract.contract_digest
    assert projection["path_counts"]["allowed_paths"] == 1
    _assert_projection_link(
        conn,
        "dag-task-contract:TASK-typed-1",
        body,
        "idem:contract:TASK-typed-1:projection",
        expected_source_table="task_deliverable_contracts",
        expected_source_id=first.contract.id,
    )

    conn.projection_links.clear()
    repaired = await store.put_task_contract(contract)

    assert repaired.contract.id == first.contract.id
    assert len(conn.typed_rows) == 1
    assert len(conn.task_contracts) == 1
    assert len(conn.artifacts) == 1
    assert len(conn.projection_links) == 1


@pytest.mark.asyncio
async def test_put_task_contract_rejects_idempotency_conflict() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    contract = _task_contract(module, idempotency_key="idem:contract:TASK-typed-1")
    conflicting = _task_contract(
        module,
        idempotency_key="idem:contract:TASK-typed-1",
        allowed_paths=[{
            "id": "path-2",
            "repo_id": "app",
            "path": "src/other.py",
            "match_kind": "file",
            "intent": "modify",
            "allow_modify": True,
        }],
    )

    await store.put_task_contract(contract)
    with pytest.raises(module.IdempotencyConflict):
        await store.put_task_contract(conflicting)

    assert len(conn.task_contracts) == 1
    assert len(conn.artifacts) == 1
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_put_task_contract_changed_digest_supersedes_prior_active_scope() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    original = _task_contract(module, idempotency_key="idem:contract:TASK-typed-1")
    changed = _task_contract(
        module,
        idempotency_key="idem:contract:TASK-typed-1:v2",
        allowed_paths=[{
            "id": "path-2",
            "repo_id": "app",
            "path": "src/other.py",
            "match_kind": "file",
            "intent": "modify",
            "allow_modify": True,
        }],
    )

    first = await store.put_task_contract(original)
    second = await store.put_task_contract(changed)

    assert first.contract.contract_digest != second.contract.contract_digest
    assert first.contract.id != second.contract.id
    assert len(conn.typed_rows) == 2
    assert len(conn.task_contracts) == 2
    assert conn.task_contracts[0]["status"] == "superseded"
    assert conn.task_contracts[1]["status"] == "active"
    assert {
        (
            row["feature_id"],
            row["dag_sha256"],
            row["group_idx"],
            row["task_id"],
        )
        for row in conn.task_contracts
    } == {(FEATURE_ID, DAG_SHA256, 7, "TASK-typed-1")}


@pytest.mark.asyncio
async def test_put_task_contract_stale_replay_after_supersession_is_idempotent() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    original = _task_contract(module, idempotency_key="idem:contract:TASK-typed-1")
    changed = _task_contract(
        module,
        idempotency_key="idem:contract:TASK-typed-1:v2",
        allowed_paths=[{
            "id": "path-2",
            "repo_id": "app",
            "path": "src/other.py",
            "match_kind": "file",
            "intent": "modify",
            "allow_modify": True,
        }],
    )

    first = await store.put_task_contract(original)
    second = await store.put_task_contract(changed)
    stale = await store.put_task_contract(original)

    assert stale.contract.id == first.contract.id
    assert stale.contract.status == "superseded"
    assert stale.created is False
    assert stale.execution.created is False
    assert len(conn.typed_rows) == 2
    assert len(conn.task_contracts) == 2
    assert len(conn.artifacts) == 2
    assert len(conn.projection_links) == 2
    _assert_task_contract_projection_exact(
        conn,
        typed_row_id=first.execution.row.id,
        contract_id=int(first.contract.id),
        contract_digest=first.contract.contract_digest,
        idempotency_key="idem:contract:TASK-typed-1:projection",
    )
    _assert_task_contract_projection_exact(
        conn,
        typed_row_id=second.execution.row.id,
        contract_id=int(second.contract.id),
        contract_digest=second.contract.contract_digest,
        idempotency_key="idem:contract:TASK-typed-1:v2:projection",
    )


@pytest.mark.asyncio
async def test_task_contract_projection_is_bounded_summary() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    allowed_paths = [
        {
            "id": f"path-{idx}",
            "repo_id": "app",
            "path": f"src/file_{idx}.py",
            "match_kind": "file",
            "intent": "modify",
            "allow_modify": True,
        }
        for idx in range(60)
    ]
    gates = [
        {
            "id": f"gate-{idx}",
            "gate_kind": "deterministic",
            "name": f"gate {idx}",
            "blocks_merge": True,
        }
        for idx in range(60)
    ]
    contract = _task_contract(
        module,
        idempotency_key="idem:contract:bounded",
        allowed_paths=allowed_paths,
        verification_gates=gates,
        compile_warnings=[f"warning {idx}" for idx in range(60)],
    )

    await store.put_task_contract(contract)

    body = json.loads(conn.artifacts[0]["value"])
    assert body["path_counts"]["allowed_paths"] == 60
    assert len(body["gates"]) == 51
    assert body["gates"][-1] == {"bounded": "list_truncated", "omitted": 10}
    assert len(body["compile_warnings"]) == 51
    assert body["compile_warnings"][-1] == {"bounded": "list_truncated", "omitted": 10}
    assert "allowed_paths" not in body


@pytest.mark.asyncio
async def test_patch_summary_and_contract_verdict_evidence_replay_with_projection() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    contract = await store.put_task_contract(
        _task_contract(module, idempotency_key="idem:contract:TASK-typed-1")
    )
    patch = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=2,
        sandbox_id="sandbox-123",
        task_id="TASK-typed-1",
        contract_ids=[contract.contract.id],
        repo_id="app",
        base_commit="abc123",
        changed_paths=[f"src/file_{idx}.py" for idx in range(60)],
        modified_paths=[f"src/file_{idx}.py" for idx in range(60)],
        diff_sha256="a" * 64,
        diff_artifact_id=1010,
        summary_artifact_id=1011,
        metadata={
            "workspace_snapshot_id": 250,
            "base_snapshot_id": 249,
            "base_snapshot_ids": [249],
        },
        payload={
            "workspace_snapshot_id": 250,
            "base_snapshot_id": 249,
            "base_snapshot_ids": [249],
        },
        idempotency_key="idem:patch:g7:attempt-2:app",
    )

    patch_result = await store.record_patch_summary(patch)
    patch_retry = await store.record_patch_summary(patch)

    assert patch_result.evidence.id == patch_retry.evidence.id
    assert len([row for row in conn.evidence_nodes if row["kind"] == "sandbox_patch_summary"]) == 1
    patch_artifact = next(
        row for row in conn.artifacts
        if row["key"] == "dag-sandbox-patch:g7:attempt-2:repo-app"
    )
    patch_projection = json.loads(patch_artifact["value"])
    assert patch_projection["patch_summary_id"] == patch_result.evidence.id
    assert patch_projection["workspace_snapshot_id"] == 250
    assert patch_projection["base_snapshot_id"] == 249
    assert patch_projection["base_snapshot_ids"] == [249]
    assert patch_projection["path_counts"]["changed_paths"] == 60
    assert len(patch_projection["changed_paths"]) == 50
    link = _assert_projection_link(
        conn,
        "dag-sandbox-patch:g7:attempt-2:repo-app",
        patch_artifact["value"],
        "idem:patch:g7:attempt-2:app:projection",
        expected_source_table="evidence_nodes",
        expected_source_id=patch_result.evidence.id,
    )
    patch_row = next(row for row in conn.evidence_nodes if row["id"] == patch_result.evidence.id)
    assert patch_row["snapshot_id"] == 250
    assert set(json.loads(patch_row["input_refs"])) >= {249, 250}
    assert link["payload"]["snapshot_id"] == 250
    assert set(link["payload"]["input_refs"]) >= {249, 250}

    verdict = module.ContractVerdict(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        task_id="TASK-typed-1",
        sandbox_id="sandbox-123",
        contract_id=contract.contract.id,
        patch_summary_id=patch_result.evidence.id,
        approved=False,
        violation_codes=["outside_allowed_paths"],
        violations=[
            {"code": "outside_allowed_paths", "path": "src/stray.py"}
        ],
        required_evidence_node_ids=[patch_result.evidence.id],
        metadata={
            "captured_patch_summary_id": patch_result.evidence.id,
            "diff_artifact_id": 1010,
            "actual_sandbox_id": "sandbox-actual-123",
        },
        idempotency_key="idem:verdict:g7:TASK-typed-1:sandbox-123",
    )

    verdict_result = await store.record_contract_verdict(verdict)
    verdict_retry = await store.record_contract_verdict(verdict)

    assert verdict_result.evidence.id == verdict_retry.evidence.id
    assert len([row for row in conn.evidence_nodes if row["kind"] == "contract_verdict"]) == 1
    verdict_artifact = next(
        row for row in conn.artifacts
        if row["key"] == "dag-contract-verdict:g7:TASK-typed-1:sandbox-123"
    )
    verdict_projection = json.loads(verdict_artifact["value"])
    assert verdict_projection["approved"] is False
    assert verdict_projection["contract_id"] == contract.contract.id
    assert verdict_projection["patch_summary_id"] == patch_result.evidence.id
    assert verdict_projection["captured_patch_summary_id"] == patch_result.evidence.id
    assert verdict_projection["diff_artifact_id"] == 1010
    assert verdict_projection["actual_sandbox_id"] == "sandbox-actual-123"
    _assert_projection_link(
        conn,
        "dag-contract-verdict:g7:TASK-typed-1:sandbox-123",
        verdict_artifact["value"],
        "idem:verdict:g7:TASK-typed-1:sandbox-123:projection",
        expected_source_table="evidence_nodes",
        expected_source_id=verdict_result.evidence.id,
    )


@pytest.mark.asyncio
async def test_patch_summary_replay_tolerates_new_diff_artifact_id() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    first = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=2,
        sandbox_id="sandbox-123",
        task_id="TASK-typed-1",
        contract_ids=[275],
        repo_id="app",
        base_commit="abc123",
        changed_paths=["src/app.py"],
        modified_paths=["src/app.py"],
        diff_sha256="a" * 64,
        diff_artifact_id=1010,
        idempotency_key="idem:patch:artifact-retry",
    )
    retry = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=2,
        sandbox_id="sandbox-123",
        task_id="TASK-typed-1",
        contract_ids=[275],
        repo_id="app",
        base_commit="abc123",
        changed_paths=["src/app.py"],
        modified_paths=["src/app.py"],
        diff_sha256="a" * 64,
        diff_artifact_id=2020,
        idempotency_key="idem:patch:artifact-retry",
    )

    first_result = await store.record_patch_summary(first)
    retry_result = await store.record_patch_summary(retry)

    assert retry_result.evidence.id == first_result.evidence.id
    assert len([row for row in conn.evidence_nodes if row["kind"] == "sandbox_patch_summary"]) == 1


@pytest.mark.asyncio
async def test_patch_summary_default_idempotency_includes_contract_scope_identity() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))

    first = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=1,
        sandbox_id="sandbox-shared",
        task_id="TASK-one",
        contract_ids=[101],
        repo_id="app",
        base_commit="abc123",
        changed_paths=["src/shared.py"],
        modified_paths=["src/shared.py"],
        diff_sha256="c" * 64,
        stage="implementation",
    )
    second = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=1,
        sandbox_id="sandbox-shared",
        task_id="TASK-two",
        contract_ids=[202],
        repo_id="app",
        base_commit="abc123",
        changed_paths=["src/shared.py"],
        modified_paths=["src/shared.py"],
        diff_sha256="c" * 64,
        stage="implementation",
    )

    first_result = await store.record_patch_summary(first)
    second_result = await store.record_patch_summary(second)

    assert first.stable_idempotency_key != second.stable_idempotency_key
    assert first_result.evidence.id != second_result.evidence.id
    keys = [
        row["idempotency_key"]
        for row in conn.evidence_nodes
        if row["kind"] == "sandbox_patch_summary"
    ]
    assert len(keys) == 2
    assert any(
        ":repo-app:task-TASK-one:stage-implementation:contracts-101:" in key
        for key in keys
    )
    assert any(
        ":repo-app:task-TASK-two:stage-implementation:contracts-202:" in key
        for key in keys
    )


@pytest.mark.asyncio
async def test_contract_evidence_missing_projection_repair_rejects_changed_body() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    patch = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=1,
        sandbox_id="sandbox-123",
        task_id="TASK-typed-1",
        contract_ids=[275],
        repo_id="app",
        changed_paths=["src/app.py"],
        modified_paths=["src/app.py"],
        diff_sha256="a" * 64,
        idempotency_key="idem:patch:g7:attempt-1:app",
    )
    changed_patch = module.PatchSummary(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA256,
        group_idx=7,
        attempt_no=1,
        sandbox_id="sandbox-123",
        task_id="TASK-typed-1",
        contract_ids=[275],
        repo_id="app",
        changed_paths=["src/changed.py"],
        modified_paths=["src/changed.py"],
        diff_sha256="b" * 64,
        idempotency_key="idem:patch:g7:attempt-1:app",
    )

    await store.record_patch_summary(patch)
    conn.projection_links.clear()
    artifact_count = len(conn.artifacts)

    with pytest.raises(module.IdempotencyConflict):
        await store.record_patch_summary(changed_patch)

    assert len(conn.artifacts) == artifact_count
    assert conn.projection_links == []
    assert conn.transaction_rollbacks == 1


@pytest.mark.asyncio
async def test_legacy_resume_does_not_treat_task_contract_as_task_completion() -> None:
    conn = _FakeConnection()
    conn.artifacts.extend([
        {
            "id": 10,
            "feature_id": FEATURE_ID,
            "key": "dag-task-contract:TASK-typed-1",
            "value": "{}",
            "sha256": _sha256("{}"),
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        },
        {
            "id": 11,
            "feature_id": FEATURE_ID,
            "key": "dag-task:TASK-typed-1",
            "value": "{\"status\":\"done\"}",
            "sha256": _sha256("{\"status\":\"done\"}"),
            "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        },
    ])
    store = _store(_FakePool(conn))

    records = await store.list_legacy_resume_artifacts(
        feature_id=FEATURE_ID,
        prefixes=("dag-task:",),
    )

    assert [record["key"] for record in records] == ["dag-task:TASK-typed-1"]


@pytest.mark.asyncio
async def test_dispatch_attempt_start_and_finish_are_idempotent_and_conflict_closed() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    request = _dispatch_request(module, request_digest="dispatch-digest-1")

    first = await store.start_dispatch_attempt(request)
    retry = await store.start_dispatch_attempt(request)

    assert first.attempt_id == retry.attempt_id
    assert first.created is True
    assert retry.created is False
    assert conn.typed_rows[0]["entry_type"] == "dispatch_attempt"
    assert conn.typed_rows[0]["dispatcher_state"] == "attempt_started"
    assert conn.typed_rows[0]["runtime"] == "codex"

    conflicting = _dispatch_request(
        module,
        idempotency_key=request.stable_idempotency_key,
        request_digest="dispatch-digest-2",
    )
    with pytest.raises(module.IdempotencyConflict):
        await store.start_dispatch_attempt(conflicting)

    structured = await store.record_structured_output(
        _structured_output(module, first.attempt_id)
    )
    outcome = module.DispatchOutcome(
        attempt_id=first.attempt_id,
        state="succeeded",
        status="succeeded",
        structured_result_evidence_id=structured.evidence.id,
        patch_summary_ids=[901],
        idempotency_key="idem:dispatch-finish:TASK-typed-1",
    )

    finished = await store.finish_dispatch_attempt(outcome)
    await store.finish_dispatch_attempt(finished)

    row_payload = json.loads(conn.typed_rows[0]["payload"])
    assert conn.typed_rows[0]["status"] == "succeeded"
    assert conn.typed_rows[0]["dispatcher_state"] == "succeeded"
    assert row_payload["dispatch_outcome_digest"] == finished.digest
    assert row_payload["dispatch_outcome"]["compatibility_artifact_ids"]

    changed_outcome = module.DispatchOutcome(
        attempt_id=first.attempt_id,
        state="failed",
        status="failed",
        runtime_failure_id=999,
        typed_failure_id=999,
        idempotency_key="idem:dispatch-finish:TASK-typed-1",
    )
    with pytest.raises(module.IdempotencyConflict):
        await store.finish_dispatch_attempt(changed_outcome)


@pytest.mark.asyncio
async def test_late_dispatch_evidence_cannot_overwrite_terminal_attempt() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    stale_row = attempt.attempt
    structured = await store.record_structured_output(
        _structured_output(module, attempt.attempt_id)
    )
    await store.finish_dispatch_attempt(
        module.DispatchOutcome(
            attempt_id=attempt.attempt_id,
            state="succeeded",
            status="succeeded",
            structured_result_evidence_id=structured.evidence.id,
            patch_summary_ids=[901],
            idempotency_key="idem:dispatch-finish:TASK-typed-1",
        )
    )
    terminal_payload = conn.typed_rows[0]["payload"]

    with pytest.raises(module.ExecutionControlError, match="no longer mutable"):
        await store._update_dispatch_attempt_row(
            conn,
            stale_row,
            status="started",
            dispatcher_state="runtime_invoking",
            payload_patch={"late_runtime_invocation_evidence_id": 999},
        )

    assert conn.typed_rows[0]["status"] == "succeeded"
    assert conn.typed_rows[0]["dispatcher_state"] == "succeeded"
    assert conn.typed_rows[0]["payload"] == terminal_payload


@pytest.mark.asyncio
async def test_runtime_invoking_request_evidence_cannot_move_returned_attempt_backward() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))

    returned = await store.record_runtime_invocation(
        module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-returned",
            runtime="codex",
            phase="response",
            status="completed",
            terminal_reason="completed",
            process_started=True,
            provider_request_id="provider-returned",
        )
    )
    payload_before = conn.typed_rows[0]["payload"]

    stale_request = await store.record_runtime_invocation(
        module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-request-stale",
            runtime="codex",
            phase="request",
            status="running",
            process_started=True,
            provider_request_id="provider-request-stale",
        )
    )

    assert returned.execution.row.dispatcher_state == "runtime_returned"
    assert stale_request.evidence.kind == "runtime_invocation"
    assert stale_request.execution.row.dispatcher_state == "runtime_returned"
    assert conn.typed_rows[0]["status"] == "started"
    assert conn.typed_rows[0]["dispatcher_state"] == "runtime_returned"
    assert conn.typed_rows[0]["payload"] == payload_before


@pytest.mark.asyncio
async def test_stale_dispatch_attempt_update_uses_monotonic_compare_and_set() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    stale_row = attempt.attempt

    await store.record_runtime_invocation(
        module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-returned",
            runtime="codex",
            phase="response",
            status="completed",
            terminal_reason="completed",
            process_started=True,
        )
    )
    payload_before = conn.typed_rows[0]["payload"]

    updated = await store._update_dispatch_attempt_row(
        conn,
        stale_row,
        status="started",
        dispatcher_state="runtime_invoking",
        payload_patch={
            "dispatcher_state": "runtime_invoking",
            "late_runtime_invocation_evidence_id": 999,
        },
    )

    assert updated.dispatcher_state == "runtime_returned"
    assert conn.typed_rows[0]["dispatcher_state"] == "runtime_returned"
    assert conn.typed_rows[0]["payload"] == payload_before


@pytest.mark.asyncio
async def test_output_normalizing_attempt_cannot_move_backward_to_runtime_invoking() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    stale_row = attempt.attempt
    conn.typed_rows[0]["dispatcher_state"] = "output_normalizing"
    conn.typed_rows[0]["payload"] = json.dumps(
        {
            "dispatcher_state": "output_normalizing",
            "normalized_output_started": True,
        },
        sort_keys=True,
    )
    payload_before = conn.typed_rows[0]["payload"]

    updated = await store._update_dispatch_attempt_row(
        conn,
        stale_row,
        status="started",
        dispatcher_state="runtime_invoking",
        payload_patch={
            "dispatcher_state": "runtime_invoking",
            "late_runtime_invocation_evidence_id": 1001,
        },
    )

    assert updated.dispatcher_state == "output_normalizing"
    assert conn.typed_rows[0]["dispatcher_state"] == "output_normalizing"
    assert conn.typed_rows[0]["payload"] == payload_before


@pytest.mark.asyncio
async def test_dispatch_raw_output_evidence_is_idempotent_and_linked() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))

    raw = module.RawOutputEvidence(
        attempt_id=attempt.attempt_id,
        invocation_id="invoke-raw",
        runtime="codex",
        status="completed",
        terminal_reason="completed",
        raw_text='{"status":"completed"}',
        raw_artifact_id=61,
        provider_request_id="provider-raw",
    )
    first = await store.record_raw_output(raw)
    second = await store.record_raw_output(raw)

    assert first.evidence.id == second.evidence.id
    assert second.created is False
    row_payload = json.loads(conn.typed_rows[0]["payload"])
    assert row_payload["last_raw_output_evidence_id"] == first.evidence.id
    assert row_payload["raw_output_evidence_ids"] == [first.evidence.id]
    assert first.evidence.kind == "raw_output"
    assert first.evidence.payload["raw_text_sha256"] == raw.raw_text_sha256

    structured = await store.record_structured_output(
        _structured_output(
            module,
            attempt.attempt_id,
            raw_text_ref=first.evidence.id,
            raw_artifact_id=61,
        )
    )

    assert structured.evidence.input_refs == [first.evidence.id, 61]

    conflicting = module.RawOutputEvidence(
        attempt_id=attempt.attempt_id,
        invocation_id="invoke-raw",
        runtime="codex",
        status="completed",
        terminal_reason="completed",
        raw_text="different",
        idempotency_key=raw.stable_idempotency_key,
    )
    with pytest.raises(module.IdempotencyConflict):
        await store.record_raw_output(conflicting)


@pytest.mark.asyncio
async def test_dispatch_runtime_failure_is_typed_resumable_evidence() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    runtime = await store.record_runtime_invocation(
        module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-1",
            runtime="codex",
            phase="response",
            status="failed",
            terminal_reason="provider_error",
            process_started=True,
            provider_request_id="provider-req-1",
            provider_error_code="rate_limited",
            elapsed_ms=1200,
        )
    )

    failure = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_provider",
            failure_type="provider_rate_limited",
            retryable=True,
            deterministic=False,
            provider_request_id="provider-req-1",
            evidence_ids=[runtime.evidence.id],
            runtime="codex",
            provider_error_code="rate_limited",
            terminal_reason="provider_error",
            signature_hash="runtime-provider-signature",
        )
    )

    assert failure.failure_id == failure.evidence.id
    assert failure.typed_failure_id == failure.evidence.id
    assert failure.signature_hash == "runtime-provider-signature"
    assert failure.evidence.kind == "runtime_failure_context"
    assert failure.evidence.status == "failed"
    assert failure.evidence.payload["failure_class"] == "runtime_provider"
    assert failure.evidence.payload["failure_type"] == "provider_rate_limited"
    assert failure.evidence.payload["retryable"] is True
    assert failure.evidence.payload["deterministic"] is False
    assert failure.evidence.payload["evidence_ids"] == [runtime.evidence.id]

    poisoned = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_provider",
            failure_type="provider_transport_error",
            retryable=True,
            deterministic=False,
            evidence_ids=[runtime.evidence.id],
            runtime="codex",
            terminal_reason="provider_error",
            signature_hash="runtime-provider-poisoned-payload",
            idempotency_key="idem:runtime-failure:poisoned-payload",
            payload={
                "failure_class": "product_defect",
                "failure_type": "semantic_verifier_rejected",
                "retryable": False,
                "deterministic": True,
                "operator_required": True,
                "signature_hash": "legacy-signature",
            },
        )
    )

    assert poisoned.evidence.payload["failure_class"] == "runtime_provider"
    assert poisoned.evidence.payload["failure_type"] == "provider_transport_error"
    assert poisoned.evidence.payload["retryable"] is True
    assert poisoned.evidence.payload["deterministic"] is False
    assert poisoned.evidence.payload["operator_required"] is False
    assert poisoned.evidence.payload["signature_hash"] == "runtime-provider-poisoned-payload"
    assert poisoned.evidence.payload["legacy_failure_class"] == "product_defect"
    assert poisoned.evidence.payload["legacy_failure_type"] == "semantic_verifier_rejected"
    assert poisoned.evidence.payload["legacy_signature_hash"] == "legacy-signature"

    outcome = module.DispatchOutcome(
        attempt_id=attempt.attempt_id,
        state="failed",
        status="failed",
        runtime_terminal_reason="provider_error",
        runtime_failure_id=failure.failure_id,
        typed_failure_id=failure.typed_failure_id,
        idempotency_key="idem:dispatch-failed:TASK-typed-1",
    )
    await store.finish_dispatch_attempt(outcome)

    with pytest.raises(module.ExecutionControlError, match="non-succeeded"):
        await store.project_task_result_from_attempt(attempt.attempt_id)
    assert conn.artifacts == []
    assert conn.projection_links == []
    # The full-dict control_plane.snapshot_changed outbox assertions that
    # previously lived here described a writer that was never built (Slice
    # 10g-1 journal). Dispatch-attempt mutations do not flow through the
    # projection seam, so they intentionally do NOT enqueue a doc-10
    # snapshot.changed outbox row; the doc-10-correct SUMMARY-only
    # projection at the projection seam is covered by the dedicated
    # test_project_control_plane_snapshot_changed_* suite below.


@pytest.mark.asyncio
async def test_late_runtime_completion_recovery_converts_timeout_attempt_to_success() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    base_request = _dispatch_request(module)
    request = _dispatch_request(
        module,
        actor_metadata={**base_request.actor_metadata, "runtime": "claude_pool"},
    )
    attempt = await store.start_dispatch_attempt(request)
    patch = await store.record_patch_summary(
        module.PatchSummary(
            feature_id=FEATURE_ID,
            dag_sha256=DAG_SHA256,
            group_idx=7,
            attempt_no=attempt.attempt_id,
            sandbox_id="sandbox-123",
            task_id="TASK-typed-1",
            repo_id="app",
            base_commit="abc123",
            changed_paths=["src/app.py"],
            modified_paths=["src/app.py"],
            diff_sha256="b" * 64,
            diff_artifact_id=1010,
            summary_artifact_id=1011,
            idempotency_key="idem:patch:g7:attempt-late:app",
        )
    )
    runtime = await store.record_runtime_invocation(
        module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-late",
            runtime="claude_pool",
            phase="response",
            status="failed",
            terminal_reason="timeout",
            process_started=True,
        )
    )
    failure = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_timeout",
            failure_type="watchdog_timeout",
            retryable=True,
            deterministic=False,
            evidence_ids=[patch.evidence.id],
            runtime="claude_pool",
            terminal_reason="timeout",
            signature_hash="late-timeout-signature",
        )
    )
    await store.finish_dispatch_attempt(
        module.DispatchOutcome(
            attempt_id=attempt.attempt_id,
            state="failed",
            status="failed",
            runtime_terminal_reason="timeout",
            runtime_failure_id=failure.failure_id,
            typed_failure_id=failure.typed_failure_id,
            idempotency_key=request.stable_idempotency_key,
        )
    )

    recovered = await store.recover_late_runtime_completion(
        attempt_id=attempt.attempt_id,
        runtime_invocation=module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-late",
            runtime="claude_pool",
            phase="response",
            status="completed",
            terminal_reason="completed",
            process_started=True,
            elapsed_ms=2_155_416,
            idempotency_key="idem:late-runtime-invocation",
        ),
        raw_output=module.RawOutputEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-late",
            runtime="claude_pool",
            status="completed",
            terminal_reason="completed",
            raw_text='{"task_id":"TASK-typed-1","status":"completed"}',
            idempotency_key="idem:late-raw-output",
        ),
        structured_output=_structured_output(
            module,
            attempt.attempt_id,
            idempotency_key="idem:late-structured-output",
        ),
        recovery_metadata={"job_id": "job-late"},
    )

    assert runtime.evidence.id
    assert recovered.status == "succeeded"
    assert recovered.runtime_terminal_reason == "completed"
    assert recovered.patch_summary_ids == [patch.evidence.id]
    row_payload = json.loads(conn.typed_rows[0]["payload"])
    assert conn.typed_rows[0]["status"] == "succeeded"
    assert row_payload["dispatch_outcome"]["status"] == "succeeded"
    assert row_payload["late_runtime_completion_recovery"]["job_id"] == "job-late"
    assert row_payload["late_runtime_completion_recovery"]["recovered_from_typed_failure_id"] == failure.typed_failure_id
    assert "typed_failure_id" not in row_payload
    assert conn.artifacts[-1]["key"] == "dag-task:TASK-typed-1"


@pytest.mark.asyncio
async def test_dispatch_runtime_failure_persists_recovery_evidence_on_attempt_payload() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    recovery_evidence = {
        "durable": True,
        "heartbeat_stale": True,
        "heartbeat_evidence_id": 501,
    }

    failure = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_provider",
            failure_type="provider_replay_stale",
            retryable=True,
            deterministic=False,
            signature_hash="runtime-replay-recovery",
            payload={
                "duplicate_replay_recovery_evidence": recovery_evidence,
            },
        )
    )

    row_payload = json.loads(conn.typed_rows[0]["payload"])
    assert row_payload["runtime_failure_signature_hash"] == "runtime-replay-recovery"
    assert row_payload["duplicate_replay_recovery_evidence"] == recovery_evidence


@pytest.mark.asyncio
async def test_dispatch_runtime_failure_updates_snapshot_retry_budget_and_outbox() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))

    failure = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_provider",
            failure_type="provider_rate_limited",
            retryable=True,
            deterministic=True,
            runtime="codex",
            provider_error_code="rate_limited",
            terminal_reason="provider_error",
            signature_hash="provider-retry-budget-signature",
            payload={
                "route": "run_product_repair",
                "retry_budget": {
                    "route": "retry_dispatch",
                    "retry": 0,
                    "max_retries": 3,
                    "idempotency_key": "idem:dispatch:TASK-typed-1",
                },
                "route_decision": {
                    "route": "retry_dispatch",
                    "failure_class": "runtime_provider",
                    "failure_type": "provider_rate_limited",
                    "deterministic": False,
                    "retryable": True,
                    "operator_required": False,
                },
            },
        )
    )

    # The full-dict control_plane.snapshot_changed outbox assertions that
    # previously lived here described a writer that was never built (Slice
    # 10g-1 journal). The orthogonal evidence/route/retry-budget contracts
    # below MUST stay and continue to cover the route normalization,
    # legacy_route preservation, and retry-budget shape on the typed row;
    # the doc-10-correct SUMMARY-only outbox projection is covered by the
    # dedicated test_project_control_plane_snapshot_changed_* suite below.
    assert "route" not in failure.evidence.payload
    assert failure.evidence.payload["legacy_route"] == "run_product_repair"
    attempt_payload = json.loads(conn.typed_rows[0]["payload"])
    assert "route" not in attempt_payload
    assert attempt_payload["retry_budget"]["max_retries"] == 3
    assert attempt_payload["route_decision"]["route"] == "retry_dispatch"


@pytest.mark.asyncio
async def test_dispatch_runtime_failure_snapshot_normalizes_max_attempt_budget() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))

    await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_provider",
            failure_type="provider_crash",
            retryable=True,
            deterministic=False,
            runtime="codex",
            provider_error_code="provider_crash",
            terminal_reason="provider_error",
            signature_hash="provider-max-attempts-signature",
            payload={
                "route": "retry_dispatch",
                "retry_budget": {
                    "route": "retry_dispatch",
                    "retry": 0,
                    "max_attempts": 2,
                    "remaining_attempts": 1,
                    "idempotency_key": "idem:dispatch:TASK-typed-1",
                },
            },
        )
    )

    # The full-dict control_plane.snapshot_changed outbox assertions that
    # previously lived here described a writer that was never built (Slice
    # 10g-1 journal). The doc-10-correct SUMMARY-only outbox projection is
    # covered by the dedicated test_project_control_plane_snapshot_changed_*
    # suite below; the retry-budget MAX-ATTEMPTS normalization on the typed
    # attempt row is still verified here.
    attempt_payload = json.loads(conn.typed_rows[0]["payload"])
    assert attempt_payload["retry_budget"]["max_attempts"] == 2
    assert attempt_payload["retry_budget"]["remaining_attempts"] == 1


def test_retry_budget_snapshot_falls_back_to_max_attempts_without_remaining() -> None:
    module = _execution_control_module()

    budgets = module._control_plane_retry_budgets(
        attempts=[
            {
                "id": 1,
                "group_idx": 7,
                "task_id": "TASK-budget",
                "status": "failed",
                "dispatcher_state": "failed",
                "retry_budget": None,
            },
            {
                "id": 2,
                "group_idx": 7,
                "task_id": "TASK-budget",
                "status": "failed",
                "dispatcher_state": "failed",
                "retry_budget": None,
            },
        ],
        runtime_failures=[
            {
                "id": 44,
                "attempt_id": 2,
                "group_idx": 7,
                "route": "retry_dispatch",
                "retry_budget": {
                    "route": "retry_dispatch",
                    "max_attempts": 2,
                    "task_id": "TASK-budget",
                },
            }
        ],
        limit=10,
    )

    assert budgets[0]["retry_budget"]["max_attempts"] == 2
    assert budgets[0]["attempts_used"] == 2
    assert budgets[0]["remaining"] == 0


@pytest.mark.asyncio
async def test_dispatch_duplicate_conflict_evidence_does_not_poison_attempt_payload() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    row = conn.typed_rows[0]
    payload_before = deepcopy(row["payload"])
    status_before = row["status"]
    state_before = row["dispatcher_state"]

    failure = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="dispatcher_internal",
            failure_type="idempotency_conflict",
            retryable=False,
            deterministic=True,
            runtime="codex",
            terminal_reason="process_failed",
            signature_hash="duplicate-inflight-signature",
        )
    )

    assert failure.failure_id == failure.evidence.id
    assert failure.evidence.kind == "runtime_failure_context"
    assert failure.evidence.payload["failure_class"] == "dispatcher_internal"
    assert failure.evidence.payload["failure_type"] == "idempotency_conflict"
    assert row["status"] == status_before
    assert row["dispatcher_state"] == state_before
    assert row["payload"] == payload_before
    assert "runtime_failure_id" not in row["payload"]
    assert "typed_failure_id" not in row["payload"]


@pytest.mark.asyncio
async def test_dispatch_duplicate_conflict_evidence_can_attach_to_terminal_attempt() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    row = conn.typed_rows[0]
    provider_failure = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="runtime_provider",
            failure_type="provider_error",
            retryable=True,
            deterministic=False,
            runtime="codex",
            terminal_reason="provider_error",
            signature_hash="provider-failure-signature",
        )
    )
    await store.finish_dispatch_attempt(
        module.DispatchOutcome(
            attempt_id=attempt.attempt_id,
            state="failed",
            status="failed",
            runtime_terminal_reason="provider_error",
            runtime_failure_id=provider_failure.failure_id,
            typed_failure_id=provider_failure.typed_failure_id,
            idempotency_key="idem:dispatch-failed:TASK-typed-1",
        )
    )
    payload_before = deepcopy(row["payload"])

    conflict = await store.record_runtime_failure(
        module.RuntimeFailureEvidence(
            attempt_id=attempt.attempt_id,
            failure_class="dispatcher_internal",
            failure_type="idempotency_conflict",
            retryable=False,
            deterministic=True,
            runtime="codex",
            terminal_reason="process_failed",
            signature_hash="terminal-duplicate-conflict-signature",
        )
    )

    assert conflict.failure_id == conflict.evidence.id
    assert conflict.evidence.kind == "runtime_failure_context"
    assert conflict.evidence.payload["failure_class"] == "dispatcher_internal"
    assert conflict.evidence.payload["failure_type"] == "idempotency_conflict"
    assert row["status"] == "failed"
    assert row["dispatcher_state"] == "failed"
    assert row["payload"] == payload_before


@pytest.mark.asyncio
async def test_project_task_result_from_attempt_once_from_valid_structured_output() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    structured = await store.record_structured_output(
        _structured_output(module, attempt.attempt_id)
    )
    await store.finish_dispatch_attempt(
        module.DispatchOutcome(
            attempt_id=attempt.attempt_id,
            state="succeeded",
            status="succeeded",
            structured_result_evidence_id=structured.evidence.id,
            patch_summary_ids=[901],
            idempotency_key="idem:dispatch-finish:TASK-typed-1",
        )
    )

    first = await store.project_task_result_from_attempt(
        module.TaskResultProjectionFromAttempt(attempt_id=attempt.attempt_id)
    )
    second = await store.project_task_result_from_attempt(
        module.TaskResultProjectionFromAttempt(attempt_id=attempt.attempt_id)
    )

    expected_body = ImplementationResult(
        task_id="TASK-typed-1",
        summary="done",
        status="completed",
        files_modified=["src/app.py"],
    ).model_dump_json()
    assert first.row.id == second.row.id == attempt.attempt_id
    assert len(conn.artifacts) == 1
    assert conn.artifacts[0]["key"] == "dag-task:TASK-typed-1"
    assert conn.artifacts[0]["value"] == expected_body
    assert len(conn.projection_links) == 1
    link = conn.projection_links[0]
    assert link["source_table"] == "evidence_nodes"
    assert link["source_id"] == structured.evidence.id
    assert link["projection_owner"] == "dispatcher"
    assert link["projection_kind"] == "task_result"
    assert json.loads(link["payload"])["projection_authority"] == "dispatcher_attempt"


@pytest.mark.asyncio
async def test_project_task_result_from_attempt_before_terminal_finish() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    structured = await store.record_structured_output(
        _structured_output(module, attempt.attempt_id)
    )

    projected = await store.project_task_result_from_attempt(
        module.TaskResultProjectionFromAttempt(
            attempt_id=attempt.attempt_id,
            structured_result_evidence_id=structured.evidence.id,
        )
    )
    await store.finish_dispatch_attempt(
        module.DispatchOutcome(
            attempt_id=attempt.attempt_id,
            state="succeeded",
            status="succeeded",
            structured_result_evidence_id=structured.evidence.id,
            patch_summary_ids=[901],
            compatibility_artifact_ids=[link.artifact_id for link in projected.projection_links],
            idempotency_key="idem:dispatch-finish:TASK-typed-1",
        )
    )

    assert len(conn.artifacts) == 1
    assert conn.typed_rows[0]["status"] == "succeeded"
    assert conn.typed_rows[0]["dispatcher_state"] == "succeeded"


@pytest.mark.asyncio
async def test_invalid_structured_output_cannot_finish_success_or_project() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))
    invalid = await store.record_structured_output(
        _structured_output(
            module,
            attempt.attempt_id,
            valid=False,
            normalized_payload=None,
            projection_body=None,
            validation_errors=["missing task_id"],
        )
    )

    assert invalid.evidence.status == "rejected"
    with pytest.raises(module.ExecutionControlError, match="invalid structured output"):
        await store.finish_dispatch_attempt(
            module.DispatchOutcome(
                attempt_id=attempt.attempt_id,
                state="succeeded",
                status="succeeded",
                structured_result_evidence_id=invalid.evidence.id,
                patch_summary_ids=[901],
            )
        )
    with pytest.raises(module.ExecutionControlError, match="invalid structured output"):
        await store.project_task_result_from_attempt(
            module.TaskResultProjectionFromAttempt(
                attempt_id=attempt.attempt_id,
                structured_result_evidence_id=invalid.evidence.id,
            )
        )
    assert conn.artifacts == []
    assert conn.projection_links == []


@pytest.mark.asyncio
async def test_dispatch_evidence_payloads_are_deterministic_jsonl_friendly() -> None:
    module = _execution_control_module()
    conn = _FakeConnection()
    store = _store(_FakePool(conn))
    attempt = await store.start_dispatch_attempt(_dispatch_request(module))

    prompt = await store.record_prompt_context(
        module.PromptContextEvidence(
            attempt_id=attempt.attempt_id,
            prompt_ref=101,
            prompt_sha256="p" * 64,
            prompt_summary="bounded prompt",
            context_file_refs=[103, 102],
            context_file_paths=["context/b.md", "context/a.md"],
            context_sha256="c" * 64,
            included_contract_ids=[2, 1],
            included_evidence_ids=[9, 7],
            excluded_evidence_ids=[8],
            truncation_notes=["trimmed prior evidence"],
        )
    )
    invocation = await store.record_runtime_invocation(
        module.RuntimeInvocationEvidence(
            attempt_id=attempt.attempt_id,
            invocation_id="invoke-1",
            runtime="codex",
            actor_name="worker-a",
            actor_role="implementer",
            prompt_ref=101,
            prompt_sha256="p" * 64,
            output_schema="ImplementationResult",
            output_schema_digest="s" * 64,
            output_type_name="ImplementationResult",
            timeout_seconds=120,
            status="running",
        )
    )
    structured = await store.record_structured_output(
        _structured_output(module, attempt.attempt_id)
    )

    assert prompt.evidence.kind == "context_package"
    assert invocation.evidence.kind == "runtime_invocation"
    assert structured.evidence.kind == "structured_result"
    assert prompt.evidence.payload["included_contract_ids"] == [1, 2]
    assert prompt.evidence.payload["included_evidence_ids"] == [7, 9]

    for row in conn.evidence_nodes:
        payload = row["payload"]
        assert isinstance(payload, str)
        assert payload == json.dumps(
            json.loads(payload),
            sort_keys=True,
            separators=(",", ":"),
        )

    schema_text = (Path(__file__).resolve().parents[1] / "schema.sql").read_text()
    assert "'context_package'" in schema_text
    assert "'runtime_invocation'" in schema_text
    assert "'structured_result'" in schema_text
    assert "'runtime_failure_context'" in schema_text


def _dispatch_request(module: Any, **overrides: Any) -> Any:
    values = {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA256,
        "group_idx": 7,
        "task_id": "TASK-typed-1",
        "task_name": "Typed task",
        "retry": 0,
        "retry_identity": {
            "retry": 0,
            "dispatch_retry_id": "dispatch-retry-0",
            "retry_of_attempt_id": None,
            "failure_retry_of_id": None,
            "route_decision_id": None,
            "route_request_id": None,
        },
        "contract_ids": [275],
        "sandbox_id": "sandbox-123",
        "workspace_snapshot_ids": [250],
        "base_commit_by_repo": {"app": "abc123"},
        "runtime_policy": "primary",
        "runtime_policy_digest": "runtime-policy-digest",
        "actor_role": "implementer",
        "actor_metadata": {
            "actor_id": "worker-a",
            "actor_name": "Worker A",
            "actor_role": "implementer",
            "runtime": "codex",
            "runtime_policy": "primary",
            "runtime_policy_digest": "runtime-policy-digest",
            "tool_profile": "standard",
            "sandbox_required": True,
            "approval_profile": "no_canonical_writes",
            "metadata_digest": "actor-metadata-digest",
        },
        "prior_evidence_ids": [],
        "request_digest": "dispatch-digest",
        "idempotency_key": "idem:dispatch:TASK-typed-1:retry-0",
        **overrides,
    }
    return module.DispatchAttemptRequest(**values)


def _structured_output(module: Any, attempt_id: int, **overrides: Any) -> Any:
    result = ImplementationResult(
        task_id="TASK-typed-1",
        summary="done",
        status="completed",
        files_modified=["src/app.py"],
    )
    values = {
        "attempt_id": attempt_id,
        "schema_name": "ImplementationResult",
        "schema_digest": "schema-digest",
        "valid": True,
        "original_payload": result.model_dump(mode="json"),
        "normalized_payload": result.model_dump(mode="json"),
        "validation_errors": [],
        "corrected_fields": {},
        "task_id_matches_request": True,
        "projection_body": result.model_dump_json(),
        **overrides,
    }
    return module.StructuredOutputEvidence(**values)


def _task_contract(module: Any, **overrides: Any) -> Any:
    allowed_paths = overrides.pop("allowed_paths", [{
        "id": "path-1",
        "repo_id": "app",
        "path": "src/app.py",
        "match_kind": "file",
        "intent": "modify",
        "allow_modify": True,
    }])
    required_paths = overrides.pop("required_paths", [{
        "id": "path-1",
        "repo_id": "app",
        "path": "src/app.py",
        "match_kind": "file",
        "intent": "modify",
        "required": True,
    }])
    verification_gates = overrides.pop("verification_gates", [{
        "id": "gate:TASK-typed-1:pytest",
        "gate_kind": "command",
        "name": "pytest",
        "criterion_ids": ["ac-1"],
        "blocks_merge": True,
    }])
    compile_warnings = overrides.pop("compile_warnings", [])
    normalized = {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA256,
        "source_dag_artifact_id": 90,
        "source_dag_sha256": "source-dag-sha",
        "group_idx": 7,
        "task_id": "TASK-typed-1",
        "repo_id": "app",
        "repo_path": "/workspace/app",
        "required_paths": required_paths,
        "allowed_paths": allowed_paths,
        "read_only_paths": [],
        "forbidden_paths": [],
        "generated_outputs": [],
        "acceptance_criteria": [{
            "id": "ac-1",
            "source_model": "TaskAcceptanceCriterion",
            "source_field": "acceptance_criteria",
            "source_ordinal": 0,
            "text": "Update app.py",
            "must_pass": True,
            "linked_path_rules": ["path-1"],
            "digest": "criterion-digest",
        }],
        "verification_gates": verification_gates,
        "execution_policy": {
            "write_set_mode": "declared",
            "sandbox_isolation": "group_shared",
            "merge_admission": "atomic_group",
            "requires_contract_verdict": True,
        },
        "non_goals": [],
        "dependency_task_ids": [],
        "unknown_write_set": False,
        "compile_warnings": compile_warnings,
    }
    normalized.update(overrides.pop("normalized_contract_json", {}))
    digest = module.stable_digest(normalized)
    values = {
        **normalized,
        "normalized_contract_json": normalized,
        "contract_digest": digest,
        "status": "active",
        "idempotency_key": "idem:contract:TASK-typed-1",
        **overrides,
    }
    return module.TaskDeliverableContract(**values)


def _sandbox_lease(module: Any, **overrides: Any) -> Any:
    values = {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA256,
        "group_idx": 7,
        "attempt_no": 2,
        "mode": "task",
        "lease_owner": "worker-a",
        "leased_until": datetime(2026, 5, 20, 13, tzinfo=timezone.utc),
        "sandbox_root": "/sandbox/feature-typed/g7/attempt-2",
        "sandbox_id": "sandbox-123",
        "manifest_path": "/sandbox/feature-typed/g7/attempt-2/sandbox-manifest.json",
        "base_snapshot_ids": [250],
        "repo_ids": ["app"],
        "base_commits": {"app": "abc123"},
        "task_ids": ["TASK-typed-1"],
        "contract_ids": [275],
        "writable_roots": ["/sandbox/feature-typed/g7/attempt-2/app/src"],
        "readonly_roots": [],
        "blocked_roots": ["/workspace/app"],
        "status": "allocated",
        **overrides,
    }
    return module.SandboxLease(**values)


def _sandbox_repo_binding(module: Any, **overrides: Any) -> Any:
    values = {
        "feature_id": FEATURE_ID,
        "repo_id": "app",
        "sandbox_repo_root": "/sandbox/feature-typed/g7/attempt-2/app",
        "canonical_repo_root": "/workspace/app",
        "base_snapshot_id": 250,
        "base_commit": "abc123",
        "writable": True,
        "writable_roots": ["/sandbox/feature-typed/g7/attempt-2/app/src"],
        "readonly_roots": [],
        "blocked_canonical_roots": ["/workspace/app"],
        "status": "active",
        **overrides,
    }
    return module.SandboxRepoBinding(**values)


def _runtime_workspace_binding(module: Any, **overrides: Any) -> Any:
    values = {
        "feature_id": FEATURE_ID,
        "sandbox_lease_id": 260,
        "attempt_id": 9001,
        "runtime_name": "codex",
        "cwd": "/sandbox/feature-typed/g7/attempt-2/app",
        "workspace_override": "/sandbox/feature-typed/g7/attempt-2/app",
        "manifest_path": "/sandbox/feature-typed/g7/attempt-2/sandbox-manifest.json",
        "repo_roots": {"app": "/sandbox/feature-typed/g7/attempt-2/app"},
        "writable_roots": ["/sandbox/feature-typed/g7/attempt-2/app/src"],
        "readonly_roots": [],
        "blocked_roots": ["/workspace/app"],
        "env": {"IRIAI_SANDBOX_ID": "sandbox-123"},
        "role_metadata": {"role": "implementer", "sandbox": True},
        "status": "bound",
        **overrides,
    }
    return module.RuntimeWorkspaceBinding(**values)


def _assert_projection_link(
    conn: _FakeConnection,
    projection_key: str,
    body_text: str,
    idempotency_key: str,
    *,
    expected_source_table: str = "execution_journal_rows",
    expected_source_id: int | None = None,
) -> dict[str, Any]:
    links = [row for row in conn.projection_links if row["projection_key"] == projection_key]
    assert len(links) == 1, (
        f"{projection_key} must have exactly one execution_artifact_projections link"
    )
    link = links[0]
    artifact = next(row for row in conn.artifacts if row["id"] == link["artifact_id"])
    assert artifact["key"] == projection_key
    assert link["feature_id"] == FEATURE_ID
    assert link["projection_key"] == projection_key
    assert link["projection_sha256"] == _sha256(body_text)
    assert link["idempotency_key"] == idempotency_key
    assert link["source_table"] == expected_source_table
    if expected_source_id is None:
        assert link["source_id"] == link["typed_row_id"]
    else:
        assert link["source_id"] == expected_source_id
    assert link["projection_owner"]
    assert link["projection_kind"]
    assert link["dashboard_outbox_event_id"] == f"artifact-write:{artifact['id']}"
    if isinstance(link.get("payload"), str):
        link["payload"] = json.loads(link["payload"])
    return link


def _assert_task_contract_projection_exact(
    conn: _FakeConnection,
    *,
    typed_row_id: int,
    contract_id: int,
    contract_digest: str,
    idempotency_key: str,
) -> dict[str, Any]:
    links = [
        row for row in conn.projection_links
        if row["source_table"] == "task_deliverable_contracts"
        and row["source_id"] == contract_id
        and row["typed_row_id"] == typed_row_id
        and row["idempotency_key"] == idempotency_key
    ]
    assert len(links) == 1
    link = links[0]
    artifact = next(row for row in conn.artifacts if row["id"] == link["artifact_id"])
    body = json.loads(artifact["value"])
    assert artifact["key"] == "dag-task-contract:TASK-typed-1"
    assert link["feature_id"] == FEATURE_ID
    assert link["projection_key"] == "dag-task-contract:TASK-typed-1"
    assert link["projection_sha256"] == _sha256(artifact["value"])
    assert link["projection_owner"] == "contract_service"
    assert link["projection_kind"] == "task_contract"
    assert link["dashboard_outbox_event_id"] == f"artifact-write:{artifact['id']}"
    assert json.loads(link["payload"]) == {
        "entry_type": "task_contract",
        "task_contract_id": contract_id,
    }
    assert body["contract_id"] == contract_id
    assert body["contract_digest"] == contract_digest
    assert body["dag_sha256"] == DAG_SHA256
    assert body["group_idx"] == 7
    assert body["task_id"] == "TASK-typed-1"
    assert "allowed_paths" not in body
    return link


def _workspace_snapshot_payload(
    *,
    captured_at: str = "2026-05-19T12:00:00+00:00",
    dirty_paths: list[str] | None = None,
) -> dict[str, Any]:
    dirty = [] if dirty_paths is None else dirty_paths
    return {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA256,
        "group_idx": 45,
        "stage": "retry-0",
        "repo_id": "app",
        "canonical_path": "/workspace/app",
        "registry_digest": "registry-digest-1",
        "head_sha": "abc123",
        "index_digest": "index-digest-1",
        "worktree_status_digest": "status-digest-1",
        "dirty_paths": dirty,
        "captured_at": captured_at,
        "validated_at": captured_at,
    }


def _workspace_snapshot_artifact_body(payload: dict[str, Any]) -> str:
    stable_payload = {
        key: value
        for key, value in payload.items()
        if key not in {
            "acl_artifact_id",
            "captured_at",
            "compatibility_projection_artifact_ids",
            "registry_artifact_id",
            "validated_at",
        }
    }
    return json.dumps(stable_payload, sort_keys=True, separators=(",", ":"))


def _first_matching(values: tuple[object, ...], predicate) -> object | None:
    return next((value for value in values if predicate(value)), None)


def _looks_like_projection_key(value: object) -> bool:
    return isinstance(value, str) and value.startswith((
        "dag-task:",
        "dag-task-contract:",
        "dag-verify:",
        "dag-commit-failure:",
        "dag-group:",
        "dag-sandbox:",
        "dag-sandbox-patch:",
        "dag-contract-verdict:",
        "dag-regroup:",
        "dag-regroup-active:",
        "dag-worktree-alias-preflight:",
        "dag-writeability-preflight:",
        "workspace-snapshot:",
        "worktree-registry",
    ))


def _looks_like_idempotency_key(value: object) -> bool:
    return isinstance(value, str) and (
        value.startswith("idem:")
        or value.startswith("verify-graph:")
        or value.startswith("snapshot:")
        or value.endswith(":projection")
    )


def _looks_like_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _body_after_key(args: tuple[object, ...], key: str) -> object:
    try:
        index = list(args).index(key)
    except ValueError:
        return "{}"
    for value in args[index + 1:]:
        if value is not None and not isinstance(value, (int, float)):
            if not _looks_like_sha256(value) and not _looks_like_idempotency_key(value):
                return value
    return "{}"


def _body_to_text(body: object) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _json_payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _compact_retry_budget(payload: dict[str, Any]) -> dict[str, Any] | int | None:
    raw = payload.get("retry_budget")
    if isinstance(raw, int):
        return raw
    source = raw if isinstance(raw, dict) else payload
    budget = {
        key: source.get(key)
        for key in (
            "route",
            "retry",
            "max_retries",
            "max_attempts",
            "remaining_attempts",
            "idempotency_key",
            "task_id",
        )
        if source.get(key) is not None
    }
    return budget or None


def _compact_route_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("route_decision")
    if not isinstance(raw, dict):
        return None
    route = {
        key: raw.get(key)
        for key in (
            "route",
            "failure_class",
            "failure_type",
            "deterministic",
            "retryable",
            "operator_required",
        )
        if raw.get(key) is not None
    }
    return route or None


def _merge_queue_payload_status(row: dict[str, Any]) -> str:
    payload = _json_payload(row.get("payload"))
    return str(
        payload.get("merge_queue_status")
        or payload.get("queue_status")
        or payload.get("status")
        or ""
    ).lower()


def _control_plane_attempt_row(
    row: dict[str, Any],
    *,
    merge_queue_status: bool = False,
) -> dict[str, Any]:
    payload = _json_payload(row.get("payload"))
    status = (
        _merge_queue_payload_status(row) if merge_queue_status else row.get("status")
    )
    return {
        key: row.get(key)
        for key in (
            "id",
            "entry_type",
            "dispatcher_state",
            "actor",
            "runtime",
            "group_idx",
            "task_id",
            "request_digest",
            "created_at",
            "updated_at",
        )
    } | {
        "status": status,
        "retry": payload.get("retry"),
        "attempt_no": payload.get("attempt_no"),
        "retry_budget": _compact_retry_budget(payload),
        "runtime_policy_digest": payload.get("runtime_policy_digest"),
        "workspace_snapshot_ids": payload.get("workspace_snapshot_ids", []),
    }


def _control_plane_runtime_failure_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_payload(row.get("payload"))
    metadata = _json_payload(row.get("metadata"))
    route_decision = _compact_route_decision(payload)
    retry_budget = _compact_retry_budget(payload)
    return {
        key: row.get(key)
        for key in (
            "id",
            "attempt_id",
            "group_idx",
            "stage",
            "name",
            "status",
            "deterministic",
            "source_ref",
            "created_at",
            "finished_at",
        )
    } | {
        "deterministic": (
            (route_decision or {}).get("deterministic")
            if (route_decision or {}).get("deterministic") is not None
            else row.get("deterministic")
        ),
        "failure_class": metadata.get("failure_class") or payload.get("failure_class"),
        "failure_type": metadata.get("failure_type") or payload.get("failure_type"),
        "route": (
            (route_decision or {}).get("route")
            or metadata.get("route")
            or payload.get("route")
            or (retry_budget if isinstance(retry_budget, dict) else {}).get("route")
        ),
        "operator_required": (
            (route_decision or {}).get("operator_required")
            if (route_decision or {}).get("operator_required") is not None
            else (
                metadata.get("operator_required")
                if metadata.get("operator_required") is not None
                else payload.get("operator_required")
            )
        ),
        "retryable": (
            (route_decision or {}).get("retryable")
            if (route_decision or {}).get("retryable") is not None
            else (
                metadata.get("retryable")
                if metadata.get("retryable") is not None
                else payload.get("retryable")
            )
        ),
        "summary": str(row.get("summary") or "")[:500],
        "summary_length": len(str(row.get("summary") or "")),
        "summary_bytes": len(str(row.get("summary") or "").encode("utf-8")),
        "retry_budget": retry_budget,
        "route_decision": route_decision,
    }


def _control_plane_projection_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_payload(row.get("payload"))
    return {
        key: row.get(key)
        for key in (
            "id",
            "typed_row_id",
            "artifact_id",
            "source_table",
            "source_id",
            "projection_owner",
            "projection_kind",
            "projection_key",
            "projection_sha256",
            "legacy_event_id",
            "dashboard_outbox_event_id",
            "created_at",
        )
    } | {
        "group_idx": payload.get("group_idx"),
        "status": payload.get("status"),
    }


def _control_plane_runtime_workspace_binding_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "id",
            "sandbox_lease_id",
            "attempt_id",
            "runtime_name",
            "cwd",
            "workspace_override",
            "manifest_path",
            "status",
            "role_metadata_digest",
            "binding_digest",
            "created_at",
            "updated_at",
        )
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _insert_table_name(sql: str) -> str:
    normalized = " ".join(sql.lower().split())
    marker = "insert into "
    if marker not in normalized:
        return "unknown"
    return normalized.split(marker, 1)[1].split(" ", 1)[0].strip('"')
