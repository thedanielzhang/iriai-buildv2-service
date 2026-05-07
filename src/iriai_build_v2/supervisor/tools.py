from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .evidence import DashboardClient, probe_bridge, probe_worktree
from .models import (
    ArtifactRecord,
    EventRecord,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
)

_MUTATING_SQL_RE = re.compile(
    r"\b("
    r"alter|analyze|call|commit|copy|create|delete|drop|execute|grant|insert|"
    r"lock|merge|refresh|reset|revoke|rollback|set|truncate|update|vacuum"
    r")\b",
    re.IGNORECASE,
)


class SupervisorEvidenceToolbox:
    """Read-only evidence surface used by the agent investigator."""

    def __init__(
        self,
        *,
        feature_id: str,
        feature_store: Any,
        artifact_store: Any,
        dashboard_url: str | None = None,
        dashboard_client: DashboardClient | None = None,
        worktree_roots: list[str | Path] | None = None,
        forbidden_paths: list[str] | None = None,
    ) -> None:
        self.feature_id = feature_id
        self.feature_store = feature_store
        self.artifact_store = artifact_store
        self.dashboard_url = dashboard_url
        self.dashboard_client = dashboard_client
        self.worktree_roots = [Path(root) for root in (worktree_roots or [])]
        self.forbidden_paths = forbidden_paths or []

    async def gather(
        self,
        request: SupervisorInvestigationRequest,
    ) -> SupervisorEvidenceBundle:
        bundle = SupervisorEvidenceBundle(request=request)
        feature = await self.feature_store.get_feature(self.feature_id)
        try:
            bundle.artifacts.extend(await self._artifact_rows(request, feature))
            bundle.events.extend(await self._event_rows(request))
            if request.include_bridge and (self.dashboard_url or self.dashboard_client):
                bundle.bridge = await probe_bridge(
                    dashboard_url=self.dashboard_url,
                    client=self.dashboard_client,
                    after=0,
                )
            if request.include_worktrees:
                bundle.worktrees = [
                    probe_worktree(root, forbidden_paths=self.forbidden_paths)
                    for root in self.worktree_roots
                ]
            sql_results, rejected = await self._sql_results(request.sql)
            bundle.sql_results.extend(sql_results)
            bundle.rejected_sql.extend(rejected)
        except Exception as exc:
            bundle.errors.append(f"{type(exc).__name__}: {exc}")
        return bundle

    async def gather_many(
        self,
        requests: list[SupervisorInvestigationRequest],
    ) -> list[SupervisorEvidenceBundle]:
        return [await self.gather(request) for request in requests]

    async def _artifact_rows(
        self,
        request: SupervisorInvestigationRequest,
        feature: Any | None,
    ) -> list[ArtifactRecord]:
        records: list[ArtifactRecord] = []
        if feature is not None and hasattr(self.artifact_store, "get_record"):
            for key in request.artifact_keys[:20]:
                row = await self.artifact_store.get_record(key, feature=feature)
                if row is not None:
                    records.append(_artifact_record({**row, "key": key}))
        if request.artifact_prefixes and hasattr(self.artifact_store, "list_records"):
            after_id = request.artifact_after_id
            if after_id is None:
                after_id = (
                    max(0, int(request.artifact_ids[0]) - 1)
                    if request.artifact_ids
                    else 0
                )
            try:
                rows = await self.artifact_store.list_records(
                    feature_id=self.feature_id,
                    prefixes=tuple(request.artifact_prefixes[:20]),
                    after_id=after_id,
                    limit=200,
                    order="desc",
                )
            except TypeError:
                rows = await self.artifact_store.list_records(
                    feature_id=self.feature_id,
                    prefixes=tuple(request.artifact_prefixes[:20]),
                    after_id=after_id,
                )
            records.extend(_artifact_record(row) for row in rows)
        records.extend(await self._artifact_rows_by_id(request.artifact_ids[:50]))
        return _dedupe_artifacts(records)

    async def _artifact_rows_by_id(self, ids: list[int]) -> list[ArtifactRecord]:
        if not ids:
            return []
        if hasattr(self.artifact_store, "get_records_by_ids"):
            rows = await self.artifact_store.get_records_by_ids(self.feature_id, ids)
            return [_artifact_record(row) for row in rows]
        pool = getattr(self.artifact_store, "_pool", None)
        if pool is None:
            return []
        rows = await pool.fetch(
            """
            SELECT id, key, created_at, value
            FROM artifacts
            WHERE feature_id = $1 AND id = ANY($2::bigint[])
            ORDER BY id
            """,
            self.feature_id,
            ids,
        )
        return [_artifact_record(dict(row)) for row in rows]

    async def _event_rows(self, request: SupervisorInvestigationRequest) -> list[EventRecord]:
        after_id = request.event_after_id
        if after_id is None:
            return []
        rows = await self.feature_store.get_events(self.feature_id)
        records = [_event_record(row) for row in rows]
        records = [record for record in records if (record.id or 0) > after_id]
        return sorted(records, key=lambda record: record.id or 0)[: max(1, request.event_limit)]

    async def _sql_results(
        self,
        statements: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        pool = getattr(self.artifact_store, "_pool", None) or getattr(
            self.feature_store,
            "_pool",
            None,
        )
        results: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        if pool is None:
            return results, [
                {"sql": sql, "reason": "no postgres pool available"} for sql in statements
            ]
        for sql in statements[:5]:
            reason = validate_read_only_sql(sql)
            if reason:
                rejected.append({"sql": sql, "reason": reason})
                continue
            try:
                rows = await pool.fetch(sql)
                results.append(
                    {
                        "sql": sql,
                        "rows": [dict(row) for row in rows[:50]],
                        "row_count": len(rows),
                    }
                )
            except Exception as exc:
                rejected.append({"sql": sql, "reason": f"{type(exc).__name__}: {exc}"})
        return results, rejected


def validate_read_only_sql(sql: str) -> str:
    stripped = sql.strip().rstrip(";").strip()
    lowered = stripped.lower()
    if not stripped:
        return "empty SQL"
    if ";" in stripped:
        return "multiple SQL statements are not allowed"
    if not (lowered.startswith("select ") or lowered.startswith("with ")):
        return "only SELECT/WITH statements are allowed"
    if _MUTATING_SQL_RE.search(lowered):
        return "mutating SQL keywords are not allowed"
    if not re.search(r"\blimit\b", lowered) and not re.search(r"\bcount\s*\(", lowered):
        return "read-only SQL must include LIMIT or COUNT"
    return ""


def _artifact_record(row: dict[str, Any]) -> ArtifactRecord:
    value = row.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return ArtifactRecord(
        id=row.get("id"),
        key=str(row.get("key") or ""),
        value=value,
        created_at=row.get("created_at"),
        sha256=row.get("sha256"),
    )


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


def _dedupe_artifacts(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    by_id_key: dict[tuple[int | None, str], ArtifactRecord] = {}
    for record in records:
        by_id_key[(record.id, record.key)] = record
    return sorted(by_id_key.values(), key=lambda record: record.id or 0)
